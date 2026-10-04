# 变更记录

写给使用者：每条说用户看得见的变化，以及要不要做什么。

## 0.1.0（2026-10-03，首次公开）

- 四步流程：提取（`flowocr-ocr`）→ 建轨（`flowocr-tracks`）→ 输出字幕稿与 SRT（`flowocr-render`）→ 特效渲染（`flowocr-typeset`）。
- 起止时间精确到帧，打字机式逐字出现的文字还原成逐字显出。
- 叠加 ASS：每条字留在原位；字幕稿可在 Aegisub 里改（含改成译文），改完重跑第 4 步。
- 匹配原文：内置原神 / 崩坏：星穹铁道 / 绝区零的游戏文本包匹配器（包由 [game-text-data](https://github.com/caca2331/game-text-data) 打），名牌译名查说话人与短名词表。
- 读游戏文本包时核对每个文件的哈希和每张表的内容契约版本：包坏了、或契约主版本不是本工具认得的，报错并说明是哪张表；包里出现没见过的枚举取值（如原神语音字幕的新样式）时打一行警告，照现有规则归类。
- 扩展：匹配器、输出预设、特效都可以用自己的 Python 文件（例子在 `examples/`）。
- 安装：`uv tool install "flowocr[nvidia]"`（或 `[cpu]`），也可以从源码 `uv sync`；推理只用 ONNX Runtime（不依赖 Paddle），NVIDIA 版依赖约 2 GB、CPU 版约 0.3 GB。
- 模型首次运行时自动下载；离线机器用 GitHub Release 上的模型包，`flowocr-models install` 装。
- 只在 Windows 上验证过（别的平台起跑时打警告）；NVIDIA 显卡加速，没有显卡时用 CPU。
