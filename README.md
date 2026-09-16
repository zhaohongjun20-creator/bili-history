# bili-history

军事历史纪录片自动化生产线：AI 编导 → TTS 配音 → 公共领域史料影像 → 自动投稿B站。

## 工作流

```
config.yaml 主题池（避重复）
 → GLM 两步法写稿（大纲 → 逐段展开控字数，约600-900字/期）
 → edge-tts 纪录片配音（zh-CN-YunjianNeural 云健）
 → Wikimedia Commons 配图（主池策略：1-2次搜索拉满，仅 PD/CC 许可）
 → Ken Burns 动效（史料图缓推缓拉，奇偶段交替）+ 字幕烧录 + BGM 铺底
 → biliup 投稿（自制类型，含完整图源/乐源署名）
```

## 用法

```bash
pip install requests pyyaml python-dotenv imageio-ffmpeg edge-tts opencv-python
cp .env.example .env       # 填 ZHIPU_API_KEY（B站登录态复用 bili-autopost-sau）
python autopost.py --dry-run   # 生成成片不投稿，downloads/final.mp4 检查
python autopost.py             # 投稿（tid=203 资讯·军事）
python autopost.py --dry-run 3 # 指定主题池第3个主题
```

## 合规设计

- **只用 Wikimedia Commons 的 PD/CC 授权史料图**，简介逐一署名（文件名/许可/作者）
- BGM 为 Kevin MacLeod CC-BY 曲目，署名
- 内容限定**历史**（二战战役/装备/人物），不涉当代时政
- 投稿声明 AI 辅助制作，欢迎观众指正史实

## 架构

| 模块 | 职责 |
|------|------|
| `src/script.py` | GLM 两步讲稿（大纲+逐段展开） |
| `src/wikiimg.py` | Commons 搜图/下载（许可过滤、节流、429退避、规范UA） |
| `src/narrator.py` | edge-tts 逐段配音 + 时长探测 |
| `src/slideshow.py` | Ken Burns 段落合成 + concat + BGM 混音 + SRT 字幕 |
| `sau_bridge.py` | biliup 运行时（内联自 social-auto-upload，账号与 bili-autopost-sau 共享） |

## 相关项目

- [bili-autopost-sau](https://github.com/zhaohongjun20-creator/bili-autopost-sau) —— 风景素材号（本项目的姊妹项目，发布链路同源）

## License

MIT
