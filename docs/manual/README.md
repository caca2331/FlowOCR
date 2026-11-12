# flowocr 使用手册

> 手册对应 1.0.0。项目在 Windows 10 + RTX 5070 Ti、ffmpeg 8.0 上跑过（CPU 路径是同一台机器关掉显卡跑的），Linux 在同一台机器的 WSL2（Ubuntu 24.04）上跑过；别的显卡、老卡、真没有显卡的机器、原生 Linux 还没试过，遇到问题请开 issue。
> 命令按 `uv tool install` 直接装的写，从源码装的差别见[从源码装](#从源码装想改代码时)。开发与调参看 [开发指南](../dev-guide/README.md)。

flowocr 从视频里把**出现过的所有文字**读出来，每条带起止时间，再按位置和时间聚成若干条"文字轨"（对白、字幕、UI、水印……），
导出 SRT，或叠加在原画面上、每条字留在原位的 ASS 字幕，也能直接烧进视频（可以先把原文抹掉）。

**能读什么文字**：识别模型是 PaddlePaddle 的 PP-OCRv6，官方称支持约 50 种语言。实际验证过的是日文和简体中文；英文只做过零星试跑；竖排文字没测过。
默认认简中、繁中、英、日和常见的拉丁字母语言，韩语、俄语、泰语要另选模型（见[识别语言](reference.md#识别语言)）。

## 先选要做的事

| 要做的事 | 要准备什么 | 怎么走 |
| --- | --- | --- |
| **画面文字翻译**：画面各处的字自己翻，译文叠回各自原来的位置 | 视频；译文自己在字幕稿里写 | [第一次跑通](#第一次跑通) → [画面文字翻译](script.md#画面文字翻译) → [烧进视频](burn.md) |
| **对上原文**：游戏有现成的原文和官方译文——原神、崩坏：星穹铁道、绝区零用游戏文本包，Naninovel 引擎的游戏（如《魔法少女ノ魔女裁判》）用它导出的剧本 | 视频；文本包（按 [game-text-data](https://github.com/caca2331/game-text-data) 的说明自己打）或剧本目录 | [第一次跑通](#第一次跑通) → [对上原文](matching.md) → [改稿与出特效](script.md#改完出特效) → [烧进视频](burn.md) |
| **只要一份字幕 SRT** | 视频（有原文时也可以先对上原文） | [第一次跑通](#第一次跑通) → [只要字幕 SRT](#只要字幕-srt) |

手册分这几份：

| 文件 | 讲什么 |
| --- | --- |
| [README.md](README.md)（这份） | 系统要求、安装、第一次跑通、只要字幕 SRT、产物怎么读 |
| [script.md](script.md) | 字幕稿：画面文字翻译的流程、字幕稿里有什么、改完出特效、时间可能错了的字幕 |
| [matching.md](matching.md) | 对上原文：游戏文本包、Naninovel 剧本、画哪些、名牌、译文怎么分行 |
| [burn.md](burn.md) | 烧进视频：原样烧还是先抹原文、输出规格 |
| [reference.md](reference.md) | 参考：输出预设、精度档位、设备与 TensorRT、只识别画面的一部分、识别语言、扩展、排错 |

## 系统要求

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11，64 位。Linux：在 WSL2 的 Ubuntu 24.04（x86-64）上验证过 CPU 与 NVIDIA GPU，原生 Linux 没实测，见 [Linux](#linux)。macOS 暂不支持 |
| Python | 3.12（由 uv 自动安装，不用自己装） |
| 显卡（可选） | NVIDIA，驱动 R580 系列或更新（CUDA 13.0 的要求）。推理库带 Turing（RTX 20 / GTX 16）到 Blackwell（RTX 50）的原生代码（sm_75–120），但只在 RTX 5070 Ti（sm_120）、驱动 610.62 上实跑过。没有 N 卡、卡太老或驱动不够时自动用 CPU，慢一到两个数量级 |
| 显存 | 默认设置下峰值约 4–5 GB（3.7 小时的长片约 5.3 GB；自选范围分 4 个组同时跑约 6 GB；检测改半精度之前量的，现在少 0.5~1 GB）；没有 N 卡时不需要 |
| 内存 | 默认设置下约 3–5 GB（4K 素材约 4.9 GB，`--quality high` 约 5.5 GB） |
| 磁盘 | NVIDIA 版依赖约 2 GB，CPU 版约 0.3 GB；模型约 0.2 GB；可选的 TensorRT 约 0.7–1.1 GB；另留产物空间 |
| ffmpeg | 必须，`ffmpeg` 和 `ffprobe` 都要在 `PATH` 上。用 8.0 或更新的完整构建（如 gyan.dev 或 BtbN 的 Windows full build）；更老的版本会警告，已知 6.1 在部分素材上取帧出错 |

ffmpeg 的构建里要有 `showinfo` 滤镜（取每帧时间戳离不开它）；有 NVDEC（`-hwaccel cuda`、`scale_cuda`）时解码走显卡，没有就自动软解。
启动时会检查这些，缺了会直接说缺什么。

## 安装

先装两样工具（装完重开一个终端，让 `PATH` 生效）：

```powershell
winget install astral-sh.uv       # uv：装 Python 和 flowocr
winget install Gyan.FFmpeg        # ffmpeg 完整构建（含 ffprobe）
```

看、改字幕时还会用到 [Aegisub](https://aegisub.org/)（改字幕稿）和 [mpv](https://mpv.io/)（带字幕播放），用到再装。然后下面两种装法选一种。

### 直接装

```powershell
uv tool install "flowocr[nvidia]"     # 有 NVIDIA 显卡
uv tool install "flowocr[cpu]"        # 没有显卡（两种只能选一种）
uv tool update-shell                  # 第一次用 uv tool 时：把命令目录加进 PATH，然后重开终端（找不到 flowocr-ocr 命令多半是漏了这步）
```

- 装完 `flowocr-ocr` 等命令在任何终端里都能用。升级：`uv tool upgrade flowocr`。
- 装错了 `nvidia` / `cpu`：`uv tool install --reinstall "flowocr[cpu]"`（或 `[nvidia]`），会换成另一个。
- 手册里少数命令以 `python -m flowocr…` 开头（核对文本包、查时间疑点）：直接装的要用 flowocr 自己环境里的 Python，
  Windows 上是 `& "$(uv tool dir)\flowocr\Scripts\python.exe" -m …`，Linux 上是 `"$(uv tool dir)/flowocr/bin/python" -m …`。

### 从源码装（想改代码时）

```powershell
winget install Git.Git
git clone https://github.com/caca2331/FlowOCR.git
cd FlowOCR
uv sync --frozen --extra nvidia      # 有 NVIDIA 显卡
uv sync --frozen --extra cpu         # 没有显卡（两种只能选一种）
.venv\Scripts\activate               # 激活后下文的命令直接可用，python -m … 也直接写 python
```

- 下文的命令在激活了 `.venv` 的终端里运行。激活时提示"禁止运行脚本"：先运行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 再激活；
  或者不激活，每条命令前加 `uv run --no-sync`（`--no-sync` 让它不再每次先核对一遍环境）。
- 装错了 `nvidia` / `cpu`：先 `deactivate`（或关掉终端），删掉源码目录下的 `.venv`，再用另一个 extra 重跑 `uv sync`。
- 请用 `git clone`，不要用网页上的 Download ZIP：没有 `.git` 目录时程序把数据目录放到 `%LOCALAPPDATA%\flowocr`，而不是源码目录（见[数据目录与模型](#数据目录与模型)）。
- 源码里另有 `examples\`（扩展的例子）和 `dev_tools\`（开发工具，例如导出自己游戏的版式文件）；直接装的没有这两个目录。

### Linux

只在 WSL2 的 Ubuntu 24.04（x86-64）上验证过：CPU 和 NVIDIA GPU 两条路都跑通，产物和 Windows 上一致（只差噪音级的个别单字和框位置）；原生 Linux 没实测。和上面不同的地方：

- 装 uv：`curl -LsSf https://astral.sh/uv/install.sh | sh`。
- ffmpeg 要 8.0 或更新：Ubuntu 24.04 的 `apt install ffmpeg` 是 6.1，在部分素材上取帧会出错。可以用 BtbN 的 Linux 静态构建（`linux64-gpl`），把解开后的 `bin` 目录放进 `PATH`。
- 从源码装时激活用 `source .venv/bin/activate`；直接装时数据目录是 `~/.local/share/flowocr`（设了 `XDG_DATA_HOME` 就在它下面）。
- WSL2 里用显卡：Windows 这边装好 NVIDIA 驱动即可，WSL 里不要另装 Linux 的显卡驱动。
- 可选的 [TensorRT](reference.md#tensorrt可选gpu-上再快一些) 后端在 Linux 上也能装，库比 Windows 的大一些。

### 数据目录与模型

**数据目录**放模型（`models\`）、游戏文本包（`gametext\`，没有就新建）和可选的 TensorRT（`trt\`）：

- 直接装的是 `%LOCALAPPDATA%\flowocr`（PowerShell 里写 `$env:LOCALAPPDATA\flowocr`）；
- 从源码装的是 `git clone` 出的那个目录（在哪个目录下运行都一样，`models\`、`gametext\`、`out\` 已被 git 忽略）。

`flowocr-paths` 打印当前用的是哪个；环境变量 `FLOWOCR_DATA_ROOT` 可以改。
命令里的输出路径（如 `out\v\v.jsonl`）是相对**当前目录**的，写到哪由你定，和数据目录无关。

**模型**第一次运行时自动下载（PaddlePaddle 官方发布的 ONNX 模型，按固定版本和 sha256 核对；不用装 PaddlePaddle。连不上 Hugging Face 时设环境变量 `HF_ENDPOINT` 指向镜像）。
离线机器：从 [GitHub Release](https://github.com/caca2331/FlowOCR/releases) 下模型包 `flowocr-models-<版本>.zip`，然后

```powershell
flowocr-models install flowocr-models-<版本>.zip   # 核过 sha256 再装进数据目录的 models\，之后不联网也能跑
flowocr-models list                               # 看已有哪些
```

或者在联网的机器上 `flowocr-models fetch`，把数据目录的 `models\` 拷过来。
模型包只有默认的识别与检测模型；离线要韩语、俄语、泰语见[识别语言](reference.md#识别语言)，要抹原文用的 LaMa 见[烧进视频](burn.md#离线使用)。

## 第一次跑通

先拿一段视频的前一分钟走一遍：

```powershell
flowocr-ocr video.mp4 --out out\v\v.jsonl --start 0 --end 60   # 1. 读字：视频 -> 观测 v.jsonl（每帧读到的字）
flowocr-tracks out\v\v.jsonl --outdir out\v --tag v             # 2. 建轨：观测 -> v-tracks.json，外加每个区域一份 SRT
flowocr-render out\v\v-tracks.json                              # 3. 出稿：默认预设，写 v-default.script.ass 和 v-default.ass
mpv video.mp4 --sub-file=out\v\v-default.ass                    # 带着叠加字幕看效果（要装 mpv）
```

- `--tag v` 是产物文件名的前缀（`v-tracks.json`、`v-default.ass`……），后面的命令按它找文件；**区域**是画面上一块固定位置的文字，每个区域聚成一条轨（见[产物怎么读](#产物怎么读)）。每个命令加 `--help` 列出全部参数。
- `out\v\` 这类输出目录不用先建。`--start` / `--end`（秒）只跑一段，时间戳仍按原片写，所以叠加字幕直接配原视频看；整部去掉这两个参数。
- **第 1 步最慢**：在一张 RTX 5070 Ti 上，5 分钟的 1080p 游戏录像（h264）用了约 25 秒，约为视频时长的 1/12；
  同一段完全不用显卡（解码和识别都在 8 核的 Ryzen 7 5800X3D 上）约 5 分钟，和视频时长差不多。画面里字越多越慢，别的机器会不同。
  要更快或更准，见[精度档位](reference.md#精度档位)。
- 第 2、3 步快得多。改参数重跑第 2 步不用重跑第 1 步，换预设重跑第 3 步不用重跑前两步；同一个叠加预设或 `script` 预设再出一遍要加 `--force`（第 3 步不覆盖已有的字幕稿，见[字幕稿](script.md)；SRT 预设照常覆盖）。
- 起止时间默认精确到一两帧（`--quality high` 精确到帧），逐字打出来的文字（打字机效果）也能还原出来。观测文件太旧时第 2 步会提示（见[排错](reference.md#排错)），用默认设置重跑第 1 步即可。
- 第 3 步的默认预设写两份：能在 Aegisub 里改的**字幕稿** `v-default.script.ass`，和加好特效（底板、逐字显出……）的**叠加 ASS** `v-default.ass`。
  只画主轨（程序判断的主字幕那一处，只从画面中下部挑；字幕在画面上部时没有主轨，见[产物怎么读](#产物怎么读)）和名牌；画面上全部的字要换 `default_all` 预设（见[输出预设](reference.md#输出预设)）。
- 第 3、4、5 步不给输出路径时写在输入文件旁边（第 3 步 `--outdir`、第 4、5 步 `-o` 另给），名字的规矩见[产物怎么读](#产物怎么读)。

后面还有两步：第 4 步 `flowocr-typeset` 按改过的字幕稿重出叠加 ASS（见[字幕稿](script.md)），第 5 步 `flowocr-burn` 烧进视频（见[烧进视频](burn.md)）。接下来按[先选要做的事](#先选要做的事)那张表走。

## 只要字幕 SRT

```powershell
flowocr-render out\v\v-tracks.json --preset default_srt                     # 写 v-default.srt：主轨的字幕，一句一条
flowocr-render out\v\v-matched.json --preset matched_srt --opt lang=cn      # 对上原文之后：写 v-cn.srt，一条记录一条（见对上原文）
```

- `default_srt` 一句一条、几行按 SRT 换行，不带名牌和常驻 UI；`--opt keep=all` 换成全部区域（写 `v-default_all.srt`；匹配产物上没有 `keep`，用 `show` / `hide`，见[画哪些](matching.md#画哪些show--hide)）。
  `--opt start=full` 让每条的起点从"整句显示完"算，贴合按稳定画面计时的工具。匹配产物的 `default_srt` 和 `matched_srt` 怎么选见[输出预设](reference.md#输出预设)。
- **逐区域 SRT**：第 2 步已经写在 `v-tracks.json` 旁边（`v-regionNN-<标签>.srt`，见下表），按识别结果原样投影、不拼行、带名牌，适合拿来分析。
  重出用 `flowocr-export out\v\v-tracks.json`（`--track` 只导一条；`--start full` 另存为 `*-full.srt`，不覆盖默认那份）。

## 产物怎么读

先说三个词：**区域**是画面上一块固定位置的文字（对话框、底部字幕带、某个 UI 角落……），每个区域聚成一条文字轨；
**主轨**是程序判断的"主字幕"那一条；**cue** 是轨上的一条字幕（一段文字和它的起止时间）。

| 文件 | 是什么 |
| --- | --- |
| `v.jsonl` | 第 1 步的观测：一行一个框，第一行是 `_meta`（用了什么设备、什么参数） |
| `v-tracks.json` | 第 2 步的结果，后面的产物都从它生成：全部区域、每条轨的 cue、主轨是哪条、来源信息 |
| `v-regionNN-<标签>.srt` | 每个区域一份 SRT。标签是 `subtitle`（字幕）、`dialogue-or-caption`（对白 / 字幕）、`text-wall`（大段文字）、`misc`、`noise` 等 |
| `v-main.srt` | 主字幕带由几个区域并成时才有：并起来的主轨 |
| `v-nameplate.srt` | 说话人名牌（识别出来时才有） |
| `v-ref.json` | 游戏文本包这条路才有：从包里圈出这段视频打过的对话（剧本），匹配的输入；换包后重做 |
| `v-matched.json` | [对上原文](matching.md)的结果（匹配产物） |
| `v-<名字>.script.ass` | 第 3 步的字幕稿：在 Aegisub / mpv 里直接看、直接改 |
| `v-<名字>.ass` | 第 4 步（叠加预设在第 3 步顺手跑了它）的叠加 ASS：字幕稿加上底板、逐字显出等特效 |
| `v-<名字>.mp4` | [烧进视频](burn.md)的成品，名字跟着它烧的那份 ASS（`x.ass` → `x.mp4`，先抹原文的是 `x.inpaint.mp4`） |

字幕稿和叠加 ASS 的名字是 `<tag>[-<语种>]-<预设或范围>`：

- 从 `v-tracks.json` 出：`v-default`、`v-default_all`、`v-dev`；`script` 预设按范围叫 `v-main`（主轨）或 `v-all`（`--opt keep=all`）。
- 从匹配产物出，中间多一段语种（`jp` / `cn` / `en`，就是 `--opt lang=` 的值，不给是 `jp`）：`v-cn-default`、`v-cn-default_all`；`script` 预设是 `v-cn-main`，打开 `show=all` 时是 `v-cn-all`。
- 第 4 步的叠加 ASS 和字幕稿同名，只是没有 `.script`（`v-cn-main.script.ass` → `v-cn-main.ass`）。
- SRT 预设：`default_srt` 是 `v-default.srt`（`keep=all` / `show=all` 时 `v-default_all.srt`），从匹配产物出时同样多一段语种（`v-cn-default.srt`）；`matched_srt` 是 `v-<语种>.srt`（`v-cn.srt`）。

**主轨是哪一份 SRT**，第 2 步的输出里写着：表里标 `←主轨` 的那个区域，或者 `主轨并带 … -> v-main.srt` 那一行。
两样都没有，说明没有可当主轨的区域——**这不等于没读到字**，逐区域 SRT 照常在。字幕长在画面上部的素材一定是这样：主轨只从画面中下部挑，免得挑中顶部的人名带之类。
这时用逐区域 SRT，或者用 `default_all` 预设画全部区域。

各级产物（观测文件 `.jsonl` 的 `_meta`、`*-tracks.json` 等）里，数据目录和用户目录下的路径记成相对路径 / `~/…`，不带用户名；
⚠ 这两处之外的视频（例如另一块盘上的素材目录）照旧记完整路径，分享产物时注意。

要写程序读这些产物，格式见 [产物格式](../architecture/artifacts.md)。
