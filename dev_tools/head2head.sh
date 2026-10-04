#!/usr/bin/env bash
# yuka input{N} 的整片头对头：flowocr 主轨（全自动） vs 望言+人手框+老算法。
#
# 存在的理由（methodology-audit 报告建议 3）：旗舰结论"整片上赢基线"此前只有
# input5 一部（n=1），而 input1-4 的 OCR 与望言基线其实早就齐了。一部片子给不出
# **素材间的波动范围**，而波动范围才决定那 +0.5 个点算不算数。
#
# 口径（和 script-corpus 报告的 input5 那节逐字一致，改了就不可比）：
#   * 基线用**随素材发来的** lower{N}_jp.ass，不重跑匹配器——那是"交付态"的老算法输出。
#   * 剧本区间由基线圈出（--span-ref lower{N}_jp.ass），两边同一个分母。
#     这对我们是**不利**的：区间来自基线匹配上的地方。保持不变，因为要和 input5 可比。
#   * 我们这侧要过一遍同一个 match_ref.py，否则比的是"匹配前 vs 匹配后"。
#   * --raw-ocr 给主轨的原始 SRT，把疑似真漏拆成"匹配器丢的"和"OCR 丢的"。
#
# 用法：  bash dev_tools/head2head.sh 1 2 3 4
#
# **缓存按 mtime 失效，不按"文件在不在"**（methodology-audit-2 报告）：
# 上一版只看 `[ ! -f build.log ]`，于是 09-06 那一夜改了三次 build_tracks、
# 重跑这个脚本却**什么都没重建**，最终结果只好落到临时目录，
# 而规范目录留着改动前的产物——照着文档复现，打出来的是另一张表。
# 要强制重来：`FORCE=1 bash dev_tools/head2head.sh 1 2 3 4`
#
# **一旋钮全流程 A/B**（methodology-audit-2 报告 §3.3）：层级探针只能量代理量
# （名牌纯度），要看命中/漏条得跑完整条链。两个环境变量就够：
#   FORCE=1 SUFFIX=-sr15 BUILD_ARGS="--slot-span-ratio 1.5" bash dev_tools/head2head.sh 1 2 3 4 5
# `SUFFIX` 同时改产物目录、匹配器 tag 和日志前缀，**A/B 两侧因此不会互相覆盖**——
# 上一次没有它，A/B 只好写进临时目录，规范目录留着改动前的产物（见下面那段缓存说明）。
# 两个都不设时，这个脚本和以前逐字节等价。
#
# `pipefail` 也不是可选的：第 3 步是 `python ... | tee | tail`，
# 没有它，python 崩了退出码也是 tail 的 0。
set -uo pipefail
FAILED=0
FORCE=${FORCE:-0}
SUFFIX=${SUFFIX:-}          # A/B 用：产物目录 / 匹配 tag / 日志前缀都带上它
BUILD_ARGS=${BUILD_ARGS:-}  # A/B 用：额外传给 build_tracks 的旋钮
# A/B 用：额外传给 merge_nameplate 的旋钮（要 MERGE_NAMEPLATE=1）。
# ⚠ **`--np-source` 显式写死在这里**（2026-09-20 复审第 2 条）：名牌来源 09-20 换成了
#   `build_tracks` 打标投影的那条名牌轨（判据 flowocr.analyze.uigate），而**这条臂的用途 2 头对头
#   还没跑**。臂的行为要写在驱动里、不跟默认值漂——"默认一翻，空参数就塌成另一条臂"
#   这个坑这个项目吃过（ocr_ab.sh 那次）。要跑旧来源：`MERGE_ARGS="--np-source geom"`。
MERGE_ARGS=${MERGE_ARGS:-}
# ⚠ **单独一个变量、永远拼进命令**（2026-09-20 复审第 2 条）：写成 `MERGE_ARGS` 的默认值时，
#   使用者为了别的旋钮传一个 `MERGE_ARGS="--paste"`，`--np-source` 就不见了、
#   名牌来源又跟着工具默认值漂。这样写 provenance 的 argv 里也永远看得到它。
NP_SOURCE=${NP_SOURCE:-track}

# 产物在、没要求强制重建；代码变没变由 same_build_args 按指纹判（build_tracks.cache_stale）
have_build() {   # $1 = 产物
  [ -s "$1" ] || return 1
  [ "$FORCE" = 1 ] && return 1
  return 0
}

# **旋钮变了也要重建**（methodology-audit-3 报告 §4）：上一版的缓存只看
# `build_tracks.py` 的 mtime，于是同一个 SUFFIX 下换掉 BUILD_ARGS 再跑，
# **产物照旧复用、旋钮被静默忽略**，而日志和表照常打印——和 09-06 那次
# "照着复现命令跑打出另一张表"是同一个形状，只是从改代码搬到了改参数。
# provenance 里的 argv 记的是当时命令行上真正打了什么；`tracksio.build_args` 按名剥掉
# obs / --outdir / --tag，剩下的就是 BUILD_ARGS（以前写死 argv[5:]，audit-6 §2.4）。
# **旧产物没有 argv 字段**（这个字段是本次加的），所以证明不了它是用什么参数建的
# ——一律重建。第一次跑到这里会把五部重建一遍，之后就都带 argv 了。
# 确实不想重建（比如只想看报表）用 `ARGS_CHECK=0`，但那是**明知故犯**，别写进文档。
same_build_args() {   # $1 = tracks.json，$2 = 这一趟的 BUILD_ARGS
  [ "${ARGS_CHECK:-1}" = 0 ] && return 0
  "$PY" -c "
import json, sys
from flowocr.artifacts import tracksio
try:
    prov = tracksio.load(sys.argv[1])['provenance']
except Exception as exc:
    print(f'  [rebuild] 读不到 provenance（{exc}），证明不了参数一致'); sys.exit(1)
if 'argv' not in prov:
    print('  [rebuild] provenance 里没有 argv（这份产物早于该字段），参数无从核对')
    sys.exit(1)
got, want = ' '.join(tracksio.build_args(prov)), ' '.join(sys.argv[2].split())
if got != want:
    print(f'  [rebuild] BUILD_ARGS 变了：产物是 [{got}]，这次要 [{want}]')
    sys.exit(1)
# 代码指纹（建轨的表 + 聚类路径 + 回抠结算）：只比 build_tracks.py 的 mtime 会漏掉放在别的模块里的默认行为
from flowocr.analyze import build_tracks as bt
why = bt.cache_stale(tracksio.load(sys.argv[1]))
if why:
    print(f'  [rebuild] {why}')
    sys.exit(1)" "$1" "$2"
}
# **代码在哪、产物在哪，是两件事**（照搬 finesub 的 paths.py）：
# `CODE` 是这个脚本所在的 checkout —— 在 worktree 里就是槽，改完直接跑的就是它；
# `cd` 过去的是**产物根**，worktree 会解析回主 checkout（那里才有 out/ tmp/ 素材）。
CODE=$(cd "$(dirname "$0")/.." && pwd)
PY="$CODE/.venv/Scripts/python.exe"   # 正式包的开发 venv（uv sync --extra nvidia --group dev）：跑的就是 $CODE/src
export PYTHONPATH="$(cd "$CODE" && (pwd -W 2>/dev/null || pwd))/src"   # 跑的一定是 $CODE/src：槽里的 .venv 若是指回主 checkout 的 junction，editable 安装会导回主 checkout 的代码
[ -x "$PY" ] || { echo "没有 $PY：先在 $CODE 里 uv sync --frozen --extra nvidia --group dev" >&2; exit 2; }
cd "$("$PY" -m flowocr.paths)"
# `pwd -W` 而不是 `pwd`：Git Bash 的 `pwd` 给的是 /c/Users/… ，
# 而 sys.path 是给 **Windows 的 python.exe** 用的，它不认这种路径。
# 第一版就是这么写的，四部片子全在 `ModuleNotFoundError: No module named 'match_ref'`
# 上失败，而 build_tracks 那一半正常跑完了——**一半成功一半失败最容易看漏**。
# 要的是**代码根**：`match_ref.py` 在 `dev_tools/reference/`，那是跟踪的代码
# （`git_head` 的 `+dirty` 也盯着它），不是产物。
CODE_W=$(cd "$CODE" && pwd -W)
SCRIPT_RAW=data/corpus/mocai/script-raw/Scripts
SCRIPT_JP=data/corpus/mocai/script-jp/Scripts
GT=data/corpus/mocai/game_text
mkdir -p tmp/h2h

# **「默认」是会变的**（audit-5 §2）：改默认那次把 A/B 的基准臂直接覆盖掉了，
# 三处文档里引的数从产物里数不回来。所以 SUFFIX 为空时，把当前默认值的指纹
# 打进日志、并留一份 tmp/h2h/defaults.txt——至少"默认换了"是可见的。
# 要留旧默认做对照，就把它冻成一条**命名臂**，别指望"默认"还是上次那个：
#   FORCE=1 SUFFIX=-band0 BUILD_ARGS="--main-band 0" bash dev_tools/head2head.sh 1 2 3 4 5
if [ -z "$SUFFIX" ]; then
  echo "默认臂（SUFFIX 为空）——报数时写明是**哪两条臂**算的，不要写「默认 vs X」"
  "$PY" -m flowocr.analyze.build_tracks --print-defaults | tee tmp/h2h/defaults.txt
fi

# **换 OCR 产物的 A/B 用这个**（2026-09-08 加）：`OBS_TAG=-bs8` 会去读
# `out/yuka/i{N}-full-bs8.jsonl`。以前 OBS 写死，于是"换一版 OCR 再比命中"
# 这类实验只能手敲 build->match->align——而这条链的口径就是靠这个脚本锁住的，
# 手敲等于放弃口径。**换 OBS 一定要同时给 SUFFIX**，否则两臂的产物互相覆盖。
OBS_TAG=${OBS_TAG:-}
if [ -n "$OBS_TAG" ] && [ -z "$SUFFIX" ]; then
  echo "**换了 OBS_TAG 却没给 SUFFIX**——两臂会写进同一个目录，互相覆盖。停。"
  exit 2
fi
# **换喂法**（2026-09-26）：`FEED=nonoise` 把全部非噪音区域轨按时间交错投影成一份 SRT 喂参照匹配器（`dev_tools/feed_srt.py`，
# 和 `scriptmatch --feed nonoise` 同一个投影），量的就是产物实际的喂法；不给就是主轨（原口径）。同样要给 SUFFIX
FEED=${FEED:-}
if [ -n "$FEED" ] && [ -z "$SUFFIX" ]; then
  echo "**换了 FEED 却没给 SUFFIX**——两种喂法会写进同一份匹配产物。停。"
  exit 2
fi
if [ -n "$FEED" ] && [ "${MERGE_NAMEPLATE:-0}" = 1 ]; then
  # 全部区域轨里已经有名牌区域，再并一遍名牌就是喂两遍（重复认领虚涨）；名牌并进主轨只对主轨口径有意义
  echo "**FEED 和 MERGE_NAMEPLATE=1 不能一起开**——区域轨里本来就有名牌，再并就喂了两遍。停。"
  exit 2
fi

for N in "$@"; do
  OBS=out/yuka/i${N}-full${OBS_TAG}.jsonl
  [ -s "$OBS" ] || { echo "[skip] $OBS 不存在"; continue; }
  LOG=tmp/h2h/f${N}${SUFFIX}
  OUTDIR=out/yuka-f${N}${SUFFIX}
  echo "=================  input${N}  $(date '+%H:%M:%S')  ================="

  # ---- 1. 聚类 ----
  if ! have_build "${LOG}-build.log" \
     || ! same_build_args "$OUTDIR/f${N}-tracks.json" "$BUILD_ARGS"; then
    "$PY" -m flowocr.analyze.build_tracks "$OBS" --outdir "$OUTDIR" --tag "f${N}" $BUILD_ARGS \
      > "${LOG}-build.log" 2>&1 \
      || { echo "[fail] build_tracks input${N}"; FAILED=$((FAILED+1)); continue; }
  fi
  # 产物版本抄一份到 tmp/h2h/，h2h_report 会校验五片是不是同一版代码产的
  # （provenance 现在住在 tracks.json 里，抄出来的仍是同样的 `-prov.json`）
  "$PY" -m flowocr.artifacts.tracksio "$OUTDIR/f${N}-tracks.json" > "${LOG}-prov.json" || true
  grep -E '常驻 UI 剔除' "${LOG}-build.log" || true
  tail -n +1 "${LOG}-build.log" | grep -E 'obs ->|←主轨'
  # **主轨文件名从 provenance 读，不 glob 猜。**（methodology-audit-2 报告 §3.4）
  # 上一版是 `ls "$OUTDIR/f4-region01-"*.srt | head -1`：label 一改就会留下同 index
  # 的旧文件，而字典序 `static-overlay` < `subtitle`，于是**整部 input4 的基线
  # 是拿一份隔夜的坏产物（17,748 条，gap_frames 修好之前的）量的，一行报错都没有**。
  SRT="$OUTDIR/$("$PY" -m flowocr.artifacts.tracksio "$OUTDIR/f${N}-tracks.json" main_srt)"
  [ -f "$SRT" ] || { echo "[fail] input${N} 主轨 SRT 找不到（provenance 里的 main_srt=$SRT）";
                     FAILED=$((FAILED+1)); continue; }
  echo "主轨: $SRT  $(grep -c -- '-->' "$SRT") 条"
  if [ -n "$FEED" ]; then
    "$PY" "$CODE/dev_tools/feed_srt.py" "$OUTDIR/f${N}-tracks.json" --feed "$FEED" --out "$OUTDIR/f${N}-$FEED.srt" \
      | tee "${LOG}-feed.log" || { echo "[fail] feed_srt input${N}"; FAILED=$((FAILED+1)); continue; }
    SRT="$OUTDIR/f${N}-$FEED.srt"
  fi
  # MERGE_NAMEPLATE=1：把几何判据挑出来的名牌区域并进主轨再送匹配器
  # （methodology-audit-2 报告 §3.5；owner 说的"上下文 momentum"那条路）
  if [ "${MERGE_NAMEPLATE:-0}" = 1 ]; then
    # **这一步的输出要落盘。** 它决定了送进匹配器的是什么（挑中哪几个区域、
    # 有没有打出"没挑中任何名牌区域"），以前只在终端上闪过，
    # `tmp/h2h/` 里一个字都不留（methodology-audit-3 报告 §5）。
    "$PY" -m flowocr.analyze.merge_nameplate "$OUTDIR/f${N}-tracks.json" \
      --np-source "$NP_SOURCE" $MERGE_ARGS \
      --out "$OUTDIR/f${N}-merged.srt" 2>&1 | tee "${LOG}-merge.log" \
      || { echo "[fail] merge_nameplate input${N}"; FAILED=$((FAILED+1)); continue; }
    cp -f "$OUTDIR/f${N}-nameplate.json" "${LOG}-nameplate.json" 2>/dev/null || true
    SRT="$OUTDIR/f${N}-merged.srt"
  fi

  # ---- 2. 我们这侧过匹配器 ----
  TAG="ours${N}${SUFFIX}"
  # 主轨比匹配结果新（或 FORCE）就重跑匹配器——否则会拿旧 ass 去量新轨
  if [ ! -s "tmp/match/${TAG}_jp.ass" ] || [ "$SRT" -nt "tmp/match/${TAG}_jp.ass" ] \
     || [ "$FORCE" = 1 ]; then
    cp "$SRT" "tmp/match/${TAG}.srt"
    # **旧产物必须先删**（methodology-audit-4 报告 §4）：`match_ref.main()` 自己
    # except 掉一切并正常返回，而下面只检查"ass 在不在"——于是**这次失败、上次的 ass
    # 还在**，就会被当成本次成果量进表里。先删再跑，存在即新鲜。
    rm -f "tmp/match/${TAG}_jp.ass" "tmp/match/${TAG}_cn.ass"
    ( cd tmp/match && "$PY" -c "
import sys; sys.path.insert(0, r'${CODE_W}/dev_tools/reference')
import match_ref; match_ref.main('${TAG}', 'dialogue')" ) > "${LOG}-match.log" 2>&1 \
      || { echo "[fail] match_ref input${N}"; FAILED=$((FAILED+1)); continue; }
    [ -s "tmp/match/${TAG}_jp.ass" ] \
      || { echo "[fail] match_ref input${N} 没产出 ass（看 ${LOG}-match.log）";
           FAILED=$((FAILED+1)); continue; }
  fi
  echo "匹配后: $(grep -c '^Dialogue:' "tmp/match/${TAG}_jp.ass") 条"\
       " / 基线 $(grep -c '^Dialogue:' "${GT}/lower${N}_jp.ass") 条"

  # ---- 3. 同一把尺量两边 ----
  for side in ours base; do
    if [ "$side" = ours ]; then SUBS="tmp/match/${TAG}_jp.ass"; RAW="$SRT"
    else SUBS="${GT}/lower${N}_jp.ass"; RAW="${GT}/lower${N}.srt"; fi
    echo "--- input${N} / ${side} ---"
    # **管道的返回值要自己接**（methodology-audit-4 报告 §4）：`pipefail` 只让
    # `$?` 反映管道里最靠后的失败，而这里用的是 `|| { }` 之外的写法，
    # 退出码根本没人看；随后只检查"JSON 非空"，**上一次的旧 JSON 会顶替本次成绩**。
    rm -f "${LOG}-${side}.json"
    "$PY" -m flowocr.analyze.script_align --script "$SCRIPT_RAW" --text "$SCRIPT_JP" \
      --subs "$SUBS" --span-ref "${GT}/lower${N}_jp.ass" --raw-ocr "$RAW" \
      --out "${LOG}-${side}.json" 2>&1 | tee "${LOG}-${side}.log" | tail -25
    rc=${PIPESTATUS[0]}
    [ "$rc" = 0 ] || { echo "**[fail] script_align input${N}/${side} 退出码 $rc**";
                       FAILED=$((FAILED+1)); }
    [ -s "${LOG}-${side}.json" ] \
      || { echo "[fail] script_align input${N}/${side} 没产出 JSON"; FAILED=$((FAILED+1)); }
  done
done
echo "=================  全部完成 $(date '+%H:%M:%S')  ================="
# **失败要非零退出。** 上一版全片失败也返回 0，而"一半成功一半失败最容易看漏"
# 这件事这个脚本自己就踩过一次（见上面 `pwd -W` 那段注释）。
[ "$FAILED" = 0 ] || { echo "**$FAILED 步失败**——上面的 [fail] 行"; exit 1; }
echo "报数：python dev_tools/h2h_report.py tmp/h2h"
