#!/usr/bin/env bash
# OCR 这一级的 **A/B（至多五臂）墙钟 + 产物对账**：代表性片段上各臂**交错**跑，
# 再建轨对主轨。报表是 dev_tools/ocr_ab_report.py（各臂都和 A 比）。
#
#   TAG=noreuse2 B_ARGS="--no-reuse-v2" bash dev_tools/ocr_ab.sh quick        # A 永远是当前默认，对照臂放 B
#   TAG=detb1 B_ARGS="--det-batch 1" bash dev_tools/ocr_ab.sh quick q-yuka-f5-dialogue q-gi-s2   # 只跑点名的几段
#   TAG=combo B_ARGS="--rec-bucket 32" C_ARGS="--det-batch 4" D_ARGS="--rec-bucket 32 --det-batch 4" \
#     bash dev_tools/ocr_ab.sh quick      # C_ARGS / D_ARGS / E_ARGS 给了才有那条臂
#   PAIRS=3 ...                     # 每段几轮（默认 2）
#   python dev_tools/ocr_ab_report.py tmp/ab/bucket
#
# 每轮把各臂**轮转**着排（第 k 轮从第 k 条臂起）：两臂时就是 AB、BA、AB…；
# **PAIRS ≥ 臂数时每条臂在每个位置恰好一次**（拉丁方）。PAIRS 少于臂数时不平衡——
# 09-10 那张五臂表（PAIRS=2，当时还是"正反各一轮"的排法）里 B/C/D 两轮都在中间，
# 只有 A、E 换过头尾，位置偏差不大但是系统性的。
#
# 为什么不直接用 sampleset.sh 跑两遍（2026-09-10 审查）：那样两臂各自连跑六段，
# 同一段的 A 和 B 相隔十几分钟、落在机器的不同状态上；上一版的表还**每对都是对照臂先跑**，
# 顺序偏差全算在同一边。这里同一段的两臂紧挨着跑，而且**逐对换先后**（AB、BA、AB…），
# 每臂的墙钟取中位。**墙钟读 `_meta.wall_sec`**（不含模型初始化），按 docs/dev-guide/verification.md「墙钟：按差幅分档读」。
#
# 产物：tmp/ab/<TAG>/<id>-{A,B}-<k>.jsonl（旁边 .cpu.json 是整棵进程树的 CPU 秒，dev_tools/jobcpu.py），
# 建轨在 tmp/ab/<TAG>/tr-<id>-{A,B}/（只建第 1 对）。
# **跑着的时候不要改 dev_tools/ 与 explore/ 目录，也不要提交**（开发指南「长任务的纪律」：产物会记两个 git_head）。
set -uo pipefail
CODE=$(cd "$(dirname "$0")/.." && pwd)
PY="$CODE/.venv/Scripts/python.exe"   # 正式包的开发 venv（uv sync --extra nvidia --group dev）：跑的就是 $CODE/src
export PYTHONPATH="$(cd "$CODE" && (pwd -W 2>/dev/null || pwd))/src"   # 跑的一定是 $CODE/src：槽里的 .venv 若是指回主 checkout 的 junction，editable 安装会导回主 checkout 的代码
[ -x "$PY" ] || { echo "没有 $PY：先在 $CODE 里 uv sync --frozen --extra nvidia --group dev" >&2; exit 2; }
cd "$("$PY" -m flowocr.paths)"
source "$CODE/explore/env.sh"

SET=${1:?用法：TAG=<名字> [A_ARGS=…] [B_ARGS=…] bash dev_tools/ocr_ab.sh quick|evidence [id…]}
shift
TAG=${TAG:?要给 TAG（产物目录名）}
A_ARGS=${A_ARGS:-}
B_ARGS=${B_ARGS:-}
PAIRS=${PAIRS:-2}
ARMS=(A B)
for L in C D E; do
  v="${L}_ARGS"
  [ -n "${!v+set}" ] && ARMS+=("$L")
done
declare -A ARGS_OF=()
for L in "${ARMS[@]}"; do
  v="${L}_ARGS"; ARGS_OF[$L]="${!v:-}"
done
# 臂撞没撞要比**解析之后的生效配置**，不是参数字面（2026-09-17 审计：默认值一翻，
# `B_ARGS="--reuse-v2 --reuse-corr 0.8 --refresh-every 32"` 就和 A 的空参数完全相等了）
ARG_LIST=()
for L in "${ARMS[@]}"; do ARG_LIST+=("${ARGS_OF[$L]}"); done
"$PY" "$CODE"/dev_tools/arm_check.py "${ARG_LIST[@]}" || exit 2
MANIFEST="data/samples.json"
DIR="tmp/ab/$TAG"
mkdir -p "$DIR"
{ for L in "${ARMS[@]}"; do printf '%s_ARGS=%s\n' "$L" "${ARGS_OF[$L]}"; done
  printf 'PAIRS=%s\nSET=%s\n' "$PAIRS" "$SET"; } > "$DIR/arms.txt"

IDS=${*:-$("$PY" -c "
import json,sys
d=json.load(open(sys.argv[2],encoding='utf-8'))
print(' '.join(s['id'] for s in d['samples'] if sys.argv[1] in s['sets']))" "$SET" "$MANIFEST")}

run_arm () {   # run_arm <id> <臂> <k> <video> <start> <end>
  local out="$DIR/$1-$2-$3.jsonl" extra=${ARGS_OF[$2]}
  local args=("$4" --out "$out" --start "$5" --end "$6" $extra --progress-every 100000)
  local why
  if why=$("$PY" -m flowocr.extract.ocr_complete "$out" --argv "${args[@]}"); then
    echo "  [skip] $out"
    # 代码指纹不同时 ocr_complete 会说话（旋钮一样、代码变了）——**墙钟 A/B 尤其不能吞**：
    # 一条臂复用旧产物、另一条现跑，表上就是拿两版代码在比（FP_CHECK=1 可升级成重建）
    [ -n "$why" ] && echo "  $why"
    return 0
  fi
  # 整棵进程树的 CPU 秒 / 提交内存峰值记进旁注 <out>.cpu.json（dev_tools/jobcpu.py；报表的 CPU 那几栏读它）
  "$PY" "$CODE"/dev_tools/jobcpu.py "${out%.jsonl}.cpu.json" -- "$PY" -m flowocr.extract.run_ocr2 "${args[@]}" \
    2>&1 | grep -E --line-buffered '"wall_sec"|没跑完|不可信|Error|error|jobcpu' | sed 's/^/  /'
  [ "${PIPESTATUS[0]}" = 0 ] || { echo "  **[fail] $1 $2-$3**"; return 1; }
}

FAILED=0
for id in $IDS; do
  # tr 见 sampleset.sh 的注释：Windows 上 python 的 print 带 CR，read 不吃。
  # 先清空：查不到时 read 什么都不写，上一段的 VIDEO 会原样留着顶替
  VIDEO="" S="" E=""
  read -r VIDEO S E < <("$PY" -c "
import json,sys
d=json.load(open(sys.argv[2],encoding='utf-8'))
s=[x for x in d['samples'] if x['id']==sys.argv[1]][0]
print(s['video'], s['start_s'], s['end_s'])" "$id" "$MANIFEST" 2>/dev/null | tr -d '\r')
  [ -n "${VIDEO:-}" ] || { echo "**[fail] $id 不在 $MANIFEST 里**"; FAILED=$((FAILED+1)); continue; }
  echo "=== $id $(date '+%H:%M:%S') ==="
  for k in $(seq 1 "$PAIRS"); do
    n=${#ARMS[@]}; order=()
    for i in $(seq 0 $((n - 1))); do order+=("${ARMS[$(( (i + k - 1) % n ))]}"); done
    for arm in "${order[@]}"; do
      echo " $arm-$k"
      run_arm "$id" "$arm" "$k" "$VIDEO" "$S" "$E" || FAILED=$((FAILED+1))
    done
  done
  for arm in "${ARMS[@]}"; do
    "$PY" -m flowocr.analyze.build_tracks "$DIR/$id-$arm-1.jsonl" --outdir "$DIR/tr-$id-$arm" \
      --tag "$id" > "$DIR/tr-$id-$arm.log" 2>&1 \
      || { echo "  **[fail] build_tracks $id $arm**"; FAILED=$((FAILED+1)); }
  done
done
echo "ALL DONE $(date '+%H:%M:%S')；报表：python dev_tools/ocr_ab_report.py $DIR"
[ "$FAILED" = 0 ] || { echo "**$FAILED 处失败**"; exit 1; }
