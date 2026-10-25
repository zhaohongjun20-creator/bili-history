# 更新日志

本格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化。

## [0.7.2] - 2026-10-26

真实 dry-run 实测后修复两处历史遗留的音画缺陷（质检首战建功），补交付基建。

### 修复
- **段落成片被 `-shortest` 截断**：`build_section_clip` 的 `-shortest` 把每段视频在
  旁白结束时截断，"旁白说完画面停留 fade 秒再溶解"的设计从未生效，且成片总长比
  设计短 fade×(n-1)（约 5.4s）；叠加最后一段旁白结尾在合成时被切掉。去掉 `-shortest`
  后设计意图恢复：视频总长 = 音频总长 = 旁白总和。
- **字幕时间轴逐段漂移**：`make_srt` 按 duration+tail 累加，但 tail 已被 xfade 重叠
  吃掉（不是顺序播放）——每段漂移 0.6s、末段字幕超出成片约 11s（QC 实测 215s>204s）。
  改为按旁白时长累加，与视频/音频严格对齐。
- **混音峰值 0dB 无余量**：最终 amix 后加 `volume=0.85`（约 -1.4dB 峰值余量），
  消除 QC 报的削波风险。
- cnnum 裸小数丢精度（3.5 → "三"，小数部分被丢弃）→ 完整转换"三点五"。
- 以上渲染语义变更以缓存键 v2（V_CLIP/V_COMPOSE）失效旧产物，重跑自动重做受影响阶段。

### 新增
- GitHub Actions（windows-latest）：安装 requirements.txt 后跑全部测试，不依赖真实密钥。
- `upload_pending.py` 重投前完整性校验（成片>1MB/封面>10KB/manifest 字段），
  已重投（.uploaded）或选题已 published 直接跳过——双闸防重复重投。
- 0.7.0/0.7.1 实际提交推送（此前仅存在于工作区，导致外部审查仍看到 0.6.0）。

### 实测记录（Windows 真机 dry-run，选题 4 敦刻尔克）
- 审查闸门真实拦截两稿：连续两稿均因"拿破仑名言与敦刻尔克年代不符"等 4 项 medium
  问题停在 review-human（此前两版会直接放行）；经 `--approve-review` 人工放行后出片。
- 断点复用实测：approve 后重跑，讲稿/配图池/审查全部走缓存，零重复 API 消耗。
- CosyVoice2 本地批量配音 10 段正常出声；段落 4 路并行渲染 ~50s/段；成片 50.7MB。
- QC 首跑即拦截上述字幕超时与削波两项真实缺陷，修复后重跑通过（缓存键自动失效旧渲染）。

## [0.7.1] - 2026-10-25

复审闭环 + 锁存活判定 + 投稿未核实闸门 + 素材逐段绑定（二轮审查反馈整改）。

### 新增
- **审查处置闭环**（P1-4 收口）：
  - `fix` + 改题 → 应用改题后**强制复审一轮**，复审 pass 才继续；复审仍 fix → 人工；
  - `fix` 无修法 / 复审仍 fix → 新状态 `review-human`（人工闸门）：
    `--redo N` 重做，或人工阅读问题清单后 `--approve-review N` 放行（manifest 记录 approved_by=manual）；
  - 确定性检查（标签词/残留数字/超30字/无钩子）**全量硬拒**，不再"记录问题后继续"；
  - verdict 不在 pass/fix/reject 内视为无效返回，重试后进入 failed(review)。
- **锁存活判定**（P1-2 收口）：接管前查锁内 PID 是否存活
  （Windows 用 OpenProcess+GetExitCodeProcess，规避 os.kill(0) 误杀与僵尸句柄误判）；
  pid 存活 + 心跳(mtime)新鲜 → 绝不接管（长任务不靠时长猜测）；心跳超 1h 未刷新 → 判挂死接管；
  pid 已退出 → 不限时长接管。流水线各阶段间刷新锁心跳。
- **投稿超时未核实闸门**（P1-1 收口）：上传超时后 manifest 记 `publish.status=uncertain`，
  重跑**拒绝自动重投**（客户端没收到成功响应 ≠ 平台没收到视频）；人工到创作中心核实后：
  `--confirm-upload N`（确认未收到，重投一次）/ `--mark-uploaded N --bvid BV号`（已收到，直接标记完成）。
- **配图逐段绑定**（P1-5 收口）：`assignment` 分配表（段→池图索引+复用标记）存 manifest，
  **审查与渲染读同一份**——审查看到的就是实际用的图；每段写结构化台账
  `materials/assets.json`（来源标题/页面URL/许可/作者/检索词/复用自第几段），
  简介署名由台账去重生成；每张图最多服务 2 段，池撑不起即失败待处理，不循环塞无关图。
- cnnum 边界：年份区间支持中文破折号/`至`（1941—1945年）、≥5位裸数字（100000→十万）、
  千分位逗号（3,000万→三千万）。

### 修复
- **审查确定性检查误杀装备编号**：cnnum 有意保留 T-34/B-17（字母邻接数字），
  旧"规范化后残留数字"检查会把它们当残留数字→整稿拒发；现只查独立残留数字。
- 年份区间 `1941-1945年` 只认半角连字符、`100000`（≥5位）不被转换留残留、`3,000万` 被拆读。

### 测试
- 80 项（+21）：cnnum 边界 11 项、锁存活/挂死/并发 7 项、审查硬拒/编号豁免/未知 verdict、
  集成层复审流转（fix→改题→复审 pass）、人工闸门与 --approve-review 放行、
  超时未核实拒重投→confirm 后重投、配图分配台账与复用标注、池不足失败。

## [0.7.0] - 2026-10-25

可靠性大版本：统一状态机 + 分阶段断点续跑 + 自动质检 + 启动前检查（对应 17 项整改方案的第一、二阶段与质量项）。

### 新增
- **选题状态机**（`src/store.py` 重写）：`pending → generating → review → ready → published`，
  失败进 `failed`（记录阶段/原因/重试次数，默认重试上限 3 次）；`ready`（成片待发）与
  `published` 分离；`review-rejected` 出自动池需 `--redo` 才回。旧库自动补列迁移
  （历史记录不丢），选题原子领取（两进程不会撞同一题），崩溃残留的 `generating` 超时后可抢占。
- **分阶段断点续跑**（`src/jobs.py` + `autopost.py` 重构）：每期任务独立目录
  `data/jobs/<job_id>/`（manifest 原子写入），讲稿/审查/配音/配图/段落渲染/合成/质检/投稿
  八阶段全带缓存键（输入内容+模型+参数版本），重跑自动复用未变化阶段；段落渲染与
  配音按段即时持久化进度（第 10 段失败只重做第 10 段，并行已完成段落也入账）；
  BGM 选择入 manifest，断点不换曲。新增 `--redo`（重做）、`--fresh`（弃缓存）、
  `--publish-only`（只投稿已过质检成片）。
- **启动前检查**（`src/preflight.py`）：配置完整性/密钥占位符/BGM 目录/ffmpeg/网络连通/
  发布登录态（只在发布时查），一次报全部问题，不花 API 额度先暴露配置错。
- **成片自动质检**（`src/qc.py`）：分辨率/帧率、音视频时长错位、静音/削波（volumedetect）、
  黑帧（blackdetect）、字幕时间轴（倒退/超长/空）、封面可解码；不合格置 `failed(qc)` 拒发，
  阈值可在 config.yaml `qc` 段调。
- **审查闸门可靠性**（`src/review.py`）：服务超时/非法 JSON 重试后仍失败 → 抛
  `ReviewUnavailable`，选题置 `failed(review)` 等待重跑，**不再自动放行**；审查官输出
  带段落号与严重度，severity=high 的问题让成片停在 `ready` 等人工核实
  （`--publish-only` 发布），AI 审查通过不再等于免检。
- **投稿可靠性**（`sau_bridge.py`）：错误分类（`RateLimited` 21566 风控 / 登录失效 /
  通用失败 / `code=-1` 超时未核实），上传 30 分钟超时兜底；错误信息去 ANSI + 凭证
  脱敏（SESSDATA 等）；登录态路径可配置（config `bilibili.account_file` 或环境变量
  `BILIUP_ACCOUNT_FILE`），manifest 记录 bvid 防重复投稿。
- **互斥锁**（`src/singleinstance.py`）：`O_CREAT|O_EXCL` 原子获取（旧版"检查+写入"
  有竞态，双启动可同时持锁），锁文件带 PID/主机名，6 小时过期接管，上下文管理器
  保证释放；`--publish-only` 也持锁。
- 依赖清单 `requirements.txt`；旧版 `downloads/manifest.json` 由任务目录 manifest 取代
  （`upload_pending.py` 通道保留，修复其引用不存在的 `bilibili.tags` 键）。

### 修复
- **审查返回裸 JSON（无代码围栏）时 `IndexError`** → 旧版落入"审查通道异常（放行）"
  被静默绕过审查；现正确解析。
- 审查通道异常自动放行（P1-4 核心）：异常不再继续生产流程。
- 段落渲染失败后重跑全部重渲染（进度未持久化）→ 按段即时入账。
- `os.environ["ZHIPU_API_KEY"]` 硬取键 → preflight 拦截 + 代码内 `.get` 容错。
- `bgm_volume` 缺失时 KeyError → 默认 0.22 + preflight 校验。

### 测试
- `tests/`：59 项（状态机/原子锁/manifest/缓存键/preflight/审查分类/质检/SRT 解析/
  投稿错误分类），含 3 条 mock 全外部依赖的流水线集成测试（全流程一次走通、
  失败重跑全复用、单段失败只重做该段），`python -m pytest tests/` 不花真实额度。

## [0.6.0] - 2026-10-25

配音、质量、并发三线大版本：本地大模型配音 + 双层审查闸门 + 连发基建。

### 新增
- **CosyVoice2 本地大模型配音**（Apache-2.0，RTX 4070 GPU 推理）：instruct2 参考音+纪录片指令
  配方（实测 F0 126Hz 低沉男声），子进程批量合成，失败自动降级 edge→熔断池→Piper 旧链
- **中文数字规范化层**（`src/cnnum.py`）：旁白送合成前阿拉伯数字全部转汉字——年份逐位读
  "一九四四"、数量按中文进位"三百五十"、日期/百分比/序数全覆盖，B-17/T-34 等装备编号不动。
  根治 CosyVoice TN 把"1944年"读成"十九四四年"的事故
- **发布前审查闸门**（`src/review.py`，渲染前介入）：
  - 确定性层（0 成本）：标题结构标签词/无钩子/超长、规范化后残留数字 → 秒拒
  - GLM 无记忆审查官：全新对话只看成品，审史实硬伤（年代穿越/武器错配）、配图匹配、
    标题质量 → pass / fix（采纳改题）/ reject（不发布，宁缺毋滥）
- **数据反馈闭环**（`src/stats.py`）：Playwright 登录态抓取B站空间播放数据 → SQLite 快照
  → 每次投稿后自动生成频道战报（均值/涨幅榜）
- **标题钩子闸门**：4 类钩子结构（反差错位/悬念留白/具体数字/错误类比）提示词硬约束 +
  正则检测 + 平铺直叙自动重写（数据结论：钩子标题 vs 平铺标题播放差 100 倍）
- **产线互斥锁**（`.autopost.lock`）：批量连发与定时任务并发防撞（选题重复/GPU 抢占），
  6 小时自动失效防死锁
- **风控重投通道**（`upload_pending.py`）：B站 21566 投稿频率拦截时，成片+封面+manifest
  备份到 `output/pending/`，冷却后一键重投不重做
- CosyVoice synth_cli 单实例锁：孤儿推理进程自动清理（防 GPU 被双进程分食）

### 修复
- **配图相关性闸门**（`src/wikiimg.py`）双层：
  - 人名撞地名（搜 Montgomery 将军 返回阿拉巴马州首府照片）
  - 通用词撞车（"battle" 匹配到 Sussex 的 Battle 街道、罗摩衍那壁画）→ 通用战争词
    黑名单 + 纯通用查询整单拒收 + 同系列图最多 2 张
- 段渲染并行化后 CPU 饿死超时：workers 6→4，单段超时 600s→1500s
- GLM 大纲偶发把钩子结构标签（"具体数字："）写进标题 → 提示词禁止 + 检测器拒收
- 讲稿字数不足自动重试、JSON 解析双候选容错

### 性能
- 段落渲染串行→并行（独立 ffmpeg 子进程）：单条视频渲染 40min → ~6min
- CosyVoice 模型单次加载批量出片：12 段 ~5min（GPU 推理）

## [0.5.0] - 2026-10-10

对标头部产品（MoneyPrinterTurbo / MoneyPrinterPlus）的四项硬指标升级。

### 新增
- 字幕样式：白字黑边 + 轻阴影（字号 15→17，描边 1→2），`subtitle.force_style` 可配置覆盖
- BGM ducking：`sidechaincompress` 广播级人声压低——旁白开口 BGM 自动下沉，说完缓升（原固定音量 amix）
- 配音音色池：云健（浑厚）/ 云扬（播音腔）/ 云泽（沉稳低音）按天轮换，`narrator.voices` 配置
- 转场轮换池：`fade / dissolve / wipeleft / circleopen / fadeblack` 纪录片向轮换，`transition.styles` 配置

### 修复
- gitignore 排除 `lyricvideo/` 实验目录（曾误入提交 3 万+文件）

## [0.4.0] - 2026-10-08

### 新增
- 可插拔 TTS 双后端：智谱 cogtts（大模型，更自然）+ edge-tts 兜底，失败自动降级
- 沙包式错误处理：余额不足/网络异常不打断产线

## [0.3.0] - 2026-10-07

### 新增
- 4-5 分钟长片：12-16 段讲稿（GLM 两步生成：大纲 JSON → 逐段扩写，字数达标）
- 名言引用：AI 自主判断是否引用（强约束"只用真实存在且广为流传的名言"）
- 四向 Ken Burns 运镜轮换（推近/拉远/左移/右移）
- xfade 交叉溶解转场 + 尾部留白对齐
- manifest.json 元数据持久化：中断可恢复发布

### 修复
- xfade offset 重复累加（31.7s vs 199.4s）
- ffmpeg filter 图死锁（apad 无限流 + 11 输入 xfade，2.9GB 内存）→ 三步合成

## [0.2.0] - 2026-10-06

### 新增
- AI 自主判断玩梗文案与 Twemoji 表情贴纸（CC-BY 4.0，CDN 运行时下载）

### 修复
- B站分区 tid 239（原军事分区 203 已被官方下线）

## [0.1.0] - 2026-10-05

### 初版
- 军事历史自动化纪录片流水线：GLM 讲稿 → Wikimedia 配图（PD/CC 授权过滤）→ edge-tts 配音 → Ken Burns 成片 → B站投稿

## [0.5.1] - 2026-10-10（晚）

### 新增
- 抖音分发：`douyin_bridge.py`（Playwright 驱动创作中心，扫码登录一次永久保存登录态）
  - 强制直连（`--no-proxy-server`，国内站走系统代理反而挂起）
  - `--disable-gpu`（抖音登录页 canvas 动画触发 Windows 有头 Chromium GPU 崩溃）
  - 描述编辑器多选择器兜底、全步骤截图留档、发布结果多重确认
- autopost 接线：B站发布后自动分发抖音（`douyin.enable` 开关，失败不影响B站）
