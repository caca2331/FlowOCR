# flowocr 使用手册

> 对应 0.1.0。在 Windows 10 + RTX 5070 Ti、ffmpeg 8.0 上跑过（CPU 路径是同一台机器关掉显卡跑的）；别的显卡、老卡、真没有显卡的机器还没试过，遇到问题请开 issue。
> 开发与调参看 [开发指南](../dev-guide/README.md)。

flowocr 从视频里把**出现过的所有文字**读出来，每条带起止时间，再按位置和时间聚成若干条"文字轨"（对白、字幕、UI、水印……），
导出 SRT 或叠加在原画面上的 ASS。两种常见用法：

- **画面文字翻译**：读出画面各处的字，把译文叠回各自原来的位置——见 [把译文叠回画面](#把译文叠回画面)。
- **对上原文**：有原文（比如游戏的文本包）时，把读到的字换成原文，并带上译文——见 [匹配原文](#匹配原文)。

**能读什么文字**：识别模型是 PaddlePaddle 的 PP-OCRv6，官方称支持约 50 种语言。实际验证过的是日文和简体中文；英文只做过零星试跑；竖排文字没测过。

## 系统要求

| 项 | 要求 |
| --- | --- |
| 系统 | Windows 10 / 11，64 位。Linux / macOS 暂不支持 |
| Python | 3.12（由 uv 自动安装，不用自己装） |
| 显卡（可选） | NVIDIA，驱动 R580 系列或更新（CUDA 13.0 的要求）。没有 N 卡、卡太老或驱动不够时自动用 CPU，慢一到两个数量级 |
| 显存 | 默认设置下峰值约 5 GB |
| 磁盘 | NVIDIA 版依赖约 2 GB，CPU 版约 0.3 GB；模型约 0.2 GB；另留产物空间 |
| ffmpeg | 必须，`ffmpeg` 和 `ffprobe` 都要在 `PATH` 上。用完整构建（如 gyan.dev 或 BtbN 的 Windows full build） |

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

### 模型

模型第一次运行时自动下载（PaddlePaddle 官方发布的 ONNX 模型，按固定版本和 sha256 核对；不用装 PaddlePaddle。连不上 Hugging Face 时设环境变量 `HF_ENDPOINT` 指向镜像）。
离线机器：从 [GitHub Release](https://github.com/caca2331/FlowOCR/releases) 下模型包 `flowocr-models-<版本>.zip`，然后

```powershell
flowocr-models install flowocr-models-0.1.0.zip   # 核过 sha256 再装进数据目录的 models\，之后不联网也能跑
flowocr-models list                               # 看已有哪些
```

或者在联网的机器上 `flowocr-models fetch`，把数据目录的 `models\` 拷过来。

**数据目录**放模型（`models\`）和游戏文本包（`gametext\`，没有就新建）：直接装的是 `%LOCALAPPDATA%\flowocr`；从源码装的是 `git clone` 出的那个目录（在哪个目录下运行都一样，`models\`、`gametext\`、`out\` 已被 git 忽略）。
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
- 第 2、3 步快得多。改参数重跑第 2 步不用重跑第 1 步，换预设重跑第 3 步不用重跑前两步。
- 起止时间默认精确到帧，逐字打出来的文字（打字机效果）也能还原出来。观测文件太旧时第 2 步会提示（见[排错](#排错)），用默认设置重跑第 1 步即可。
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

⚠ **观测文件（`.jsonl`）的 `_meta` 里记着视频的完整本机路径**（重跑时判断能不能沿用已有结果要用它），分享产物时注意。
`*-tracks.json` 等后续产物里，数据目录和用户目录下的路径记成相对路径 / `~/…`，不带用户名。

## 输出预设

第 3 步 `flowocr-render <产物> --preset <名字>`：

| 预设 | 出什么 |
| --- | --- |
| `default`（不给时就是它） | 字幕稿 + 叠加 ASS，只画主轨和名牌，成品样式 |
| `default_all` | 字幕稿 + 叠加 ASS，全部区域都画（常驻 UI 也画，要不要删自己判断） |
| `dev` | 字幕稿 + 叠加 ASS，全部保留，非主轨浅蓝、底板右上角红字标区域号，看聚类用 |
| `script` | 只写字幕稿（`--opt keep=main` 或 `keep=all`），改完自己跑 `flowocr-typeset` |
| `srt_main` | 只取主轨的一份 SRT |
| `matched_srt` | 匹配产物拍平成 SRT：匹配上的出原文，没匹配上的留 OCR（`--opt lang=jp|cn`） |

**振り仮名**（汉字上方的小假名注音）在第 2 步按位置打了标：主轨和逐区域 SRT、`default`、`srt_main`、匹配都不单独出它；
`default_all` / `dev` 照画（`dev` 按常驻 UI 的样子标出来）。判据是按位置猜的，偶尔会把任务栏之类的假名当成注音，
要找回这些字就看这两个预设。不想要这一步，第 2 步加 `--no-ruby-mark`。

每条轨单独一份 SRT 用 `flowocr-export v-tracks.json`（`--track` 只导一条）。SRT 的起点默认是首字出现；
要以"整句显示完"为起点（贴合按稳定画面计时的工具），加 `--start full`，另存为 `*-full.srt`，不覆盖默认那份。

### 改字幕再出特效

字幕稿（`*.script.ass`）是一份普通的 ASS：一条字幕一行，样式按角色分（`Body` 正文、`Name` 名牌、`Choice` 选项、`UI` 常驻 UI……），
Actor 列是区域号（`r3`）。正文是纯文字，前面一个位置、字号的 tag 块；特效（Effect）列里的 `fo:…` 是给第 4 步看的参数（原文框、打字机时长、轨迹……），不用管它。
字幕稿里预览不到打字机（最终 ASS 里有）；要自己调某一行的节奏，在那一行写卡拉OK计时（`\k`），Aegisub 的卡拉OK模式里可以逐字调。

```powershell
flowocr-typeset out\v\v-default.script.ass                # 改完字幕稿，重新出 v-default.ass
flowocr-typeset out\v\v-default.script.ass --preset dev   # 开发看的样子
flowocr-typeset out\v\v-default.ass --restore             # 只有特效版时，还原出字幕稿（已有字幕稿不覆盖，要覆盖加 --force）
```

- 改文字、改时间直接生效；改了译文，字号和底板会重新适配。
- 拖了位置、改了字号：以你改的为准，这一行不再按原框自动摆 / 适配，第 4 步会打一行提示。
- 自己注释掉的行、别的工具生成的特效行不会被动。第 4 步可以反复跑，也可以直接跑在它自己的输出上。
- 格式细节见 [artifacts.md 的字幕稿一节](../architecture/artifacts.md#5-字幕稿阶段-3-写阶段-4-读)。

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

## 只识别画面的一部分

第 1 步加 `--regions <配置.json>` 划定识别范围（坐标是画面宽高的百分比，时间是原片秒）：

```json
{"groups": [{"name": "subs", "rects": [{"box": [0, 70, 100, 100]}, {"box": [80, 70, 100, 80], "neg": true}]}]}
```

`neg: true` 是排除区，`"t": [t0, t1]` 限定生效时段。一个配置里可以有多个组，每次跑一个（`--region-group <name>`），每组是一条独立的识别线。

## 匹配原文

有原文时，第 2 步之后多一步"匹配"，把读到的字换成原文（并带译文），再用第 3 步出字幕。

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

- `--game` 取 `genshin`、`starrail`、`zzz`；观测文件名以 `gi` / `hsr` / `zzz` 开头时可以省，按前缀认。
- 第 2 步带上 `--matcher gametext`，是让建轨按这款游戏的版式微调分轨参数（目前只有星铁有调整）；星铁已经按默认跑过第 2 步的，带上它再跑一次。
- 包里每张表带**内容契约**版本（字段的类型、能否为空、取值，由 game-text-data 定义）。契约主版本和本工具认得的不一样的包，读的时候会报错并说明是哪张表；多半是两边版本差得太远，把 flowocr 和 game-text-data 都更新到最新、重新打包即可。
- 读包时会核对包里每个文件的哈希，包坏了会明确报错；剧本和匹配产物都记着包的**指纹**与 `rev`，
  以后换了新版本的包，看指纹就知道一份字幕出自哪一版语料。
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
- `examples\fx_minimal.py`：最小的特效——在字幕稿某行特效列的 `fo:…` 里加 `;frame`，这一行就沿原文框画一圈框。

入口的约定写在各个例子的文件头。

## 排错

| 现象 | 原因与处理 |
| --- | --- |
| `没装推理后端（onnxruntime）` / `onnxruntime-gpu 和 onnxruntime 同时装着` | 装的时候没选 extra，或两个都选了：按提示重装，`nvidia` / `cpu` 只选一个 |
| 找不到 `ffmpeg` / `ffprobe`，或缺 `showinfo` 滤镜 | 装完整构建的 ffmpeg，把它的 `bin` 放进 `PATH` |
| `--device auto 退回 CPU：…` | GPU 不可用，原因在这一行里；想用 GPU 就按原因修（驱动、CUDA 运行库），否则忽略 |
| `[设备] GPU 可用，但试推理出错——不退回 CPU…` 然后退出 | GPU 可用但出错。按报错修，或显式 `--device cpu` |
| 第 2 步打印 `obs 里没有在线回抠的证据…产物停在采样级` | 观测文件太旧（或第 1 步关掉了这项），起止时间只精确到 ±0.25 秒、打字机不逐字显出。用默认设置重跑第 1 步，再跑第 2 步 |
| 模型下载失败 | 网络问题。连不上 Hugging Face 时设环境变量 `HF_ENDPOINT` 指向镜像；或用 GitHub Release 上的模型包 `flowocr-models install`（见[模型](#模型)） |
| `没找到 <游戏> 的游戏文本包` | 包没放进 `gametext\`，或 `--gametext` 指错了 |
| `文本包 … 和清单对不上（sha256）` | 包拷坏了或被改过，重新拿一份 |
| `文本包 … 缺 …×ja` | 这份包是收窄语种打出来的，缺日文；要一份带中日文的包 |
