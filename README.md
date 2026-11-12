# flowocr

把视频里**出现过的所有文字**读出来——不只是底部字幕，还有对话框、名牌、画面上的标注——每条带起止时间（精确到一两帧），
并按位置自动分成若干条"文字轨"（对白、字幕、界面、水印……）。产物是 SRT，或叠加在原画面上、每条字留在原位的 ASS 字幕，也能直接烧进视频（可以先把原文抹掉）。

## 用来做什么

- **画面文字翻译**：画面各处的文字都读出来、按位置分好轨；在字幕稿里把原文改成译文，叠加时每条译文留在它原来的位置上。
- **字幕与原文对齐**：游戏、影视常常能找到原文剧本。把读到的字幕对上原文，拿到准确的文本和时间轴，用来做翻译字幕。
  原神、崩坏：星穹铁道、绝区零有现成的提取工具，按它的说明自己打出文本包（见[相关项目](https://github.com/caca2331/FlowOCR#相关项目)）。

## 能读什么、在哪跑

- **文字**：识别模型是 PaddlePaddle 的 PP-OCRv6（官方称支持约 50 种语言）。实际验证过的是日文（几十小时实况录像和四款游戏的直播录像）与简体中文；
  英文只做过零星试跑；竖排文字没测过。
- **系统**：Windows 10 / 11；Linux 只在 WSL2 的 Ubuntu 24.04 上验证过。有 NVIDIA 显卡时走显卡；没有也能跑，用 CPU，慢一到两个数量级。
- 另要 [ffmpeg](https://ffmpeg.org/)（完整构建）。

## 快速开始

要先装 [uv](https://docs.astral.sh/uv/) 和完整版 [ffmpeg](https://ffmpeg.org/)；从源码装、参数和产物说明都在 [使用手册](https://github.com/caca2331/FlowOCR/blob/main/docs/manual/README.md)。
第一次运行会自动下载约 0.2 GB 模型（连不上 Hugging Face、或要离线用，见手册的[数据目录与模型](https://github.com/caca2331/FlowOCR/blob/main/docs/manual/README.md#数据目录与模型)）；长视频先在第 1 步加 `--start 0 --end 60` 试一段。

```powershell
uv tool install "flowocr[nvidia]"                   # 没有 NVIDIA 显卡用 "flowocr[cpu]"；驱动要 R580 或更新，不够时自动用 CPU
uv tool update-shell                                # 第一次用 uv tool 时，然后重开终端
```

有原文剧本时，推荐**开启匹配器，使用默认配置，先做到第 3 步的字幕稿**；导入 [Aegisub](https://aegisub.org/) 并加载视频，检查文字、时间和位置，改好保存后再跑第 4 步出特效。
下面以星穹铁道为例，画中文译文（来自文本包）；录像要用游戏的**日文**界面（按日文原文对，中文界面的录像对不上）。先按 [game-text-data](https://github.com/caca2331/game-text-data) 的说明准备文本包，
放进数据目录的 `gametext\`：直接装的是 `%LOCALAPPDATA%\flowocr\gametext\`（没有就新建；`flowocr-paths` 打印数据目录在哪）。

```powershell
flowocr-ocr video.mp4 --out out\v\v.jsonl           # 第 1 步：读字
flowocr-gamescript out\v\v.jsonl --out out\v\v-ref.json --game starrail                            # 从文本包圈出剧本
flowocr-tracks out\v\v.jsonl --outdir out\v --tag v --matcher gametext --matcher-ctx game=starrail   # 第 2 步：建轨
flowocr-match out\v\v-tracks.json --matcher gametext --ctx ref=out\v\v-ref.json --out out\v\v-matched.json   # 对上原文
flowocr-render out\v\v-matched.json --preset script --opt lang=cn   # 第 3 步：只写字幕稿 v-cn-main.script.ass（lang=cn 中文译文，en 英文，不给是日文原文）
```

在 Aegisub 中检查并保存 `out\v\v-cn-main.script.ass`，然后用 `default` 预设出特效字幕：

```powershell
flowocr-typeset out\v\v-cn-main.script.ass --preset default   # 第 4 步：写 v-cn-main.ass（成品要先抹原文时加 --opt plate=src）
flowocr-burn out\v\v-cn-main.ass                              # 第 5 步（可选）：烧进视频，写 v-cn-main.mp4；--inpaint 先抹掉原文
```

原神 / 绝区零把 `starrail` 换成 `genshin` / `zzz`。没有剧本时省略剧本提取与匹配，用 `flowocr-tracks` 不带匹配参数建轨，再对 `v-tracks.json` 跑第 3 步：
只翻主字幕用 `--preset script`（字幕稿为 `v-main.script.ass`）；画面各处的字都要翻用 `--preset default_all`，在字幕稿里自己把原文改成译文，见手册的[画面文字翻译](https://github.com/caca2331/FlowOCR/blob/main/docs/manual/script.md#画面文字翻译)。

## 相关项目

- [game-text-data](https://github.com/caca2331/game-text-data)：从原神、崩坏：星穹铁道、绝区零的社区解包数据里提取对白文本（中日英对齐），
  打成 flowocr 能直接读的文本包。它只公开提取代码，文本本身不分发——要用的话按它的说明自己打包。

## 问题与参与

- 用起来有问题、或者在别的显卡 / 机器上跑出了结果，欢迎开 [issue](https://github.com/caca2331/FlowOCR/issues)。
- Pull request 请以 `main` 为目标。开发环境、代码结构和验证方法见 [开发指南](https://github.com/caca2331/FlowOCR/blob/main/docs/dev-guide/README.md)，全部文档见 [文档地图](https://github.com/caca2331/FlowOCR/blob/main/docs/README.md)。
- 版本变化见 [CHANGELOG](https://github.com/caca2331/FlowOCR/blob/main/CHANGELOG.md)。

## 许可

GPL-3.0-or-later，全文见 [LICENSE](https://github.com/caca2331/FlowOCR/blob/main/LICENSE)。识别用的 PP-OCRv6 模型由 PaddlePaddle 以 Apache-2.0 发布，来源与我们的改动见 [LICENSES/](https://github.com/caca2331/FlowOCR/blob/main/LICENSES/PP-OCRv6-NOTICE.md)。可选的 TensorRT 后端由用户自己从 NVIDIA 下载、按 NVIDIA 的许可协议使用，flowocr 不分发它（[说明](https://github.com/caca2331/FlowOCR/blob/main/LICENSES/TensorRT-NOTICE.md)）。
