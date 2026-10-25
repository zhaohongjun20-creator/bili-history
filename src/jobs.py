"""每期视频的独立任务目录 + manifest + 断点续跑（方案 P1-1）。

目录结构：
  data/jobs/<job_id>/
    manifest.json    # 阶段状态/缓存键/耗时/失败原因（原子写入，崩溃不留半截文件）
    script.json      # 讲稿文档（标题/段落/标签）
    narration/       # 各段配音 + index.json
    materials/       # 配图 + credits.json
    clips/           # 各段成片
    final.mp4 / final.srt / cover.jpg

断点规则：阶段带 cache_key（输入内容+模型+提示词+配置版本的哈希）。
manifest 中该阶段 status=done 且 key 与本次一致 → 复用产物跳过；
key 不一致（输入实质变化）→ 重跑。单段级（clips/narration）按条目 key 复用。
"""
import datetime
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

STAGES = ("script", "review", "narration", "images", "clips", "compose",
          "cover", "qc", "upload")


def cache_key(**parts) -> str:
    """稳定缓存键：参与生成的实质输入（文本/模型/参数版本）→ sha1。
    不放时间戳、路径等无关量，否则永远失配。"""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def new_job_id(topic: str) -> str:
    seed = hashlib.sha1(topic.encode("utf-8")).hexdigest()[:6]
    return f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{seed}" \
        f"{os.urandom(2).hex()}"      # 同秒同题也不撞；名字即创建时间，可排序


class Job:
    def __init__(self, path: Path):
        self.dir = Path(path)
        self.manifest_path = self.dir / "manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8")) \
            if self.manifest_path.exists() else self._fresh_manifest()

    # ── 创建/查找 ─────────────────────────────────────────
    @classmethod
    def create(cls, jobs_root: Path, topic: str) -> "Job":
        path = Path(jobs_root) / new_job_id(topic)
        path.mkdir(parents=True, exist_ok=True)
        for sub in ("narration", "materials", "clips"):
            (path / sub).mkdir(exist_ok=True)
        job = cls(path)
        job.manifest["topic"] = topic
        job.manifest["created_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        job.save()
        return job

    @classmethod
    def find_resumable(cls, jobs_root: Path, topic: str,
                       max_age_hours: float = 48.0) -> "Job | None":
        """同选题最近未走完 upload 的任务（崩溃/失败后重跑续命）。"""
        root = Path(jobs_root)
        if not root.is_dir():
            return None
        cutoff = time.time() - max_age_hours * 3600
        best = None
        for d in sorted(root.iterdir(), reverse=True):
            mf = d / "manifest.json"
            try:
                if mf.stat().st_mtime < cutoff:
                    break                                  # 目录按名倒序，更老的直接停
                m = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if m.get("topic") != topic:
                continue
            up = (m.get("stages") or {}).get("upload") or {}
            if up.get("status") == "done":
                continue
            best = cls(d)
            break
        return best

    # ── manifest 读写（原子） ─────────────────────────────
    @staticmethod
    def _fresh_manifest() -> dict:
        return {"job_id": "", "topic": "", "title": "", "created_at": "",
                "stages": {}, "publish": {}, "errors": []}

    def save(self):
        self.manifest.setdefault("stages", {})
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(self.manifest_path)                    # 同盘原子替换

    @property
    def job_id(self) -> str:
        return self.manifest.get("job_id") or self.dir.name

    # ── 阶段状态 ─────────────────────────────────────────
    def stage(self, name: str) -> dict | None:
        return (self.manifest["stages"] or {}).get(name)

    def reusable(self, name: str, key: str) -> bool:
        """阶段已完成且缓存键未变 → 可复用。"""
        st = self.stage(name) or {}
        return st.get("status") == "done" and (not key or st.get("key") == key)

    def mark_running(self, name: str, key: str = ""):
        self.manifest["stages"][name] = {"status": "running", "key": key,
                                         "started_at": time.time()}
        self.save()

    def mark_done(self, name: str, key: str = "", outputs: dict | None = None,
                  seconds: float | None = None, extra: dict | None = None):
        st = self.manifest["stages"].get(name) or {}
        st.update({"status": "done", "key": key,
                   "outputs": outputs or st.get("outputs") or {},
                   "finished_at": time.time()})
        if st.get("started_at") and seconds is None:
            seconds = time.time() - st["started_at"]
        if seconds is not None:
            st["seconds"] = round(seconds, 1)
        if extra:
            st.update(extra)
        self.manifest["stages"][name] = st
        self.save()

    def mark_failed(self, name: str, reason: str):
        st = self.manifest["stages"].get(name) or {"key": ""}
        st["status"] = "failed"
        st["fail_reason"] = str(reason)[:500]
        st["failed_at"] = time.time()
        self.manifest["stages"][name] = st
        self.manifest.setdefault("errors", []).append(
            {"stage": name, "reason": str(reason)[:300],
             "at": datetime.datetime.now().isoformat(timespec="seconds")})
        self.save()

    def heartbeat(self):
        for st in self.manifest["stages"].values():
            if st.get("status") == "running":
                st["last_alive"] = time.time()
        self.save()

    # ── 产物路径 ─────────────────────────────────────────
    def path(self, *parts: str) -> str:
        return str(self.dir.joinpath(*parts))

    def set_publish(self, **kw):
        self.manifest["publish"].update(kw)
        self.save()

    def set_title(self, title: str):
        self.manifest["title"] = title
        self.save()

    # ── 清理 ─────────────────────────────────────────────
    @staticmethod
    def prune(jobs_root: Path, keep: int = 20) -> int:
        """删除最老的任务目录（仅限已上传完成的；未上传的保留）。
        返回删除数。磁盘治理：防止 jobs 无限堆积。"""
        root = Path(jobs_root)
        if not root.is_dir():
            return 0
        dirs = [d for d in root.iterdir() if d.is_dir()]
        dirs.sort(key=lambda d: (d.stat().st_mtime_ns, d.name))
        removed = 0
        for d in dirs[:-keep] if len(dirs) > keep else []:
            try:
                m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
                if ((m.get("stages") or {}).get("upload") or {}).get("status") == "done":
                    shutil.rmtree(d, ignore_errors=True)
                    removed += 1
            except (OSError, ValueError):
                continue
        return removed
