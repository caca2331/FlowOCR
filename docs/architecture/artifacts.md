# 产物格式：一份富信息 JSON + 按需导出 + 可编辑的字幕稿

**这是产物侧的现行契约**（2026-09-09 落地；字幕稿 2026-09-27 加，script-fx 计划）。需求怎么来的、实现过程中四轮复审揪出什么，
在 output-format-plan（已归档）（已归档的计划书）。

- **机器侧**的一级产物只有一份 `<tag>-tracks.json`（匹配产物同理），别的格式都由阶段 3 从它投影；
- **人工编辑层**是阶段 3 写的[字幕稿](#5-字幕稿阶段-3-写阶段-4-读)（`*.script.ass`）：在 Aegisub 里改，阶段 4 只读它、不回读阶段 2。

工具：`flowocr.artifacts.tracksio`（形状 + 读写校验）、`flowocr.output.export`（投影出 SRT）、
`flowocr.output.script`（投影出字幕稿）、`flowocr.typeset`（阶段 4：字幕稿 → 最终叠加 ASS）；入口是 `render` 的预设和 `flowocr.typeset`。

## 1. 一级产物只有一份

`build_tracks` 写 **`<tag>-tracks.json`**，它是机器侧唯一的事实来源：

```text
schema / tag / meta / frame_us / size / window_sec / n_windows
provenance : git_head, argv, args, main_track, main_srt, main_cues, main_candidates
regions[]  : index, label, lang, primary_score, n_windows_present, features,
             align?（left / center / right）, align_how?（by = stats / vote / none、n、spread、votes）
tracks[]   : id("r00" / "main" / "np"), kind, region|regions, label, srt, ui_lines[], cues[]
             cues[] : id, t_start, t_end, events[]（事件 id，**顺序就是导出时的行序**）
events[]   : id, region, t_start, t_full, t_end, box, text, conf, n_obs,
             n_independent, flags[], boxes[]?（只有 moving 的才落）, texts[]?（可选）,
             t_full_sampled?（采样级全字出现）, t_full_how?（回抠填：首字 / 全字各是 agree / single / none；全字只防晚很多：没有两路同意且比最早候选晚 10 帧以上时夹回、记 capped）,
             t_full_end?（回抠填：完全显示结束）, t_end_how?（t_end 来自 curve 字形对比度 / ncc 整框相关系数；open = 测量窗里一直没看到它消失——字到窗尾还在屏上或视频先结束——t_end 只是下界、不填 t_full_end）
text_effect? : 回抠写的整段素材效果统计：samples, verdict?（instant / typewriter / likely-typewriter；样本 < 8 不写）,
             ms_per_char_median?, iqr_over_median?, fade_out_*?
```

* **`align` 是区域的对齐方式**（2026-09-24，overlay-style 计划）：
  `flowocr.analyze.align` 在 `build_tracks` 落盘前判——区域统计（左沿 / 中心 / 右沿哪个最稳）判不出时，
  由同屏上下相邻的两行成对投票；叠加导出只照着锚边。**可选字段**：旧产物没有它，不读对齐的下游照常能读；
  叠加预设读到没有它的产物当场报错、提示重跑 build_tracks（只是阶段 2）。

* **`kind` 有三种**（2026-09-20 加了第三种）：
  * `region` —— 一个区域一条，**下游遍历 tracks 时只该认这一种**（见下面第 4 条契约）；
  * `main` —— 并带之后的聚合投影，装的是区域轨里已经有的同一段文字；
  * **`nameplate`** —— 打了 `nameplate` 标的 run 的投影（`<tag>-nameplate.srt`，id 固定 `np`）。
    判据是 `flowocr.analyze.uigate.pick_nameplates`（框位 + 在正文带上方 + 时长 ≥ 台词 + 词表小，
    **按时间分窗**；ui-gate 计划）。
    ⚠ **它和区域轨有意重叠**：同一条 run 两边都在——用途 1 要在原位看到名牌，
    用途 2 要把它并进正文（`merge_nameplate --paste`）。
* **`-provenance.json` 并进去了**，不做兼容；`clear_stale_srts()` 会把旧的那份
  连同 `<tag>-main.srt` / `<tag>-nameplate.srt` 一起删掉（它们都是"某个开关打开时才产"的文件，
  关掉之后旧的留在原地最危险）。
* 旧格式的产物 `tracksio.load` **当场拒收**（"schema 不是 `flowocr-tracks/1`"）——
  响，不是静默降级。`out/` 里 47 个历史 A/B 臂**故意**留在旧格式：文档里的数出自它们。
* 下游取值走 `python -m flowocr.artifacts.tracksio <tracks.json> main_srt`，**别自己 `json.load`**
* **匹配产物 `*-matched.json` 同理**：格式、版本和"匹配上用原文否则留 OCR"的规矩只有一份，在
  `flowocr.artifacts.matchedio`（2026-09-22）；从它出字幕走阶段 3 的入口
  `python -m flowocr.output.render <matched.json> --preset matched_srt|default|default_all|dev`，
  **各层盖哪些块已经在阶段 2 判好写进 `overlay.items`，预设只投影、不重新判断**（同第 1 条）
  （那样校验就丢了）。

## 2. 四条契约

1. **切分、常驻 UI 的判定、并带的结果都已经写在 JSON 里**，导出器只做**投影**。
   回抠在建轨末尾结算之后也走同一个导出器重出 SRT，所以 audit-4 C6（当年回抠 CLI 的 `--srt-only` 绕过输出过滤）在结构上不可能再发生。
   **⚠ 保持关注**（2026-09-14）：用途 2 的叠加 ASS（`scriptmatch --ass`）里"主轨外的选项按钮 / 阅读面板 / 名牌该不该盖"
   原先在导出时现场判；现在判定写进 `*-matched.json` 的 `overlay.items`，导出只按层投影（matcher 计划）。
   但**规则本身**是按预览摆帧长出来的（`choice_items`）、`readable_items` 仍直接读上游 TextMap（原型）、名牌判据会认进键位和 UI——
   owner 要求对这块持续关注（产品方向记录）。
2. **过滤不删事件、只打标**：事件带 `ui_filtered`（文本那条判据，**逐轨**）/ **`ui_footprint`**（框位 + 时间占比那条，**全局**）/ **`nameplate`**。⚠ **两种 UI 判决的作用域不同，别混成一个 flag**——混了导出器会把『区域轨里判的 UI』带进主轨（实测 592 → 541，守卫当场抓到）。文本那条的权威清单是**逐轨**的
   `tracks[].ui_lines`（同一行在区域轨里够比例、并进主轨后不一定够）。
   用途 1 要留、用途 2 要剔，同一份数据。
   另有 **`ruby`**（振り仮名：紧贴含汉字正文上方、字高约一半、全假名，**全局**、按几何）：它不是 UI，是正文的注音，
   和 UI 一样只打标：剔 UI 的导出（`cue_lines` 默认、SRT、匹配器、`default` 叠加）不给，全部文字的视图（`drop_filtered=False`、`dev` / `default_all` 叠加）照给、按 UI 标——
   判据是几何的、会误标，误标的字要读得回来（2026-09-26，[defaults.md 1.30](defaults.md#130-建轨的四项2026-09-26)）。
   框位 UI 和注音在切 cue **之前**就和正文分开：区域轨里它们自成 cue，并带主轨里不收。
3. **下游遍历 `tracks` 要用白名单 `kind == "region"`，不是黑名单**（2026-09-20 的真回归）：
   `scriptmatch.feed_from_doc` 原来只排 `kind == "main"`，名牌轨一加就被**喂了第二遍**，
   而且是"独立成条"的形式——`methodology-audit-3` 记过代价：只有名字的 cue 会去抢
   含名字的剧本行，**重复认领涨 2.2~7.0 倍**。守卫 `t_panel_like` 里钉了
   "加一条名牌轨，三种 `--feed` 的条数都不变"。
4. **`t_full` 没回抠时是 `null`，不许拿 `t_start` 顶替**（打字机三态）；
   `boxes[]` 只给 `moving` 的事件；`n_independent` 全是沿用票时**就是 0**。

## 3. 回抠只更新时间

`refine_boundaries` 就地改事件的 `t_start / t_full / t_end`，然后
`tracksio.refresh_cue_bounds(doc, before)` 把 cue 的边界跟着挪——**切分一个字不动**。
`before` 是回抠前的快照（`event_times()`），没有它就一个边界都不动。

三条规矩（每条都是一次复审换来的，`refresh_cue_bounds` 的 docstring 里有反例）：

| 规矩 | 不这么做会怎样 |
| --- | --- |
| 一条 cue 的边界**只认它自己的成员**（起点取最早、终点取最晚） | 别的轨、别的 cue 里碰巧同一时刻开始/结束的事件会把它拽走 |
| **有成员跨过切点**才算共享、才一起挪（不是"时间相接"） | 相接但独立的两条被绑在一起，后一条会在**空白期提前显示** |
| 夹只夹这一条自己（首尾不倒挂），**不跨 cue 夹单调** | `raw`/`cue` 档的 cue 本来就能时间重叠，跨 cue 夹会把后一条硬推到前一条的终点上 |

**起点口径（`first` / `full`）是导出时的投影，不写进 JSON**（`export.cue_start`）——
写回去之后那个值不再等于任何事件的首字时间，就再也切不回 `first` 了。

## 4. 导出

```powershell
python -m flowocr.output.export <tracks.json>                        # 逐轨 SRT（与 build 写的逐字节相同）
python -m flowocr.output.render <tracks.json> --outdir tmp\ov        # 叠加：默认预设 default（只留主轨），写字幕稿 + 最终 ASS
python -m flowocr.output.render <tracks.json> --preset dev --outdir tmp\ov   # 开发看：全部区域、聚类编号
python -m flowocr.output.render <tracks.json> --preset script --opt keep=all --outdir tmp\ov   # 只写字幕稿
python -m flowocr.typeset tmp\ov\<tag>-all.script.ass [--preset dev]  # 改完字幕稿再生成最终 ASS
```

**叠加字幕**（把字压回原位，overlay-style 计划；2026-09-27 起分成字幕稿和特效两步，script-fx 计划）：
tracks 产物叠 OCR 原文，matched 产物叠剧本译文，走同一个投影核心 `flowocr.output.script`，写成[字幕稿](#5-字幕稿阶段-3-写阶段-4-读)；
阶段 4（`flowocr.typeset`）读字幕稿生成最终 ASS。三个叠加预设两步连跑、两份都留下：

| 预设 | 保留（tracks / matched） | 样式 |
| --- | --- | --- |
| `default`（`render` 的默认） | 主轨 + 名牌轨 / `body`、`extra`、`name` 三层 | 底板 α 0x20 |
| `default_all` | 全部区域轨，常驻 UI 照常画 / 全部六层，另把没叠译文的事件照 tracks 画 OCR 原文 | 底板 α 0x20 |
| `dev` | 同上，UI 黄字（#ffe033）；字幕稿另写效果判定的依据 | 非主轨浅蓝（#73d7ff）、底板内侧右上角红字（#ff2d2d）区域号、底板 α 0x80 |

* **取事件按轨取**：`default` 走主轨的 `cue_lines`（和 `srt_main` 同一个行集），全部区域时按**那条区域轨自己的**
  `ui_lines` + 全局的 `ui_footprint` 标 UI——不用事件上的 `ui_filtered`（第 2 条契约）。**一个事件只画一次**：
  `segment` 模式下同一事件在好几条 cue 里，按 cue 画会叠好几层。
* **对齐**：锚哪条边看区域的 `align`；原文锚这一行自己的框，译文（行数重分过）锚整组的公共轴。
* **效果检出即开**：打字机逐字显出（按事件判：被回抠过的看全字出现离首字 ≥ 2 个原生帧，整段判"字确实没有生长"时不做；
  没被回抠的看 `t_full_sampled` ≥ 2 个采样间隔）；有 `t_full_end` 就复刻淡出；淡入最长 300 ms。在动的、名牌、选项不做。
  门的细节和依据见 [defaults.md](defaults.md) 的叠加 ASS 一节。判定在阶段 3 做一次，写进字幕稿（`\fad`、`fo` 的 `tw`）。
  `--opt typewriter=dim` 是审计时刻用的两态（全字出现前半透明）。
* 不动的事件一段；**会动的按轨迹逐段**（停顿 `\pos` / 位移 `\move`，裁剪处端点插值），底板、正文、编号一起挪——
  一条从首点到末点的 `\move` 会把停顿和步进抹成匀速滑。Actor（Name 字段）是区域号 `r<n>`。
* 译文比原文框多出几行时，按原来一行的高度扩出几行、逐行画（2026-09-27 前按框逐行配对，第二行起被丢掉）。

**字号换算是量出来的**（一次性探针 `probe_ass_fontsize.py`，libass 渲已知字号、量白色字形高）：
叠加 ASS 只用 Microsoft YaHei，ratio = Fontsize / 字形高 = **1.35**（CJK 正文）。同一个 Fontsize 换字体能差两成，
换字体要重跑探针。⚠ 字体名必须是这台机器上真有的：写一个不存在的，libass 会**静默换替身**，比例当场作废。

**框宽也给字号设上限**（2026-09-12，owner 看预览时指出只按框高定、字会冲出框）：
每行文本先在参考字号下**用 libass 真渲一次**、量墨迹宽（`export.ink_widths`，一页一次 ffmpeg），
墨迹宽和字号成正比，于是 `Fontsize = min(框高 × ratio, 框宽 / 字号 1 的墨迹宽)`。
不拿字体文件的度量估：标定时 Yu Mincho 系统性偏 17%、Consolas 渲日文回退到别的字体后 −15%～+40%。
译文另有保底（不低于框高换算的 75%，选项按钮的译文被压得过小那次加的）；原文不保底，框偏高时保底会把字撑出框。
核对（一次性探针 `probe_ass_fit.py`，按定好的字号再渲一遍量落没落在框里；2026-09-24 用 YaHei 重量）：
quick 的 q-gi-s2 全部 140 条事件墨迹宽 / 框宽 P50 0.69、最大 1.00；q-hsr-s2 抽 600 条 P50 0.97、最大 1.04，
超出 2% 的 9 条都是 1–3 个字的短框（`企`、`艾光`、`36m`）。

## 5. 字幕稿（阶段 3 写，阶段 4 读）

字幕稿是一份普通的 ASS（`*.script.ass`），在 Aegisub、mpv 里直接打开就能看到大致效果：位置、打字机、淡入淡出、一个预览用的底框。
人在这里改文字、时间、位置、打字机节奏；阶段 4 只读这份文件，不回读阶段 2，所以一份字幕稿就能交给译者、校对。
Aegisub 里的修改**不回流**到阶段 2 产物（owner 2026-09-27：先单向）。

**两类行**（结构照 Aegisub 卡拉OK模板的做法，标记用自己的——kara-templater 删**所有** Effect 为 `fx` 的行，借它的标记会互删）：

| 行 | 样子 | 谁写 |
| --- | --- | --- |
| 源行 | `Dialogue:`、Effect 是阶段 3 写的参数 `fo:…`（手补的行 Effect 为空）；阶段 4 处理过之后改成 `Comment:`、Effect 前面加 `fo-src\|` | 阶段 3，人编辑 |
| 生成行 | `Dialogue:`、Effect `fo-fx`、样式是派生的 `<Style>.fo-fx` | 阶段 4 |

* **阶段 4 可以反复重跑**：先 `restore`（删 `fo-fx` 行和派生样式、把 `fo-src|` 行恢复成源行、去掉前缀，得到逐字节相同的字幕稿）再生成，
  所以阶段 4 的输出也是合法输入；`python -m flowocr.typeset x.ass --restore` 单独还原（`x.script.ass` 已经在就不覆盖——它可能是改过的，
  要覆盖加 `--force`，或 `-o` 另写一份）。提示里的行号是输入的那份文件的行号。
* **不碰的行**：用户自己注释掉的行（没有 `fo-src|` 前缀，带不带 `fo:` 参数都一样）、Effect 列有别的值的行（别人的 `fx`、`Banner;`…）原样留着、不生成。
* **字段**：Style 按角色（`Body` `Extra` `Name` `Choice` `Offmain` `Panel` `UI`，Style 表示"这是什么"）；Actor 是区域号 `r<n>`（在不在主轨另由 `fo` 的 `off` 记）；
  Effect 是阶段 4 的参数；`[Script Info]` 里 `FlowOCR Script`（格式版本）、`FlowOCR Source`（来自哪份产物，带 tracks `events[]` 的内容指纹 `events-sha256:…`——`ev` 是它的下标，
  指纹对不上的产物不能按 `ev` 回去）、`FlowOCR Code`（代码版本）只作追溯——写成键，Aegisub 会丢掉 `;` 注释行。
  派生样式名是 `<Style>.fo-fx`（`restore` 按这个后缀删，用户自己起的 `Sign.fx` 之类不受影响）。
* **一行的样子**：Effect `fo:ev=56 57;box=374 891 1588 931|…;fit=tr;rows;plate;tw=1750;was=…`，
  正文 `{\an5\pos(966,930)\fs52\fad(0,66)}正文第一行\N正文第二行`——正文里一个主流 tag 块 + 文字，
  **正文尽量是纯文字**（owner 2026-09-27：可以按需插 tag，但尽量避免、要便于编辑），参数都在 Effect 里、改字碰不到。
* **主流 tag**：`\an4/5/6` + `\pos`、`\fs`、`\fad`；在动的写一个首点到末点的 `\move` 供预览；多行（同起同止的一组）一条 Dialogue、行间 `\N`。
  样式 `BorderStyle=3` 出预览底框。打字机不写进正文，记成 `fo` 的 `tw`（字幕稿预览里没有打字机，最终 ASS 有）；
  要自己调某一行的节奏就在那一行写 `\k` 族 tag，阶段 4 按人写的节奏、不用 `tw`（样式的次要色全透明、阴影 0，预览也是逐字显出——`\ko` 藏不住阴影）。

**Effect 字段里的 `fo:…`**（owner 2026-09-27 提的）：主流 tag 表达不了的参数放 Effect 字段——它是 ASS 的标准字段，各编辑器原样保留，
Aegisub 里是单独一栏；渲染器只认 `Banner;` / `Scroll up;` / `Scroll down;` 开头的值，`fo:` 开头的忽略（libass 真渲核过：逐像素相同）。
不用 Aegisub 自己的 extradata：别的编辑器不认，保存时可能丢。
`fo:` 可以跟在别的内容后面（`备注;fo:…`、`Banner;10;fo:…`，libass 照样认前面的 Banner），它之后到末尾都是参数；
前面那段只留在源行上、不带到生成行——横幅 / 滚动这类渲染器效果在最终 ASS 里不生效，卡拉OK模板也不该往这些行上套。
项之间 `;`，每项 `名字` 或 `名字=值`；数值列表用空格分隔；值里的 `%` `;` `,` `{` `}` `\` 和换行按百分号编码——
Effect 不是 ASS 行的最后一个字段，逗号会把字段切错（Aegisub 保存时也会把它换成分号）。

| 参数 | 意思 | 归阶段 4 管的 tag |
| --- | --- | --- |
| `ev` | 来自哪些事件的 id，只作追溯；只在产它的那份产物里有效（重跑阶段 2 会重编号） | — |
| `key` | matched：剧本行的键（跨阶段 2 重跑仍稳定） | — |
| `box` | 原文各行的框 `x0 y0 x1 y1\|…` | — |
| `fit=src\|tr` | 按原框适配字号；`tr` 是译文（不低于框高换算的 75%、不超画面） | `\fs` |
| `rows` | 按原框逐行摆（正文行数和 `box` 对得上时；多出来的行扩出去） | `\pos` |
| `cap` | 原文框本来装几行（OCR 原文带换行时一个框装几行；等于框数时不写） | — |
| `plate` / `plate=box\|rows\|text` | 画盖住原文的底板（不写值就用阶段 4 预设的模式） | — |
| `path` | 在动的字的轨迹 `微秒:x0 y0 x1 y1\|…`，**视频时刻**（平移行的时间，轨迹不跟着挪） | `\pos` `\move` |
| `tw` | 打字机：首字出现到全字出现的毫秒数，逐字匀速显出（行里写了 `\k` 族 tag 就不用它） | — |
| `wrap=<行高>` | 面板长文本按框折行 | `\fs`、换行 |
| `off` | 这条不在主轨（`dev` 画浅蓝） | — |
| `was` | 阶段 3 写下的、归阶段 4 管的 tag 的值（`pos:x y\|fs:n\|an:5`，在动的是 `move:…`；按数值比，不管逗号还是空格） | — |
| `why` / `spk` | 效果判定的依据（`dev`）/ 说话人（预留） | — |

**谁说了算**（owner 2026-09-27：改过就听人的）：源行里不归阶段 4 管的 tag 原样保留；归阶段 4 管的 tag（行首块里的 `\pos` / `\move`、
整行字号 `\fs`、对齐 `\an`）的值和 `was` 不一样，就是人改过——这一项以人为准、管它的参数在这一行上作废、打一行提示。
改文字、改换行不算：改译文正是要阶段 4 重新适配。行内的 tag（`A{\fs80}B`、`{\1c&H0000FF&}`、`\t(…)`）原样留着，只管它后面的字；
按原框逐行摆拆成几条时，上一行的行内样式补到下一行开头。
正文行数比原文框装得下的多（`cap`，默认就是框数）时，按原来一行的高度（几个框时取行距）往下扩出多的那几行、逐行摆，
往下会出画面就往上扩，压到别的字不管（owner 2026-09-27）；一个框本来装着几行（`cap` 比框数多）时，这个框按一行的高度（框高 / `cap`）长出去。
扩出的行只改排版，在动的行照原文框的轨迹走。几个框、其中有的框装着几行，这种再加行时不扩，整块画在 `\pos` 并打提示。
正文行数比原文框装的少时整块画在 `\pos`，字号按原来一行的高度适配。
底板按画出来的字宽：行内 `\fs` 改过字号的那几个字按它的字号量。Effect 为空的源行（手补的）照主流 tag 原样生成一条，只换派生样式。
不认识的 `fo` 参数**当场报错**、列出行号（`--opt unknown=ignore` 才跳过）。

**阶段 4 的顺序固定**，面板和普通条目走同一条路：排版（`wrap` 分行 → `fit` 定字号 → `rows` 定锚点 → 量文字边界）→ 元素（底板、正文、编号）→
运动（所有元素一起按 `path` 逐段展开）→ 时间效果（逐字 `\alpha`、`\fad`、配色）。时间效果按**源行**的时间算：
在动的行拆成几段时，淡入淡出换成各段的七参数 `\fade`、逐字显出和人写的 `\t` / `\fade` 减去这一段的起点，段接段连起来和一整条相同，
不在每段从头放一遍；这一段开始前已经变完的 `\t` 换成里面的 tag 本身（libass 把结束时刻 0 读成到行尾，不能减成 0）。
扩展效果的 `tags` 挂点在每一段定稿之后调。
扩展效果（`--fx <模块>`，`flowocr.extensions` 的 `fx` 种类）有两个挂点：第 2 步加元素、第 4 步改元素的 tag；
模块声明 `NAME`（它在 `fo` 里的参数名），源行带了这个参数才调它。例子 `examples/fx_minimal.py`。

## 6. 对账口径

改动了产物路径就要跑这三样（实测数在维护者的计划书里）：

1. `.venv/Scripts/python.exe tests/test_guards.py`（秒级）；
2. **同一份 obs、改动前后的 SRT 逐字节比**——实测 yuka input1 整片 245 份、
   input5 整片 211 份全同；
3. `flowocr-export`（一个时间都不改）重出的 SRT 与 build 写的**逐字节相同**（当年用回抠 CLI 的 `--srt-only` 在 f5 上核过 80 份；CLI 09-26 已去掉，是同一个导出器）；
   再加一遍**压力检**：把每个事件随机挪 ±0.25 s，检查 cue 不倒挂、
   共享成员的相邻对不重叠（f5：211 条轨、26,641 条 cue 全过）。
