# 参考

按需查：输出预设、精度档位、设备与 TensorRT、只识别画面的一部分、识别语言、振り仮名、扩展、排错。第一次用先看 [使用手册入口](README.md)。

## 输出预设

第 3 步 `flowocr-render <产物> --preset <名字>`，产物是 `*-tracks.json` 或匹配产物 `*-matched.json`：

| 预设 | 出什么 |
| --- | --- |
| `default`（不给时就是它；匹配器推荐了预设时用推荐的，见[扩展](#扩展匹配器输出预设与特效)） | 字幕稿 + 叠加 ASS，成品样式：近黑半透明底板（要取画面的颜色见[改完出特效](script.md#改完出特效)）。`*-tracks.json` 只画主轨和名牌；匹配产物画匹配器定的默认内容（见[画哪些](matching.md#画哪些show--hide)） |
| `default_all` | 字幕稿 + 叠加 ASS，全部都画：`*-tracks.json` 画全部区域（常驻 UI 也画，要不要删自己判断）；匹配产物等于 `--opt show=all` |
| `dev` | 同 `default_all` 的范围，非主轨浅蓝、底板右上角红字标区域号，看聚类用 |
| `script` | 只写字幕稿（`*-tracks.json` 用 `--opt keep=main` 或 `keep=all`，匹配产物用 `show` / `hide`），改完自己跑 `flowocr-typeset` |
| `default_srt` | 只出一份 SRT，和 `default` 同一批条目：一句一条、几行按 SRT 换行；不带名牌、常驻 UI、`special` 组，挂一整场的行（没认出来的名牌、横幅）也不出。`*-tracks.json` 上 `--opt keep=all` 换成 `default_all` 的条目，匹配产物上用 `show` / `hide`；`--opt start=full` 起点改成全字出现 |
| `matched_srt` | 只用于匹配产物：拍平成 SRT，一条记录一条。认上的出剧本文本（`--opt lang=jp|cn|en`），没认上的识别原文只在 `unmatched` 打开时出；`show` / `hide` 同上（`special` 组一律不出） |

- 匹配产物上两个 SRT 预设都默认不出同一句的回显，`--opt echo=keep` 放回来（见[同一句出现在几处](matching.md#同一句出现在几处)）。`default_srt` 和叠加是同一批条目，`matched_srt` 按匹配记录出。
- 叠加预设（`default`、`default_all`、`dev`）加 `--burn plate|inpaint|hq` 接着烧进视频：`plate` 原样烧，`inpaint` / `hq` 先抹原文（两档，见[烧进视频](burn.md#两条路怎么选)）。
- 匹配产物上没有 `keep` 选项，传了会报错。
- 文件名的规矩见[产物怎么读](README.md#产物怎么读)。

## 精度档位

第 1 步 `--quality high|default|fast`：越往后，量起止时刻的那一路帧越稀，省时省 CPU，起止时间精度略降（`fast` 还把它缩得更小）。

- **`fast` 每秒只读 1 帧字**：显示不到 1 秒的字可能整条漏掉，要防漏条用 `default` 或 `high`。
- 不给时是 `default`，起止时间精确到一两帧；`high` 精确到帧。三档的具体参数见 `flowocr-ocr --help`；显式给的 `--fps` / `--aux-max-fps` / `--refine-scale` 优先。

## 设备：GPU 还是 CPU

`--device auto`（默认）先看有没有能用的 NVIDIA 卡，再在卡上建模型、试推理一次：

- **GPU 不可用**（推理库不带 CUDA、没有卡、驱动太旧、卡的架构不受支持、CUDA 运行库加载不了）：退回 CPU，
  打印原因，写进观测的 `_meta.device_fallback`。结果照常可用，只是慢。
- **GPU 可用、但试推理出错**（显存不够、运行时报错等）：**直接报错退出**，不悄悄换 CPU 跑完——
  那种情况多半是配置或环境有问题，CPU 跑出来的结果会掩盖它。确认要用 CPU，就显式给 `--device cpu`。

`--device gpu` 必须用 GPU，不行就报错；`--device cpu` 推理只用 CPU。实际用了哪个设备看观测的 `_meta.device_effective`。
`--device` 只管推理：有 NVDEC 时解码照样走显卡。要连解码也不碰显卡，另加 `--hwaccel=`（等号后面空着，就是强制软解）。

### TensorRT：可选，GPU 上再快一些

另装 NVIDIA 的 TensorRT 之后，GPU 上的检测和识别改由它执行：第 1 步不算起动的处理部分快约 11%、算上起动整趟快约 6%，CPU 和内存也略省
（30 分钟的测试集、RTX 5070 Ti 上量的）。不装照常用 CUDA 跑，结果的差别在逐次运行的抖动量级。

```powershell
flowocr-trt install     # 取本机显卡用得上的 TensorRT 库（下载约 0.3–0.6 GB，Linux 0.4–0.7 GB），再建好默认模型的 fp32 引擎（约 1.5–3 分钟，打进度）
flowocr-trt status      # 库、显卡算力、引擎缓存、升级后留下的旧版本
flowocr-trt remove      # 删库和引擎缓存（各版本都删；目录里有不认识的文件就不动）
```

- **范围**：Windows 与 Linux 的 x86-64（Linux 只在 WSL2 上验证过），和 `flowocr[nvidia]` 装的 onnxruntime-gpu 版本配对（对不上就不装、不用）。
- **许可**：TensorRT 是 NVIDIA 的专有软件，不随 flowocr 分发；`flowocr-trt install` 从 NVIDIA 的包索引下载它，下载和使用即表示接受 NVIDIA 的许可协议（install 下载前会打出许可链接）。
  运行前请先读：NVIDIA 官网的 [TensorRT 许可](https://docs.nvidia.com/deeplearning/tensorrt/latest/reference/sla.html)是最新版本；随所装版本的那份全文装好后在 `trt\<版本>\lib\LICENSE.txt`（另见 `LICENSES\TensorRT-NOTICE.md`）。
- **放在哪**：数据目录的 `trt\<TensorRT 版本>\`（`lib\` 放库、`engines\` 放引擎缓存），共约 0.7–1.1 GB（库按显卡架构 0.5–0.9 GB；Linux 的库 0.8–1.1 GB）；环境变量 `FLOWOCR_TRT` 可改位置。不进 Python 环境。
- **升级 flowocr**：TensorRT 版本没变就不用重装。升级带来了新的 onnxruntime、配对的 TensorRT 版本变了时，新版本目录里没有库，第 1 步打一行警告后退回 CUDA：
  照提示先 `flowocr-trt remove`（各版本都删）再 `flowocr-trt install`；`flowocr-trt status` 也会列出旧版本目录。
- **离线**：在联网的机器上从 NVIDIA 的包索引 <https://pypi.nvidia.com/tensorrt-cu13-libs/> 下好整包 wheel（约 1.9 GB，文件名见 `flowocr-trt install --help`），再 `flowocr-trt install --from <wheel>`。
- 装好之后不用加参数，默认就走 TensorRT。开关：
  - `--backend auto|cuda|trt`：`auto`（默认）装了就用；`cuda` 不用 TensorRT；`trt` 和 `auto` 一样，只是没装库（或两个模型都自带）时也打警告（`auto` 只在装了却用不上时警告）。`--device cpu` 时不看这个开关。
  - `--trt-precision fp32|fp16`：默认 fp32；fp16 再快一点，读数有少量确定性的变化。`--det-precision` 只管不走 TensorRT 时的检测。
- **什么时候要现建引擎**：`install` 已建好默认模型的 fp32 引擎（`install --precision both` 连 fp16 一起建）。第一次用 `--trt-precision fp16`、
  `--ocr-lang ko` / `ru` / `th` / `auto` 用到别的识别模型、换了显卡或 TensorRT / onnxruntime 版本时，第 1 步起来先建（每个模型约 0.5–1.5 分钟，打进度），建好存进缓存，之后直接读。
- **用不上时退回 CUDA，不报错**：没装、自带的 `--det-onnx` / `--rec-onnx`、onnxruntime 版本对不上、建引擎或起动自检出错，都退回 CUDA 跑，
  每趟开头那行 `[后端]` 写着每个模型走了哪条路、为什么（也记在观测的 `_meta.backend`）。运行中个别批次退回 CUDA 的情形见[排错](#排错)。
- **显存**：推理服务稳定在约 2.3 GB（引擎按形状上限一次预留，不随素材涨）；建引擎时峰值约 3.8 GB。

## 只识别画面的一部分

第 1 步加 `--regions <配置.json>` 划定识别范围：

```json
{"groups": [{"name": "subs", "rects": [{"box": [0, 70, 100, 100]}, {"box": [80, 70, 100, 80], "neg": true}]}]}
```

- `box` 是 `[左, 上, 右, 下]` 四条边，单位是**百分比**（0–100）：左、右按画面宽算，上、下按画面高算。上面第一个矩形是画面下方 30%。
  别和[游戏画面位置](matching.md#译文怎么分行)的 `--viewport x,y,w,h` 搞混：那个是像素、左上角加宽高。
- `neg: true` 是排除区，`"t": [t0, t1]` 限定生效时段（原片秒）。
- 一个配置里可以有多个组，每次跑一个（`--region-group <name>`），每组是一条独立的识别线。
- 同一组里分开的几个正矩形不会因此分成不同的文字：组内照常聚类；要让两块互不影响，就分到不同的组。
- 组里配了正矩形、此刻都不在 `t` 时段内时，此刻不识别，不退回全屏；只配了负矩形的组等于全屏减去负矩形。

## 识别语言

默认模型认简中、繁中、英、日，以及德、法、西、葡、意、土、印尼这些拉丁字母语言。

- 韩语、俄语、泰语各有一个另下的模型：`flowocr-ocr … --ocr-lang ko`（`ru` / `th`）用到时才下载（各约 8–13 MB）。三个语言模型都认英文字母和数字，不认汉字和假名。
- `--ocr-lang auto` 会把三个都下载（共约 29 MB），开跑前抽帧判一次是哪种语言（整部视频一个语言），判不出就用默认。
- 越南语没有覆盖的官方模型。
- 离线机器要这几种语言：在联网的机器上 `flowocr-models fetch rec-ko rec-ru rec-th`，把数据目录的 `models\` 拷过来（[模型包](README.md#数据目录与模型)里只有默认模型）。

## 振り仮名

汉字上方的小假名注音在第 2 步按位置打了标：主轨和逐区域 SRT、`default`、`default_srt`、匹配都不单独出它；
`default_all` / `dev` 照画（`dev` 按常驻 UI 的样子标出来）。判据是按位置猜的，偶尔会把任务栏之类的假名当成注音，
要找回这些字就看这两个预设。不想要这一步，第 2 步加 `--no-ruby-mark`。这是从画面上认出来的注音；对上原文时剧本自带的 Ruby 是另一回事、另有开关（见[剧本里的样式与 Ruby](matching.md#剧本里的样式与-ruby)）。

## 扩展：匹配器、输出预设与特效

匹配器、输出预设和第 4 步的特效都是普通的 Python 文件，复制 `examples\` 里的例子改（直接装的没有这个目录，从 [GitHub 上的 examples](https://github.com/caca2331/FlowOCR/tree/main/examples) 取）：

```powershell
flowocr-match out\v\v-tracks.json --matcher D:\mine\matcher.py --opt table=D:\mine\table.json --out out\v\v-matched.json
flowocr-render out\v\v-matched.json --preset D:\mine\preset.py
flowocr-typeset out\v\v-default.script.ass --fx D:\mine\fx.py
```

- `examples\matcher_minimal.py`：查一张"识别文本 -> 原文"的对照表；
- `examples\preset_minimal.py`：最小的输出预设；
- `examples\preset_srt_oneline.py`：和 `default_srt` 同一批条目、换一条文字规则（一条拼成一行）；
- `examples\fx_minimal.py`：最小的特效——在字幕稿某行特效列的 `fo:…` 里加 `;frame`，这一行就沿原文框画一圈框。

入口的约定写在各个例子的文件头。匹配器可以在文件里写 `PRESET = "<预设名或 .py 路径>"`（和可选的 `PRESET_OPTS = {...}`）推荐一个输出预设：它记进匹配产物，之后 `flowocr-render <matched.json>` 不给 `--preset` 时就用它（会打一行说明；命令行的 `--opt` 优先）。写相对的 `.py` 路径时按匹配器文件所在的目录找，记进产物时换成可移植的路径；之后在哪个目录下渲染，加载的都是同一个文件。
匹配器要自己的组和默认画不画，照[画哪些](matching.md#画哪些show--hide)的档位声明。

匹配器、输出预设、特效的 `.py` 文件会**以你的用户权限直接执行，不做隔离**：只加载自己写的或信得过的文件，第三方扩展的依赖自己装。
扩展入口的形状以本节和 `examples\` 为准，目前没有版本机制；没写进本节的内部实现不承诺长期兼容，升级后扩展失效时看 CHANGELOG。

## 排错

| 现象 | 原因与处理 |
| --- | --- |
| `没装推理后端（onnxruntime）` / `onnxruntime-gpu 和 onnxruntime 同时装着` | 装的时候没选 extra，或两个都选了：按提示重装，`nvidia` / `cpu` 只选一个 |
| 找不到 `ffmpeg` / `ffprobe`，或缺 `showinfo` 滤镜 | 装完整构建的 ffmpeg，把它的 `bin` 放进 `PATH` |
| 找不到 `flowocr-ocr` 这类命令 | 直接装的漏了 `uv tool update-shell`（然后重开终端）；从源码装的没激活 `.venv` |
| `--device auto 退回 CPU：…` | GPU 不可用，原因在这一行里；想用 GPU 就按原因修（驱动、CUDA 运行库），否则忽略 |
| `[设备] GPU 可用，但试推理出错——不退回 CPU…` 然后退出 | GPU 可用但出错。按报错修，或显式 `--device cpu` |
| `[后端] ⚠ --backend …：…，用 CUDA EP` / `[trt] det 退回 CUDA EP：…` | TensorRT 没用上，原因在这一行里（没装、版本对不上、建引擎或自检出错）。结果照常可用；`flowocr-trt status` 看库和缓存，删掉重装用 `flowocr-trt remove` 再 `install` |
| `[后端] ⚠ … 次形状超出 TensorRT 引擎的范围、… 次 TensorRT 报错…` | 有几批超出了引擎的形状范围（多半是调大了 `--det-batch`（超过 4）或识别批），或 TensorRT 运行中报错；这几批用 CUDA 跑了，结果照常可用。报错反复出现时先用 `--backend cuda`，并开 issue 附上那一行 |
| 第 2 步打印 `obs 里没有在线回抠的证据…产物停在采样级` | 观测文件太旧（或第 1 步关掉了这项），起止时间只精确到 ±0.25 秒、打字机不逐字显出。用默认设置重跑第 1 步，再跑第 2 步 |
| 第 2 步的输出里没有 `←主轨`，也没有 `主轨并带` | 没有可当主轨的区域，字不是没读到：见[产物怎么读](README.md#产物怎么读)里主轨那段 |
| 模型下载失败 | 连不上 Hugging Face 时可用 `HF_ENDPOINT` 指向镜像。离线用默认识别模型时装 [GitHub Release 的模型包](README.md#数据目录与模型)；用 LaMa 时按[烧进视频的离线使用](burn.md#离线使用)单独准备 |
| `没找到 <游戏> 的游戏文本包` | 包没放进数据目录的 `gametext\`（`flowocr-paths` 看数据目录在哪），或 `--gametext` 指错了 |
| `文本包 … 和清单对不上（sha256）` | 包拷坏了或被改过，重新拿一份 |
| `文本包 … 缺 …×ja` | 这份包是收窄语种打出来的，缺日文；要一份带中日文的包 |
| `… 的 schema 不是 flowocr-gameref/7：…（重跑 gamescript）` | 1.0.0 之前做的剧本，重跑 `flowocr-gamescript`，再重跑匹配 |
| `预设 … 吃的是 [… 'flowocr-matched/8']，给的是 'flowocr-matched/7'` | 1.0.0 之前的匹配产物，从轨道产物重跑匹配（见[对上原文](matching.md)开头） |
| 第 4 步报找不到视频（取色时）/ 第 5 步找不到源视频 | 视频挪了地方：加 `--video <视频>`（见[烧进视频](burn.md)） |
| 第 5 步 `--inpaint` 报 `invalid choice` | `--inpaint` 写到了 ASS 前面：写成 `flowocr-burn x.ass --inpaint` |
| 第 5 步 `--inpaint` 报底板取过色 / 没有打了标的底板 | 第 4 步不开取色、用当前版本重出叠加 ASS（见[两条路怎么选](burn.md#两条路怎么选)） |
| 第 3 步报 `… 已经在（可能是改过的字幕稿），不覆盖` | 同名字幕稿已经在：要重出加 `--force`（改过的先另存），或 `--outdir` 写到别处 |
| `… 不是这一版的版式文件` | `--ctx layout=` 给的文件格式不对：换一份当前格式的，或 `--ctx layout=none` 不用 |
