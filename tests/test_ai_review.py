"""AI 终审（codebuddy/magpie 网关）测试：解析器 + 放行/否决分支（mock 网关调用）。"""
import json
import logging

import autopost
from src.jobs import Job
from src.store import Store

log = logging.getLogger("test_ai_review")
logging.basicConfig(level=logging.ERROR)


def _wrap(verdict_text):
    """模拟 codebuddy --output-format json：数组，result 为含 json 块的文本。"""
    inner = "结论\n```json\n" + verdict_text + "\n```"
    return json.dumps(
        [{"type": "result", "result": inner}, {"permission_denials": []}])


def test_parse_pass():
    out = _wrap('{"verdict":"PASS","issues_confirmed":[],"issues_overruled":["年代误报"],'
                '"new_findings":[],"summary":"史实无误"}')
    v = autopost._parse_codebuddy_verdict(out, log)
    assert v["verdict"] == "PASS"


def test_parse_reject_and_bad_inputs():
    out = _wrap('{"verdict":"REJECT","issues_confirmed":["伪造引语"],'
                '"issues_overruled":[],"new_findings":["碾过防线"],"summary":"否决"}')
    v = autopost._parse_codebuddy_verdict(out, log)
    assert v["verdict"] == "REJECT" and v["issues_confirmed"] == ["伪造引语"]
    assert autopost._parse_codebuddy_verdict("garbage", log) is None
    assert autopost._parse_codebuddy_verdict('["x"]', log) is None


def test_ai_review_pass_approves_and_continues(tmp_path, monkeypatch):
    """PASS → manifest 置 done(approved_by=codebuddy) 并继续制作流程。"""
    job = Job.create(tmp_path / "jobs", "AI审选题")
    job.mark_done("script", "k")
    job.manifest["stages"]["review"] = {"status": "needs-human", "key": "kk"}
    job.save()
    monkeypatch.setattr(
        autopost.jobs_mod.Job, "find_resumable",
        classmethod(lambda cls, root, topic, **kw: job))
    monkeypatch.setattr(
        autopost, "_run_codebuddy_review",
        lambda j, lg: {"verdict": "PASS", "issues_confirmed": [],
                       "issues_overruled": [], "new_findings": [],
                       "summary": "ok"})
    ran = []
    monkeypatch.setattr(autopost, "_pipeline", lambda *a, **kw: ran.append(1))
    cfg = {"topics": ["AI审选题"]}
    store = Store(str(tmp_path / "t.db"))
    autopost._ai_review(store, cfg, 1, log, dry_run=True, confirm_upload=False)
    assert ran == [1]
    assert job.stage("review")["status"] == "done"
    assert job.stage("review")["approved_by"] == "codebuddy"


def test_ai_review_reject_marks_rejected(tmp_path, monkeypatch):
    """REJECT → review-rejected 留痕（store + manifest），不继续制作。"""
    job = Job.create(tmp_path / "jobs", "AI否决选题")
    job.mark_done("script", "k")
    job.manifest["stages"]["review"] = {"status": "needs-human", "key": "kk"}
    job.save()
    monkeypatch.setattr(
        autopost.jobs_mod.Job, "find_resumable",
        classmethod(lambda cls, root, topic, **kw: job))
    monkeypatch.setattr(
        autopost, "_run_codebuddy_review",
        lambda j, lg: {"verdict": "REJECT",
                       "issues_confirmed": ["伪造克劳塞维茨引言"],
                       "issues_overruled": [], "new_findings": [],
                       "summary": "史实硬伤"})
    ran = []
    monkeypatch.setattr(autopost, "_pipeline", lambda *a, **kw: ran.append(1))
    cfg = {"topics": ["AI否决选题"]}
    store = Store(str(tmp_path / "t.db"))
    autopost._ai_review(store, cfg, 1, log, dry_run=True, confirm_upload=False)
    # 2026-11-07 新规矩：REJECT 留痕后自动重做（不再需要手动 --redo）
    assert ran == [True]
    assert job.stage("review")["status"] == "rejected"
    assert job.stage("review")["rejected_by"] == "codebuddy"
    assert job.stage("review")["round"] == 1
    assert store.get_topic("AI否决选题")["status"] == "pending"  # 已自动复位


def test_ai_review_gateway_fail_stays_human(tmp_path, monkeypatch):
    """网关失败 → fail-closed：保持 needs-human，不否决不放行。"""
    job = Job.create(tmp_path / "jobs", "网关挂")
    job.mark_done("script", "k")
    job.manifest["stages"]["review"] = {"status": "needs-human", "key": "kk"}
    job.save()
    monkeypatch.setattr(
        autopost.jobs_mod.Job, "find_resumable",
        classmethod(lambda cls, root, topic, **kw: job))
    monkeypatch.setattr(autopost, "_run_codebuddy_review", lambda j, lg: None)
    cfg = {"topics": ["网关挂"]}
    store = Store(str(tmp_path / "t.db"))
    autopost._ai_review(store, cfg, 1, log, dry_run=True, confirm_upload=False)
    assert job.stage("review")["status"] == "needs-human"

def test_parse_text_mode_primary():
    """text 模式主形态：首词 PASS/REJECT + 理由行。"""
    out = "PASS\n\n理由：\n1. 史实无误\n2. 配图相关"
    v = autopost._parse_codebuddy_verdict(out, log)
    assert v["verdict"] == "PASS" and "史实无误" in v["summary"]
    out2 = "REJECT\n\n1. 伪造克劳塞维茨引语\n2. 1935年开建年份错（应为1930）"
    v2 = autopost._parse_codebuddy_verdict(out2, log)
    assert v2["verdict"] == "REJECT" and "1930" in v2["summary"]


def test_parse_no_verdict_fails_closed():
    """输出无明确结论（如中途截断的状态文本）→ None。"""
    assert autopost._parse_codebuddy_verdict(
        "已读完三份关键文件，确认了任务上下文，正在分析……", log) is None

def test_ai_review_third_rejection_forced_publish(tmp_path, monkeypatch):
    """同一题被打回 3 次后，第 4 轮 REJECT 也强制放行（用户定的规矩）。"""
    job = Job.create(tmp_path / "jobs", "三轮选题")
    job.mark_done("script", "k")
    job.manifest["stages"]["review"] = {"status": "needs-human", "key": "kk"}
    job.save()
    # 造 3 个历史被否决任务
    for i in range(3):
        old = Job.create(tmp_path / "jobs", "三轮选题")
        old.manifest["stages"]["review"] = {
            "status": "rejected", "rejected_by": "codebuddy", "round": i + 1}
        old.save()
    monkeypatch.setattr(
        autopost.jobs_mod.Job, "find_resumable",
        classmethod(lambda cls, root, topic, **kw: job))
    monkeypatch.setattr(
        autopost, "_run_codebuddy_review",
        lambda j, lg: {"verdict": "REJECT", "issues_confirmed": ["仍有问题"],
                       "issues_overruled": [], "new_findings": [], "summary": "还是不行"})
    ran = []
    monkeypatch.setattr(autopost, "_pipeline", lambda *a, **kw: ran.append(1))
    cfg = {"topics": ["三轮选题"]}
    store = Store(str(tmp_path / "t.db"))
    autopost._ai_review(store, cfg, 1, log, dry_run=True, confirm_upload=False)
    assert ran == [1]                          # 强制放行继续制作
    assert job.stage("review")["approved_by"] == "codebuddy-forced"
    assert "上限" in job.stage("review")["forced_note"]


def test_ai_review_reject_auto_remakes(tmp_path, monkeypatch):
    """REJECT（未达上限）→ 留痕 + 自动重做（不再需要手动 --redo）。"""
    job = Job.create(tmp_path / "jobs", "自动重做选题")
    job.mark_done("script", "k")
    job.manifest["stages"]["review"] = {"status": "needs-human", "key": "kk"}
    job.save()
    monkeypatch.setattr(
        autopost.jobs_mod.Job, "find_resumable",
        classmethod(lambda cls, root, topic, **kw: job))
    monkeypatch.setattr(
        autopost, "_run_codebuddy_review",
        lambda j, lg: {"verdict": "REJECT", "issues_confirmed": ["年份错"],
                       "issues_overruled": [], "new_findings": [], "summary": "否决"})
    remade = []
    monkeypatch.setattr(autopost, "_pipeline",
                        lambda *a, **kw: remade.append(kw.get("fresh")))
    cfg = {"topics": ["自动重做选题"]}
    store = Store(str(tmp_path / "t.db"))
    autopost._ai_review(store, cfg, 1, log, dry_run=True, confirm_upload=False)
    assert remade == [True]                    # 自动 fresh 重做
    assert job.stage("review")["round"] == 1
