#!/usr/bin/env bash
# 游戏直播素材的整片 OCR（原神 / 崩壊スターレイル / ゼンレスゾーンゼロ / 鳴潮）。
#
# 和 run_full_ocr.sh 是同一条命令，只换素材；参数**一个都不调**——
# 这一轮问的就是"yuka 五部上定下来的那套参数，换到别的游戏还成不成立"。
# 调参会把这个问题问没了。
#
# `--line-buffered` 不是可选的（run_full_ocr.sh 的注释里有那次误判）。
# 看进度用产物：python dev_tools/ocr_progress.py out/gamestream/gi1.jsonl
#
# 切片（10 分钟那批）跑得快，先跑它们，整场放后面。
# **代码在哪、产物在哪，是两件事**（照搬 finesub 的 paths.py）：
# `CODE` 是这个脚本所在的 checkout —— 在 worktree 里就是槽，改完直接跑的就是它；
# `cd` 过去的是**产物根**，worktree 会解析回主 checkout（那里才有 out/ tmp/ 素材）。
CODE=$(cd "$(dirname "$0")/.." && pwd)
PY="$CODE/.venv/Scripts/python.exe"   # 正式包的开发 venv（uv sync --extra nvidia --group dev）：跑的就是 $CODE/src
export PYTHONPATH="$(cd "$CODE" && (pwd -W 2>/dev/null || pwd))/src"   # 跑的一定是 $CODE/src：槽里的 .venv 若是指回主 checkout 的 junction，editable 安装会导回主 checkout 的代码
[ -x "$PY" ] || { echo "没有 $PY：先在 $CODE 里 uv sync --frozen --extra nvidia --group dev" >&2; exit 2; }
cd "$("$PY" -m flowocr.paths)"
source "$CODE/explore/env.sh"
FAILED=0
mkdir -p out/gamestream
# 素材根：FLOWOCR_ARCHIVE，没设就读数据根下的 data/archive-root.txt（一行路径；data/ 只留本地）
_DATA=$(PYTHONPATH="$(cd "$(dirname "$0")/.." && pwd)/src" python -m flowocr.paths | tr -d '\r')
ARCHIVE=${FLOWOCR_ARCHIVE:-$(tr -d '\r' < "$_DATA/data/archive-root.txt" 2>/dev/null)}
[ -n "$ARCHIVE" ] || { echo "不知道素材在哪：设 FLOWOCR_ARCHIVE，或在数据根的 data/archive-root.txt 里写一行路径" >&2; exit 2; }
SRC="$ARCHIVE/gamestream"

# **"文件非空"不等于"跑完了"**（methodology-audit-4 报告 C7）：解码中途断掉也会
# 留下一份看着正常的 jsonl，而按非空跳过就等于"一次中断变成之后每次都已经做完"。
# 判据改成读 `_meta.complete`——那是 run_ocr2 按**实际推进到哪一帧**写的。
# 判据本身在 `flowocr.extract.ocr_complete`（三个驱动共用一份，audit-5 §3）。
# 复用还要对上**生效参数**（`--argv`，按 flowocr.extract.ocr_args 解析后比）：
# 默认值一翻，旧产物就不再算"已完成"。
run () {   # run <标签> <视频路径>
  out="out/gamestream/$1.jsonl"
  local args=("$2" --out "$out" --progress-every 2000)
  if why=$("$PY" -m flowocr.extract.ocr_complete "$out" --argv "${args[@]}"); then
    echo "[skip] $out 已完成（同一组生效参数）"
    [ -n "$why" ] && echo "  $why"     # 代码指纹不同时 ocr_complete 会说话，别吞掉
    return
  fi
  [ -s "$out" ] && echo "[redo] $out 在，但不能复用：$why"
  [ -f "$2" ] || { echo "[skip] $2 不在"; return; }
  echo "=== $1 开始 $(date '+%H:%M:%S') ==="
  "$PY" -m flowocr.extract.run_ocr2 "${args[@]}" \
    2>&1 | grep -v --line-buffered "Warning\|warn\|INFO\|ccache\|Model files\|WARNING"
  rc=${PIPESTATUS[0]}
  [ "$rc" = 0 ] || { echo "**[fail] $1 退出码 $rc**（未完成的产物留着，但不算数）";
                     FAILED=$((FAILED+1)); }
  echo "=== $1 结束 $(date '+%H:%M:%S') ==="
}

# `bash dev_tools/run_gamestream_ocr.sh slices` 只跑切片（90 分钟视频，十几分钟就完）；
# 不带参数才跑整场（现在只有原神两场 8.9 小时视频，**那是 owner 机器上的开销**）。
SCOPE=${1:-all}

if [ "$SCOPE" != full ]; then
  for f in "$SRC"/slices/*.mp4 "$SRC"/slices/*.webm; do
    [ -e "$f" ] || continue
    # `*.audio.webm` 是只有音轨的那份（09-12 补下的，预览要声音当参照——见 download-audio-with-footage）。
    # 不跳过它就会拿音频文件去跑 OCR：四段全 fail、驱动退出码 1（2026-09-17 发现，那之后 slices 没重跑过）
    case "$(basename "$f")" in *.audio.*) continue ;; esac
    run "$(basename "${f%.*}")" "$f"
  done
fi
# 默认只列原神两场（owner 2026-09-08：当时只有原神能拿到文本库）。星铁 / 绝区零的库补齐之后，
# 2026-09-11 用 `FULL_TAGS="zzz hsr"` 跑过一次整场（game-text-corpus 报告 §7：两场的主轨都挑错了）；
# 鸣潮没有库，还没跑。**注意**原神两场的旧观测是旧时间轴，不带参数跑会被 ocr_complete 判成要重跑（8.9 小时）。
FULL_TAGS=${FULL_TAGS:-"gi1 gi2"}

if [ "$SCOPE" != slices ]; then
  for base in $FULL_TAGS; do
    f=""
    for cand in "$SRC/$base.mp4" "$SRC/$base.webm"; do
      [ -e "$cand" ] && f="$cand"
    done
    [ -n "$f" ] || { echo "[skip] $base 的源片不在 $SRC"; continue; }
    # **av1 一律走 dev_tools/prep_gamestream.sh 转出来的 h264 副本**：
    # 解码是 av1 的瓶颈，转完 1.6x -> 5.2x（gamestream-first-look 报告 §6）。
    # 原神这两部是 h264 / vp9，**都不用转**。（这条是 cv2 解码时代的；ffmpeg 解码进默认之后
    # av1 端到端已有 3.4x，副本还有没有必要没重测——09-11 的整场 hsr 仍走的副本。）
    [ -s "$SRC/h264/$base.mp4" ] && f="$SRC/h264/$base.mp4"
    run "$base" "$f"
  done
fi
echo "ALL DONE $(date '+%H:%M:%S')"
[ "$FAILED" = 0 ] || { echo "**$FAILED 段没跑完**——上面的 [fail] 行"; exit 1; }
