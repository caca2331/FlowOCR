# 文档地图

公开文档每份一行：主题和状态。状态的含义：

| 状态 | 含义 |
| --- | --- |
| 规范 | 现行行为契约与开发规矩，改行为前先读 |
| 含实测 | 带实测读数，数字可能已过时 |

单份文档的现状以它自己开头的状态说明为准。agent 按动作找入口看 [AGENTS.md](../AGENTS.md)。

计划、实验报告与归档是维护者的过程记录，不在公开仓库里；正文里写作"x 报告 / x 计划"的就是它们，需要的结论已经写进正文。

## 先读

| 文档 | 主题 | 状态 |
| --- | --- | --- |
| [manual/README.md](manual/README.md) | 使用手册入口：先选要做的事、安装、第一次跑通、只要字幕 SRT、产物怎么读（面向使用者，下面四份同） | 规范 |
| [manual/script.md](manual/script.md) | 使用手册·字幕稿：画面文字翻译、改稿与出特效、时间疑点 | 规范 |
| [manual/matching.md](manual/matching.md) | 使用手册·对上原文：游戏文本包、Naninovel 剧本、画哪些、名牌、译文怎么分行 | 规范 |
| [manual/burn.md](manual/burn.md) | 使用手册·烧进视频 | 规范 |
| [manual/reference.md](manual/reference.md) | 使用手册·参考：输出预设、精度档位、设备与 TensorRT、识别范围与语言、扩展、排错 | 规范 |
| [dev-guide/README.md](dev-guide/README.md) | 开发指南 | 规范 |
| [architecture/README.md](architecture/README.md) | 架构说明 | 规范 |

## 开发手册

| 文档 | 主题 | 状态 |
| --- | --- | --- |
| [dev-guide/verification.md](dev-guide/verification.md) | 验证与报数 | 规范 |
| [dev-guide/pitfalls.md](dev-guide/pitfalls.md) | 已知的坑 | 规范 |
| [architecture/defaults.md](architecture/defaults.md) | 当前默认值：是什么、凭什么、以及量过之后没采纳的 | 规范 · 含实测 |
| [architecture/artifacts.md](architecture/artifacts.md) | 产物格式：一份富信息 JSON + 按需导出 | 规范 |
| [architecture/pipeline.md](architecture/pipeline.md) | OCR 全流程的技术路径：每一段做什么、哪些判据以采样间隔为单位 | 含实测 |
