# 烧进视频

第 5 步 `flowocr-burn <叠加 ASS>` 把第 4 步出的叠加 ASS（`*.ass`，不是字幕稿 `*.script.ass`）烧进源视频。
源视频的位置第 3 步记在字幕稿里（ASS 开头 `[Script Info]` 的 `FlowOCR Video` 一行，第 4 步照抄进叠加 ASS），挪了地方就加 `--video <视频>`。
下面的例子是对上原文那条路的 `v-cn-main.ass`；换成你第 4 步出的那份，如画面文字翻译的 `v-default_all.ass`、只翻主字幕的 `v-main.ass`。
输出默认和 ASS 放在一起（`x.ass` → `x.mp4`，先抹原文的是 `x.inpaint.mp4`），`-o` 另给；跑到一半失败或按 Ctrl-C 不留半成品。

```powershell
flowocr-burn out\v\v-cn-main.ass                     # 原样烧：译文压在底板上，写 out\v\v-cn-main.mp4
flowocr-burn out\v\v-cn-main.ass --inpaint           # 先抹掉原文再写译文，写 out\v\v-cn-main.inpaint.mp4
flowocr-burn out\v\v-cn-main.ass --start 60 --end 90 # 先试烧一段看效果
```

不改字幕、一步到底时，叠加预设可以直接接着烧：`flowocr-render <产物> --preset default_all --burn plate|inpaint|hq`（`inpaint` / `hq` 会替你带上 `plate=src`）。

## 两条路怎么选

- **原样烧**（默认）：叠加 ASS 怎么显示就怎么烧，译文压在半透明底板上，原文从底板下面透出一点。CPU 上也快。
- **先抹原文**（`--inpaint`）：先用 LaMa 模型把底板盖住的地方补成背景（原文没了），再烧译文；底板本身不画，译文是白字黑边。涂抹跟着打字机逐字展开，比底板略早开始、略晚收起，盖住比底板早出、晚消的原文。
  - 只抹第 4 步自己画的底板：[扩展效果](reference.md#扩展匹配器输出预设与特效)画的框不算。
  - 两档：`--inpaint`（default，快）和 `--inpaint hq`（补得细一些，慢约七成）。镜头、人物在动时，补过的地方看得出和周围画面略微错开。
  - **第 4 步就要为它准备**：出叠加 ASS 时加 `--opt plate=src`、不要取色（为什么见[改完出特效](script.md#改完出特效)）。
    没加 `plate=src` 也能烧，只是译文伸出原文的部分也被抹掉，会打一行提示；取过色的稿、0.3.0 之前的第 4 步出的稿读不出底板，会报错，按提示重出。
  - `--inpaint` 写在 ASS 后面（`flowocr-burn x.ass --inpaint`）：写在前面时 ASS 会被当成档名，报 `invalid choice`。
  - 设备：装了 TensorRT（见[TensorRT](reference.md#tensorrt可选gpu-上再快一些)）就用它，每档第一次用先建引擎（约 1 分钟，之后读缓存）；没装用 CUDA，打一行提示；没有 N 卡用 CPU。
    原神一段 3 分钟的 1080p60 录像、default 档，在 RTX 5070 Ti 上：TensorRT 约 100 秒，CUDA 约 230 秒；CPU 上每秒视频约 10 秒（前 5 秒用了 51 秒）。
    显存约 0.8 GB（CUDA 约 1.5 GB），内存约 2 GB（含解码、编码两个 ffmpeg）。

## 离线使用

抹原文用的模型第一次用时自动取（约 200 MB，Hugging Face 上 Carve 转的 ONNX 版 big-lama，许可见 [LaMa-NOTICE](https://github.com/caca2331/FlowOCR/blob/main/LICENSES/LaMa-NOTICE.md)），连同两档的派生图在模型目录里共约 590 MB。
GitHub Release 的模型包不含 LaMa。离线使用时，先在联网机器上跑 `flowocr-models fetch lama`，再把数据目录 `models\` 下的 `LaMa-ONNX\` 拷进离线机器的同一处；首次烧录会在本地生成派生图。

## 输出的规格

- 分辨率、帧率按源，音轨照拷（mp4 装不下的编码重编成 AAC）。`--size 宽x高`、`--fps` 改。
- 编码器 `--codec auto`（默认）按先后挑第一个能用的：H.265 硬件编码（如 NVIDIA 的 `hevc_nvenc`）→ H.264 硬件编码 → libx264（CPU）。也可以直接写编码器名，如 `--codec libx265`。
- 质量 `--quality <n>`：硬件编码器的恒定质量值 / libx264 的 CRF，越大文件越小。不给时：
  - 不到 30 分钟的视频用保守的默认值（硬件 26、libx264 14），画质和源接近，文件往往比源大；
  - 30 分钟以上先在源视频上抽样试压，找画质分数（VMAF）刚到 95 的质量值，约 2 分钟，换来的文件小一到三成。`--quality auto` 不论长短都找，`--target` 改目标分数。
    要带 libvmaf 的 ffmpeg（gyan.dev 的 full 构建有）；没有时用默认值并提示。
- `--start` / `--end`（秒）只烧一段，先看效果用；字幕按原片的时间对上。源视频起始时间戳不为 0 时，整片烧录与截段烧录的字幕时刻可能不一致，先试烧一段核对。
- 输出一律是 8 bit（yuv420p）；10 bit / HDR 的源没试过，会降成 8 bit。
