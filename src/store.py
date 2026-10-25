"""主题级状态存储 + 视频资产/数据快照（数据反馈闭环）。

状态机（方案 P0-2）：
  pending → generating → review → ready → published
                    ↘ failed（记录失败阶段/原因/重试次数，可重试回 generating）

legacy 状态（旧库/旧流程写入）：dryrun（试跑过未发布，可重新制作）、
review-rejected（审查否决，默认跳过，显式 remake 才重做）。

旧库迁移：topics 表按需补列（stage/fail_reason/retry_count/updated_at/job_id），
历史记录一律保留，不重建数据库。

topics   —— 选题状态机
videos   —— 咱们产出的视频资产（bvid/标题/频道）
snapshots —— 每日播放量快照（MatrixMedia 式数据回读）
"""
import datetime
import os
import random
import sqlite3

PENDING = "pending"
GENERATING = "generating"
REVIEW = "review"
READY = "ready"
PUBLISHED = "published"
FAILED = "failed"
DRYRUN = "dryrun"
REVIEW_REJECTED = "review-rejected"
REVIEW_HUMAN = "review-human"

# 默认跳过（已产出/已否决/进行中/待人工）；failed 与 dryrun 可重新领取。
# review-human：审查要求修正但无修法/高危问题——等 --redo 重做或 --approve-review 人工放行
BLOCKING = {GENERATING, REVIEW, READY, PUBLISHED, REVIEW_REJECTED, REVIEW_HUMAN}
CLAIMABLE = {PENDING, FAILED, DRYRUN}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    topic       TEXT PRIMARY KEY,
    title       TEXT DEFAULT '',
    status      TEXT DEFAULT 'pending',
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS videos (
    bvid        TEXT PRIMARY KEY,
    title       TEXT DEFAULT '',
    channel     TEXT DEFAULT '',
    published_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS snapshots (
    bvid        TEXT,
    date        TEXT,
    plays       INTEGER DEFAULT 0,
    PRIMARY KEY (bvid, date)
);
"""

_EXTRA_COLS = {  # 旧库补列（幂等）
    "stage": "TEXT DEFAULT ''",
    "fail_reason": "TEXT DEFAULT ''",
    "retry_count": "INTEGER DEFAULT 0",
    "updated_at": "TEXT DEFAULT ''",
    "job_id": "TEXT DEFAULT ''",
}


class Store:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=10)
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.executescript(_SCHEMA)
        self._migrate()
        # 自动提交模式：每条 UPDATE 自带原子性，WHERE 守卫即领取语义
        self.conn.isolation_level = None

    def _migrate(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(topics)")}
        for col, ddl in _EXTRA_COLS.items():
            if col not in cols:
                self.conn.execute(f"ALTER TABLE topics ADD COLUMN {col} {ddl}")
        self.conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.datetime.now().isoformat(timespec="seconds")

    # ── 状态机 ────────────────────────────────────────────
    def get_topic(self, topic: str) -> dict | None:
        row = self.conn.execute(
            "SELECT topic,title,status,stage,fail_reason,retry_count,updated_at,job_id "
            "FROM topics WHERE topic=?", (topic,)).fetchone()
        if not row:
            return None
        return dict(zip(("topic", "title", "status", "stage", "fail_reason",
                         "retry_count", "updated_at", "job_id"), row))

    def _set(self, topic: str, title: str, status: str, stage: str = "",
             reason: str = "", job_id: str = ""):
        # retry_count：failed +1，published 清零，其余保持（重试上限才有效）
        self.conn.execute(
            "INSERT INTO topics (topic,title,status,stage,fail_reason,retry_count,"
            "updated_at,job_id) VALUES (?,?,?,?,?,0,?,?) "
            "ON CONFLICT(topic) DO UPDATE SET "
            "title=COALESCE(NULLIF(excluded.title,''), title), status=excluded.status, "
            "stage=excluded.stage, updated_at=excluded.updated_at, "
            "job_id=CASE WHEN excluded.job_id='' THEN topics.job_id ELSE excluded.job_id END, "
            "fail_reason=CASE WHEN excluded.status='failed' THEN excluded.fail_reason ELSE '' END, "
            "retry_count=CASE WHEN excluded.status='failed' THEN topics.retry_count+1 "
            "WHEN excluded.status='published' THEN 0 ELSE topics.retry_count END",
            (topic, title or "", status, stage, reason[:500], self._now(), job_id))

    def mark_generating(self, topic, title="", job_id=""):
        self._set(topic, title, GENERATING, job_id=job_id)

    def mark_review(self, topic, title="", job_id=""):
        self._set(topic, title, REVIEW, job_id=job_id)

    def mark_ready(self, topic, title="", job_id=""):
        self._set(topic, title, READY, job_id=job_id)

    def mark_published(self, topic, title="", job_id=""):
        self._set(topic, title, PUBLISHED, job_id=job_id)

    def mark_dryrun(self, topic, title="", job_id=""):
        self._set(topic, title, DRYRUN, job_id=job_id)

    def mark_review_rejected(self, topic, title="", job_id=""):
        self._set(topic, title, REVIEW_REJECTED, job_id=job_id)

    def mark_review_human(self, topic, title="", job_id=""):
        """审查要求修正但无修法/发现高危问题——需人工介入：
        --redo N 重做，或人工阅读问题后 --approve-review N 放行。"""
        self._set(topic, title, REVIEW_HUMAN, job_id=job_id)

    def mark_failed(self, topic, title="", stage: str = "", reason: str = "",
                    job_id: str = ""):
        """failed 自动累加 retry_count，成功/重新开始时清零。"""
        self._set(topic, title, FAILED, stage=stage, reason=reason, job_id=job_id)

    def reset_topic(self, topic, status=PENDING):
        """人工修复用：强制改状态、清失败计数。"""
        self.conn.execute(
            "UPDATE topics SET status=?, retry_count=0, fail_reason='', updated_at=? "
            "WHERE topic=?", (status, self._now(), topic))

    # ── 选题领取（原子，两进程不会领到同一题） ────────────
    def select_and_claim(self, candidates: list, max_retries: int = 3,
                         stale_hours: float = 12.0,
                         rng: random.Random | None = None) -> tuple[str, str] | None:
        """从候选主题里领一个：随机打乱后逐个原子抢占。

        返回 (topic, 之前的 status)；全都不可领取返回 None。
        - pending/dryrun/failed(未超次) 可领取
        - generating 超过 stale_hours 视为崩溃残留，可抢占
        """
        pool = list(candidates)
        (rng or random).shuffle(pool)
        stale_cut = (datetime.datetime.now()
                     - datetime.timedelta(hours=stale_hours)).isoformat(timespec="seconds")
        for topic in pool:
            row = self.get_topic(topic)
            if row is None:
                self.conn.execute(
                    "INSERT INTO topics (topic,status,updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(topic) DO NOTHING", (topic, PENDING, self._now()))
                row = self.get_topic(topic) or {"status": PENDING}
            st = row["status"]
            if st in BLOCKING:
                if st == GENERATING and (row["updated_at"] or "") < stale_cut:
                    pass        # 崩溃残留：允许抢占
                else:
                    continue
            if st == FAILED and row["retry_count"] >= max_retries:
                continue
            # 原子领取：WHERE 守卫与写入同一条语句，并发下只有一个进程能改成功
            cur = self.conn.execute(
                "UPDATE topics SET status=?, stage='', updated_at=? "
                "WHERE topic=? AND status=?",
                (GENERATING, self._now(), topic, st))
            if cur.rowcount == 1:
                return topic, st
        return None

    # ── 兼容旧接口 ────────────────────────────────────────
    def is_topic_done(self, topic: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM topics WHERE topic=?", (topic,)).fetchone()
        return bool(row and row[0] == PUBLISHED)

    def mark_topic(self, topic: str, title: str, status: str):
        """旧式任意状态写入（upload_pending 等外部脚本用）。"""
        self._set(topic, title, status)

    # ── 视频资产 / 快照 ──────────────────────────────────
    def record_video(self, bvid: str, title: str, channel: str,
                     published_at: str = ""):
        self.conn.execute(
            "INSERT INTO videos (bvid,title,channel,published_at) VALUES (?,?,?,?) "
            "ON CONFLICT(bvid) DO UPDATE SET title=excluded.title, channel=excluded.channel",
            (bvid, title, channel,
             published_at or datetime.date.today().isoformat()))

    def upsert_snapshot(self, bvid: str, plays: int, date: str = None):
        self.conn.execute(
            "INSERT INTO snapshots (bvid,date,plays) VALUES (?,?,?) "
            "ON CONFLICT(bvid,date) DO UPDATE SET plays=excluded.plays",
            (bvid, date or datetime.date.today().isoformat(), int(plays)))

    def videos_all(self) -> list:
        return self.conn.execute(
            "SELECT bvid,title,channel FROM videos").fetchall()

    def prev_plays(self, bvid: str, before_date: str) -> int | None:
        """before_date 之前最近一次快照的播放数（无则 None=新视频）。"""
        row = self.conn.execute(
            "SELECT plays FROM snapshots WHERE bvid=? AND date<? "
            "ORDER BY date DESC LIMIT 1", (bvid, before_date)).fetchone()
        return row[0] if row else None
