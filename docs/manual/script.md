# 字幕稿：改稿与出特效

第 3 步写出的**字幕稿**（`*.script.ass`）是一份普通的 ASS，也是唯一要人改的地方：在 [Aegisub](https://aegisub.org/) 里改文字、时间、位置，
再用第 4 步 `flowocr-typeset` 按它出带特效的叠加 ASS。两条路都经过这里——画面文字翻译在这里把原文改成译文；[对上原文](matching.md)时选了 `lang=cn` / `en` 的稿已经是译文，在这里校对。
文件名的规矩见[产物怎么读](README.md#产物怎么读)。
第 3 步不覆盖已有的字幕稿（它可能是你改过的）：同名的已经在就报错退出、一个文件都不写；确定要重出就加 `--force`（改过的先另存一份），或用 `--outdir` 写到别处。

## 画面文字翻译

画面文字翻译要的是**所有位置**的字，所以第 3 步用 `default_all`（`default` 只画主轨和名牌）。前两步同[第一次跑通](README.md#第一次跑通)（整部视频去掉 `--start` / `--end`）：

```powershell
flowocr-ocr video.mp4 --out out\v\v.jsonl                   # 1. 读字
flowocr-tracks out\v\v.jsonl --outdir out\v --tag v         # 2. 建轨
flowocr-render out\v\v-tracks.json --preset default_all     # 3. 写 v-default_all.script.ass 和 v-default_all.ass
```

1. 用 Aegisub 打开 `v-default_all.script.ass`（可以同时载入视频对照），把每行的原文改成译文；不要的行（常驻 UI、水印、日文汉字上方单独成行的[振り仮名](reference.md#振り仮名)……）删掉或注释掉。
2. 定下成品要不要[先抹掉原文](burn.md#两条路怎么选)：要抹，下一步加 `--opt plate=src`。
3. `flowocr-typeset out\v\v-default_all.script.ass`（要抹原文时加 `--opt plate=src`），重新出 `v-default_all.ass`：译文按原文框重新排字号和底板，打字机、移动照旧。
4. 看效果：`mpv video.mp4 --sub-file=out\v\v-default_all.ass`。
5. 烧进视频：`flowocr-burn out\v\v-default_all.ass`，要抹原文的写 `flowocr-burn out\v\v-default_all.ass --inpaint`（见[烧进视频](burn.md)）。

只翻主字幕：第 3 步换成 `--preset script`，改 `v-main.script.ass`，第 4 步 `flowocr-typeset out\v\v-main.script.ass` 写 `v-main.ass`，其余同上。

不改字幕、一步到底时用 `flowocr-render out\v\v-tracks.json --preset default_all --burn inpaint`，它会替你带上 `plate=src`（这一份已经出过时同样要加 `--force` 或换 `--outdir`）。

## 字幕稿里有什么

一条字幕一行：画面上一次显示的一句话是一条，几行之间用 `\N` 隔开，翻译时整句改、换行随意。样式按角色分：

| 样式 | 是什么 |
| --- | --- |
| `Body` | 正文 |
| `Choice` | 选项 |
| `UI` | 常驻 UI |
| `Name` / `Title` | 名牌 / 名牌下面字小一号的称号 |
| `PlainName` | 字色和游戏名牌色对不上的名牌（匹配产物才有，见[名牌](matching.md#名牌)） |
| `StrayName` | 像名牌、但没挂在台词上或没认出中文名的字 |
| `Special` | 横幅、战斗喊话这类特殊上屏形式的台词与提示（游戏文本包匹配器才有，见[画哪些](matching.md#画哪些show--hide)） |

还有别的样式（对话框外的台词、阅读面板……），名字照字面。在 Aegisub 里改一个样式的字号、颜色，用这个样式的所有行一起改。

- Actor 列是区域号（`r3`）。
- 正文是纯文字，前面一个位置、字号的 tag 块。特效（Effect）列里的 `fo:…` 是给第 4 步看的参数（原文框、打字机时长、轨迹……），不用管它。
- 字幕稿里预览不到打字机（最终 ASS 里有）；要自己调某一行的节奏，在那一行写卡拉OK计时（`\k`），Aegisub 的卡拉OK模式里可以逐字调。
- 对上原文的稿里还会有剧本带来的样式（粗体、字色）、Ruby 标记和占位标记，怎么改见[剧本里的样式与 Ruby](matching.md#剧本里的样式与-ruby)和[运行时才定的字](matching.md#运行时才定的字)。

## 改完出特效

```powershell
flowocr-typeset out\v\v-default.script.ass                  # 改完字幕稿，重新出 v-default.ass
flowocr-typeset out\v\v-default.script.ass --preset dev     # 看分区用的样子（同第 3 步的 dev）
flowocr-typeset out\v\v-default.script.ass --video D:\clips\v.mp4   # 视频挪了地方
flowocr-typeset out\v\v-default.ass --restore               # 只有特效版时，还原出字幕稿（已有字幕稿不覆盖，要覆盖加 --force）
```

- 第 4 步的 `--preset` 只管长相：`default`（不给时）是成品样子，`dev` 同第 3 步 `dev` 的样子；画哪些行由字幕稿定。
- 改文字、改时间直接生效；改了译文，字号和底板会重新适配。
- 拖了位置、改了字号：以你改的为准，这一行不再按原框自动摆 / 适配，第 4 步会打一行提示。
- 自己注释掉的行、别的工具生成的特效行不会被动。第 4 步可以反复跑，也可以直接跑在它自己的输出上。
- **要先抹原文再烧**（[烧进视频](burn.md#两条路怎么选)的 `--inpaint`），这一步就加 `--opt plate=src`：底板只盖原文，抹的也就只是原文。不加时底板也盖着译文画出来的地方，抹的范围会多出来。
- **底板颜色**：默认是近黑半透明的。近黑底板压在浅色对话框上太显眼时，加 `--opt plate_color=on` 让第 4 步读源视频、给底板取画面的背景色，正文按底板深浅改黑字或白字；
  读视频的时间约是视频时长的十分之一（对话越密越长；有 N 卡时用硬解，CPU 占得少）。视频的位置第 3 步记在字幕稿里，挪了地方就加 `--video <视频>`。
  要先抹原文再烧的稿不要取色。加了 `plate=src` 的稿原样烧、用 mpv 看时，译文伸出原文的部分没有底板。
- **字体与播放器**：字号和底板是按微软雅黑（Microsoft YaHei）量出来排的。在 Aegisub 里把样式换成别的字体，同一个字号能差两成，底板就对不上了；字体名必须是这台机器上真有的，写错了播放器会悄悄换成别的字体。
  逐字显出、底板、淡入淡出只在 libass（mpv 和 ffmpeg 用的都是它，[烧进视频](burn.md)也走它）上核过；MPC-HC 这类 VSFilter 系播放器没核过。
- 格式细节见 [产物格式的字幕稿一节](../architecture/artifacts.md#5-字幕稿阶段-3-写阶段-4-读)。

## 时间可能错了的字幕

第 3 步写完字幕稿（`v-tracks.json`、匹配产物出的都算），如果有起止时间疑似错了的字幕，会多打一行 `⚠ 时间疑点 N 条：…`，后面给出逐条看的命令（写好了用哪个 Python、带上这次字幕稿生效的 `--opt`），把 `<这份产物>` 换成路径照抄即可。
直接装的在 PowerShell 里它等于：

```powershell
& "$(uv tool dir)\flowocr\Scripts\python.exe" -m flowocr.output.timing_check out\v\v-matched.json --opt lang=cn --json out\v\timing.json
```

从源码装、激活了 `.venv` 时开头直接写 `python`；Linux 的写法见[直接装](README.md#直接装)。

每条给出字幕稿里的时刻（`H:MM:SS.cc`，在 Aegisub 里直接跳过去）、规则、偏差和证据；`--opt` 照那行提示给的抄，查的就是同一份字幕稿（`default_all`、`dev` 预设会替你加上 `show=all` / `keep=all`，提示里已经算进去了）。
它只读产物、不改任何东西，报的是**线索**：摆帧看过才算数。它宁可多报，不求每条都是真错；
下表最后一列是开发时在几部游戏直播和视觉小说的整片上抽样摆帧（每条规则 40–90 条，A2 只有 16 条）量到的真错比例，时间紧时按它从高往低看。

匹配产物里同一句话被认领了几次时，除"主"以外的那几次是回显（对话记录面板里的旧台词、重演、同时出现在别处……，见[同一句出现在几处](matching.md#同一句出现在几处)）。
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

检测器重算的是产物加 `--opt` 下的那组时刻，**不读你在 Aegisub 里手改过的字幕稿**：手改之后它的结论不再对应字幕稿，要查就重新出字幕稿（加 `--force` 会冲掉改过的稿，可以换 `--outdir`）再跑。
