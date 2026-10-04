#!/usr/bin/env bash
# 把 **av1** 的素材转成 h264 再送 OCR。只转 av1，别的一律不动。
#
# 来历（gamestream-first-look 报告 §6）：`run_ocr2.py` 用 `cv2.VideoCapture`，
# 它解 av1 很慢——实测把 rec 调用砍掉 2/3，墙钟只降 18%，时间全在解码上。
# 转码之后 gi-s2 那 120 秒从 1.61x 变 5.18x，**连转码一起算净省 47%**。
# 逐片实测（各跑 120 秒）：
#   h264 1080p60  5.75x     vp9 1080p60  6.66x     vp9 720p30  4.66x   <- 都不用转
#   av1  1080p60  1.6x      -> 转码后 5.2x                              <- 只转这些
#
# 产物对账（同一段 120 秒）：文本多重集一致率 **97.6%**，差的全是 `UID:…` /
# 单字母那类边角小字，**没有台词**。cq 20 是拿这个数定的，别随手调。
#
# 转码写进 h264/ 子目录，原片一个字节不动（素材只读）。
set -uo pipefail
# 素材根：FLOWOCR_ARCHIVE，没设就读数据根下的 data/archive-root.txt（一行路径；data/ 只留本地）
_DATA=$(PYTHONPATH="$(cd "$(dirname "$0")/.." && pwd)/src" python -m flowocr.paths | tr -d '\r')
ARCHIVE=${FLOWOCR_ARCHIVE:-$(tr -d '\r' < "$_DATA/data/archive-root.txt" 2>/dev/null)}
[ -n "$ARCHIVE" ] || { echo "不知道素材在哪：设 FLOWOCR_ARCHIVE，或在数据根的 data/archive-root.txt 里写一行路径" >&2; exit 2; }
SRC="$ARCHIVE/gamestream"
mkdir -p "$SRC/h264"

for f in "$SRC"/*.mp4 "$SRC"/*.webm; do
  [ -e "$f" ] || continue
  base=$(basename "${f%.*}")
  case "$base" in *-lowbr-*) continue;; esac      # 那份是码率对照，不进管线
  codec=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name \
          -of csv=p=0 "$f")
  if [ "$codec" != "av1" ]; then
    echo "[skip] $base 是 $codec，实测够快，不转"
    continue
  fi
  out="$SRC/h264/$base.mp4"
  # **"文件非空"不等于"转完了"**——2026-09-08 中途停掉一次转码，留下 34 GB
  # 一个 moov atom 都没有的 wuwa.mp4；按非空跳过就等于之后一直拿它当好文件。
  # 和 run_gamestream_ocr.sh 的 _meta.complete 是同一条（audit-4 C7）。
  if [ -s "$out" ] && ffprobe -v error -show_entries format=duration        -of csv=p=0 "$out" >/dev/null 2>&1; then
    echo "[skip] $out 已存在且能解"; continue
  fi
  [ -s "$out" ] && echo "[redo] $out 在，但 ffprobe 读不出时长——**是半个文件**，重转"
  echo "=== 转码 $base（av1 -> h264 NVENC cq20）$(date '+%H:%M:%S')"
  ffmpeg -v error -stats -i "$f" -an -c:v h264_nvenc -cq 20 -y "$out" 2>&1 | tail -2
  echo "=== $base 完成 $(date '+%H:%M:%S')  $(du -h "$out" | cut -f1)"
done
echo "PREP DONE $(date '+%H:%M:%S')"
