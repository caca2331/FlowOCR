# flowocr 使用手册

> 对应 0.2.0。在 Windows 10 + RTX 5070 Ti、ffmpeg 8.0 上跑过（CPU 路径是同一台机器关掉显卡跑的），Linux 在同一台机器的 WSL2（Ubuntu 24.04）上跑过；别的显卡、老卡、真没有显卡的机器、原生 Linux 还没试过，遇到问题请开 issue。
> 开发与调参看 [开发指南](../dev-guide/README.md)。

flowocr 从视频里把**出现过的所有文字**读出来，每条带起止时间，再按位置和时间聚成若干条"文字轨"（对白、字幕、UI、水印……），
导出 SRT 或叠加在原画面上的 ASS。两种常见用法：

- **画面文字翻译**：读出画面各处的字，把译文叠回各自原来的位置——见 [把译文叠回画面](#把译文叠回画面)。
- **对上原文**：有原文（比如游戏的文本包）时，把读到的字换成原文，并带上译文——见 [匹配原文](#匹配原文)。

**能读什么文字**：识别模型是 PaddlePaddle 的 PP-OCRv6，官方称支持约 50 种语言。实际验证过的是日文和简体中文；英文只做过零星试跑；竖排文字没测过。

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
- 数据目录（模型、游戏文本包）是 `%LOCALAPPDATA%\flowocr`。

### 从源码装（想改代码时）

```powershell
winget install Git.Git
git clone https://github.com/caca2331/FlowOCR.git
cd FlowOCR
uv sync --frozen --extra nvidia      # 有 NVIDIA 显卡
uv sync --frozen --extra cpu         # 没有显卡（两种只能选一种）
.venv\Scripts\activate               # 激活后下文的命令直接可用
```

- 激活时提示"禁止运行脚本"：先运行 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` 再激活；或者不激活，每条命令前加 `uv run --no-sync`
  （`--no-sync` 让它不再每次先核对一遍环境）。
- 装错了 `nvidia` / `cpu`：先 `deactivate`（或关掉终端），删掉源码目录下的 `.venv`，再用另一个 extra 重跑 `uv sync`。
- 请用 `git clone`，不要用网页上的 Download ZIP：没有 `.git` 目录时程序把数据目录放到 `%LOCALAPPDATA%\flowocr`，而不是源码目录（见下）。

### Linux

只在 WSL2 的 Ubuntu 24.04（x86-64）上验证过：CPU 和 NVIDIA GPU 两条路，四步都跑通，产物和 Windows 上一致（只差噪音级的个别单字和框位置）；原生 Linux 没实测。和上面不同的地方：

- 装 uv：`curl -LsSf https://astral.sh/uv/install.sh | sh`。
- ffmpeg 要 8.0 或更新：Ubuntu 24.04 的 `apt install ffmpeg` 是 6.1，在部分素材上取帧会出错。可以用 BtbN 的 Linux 静态构建（`linux64-gpl`），把解开后的 `bin` 目录放进 `PATH`。
- 从源码装时激活用 `source .venv/bin/activate`；直接装时数据目录是 `~/.local/share/flowocr`（设了 `XDG_DATA_HOME` 就在它下面）。
- WSL2 里用显卡：Windows 这边装好 NVIDIA 驱动即可，WSL 里不要另装 Linux 的显卡驱动。
- 可选的 [TensorRT](#tensorrt可选gpu-上再快一些) 后端在 Linux 上也能装（`flowocr-trt install`），库比 Windows 的大一些：下载约 0.4–0.7 GB、解开后 0.8–1.1 GB。

### 模型

模型第一次运行时自动下载（PaddlePaddle 官方发布的 ONNX 模型，按固定版本和 sha256 核对；不用装 PaddlePaddle。连不上 Hugging Face 时设环境变量 `HF_ENDPOINT` 指向镜像）。
离线机器：从 [GitHub Release](https://github.com/caca2331/FlowOCR/releases) 下模型包 `flowocr-models-<版本>.zip`，然后

```powershell
flowocr-models install flowocr-models-<版本>.zip   # 核过 sha256 再装进数据目录的 models\，之后不联网也能跑
flowocr-models list                               # 看已有哪些
```

或者在联网的机器上 `flowocr-models fetch`，把数据目录的 `models\` 拷过来。
模型包只有默认的识别与检测模型；离线机器要用下面的韩语、俄语、泰语，在联网的机器上 `flowocr-models fetch rec-ko`（`rec-ru` / `rec-th`）取好，同样把 `models\` 拷过来。

**识别语言**：默认模型认简中、繁中、英、日，以及德、法、西、葡、意、土、印尼这些拉丁字母语言。
韩语、俄语、泰语各有一个另下的模型：`flowocr-ocr … --ocr-lang ko`（`ru` / `th`）用到时才下载（各约 8–13 MB）。
`--ocr-lang auto` 会把三个都下载（共约 29 MB），开跑前抽帧判一次是哪种语言（整部视频一个语言），判不出就用默认。
三个语言模型都认英文字母和数字，不认汉字和假名。越南语没有覆盖的官方模型。
离线机器要这几种语言：在联网的机器上 `flowocr-models fetch rec-ko rec-ru rec-th`，把 `models\` 拷过来（模型包里只有默认模型）。

**数据目录**放模型（`models\`）、游戏文本包（`gametext\`，没有就新建）和可选的 TensorRT（`trt\`，见 [TensorRT](#tensorrt可选gpu-上再快一些)）：直接装的是 `%LOCALAPPDATA%\flowocr`；从源码装的是 `git clone` 出的那个目录（在哪个目录下运行都一样，`models\`、`gametext\`、`out\` 已被 git 忽略）。
命令里的输出路径（如 `out\v\v.jsonl`）是相对**当前目录**的，写到哪由你定，和数据目录无关。
`flowocr-paths` 打印当前用的是哪个；环境变量 `FLOWOCR_DATA_ROOT` 可以改。

## 三步跑一段视频

```powershell
flowocr-ocr video.mp4 --out out\v\v.jsonl                 # 1. 提取：视频 -> 观测（每帧读到的字）
flowocr-tracks out\v\v.jsonl --outdir out\v --tag v       # 2. 建轨：观测 -> v-tracks.json（+ 每条轨一份 SRT）
flowocr-render out\v\v-tracks.json                        # 3. 输出：默认预设，字幕稿 + 叠加 ASS
```

- 直接装的在任何终端里运行；从源码装的在激活了 `.venv` 的终端里、源码目录下运行。`out\v\` 这类输出目录不用先建。
- **第 1 步最慢**：在一张 RTX 5070 Ti 上，5 分钟的 1080p 游戏录像（h264）用了约 25 秒，约为视频时长的 1/12；
  同一段完全不用显卡（解码和识别都在 8 核的 Ryzen 7 5800X3D 上）约 5 分钟，和视频时长差不多。画面里字越多越慢，别的机器会不同。
  `--start` / `--end`（秒）只跑一段，时间戳仍按原片写。
- `--quality high|default|fast` 是精度档位：越往后，量起止时刻的那一路帧越稀，省时省 CPU，起止时间精度略降（`fast` 还把它缩得更小）。
  **`fast` 每秒只读 1 帧字**：显示不到 1 秒的字可能整条漏掉，要防漏条用 `default` 或 `high`。
  不给时是 `default`；要最高精度用 `high`；三档的具体参数见 `flowocr-ocr --help`；显式给的 `--fps` / `--aux-max-fps` / `--refine-scale` 优先。
- 第 2、3 步快得多。改参数重跑第 2 步不用重跑第 1 步，换预设重跑第 3 步不用重跑前两步。
- 起止时间默认精确到一两帧（`--quality high` 精确到帧），逐字打出来的文字（打字机效果）也能还原出来。观测文件太旧时第 2 步会提示（见[排错](#排错)），用默认设置重跑第 1 步即可。
- 第 3 步的默认预设写两份：能在 Aegisub 里改的字幕稿 `v-default.script.ass`，和加好特效的 `v-default.ass`（见[改字幕再出特效](#改字幕再出特效)）。

## 产物怎么读

先说三个词：**区域**是画面上一块固定位置的文字（对话框、底部字幕带、某个 UI 角落……），每个区域聚成一条文字轨；
**主轨**是程序判断的"主字幕"那一条；**cue** 是轨上的一条字幕（一段文字和它的起止时间）。

| 文件 | 是什么 |
| --- | --- |
| `v.jsonl` | 第 1 步的观测：一行一个框，第一行是 `_meta`（用了什么设备、什么参数） |
| `v-tracks.json` | 第 2 步的结果，后面的产物都从它生成：全部区域、每条轨的 cue、主轨是哪条、来源信息 |
| `v-regionNN-<标签>.srt` | 每个区域一份 SRT。标签是 `subtitle`（字幕）、`dialogue-or-caption`（对白 / 字幕）、`text-wall`（大段文字）、`misc`、`noise` 等 |
| `v-main.srt` | 主字幕带由几个区域并成时才有：并起来的主轨 |
| `v-<预设>.script.ass` | 第 3 步的字幕稿：在 Aegisub / mpv 里直接看、直接改 |
| `v-<预设>.ass` | 字幕稿加上特效（底板、逐字显出……）的叠加字幕，如 `v-default.ass` |
| `v-nameplate.srt` | 说话人名牌（识别出来时才有） |

主轨是哪一份 SRT，第 2 步的输出里写着：表里标 `←主轨` 的那个区域，或者 `主轨并带 … -> v-main.srt` 那一行；两样都没有说明没有可当主轨的区域。

写程序读 `*-tracks.json` 时，遍历 `regions` 只认 `kind == "region"` 的条目，别的种类以后可能增加。格式说明见 [artifacts.md](../architecture/artifacts.md)。

各级产物（观测文件 `.jsonl` 的 `_meta`、`*-tracks.json` 等）里，数据目录和用户目录下的路径记成相对路径 / `~/…`，不带用户名；
⚠ 这两处之外的视频（例如另一块盘上的素材目录）照旧记完整路径，分享产物时注意。

## 输出预设

第 3 步 `flowocr-render <产物> --preset <名字>`：

| 预设 | 出什么 |
| --- | --- |
| `default`（不给时就是它） | 字幕稿 + 叠加 ASS，成品样式：底板颜色取自画面（要读源视频，见[改字幕再出特效](#改字幕再出特效)）。`*-tracks.json` 只画主轨和名牌；匹配产物画匹配器定的默认内容（见[匹配产物画哪些](#匹配产物画哪些show--hide)） |
| `default_all` | 字幕稿 + 叠加 ASS，全部都画：`*-tracks.json` 画全部区域（常驻 UI 也画，要不要删自己判断）；匹配产物等于 `--opt show=all` |
| `dev` | 同 `default_all` 的范围，非主轨浅蓝、底板右上角红字标区域号，看聚类用 |
| `script` | 只写字幕稿（`*-tracks.json` 用 `--opt keep=main` 或 `keep=all`，匹配产物用 `show` / `hide`），改完自己跑 `flowocr-typeset` |
| `default_srt` | 只出一份 SRT：和 `default` 同一批条目——一句一条、几行按 SRT 换行、不带名牌和常驻 UI，挂一整场的行（没认出来的名牌、横幅）也不出（`*-tracks.json` 上 `--opt keep=all` 换成 `default_all` 的条目，匹配产物上用 `show` / `hide`；`--opt start=full` 起点改成全字出现；匹配产物上同一句的回显默认不出，见[匹配原文](#匹配原文)） |
| `matched_srt` | 匹配产物拍平成 SRT，一条记录一条：认上的出原文（`--opt lang=jp|cn`），没认上的 OCR 原文只在 `unmatched` 打开时出；`show` / `hide` 同上；同一句的回显默认不出，`--opt echo=keep` 放回来 |

### 匹配产物画哪些（show / hide）

匹配产物里，匹配器把要画的东西分成几个**组**（台词、名牌、选项按钮、界面文字……），每组定一个档位：

| 档位 | 意思 | 能怎么改 |
| --- | --- | --- |
| `always` | 一定画 | 关不掉 |
| `on` | 默认画 | `--opt hide=<组>` 关掉 |
| `off` | 默认不画 | `--opt show=<组>` 打开 |

- 有哪些组、各是什么档，写在匹配产物的 `display` 里（每组带一个给人看的 `label`）；写错组名会报错并列出这份产物有的组。
- `show` / `hide` 都可以写几个，逗号隔开（`--opt show=ui,unmatched`）。同一个名字不能既 show 又 hide。
- `fill` 指**填位**：匹配器认定这里该有一句、但 OCR 没读到它的认领（比如没读出来的选项）。它不是组，是认领上的一个标记——填位的选项仍属于"选项"组，`hide=choice` 连它一起关；`hide=fill` 只关填位。默认画不画由匹配器定。
- `show=all` 打开全部组和填位；`default_all` / `dev` 就是这样，再给 `hide` 照样能关掉其中几组。`hide=all` 不行。
- 两个**每个匹配器都有**的组，默认画不画也由匹配器定，打开时画的是 OCR 原文：
  - `unmatched`：交给了匹配器、但没画成译文的字（没认上原文的，和认上了但没有所选语种译文的）；
  - `unfed`：没交给匹配器的字（别的区域、被当成常驻 UI 剔掉的等）。
  阅读面板画出来时，被它盖住的那段时间里这两组的字不再画。
- 匹配产物上没有 `keep` 和 `layers` 选项了，传了会报错；`*-tracks.json` 照旧用 `keep`。
- 叠加里条目的样子（盖在正文行上、单独一块、名牌、阅读面板）由条目本身决定，和它属于哪个组无关。

内置匹配器的组：

| 匹配器 | 默认画 | 默认不画 |
| --- | --- | --- |
| `gametext` | `dialogue` 台词（一定画）、`name` 名牌、`choice` 选项按钮、`offband` 字幕带外的台词（含绝区零的选项按钮、居中旁白）、`cutscene` 过场字幕、`narration` 黑屏旁白 | `ui` 界面文字（技能说明、帖子、提示…）、`elsewhere` 别处重现的台词（对话记录面板等）、`panel` 阅读面板长文本、`unmatched`、`unfed` |
| `naninovel` | `dialogue` 台词（一定画）、`name` 名牌、`unmatched` | `unfed` |

两个匹配器的填位都默认画。

**振り仮名**（汉字上方的小假名注音）在第 2 步按位置打了标：主轨和逐区域 SRT、`default`、`default_srt`、匹配都不单独出它；
`default_all` / `dev` 照画（`dev` 按常驻 UI 的样子标出来）。判据是按位置猜的，偶尔会把任务栏之类的假名当成注音，
要找回这些字就看这两个预设。不想要这一步，第 2 步加 `--no-ruby-mark`。

要一份字幕 SRT 用 `default_srt`。另有分析用的逐轨 SRT：第 2 步已经写在 `v-tracks.json` 旁边，重出用 `flowocr-export v-tracks.json`（`--track` 只导一条），它们按 cue 原样投影、不拼行、带名牌。SRT 的起点默认是首字出现；
要以"整句显示完"为起点（贴合按稳定画面计时的工具），加 `--start full`，另存为 `*-full.srt`，不覆盖默认那份。

### 改字幕再出特效

字幕稿（`*.script.ass`）是一份普通的 ASS：一条字幕一行——画面上一次显示的一句话是一条，几行之间用 `\N` 隔开，翻译时整句改、换行随意——样式按角色分（`Body` 正文、`Name` 名牌、`Choice` 选项、`UI` 常驻 UI……），
Actor 列是区域号（`r3`）。正文是纯文字，前面一个位置、字号的 tag 块；特效（Effect）列里的 `fo:…` 是给第 4 步看的参数（原文框、打字机时长、轨迹……），不用管它。
字幕稿里预览不到打字机（最终 ASS 里有）；要自己调某一行的节奏，在那一行写卡拉OK计时（`\k`），Aegisub 的卡拉OK模式里可以逐字调。

```powershell
flowocr-typeset out\v\v-default.script.ass                # 改完字幕稿，重新出 v-default.ass
flowocr-typeset out\v\v-default.script.ass --preset dev   # 开发看的样子
flowocr-typeset out\v\v-default.script.ass --video D:\clips\v.mp4   # 视频挪了地方
flowocr-typeset out\v\v-default.ass --restore             # 只有特效版时，还原出字幕稿（已有字幕稿不覆盖，要覆盖加 --force）
```

- 改文字、改时间直接生效；改了译文，字号和底板会重新适配。
- 拖了位置、改了字号：以你改的为准，这一行不再按原框自动摆 / 适配，第 4 步会打一行提示。
- 自己注释掉的行、别的工具生成的特效行不会被动。第 4 步可以反复跑，也可以直接跑在它自己的输出上。
- 第 4 步（`dev` 之外）会读源视频，给底板取画面的背景色、正文按底板深浅改黑字或白字；读视频的时间约是视频时长的十分之一（对话越密越长；有 N 卡时用硬解，CPU 占得少）。视频的位置第 3 步记在字幕稿里，挪了地方就加 `--video <视频>`；不要取色（手边没有视频、或想快）加 `--opt plate_color=off`，底板回到近黑半透明。
- 格式细节见 [artifacts.md 的字幕稿一节](../architecture/artifacts.md#5-字幕稿阶段-3-写阶段-4-读)。

### 时间可能错了的字幕

第 3 步写完字幕稿，如果有起止时间疑似错了的字幕，会多打一行 `⚠ 时间疑点 N 条：…`，后面给出逐条看的命令（带上这次字幕稿生效的 `--opt`）：

```powershell
flowocr-render out\v\v-matched.json --opt lang=cn            # 先照常出字幕稿
python -m flowocr.output.timing_check out\v\v-matched.json --opt lang=cn --json out\v\timing.json
```

每条给出字幕稿里的时刻（`H:MM:SS.cc`，在 Aegisub 里直接跳过去）、规则、偏差和证据；`--opt` 照那行提示给的抄，查的就是同一份字幕稿（`default_all`、`dev` 预设会替你加上 `show=all` / `keep=all`，提示里已经算进去了）。
它只读产物、不改任何东西，报的是**线索**：摆帧看过才算数。它宁可多报，不求每条都是真错；
下表最后一列是开发时在几部游戏直播和视觉小说的整片上抽样摆帧（每条规则 40–90 条，A2 只有 16 条）量到的真错比例，时间紧时按它从高往低看。

匹配产物里同一句话被认领了几次时，除"主"以外的那几次是回显（对话记录面板里的旧台词、重演、同时出现在别处……，见[匹配原文](#匹配原文)一节的 `echo`）。
回显条目照样检查、照样报（叠加 ASS 会画它们），但明细里注明"回显·对话记录"之类，汇总行每条规则另报"其中回显 N"，逐条列表把它们排在后面：
打开对话记录时一屏台词同时出现、挂到关掉为止，会成批报出来，可以先跳过。

| 规则 | 说的是 | 抽样里真错约占 |
| --- | --- | --- |
| A1 同位重叠 | 同一处前后两条在时间上叠住了：后一条起点早，或前一条终点晚，或同一句被拆成了两条（同一区域接连叠的并成一条；重叠的只是两边各一行短字时会注明，多半是一直挂着的名字） | 五成 |
| A2 起止超出成员 | 字幕的起点早于（终点晚于）读到这句字的那些识别结果；证据里写着是谁定下的这个时刻，以及那一刻同一处是不是已经有这句字（有就多半不是错） | 两成半 |
| A3 起点晚于同句在屏 | 字幕起点之前，同一处已经有这句字（或只差一两个字的读数）一直挂到起点：起点晚了。明细注明三种：挂过起点、没有字幕盖着；已由前一条画出（多半是同一句被拆成两条）；起点前刚断开（多是衣服上的字、名牌、按键提示这类一直挂着的字读得断断续续，可以先跳过） | 挂过起点、没有字幕盖着的约七成；已由前一条画出的四成；刚断开的八成，但几乎都不是对白 |
| B1 起点被个别成员拉早 | 一句里最早出现的那一行单独停了很久，下一行才出来 | 两成半 |
| B2 显出速度离群 | 逐字显出的速度和前后几句差三倍以上；偏慢的（多半是显出的起点或显完的时刻晚）远比偏快的准；按行显字的一行一条，同一句可能报好几条 | 偏慢八成、偏快两成 |
| B3 停留时长离群 | 显完之后停留的时间（按字数折算）比前后几句长四倍以上 | 三成 |

检测器重算的是产物加 `--opt` 下的那组时刻，**不读你在 Aegisub 里手改过的字幕稿**：手改之后它的结论不再对应字幕稿，要查就重新出字幕稿再跑。

## 把译文叠回画面

画面文字翻译要的是**所有位置**的字，所以第 3 步用 `default_all`（`default` 只画主轨和名牌）：

```powershell
flowocr-render out\v\v-tracks.json --preset default_all   # 写 v-default_all.script.ass 和 v-default_all.ass
```

1. 用 Aegisub 打开 `v-default_all.script.ass`（可以同时载入视频对照），把每行的原文改成译文；不要的行（常驻 UI、水印……）删掉或注释掉。
2. `flowocr-typeset out\v\v-default_all.script.ass`，重新出 `v-default_all.ass`：译文按原文框重新排字号和底板，打字机、移动照旧。
3. 看效果：mpv 里 `mpv video.mp4 --sub-file=out\v\v-default_all.ass`；要烧进视频：

```powershell
cd out\v
ffmpeg -i ..\..\video.mp4 -vf "ass=v-default_all.ass" -c:a copy v-translated.mp4
```

在字幕所在的目录里运行、`ass=` 后面只写文件名：滤镜参数里的盘符冒号和反斜杠要另行转义，只写文件名就避开了。`-i` 后面的视频路径照常写（视频在别处就写它的完整路径）。
要带 libass 的 ffmpeg（gyan.dev 的 full 构建有；`ffmpeg -filters` 里能找到 `ass`）。

## 设备：GPU 还是 CPU

`--device auto`（默认）先看有没有能用的 NVIDIA 卡，再在卡上建模型、试推理一次：

- **GPU 不可用**（推理库不带 CUDA、没有卡、驱动太旧、卡的架构不受支持、CUDA 运行库加载不了）：退回 CPU，
  打印原因，写进观测的 `_meta.device_fallback`。结果照常可用，只是慢。
- **GPU 可用、但试推理出错**（显存不够、运行时报错等）：**直接报错退出**，不悄悄换 CPU 跑完——
  那种情况多半是配置或环境有问题，CPU 跑出来的结果会掩盖它。确认要用 CPU，就显式给 `--device cpu`。

实际用了哪个设备看 `_meta.device_effective`。`--device gpu`（必须用 GPU，不行就报错）、`--device cpu`（只用 CPU）。

### TensorRT：可选，GPU 上再快一些

另装 NVIDIA 的 TensorRT 之后，GPU 上的检测和识别改由它执行：第 1 步不算起动的处理部分快约 11%、算上起动整趟快约 6%，CPU 和内存也略省
（30 分钟的测试集、RTX 5070 Ti 上量的）。不装照常用 CUDA 跑，结果的差别在逐次运行的抖动量级。

```powershell
flowocr-trt install     # 取本机显卡用得上的 TensorRT 库（下载约 0.3–0.6 GB，Linux 0.4–0.7 GB），再建好默认模型的 fp32 引擎（约 1.5–3 分钟，打进度）
flowocr-trt status      # 库、显卡算力、引擎缓存、升级后留下的旧版本
flowocr-trt remove      # 删库和引擎缓存（各版本都删；目录里有不认识的文件就不动）
```

- **许可**：TensorRT 是 NVIDIA 的专有软件，不随 flowocr 分发；`flowocr-trt install` 从 NVIDIA 的包索引下载它，下载和使用即表示接受 NVIDIA 的许可协议（install 下载前会打出许可链接）。
  运行前请先读：NVIDIA 官网的 [TensorRT 许可](https://docs.nvidia.com/deeplearning/tensorrt/latest/reference/sla.html)是最新版本；随所装版本的那份全文装好后在 `trt\<版本>\lib\LICENSE.txt`（另见 `LICENSES\TensorRT-NOTICE.md`）。
- **放在哪**：数据目录的 `trt\<TensorRT 版本>\`（`lib\` 放库、`engines\` 放引擎缓存），共约 0.7–1.1 GB（库按显卡架构 0.5–0.9 GB；Linux 的库 0.8–1.1 GB）；环境变量 `FLOWOCR_TRT` 可改位置。不进 Python 环境。
- **升级 flowocr**：TensorRT 版本没变就不用重装。升级带来了新的 onnxruntime、配对的 TensorRT 版本变了时，新版本目录里没有库，第 1 步打一行警告后退回 CUDA：
  照提示先 `flowocr-trt remove`（各版本都删）再 `flowocr-trt install`；`flowocr-trt status` 也会列出旧版本目录。
- **离线**：在联网的机器上从 NVIDIA 的包索引 <https://pypi.nvidia.com/tensorrt-cu13-libs/> 下好整包 wheel（约 1.9 GB，文件名见 `flowocr-trt install --help`），再 `flowocr-trt install --from <wheel>`。
- **范围**：只支持 Windows 64 位，和 `flowocr[nvidia]` 装的 onnxruntime-gpu 版本配对（对不上就不装、不用）。
- 装好之后不用加参数，默认就走 TensorRT。开关：
  - `--backend auto|cuda|trt`：`auto`（默认）装了就用；`cuda` 不用 TensorRT；`trt` 和 `auto` 一样，只是没装库（或两个模型都自带）时也打警告（`auto` 只在装了却用不上时警告）。`--device cpu` 时不看这个开关。
  - `--trt-precision fp32|fp16`：默认 fp32；fp16 再快一点，读数有少量确定性的变化。`--det-precision` 只管不走 TensorRT 时的检测。
- **什么时候要现建引擎**：`install` 已建好默认模型的 fp32 引擎（`install --precision both` 连 fp16 一起建）。第一次用 `--trt-precision fp16`、
  `--ocr-lang ko` / `ru` / `th` / `auto` 用到别的识别模型、换了显卡或 TensorRT / onnxruntime 版本时，第 1 步起来先建（每个模型约 0.5–1.5 分钟，打进度），建好存进缓存，之后直接读。
- **用不上时退回 CUDA，不报错**：没装、自带的 `--det-onnx` / `--rec-onnx`、onnxruntime 版本对不上、建引擎或起动自检出错，都退回 CUDA 跑，
  每趟开头那行 `[后端]` 写着每个模型走了哪条路、为什么（也记在观测的 `_meta.backend`）。
  运行中个别批次超出引擎的形状范围（`--det-batch` 大于 4、手动放大的识别批）或 TensorRT 报错时，这几次改用 CUDA，跑完打一行次数。
- **显存**：推理服务稳定在约 2.3 GB（引擎按形状上限一次预留，不随素材涨）；建引擎时峰值约 3.8 GB。

## 只识别画面的一部分

第 1 步加 `--regions <配置.json>` 划定识别范围（坐标是画面宽高的百分比，时间是原片秒）：

```json
{"groups": [{"name": "subs", "rects": [{"box": [0, 70, 100, 100]}, {"box": [80, 70, 100, 80], "neg": true}]}]}
```

`neg: true` 是排除区，`"t": [t0, t1]` 限定生效时段。一个配置里可以有多个组，每次跑一个（`--region-group <name>`），每组是一条独立的识别线。

同一组里分开的几个正矩形不会因此分成不同的文字：组内照常聚类；要让两块互不影响，就分到不同的组。
组里配了正矩形、此刻都不在 `t` 时段内时，此刻不识别，不退回全屏；只配了负矩形的组等于全屏减去负矩形。

## 匹配原文

有原文时，第 2 步之后多一步"匹配"，把读到的字换成原文（并带译文），再用第 3 步出字幕。

**同一句出现在几处**：同一句台词常同时出现在对话框和别的位置，或在对话记录面板里再出现一次，匹配时每一处都认上。
匹配产物给每次认领标一个形态（`matched.json` 记录上的 `echo`）：这一句最早被读到的那一次是"主"（不标；游戏文本包匹配器认得出的对话记录——一屏好几句早先上过屏的台词——不当主，记录里的这一句一律是 `log`；
认不出的对话记录比正文先出现时，主会是对话记录里那一次），其余是
`same_time`（同一时刻别处的同一句）、`log`（对话记录这类面板里的旧台词）、`replay`（同一位置隔一阵又出现，剧情回放、选错重来）、`other`（剩下的，值得人看一眼）。
SRT（`matched_srt`、`default_srt`）默认不出 `same_time` 和 `log`，一句一条；要屏上出现过的每一次，渲染时加 `--opt echo=keep`（`scriptmatch --srt` 顺手出的那份不接这个选项，要放回就拿匹配产物走 `flowocr-render`）。叠加 ASS 照旧每一处都画译文。

**剧本里的样式**：原文里的粗体、斜体、下划线、删除线、字色（游戏用字色标重点）默认照搬进字幕稿和叠加 ASS（ASS 的行内标签）；SRT 是基础输出，一律纯文字。
不要就渲染时加 `--opt style=off`（叠加预设和 `script` 都收）。字号、对齐不搬。剧本的字色照原色画：底板取色把正文改成深色字的地方，
浅色的强调字可能不好认，要改就在字幕稿里改颜色或关掉样式。这些样式是匹配时存进匹配产物的，更早的匹配产物里没有，重跑匹配才有（游戏文本包连同圈剧本那步 `flowocr-gamescript`；不用重跑识别）。
原文里的 Ruby（正文上方的小字：日文多是读音，中文多是注释，如星铁的「欧洛尼斯」上方注「岁月之泰坦」）默认也写进字幕稿，写成 `{ruby:岁月之泰坦}欧洛尼斯{/ruby}`：
花括号里没有 `\`，预览里看不见、只见正文，Aegisub 里可以隐藏；改字时标记跟着字走，不要哪一处就把它前后两个标记删掉，全都不要就加 `--opt ruby=off`（和 `style` 各管各的）。
叠加 ASS 里把它画在正文那几个字的上方（字号约正文的一半，太长的缩小，字色、淡入淡出、打字机跟着这一行，底板往上盖住它）；
阅读面板、用 `\move` 挪过的整块不画。SRT 不带。同样要重跑匹配才有。

### 游戏文本包（原神 / 崩坏：星穹铁道 / 绝区零）

游戏文本包由另一个项目 [game-text-data](https://github.com/caca2331/game-text-data) 从社区解包数据中提取。
**文本版权归游戏的权利人，包不公开分发**：按 game-text-data 的说明自己打包。
包是 `<游戏>-<版本>-<指纹>.zip`（如 `genshin-7.1.0-76ad9320868e.zip`），放进数据目录的 `gametext\` 即可，**不用解压**
（或用 `--gametext <文件或目录>` / 环境变量 `FLOWOCR_GAMETEXT` 指过去）。解压了也行：解压到 `gametext\` 下，
不管是 `gametext\genshin\` 还是右键"全部解压缩"默认的 `gametext\genshin-7.1.0-76ad9320868e\genshin\` 都认。
同一款游戏放了两份不同版本会报错，只留要用的那份；zip 和它解压出的目录并存不算两份。

```powershell
flowocr-gamescript out\v\v.jsonl --out out\v\v-ref.json --game starrail      # 从包里圈出这段视频打过的对话（"剧本"）
flowocr-tracks out\v\v.jsonl --outdir out\v --tag v --matcher gametext --matcher-ctx game=starrail
flowocr-match out\v\v-tracks.json --matcher gametext --ctx ref=out\v\v-ref.json --out out\v\v-matched.json
flowocr-render out\v\v-matched.json --preset default          # 叠加 ASS：画上匹配到的日文原文；--opt lang=cn 换成中文译文
flowocr-render out\v\v-matched.json --preset matched_srt --opt lang=cn
```

- 阅读面板（书籍、信件、教程说明这类长文本）也要盖译文时，匹配加 `--opt readables=1 --opt obs=out\v\v.jsonl`（选开，要多读一遍观测文件）。要 game-text-data 0.2.0 起打的带 `readables` 表的包（目前只做了原神）；包里没有这张表会直接报错。面板滚动时边缘只露出一行的那一段可能暂时不盖译文（一行太短时分不清是不是这一段，宁可不盖）。渲染时面板默认不画，要打开：`--preset default_all`（全部都画），或 `--preset default --opt show=panel`，中文译文再加 `--opt lang=cn`。
- 原神里按主角性别变的称谓（他 / 她、哥哥 / 妹妹…）按从视频里认出的主角性别直接填好；要用 game-text-data 0.2.0 起打的带称谓表的包（旧包里这些称谓会被删掉）。
- 剧本里运行时才定的字（玩家起的名字 `{NICKNAME}`、流浪者的名字等）在字幕稿里标成 `` [`0:值] ``：值是匹配时从视频原文读出来的玩家名，
  读不出、或别的占位就空着（`` [`1:] ``）。生成最终 ASS 时标记换成值、空的连同多出来的标点删掉；第 3 步会打一段提示，列出有哪些占位、各几处、填的什么。
  要换名字就在字幕稿里改标记里的值（Aegisub 里查找替换 `` [`0:旧名] ``），或把整个标记换成文字。
  SRT 没有这一步，标记会留在正文里，搜 `` [` `` 逐处改。
- 星铁里玩家给宠物、飞船、社团起的名字（`{TEXTJOIN#n}`）同样标成 `` [`0:值] ``：值是从视频原文认出的那个候选（按字幕的语言给日文或中文），
  认不出就用游戏的默认名；要用 game-text-data 0.2.0 起打的带候选表 `textjoins_*` 的包（旧包里这些名字空着）。

- `--game` 取 `genshin`、`starrail`、`zzz`；观测文件名以 `gi` / `hsr` / `zzz` 开头时可以省，按前缀认。
- 第 2 步带上 `--matcher gametext`，是让建轨按这款游戏的版式微调分轨参数（目前只有星铁有调整）；星铁已经按默认跑过第 2 步的，带上它再跑一次。
- 包里每张表带**内容契约**版本（字段的类型、能否为空、取值，由 game-text-data 定义）。契约主版本和本工具认得的不一样的包，读的时候会报错并说明是哪张表；多半是两边版本差得太远，把 flowocr 和 game-text-data 都更新到最新、重新打包即可。
- 读包时会核对包里每个文件的哈希，包坏了会明确报错；剧本和匹配产物都记着包的**指纹**与 `rev`，
  以后换了新版本的包，看指纹就知道一份字幕出自哪一版语料。对白的语料跟着剧本走；阅读面板层读的是匹配时找到的包，另记在匹配产物的 `overlay.stats.panel_gametext`。
- **核对版本**：包自带的哈希只证明包和它自己的清单一致。要确认手上的包就是维护者发布过的那份语料，
  打印它的指纹，再到 game-text-data 仓库里的 `releases/<游戏>/fingerprints.tsv` 找这一行。
  下面的命令先把包里每个文件的哈希核一遍（每款几秒），对不上就报错、不打印指纹（从源码装、激活了 `.venv` 时）：

  ```powershell
  python -m flowocr.analyze.gtdbundle starrail                # 打印指纹
  python -m flowocr.analyze.gtdbundle starrail --field json   # 指纹、rev、上游版本等全部来源信息
  ```

  直接装的把 `python` 换成 flowocr 自己环境里的那个：`& "$(uv tool dir)\flowocr\Scripts\python.exe" -m flowocr.analyze.gtdbundle starrail`。

  找得到，这一行同时说明它出自哪份上游、哪一版提取行为（`rev`）；`rev` 形如 `1+0a1b2c3d` 的是本地打的包，不在表里。
- 同一句原文在画面上换了位置（跟着角色走的气泡、同时出现在对话框和对话记录里）时，叠加按位置分段画，每段画在它自己的位置上；
  字在移动的那段空档不画。
- **名牌的译名**：先查剧本里的说话人；查不到的（名字隐藏的 NPC、绝区零这类行上没有说话人的场景）再按屏幕上的字
  逐字查包里的**短名词表**（`terms`，游戏文本里的短串及其译文），`matched.json` 里名牌条目的 `name_src` 记是哪一种。
  这张表是按字形查的：查到的是游戏自己的译法，但那块字不一定是人名（任务目标、称号也会查到）。

### 顺序剧本（Naninovel 引擎，如《魔法少女ノ魔女裁判》）

游戏用 Naninovel 引擎、手上有它导出的本地化脚本（一个目录的 `*.bytes`，每条是 `# key`、`; 日文原文`、译文）时，用内置匹配器 `naninovel`。
剧本由自己从游戏里取得，不随本工具分发。

```powershell
flowocr-tracks out\v\v.jsonl --outdir out\v --tag v --matcher naninovel --matcher-ctx game=mocai
flowocr-match out\v\v-tracks.json --matcher naninovel --ctx script=<剧本目录>\Scripts --out out\v\v-matched.json
flowocr-render out\v\v-matched.json            # 匹配器推荐 matched_srt --opt lang=cn，不给 --preset 就用它
```

- 只匹配主轨（字幕带）；选项、法庭辩论屏、Toast 这类不在字幕带的条目不参与。
- 建轨时的 `--matcher naninovel --matcher-ctx game=mocai` 是给《魔法少女ノ魔女裁判》的：主轨按时间窗判并带，字幕带被缝进别的区域的那几段也喂得到匹配器；别的 Naninovel 游戏不写 `game` 就是默认建轨。剧本按文件顺序走，跳着玩、回放、分支都能接上。
- `matched.json` 里每条认上的记录带剧本行的 `key`、原文 `jp`、译文 `cn` 和**说话人** `speaker` / `speaker_cn`（旁白是空）。
  说话人的名字从剧本目录上一级的 `CharacterNames.bytes` 查（在别处就加 `--ctx names=<文件>`）；名牌显示成 `？？？` 的台词，
  脚本里注记了真名的记真名。
- 记录的时间取字幕在画面上出现到消失；同一句隔了 2 秒以上再显示（比如选错证据之后）是两条记录，`--opt merge_gap=<秒>` 可改。
- 只想匹配一段：`--opt start=<秒> --opt end=<秒>`。

### 自己的原文

写一个匹配器（一个 Python 文件），见下一节。

## 扩展：匹配器、输出预设与特效

匹配器、输出预设和第 4 步的特效都是普通的 Python 文件，复制 `examples\` 里的例子改（直接装的没有这个目录，从 [GitHub 上的 examples](https://github.com/caca2331/FlowOCR/tree/main/examples) 取）：

```powershell
flowocr-match out\v\v-tracks.json --matcher D:\mine\matcher.py --opt table=D:\mine\table.json --out out\v\v-matched.json
flowocr-render out\v\v-matched.json --preset D:\mine\preset.py
flowocr-typeset out\v\v-default.script.ass --fx D:\mine\fx.py
```

- `examples\matcher_minimal.py`：查一张"OCR 文本 -> 原文"的对照表；
- `examples\preset_minimal.py`：最小的输出预设；
- `examples\preset_srt_oneline.py`：和 `default_srt` 同一批条目、换一条文字规则（一条拼成一行）；
- `examples\fx_minimal.py`：最小的特效——在字幕稿某行特效列的 `fo:…` 里加 `;frame`，这一行就沿原文框画一圈框。

入口的约定写在各个例子的文件头。匹配器可以在文件里写 `PRESET = "<预设名或 .py 路径>"`（和可选的 `PRESET_OPTS = {...}`）推荐一个输出预设：它记进匹配产物，之后 `flowocr-render <matched.json>` 不给 `--preset` 时就用它（会打一行说明；命令行的 `--opt` 优先）。写相对的 `.py` 路径时按匹配器文件所在的目录找，记进产物时换成可移植的路径；之后在哪个目录下渲染，加载的都是同一个文件。

匹配器、输出预设、特效的 `.py` 文件会**以你的用户权限直接执行，不做隔离**：只加载自己写的或信得过的文件，第三方扩展的依赖自己装。
扩展入口的形状以本节和 `examples\` 为准，目前没有版本机制；没写进本节的内部实现不承诺长期兼容，升级后扩展失效时看 CHANGELOG。

## 排错

| 现象 | 原因与处理 |
| --- | --- |
| `没装推理后端（onnxruntime）` / `onnxruntime-gpu 和 onnxruntime 同时装着` | 装的时候没选 extra，或两个都选了：按提示重装，`nvidia` / `cpu` 只选一个 |
| 找不到 `ffmpeg` / `ffprobe`，或缺 `showinfo` 滤镜 | 装完整构建的 ffmpeg，把它的 `bin` 放进 `PATH` |
| `--device auto 退回 CPU：…` | GPU 不可用，原因在这一行里；想用 GPU 就按原因修（驱动、CUDA 运行库），否则忽略 |
| `[设备] GPU 可用，但试推理出错——不退回 CPU…` 然后退出 | GPU 可用但出错。按报错修，或显式 `--device cpu` |
| `[后端] ⚠ --backend …：…，用 CUDA EP` / `[trt] det 退回 CUDA EP：…` | TensorRT 没用上，原因在这一行里（没装、版本对不上、建引擎或自检出错）。结果照常可用；`flowocr-trt status` 看库和缓存，删掉重装用 `flowocr-trt remove` 再 `install` |
| `[后端] ⚠ … 次形状超出 TensorRT 引擎的范围、… 次 TensorRT 报错…` | 有几批超出了引擎的形状范围（多半是调大了 `--det-batch` 或识别批），或 TensorRT 运行中报错；这几批用 CUDA 跑了，结果照常可用。报错反复出现时先用 `--backend cuda`，并开 issue 附上那一行 |
| 第 2 步打印 `obs 里没有在线回抠的证据…产物停在采样级` | 观测文件太旧（或第 1 步关掉了这项），起止时间只精确到 ±0.25 秒、打字机不逐字显出。用默认设置重跑第 1 步，再跑第 2 步 |
| 模型下载失败 | 网络问题。连不上 Hugging Face 时设环境变量 `HF_ENDPOINT` 指向镜像；或用 GitHub Release 上的模型包 `flowocr-models install`（见[模型](#模型)） |
| `没找到 <游戏> 的游戏文本包` | 包没放进 `gametext\`，或 `--gametext` 指错了 |
| `文本包 … 和清单对不上（sha256）` | 包拷坏了或被改过，重新拿一份 |
| `文本包 … 缺 …×ja` | 这份包是收窄语种打出来的，缺日文；要一份带中日文的包 |
