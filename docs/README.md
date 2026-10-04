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
| [manual/README.md](manual/README.md) | 使用手册：安装、三步跑一段视频、产物、匹配原文（面向使用者） | 规范 |
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
