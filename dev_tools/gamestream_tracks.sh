#!/usr/bin/env bash
# 游戏直播素材：obs jsonl -> 轨 + 两份体检，一条命令。
#
# 为什么单独写一个：切片那批是手敲跑的，五部整片再手敲一遍，迟早会出现
# "照着文档的复现命令跑，打出来的是另一张表"那类事故（docs/dev-guide/verification.md「追溯」）。
# 参数**一个都不调**——和 run_gamestream_ocr.sh 同一个理由：这一轮问的是
# "yuka 五部定下来的那套参数换到别的游戏还成不成立"，调参会把问题问没了。
#
#   bash dev_tools/gamestream_tracks.sh slices|full|all
#
# 只处理 `_meta.complete` 为真的产物——OCR 中途断掉的 jsonl 长得和跑完的一样
# （methodology-audit-4 报告 C7）。
set -u
# **代码在哪、产物在哪，是两件事**（照搬 finesub 的 paths.py）：
# `CODE` 是这个脚本所在的 checkout —— 在 worktree 里就是槽，改完直接跑的就是它；
# `cd` 过去的是**产物根**，worktree 会解析回主 checkout（那里才有 out/ tmp/ 素材）。
CODE=$(cd "$(dirname "$0")/.." && pwd)
PY="$CODE/.venv/Scripts/python.exe"   # 正式包的开发 venv（uv sync --extra nvidia --group dev）：跑的就是 $CODE/src
export PYTHONPATH="$(cd "$CODE" && (pwd -W 2>/dev/null || pwd))/src"   # 跑的一定是 $CODE/src：槽里的 .venv 若是指回主 checkout 的 junction，editable 安装会导回主 checkout 的代码
CODE_W=$(cd "$CODE" && pwd -W)        # 给 Windows 的 python.exe 的 sys.path 用（它不认 /c/… 这种路径，同 head2head.sh）
[ -x "$PY" ] || { echo "没有 $PY：先在 $CODE 里 uv sync --frozen --extra nvidia --group dev" >&2; exit 2; }
cd "$("$PY" -m flowocr.paths)"

SLICES="gi-s1 gi-s2 gi-s3 hsr-s1 hsr-s2 zzz-s1 zzz-s2 wuwa-s1 wuwa-s2"
# 整场只跑原神（owner 2026-09-08，理由见 run_gamestream_ocr.sh 里的注释）
FULL="gi1 gi2"
case "${1:-all}" in
  slices) TAGS="$SLICES";;
  full)   TAGS="$FULL";;
  all)    TAGS="$SLICES $FULL";;
  *) echo "用法：bash dev_tools/gamestream_tracks.sh slices|full|all"; exit 2;;
esac

FAILED=0
for tag in $TAGS; do
  obs="out/gamestream/$tag.jsonl"
  [ -s "$obs" ] || { echo "[skip] $obs 不在"; continue; }
  info=$("$PY" -c "
import json, sys
sys.path.insert(0, sys.argv[2])     # 这份 checkout 的 dev_tools/（驱动的当前目录是数据根，相对路径会读到主 checkout 的）
import ocr_progress as op
from pathlib import Path
p = Path(sys.argv[1])
m = json.loads(p.open(encoding='utf-8').readline()).get('_meta', {})
f = op._last_frame(p) or 0
# 别在这里四舍五入：`--hours` 是 条/h 的分母，0.167 和 0.16667 差出 2 条/h，
# 文档里的数就对不回来了（docs/dev-guide/verification.md「报数口径」：写进文档的每个数都要能数回来）。
print('ok' if m.get('complete') else 'partial', round(f / (m.get('src_fps') or 60) / 3600, 6))
" "$obs" "$CODE_W/dev_tools")
  state=${info%% *}; hrs=${info##* }
  if [ "$state" != ok ]; then
    echo "**[skip] $tag 的 OCR 没跑完**（_meta.complete 不为真，已到 ${hrs}h）——不参与统计"
    FAILED=$((FAILED+1)); continue
  fi
  echo
  echo "======== $tag（${hrs}h） ========"
  "$PY" -m flowocr.analyze.build_tracks "$obs" --outdir "out/gs-$tag" --tag "$tag" || {
    echo "**[fail] build_tracks $tag**"; FAILED=$((FAILED+1)); continue; }

  # 主轨走 provenance 里的 main_srt，**不许 glob 猜**（字典序事故见 docs/dev-guide/verification.md「追溯」）。
  main=$("$PY" -m flowocr.artifacts.tracksio "out/gs-$tag/$tag-tracks.json" main_srt)
  if [ -n "$main" ]; then
    echo "---- 用途 2 的轨体检：$main ----"
    # 体检只在出错时非零（报警照样返回 0）；出错要计进失败，别让驱动照样报成功（2026-09-24 整理复审）
    "$PY" "$CODE"/dev_tools/track_health.py "out/gs-$tag/$main" --hours "$hrs" \
      || { echo "**[fail] $tag track_health**"; FAILED=$((FAILED+1)); }
  else
    echo "---- **没有主轨**（build_tracks 一条都没接受）——用途 2 这部素材上是空的 ----"
  fi
  echo "---- 用途 1 的产物体检 ----"
  "$PY" "$CODE"/dev_tools/usage1_health.py "out/gs-$tag/$tag-tracks.json" --obs "$obs" \
    || { echo "**[fail] $tag usage1_health**"; FAILED=$((FAILED+1)); }
done
echo
echo "TRACKS DONE $(date '+%H:%M:%S')"
[ "$FAILED" = 0 ] || { echo "**$FAILED 处失败或没进统计**——上面的 [skip]/[fail] 行"; exit 1; }
