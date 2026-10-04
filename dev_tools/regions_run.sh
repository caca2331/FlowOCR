#!/usr/bin/env bash
# 自选 OCR 范围：配置文件里**每个组一条独立 OCR 线**（ocr-regions 计划 §3），各跑一遍提取 + 建轨，产物按组名分目录。
#
#   bash dev_tools/regions_run.sh <视频> <regions.json> <输出目录> [起秒 止秒]
#
# 产物：<输出目录>/<组名>.jsonl（obs，`_meta.regions` 带这组的规格与时间切点）、<输出目录>/<组名>/<组名>-tracks.json。
# 提取走 `python -m flowocr.extract.run_groups`：各组**同时**跑、共用一个 ORT 服务（SERIAL=1 逐组串行，对照臂）。
# 额外旋钮：OCR_ARGS（原样传给 run_ocr2）、BUILD_ARGS（原样传给 build_tracks）、
# GAME（genshin / starrail / zzz：指定了游戏，各组建轨就套这款游戏的补丁——owner 2026-09-24"指定了游戏就默认开"，
# product-goals 第 14 条；组名当标签认不出游戏，所以显式给）。
set -euo pipefail
CODE=$(cd "$(dirname "$0")/.." && pwd)
PY="$CODE/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$CODE/.venv/bin/python"
export PYTHONPATH="$(cd "$CODE" && (pwd -W 2>/dev/null || pwd))/src"   # 跑的一定是 $CODE/src（同 head2head.sh）
VIDEO=$1; SPEC=$2; OUT=$3; S=${4:-0}; E=${5:-0}
mkdir -p "$OUT"
# 一行一个组名（组名里可以有空格；Windows 的 print 带 CR，去掉）
mapfile -t NAMES < <("$PY" -c "import json,sys; print('\n'.join(g['name'] for g in (json.load(open(sys.argv[1], encoding='utf-8')).get('groups') or [{'name': 'all'}])))" "$SPEC" | tr -d '\r')
echo "组：${NAMES[*]}"
echo "=== 提取（各组同时跑）$(date '+%H:%M:%S') ==="
# shellcheck disable=SC2086
"$PY" -m flowocr.extract.run_groups "$VIDEO" --regions "$SPEC" --outdir "$OUT" --start "$S" --end "$E" \
  ${SERIAL:+--serial} --progress-every 2000 ${OCR_ARGS:-}
for g in "${NAMES[@]}"; do
  echo "=== 组 $g 建轨 ==="
  # shellcheck disable=SC2086
  "$PY" -m flowocr.analyze.build_tracks "$OUT/$g.jsonl" --outdir "$OUT/$g" --tag "$g" \
    ${GAME:+--matcher gametext --matcher-ctx game=$GAME} ${BUILD_ARGS:-}
done
echo "全部完成：$OUT"
