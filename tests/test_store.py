"""选题状态机测试：状态流转、原子领取、旧库迁移、失败重试上限。"""
import sqlite3

import pytest

from src.store import (BLOCKING, Store, GENERATING, PUBLISHED, FAILED,
                       PENDING, READY, REVIEW_REJECTED, DRYRUN)


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "topics.db"))


def test_new_topic_lifecycle(store):
    topic = "库尔斯克会战"
    assert store.get_topic(topic) is None
    got = store.select_and_claim([topic])
    assert got == (topic, "pending")
    assert store.get_topic(topic)["status"] == GENERATING
    store.mark_review(topic, "标题")
    store.mark_ready(topic, "标题")
    store.mark_published(topic, "标题")
    assert store.get_topic(topic)["status"] == PUBLISHED
    assert store.is_topic_done(topic)


def test_published_not_reclaimed(store):
    topic = "斯大林格勒战役"
    store.mark_published(topic, "标题")
    assert store.select_and_claim([topic]) is None


def test_failed_retryable_until_limit(store):
    topic = "冬季战争"
    store.mark_generating(topic)
    for _ in range(3):
        store.mark_failed(topic, stage="upload", reason="21566")
    assert store.get_topic(topic)["retry_count"] == 3
    assert store.select_and_claim([topic], max_retries=3) is None  # 超限
    store.reset_topic(topic)                                       # 人工重置
    assert store.select_and_claim([topic]) is not None


def test_failed_does_not_block_forever(store):
    """failed 可重新领取（与 published/review-rejected 的关键区别）。"""
    topic = "莱特湾海战"
    store.mark_failed(topic, stage="qc", reason="黑帧过多")
    assert store.get_topic(topic)["status"] == FAILED
    assert store.select_and_claim([topic]) is not None


def test_review_rejected_blocked(store):
    topic = "珍珠港事件"
    store.mark_review_rejected(topic)
    assert store.select_and_claim([topic]) is None


def test_dryrun_reclaimable(store):
    topic = "中途岛海战"
    store.mark_dryrun(topic)
    assert store.select_and_claim([topic]) is not None


def test_claim_is_atomic_across_processes(tmp_path):
    """两个连接同时抢同一题：只有一个能成功（方案 P0-2）。"""
    db = str(tmp_path / "topics.db")
    s1, s2 = Store(db), Store(db)
    topic = "诺曼底登陆"
    got1 = s1.select_and_claim([topic])
    got2 = s2.select_and_claim([topic])
    assert got1 is not None and got2 is None
    assert s2.select_and_claim([topic]) is None


def test_stale_generating_can_be_taken_over(store):
    """崩溃残留的 generating 超过 stale_hours 后可抢占。"""
    topic = "阿登反击战"
    store.mark_generating(topic)
    assert store.select_and_claim([topic], stale_hours=12) is None
    # 把 updated_at 拨回 13 小时前
    store.conn.execute(
        "UPDATE topics SET updated_at=datetime('now','localtime','-13 hours') "
        "WHERE topic=?", (topic,))
    assert store.select_and_claim([topic], stale_hours=12) is not None


def test_generating_cleared_retry_count(store):
    topic = "X"
    store.mark_generating(topic)
    store.mark_failed(topic, stage="tts", reason="e")
    store.mark_failed(topic, stage="tts", reason="e")
    assert store.get_topic(topic)["retry_count"] == 2
    store.mark_generating(topic)          # 重新领取不清零（防无限重试）
    assert store.get_topic(topic)["retry_count"] == 2
    store.mark_published(topic)           # 成功才清零
    assert store.get_topic(topic)["retry_count"] == 0


def test_legacy_db_migration(tmp_path):
    """旧库（无新列）打开后自动补列，历史记录保留（方案 P0-2 验收）。"""
    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
      CREATE TABLE topics (topic TEXT PRIMARY KEY, title TEXT DEFAULT '',
        status TEXT DEFAULT 'dryrun',
        created_at TEXT DEFAULT (datetime('now','localtime')));
      INSERT INTO topics (topic, title, status) VALUES
        ('旧主题A', '旧标题A', 'published'),
        ('旧主题B', '旧标题B', 'failed:RuntimeError(21566)');
    """)
    conn.commit()
    conn.close()
    store = Store(db)
    row = store.get_topic("旧主题A")
    assert row["status"] == "published" and row["retry_count"] == 0
    assert store.is_topic_done("旧主题A")
    # 旧 failed 记录可重新领取，且新列写入正常
    got = store.select_and_claim(["旧主题B"])
    assert got == ("旧主题B", "failed:RuntimeError(21566)")


def test_mark_topic_keeps_title_on_empty(store):
    store.mark_topic("T", "原标题", "published")
    store.mark_topic("T", "", "failed")
    assert store.get_topic("T")["title"] == "原标题"
