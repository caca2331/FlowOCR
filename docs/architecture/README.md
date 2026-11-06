# 架构说明

系统怎么分工、哪些行为必须成立、为什么这样设计。怎么搭环境和跑验证见 [开发指南](../dev-guide/README.md)。

```text
视频 ──阶段 1 提取──> obs.jsonl ──阶段 2 分析──> <tag>-tracks.json（一级产物）──阶段 3 输出──> SRT / 字幕稿 ──阶段 4 特效──> 叠加 ASS ──阶段 5 烧录──> 视频
        flowocr.extract              flowocr.analyze（建轨、匹配、回抠）          flowocr.output（导出器、预设）   flowocr.typeset             flowocr.burn
```

五个阶段各写一份产物、各有独立入口：换预设不重跑匹配，换匹配器不重跑提取，改字幕稿不重跑阶段 3。
阶段 3 的字幕稿是人工编辑层（在 Aegisub 里预览、编辑），阶段 4 只读它、可以反复重跑（[字幕稿](artifacts.md#5-字幕稿阶段-3-写阶段-4-读)）；
阶段 5 只读阶段 4 的叠加 ASS 和源视频，人工改过的叠加 ASS 照样能烧。阶段 3 的叠加预设可以 `--burn` 把 3、4、5 连跑。
匹配器、输出预设、阶段 4 的扩展效果都是用户可写的 Python 模块（`flowocr.extensions`）。

## 模块与文档

| 模块 | 做什么 | 现行说明 |
| --- | --- | --- |
| `flowocr.extract.run_ocr2` / `run_groups` | 解码、采样、det / rec、复用判据、在线回抠证据 -> obs | [pipeline.md](pipeline.md)（全流程）、[defaults.md](defaults.md)、下面三节 |
| `flowocr.extract.framesource` / `framegrid` / `decode_*` / `supervisor` | 取帧（软硬解、pts、时间网格、分片、硬解失败的整体退回） | [帧身份与解码](#帧身份与解码) |
| `flowocr.extract.ort_server` / `ortclient` / `recort` / `recpool` / `fast_det` / `detpost` | ORT 推理服务与 rec 组批；det 的前处理、配置与后处理 | [推理服务](#推理服务) |
| `flowocr.extract.ocr_args` / `ocr_complete` | 参数解析（唯一一份）与 obs 复用判据 | [obs 复用判据](#obs-复用判据) |
| `flowocr.analyze.build_tracks` / `uigate` / `nameplate` / `align` | run -> 区域 -> 槽位 -> 轨，常驻 UI 与名牌打标，区域的对齐方式 | [defaults.md](defaults.md)、[已知的坑](../dev-guide/pitfalls.md) |
| `flowocr.analyze.match` / `scriptmatch` / `matchers/` | 匹配器入口、内置匹配器（`gametext`：游戏文本库；`naninovel`：Naninovel 顺序剧本，游标 + 局部窗 + 全局跳转 + 上下文加权，记剧本行的说话人）、按游戏补丁（`matchers.gametext` 的 `PATCHES` / `cluster_patch`；生效顺序是默认 → 匹配器补丁 → 命令行显式，覆盖了什么会打印并写进 provenance） | [使用手册·匹配原文](../manual/README.md#匹配原文)、`examples/matcher_minimal.py` |
| `flowocr.analyze.gamescript` / `gtdbundle` / `game_align` | 游戏文本包（`gtd-bundle/1`：一款游戏一个 zip，私下获得，放数据根的 `gametext/`）-> 这段视频的剧本；读包时核两种哈希、剧本与匹配产物记包的内容指纹；用途 2 的尺 | [使用手册·匹配原文](../manual/README.md#匹配原文)（协议全文在 game-text-data 的 `docs/architecture.md` 里「产物包协议（gtd-bundle/1）」一节，每张表字段的含义在那边的内容契约里） |
| `flowocr.analyze.refine_boundaries` | 打字机首字 / 全字 / 消失的回抠结算（没有命令行入口）：`build_tracks` 默认（`--refine auto`）在建轨末尾用阶段 1 量好的 obs 证据结算；开视频的离线测量只给开发探针 | [defaults.md](defaults.md) |
| `flowocr.artifacts.tracksio` / `srtio` / `evalkit` | 一级产物与字幕的读写、评测自检 | [artifacts.md](artifacts.md) |
| `flowocr.output.export` / `script` / `layout` / `render` / `presets/` | 阶段 3：从一级产物投影出 SRT 和字幕稿（预设 `script`；两步连跑的 `default` / `default_all` / `dev`）；`layout` 是阶段 3、4 共用的排版 | [artifacts.md](artifacts.md) |
| `flowocr.typeset`（`core` / `assfile` / `fx/`） | 阶段 4：字幕稿 -> 最终叠加 ASS（排版 → 元素 → 运动 → 时间效果）、`restore`、扩展效果 | [字幕稿](artifacts.md#5-字幕稿阶段-3-写阶段-4-读)、`examples/fx_minimal.py` |
| `flowocr.burn`（`pipeline` / `encode` / `plates` / `inpaint` / `lamagraph`） | 阶段 5：叠加 ASS 烧进源视频；底板路线原样烧，inpainting 路线先用 LaMa 修补掉底板盖住的原文 | [烧进视频](#烧进视频阶段-5)、[defaults.md](defaults.md#170-阶段-5-的默认烧录路线编码质量修补两档2026-10-31) |
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

## 烧进视频（阶段 5）

两条路线：底板路线是一个 ffmpeg 进程（`subtitles` 滤镜原样烧）；inpainting 路线边解码边修补边编码，整片不进内存。下面几条必须成立：

- **只认标抹底板**：要抹的区域只来自 Effect `fo-fx|plate` 的行（[字幕稿](artifacts.md#5-字幕稿阶段-3-写阶段-4-读)的底板的标），每帧取底板矩形（`\move` 按时段插值）与那一刻 `\clip` 的交（跟着打字机展开）。
  稿里没有标却有 `\p1` 绘图（旧阶段 4 出的）、标着的行不是一整块矩形（取过色）都报错，不静默地什么都不抹。
- **流式**：解码、编码是两个 ffmpeg 进程，bgr24 原始帧走管道；读、写管道各一个线程，主线程只修补，两头的队列各最多攒 4 帧。
  显存不随片长涨；内存随片长涨的主要是 ffmpeg 里 libass 的缓存（涨速越来越慢），另有 mp4 索引和音轨按片长匀速涨（每小时约 23 MB）；60 分钟比 10 分钟的峰值多约 10%，11 小时的整片没实测。
- **色彩不偏**：YUV ↔ RGB 两头按源的色彩矩阵和范围精确转换，输出帧标上同样的色彩信息；源没标的按高 ≥ 720 当 BT.709、否则 BT.601。
  ffmpeg 默认的转换回到 YUV 时按 BT.601、舍入也粗，BT.709 的源整片每帧偏 1–2 级；改了之后和不经 RGB 的底板路线一样（±0.03 级）。
  精确要 swscale 的 `accurate_rnd`、`full_chroma_int`、`full_chroma_inp` 三个一起开：只开 `accurate_rnd` 仍偏约 0.65 级，后两个不配它时不起作用；只换矩阵不开这三个，亮度反而偏得更多（−1.6 级）。
  代价是 CPU：原神 3 分钟 1080p60 整树多约 260 CPU 秒、墙钟多约 6%。换 `zscale`（zimg）精度相同、精确转换多出的 CPU 少约四成（只做往返的 30 s：比默认转换多 30 对 50 CPU 秒），但整条流水线的墙钟没变快，没用。
- **不留半成品、不碰输入**：先写 `<输出>.part`，成功了再改名；失败或 Ctrl-C 删掉，两个 ffmpeg 进程一并结束。输出就是源视频或 ASS 时报错。
- **时间基准**：烧一段时解码从起点开始、时间戳从 0 算，烧字幕前加回起点（ASS 的时间是原片的）。前提是源的时间从 0 起（现有素材都是）；
  不是的话整段烧和烧一段会差那么多，和阶段 1 带前摇的 av1（见[帧身份与解码](#帧身份与解码)）也没对过。
- **后端**：同阶段 1 的规矩（[TensorRT 后端](defaults.md#160-tensorrt-后端--backend-auto--trt-precision-fp32rec-池-1-个det-profile-上限批-42026-10-16-owner-定trt-后端计划)）但不给开关：
  装了 TensorRT 走 TRT EP fp16，引擎缓存和阶段 1 的放在一起（`flowocr-trt remove` 一并删）；没装走 CUDA EP fp32（显存池上限 2 GiB）并提示；没有 GPU 走 CPU。
  fp16 出 NaN 的窗按需建 fp32 会话重算；各后端都数出 NaN 的窗，有就写在结尾那行。

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
- 辅助写端在连接与认证前登记可取消的单一 socket 所有者；MP 适配器仅借用句柄，不负责关闭数字句柄。
  正常结束须排空并交付 PTS 结束标记；显式取消才可丢弃对应同伴的排队帧，不能伪造正常结束。
  广播同伴分别取消，全部离开才取消源；分片收尾串行登记清理错误、等待工作线程，清理失败通过错误消息交回调用方，并继续回收共享内存。
- 契约测试：`dev_tools/aux_identity_check.py`（同号同画面）、`dev_tools/hw_retry_check.py`（重试前整棵树退干净）。实测与来历见 hw-decode-results 报告。

## 推理服务

- **资源预算**（owner 2026-10-05，取代 09-19 的"显存配额 8 GB"）：显存按 8 GB 卡、别的程序常驻 1.5 GB 估，flowocr 自己 **6.5 GB**（设计目标，靠下面的最坏情况读数验证，运行时不硬卡；没在 8 GB 的卡上实测过，owner 定放弃；超出时 CUDA 可能把显存溢到系统内存，表现是变慢、不报错；按空闲显存的运行时兜底 owner 2026-10-05 定不做，arena 上限写死 6144 MB）；
  内存 flowocr 自己 **8 GB**（各进程私有工作集之和）；所有默认配置都要在内，性能几乎不受影响的前提下越少越好。ORT 服务是每个形状一个 session（`--per-shape`）。
  最坏情况的读数（`dev_tools/jobcpu.py` 的 `res`，显存是含 ffmpeg 的上界；GB = 计数器给的 MiB ÷ 1000；2026-10-05，resource 计划）：

  | 场景 | 显存（GB） | 私有内存（GB） |
  | --- | --- | --- |
  | quick 六段（1080p60，默认档） | ≤ 4.3 | ≤ 3.3 |
  | 长片单趟（zzz 3.7 h，720p） | 5.35 | 3.1 |
  | 4K（gi-s1 放大，默认档 / high 档） | 4.5 / 4.6 | 4.9 / 5.5 |
  | 多组 4 组（wuwa-s2 5 分钟 / wuwa 1080p 前 60 分钟，整屏组） | 5.9–6.2 / 6.1 | 4.9 / 4.8 |
  | 单组结构最坏（真实服务，会话池打满 + 两条 rec 连接一直发最宽满批 + det 批 4） | ORT 5.96，加 ffmpeg 估约 6.7–7.1（**超预算**，owner 接受：真实素材碰不到） | — |
  | 纯 CPU（gi-s1 60 s） | 0.7（只有硬解） | 1.9 |
  | 阶段 2（523 k / 157 万条 obs） | — | 0.55 / 1.43 |

  ORT 显存在长片上是一级一级涨的（按需扩容后历史最高的并发需求 + 碎片），所以短片段上的读数会低估整片；宽批跑完收缩 arena 量过不采纳（[未采纳的实验](defaults.md#3-量过之后没有采纳的)）。
  - **现行默认**（`--ort-share-mem`，2026-09-23）：同一个模型的 session 共用一个进程级 CUDA arena、权重在显存里只放一份，每个形状的 session 约 50 MB；
    共享权重的 CUDA 数据缓冲与服务进程同寿命；DLPack 元数据由 native OrtValue 单独拥有，以同一 CRT/libc 的原生 free 回收，不依赖 Python 回调或 keep 列表的销毁顺序。转换或拷贝失败只回收尚未发布的当前 CUDA 缓冲。
    池子（`--ort-max-sessions`）跟着它开到 64，装得下一段素材的全部形状、不淘汰。这时 `ort_server --gpu-mem-mb`（默认 6144）才真是 arena 的总上限。
    依据和读数见 [defaults.md](defaults.md) [§1.13](defaults.md#113---ort-share-mem2026-09-23-夜owner自由探索分批提交decode-buffer)。
    arena 要多少扩多少（`--ort-arena-extend same`）、cuBLAS 每份工作区 4 MB（`--ort-cublas-ws :4096:1`）：每个（调用线程，session）各有一套 cuBLAS 句柄和工作区，
    两样合起来 zzz 中段 30 分钟 ORT 显存 4.34 → 3.12 GB，墙钟噪声档（[defaults.md](defaults.md) [§1.41](defaults.md#141-ort-显存arena-按需扩---ort-arena-extend-samecublas-工作区---ort-cublas-ws-409612026-10-05resource-计划)）。
  - **回退模式**（`--no-ort-share-mem`）：每个 session 各自一份 arena、各涨到自己形状的激活峰值（约 155 MB + 峰值），显存按形状数线性涨；
    池子回到 12、按最久没用的淘汰。这时 `gpu_mem_limit` 只管单个 session 的 arena，封住总量的是 session 数。
  - `--workers 3` 整机 15.3 GB，已经因为这条出局；`--workers` 维持 1（owner 2026-09-19）。多组 / `--workers` 现在共用一个服务、按 批 × 宽 准入（[defaults.md](defaults.md) 1.42）。
- **单 worker 默认共用服务**：det 并进 rec 的 ORT 服务，几条连接可以并发请求，普通 session 的推理不加全局锁。服务靠 `--exit-on-stdin-eof` 自退出。
  附着客户端仅关闭自己的连接与共享内存；发送退出消息失败时，缓冲流和 socket 仍分别关闭，避免取消后留下句柄。
  CUDA Graph 录图量过两次都不采纳、旋钮已删（捕图要服务内 CUDA 操作互斥，而且 ORT 按线程各捕一次），见 [未采纳的实验](defaults.md#3-量过之后没有采纳的)。
- **TensorRT 后端**（可选，装了就默认走；取值和依据见 [defaults.md](defaults.md) [§1.60](defaults.md#160-tensorrt-后端--backend-auto--trt-precision-fp32rec-池-1-个det-profile-上限批-42026-10-16-owner-定trt-后端计划)）：
  - 判定分两头。客户端（`run_ocr2.trt_plan`，CPU 上 `ort_server_args` 根本不调它）看装没装库、`--backend`、自带模型、主进程的 ORT 版本，决定给不给 `ort_server` 传 `--trt-*`；服务端（`ort_server.start_trt`）再核 ORT 版本、载库、缺引擎就现建、算 CPU 参考、建 session、自检。
    哪一步不过，那个模型就整体用 CUDA EP 的按形状 session，原因按"哪一步：原因"经握手记进 `_meta.backend.models.<模型>.fallback`。服务端在 ready 行之前把进度和回退写成 `{"note": …}` 行，客户端转给用户。
  - 引擎部分在 `extract/trt_engine.py`，服务端按文件路径载入（服务可能跑在另一个解释器里、不 import flowocr，同 `recprep.py`）；库目录、引擎目录经命令行传。缺引擎时在服务端那个进程里建，缓存名里的 ORT 版本因此和实际跑的对得上。
  - 缓存名 = 模型 sha256 + TensorRT / ORT 版本 + 算力 + 精度 + 会改引擎的全部 EP 选项（profile、workspace、fp16 开关…）的哈希（ORT 自己只按图结构算哈希，换权重不换结构会读到旧引擎）。建到临时目录、冒烟过了再逐个改名进 `engines/`，`.engine` 最后放。
    两个进程同时缺同一个引擎时靠操作系统的文件锁排队（进程死了系统放锁）；不用刷修改时间的心跳锁：ORT 建 session 时整段持有 GIL，TensorRT 又在这一步建引擎，心跳线程跳不动，活着的锁会被当成陈锁抢走。
  - ORT 1.29 上**同一个 TRT session 的并发调用是串行的**，所以每个模型一个小池子，调用各领一个空闲 session，客户端多于池子时排队（排队时间算进 `session_ms`）。
    超出 profile 的形状或运行时报错，这一次改用 CUDA EP 的按形状 session，计数在 `_meta.ort.<模型>.trt_oop / trt_err`，跑完有就警告。ORT 的 Python 封装在建 session 失败和 run 抛错时都会悄悄换 CPU 重来（建失败时还往 stdout 打一行，而 stdout 是管道模式的回信通道），GPU 上的 TRT / CUDA EP session 建的时候就关掉这个回退（`enable_fallback=0`）。
  - 每个引擎只有一个 profile（ORT 1.29 建得出多 profile 的引擎，跑的时候只用第一个）；建 session 时就按 profile 上限预留激活，所以 TRT 的显存是一条和素材无关的平线、读数就是上界。`--gpu-mem-mb` 只限 CUDA arena，管不到 TRT 的 workspace。
  - 起动自检：入库的一张合成文字图（`extract/trt_check.npz`）拼成 profile 的 min / opt / max 三个形状，池子里第一个 session 跑一遍，对 CPU EP 的参考输出（按模型缓存在 `engines/`）：浮点差超过 0.25 的元素 ≤ 0.5%、整数输出不一致 ≤ 2%。要抓的是整张图算错（差到 1.0 那种），不是 TF32 / fp16 的抖动。
  - TRT EP 的布尔选项只认字符串 `'True'` / `'False'`，写错时整个 EP 静默退回 CUDA EP：`make_session` 断言首选 provider 真是 TRT。
  - 多组 / `--workers` 共用一个服务时，各组 `_meta` 里的 `trt_oop / trt_err` 和 TRT 说明是整个服务的，不是这一组自己的。`_meta.ort.<模型>.server` 记这个连接实际连的服务（接上的地址，或自己起的服务进程号）；`--workers` 合并各段时按它分组，同一服务取最大、不同服务相加（`--no-ort-share`、接外部地址失败后各段自己起的）。
- **管线里单次推理比单独跑慢 2~5 倍是并发的代价**：det 单独 37 ms、管线里 66~80 ms，rec 单独 7 ms、管线里 29~35 ms；可 GPU 上核函数的并集只占墙钟不到一半，
  ffmpeg 的核函数几乎不占卡。拿掉任何一个并发来源（det 挪出服务、rec 只留 1 个在途、软解、不分片）单次都变快，墙钟却几乎都不变快——
  重叠提交换来的吞吐比拉长的延迟大。别拿单独跑的延迟推管线的墙钟（followups-evidence-0925 报告）。
- **进程与内存**（私有工作集峰值，zzz 整片 / wuwa-s2 5 分钟）：ORT 服务约 1.9–2.2 GB（另有约 1.7 GB 是映射的 CUDA 库文件，文件支撑、系统可回收，不计）、
  只解码进程 0.3–0.8 GB（预取按字节封顶 1 GB，4K 时 1.2 GB）、管线进程 0.3–0.7 GB（长片上随轨的状态涨）、回抠进程 0.1–0.2 GB（4K high 档 1.4 GB：帧环按帧数封顶）、ffmpeg 各约 0.2 GB（同时 2–3 个，4K 各 0.5 GB）。
  ffmpeg 硬解每个还占约 0.3 GB 显存（720p / 1080p，4K 约 0.7 GB）。提交量（整棵树单组约 10 GB、4K 12–13 GB、4 组 16.6 GB）主要是 CUDA 的预留，WDDM 下显存分配一比一计入，不是物理占用；8 GB 内存的机器要靠页面文件兜住提交量（2026-10-05 一趟采样，没在别的机器上核过）。
- **批与并发的边界**：rec 每裁剪的成本在批 16（Paddle）/ 32（ORT）之后不再降，共享推理服务不需要很大的批；同进程多推理线程不扩展、并发靠多进程（defaults.md 4.0.2）。读数是合成裁剪与 Paddle 时期的，换引擎要重量（inference-runtime 计划）。
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
obs 头（`_meta`）里的参数只看 `config`（解析后的生效值），不记命令行原样（`argv`）；`video` 记成可移植的路径（数据根下相对、用户目录下 `~/…`），复用判据比之前两边都先换成这个形式。画面宽高是必需字段：缺宽高的是很早的 obs，建轨直接报错让重跑提取，不再开视频补探（2026-10-06）。
代码指纹不进复用判据（改一下代码就重跑所有整片不划算，只提示）；所以改了"读哪一帧"的修复要自己认出受影响的旧产物、局部作废：
精确 seek 退半帧（2026-09-26）之前的产物没有 `_meta.seek_rev`，其中窗口从片中起、记了缺格、首个采样点晚了一个采样间隔以上的——首格被丢的那个形状——重建，别的照旧复用（`ocr_complete.seek_dropped_first`）。
