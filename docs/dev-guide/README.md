# 开发指南

怎么搭环境、跑管线、跑验证、在 worktree 里并行开发。报数和验证的规矩在 [验证与报数](verification.md)，
踩过的坑和它们的成立条件在 [已知的坑](pitfalls.md)，系统怎么分工在 [架构说明](../architecture/README.md)。

## 环境

一个 venv 装全（ONNX Runtime GPU + cv2 一份 + dev 工具），editable 安装当前 checkout；驱动脚本只认它。
要先有 [uv](https://docs.astral.sh/uv/)（它装 Python 3.12 和全部依赖）和 PATH 上的 ffmpeg / ffprobe（完整构建，见 [使用手册](../manual/README.md#系统要求)）。

```powershell
uv sync --frozen --extra nvidia --group dev     # 建 / 更新 .venv（锁是 uv.lock；CPU 机器用 --extra cpu）
```

- 推理只有 ONNX Runtime（2026-10 运行时去掉了 Paddle；默认路径从 09-25 起就不碰它）：ORT 1.29（CUDA 13.0），CUDA 运行库钉在 `pyproject.toml` 写的那一套（`.venv` 约 2 GB）。
  ORT 的 GPU 进程（`ort_server.py`）自己 `preload_dlls()`；CUDA 13 的 DLL 目录别加进 `PATH`：子进程继承了它，ORT 每个新形状首跑会从 0.15 s 变成 2 s。
- `uv sync` 默认不装 dev 组（`pyproject.toml` 的 `default-groups = []`），开发要显式 `--group dev`。
- `FLOWOCR_ORT_PYTHON=<别的 venv 的 python>` 把 ORT worker 钉到另一个解释器（换 ORT 版本做 A/B 时用）。
- ONNX 模型全用官方发布的（owner 2026-09-22）：`flowocr.models` 按钉死的提交号 + sha256 取到数据根的 `models/`，
  缺了管线当场取，也可以 `python -m flowocr.models fetch`。许可与改动说明在 `LICENSES/`。
- 游戏文本包（`gtd-bundle/1`）默认在数据根的 `gametext/`；没有这个目录、又把 game-text-data 克隆在 flowocr 旁边时，`explore/env.sh` / `env.ps1` 把 `FLOWOCR_GAMETEXT` 指到
  它的 `out/`（那边 `python -m <pkg>.cli bundle` 出的包）。不 source env 直接跑 `gamescript` 就得自己设或给 `--gametext`。

### 探索候选的隔离环境

每个候选在 `explore/` 下占一个目录，自带 venv，缓存走 `explore/env.ps1`，目标是"删掉那个目录和 explore 的 `_cache/` 就等于没装过"：

```powershell
. .\explore\env.ps1            # 把 HF_HOME / TORCH_HOME / UV_CACHE_DIR 等指到 explore 的 _cache 目录（要 PaddleX 的实验自己设 PADDLE_PDX_CACHE_HOME）
uv venv explore\<name>\.venv   # 每个候选独立 venv，卸载 = 删目录
python dev_tools\residue_scan.py snapshot --label before-<name>    # 装之前、装之后各拍一张，再 diff
python dev_tools\residue_scan.py diff --before before-<name> --after after-<name>
```

做不到"删目录即干净"的候选，要在文档里写清它在目录外留下了什么、怎么删。

## 常用命令

四个阶段各有入口（9 个 console script 见 `pyproject.toml` 的 `[project.scripts]`）：

```powershell
# 阶段 1：视频 -> obs
.venv\Scripts\python.exe -m flowocr.extract.run_ocr2 <video> --out <obs.jsonl>          # 或 flowocr-ocr
.venv\Scripts\python.exe -m flowocr.extract.run_groups ...                                # 自选范围多组同时跑（见使用手册「只识别画面的一部分」）
# 阶段 2：obs -> tracks.json（一级产物）；匹配器
.venv\Scripts\python.exe -m flowocr.analyze.build_tracks <obs.jsonl> --outdir <dir> --tag <tag>
.venv\Scripts\python.exe -m flowocr.analyze.match <tracks.json> --matcher gametext --ctx ref=<ref.json> --out <matched.json>
# 阶段 3：出字幕 / 字幕稿（见 docs/architecture/artifacts.md 的导出与字幕稿两节）
.venv\Scripts\python.exe -m flowocr.output.render <matched.json> --preset matched_srt --opt lang=cn
.venv\Scripts\python.exe -m flowocr.output.render <tracks.json> --preset script --opt keep=all --outdir <dir>   # 只写字幕稿
.venv\Scripts\python.exe -m flowocr.output.export <tracks.json> --outdir <dir>                # 逐轨 SRT
python -m flowocr.artifacts.tracksio <tracks.json> main_srt    # 从一级产物取值，别自己 json.load
# 阶段 4：字幕稿 -> 最终叠加 ASS（可以反复重跑；--restore 还原字幕稿）
.venv\Scripts\python.exe -m flowocr.typeset <x.script.ass> [--preset dev] [--fx <扩展效果>]
# 阶段 3 + 4 连跑：叠加预设 default / default_all / dev 两份都写（字幕稿 + 最终 ASS）
.venv\Scripts\python.exe -m flowocr.output.render <tracks.json> --preset dev --outdir <dir>   # 不给 --preset 是 default
```

用户扩展（匹配器 / 输出预设 / 阶段 4 的扩展效果）的入口形状见 `src/flowocr/extensions.py` 文件头，可复制的例子在 `examples/`；内置实现和用户代码走同一条路。

### 驱动与验证工具（`dev_tools/`）

`dev_tools/` 放还在维护的工具；一次性探针放在 `explore/` 下（不公开）。
共享层守卫的入口是 `tests/test_guards.py`，守卫本身按领域分在 `tests/guards/`（`extract` / `analyze` / `gametext` / `output` / `infra`，共用的 `check` / `raises` / 合成样本在 `_common.py`）；
新守卫写成对应模块里的 `t_*` 函数就会被跑到，不用登记。
多数评测驱动要本地的素材与真值（`data/`，不进版本库；素材根由 `FLOWOCR_ARCHIVE` 或 `data/archive-root.txt` 给），公开仓库里拿不到，照着它们的写法换成自己的素材即可。

```bash
.venv/Scripts/python.exe tests/test_guards.py       # 共享层守卫，改完代码先跑（秒级）；用 venv，系统 python 会带出依赖版本的假报错
bash dev_tools/sampleset.sh list|quick|evidence|holdout2|zh   # 代表性片段；跑之前先打总时长
bash dev_tools/head2head.sh 1 2 3 4 5 && python dev_tools/h2h_report.py tmp/h2h      # 五部整片头对头（非必要不跑）
bash dev_tools/gametext_eval.sh && python dev_tools/gametext_report.py               # 游戏直播的用途 2 尺（文本包：数据根的 gametext/，或旁边克隆的 game-text-data 的 out/）
TAG=<臂名> B_ARGS="<旋钮>" bash dev_tools/ocr_ab.sh quick && python dev_tools/ocr_ab_report.py tmp/ab/<臂名>   # OCR 这一级的 A/B（交错驱动，报墙钟 + 整棵进程树 CPU 用它）
python dev_tools/jobcpu.py <旁注.cpu.json> -- <命令…>   # 单跑一条命令，记它连同子孙进程的 CPU 秒和提交内存峰值（Windows Job 记账）
bash dev_tools/ui_share_ab.sh                        # 常驻 UI 剔除的一旋钮 A/B（11 段异构切片，几十秒）
python dev_tools/track_health.py <轨.srt> --hours <h>   # 轨的体检，只看产物；改完 build_tracks 先跑
python dev_tools/usage1_health.py <tracks.json> --obs <obs.jsonl>   # 用途 1 的产物体检
python dev_tools/typewriter_eval.py <tracks.json> data/gt/typewriter-<tag>.json --split validation   # 默认建轨已回抠；--refine off 的产物加 --sampled
python dev_tools/refine_offline_probe.py <--refine off 的 tracks.json> --video <mp4>   # 回抠的在线证据 vs 开视频离线测量，逐事件比三个时刻（不写产物）
python -m flowocr.analyze.slot_pairs score data/gt/slotpairs-*.json --split dev    # 聚类的尺；改聚类先跑
python dev_tools/hit_delta.py 1 2 3 4 5 --b=<臂后缀>  # 跨臂的命中集合差（按三档拆）+ 重复认领
python dev_tools/missed_trace.py <matched.json> [--vs <另一臂 matched.json>] --out <x.json>   # 匹配后的漏行逐条追查：缺口、时间窗、另一版本、丢在哪一层
python dev_tools/tracks_identical.py <目录A> <目录B>  # 两份建轨产物是否相同（先剥 provenance；先做 A/A）
python dev_tools/timeline_report.py <路径>           # run_ocr2 --timeline 的各级时间线
python dev_tools/ocr_progress.py <obs.jsonl>          # 长任务进度：读产物尾部，不读日志
python dev_tools/publish.py check                   # 公开树的核对：剥离私有路径后查私有引用与死链（配置 .publish.toml，不写任何东西）
python dev_tools/pyrun.py dev_tools/public_tree.py [<提交>]   # 把提交剥离成公开 main 会有的树（tmp/public-tree/），在里面跑守卫与文档检查
python dev_tools/preview.py <tracks.json|matched.json> --preset dev [--opt lang=cn] [--start <秒>] [--audio <音轨> --audio-offset <秒>] --out <mp4>
                                                    # 叠加字幕的预览视频：跑预设出 ASS、烧进原视频的一段（默认 120 s），可混入另下的音轨；只用 CPU
                                                    # 叠加预设烧最终 ASS；--preset script 烧字幕稿本身（Aegisub 里打开的样子）
                                                    # --mode ass 改出不烧字的 mkv、ASS 作字幕轨；产物没回抠（--refine off / 旧 obs）会提示
```

更多：
- 事件级对账 `dev_tools/arm_events.py`（IoU≥0.7 + 时间重叠，A/A、B/B 底噪 0）；ORT 臂的观测对账用 `dev_tools/obs_textdiff.py` / `dev_tools/rec_engine_accept.py`；逐字节对账用 `dev_tools/obs_identical.py`。
- 复用判据的因果轨迹：`run_ocr2 --trace` 产出，`dev_tools/rec_window_replay.py` 重放（a1-mask-reuse 实验里的 `rec_window_sim.py` 把窗长高估了一倍多，别拿它的数）。
- 解码契约测试：`dev_tools/aux_identity_check.py`（同号同画面）、`dev_tools/hw_retry_check.py`（重试前整棵进程树退干净）。
- 某一处漏在哪一层：归因用 `dev_tools/probe_gap_locate.py`（位置判据），匹配器的配对导出用 `dev_tools/match_probe.py`。
- 上屏事件真值：`dev_tools/sample_events.py <tracks.json> --video <mp4> --n 8 --seed 1 --outdir tmp/gt/<tag>`（人标或模型看图标都算真值，每帧记 `labeled_by`；owner 2026-09-08）。
- 名牌判据的精确 / 召回：`dev_tools/probe_np_score.py`（只读 tracks.json，不重跑 build_tracks；口径见 ui-gate 计划）。
  召回的分母含主轨里的名字 run；`--exclude-main` 是另一种口径，两种不能混着引。`--sweep-cover 0,.3,.5,.7` 看阈值坐不坐在平台上。
- 文字怎么动：`dev_tools/probe_motion.py <obs.jsonl> --out <tsv>`。
- CI（`.github/workflows/ci.yml`）除守卫与文档检查外：`dev_tools/ci_smoke.py`（生成一段带字幕的视频走完三步）；`uv build` 打 sdist 与 wheel 并 `twine check`；`dev_tools/ci_extensions.py`（在 wheel 装进的全新 venv 里，
  用装好的命令加载 `examples/` 的匹配器和预设；要非 editable 的环境，本地验证可以 `uv build --wheel` 之后 `--no-deps` 装进临时 venv）。
- 发版（`.github/workflows/release.yml`，手动派发）：从 `main` 或 tag 构建，在全新 venv 里装 wheel 跑冒烟、装模型包离线跑第 1 步，再按 target 传 TestPyPI，或建 GitHub Release 后传 PyPI。用法见文件头。

各工具的用法在它自己的文件头。

## 在 worktree 里并行开发

日常开发在 `dev` 上（公开的 `main` 只由 `dev_tools/publish.py` 生成）；并行工作各开一个 worktree。

- 开槽：`git worktree add .worktrees/<slot> -b <branch>`。槽位嵌在仓库里，`.worktrees/` 已 ignore，主 checkout 看不见槽里的树。
  `git clean -xdff` 会删掉它们，单 `-f` 会跳过。
- 槽里没有被 ignore 的目录：`out/`、`tmp/`、explore 下各实验的 `.venv`、整个 `data/`（样本清单、真值也在内）。所以：
  - 读 `data/` 一律走 `data_root()`；按代码根拼出来的路径在槽里不存在。
  - 模型缓存同理：`explore/env.sh` / `env.ps1` 把 explore 的 `_cache/` 指到数据根。
  - 代码在哪、产物在哪是两件事，判据只有一份：`src/flowocr/paths.py`。`CODE_ROOT` 是这份代码所在的 checkout；`data_root()` 在 linked worktree 里解析回主 checkout（读 `.git` 文件的 `gitdir:`）。`FLOWOCR_DATA_ROOT` 可以覆盖产物根。
  - 驱动脚本一律 `CODE=$(cd "$(dirname "$0")/.." && pwd)` + `PY="$CODE/.venv/Scripts/python.exe"` + `cd "$("$PY" -m flowocr.paths)"`：
    槽里改完就能直接跑整片，跑的是槽里的代码、读写的是主 checkout 的素材与产物，provenance 的 `git_head` 仍是槽自己那棵树。
    驱动紧接着 `export PYTHONPATH="$CODE/src"`（`PYTHONPATH` 排在 editable 安装前面）：槽里的 `.venv` 若是指回主 checkout 的 junction，
    editable 安装会导回主 checkout 的代码，跑出来的不是槽里的改动（守卫查每个驱动都钉了）。槽里没有 `.venv` 时，用主 checkout 的 `.venv` 加 `PYTHONPATH=<槽>/src`。
    手敲命令用 `python dev_tools/pyrun.py <参数>` 可以省掉这两步：它挑解释器（本 checkout 的 `.venv`，没有就用主 checkout 的）并把本 checkout 的 `src` 排在 `PYTHONPATH` 最前；`--which` 只打印会用哪个。
- `git worktree remove` 之前先分诊槽里被 ignore 的文件（`git -C <slot> clean -xdn`）：未提交、未合并的改动 git 会挡住，被 ignore 的文件它一声不响就删。
- 槽从本地 HEAD 分叉（维护者的 Claude Code 项目设置里 `worktree.baseRef: "head"`，不在公开仓库里）。
- 每次提交后报告当前分支和 commit hash。

### 只适用于 Claude Code

- 暂不用 `EnterWorktree` / `ExitWorktree`（它的隔离守卫会拒掉大部分复合命令；守卫放宽后改回 `path` 形式）：会话留在主 checkout，用上面的 `git worktree add` 开槽，合并回 `dev` 就在主 checkout 里 `git merge <分支>`。
  由于你实际位于主 checkout，因此请务必注意环境的区别：相对路径、Bash 的 cwd、Grep / Glob 的默认搜索范围和自动加载的入口文件，都是主 checkout 的。
  - 读改槽里的文件用槽内绝对路径；搜索给 `path=<槽>`。不给时，Grep 只搜主 checkout（`.worktrees/` 被 `.gitignore` 挡着，槽里的文件搜不到）；Glob 则把主 checkout 和槽里的同名文件一起列出来。两种情况下都别把主 checkout 的文件当成槽里的。
  - git 用 `git -C <槽>`。
  - 要在槽里跑的命令写成 `(cd <槽> && …)`，跑 Python 就是 `(cd <槽> && python dev_tools/pyrun.py …)`：pyrun 按自己所在的 checkout 挑代码，`cd` 让脚本和数据的相对路径也按槽解析。括号让 `cd` 只在这一条命令里生效；不带括号时，harness 会把会话的主工作目录切进槽，之后删槽可能报 Permission denied。

## 长任务的纪律

长任务 = 整片 OCR、头对头、多段样本集这类跑几十分钟以上的批。

- **跑着的时候不改 `dev_tools/`、`explore/`，也不提交**（连文档提交都不行）。provenance 的 `git_head` 记的是 HEAD，
  中途提交会让一批产物出现两个 `git_head`；中途改文件会让后半批带 `+dirty`，`h2h_report` 会拒收。要改就等它跑完，或改完整体重跑。
- **不改正在运行的 shell 脚本**。bash 边读边执行，改到一半的脚本会从错位的偏移继续读，退出码和日志都会骗人。
- **判断活没活看产物，不看日志**。管道里的 `grep` / `sed` / `awk` 写文件时是 4 KB 块缓冲，进度行攒不满一块就不落盘；
  驱动里一律加 `--line-buffered`。查进度用 `dev_tools/ocr_progress.py`（读产物尾部的 `frame` 和 mtime）。
  读正在被写的文件大小用 bash 的 `stat`，PowerShell 的目录枚举报过 0 字节。
- 合回 `dev` 之前先确认主 checkout 上没有长任务在跑，理由同第一条。
