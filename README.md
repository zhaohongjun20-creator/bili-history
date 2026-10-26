# bili-history

军事历史纪录片**全自动日更生产线**：AI 编导 → 神经配音 → 公共领域史料影像 → B站+抖音+西瓜三平台分发 → 数据战报回读。

> 实跑记录：连续运行数周，[CHANGELOG](CHANGELOG.md) 记录全部演进。

## 生产线全貌

```
config.yaml 主题池(30个, 状态机自动跳过已做/失败可恢复)
 → 启动前检查(preflight)：配置/密钥/路径/BGM/ffmpeg/网络，不花额度先暴露问题
 → GLM 两步写稿（大纲JSON → 逐段扩写控字数；名言只用真实流传的）
 → 无记忆审查官（渲染前）：确定性检查 + GLM 审史实/配图/标题
   （服务异常不自动放行→等待重试；高危问题→成片停在待人工核实）
 → 配音四级链：edge-tts云端神经 → 音色熔断池 → Piper本地神经 → SAPI(默认禁用)
   （CosyVoice2 本地大模型优先；按段缓存只补缺段）
 → 质量闸门：神经音色全灭时宁缺毋滥，拒绝机械音上线
 → Wikimedia Commons 配图（主池策略省请求；仅 PD/CC 许可，逐一署名）
 → Ken Burns 四向运镜 + Twemoji 贴纸 + 转场轮换池(fade/dissolve/wipe/circle/black)
 → BGM sidechain ducking（旁白开口自动压低配乐）
 → 三步合成避 filter 死锁 → 字幕烧录(描边+阴影)
 → 成片自动质检(qc)：规格/音视频时长/静音削波/黑帧/字幕时间轴/封面
 → 投稿：B站(biliup CLI, 失败分类+防重复投稿) + 抖音(桌面客户端UI自动化) + 西瓜(抖音同步勾选)
 → 数据反馈闭环：无头浏览器回读主页全量播放数 → SQLite 快照 → 每日战报(频道聚合+涨幅榜)
```

## 断点续跑（可靠性核心）

每期视频独立任务目录 `data/jobs/<job_id>/`（manifest 记录阶段状态/缓存键/耗时/失败原因）：

- 任何阶段失败后重跑自动续走：讲稿/审查/配音/配图/段落渲染/合成/质检全部按
  缓存键（输入内容+模型+参数版本）复用，输入没变不重复花钱；
- 段落渲染与配音按段即时持久化进度：第 10 段失败只重做第 10 段；
- 投稿失败分类入库（风控限流/登录失效/超时未核实），成片与封面保留在任务目录，
  冷却后重跑自动续投，不会重新制作内容；
- 互斥锁为**操作系统级文件锁**（Windows msvcrt / POSIX flock，内核保证原子）：
  进程死亡（崩溃/被杀）由内核自动释放，无需任何过期等待；若进程活着但挂死
  会阻塞生产（正确性优先）——锁的 sidecar 文件记录持有者 pid，
  人工确认挂死后 `taskkill /PID <pid>` 即恢复。

## 无人值守调度（Windows）

- 计划任务每日 20:00，`run_hidden.vbs` **隐藏运行**（无窗口不打扰/不可误关）
- `run_daily.bat` 自带代理探测：Clash 活着走代理取 Wikimedia，没开自动直连
- 电池/睡眠已解锁，错过自动补跑

## 用法

```bash
pip install -r requirements.txt
cp .env.example .env             # 填 ZHIPU_API_KEY（GLM 免费模型即可）
python autopost.py --dry-run     # 只出片不投稿，data/jobs/<id>/final.mp4 检查
python autopost.py               # 完整发布(B站+战报)
python autopost.py 15            # 指定主题池第15个
python autopost.py --redo 15     # 重做某题（复用未变化的产物缓存）
python autopost.py --redo 15 --fresh   # 连缓存一起废弃
python autopost.py --approve-review 15 # 人工阅读问题清单后放行待审核讲稿
python autopost.py --publish-only      # 只投稿已过质检的成片（人工核实后）
python autopost.py --confirm-upload 15 # 投稿超时后人工核实"平台未收到"，重投
python autopost.py --mark-uploaded 15 --bvid BVxxxx  # 人工核实"已收到"，直接标记完成

python -m pytest tests/          # 单元+集成测试（不花真实额度）
```

抖音/西瓜分发（可选）：
```bash
python douyin_bridge.py login    # 首次扫码，登录态存本地 profile
# autopost ⑦步自动分发；网页 session 被客户端顶掉时走客户端 UI 操纵
```

## 合规设计

- 史料图**只用 Wikimedia Commons PD/CC 授权**，简介逐一署名（文件/许可/作者）
- BGM 为 Kevin MacLeod CC-BY 曲目，署名
- 内容限定**历史**（二战战役/装备/人物），不涉当代时政
- 抖音无公开上传 API，采用与 social-auto-upload 等开源项目一致的浏览器/客户端自动化路线

## 相关项目

- biliup（B站投稿 CLI）、social-auto-upload、MoneyPrinterTurbo —— 本项目借鉴的优秀开源

## License

MIT
