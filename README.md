# bili-history

军事历史纪录片**全自动日更生产线**：AI 编导 → 神经配音 → 公共领域史料影像 → B站+抖音+西瓜三平台分发 → 数据战报回读。

> 实跑记录：连续运行数周，[CHANGELOG](CHANGELOG.md) 记录全部演进。

## 生产线全貌

```
config.yaml 主题池(30个, 自动跳过已做)
 → GLM 两步写稿（大纲JSON → 逐段扩写控字数；名言只用真实流传的）
 → 配音四级链：edge-tts云端神经 → 音色熔断池 → Piper本地神经 → SAPI(默认禁用)
 → 质量闸门：神经音色全灭时宁缺毋滥，拒绝机械音上线
 → Wikimedia Commons 配图（主池策略省请求；仅 PD/CC 许可，逐一署名）
 → Ken Burns 四向运镜 + Twemoji 贴纸 + 转场轮换池(fade/dissolve/wipe/circle/black)
 → BGM sidechain ducking（旁白开口自动压低配乐）
 → 三步合成避 filter 死锁 → 字幕烧录(描边+阴影)
 → 投稿：B站(biliup CLI) + 抖音(桌面客户端UI自动化) + 西瓜(抖音同步勾选)
 → 数据反馈闭环：无头浏览器回读主页全量播放数 → SQLite 快照 → 每日战报(频道聚合+涨幅榜)
```

## 无人值守调度（Windows）

- 计划任务每日 20:00，`run_hidden.vbs` **隐藏运行**（无窗口不打扰/不可误关）
- `run_daily.bat` 自带代理探测：Clash 活着走代理取 Wikimedia，没开自动直连
- 电池/睡眠已解锁，错过自动补跑

## 用法

```bash
pip install requests pyyaml python-dotenv imageio-ffmpeg edge-tts playwright
pip install piper-tts            # 可选：edge 被封时的本地神经配音
cp .env.example .env             # 填 ZHIPU_API_KEY（GLM 免费模型即可）
python autopost.py --dry-run     # 只出片不投稿，downloads/final.mp4 检查
python autopost.py               # 完整发布(B站+战报)
python autopost.py 15            # 指定主题池第15个
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
