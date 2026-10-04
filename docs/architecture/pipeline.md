# OCR 全流程的技术路径

这一页回答"默认跑一遍，每一段在做什么、用什么单位"（2026-09-26 按现行管线重写）。**参数的值和依据只在
[defaults.md](defaults.md)，这里不抄第二份**；进程、推理服务、帧身份这些系统级约束在[架构说明](README.md)。
这里独有的是[哪些判据以采样间隔为单位](#4-哪些判据以采样间隔为单位)那张表。

```text
视频 ─(1) run_ocr2─▶ obs.jsonl（每行一个框；带在线回抠证据 edge）
     ─(2) build_tracks─▶ <tag>-tracks.json（一级产物，provenance 在里面；建轨末尾按 obs 证据结算回抠，帧级时刻）
                          └─ 逐区域 SRT / main.srt（flowocr.output.export 投影）、字幕稿 *.script.ass（flowocr.output.script，render 的预设）
                             ─(4) flowocr.typeset─▶ 叠加 ASS（叠加预设 default / default_all / dev 两步连跑）
                          └─ 匹配（flowocr-match → *-matched.json）→ 译文字幕 / 叠加；评测：game_align / script_align / hit_delta / missed_trace
```

默认路径**全屏扫、不排区、不预扫**；自选范围（识别组）是可选的。回抠没有单独的命令：测量在 (1) 在线做，结算在 (2) 末尾做；
obs 没有证据就用默认设置重跑 (1)。

## 1. 观测：`flowocr.extract.run_ocr2`

| 环节 | 做法 |
| --- | --- |
| 进程 | 监督者起一个 worker（`--workers 1`）；worker 里是管线进程 + 只解码进程（分片）+ 回抠进程 + 一个 ORT 推理服务（det 和 rec 同一个服务）。硬解中途交付错时整棵树收尾、退回软解重跑那一趟（[帧身份与解码](README.md#帧身份与解码)） |
| 解码 | ffmpeg（GPU 上默认 NVDEC 硬解，CPU 上软解）按**时间网格**出采样帧和 540p 灰度辅助流（`flowocr.extract.framegrid`：第 k 格是帧 `ceil(k × 素材帧率 / fps)`，不整除也不累积误差，采样帧 ⊆ 辅助流）；帧身份是 pts 帧号，`t_us` 是真实 pts。解码分片、预取按字节封顶 |
| 检测 | 每个采样帧全屏 det（`PP-OCRv6_medium_det` 的官方 ONNX，ORT；长边压到 960）。前处理自己写（`fast_det`，去掉 Paddle 之前和 PaddleX 对拍过逐字节相同）、后处理是照抄的一份（`detpost`），攒批、下一批的前处理与推理和这一批的后处理重叠 |
| 框级复用 | 第二版判据（`reuse_v2`）：按手里文本的类别分门、框宽门、链首跨空档；框内灰度相关过门就沿用上一帧文本、不送 rec，到期强制重识别；沿用错了的由回补改写 |
| 识别 | 只对新框 / 变了的框 / 到期刷新的框裁剪送 rec（`PP-OCRv6_medium_rec` 官方 ONNX，ORT）；按目标宽分桶组批（宽度收到 320 的倍数，每个形状一个 session、共用显存），异步在途 |
| 在线回抠证据 | 辅助流上，链首 / 文本变 / 链断时量回抠的像素信号（首字 / 全字 / 消失的证据窗），写进 obs 行的 `edge`；被回补改写的那一段作废 |
| 写出 | 每个框一行 JSON 流式追加；跑完改写首行 `_meta`（生效参数 `config`、采样 / 辅助流网格、代码指纹、设备回退原因…） |
| 完成契约 | `_meta.complete = 推进帧数 ≥ 请求帧数 × 0.98`；没跑完退出码 3，pts 不合法退出码 4，驱动按它决定跳过还是重跑 |

每行：`frame, t_us, box, poly, text, conf(=rec_score), reused, edge?`。依据与各旋钮的值：[defaults.md](defaults.md) 1.1–1.28。

## 2. 轨：`flowocr.analyze.build_tracks`

**2.1 时间合并 `build_runs`**（观测 → run = "同一段文字在屏上的一次存续"）：逐采样帧先把 det 切碎的在场一行拼回去（`--rejoin-split`），
再把观测配到在场的 run：文本相似度过 `--sim` 且 IoU 过 `--iou`；打字机中间态单判（框只向一侧长、文本是前缀、框高比过门）；前缀加分要短边 ≥ `--prefix-min` 字；
断档容忍 `(gap_frames + 0.5) × frame_us`；文本多数投票；同帧的两个框不给一条 run 记两次观测。

**2.2 三层空间聚类**（run → 行轨 → 60 s 窗内区域 → 跨窗槽位）：行轨按中位质心；窗内区域按并查集（Δcy、x 重叠或中心对齐、字高比门）；
跨窗 `stitch_slots` 按槽位签名（成员几何的逐分量中位数）缝合，收敛后对着最终签名重分配。实验性的另两种区域构建器：`--cluster learned` / `lines`。

**2.3 打标**：常驻 UI 两条判据（文本 + cue 占比、框位 + 时间占比）、名牌（正文带上方、时长够、词表小、重复文本占比，按时间分窗）、振り仮名（几何）。
**过滤不删事件、只打标**。

**2.4 判读与主轨**：区域特征 → `label_region`（noise / clock / static-overlay / text-wall / subtitle / dialogue-or-caption / ui-static / misc）→ `primary_score` → `pick_main`
（排名和接受分开：分数 > 0、标签可当主轨、没有在动；接受不了就"没有主轨"）→ `--main-band` 把同一条字幕带（y 和 x 都对得上）的区域并成 `main.srt`。

**2.5 切分与导出**：`--srt-mode segment`（按同屏行集合切段、"行只增不减"的相邻段并掉）；框位 UI 和注音在切分之前就和正文分开；< 150 ms 的碎片丢；
写 SRT 时剔常驻 UI 与注音（`tracks.json` 不过滤）；provenance（`git_head(+dirty)`、argv、代码指纹、主轨与候选）写进 `tracks.json` 自己。
**一级产物只有那份 JSON，别的格式一律由 `flowocr.output.export` 投影**（读写只走 `flowocr.artifacts.tracksio`）——契约见 [artifacts.md](artifacts.md)。

## 3. 回抠结算：`flowocr.analyze.refine_boundaries`（建轨末尾，`--refine auto`）

不开视频：拿 obs 行的 `edge` 证据配到回抠标签区域（subtitle、dialogue-or-caption）的 run 上，按 `flowocr.extract.typewriter_fuse` 多信号融合出**首字出现 / 全字出现**
（OCR 中途读数外推、首字格开始出现、逐字格定稿的稳健直线、尾字格定稿；每条记 `t_full_how`），结尾窗逐字格量字形对比度出**完全显示结束 / 完全消失**
（`t_full_end` + `t_end`，`t_end_how` = curve / ncc；测量窗里一直没看到消失的记开放边界 `open`）。量不到的时刻留采样级。结算完 SRT 重出，最后才写 `tracks.json`。
开视频的离线测量只留在开发探针 `dev_tools/refine_offline_probe.py` 里核对在线证据。依据：[defaults.md 1.29](defaults.md#129-建轨顺带结算回抠build_tracks---refine-auto2026-09-25-owner-定)。

## 4. 哪些判据以采样间隔为单位

`frame_us = round(1e6 / sample_fps)` 不只是精度，它是下面这些判据的**单位**。换 fps 前要逐条决定"保持帧数"还是"保持时间"，否则换 fps 等于同时改十个默认。
（采样网格不等距时相邻间隔差一帧；下游的容差都按 1.5 个名义间隔留了余量，见 [defaults.md 1.23](defaults.md#123-采样和辅助流都按时间网格取帧--fps----aux-max-fps-602026-09-24-owner-要的功能)。）

| 处 | 现在 | 4 fps 下的含义 |
| --- | --- | --- |
| 观测时刻 | 只在采样帧上有值（回抠结算过的时刻是帧级） | 采样级边界量化 ±0.25 s → ±0.125 s |
| run 起止 | `t_end` = 末次观测 + 1 个 frame_us | "末帧占满一个间隔"跟着变 |
| 断档容忍 | `(gap_frames + 0.5) × frame_us`，默认 1 = 一帧不许丢 | 一帧只有 0.25 s，同样的掉帧更容易断 |
| 时间支持度 `n_obs`、`--min-obs` | 数采样点 | 票数翻倍 |
| 复用刷新 `--refresh-every` | 数采样帧（第二版判据下默认 32） | 刷新间隔的时间减半（保持策略还是保持时间预算，要先定） |
| 滚动位移 `estimate_shift` | 每采样间隔位移 ≥ 4 px 才算动、12 px 网格 | 隐含速度阈值减半 |
| 打字机增长 | "一帧能长十几个字" | 每帧长得少 |
| cue 终点容差 | `--cue-end-tol-frames 1.0` × frame_us | 0.5 → 0.25 s |
| 回抠认领"起点前一票"、融合的首字下界 | 1.5 个名义间隔内 | 跟着减半 |
| `usage1_health --sample-us` | 写死 500_000 | 不改就量错 |

**绝对时间的（不随 fps 变）**：`--window-sec 60`、碎片下限 150 ms、时长下限 0.3 s、`--ui-min-support 20`（数 cue）。

### 时间轴的来源：真实 PTS

`t_us` 是解码器给的真实 pts，`_meta.timebase = "pts"`。判据在 `src/flowocr/extract/ptsclock.py`，三道门：非有限 / 为负 / 不严格递增，任何一道不过就把产物判为未完成、退出码 4。
此前（2026-09-09 以前）是 `idx / src_fps`，而 `src_fps` 是**容器的平均帧率**，有丢帧缺口的素材上整条时间轴被拉长（[已知的坑](../dev-guide/pitfalls.md#容器平均帧率不是时间轴)）。
`--decoder cv2`（旧默认、离线探针还在用）的两条解码器语义是实测的：
`read()` 之后 `CAP_PROP_POS_MSEC` 给的是刚读到那一帧的 pts（逐帧对齐 ffprobe 的 `pts_time`）；
`cap.set(CAP_PROP_POS_FRAMES, i)` 其实按 `i / src_fps` 做时间 seek、不是按帧号——所以"帧号 → 时刻 → seek"的两次换算互相抵消。
`--start / --end` 开窗时起点对齐到采样网格上帧距最近的一格，窗口跑和整片跑采样的是同一批帧；产物里 `start_sec` / `end_sec` 记**请求**的窗口（复用判据拿它对账），
实际覆盖到的区间另记 `pts_first_sec` / `pts_last_sec`。
那次 1 µs 的坑（`frame_us` 是 `round()` 出来的，实测间隔抖 1 µs，38% 的间隔超容差，run 全断）就是这种绑定的代价（[已知的坑](../dev-guide/pitfalls.md#时间戳比较留半个间隔的余量)）。

## 5. 成本在哪

GPU 默认路径上（2026-09-25 的画像，perf-nightly-0925 报告）：主线程大半时间在等 rec；单次推理在管线里比单独跑慢 2~5 倍，那是几路同时往一张卡上提交的并发代价，
不是浪费（[推理服务](README.md#推理服务)）；辅助流是最大的搬运方。CPU 路径上 det 和 rec 各自就能吃满物理核。
判"哪一段能省多少"要看同一次运行里的等待占比（`run_ocr2 --timeline`）和端到端 A/B，别拿单独跑的毫秒数乘调用数（[已知的坑](../dev-guide/pitfalls.md#单独跑的推理延迟不能当管线里的延迟)）。
建轨在十分钟切片上是秒级，整场（3–5 h 素材）几分钟；回抠结算十分钟片段一两秒。

## 6. 做了但不在默认路径上的

`predet_scan`（det-only 稀疏预扫候选区域）、`--exclude-regions`（排评论墙）、`--workers`（切段并行，有丢帧缺口的素材上拒绝）、
`--ort-cuda-graph`（det 和 rec 同一个服务时捕图会崩）、`--vote-independent`、`--mutual-x`、`--region-time-gate`、`--cluster learned` / `lines`——
各自的账在 [defaults.md](defaults.md) 的"量过之后没有采纳的"和"还没定的"两节。
