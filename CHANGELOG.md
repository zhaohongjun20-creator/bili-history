# 更新日志

本格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化。

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
