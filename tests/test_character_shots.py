"""角色分镜命令：mock GLM，不打真实接口。

纪录片 Ken Burns / qc 的行为在原有测试里，这里只确认新命令没有接进去。
"""
from pathlib import Path

import pytest

import character_shots as cli
from src import character_shots as mod
from src.slideshow import _kenburns

ROOT = Path(__file__).resolve().parents[1]

BRIEF = """一条 6 秒角色短片，讲一个蓝发女孩在雨夜巷口停下又跑起来。
角色锁定：齐肩蓝发、白外套、黑靴，不改设计。
时长 6 秒，画幅 16:9，共 2 镜。
风格是电影感三维，表演用连续动作，开拍前一次定死。
"""

BRIEF_ONE = """一条 3 秒角色短片，蓝发女孩在雨夜巷口起跑。
角色锁定：齐肩蓝发、白外套、黑靴。
时长 3 秒，画幅 16:9，共 1 镜。
风格是电影感三维，连续动作。
"""

BRIEF_ARCHIVAL = """一条 4 秒史料静帧，用一张 1944 年港口照片做 Ken Burns 左移。
没有角色。画幅 16:9，共 1 镜。风格是纪录片静帧滑动。
"""

GOOD = """
CUT: 01
时码: 00:00.00–00:03.00
镜类: 角色表演
提示词:
  总规格：16:9，3.00 秒，电影感三维。这是生成表演。她在雨夜巷口停下。
  锁定元素：齐肩蓝发、白外套、黑靴，设计锁定。
  按秒分镜：0.00–1.20 她跑进巷口，双臂摆动、双脚交替蹬地，重心随步伐起伏；1.20–3.00 重心前移后刹住。
  禁止项：禁止把全身当静帧平移；不添加 brief 没有的台词。
检查:
  手脚重心：必须连续动
  字先可读：无字
  浅色遮罩：查破边、破洞、手和装具脱落；破了重拍或换镜，不用生成像素补

CUT: 02
时码: 00:03.00–00:06.00
镜类: 动作后定格
提示词:
  总规格：16:9，3.00 秒，电影感三维。这是生成表演。她再次跑起来，动作做到后定格。
  锁定元素：齐肩蓝发、白外套、黑靴，设计锁定。
  按秒分镜：0.00–2.20 她连续起跑，双臂摆动、双脚蹬地，重心前移；2.20–3.00 动作做到这一帧后定格。
  禁止项：禁止未做动作就静帧平移。
检查:
  手脚重心：可在动作做到后定格
  字先可读：无字
  浅色遮罩：查破边、破洞、手和装具脱落；破了重拍或换镜，不用生成像素补
"""

GOOD_ONE = """
CUT: 01
时码: 00:00.00–00:03.00
镜类: 角色表演
提示词:
  总规格：16:9，3.00 秒，电影感三维。这是生成表演。她在雨夜巷口起跑。
  锁定元素：齐肩蓝发、白外套、黑靴，设计锁定。
  按秒分镜：她连续起跑，双臂摆动、双脚交替蹬地，重心前移。
  禁止项：禁止把全身当静帧平移。
检查:
  手脚重心：必须连续动
  字先可读：无字
  浅色遮罩：查破边、破洞、手和装具脱落；破了重拍或换镜，不用生成像素补
"""

STILL = """
CUT: 01
时码: 00:00.00–00:03.00
镜类: 角色表演
提示词:
  总规格：16:9，3.00 秒。把她的静帧从左滑到右。
  锁定元素：齐肩蓝发、白外套、黑靴。
  按秒分镜：整张角色静帧从左平移到右，手脚和重心保持不动，只是画面在滑。
  禁止项：不要改发型。
检查:
  手脚重心：不查
  字先可读：无字
  浅色遮罩：不查
"""

STILL_CUT_02 = """
CUT: 02
时码: 00:03.00–00:06.00
镜类: 角色表演
提示词:
  总规格：16:9，3.00 秒。把她的静帧从左滑到右。
  锁定元素：齐肩蓝发、白外套、黑靴。
  按秒分镜：整张角色静帧从左平移到右，手脚和重心保持不动，只是画面在滑。
  禁止项：不要改发型。
检查:
  手脚重心：不查
  字先可读：无字
  浅色遮罩：不查
"""

FIXED_CUT_02 = """
CUT: 02
时码: 00:03.00–00:06.00
镜类: 角色表演
提示词:
  总规格：16:9，3.00 秒，电影感三维。这是生成表演。她再次跑起来。
  锁定元素：齐肩蓝发、白外套、黑靴，设计锁定。
  按秒分镜：她从画面左侧连续跑向右侧，双臂摆动、双脚交替蹬地，重心随步伐起伏。
  禁止项：禁止把全身当静帧平移。
检查:
  手脚重心：必须连续动
  字先可读：无字
  浅色遮罩：查破边、破洞、手和装具脱落；破了重拍或换镜，不用生成像素补
"""

ARCHIVAL = """
CUT: 01
时码: 00:00.00–00:04.00
镜类: 史料静帧
提示词:
  总规格：16:9，4.00 秒。一张 1944 年港口照片。
  锁定元素：照片内容锁定，不改像素。
  按秒分镜：Ken Burns 左移，从码头左侧缓慢移到右侧。这是史料静帧滑动，不是角色在跑。
  禁止项：不改照片里的船和人，不把史料图写成角色表演。
检查:
  手脚重心：史料静帧（Ken Burns，不按静帧平移判失败）
  字先可读：无字
  浅色遮罩：不查
"""

ARCHIVAL_BANNED = """
CUT: 01
时码: 00:00.00–00:04.00
镜类: 史料静帧
提示词:
  总规格：16:9，4.00 秒。一张 1944 年港口照片。
  锁定元素：照片内容锁定。
  按秒分镜：Ken Burns 左移，从码头左侧缓慢移到右侧。
  禁止项：禁止静帧平移，禁止 Ken Burns 左移。
检查:
  手脚重心：史料静帧（Ken Burns，不按静帧平移判失败）
  字先可读：无字
  浅色遮罩：不查
"""


def test_sample_brief_has_four_blocks_checks_and_delivery():
    calls = []

    def chat(prompt):
        calls.append(prompt)
        return GOOD

    out = mod.generate_shot_markdown(BRIEF, chat)
    assert len(calls) == 1
    shots = mod.parse_shots(out)
    assert [s.number for s in shots] == [1, 2]
    for shot in shots:
        assert mod.shot_problems(shot) == []
        for name in mod.BLOCKS:
            assert shot.blocks[name].strip()
        for name in mod.CHECKS:
            assert shot.checks[name].strip()
    assert "交片提醒" in out
    assert "完整" in out and "时间码" in out and "编号" in out
    assert "压缩包" in out
    assert "生成像素" in out
    assert "重拍" in out and "换镜" in out
    assert "不要只交静帧" in out


def test_prompt_limits_still_pan_ban_and_ao_list():
    prompt = mod.build_prompt(BRIEF)
    assert "禁止静帧平移" in prompt
    assert "Ken Burns" in prompt
    assert "史料" in prompt
    assert "13" in prompt and "示例" in prompt
    assert "道具" in prompt and "台词" in prompt
    assert "不添加" in prompt
    assert "武器" in prompt and "不编造史实" in prompt
    assert "生成表演" in prompt
    assert "手和装具脱落" in prompt
    assert "雪地" in prompt and "硬抠" in prompt
    assert "左移" in prompt


def test_still_slide_character_shot_is_rewritten_once():
    calls = []

    def chat(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return GOOD.split("CUT: 02")[0] + STILL_CUT_02
        return FIXED_CUT_02

    out = mod.generate_shot_markdown(BRIEF, chat)
    assert len(calls) == 2
    assert "角色镜只有静帧平移" in calls[1]
    assert "手脚和重心保持不动" in calls[1]
    assert "手脚和重心保持不动" not in out
    assert "双臂摆动" in out
    assert "生成表演" in calls[1]
    assert "史料实拍" in calls[1]
    assert "状态: 未通过" not in out
    shots = mod.parse_shots(out)
    assert [s.number for s in shots] == [1, 2]
    assert mod.shot_problems(shots[1]) == []
    assert "交片提醒" in out


def test_still_slide_stays_rejected_when_rewrite_does_not_fix_it():
    calls = []

    def chat(prompt):
        calls.append(prompt)
        return STILL

    out = mod.generate_shot_markdown(BRIEF_ONE, chat)
    assert len(calls) == 2
    assert "未通过" in out
    assert "角色镜只有静帧平移" in out
    assert "整张角色静帧从左平移到右" in out
    assert "交片提醒" in out
    assert "生成像素" in out


def test_missing_block_is_rewritten_once():
    bad = GOOD_ONE.replace("  禁止项：禁止把全身当静帧平移。\n", "")
    calls = []

    def chat(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return bad
        assert "缺少禁止项" in prompt
        return GOOD_ONE

    out = mod.generate_shot_markdown(BRIEF_ONE, chat)
    assert len(calls) == 2
    shots = mod.parse_shots(out)
    assert len(shots) == 1
    assert mod.shot_problems(shots[0]) == []
    assert "禁止项" in out
    assert "状态: 未通过" not in out


def test_archival_ken_burns_pan_is_not_rewritten():
    calls = []

    def chat(prompt):
        calls.append(prompt)
        return ARCHIVAL

    out = mod.generate_shot_markdown(BRIEF_ARCHIVAL, chat)
    assert len(calls) == 1
    assert "Ken Burns 左移" in out
    assert "不改照片里的船和人" in out
    assert "状态: 未通过" not in out
    shot = mod.parse_shots(out)[0]
    assert mod.classify(shot.kind) == "archival"
    assert mod.shot_problems(shot) == []
    assert "禁止静帧平移" not in shot.blocks["禁止项"]


def test_archival_ban_on_pan_is_rewritten_without_dropping_direction():
    calls = []

    def chat(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return ARCHIVAL_BANNED
        assert "Ken Burns" in prompt
        assert "不要改成角色在动" in prompt
        return ARCHIVAL

    out = mod.generate_shot_markdown(BRIEF_ARCHIVAL, chat)
    assert len(calls) == 2
    assert "Ken Burns 左移" in out
    shot = mod.parse_shots(out)[0]
    assert "禁止静帧平移" not in shot.blocks["禁止项"]
    assert "左移" in shot.blocks["按秒分镜"]
    assert mod.shot_problems(shot) == []


def test_shot_count_follows_brief_not_thirteen_cut_example():
    calls = []

    def chat(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return GOOD  # 两镜，brief 只要一镜
        assert "brief 要求 1" in prompt
        assert "13" in prompt
        return GOOD_ONE

    out = mod.generate_shot_markdown(BRIEF_ONE, chat)
    assert len(calls) == 2
    assert [s.number for s in mod.parse_shots(out)] == [1]


def test_wrong_count_stays_rejected_after_one_rewrite():
    calls = []

    def chat(prompt):
        calls.append(prompt)
        return GOOD

    out = mod.generate_shot_markdown(BRIEF_ONE, chat)
    assert len(calls) == 2
    assert "未通过" in out
    assert "brief 要求 1" in out
    assert len(mod.parse_shots(out)) == 2


def test_character_shot_missing_generated_label_is_rejected():
    unlabeled = GOOD_ONE.replace("这是生成表演。", "")
    calls = []

    def chat(prompt):
        calls.append(prompt)
        return unlabeled

    out = mod.generate_shot_markdown(BRIEF_ONE, chat)
    assert len(calls) == 2
    assert "角色镜总规格未标明是生成表演" in calls[1]
    assert "生成表演" in calls[1]
    assert "史料实拍" in calls[1]
    assert "状态: 未通过" in out
    assert "角色镜总规格未标明是生成表演" in out
    shot = mod.parse_shots(unlabeled)[0]
    assert any("角色镜总规格未标明是生成表演" == p for p in mod.shot_problems(shot))


def test_officer_walk_in_stop_and_raise_passes():
    brief = """一条 8 秒、16:9、共 1 镜的占位短片。
军官走入、停步举手。黄卡其军装锁定。风格一次定死。
不添加武器、台词或结局。
"""
    shot = """
CUT: 01
时码: 00:00.00–00:08.00
镜类: 角色表演
提示词:
  总规格：16:9，8.00 秒。这是生成表演，不是史料实拍。
  锁定元素：占位军官，黄卡其军装，设计锁定。
  按秒分镜：军官从左走入，双脚交替迈开步伐，停步后右手平举。
  禁止项：禁止全身静帧平移。不得写成史料实拍或历史照片。不补 brief 没有的武器或台词。
检查:
  手脚重心：必须连续动
  字先可读：无字
  浅色遮罩：查破边、破洞、手和装具脱落；破了重拍或换镜，不用生成像素补
"""
    calls = []

    def chat(prompt):
        calls.append(prompt)
        return shot

    out = mod.generate_shot_markdown(brief, chat)
    assert len(calls) == 1
    parsed = mod.parse_shots(out)
    assert len(parsed) == 1
    assert mod.shot_problems(parsed[0]) == []
    assert "走入" in parsed[0].blocks["按秒分镜"]
    assert "停步" in parsed[0].blocks["按秒分镜"]
    assert "平举" in parsed[0].blocks["按秒分镜"]
    assert "状态: 未通过" not in out
    assert not mod.still_slide_only("军官从左走入，迈开步伐，停步后举手平举。")


def test_empty_brief_rejected():
    with pytest.raises(ValueError):
        mod.generate_shot_markdown("  \n", lambda prompt: GOOD)


def test_cli_writes_markdown_without_network(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("不应请求真实接口")

    monkeypatch.setattr("requests.post", boom)
    monkeypatch.setattr("src.character_shots._call_glm",
                        lambda prompt, model: GOOD)
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    dest = tmp_path / "out.md"
    assert cli.main([str(brief), "-o", str(dest), "--model", "glm-4-flash"]) == 0
    text = dest.read_text(encoding="utf-8")
    assert "总规格" in text and "锁定元素" in text
    assert "按秒分镜" in text and "禁止项" in text
    assert "交片提醒" in text and "压缩包" in text


def test_cli_default_output_name(tmp_path, monkeypatch):
    monkeypatch.setattr("src.character_shots._call_glm",
                        lambda prompt, model: GOOD)
    brief = tmp_path / "rain.md"
    brief.write_text(BRIEF, encoding="utf-8")
    assert cli.main([str(brief)]) == 0
    written = tmp_path / "rain-分镜提示词.md"
    assert written.is_file()
    assert "交片提醒" in written.read_text(encoding="utf-8")


def test_cli_requires_zhipu_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *_a, **_k: False)
    brief = tmp_path / "brief.md"
    brief.write_text(BRIEF, encoding="utf-8")
    with pytest.raises(SystemExit, match="ZHIPU_API_KEY"):
        cli.main([str(brief), "-o", str(tmp_path / "out.md")])


def test_not_wired_into_documentary_line_and_ken_burns_still_pans():
    for rel in ("autopost.py", "douyin_bridge.py", "upload_pending.py",
                "src/slideshow.py", "src/qc.py", "src/script.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "character_shots" not in text
    for mode in ("in", "out", "panleft", "panright"):
        expr = _kenburns(mode, 90)
        assert "cos" in expr
    assert "iw-iw/zoom" in _kenburns("panleft", 90)
    assert "iw-iw/zoom" in _kenburns("panright", 90)
    qc_text = (ROOT / "src" / "qc.py").read_text(encoding="utf-8")
    body = qc_text.split('"""', 2)[-1]
    assert "freezedetect" not in body
    assert "静帧平移" not in body
