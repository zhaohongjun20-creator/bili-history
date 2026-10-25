"""任务 manifest / 断点续跑测试：缓存键、阶段复用、原子写入、清理。"""
import json

from src.jobs import Job, cache_key


def test_cache_key_stable_and_sensitive():
    a = cache_key(topic="X", model="glm-4-flash", n=12)
    b = cache_key(model="glm-4-flash", n=12, topic="X")       # 顺序无关
    assert a == b
    assert a != cache_key(topic="X", model="glm-4-flash", n=13)
    assert a != cache_key(topic="Y", model="glm-4-flash", n=12)
    assert len(cache_key()) == 12


def test_stage_lifecycle_and_reuse(tmp_path):
    job = Job.create(tmp_path / "jobs", "测试主题")
    assert (tmp_path / "jobs" / job.dir.name / "narration").is_dir()
    assert job.manifest["topic"] == "测试主题"

    assert not job.reusable("script", "k1")
    job.mark_running("script", "k1")
    assert job.stage("script")["status"] == "running"
    job.mark_done("script", "k1", {"script": "script.json"}, seconds=1.0)
    assert job.reusable("script", "k1")
    assert not job.reusable("script", "k2")     # 键变化 → 必须重跑

    job.mark_failed("script", "boom")
    assert not job.reusable("script", "k1")
    assert job.manifest["errors"][-1]["stage"] == "script"


def test_manifest_atomic_and_reload(tmp_path):
    job = Job.create(tmp_path / "jobs", "主题")
    job.mark_done("narration", "kk", extra={"items": ["a", "b"]})
    reloaded = Job(job.dir)
    assert reloaded.stage("narration")["items"] == ["a", "b"]
    assert reloaded.job_id == job.job_id


def test_find_resumable(tmp_path):
    job = Job.create(tmp_path / "jobs", "断点主题")
    job.mark_done("script", "k")
    found = Job.find_resumable(tmp_path / "jobs", "断点主题")
    assert found is not None and found.job_id == job.job_id
    # upload 完成后不再续跑
    job.mark_done("upload", outputs={"bvid": "BV1"})
    assert Job.find_resumable(tmp_path / "jobs", "断点主题") is None
    # 其他主题不串
    assert Job.find_resumable(tmp_path / "jobs", "别的主题") is None


def test_find_resumable_respects_age(tmp_path):
    import os, time
    job = Job.create(tmp_path / "jobs", "老任务")
    old = time.time() - 72 * 3600
    os.utime(job.dir / "manifest.json", (old, old))
    assert Job.find_resumable(tmp_path / "jobs", "老任务", max_age_hours=48) is None


def test_prune_keeps_unuploaded(tmp_path):
    import os, time
    done = Job.create(tmp_path / "jobs", "已完成")
    done.mark_done("upload", outputs={"bvid": "BVx"})
    pending = Job.create(tmp_path / "jobs", "未上传")
    old = time.time() - 3600
    os.utime(done.dir, (old, old))     # 明确比 pending 老
    removed = Job.prune(tmp_path / "jobs", keep=1)
    assert removed == 1
    assert pending.dir.exists() and not done.dir.exists()


def test_publish_info(tmp_path):
    job = Job.create(tmp_path / "jobs", "发布")
    job.set_publish(title="t", bvid="BV1abc")
    job.set_title("新标题")
    m = json.loads((job.dir / "manifest.json").read_text(encoding="utf-8"))
    assert m["publish"]["bvid"] == "BV1abc" and m["title"] == "新标题"
