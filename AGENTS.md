# flowocr

把视频里**出现过的所有文字**抓出来（不只是底部字幕带），每条带起止时间戳，再按位置和时间聚成若干条"文字轨"。
`README.md` 和 `docs/` 用中文写，是正式文档；这一页只放每次都用得上的规则和按动作找入口的索引。
若仓库根有 `CLAUDE.local.md`（维护者的私有补充，公开仓库里没有），开工前先读。

```text
视频 -> 采样 -> 检测 -> 识别 -> 时间合并（起止） -> 时空聚类（对白 / 字幕 / UI / 水印…） -> <tag>-tracks.json -> SRT / 字幕稿 -> 叠加 ASS
```

四个阶段：1 提取（`flowocr.extract`）、2 分析（`flowocr.analyze`）、3 输出 SRT 和可在 Aegisub 里编辑的字幕稿（`flowocr.output`）、4 特效渲染（`flowocr.typeset`）。

**两个用途**：①视频文字帧翻译（漫画翻译式，多位置 + overlay）；②游戏 / 影视字幕，往往能找到原文剧本做匹配，
产出用于翻译 overlay 与语音识别去重。用途②把准确性重心从字符错误率挪到了**漏条 / 幽灵条目 / 切分口径 / 时间精度**上。

## 常用命令

```powershell
uv sync --frozen --extra nvidia --group dev      # 开发 venv（editable；CPU 机器 --extra cpu）
python dev_tools\pyrun.py -m flowocr.extract.run_ocr2 <video> --out <obs.jsonl>                       # 阶段 1
python dev_tools\pyrun.py -m flowocr.analyze.build_tracks <obs.jsonl> --outdir <dir> --tag <tag>       # 阶段 2
python dev_tools\pyrun.py -m flowocr.output.render <matched.json> --preset matched_srt --opt lang=cn   # 阶段 3
python dev_tools\pyrun.py -m flowocr.typeset <x.script.ass>                                            # 阶段 4（叠加预设会连跑 3、4）
python dev_tools\pyrun.py tests\test_guards.py      # 共享层守卫（秒级；改完代码先跑）
python dev_tools\check_docs.py                    # 文档检查（链接、锚点、公开文档不指向私有文件）
python dev_tools\track_health.py <轨.srt> --hours <h>   # 改完 build_tracks 先跑：只看产物的体检
```

完整命令、各驱动和验证工具见 [开发指南](docs/dev-guide/README.md)。

## 护栏

- **每个操作都要有 CPU 版**（owner 2026-09-23）：没 GPU 也要能跑，不求结果一致、不求并发。
- **长任务跑着的时候不改 `dev_tools/` / `explore/`、不提交**：provenance 的 `git_head` 记 HEAD，中途提交或改文件会让一批产物对不上代码（[长任务的纪律](docs/dev-guide/README.md#长任务的纪律)）。
- **先定指标再看结果，先怀疑评测口径再怀疑算法**；写进文档的每个数都要能从产物里数回来（[验证与报数](docs/dev-guide/verification.md)）。
- **验证按"跑多长"分档**（owner 2026-09-08）：一般改动 ≤30 分钟代表性片段，阶段性结论 120–240 分钟，再往上先说清为什么。
- **疑似真漏按三档报**（owner 2026-09-06）：纯标点 / 1–2 字 / 有内容 ≥3 字；跨臂比命中集合，代价用重复认领量（[报数口径](docs/dev-guide/verification.md#报数口径)）。
- **判读规则不求完备、不做 VLM 判读层**；OCR 之后的方向是决定式清噪 → LLM 清噪（可选，还没实现）→ 合并；不为单个病例加特判；可以按游戏特化（owner 2026-09-03 / 09-11）。
- **改动凭什么进默认只看一处**：[采纳规则](docs/architecture/defaults.md#采纳规则改动凭什么进默认)——正确性优先于提速、先有等价性或质量证据、资源占用也算收益、
  没提升但更合理更简洁也可以采纳、默认值全局统一不按素材分叉。
- **机器侧的一级产物只有一份 `<tag>-tracks.json`**（匹配产物同理），读写只走 `flowocr.artifacts.tracksio`，别的格式由阶段 3 投影；下游遍历 tracks 用白名单 `kind == "region"`（[产物契约](docs/architecture/README.md#产物契约)）。
  **人工编辑层是阶段 3 的字幕稿 ASS**，阶段 4 只读它、不回读阶段 2（owner 2026-09-27，[字幕稿](docs/architecture/artifacts.md#5-字幕稿阶段-3-写阶段-4-读)）。
- **公开文档只指向公开内容**：`docs/plans/`、`docs/reports/`、`docs/archive/`、`explore/` 下的实验不随公开仓库发布，公开的文档和代码不链接、不写出其中的文件路径；要引用依据就把结论写进正文。

## 项目约定

- 目录：
  - `src/flowocr/` 放生产代码（四个阶段的子包 extract / analyze / output / typeset，加 artifacts）。
  - `dev_tools/` 放还在维护的工具：驱动、评测、对账；改了要跟着更新文档和指标。
  - `explore/` 下每个一次性实验一个目录、自带 venv，缓存走 `explore/env.ps1` / `explore/env.sh`；可整目录删，结论写进文档。实验本身不公开。
  - `tests/` 放守卫与单元测试（`tests/test_guards.py` 是入口）。
  - `examples/` 放用户扩展（匹配器 / 输出预设 / 阶段 4 的扩展效果）的可复制例子。
  - `LICENSE` 是本项目的 GPL-3.0-or-later；`LICENSES/` 放第三方模型的许可与我们改动的说明。
  - `data/` 只留本地、整个 ignore（owner 2026-09-20）；`out/`、`tmp/` 可整目录删；`models/`（下载的模型）与 `gametext/`（游戏文本包）也只在本地、不进版本库。
- 路径只经 `flowocr.paths` 解析：代码根是这份 checkout，数据根在 worktree 里解析回主 checkout；读 `data/` 一律走 `data_root()`。
- 依赖只写 `pyproject.toml`（锁是 `uv.lock`）。
- 文档：
  - 公开文档的地图在 [docs/README.md](docs/README.md)；使用手册在 `docs/manual/`，开发指南在 `docs/dev-guide/`，架构说明在 `docs/architecture/`。
  - 计划、报告、归档是维护者的过程记录，不公开。
  - 已完成的事实改在原处，不在文末追加更正。
- 分支：`dev` 上日常开发（不公开）；公开的 `main` 只由 `dev_tools/publish.py` 从 `dev` 生成、不手工提交；外部 PR 以 `main` 为目标，合并后再合回 `dev`。
  并行工作各开一个 `.worktrees/<slot>/`（[worktree 规矩](docs/dev-guide/README.md#在-worktree-里并行开发)）。每次提交后报告当前分支和 commit hash。

## 按动作找入口

| 要做的事 | 先读 |
| --- | --- |
| 报任何数、写任何"更好 / 更差"的结论之前 | [验证与报数](docs/dev-guide/verification.md) |
| 跑 A/B、报墙钟 | [A/B 的规矩](docs/dev-guide/verification.md#ab-的规矩)、[墙钟](docs/dev-guide/verification.md#墙钟按差幅分档读owner-2026-09-05) |
| 改提取层（`run_ocr2`、解码、推理、复用判据） | [架构说明](docs/architecture/README.md) 的帧身份 / 推理服务 / obs 复用判据三节；改完过打字机真值 |
| 改建轨、聚类、常驻 UI、名牌 | [已知的坑](docs/dev-guide/pitfalls.md) |
| 翻或加一个默认值 | [defaults.md](docs/architecture/defaults.md) |
| 改产物格式、写读产物的下游 | [artifacts.md](docs/architecture/artifacts.md) |
| 装依赖、下模型 | [开发指南·环境](docs/dev-guide/README.md#环境) |
| 开 worktree、合并分支 | [在 worktree 里并行开发](docs/dev-guide/README.md#在-worktree-里并行开发) |
| 发布到公开仓库 | `python dev_tools/publish.py check`（配置在 `.publish.toml`） |
| 找其他文档 | [docs/README.md](docs/README.md) |
