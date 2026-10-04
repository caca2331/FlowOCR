#!/usr/bin/env bash
# 游戏直播素材的 10 分钟切片（每款 2-3 段，**都挑和整场那部不同的主播**，
# 为的是版式多样性：overlay 位置、弹幕框、摄像头、有没有立绘各不相同）。
#
# 整场那五部下完之后再跑，免得抢带宽。产物写归档目录，不进本项目。
# 每段下完立刻抽一帧，最后拼成一张 contact sheet——**先摆帧，再决定要不要用**。
set -uo pipefail
# 素材根：FLOWOCR_ARCHIVE，没设就读数据根下的 data/archive-root.txt（一行路径；data/ 只留本地）
_DATA=$(PYTHONPATH="$(cd "$(dirname "$0")/.." && pwd)/src" python -m flowocr.paths | tr -d '\r')
ARCHIVE=${FLOWOCR_ARCHIVE:-$(tr -d '\r' < "$_DATA/data/archive-root.txt" 2>/dev/null)}
[ -n "$ARCHIVE" ] || { echo "不知道素材在哪：设 FLOWOCR_ARCHIVE，或在数据根的 data/archive-root.txt 里写一行路径" >&2; exit 2; }
DST="$ARCHIVE/gamestream/slices"
mkdir -p "$DST"

# tag:id:起点（10 分钟）
LIST="
gi-s1:xmSfcwlQVGI:03:00:00
gi-s2:P5pioLbSahA:02:30:00
gi-s3:JHeD01NZofU:04:00:00
hsr-s1:t9aY2NfvEoc:02:30:00
hsr-s2:5PjikuOB2kU:03:00:00
zzz-s1:dreGJ7IytPQ:02:30:00
zzz-s2:jQekKaJDBvg:00:10:00
wuwa-s1:z5cxJAbHbjE:03:00:00
wuwa-s2:adXkKq9YZX4:00:30:00
"

for row in $LIST; do
  tag=$(echo "$row" | cut -d: -f1)
  id=$(echo "$row" | cut -d: -f2)
  t=$(echo "$row" | cut -d: -f3-5)
  end=$("$PY" -c "
import datetime as d, sys
h, m, s = sys.argv[1].split(':')
print(str(d.timedelta(hours=int(h), minutes=int(m), seconds=int(s)) + d.timedelta(minutes=10)))" "$t")
  echo "=== $tag ($id) $t-$end  $(date '+%H:%M:%S')"
  yt-dlp -q --no-warnings -f "bv[height<=1080]" --download-sections "*${t}-${end}" \
    -o "${DST}/${tag}.%(ext)s" "$id" || { echo "[fail] $tag"; continue; }
  f=$(ls "${DST}/${tag}".* 2>/dev/null | grep -v '\.jpg$' | head -1)
  [ -n "$f" ] && ffmpeg -v error -ss 120 -i "$f" -frames:v 1 -vf scale=640:-1 \
    -y "${DST}/${tag}.jpg"
done

# 九宫格：一次看完，省得逐张开
cd "$DST" && ffmpeg -v error -pattern_type glob -i "*.jpg" -filter_complex \
  "tile=3x3:margin=4:padding=4" -frames:v 1 -y sheet.jpg
echo "SLICES DONE $(date '+%H:%M:%S')"
ls -l "$DST" | awk '{print $5, $9}'
