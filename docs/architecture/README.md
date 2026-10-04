# 架构说明

系统怎么分工、哪些行为必须成立、为什么这样设计。怎么搭环境和跑验证见 [开发指南](../dev-guide/README.md)。

```text
视频 ──阶段 1 提取──> obs.jsonl ──阶段 2 分析──> <tag>-tracks.json（一级产物）──阶段 3 输出──> SRT / 字幕稿 ──阶段 4 特效──> 叠加 ASS
        flowocr.extract              flowocr.analyze（建轨、匹配、回抠）          flowocr.output（导出器、预设）   flowocr.typeset
```

四个阶段各写一份产物、各有独立入口：换预设不重跑匹配，换匹配器不重跑提取，改字幕稿不重跑阶段 3。
阶段 3 的字幕稿是人工编辑层（在 Aegisub 里预览、编辑），阶段 4 只读它、可以反复重跑（[字幕稿](artifacts.md#5-字幕稿阶段-3-写阶段-4-读)）。
匹配器、输出预设、阶段 4 的扩展效果都是用户可写的 Python 模块（`flowocr.extensions`）。

## 模块与文档

| 模块 | 做什么 | 现行说明 |
| --- | --- | --- |
| `flowocr.extract.run_ocr2` / `run_groups` | 解码、采样、det / rec、复用判据、在线回抠证据 -> obs | [pipeline.md](pipeline.md)（全流程）、[defaults.md](defaults.md)、下面三节 |
| `flowocr.extract.framesource` / `framegrid` / `decode_*` / `supervisor` | 取帧（软硬解、pts、时间网格、分片、硬解失败的整体退回） | [帧身份与解码](#帧身份与解码) |
| `flowocr.extract.ort_server` / `ortclient` / `recort` / `recpool` / `fast_det` / `detpost` | ORT 推理服务与 rec 组批；det 的前处理、配置与后处理 | [推理服务](#推理服务) |
| `flowocr.extract.ocr_args` / `ocr_complete` | 参数解析（唯一一份）与 obs 复用判据 | [obs 复用判据](#obs-复用判据) |
| `flowocr.analyze.build_tracks` / `uigate` / `nameplate` / `align` | run -> 区域 -> 槽位 -> 轨，常驻 UI 与名牌打标，区域的对齐方式 | [defaults.md](defaults.md)、[已知的坑](../dev-guide/pitfalls.md) |
| `flowocr.analyze.match` / `scriptmatch` / `matchers/` | 匹配器入口、内置匹配器、按游戏补丁（`matchers.gametext` 的 `PATCHES` / `cluster_patch`；生效顺序是默认 → 匹配器补丁 → 命令行显式，覆盖了什么会打印并写进 provenance） | [使用手册·匹配原文](../manual/README.md#匹配原文)、`examples/matcher_minimal.py` |
| `flowocr.analyze.gamescript` / `gtdbundle` / `game_align` | 游戏文本包（`gtd-bundle/1`：一款游戏一个 zip，私下获得，放数据根的 `gametext/`）-> 这段视频的剧本；读包时核两种哈希、剧本与匹配产物记包的内容指纹；用途 2 的尺 | [使用手册·匹配原文](../manual/README.md#匹配原文)（协议全文在 game-text-data 的 `docs/architecture.md` 里「产物包协议（gtd-bundle/1）」一节，每张表字段的含义在那边的内容契约里） |
| `flowocr.analyze.refine_boundaries` | 打字机首字 / 全字 / 消失的回抠结算（没有命令行入口）：`build_tracks` 默认（`--refine auto`）在建轨末尾用阶段 1 量好的 obs 证据结算；开视频的离线测量只给开发探针 | [defaults.md](defaults.md) |
| `flowocr.artifacts.tracksio` / `srtio` / `evalkit` | 一级产物与字幕的读写、评测自检 | [artifacts.md](artifacts.md) |
| `flowocr.output.export` / `script` / `layout` / `render` / `presets/` | 阶段 3：从一级产物投影出 SRT 和字幕稿（预设 `script`；两步连跑的 `default` / `default_all` / `dev`）；`layout` 是阶段 3、4 共用的排版 | [artifacts.md](artifacts.md) |
| `flowocr.typeset`（`core` / `assfile` / `fx/`） | 阶段 4：字幕稿 -> 最终叠加 ASS（排版 → 元素 → 运动 → 时间效果）、`restore`、扩展效果 | [字幕稿](artifacts.md#5-字幕稿阶段-3-写阶段-4-读)、`examples/fx_minimal.py` |
| `flowocr.paths` / `provenance` | 代码根 / 数据根的唯一解析入口；git HEAD、`+dirty`、代码指纹 | [开发指南](../dev-guide/README.md#在-worktree-里并行开发)、[验证与报数](../dev-guide/verification.md#追溯产物要能追溯到是哪一版代码产的) |

当前默认值、依据和量过没采纳的在 [defaults.md](defaults.md)。

## 产物契约

机器侧的一级产物只有一份 `<tag>-tracks.json`，读写只走 `flowocr.artifacts.tracksio`，别的格式一律由阶段 3 的导出器（`flowocr.output.export` 出 SRT、`flowocr.output.script` 出字幕稿）从它投影（owner 2026-09-08/09）；
人工编辑层是字幕稿，阶段 4 只读它、不回读阶段 2（owner 2026-09-27，[字幕稿](artifacts.md#5-字幕稿阶段-3-写阶段-4-读)）：

- 切分、常驻 UI 的判定、并带的结果都已经写在 JSON 里，导出器不重新判断（回抠也走导出器）。
- 过滤不删事件、只打标：用途 1 要留、用途 2 要剔，同一份数据；权威清单是逐轨的 `tracks[].ui_lines`。
- 下游遍历 tracks 一律用白名单 `kind == "region"`：名牌轨和区域轨装的是同一批 run，黑名单会让它被喂第二遍。
- provenance 已经并进这份 JSON；下游取值用 `python -m flowocr.artifacts.tracksio <tracks.json> main_srt`，别自己 `json.load`。

细节见 [artifacts.md](artifacts.md)。

## 帧身份与解码

- **帧身份一律是 pts 帧号** `round(t × 名义帧率)`（`framesource.id_rate`）：采样帧、辅助流帧环的键、obs 的 `frame` 同一套。平均帧率在有缺口的文件上会撞号。
- 两路 pts 从命名的 `showinfo@main` / `@aux` 分流，按 showinfo 的 `n` 逐帧核对：撞号 / 丢行当场报，绝不静默错位。
- 采样和辅助流都按**时间网格**取帧（`extract/framegrid.py`）：`--fps` / `--aux-max-fps` 不要求整除，采样帧一定在辅助流里；不整除的采样要求最长间隔 < 1.5 个名义间隔；NTSC 帧率吸附成等距。依据见 [defaults.md](defaults.md) [§1.23](defaults.md#123-采样和辅助流都按时间网格取帧--fps----aux-max-fps-602026-09-24-owner-要的功能)。
- 硬解（默认 `--hwaccel cuda`）：偏离格子才算交付错；缺格问 `source_has_frames`（源里有 = NVDEC 吞帧，没有 = 素材缺口、放行）。
  有前摇的 av1 从文件头起解（`-ignore_editlist` + 整数刻度 `setpts` 平移，试解核像素）。开头 `hwaccel_blocker` 试解 4 格、不过就退回软解；
  中途 `HwaccelMismatch` -> worker 退出码 75 -> 外层监督者按进程树收尾、确认清空之后再起软解那一趟（`_meta.hwaccel_fallback`）。
- 硬解的回抠证据 `edge` 和软解不等价（缩放器不同），所以软硬解的 obs 互不复用；两种解码器（ffmpeg / cv2）的产物也不混用。
- 回抠：阶段 1 在线量证据（obs 行的 `edge`，`--refine-fused` 默认开），阶段 2 `build_tracks --refine auto` 只读这些证据结算、不开视频；空臂 `--refine-aux noop` 和 `_meta.edge.on = 0` 都不算有证据（产物停在采样级），有证据却一条都认领不到就非零退出。
  开视频的离线测量（av1 走 ffmpeg 窗口解码、其余 cv2）只留在开发探针 `dev_tools/refine_offline_probe.py` 里核对在线证据。依据见 reuse-budget 计划。
- 辅助流是整条管线最大的搬运方（300 s 窗 540p 时 9.3 GB，采样流 1.87 GB），成本在搬运不在缩放。
- 契约测试：`dev_tools/aux_identity_check.py`（同号同画面）、`dev_tools/hw_retry_check.py`（重试前整棵树退干净）。实测与来历见 hw-decode-results 报告。

## 推理服务

- **显存配额 8 GB**（owner 2026-09-19）：默认配置下 flowocr 自己那份留在 8 GB 以内（跑它的机器往往同时是日常用机）。ORT 服务是每个形状一个 session（`--per-shape`）。
  - **现行默认**（`--ort-share-mem`，2026-09-23）：同一个模型的 session 共用一个进程级 CUDA arena、权重在显存里只放一份，每个形状的 session 约 50 MB；
    池子（`--ort-max-sessions`）跟着它开到 64，装得下一段素材的全部形状、不淘汰。这时 `ort_server --gpu-mem-mb`（默认 6144）才真是 arena 的总上限。
    依据和读数见 [defaults.md](defaults.md) [§1.13](defaults.md#113---ort-share-mem2026-09-23-夜owner自由探索分批提交decode-buffer)。
  - **回退模式**（`--no-ort-share-mem`）：每个 session 各自一份 arena、各涨到自己形状的激活峰值（约 155 MB + 峰值），显存按形状数线性涨；
    池子回到 12、按最久没用的淘汰。这时 `gpu_mem_limit` 只管单个 session 的 arena，封住总量的是 session 数。
  - `--workers 3` 整机 15.3 GB，已经因为这条出局；`--workers` 维持 1（owner 2026-09-19）。
- **一个引擎一个服务**：把几路 det / rec 塞进一个服务会把它们串回去；单 worker 的默认是 det 并进 rec 那个服务（D1，量过划算）。服务靠 `--exit-on-stdin-eof` 自退出。
  限制：D1 之下 CUDA Graph（`--ort-cuda-graph`）捕图必崩，只有 det 不在这个服务里才捕得成（2026-09-25 二分，followups-evidence-0925 报告）。
- **管线里单次推理比单独跑慢 2~5 倍是并发的代价**：det 单独 37 ms、管线里 66~80 ms，rec 单独 7 ms、管线里 29~35 ms；可 GPU 上核函数的并集只占墙钟不到一半，
  ffmpeg 的核函数几乎不占卡。拿掉任何一个并发来源（det 挪出服务、rec 只留 1 个在途、软解、不分片）单次都变快，墙钟却几乎都不变快——
  重叠提交换来的吞吐比拉长的延迟大。别拿单独跑的延迟推管线的墙钟（followups-evidence-0925 报告）。
- **进程与内存**（gi-s1 默认路径，各进程工作集峰值）：ORT 服务 2.0 GB、只解码进程 1.0 GB（预取按字节封顶 1 GB，慢机器上一直是满的）、回抠进程 0.56 GB、
  管线进程 0.56 GB、ffmpeg 各 0.19 GB（同时 2~3 个）、监督者 0.18 GB，同一时刻约 4.3 GB。这是一台机器上的采样，不能推"8 GB 内存的机器放得下"（perf-nightly-0925 报告）。
- **推理只用 ONNX Runtime**（2026-10 运行时去掉了 Paddle）：det 的前后处理配置读 ONNX 仓库自带的 `inference.yml`、DB 后处理是照抄 PaddleX 的一份（`extract/detpost.py`），
  `FastDet` 只认这组参数 + 一个 runner。照抄版和 PaddleX 原实现的等价在去掉 Paddle 之前对拍过，之后守卫拿冻结的 PaddleX 输出做回归（`tests/_oracle_*`）。
- ORT 的动态形状、补零剂量反应和宽度网格见 [已知的坑](../dev-guide/pitfalls.md#ort-的动态形状)。
- 每个操作都要有 CPU 版（owner 2026-09-23）：`--device auto` 在 GPU 上建模型并试推理一次。**GPU 不可用**（推理库不带 CUDA、没有卡、驱动太旧、架构不支持、CUDA 运行库加载不了，判据是 `ocr_args.GPU_UNAVAILABLE_MARKS`）就整体退回 CPU，原因写进 `_meta.device_fallback`；**GPU 可用但试推理出了别的错**（显存不够、捕图失败……）报错退出，不静默降级（2026-09-25）。`_meta.device_*` 记实际设备；不求逐字一致、不求并发。
  CPU 上两条调度规则自动生效（2026-09-25，[defaults.md](defaults.md) [§1.17](defaults.md#117-rec-池同时两个在途--rec-inflight-22026-09-23decode-buffer) 末尾 / [§1.27](defaults.md#127-ort-线程池不自旋--ort-spin-off2026-09-25-owner-定gpu--cpu-一样perf-nightly-0925-报告)）：rec 只留 1 个在途（`--rec-inflight auto`，显式给数照给的）、不按形状分 session 也不预热——
  CPU 的 det 和 rec 各自就能吃满物理核，多一个在途只是互相挤，按形状分 session 是 CUDA EP 才需要的。ORT 线程池不自旋是两条路共用的默认（[§1.27](defaults.md#127-ort-线程池不自旋--ort-spin-off2026-09-25-owner-定gpu--cpu-一样perf-nightly-0925-报告)）。
  三样叠起来 gi-s1 60 s 72 → 44 s；生效的服务参数记 `_meta.ort_server_args`；证据只有一段 60 s 素材、一台 8 核机器（perf-nightly-0925 报告）。

## obs 复用判据

驱动只复用"跑完了、pts 时间轴、当前默认解码器、同一窗口、同一组**生效**参数"的 obs（`flowocr.extract.ocr_complete`，参数按 `ocr_args` 解析后比，默认值翻了也拦）。
参数按对复用的影响分四档，集合定义在 `src/flowocr/extract/ocr_args.py`：

| 档 | 含义 | 进这一档的条件 |
| --- | --- | --- |
| `NON_PRODUCT` | 压根不写进 config | 不影响产物的输出类参数（路径、进度、时间线、trace） |
| `SCHEDULING` | 写进 config 分得开臂，但**不触发重建** | 取任何值都不改产物；加之前必须 `obs_identical` 逐字节验过 |
| `NOISE_EQUIV` | 行为同 SCHEDULING | 依据是 **owner 定的噪音级**、不是逐字节相同（owner 2026-09-23：对账过的噪音级都可直接豁免；改读哪一帧的旋钮不算） |
| `ADDED_NOOP` | 只赦免"加旋钮之前的行为"那个值 | 新加的默认关旋钮，不让现成 obs 全部重建 |
| `REMOVED_NOOP` | 只赦免"删旋钮之前的默认"那个值 | 删掉的旋钮：产物记着、解析器不认识；值是它没开时的那个数 |

`rec_engine` / `det_engine` 已不是选项（2026-10 去掉 Paddle 之后只有 ORT），但照旧固定写进 config 当身份串：没有这两个键的旧 obs 按 `ADDED_NOOP` 记成 `paddle`，
对现在的 `ort` 判差异，免得 Paddle 产的旧产物被当成 ORT 的复用。`det_engine` 按条件豁免（`NOISE_EQUIV_IF`，只限默认 det 模型 + 官方 ONNX 那一对：两种 det 的产物按噪音级复用）。改产物的旋钮一翻默认，磁盘上现成 obs 的 config 就对不上、驱动会一律重建——这是有意的。
代码指纹不进复用判据（改一下代码就重跑所有整片不划算，只提示）；所以改了"读哪一帧"的修复要自己认出受影响的旧产物、局部作废：
精确 seek 退半帧（2026-09-26）之前的产物没有 `_meta.seek_rev`，其中窗口从片中起、记了缺格、首个采样点晚了一个采样间隔以上的——首格被丢的那个形状——重建，别的照旧复用（`ocr_complete.seek_dropped_first`）。
