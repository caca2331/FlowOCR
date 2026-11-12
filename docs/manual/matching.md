# 对上原文

有原文时，第 2 步建轨之后多一步**匹配**：把读到的字换成原文，并带上官方译文，再用第 3 步出字幕稿、叠加 ASS 或 SRT。
现成的有两种：原神、崩坏：星穹铁道、绝区零用[游戏文本包](#游戏文本包原神--崩坏星穹铁道--绝区零)，Naninovel 引擎的游戏用[它导出的剧本](#naninovel-剧本如魔法少女ノ魔女裁判)；别的原文要[自己写匹配器](#自己的原文)。
出稿之后的校对、出特效见 [字幕稿](script.md#改完出特效)，烧录见 [烧进视频](burn.md)。

> **从 0.3 升上来**：1.0.0 读不了更早的剧本（`*-ref.json`）和匹配产物（`*-matched.json`），都不用重做识别：
> 游戏文本包先重跑 `flowocr-gamescript`，再从已有的轨道产物（`*-tracks.json`）重跑匹配；Naninovel 只重跑匹配。

## 游戏文本包（原神 / 崩坏：星穹铁道 / 绝区零）

**录像要用游戏的日文界面**（语言设成日语）：剧本按日文原文从包里检索、对齐，中文界面的录像对不上；中文、英文是对上之后画的译文。

### 准备文本包

游戏文本包由另一个项目 [game-text-data](https://github.com/caca2331/game-text-data) 从社区解包数据中提取。
**文本版权归游戏的权利人，包不公开分发**：按 game-text-data 的说明自己打包。
要用原神 7.2.0，请检出 game-text-data 的 `v0.2.1` 标签，再按[打包原神 7.2.0](https://github.com/caca2331/game-text-data/blob/v0.2.1/docs/genshin/README.md#打包原神-720)生成文本包。

包是 `<游戏>-<版本>-<指纹>.zip`（如 `genshin-7.2.0-c95edd4bc412.zip`），放进[数据目录](README.md#数据目录与模型)的 `gametext\` 即可，**不用解压**：
直接装的是 `%LOCALAPPDATA%\flowocr\gametext\`（资源管理器地址栏的写法；PowerShell 里新建它：`mkdir "$env:LOCALAPPDATA\flowocr\gametext"`；不确定数据目录在哪就跑 `flowocr-paths`）。
也可以用 `--gametext <文件或目录>` / 环境变量 `FLOWOCR_GAMETEXT` 指过去。解压了也行：解压到 `gametext\` 下，
不管是 `gametext\genshin\` 还是右键"全部解压缩"默认的 `gametext\genshin-7.2.0-c95edd4bc412\genshin\` 都认。
同一款游戏放了两份不同版本会报错，只留要用的那份；zip 和它解压出的目录并存不算两份。
换包后重跑 `flowocr-gamescript` 和匹配，已有的观测和轨道文件可继续用。

### 跑一遍

```powershell
flowocr-ocr video.mp4 --out out\v\v.jsonl                                   # 读字（同第一次跑通）
flowocr-gamescript out\v\v.jsonl --out out\v\v-ref.json --game starrail      # 从包里圈出这段视频打过的对话（"剧本"）；只要第 1 步的观测
flowocr-tracks out\v\v.jsonl --outdir out\v --tag v --matcher gametext --matcher-ctx game=starrail
flowocr-match out\v\v-tracks.json --matcher gametext --ctx ref=out\v\v-ref.json --out out\v\v-matched.json
flowocr-render out\v\v-matched.json --preset script --opt lang=cn           # 只写字幕稿 v-cn-main.script.ass：在 Aegisub 里校对，再跑第 4 步
```

- 三种 `键=值` 参数：建轨的 `--matcher-ctx` 记下这是哪款游戏，匹配时自动带上；匹配的 `--ctx`、`--opt` 都交给匹配器，各自收哪些键照本手册写的抄：写到另一边的键多半不报错、只是不起作用；第 3 步 `flowocr-render` 的 `--opt` 是输出预设的选项（见[输出预设](reference.md#输出预设)），第 4 步 `flowocr-typeset` 的 `--opt` 是出特效的选项（如 `plate=src`，见[改完出特效](script.md#改完出特效)）。
- `--opt lang=` 选画哪种文字：`cn` 中文译文、`en` 英文译文、`jp` 日文原文（不给就是 `jp`）。文件名里带着它（`v-cn-…`）。
- 推荐先只出字幕稿、在 Aegisub 里校对（文字、时间、位置）再出特效：`flowocr-typeset out\v\v-cn-main.script.ass --preset default` 写 `v-cn-main.ass`，
  要先抹原文再烧就加 `--opt plate=src`（见[改完出特效](script.md#改完出特效)）。不校对就直接 `--preset default --opt lang=cn`，一次写出字幕稿和叠加 ASS（`v-cn-default…`）。
- 要 SRT：`flowocr-render out\v\v-matched.json --preset matched_srt --opt lang=cn`，写 `v-cn.srt`（预设的区别见[输出预设](reference.md#输出预设)）。
- `--game` 取 `genshin`、`starrail`、`zzz`；观测文件名以 `gi` / `hsr` / `zzz` 开头时可以省，按前缀认。
- 第 2 步带上 `--matcher gametext`，是让建轨按这款游戏的版式微调分轨参数（目前只有星铁有调整）；星铁已经按默认跑过第 2 步的，带上它再跑一次。

### 英文译文

`--opt lang=en` 画英文：包里有英文那份时，剧本和匹配产物里带着英文，叠加按游戏英文版的样子画——字号让英文字母的平均宽和英文版相近、
按对话框的宽在空格处折行、行数多了照对话框的上下对齐往外扩。1.0.0 之前做的剧本和匹配产物没有英文（也读不了），重跑 `flowocr-gamescript` 和匹配即可（不用重做识别）。
英文字宽按游戏量过的有原神、星铁、绝区零，对不上[版式文件](#译文怎么分行)时按默认值画。
匹配产物里一条所选语种的文本都没有时，出叠加和 SRT 会直接报错。

### 阅读面板

书籍、信件、教程说明这类长文本也要盖译文时，匹配加 `--opt readables=1 --opt obs=out\v\v.jsonl`（选开，要多读一遍观测文件）。
要 game-text-data 0.2.0 起打的带 `readables` 表的包（目前只做了原神）；包里没有这张表会直接报错。
面板滚动时边缘只露出一行的那一段可能暂时不盖译文（一行太短时分不清是不是这一段，宁可不盖）。
渲染时面板默认不画，要打开：`--preset default_all`（全部都画），或 `--preset default --opt show=panel`，中文译文再加 `--opt lang=cn`。

### 运行时才定的字

- 原神里按主角性别变的称谓（他 / 她、哥哥 / 妹妹…）按从视频里认出的主角性别直接填好；要用 game-text-data 0.2.0 起打的带称谓表的包（旧包里这些称谓会被删掉）。
- 剧本里运行时才定的字（玩家起的名字 `{NICKNAME}`、流浪者的名字等）在字幕稿里标成 `` [`0:值] ``：值是匹配时从视频原文读出来的玩家名，
  读不出、或别的占位就空着（`` [`1:] ``）。生成最终 ASS 时标记换成值、空的连同多出来的标点删掉；第 3 步会打一段提示，列出有哪些占位、各几处、填的什么。
  要换名字就在字幕稿里改标记里的值（Aegisub 里查找替换 `` [`0:旧名] ``），或把整个标记换成文字。
  SRT 没有这一步，标记会留在正文里，搜 `` [` `` 逐处改。
- 星铁里玩家给宠物、飞船、社团起的名字（`{TEXTJOIN#n}`）同样标成 `` [`0:值] ``：值是从视频原文认出的那个候选（按字幕的语言给日文或中文），
  认不出就用游戏的默认名；要用 game-text-data 0.2.0 起打的带候选表 `textjoins_*` 的包（旧包里这些名字空着）。

### 核对文本包的版本

- 包里每张表带**内容契约**版本（字段的类型、能否为空、取值，由 game-text-data 定义）。契约主版本和本工具认得的不一样的包，读的时候会报错并说明是哪张表；多半是两边版本差得太远，把 flowocr 和 game-text-data 都更新到最新、重新打包即可。
- 读包时会核对包里每个文件的哈希，包坏了会明确报错；剧本和匹配产物都记着包的**指纹**与 `rev`，
  以后换了新版本的包，看指纹就知道一份字幕出自哪一版语料。对白的语料跟着剧本走；阅读面板层读的是匹配时找到的包，另记在匹配产物的 `overlay.stats.panel_gametext`。
- 包自带的哈希只证明包和它自己的清单一致。要确认手上的包就是维护者发布过的那份语料，
  打印它的指纹，再到 game-text-data 仓库里的 `releases/<游戏>/fingerprints.tsv` 找这一行。
  下面的命令先把包里每个文件的哈希核一遍（每款几秒），对不上就报错、不打印指纹：

  ```powershell
  & "$(uv tool dir)\flowocr\Scripts\python.exe" -m flowocr.analyze.gtdbundle starrail                # 打印指纹
  & "$(uv tool dir)\flowocr\Scripts\python.exe" -m flowocr.analyze.gtdbundle starrail --field json   # 指纹、rev、上游版本等全部来源信息
  ```

  从源码装、激活了 `.venv` 时，把前面的 `& "…python.exe"` 换成 `python`。
  找得到，这一行同时说明它出自哪份上游、哪一版提取行为（`rev`）；`rev` 形如 `1+0a1b2c3d` 的是本地打的包，不在表里。

## Naninovel 剧本（如《魔法少女ノ魔女裁判》）

游戏用 Naninovel 引擎、手上有它导出的本地化脚本（一个目录的 `*.bytes`，每条是 `# key`、`; 日文原文`、译文）时，用内置匹配器 `naninovel`。
剧本由自己从游戏里取得，不随本工具分发。

```powershell
flowocr-tracks out\v\v.jsonl --outdir out\v --tag v --matcher naninovel --matcher-ctx game=mocai
flowocr-match out\v\v-tracks.json --matcher naninovel --ctx script=<剧本目录>\Scripts --out out\v\v-matched.json
flowocr-render out\v\v-matched.json            # 匹配器推荐 matched_srt --opt lang=cn，不给 --preset 就用它
```

- 要改稿、出叠加：`flowocr-render out\v\v-matched.json --preset script --opt lang=cn` 写 `v-cn-main.script.ass`，之后同[改完出特效](script.md#改完出特效)。
- 只匹配主轨（字幕带）；选项、法庭辩论屏、Toast 这类不在字幕带的条目不参与。
- 建轨时的 `--matcher naninovel --matcher-ctx game=mocai` 是给《魔法少女ノ魔女裁判》的：主轨按时间窗判并带，字幕带被缝进别的区域的那几段也喂得到匹配器；别的 Naninovel 游戏不写 `game` 就是默认建轨。
  建轨时给的 `game` 记在轨道产物里，用同一个匹配器匹配时自动带上（匹配时 `--ctx game=` 给了以它为准）；1.0.0 之前建的轨道没记，匹配时加 `--ctx game=mocai`，或者重新建轨。
  它也让叠加用这款游戏的[版式文件](#译文怎么分行)。剧本按文件顺序走，跳着玩、回放、分支都能接上。
- `matched.json` 里每条认上的记录带剧本行的 `key`、原文 `jp`、译文 `cn` 和**说话人** `speaker` / `speaker_cn`（旁白是空）。
  说话人的名字从剧本目录上一级的 `CharacterNames.bytes` 查（在别处就加 `--ctx names=<文件>`）；名牌显示成 `？？？` 的台词，脚本里注记了真名的记真名。
  叠加里粘在台词上方的名牌也按这张表出中文名（见[名牌](#名牌)）。
- 记录的时间取字幕在画面上出现到消失；同一句隔了 2 秒以上再显示（比如选错证据之后）是两条记录，匹配时加 `--opt merge_gap=<秒>` 可改。
- 只想匹配一段：匹配时加 `--opt start=<秒> --opt end=<秒>`（和第 3 步 SRT 预设的 `start=full` 不是一回事）。

## 自己的原文

写一个匹配器（一个 Python 文件），见[扩展](reference.md#扩展匹配器输出预设与特效)。

## 画哪些（show / hide）

匹配器把要画的东西分成几个**组**（台词、名牌、选项按钮、界面文字……），每组定一个档位：

| 档位 | 意思 | 能怎么改 |
| --- | --- | --- |
| `always` | 一定画 | 关不掉 |
| `on` | 默认画 | `--opt hide=<组>` 关掉 |
| `off` | 默认不画 | `--opt show=<组>` 打开 |

内置匹配器的组：

| 匹配器 | 默认画 | 默认不画 |
| --- | --- | --- |
| `gametext` | `dialogue` 台词（一定画）、`name` 名牌（含称号）、`choice` 选项按钮、`offband` 字幕带外的台词（含绝区零的选项按钮、居中旁白）、`cutscene` 过场字幕、`narration` 黑屏旁白、`special` 特殊上屏形式的台词与提示 | `ui` 界面文字（技能说明、帖子、成就…）、`elsewhere` 别处重现的台词（对话记录面板等）、`stray_name` 没挂在台词上的名字、`panel` 阅读面板长文本、`unmatched`、`unfed` |
| `naninovel` | `dialogue` 台词（一定画）、`name` 名牌、`unmatched` | `stray_name` 没挂在台词上的名字、`unfed` |

- `show` / `hide` 都可以写几个，逗号隔开（`--opt show=ui,unmatched`）。同一个名字不能既 show 又 hide。
  写错组名会报错并列出这份产物有的组；有哪些组、各是什么档，也写在匹配产物的 `display` 里（每组带一个给人看的 `label`）。
- `show=all` 打开全部组和填位；`default_all` / `dev` 预设就是这样，再给 `hide` 照样能关掉其中几组。`hide=all` 不行。
- `fill` 指**填位**：匹配器认定这里该有一句、但识别没读到它的认领（比如没读出来的选项）。它不是组，是认领上的一个标记——填位的选项仍属于"选项"组，`hide=choice` 连它一起关；`hide=fill` 只关填位。两个内置匹配器的填位都默认画；自己写的匹配器在产物里声明各组和填位默认画不画（见[产物格式](../architecture/artifacts.md#1-一级产物只有一份)的显示协议一条）。
- 两个**每个匹配器都有**的组，打开时画的是识别出的原文：
  - `unmatched`：交给了匹配器、但没画成译文的字（没认上原文的，和认上了但没有所选语种译文的）；
  - `unfed`：没交给匹配器的字（别的区域、被当成常驻 UI 剔掉的等）。

  阅读面板画出来时，被它盖住的那段时间里这两组的字不再画。
- `special` 是游戏用特殊形式出在画面上的台词与提示：原神语音字幕里画面上方的横幅、战斗喊话、白字提示、教程式提示，绝区零的野外喊话、系统通知（`Tips`）和战斗提示横幅（`MiddleTips`）。
  它们在字幕稿里用单独的 `Special` 样式，在 Aegisub 里改这个样式就能整批改字号、颜色、位置，不要就 `--opt hide=special` 或按样式删掉；SRT 不出这一组（`show=special` / `show=all` 也不出）。
- 叠加里条目的样子（盖在正文行上、单独一块、名牌、阅读面板）由条目本身决定，和它属于哪个组无关；字幕稿的样式有两组例外：`stray_name` 用 `StrayName`，`special` 用 `Special`。

## 同一句出现在几处

同一句台词常同时出现在对话框和别的位置，或在对话记录面板里再出现一次，匹配时每一处都认上。
匹配产物给每次认领标一个形态（`matched.json` 记录上的 `echo`）：这一句最早被读到的那一次是"主"（不标），其余是
`same_time`（同一时刻别处的同一句）、`log`（对话记录这类面板里的旧台词）、`replay`（同一位置隔一阵又出现，剧情回放、选错重来）、`other`（剩下的，值得人看一眼）。
游戏文本包匹配器认得出的对话记录不当主；认不出的对话记录比正文先出现时，主会落在对话记录里那一次（判法见 [产物格式](../architecture/artifacts.md#1-一级产物只有一份)的回显判定一条）。

- SRT（`matched_srt`、`default_srt`）默认不出 `same_time` 和 `log`，一句一条；要屏上出现过的每一次，渲染时加 `--opt echo=keep`。
- 叠加 ASS 照旧每一处都画译文；同一句原文在画面上换了位置（跟着角色走的气泡、同时出现在对话框和对话记录里）时，按位置分段画，每段画在它自己的位置上，字在移动的那段空档不画。

## 名牌

名牌一块一块看：画面上它正下方同时有一句认上了的台词，才算这句台词的名牌、归 `name`；
队伍栏、好友列表、角色头顶、对话记录面板里那些长得像名牌的字归 `stray_name`，默认不画（要看就 `--opt show=stray_name`）。
台词没认上时，它上面的名牌也跟着不画。

- **样式**：挂在台词上、认出了中文名的用 `Name`，名牌下面字小一号的称号（原神的「佣兵」）用 `Title`；打开 `stray_name` 时那些字、以及没译出而退回画识别原文的名牌都用 `StrayName`。
- **颜色**：原神、星铁的名牌照游戏里的颜色画（原神名字金色、称号暗金，星铁米金），字幕稿的 `Name` / `Title` 样式就是这个颜色；
  匹配时读源视频，原文字色和这个色对不上的（原神「グライアイの眼」那种蓝底白字面板）改用 `PlainName` 样式、白字。字色按种量（同一个名字在画面同一处算一种，抽几帧），偶尔量错，那一种名牌整批画成白字。
  在 Aegisub 里改这几个样式的颜色就全改。
  匹配要找得到源视频（轨道产物里记的路径）和 ffmpeg；找不到、只有 `*-tracks.json`、或不是这两款游戏时名牌照旧白字（绝区零的名牌本来就是白字）。
- **译名**：先查剧本里的说话人；查不到的（名字隐藏的 NPC、绝区零这类行上没有说话人的场景）再按屏幕上的字查游戏自己的短名词表。
  识别把名字读错一个字、只读到半截时，按名牌下面那几句台词的说话人投票认，或借这段视频里已译出、只差一个字的名牌；证据不够的不认，这块名牌没有译文（Naninovel 默认画识别原文；游戏文本包默认不画，`--opt show=unmatched` 才画）。
  这几种来源偶尔会认错（任务目标、称号这类字也可能查到译名），`matched.json` 里名牌条目的 `name_src` 记着是哪一种（取值见 [产物格式](../architecture/artifacts.md#1-一级产物只有一份)的「名牌的译名从哪来」）。

## 译文怎么分行

叠加里的译文照剧本里的换行分，尽量画成目标语言版游戏里的样子：

- **折行**：匹配时从录像里量出这款游戏会按宽度自动折行（原神、星铁、绝区零的主对话框）的，译文一行超过那个宽度才折。量不出的（《魔法少女ノ魔女裁判》这类换行写死在剧本里的）不折。
- **摆行**：译文第几行就摆在原文第几行上。行数不同时，按对话框的对齐方式（上 / 下 / 居中）贴着原文那一整块排开，多出的行往下扩，底板盖住原文的每一行。
- **字号**：跟原文的字一样大。译文比原文宽时顺着对齐方向往外画、底板跟着放宽，到对话框宽为止（量不出对话框宽时到画面边上），再宽才整句缩小。
- **在字幕稿里改**：改了换行，第 4 步照你的换行画、不再自动折；一行太宽就照上一条缩字号。
- **短片段**：对话框的宽度、折行宽度、对齐方式要从录像里攒够样本才量得出。片段短、量不出的，按这款游戏随附的**版式文件**补（现有《魔法少女ノ魔女裁判》、原神、星铁、绝区零的主对话框）。
  匹配器按选中的游戏自动找：游戏文本包认剧本的游戏；Naninovel 用建轨时给的 `--matcher-ctx game=mocai`。
  不用就 `--ctx layout=none`；换成自己的版式文件加 `--ctx layout=<文件>`（格式见[产物格式](../architecture/artifacts.md#1-一级产物只有一份)的版式文件一条）。
  自己从整场的匹配产物导出版式文件要[从源码装](README.md#从源码装想改代码时)，用 `dev_tools\layout_export.py`；直接装的没有这个工具。
- **游戏画面不占满录像时**：游戏画面被缩放放在录像一角（主播布局、画中画）、带黑边、游戏长宽比不同的录像，匹配时按对话框的字号和位置推算游戏画面在哪，日志的「游戏画面」一行报推出来的位置；
  推错或推不出（对话框太少）时直接给游戏画面在录像里的位置（**像素**，左上角和宽高）：建轨时加 `--viewport x,y,w,h`，记进轨道产物，以后每次重跑匹配都自动带上；
  已经建好的轨道不想重建，匹配时加 `--ctx viewport=x,y,w,h`（两边都给了以匹配时的为准）。

## 剧本里的样式与 Ruby

- **样式**：原文里的粗体、斜体、下划线、删除线、字色（游戏用字色标重点）默认照搬进字幕稿和叠加 ASS（ASS 的行内标签）；SRT 是基础输出，一律纯文字。
  不要就渲染时加 `--opt style=off`（叠加预设和 `script` 都收）。字号、对齐不搬。剧本的字色照原色画：底板取色把正文改成深色字的地方，
  浅色的强调字可能不好认，要改就在字幕稿里改颜色或关掉样式。
- **Ruby**：原文里正文上方的小字（日文多是读音，中文多是注释，如星铁的「欧洛尼斯」上方注「岁月之泰坦」）默认也写进字幕稿，写成 `{ruby:岁月之泰坦}欧洛尼斯{/ruby}`：
  花括号里没有 `\`，预览里看不见、只见正文，Aegisub 里可以隐藏；改字时标记跟着字走，不要哪一处就把它前后两个标记删掉，全都不要就加 `--opt ruby=off`（和 `style` 各管各的）。
  叠加 ASS 里把它画在正文那几个字的上方（字号约正文的一半，太长的缩小，字色、淡入淡出、打字机跟着这一行，底板往上盖住它）；
  阅读面板、用 `\move` 挪过的整块不画。SRT 不带。
