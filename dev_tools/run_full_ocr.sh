#!/usr/bin/env bash
# 整片 OCR 的批量驱动（yuka input1-5）。
#
#   bash dev_tools/run_full_ocr.sh                  # 五部，当前默认
#   bash dev_tools/run_full_ocr.sh 4 5              # 只跑这几部
#   ARM=-bucket OCR_ARGS="--rec-bucket 32" bash dev_tools/run_full_ocr.sh
#
# **A/B 两条臂**（2026-09-10 加，和 sampleset.sh 同一套规矩）：`OCR_ARGS` 是多传给
# run_ocr2 的旋钮，`ARM` 是产物后缀 → `out/yuka/i{N}-full{ARM}.jsonl`，
# 正好是 head2head.sh 的 `OBS_TAG` 读的那个名字。**给了 OCR_ARGS 不给 ARM 会被拦下。**
# 以前这里写死 input1-4、没有旋钮，"整片上跑一条 OCR 臂"根本没有驱动。
#
# `--line-buffered` 不是可选的：不加它，grep 的输出走块缓冲（4 KB），
# 进度行攒不满一块就不落盘，长片上日志能两个小时不动，看着像是断了。
# 2026-09-05 就是这么误判过一次。判断活没活请用产物：
#     python dev_tools/ocr_progress.py out/yuka/i2-full.jsonl
#
# 复用判据走 `flowocr.extract.ocr_complete`，**不是"文件在不在"**（audit-4 C7 / audit-5 §3）：
# 一次中断留下的半截 jsonl 也是非空的，`[ -s ]` 会把它当成"已经做完"，
# 于是之后每次都跳过，而且不报错。同理管道退出码要用 `PIPESTATUS[0]`——
# 管道末端是 grep，它成功了不代表 OCR 成功了。
set -uo pipefail
# **代码在哪、产物在哪，是两件事**（照搬 finesub 的 paths.py）：
# `CODE` 是这个脚本所在的 checkout —— 在 worktree 里就是槽，改完直接跑的就是它；
# `cd` 过去的是**产物根**，worktree 会解析回主 checkout（那里才有 out/ tmp/ 素材）。
CODE=$(cd "$(dirname "$0")/.." && pwd)
PY="$CODE/.venv/Scripts/python.exe"   # 正式包的开发 venv（uv sync --extra nvidia --group dev）：跑的就是 $CODE/src
export PYTHONPATH="$(cd "$CODE" && (pwd -W 2>/dev/null || pwd))/src"   # 跑的一定是 $CODE/src：槽里的 .venv 若是指回主 checkout 的 junction，editable 安装会导回主 checkout 的代码
[ -x "$PY" ] || { echo "没有 $PY：先在 $CODE 里 uv sync --frozen --extra nvidia --group dev" >&2; exit 2; }
cd "$("$PY" -m flowocr.paths)"
# 素材根：FLOWOCR_ARCHIVE，没设就读数据根下的 data/archive-root.txt（一行路径；data/ 只留本地）
_DATA=$(PYTHONPATH="$(cd "$(dirname "$0")/.." && pwd)/src" python -m flowocr.paths | tr -d '\r')
ARCHIVE=${FLOWOCR_ARCHIVE:-$(tr -d '\r' < "$_DATA/data/archive-root.txt" 2>/dev/null)}
[ -n "$ARCHIVE" ] || { echo "不知道素材在哪：设 FLOWOCR_ARCHIVE，或在数据根的 data/archive-root.txt 里写一行路径" >&2; exit 2; }
source "$CODE/explore/env.sh"

OCR_ARGS=${OCR_ARGS:-}      # A/B 用：额外传给 run_ocr2 的旋钮
ARM=${ARM:-}                # A/B 用：产物后缀 = head2head.sh 的 OBS_TAG
if [ -n "$OCR_ARGS" ] && [ -z "$ARM" ]; then
  echo "**给了 OCR_ARGS 却没给 ARM**——两臂会写进同一个文件，互相覆盖。停。"
  exit 2
fi
FILMS=${*:-1 2 3 4 5}

FAILED=0
for n in $FILMS; do
  out="out/yuka/i${n}-full${ARM}.jsonl"
  # 这一趟要跑的 run_ocr2 参数——**复用判据和真正的调用用同一份**。
  # 判据按解析后的生效值比（flowocr.extract.ocr_args）：换了旋钮、或者默认值翻了，都会重跑。
  args=("$ARCHIVE/yuka/input${n}.mp4" --out "$out" $OCR_ARGS --progress-every 2000)
  # ⚠ 这里必须是 `$CODE`：cwd 已经切到**产物根**，写相对路径会去跑主 checkout 那份
  # ——那份可能还没有 CLI（`python -m flowocr.extract.ocr_complete x` 直接成功退出），
  # 于是**没跑完、甚至不存在的产物都会被当成"已完成"跳过**。
  if why=$("$PY" -m flowocr.extract.ocr_complete "$out" --argv "${args[@]}"); then
    echo "[skip] $out 已跑完（同一组生效参数）"
    [ -n "$why" ] && echo "  $why"     # 代码指纹不同时 ocr_complete 会说话，别吞掉
    continue
  fi
  [ -s "$out" ] && echo "[redo] $out 在，但不能复用：$why"
  echo "=== input$n$ARM 开始 $(date '+%H:%M:%S') ==="
  "$PY" -m flowocr.extract.run_ocr2 "${args[@]}" \
    2>&1 | grep -v --line-buffered "Warning\|warn\|INFO\|ccache\|Model files\|WARNING"
  rc=${PIPESTATUS[0]}
  echo "=== input$n$ARM 结束 $(date '+%H:%M:%S') 退出码 $rc ==="
  [ "$rc" = 0 ] || { echo "**[fail] input$n$ARM OCR（退出码 $rc）**"; FAILED=$((FAILED+1)); }
done
echo "ALL DONE $(date '+%H:%M:%S')"
[ "$FAILED" = 0 ] || { echo "**$FAILED 部失败**"; exit 1; }
