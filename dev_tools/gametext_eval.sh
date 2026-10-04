#!/usr/bin/env bash
# 游戏直播素材上的用途 2 尺：obs -> 剧本（gamescript）-> 轨（build_tracks）-> 量（game_align）。
#
#   bash dev_tools/gametext_eval.sh                       # 有文本库的 9 段，默认臂
#   bash dev_tools/gametext_eval.sh gi1 gi2               # 只跑这几段
#   SUFFIX=-band0 BUILD_ARGS="--main-band 0" bash dev_tools/gametext_eval.sh gi-s2 gi1
#   python dev_tools/gametext_report.py                   # 汇总表（按臂分组）
#
# 规矩照搬 head2head.sh：
# * **给了 BUILD_ARGS 就必须给 SUFFIX**——两臂写进同一个目录是静默错误；
# * 轨的缓存按 build_tracks.py 的 mtime + provenance 里的 argv 失效（FORCE=1 强制重建）；
# * 主轨从 provenance 的 main_srt 取，**不 glob 猜**（game_align 直接吃 tracks.json）；
# * 有 SUFFIX 时顺手和默认臂比命中集合差（`${LOG}-vs.log`）。
# 剧本只依赖 obs、gamescript.py 与文本库，各臂共用一份（这是分母各臂一致的前提），
# 三者任一比剧本新就重建（FORCE_REF=1 强制重建）。
set -uo pipefail
FAILED=0
FORCE=${FORCE:-0}
FORCE_REF=${FORCE_REF:-0}
SUFFIX=${SUFFIX:-}
BUILD_ARGS=${BUILD_ARGS:-}
if [ -n "$BUILD_ARGS" ] && [ -z "$SUFFIX" ]; then
  echo "**给了 BUILD_ARGS 却没给 SUFFIX**——两臂会写进同一个目录，互相覆盖。停。"
  exit 2
fi
CODE=$(cd "$(dirname "$0")/.." && pwd)
PY="$CODE/.venv/Scripts/python.exe"   # 正式包的开发 venv（uv sync --extra nvidia --group dev）：跑的就是 $CODE/src
export PYTHONPATH="$(cd "$CODE" && (pwd -W 2>/dev/null || pwd))/src"   # 跑的一定是 $CODE/src：槽里的 .venv 若是指回主 checkout 的 junction，editable 安装会导回主 checkout 的代码
[ -x "$PY" ] || { echo "没有 $PY：先在 $CODE 里 uv sync --frozen --extra nvidia --group dev" >&2; exit 2; }
cd "$("$PY" -m flowocr.paths)"
mkdir -p tmp/gametext out/gametext
# 文本包：没指定、数据根也没有 gametext/ 时，开发机用 game-text-data 那边 bundle 出的 out/（生产代码不认这个目录布局，只在驱动里设）
if [ -z "${FLOWOCR_GAMETEXT:-}" ] && [ ! -d gametext ] && [ -d ../game-text-data/out ]; then
  export FLOWOCR_GAMETEXT="$(pwd)/../game-text-data/out"
fi

# 鸣潮没有文本库。库覆盖多少因段而异（hsr-s2 七成是主播自己的中英字幕），
# 报数时连 gamescript 打的"obs 文本在库里找得到的"那一行一起看。
# hsr / zzz 是整场（2026-09-11 起跑），没跑完的会被下面的门跳过。
TAGS=${*:-"gi-s1 gi-s2 gi-s3 hsr-s1 hsr-s2 zzz-s1 zzz-s2 gi1 gi2 hsr zzz"}

# **按游戏的特化**（owner 2026-09-11，product-goals 第 14 条："是星铁就开"）：
# 按游戏的特化 = **匹配器给默认聚类器的补丁**（`--matcher gametext`，表在 flowocr.analyze.matchers.gametext.PATCHES）：
# build_tracks 自己按 --tag 认游戏、套补丁、把覆盖了什么打出来并写进 provenance（2026-09-22 起，原来是 game_patches 翻成 CLI）。

same_build_args() {   # $1 = tracks.json，$2 = 这一趟的 BUILD_ARGS（同 head2head.sh）
  "$PY" -c "
import sys
from flowocr.artifacts import tracksio
try:
    prov = tracksio.load(sys.argv[1])['provenance']
except Exception as exc:
    print(f'  [rebuild] 读不到 provenance（{exc}）'); sys.exit(1)
got = ' '.join(tracksio.build_args(prov)) if 'argv' in prov else '?'
want = ' '.join(sys.argv[2].split())
if 'argv' not in prov or got != want:
    print(f'  [rebuild] BUILD_ARGS 对不上：产物是 [{got}]，这次要 [{want}]'); sys.exit(1)
# 代码指纹（建轨的表、--cluster learned / lines 另记的代码 + 模型、回抠结算）：只比 mtime 会静默复用
# （2026-09-21 换了模型重跑打出上一版的数；2026-09-26 审计：默认行为散在 build_tracks.py 之外的模块里）
from flowocr.analyze import build_tracks as bt
why = bt.cache_stale(tracksio.load(sys.argv[1]))
if why:
    print(f'  [rebuild] {why}'); sys.exit(1)" \
    "$1" "$2"
}

# 剧本的缓存也要看**参数与 schema**，不只看 mtime（audit-6 §2.4）：手工跑过一次 `--qmin 6` 之后，
# 光比 mtime 会静默复用那份。驱动从来不给 gamescript 额外参数，所以 argv 必须恰好是 obs --out ref。
same_ref() {   # $1 = 剧本 JSON，$2 = obs
  "$PY" -c "
import json, sys
from flowocr.analyze import gamescript as GS
try:
    d = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception as exc:
    print(f'  [rebuild-ref] 读不到剧本（{exc}）'); sys.exit(1)
if d.get('schema') != GS.SCHEMA:
    print(f'  [rebuild-ref] 剧本的 schema 是 {d.get(\"schema\")!r}，当前是 {GS.SCHEMA}'); sys.exit(1)
got, want = d['provenance'].get('argv'), [sys.argv[2], '--out', sys.argv[1]]
if got != want:
    print(f'  [rebuild-ref] 剧本当初的参数是 {got}，驱动要的是 {want}'); sys.exit(1)
# 剧本还依赖语料：按文本包的内容指纹比（拷贝 / 解压不改它），不看文件时间
have, now = d['provenance']['gametext']['fingerprint'], GS.bundle_of(d['provenance']['game']).fingerprint
if have != now:
    print(f'  [rebuild-ref] 剧本出自文本包 {have[7:19]}，现在的是 {now[7:19]}'); sys.exit(1)" \
    "$1" "$2"
}

for tag in $TAGS; do
  obs=out/gamestream/$tag.jsonl
  # **不能用 `[ -s ]`**：整场 OCR 跑着的时候观测已经非空，读进来量的是前几十分钟、不报错
  if ! why=$("$PY" -m flowocr.extract.ocr_complete "$obs" --finished); then
    echo "[skip] $obs：$why"; continue
  fi
  ref=out/gametext/$tag-ref.json
  outdir=out/gs-$tag$SUFFIX
  tracks=$outdir/$tag-tracks.json
  LOG=tmp/gametext/$tag$SUFFIX
  echo "================ $tag$SUFFIX  $(date '+%H:%M:%S') ================"

  # 剧本还依赖文本包：换了语料（指纹不同）也要重建，见 same_ref
  if [ "$FORCE_REF" = 1 ] || [ ! -s "$ref" ] || [ "$CODE/src/flowocr/analyze/gamescript.py" -nt "$ref" ] \
     || [ "$obs" -nt "$ref" ] \
     || ! same_ref "$ref" "$obs"; then
    "$PY" -m flowocr.analyze.gamescript "$obs" --out "$ref" > "tmp/gametext/$tag-ref.log" 2>&1 \
      || { echo "[fail] gamescript $tag（看 tmp/gametext/$tag-ref.log）"; FAILED=$((FAILED+1)); continue; }
  fi
  grep -E '找得到|圈中' "tmp/gametext/$tag-ref.log"

  # 游戏补丁在前、开臂的 BUILD_ARGS 在后（后者可以覆盖前者）；缓存比的是**合起来**的那串
  ARGS="--matcher gametext $BUILD_ARGS"
  if [ "$FORCE" = 1 ] || [ ! -s "$tracks" ] \
     || ! same_build_args "$tracks" "$ARGS"; then
    "$PY" -m flowocr.analyze.build_tracks "$obs" --outdir "$outdir" --tag "$tag" $ARGS \
      > "${LOG}-build.log" 2>&1 \
      || { echo "[fail] build_tracks $tag$SUFFIX"; FAILED=$((FAILED+1)); continue; }
  fi

  "$PY" -m flowocr.analyze.game_align "$ref" --subs "$tracks" --out "${LOG}.json" --show 20 \
    > "${LOG}.log" 2>&1 \
    || { echo "[fail] game_align $tag$SUFFIX（看 ${LOG}.log）"; FAILED=$((FAILED+1)); continue; }
  grep -E '^轨 |^剧本侧' "${LOG}.log"
  if [ -n "$SUFFIX" ]; then
    base=out/gs-$tag/$tag-tracks.json
    if [ -s "$base" ]; then
      "$PY" -m flowocr.analyze.game_align "$ref" --subs "$base" --vs "$tracks" --show 8 \
        > "${LOG}-vs.log" 2>&1 && sed -n '/命中集合差/,$p' "${LOG}-vs.log"
    else
      echo "  （默认臂 $base 还没建，跳过集合差）"
    fi
  fi
done
echo
echo "GAMETEXT DONE $(date '+%H:%M:%S')"
[ "$FAILED" = 0 ] || { echo "**$FAILED 段失败**——上面的 [fail] 行"; exit 1; }
