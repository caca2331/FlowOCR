#!/usr/bin/env bash
# 代表性片段的验证驱动。清单在 data/samples.json（**从这份代码所在的 checkout 读**，见 MANIFEST）。
#
# 为什么有它（owner 2026-09-08）：**别动不动重跑全部**。验证按"跑多长"分档——
# 一般性改动 ≤30 分钟、需要大量证据或阶段性结论 120–240 分钟，再往上先说清为什么
# （docs/dev-guide/verification.md「跑多长：按证据强度分档」那张表）。这个脚本**跑之前先把总时长打出来**，省得事后才发现占了几小时。
#
#   bash dev_tools/sampleset.sh list            # 只看清单和总时长，不跑
#   bash dev_tools/sampleset.sh quick           # 一般性改动的默认验证集（30 分钟素材）
#   bash dev_tools/sampleset.sh evidence        # 需要大量证据时（约 150 分钟素材）
#
# **A/B 两条臂**（照搬 head2head.sh 的 SUFFIX 规矩）：`OCR_ARGS` 是要多传给 run_ocr2 的
# 旋钮，`ARM` 是产物后缀。**给了 OCR_ARGS 就必须给 ARM**，否则两臂写进同一个文件互相覆盖。
#
#   ARM=-bucket OCR_ARGS="--rec-bucket 32" bash dev_tools/sampleset.sh quick
#   bash dev_tools/sampleset.sh quick           # 对照臂（ARM 为空 = 当前默认）
#
# 复用判据看五件事：跑完了、pts 时间轴、当前默认解码器、同一个窗口、**同一组生效参数**——
# 最后一条把这一趟的 argv 按 flowocr.extract.ocr_args 解析后，和 obs 的 `_meta.config` 比，
# **默认值翻了也拦**。没有 `config` 字段的旧产物一律重建。
#
# ⚠ 这个驱动**不适合报墙钟 A/B**：两臂各自连跑六段，同一段的两臂相隔十几分钟。
# 墙钟用 dev_tools/ocr_ab.sh（交错、逐对换先后）。
#
# yuka 那几段**不切文件**：run_ocr2 的 --start/--end 按原片绝对时间写时间戳，
# 窗口结果能直接和整片的参照对齐（素材只读）。
# 产物：out/samples/<id>.jsonl（观测）、out/samples/<id>/（轨）。
set -uo pipefail
# **代码在哪、产物在哪，是两件事**（照搬 finesub 的 paths.py）：
# `CODE` 是这个脚本所在的 checkout —— 在 worktree 里就是槽，改完直接跑的就是它；
# `cd` 过去的是**产物根**，worktree 会解析回主 checkout（那里才有 out/ tmp/ 素材）。
CODE=$(cd "$(dirname "$0")/.." && pwd)
PY="$CODE/.venv/Scripts/python.exe"   # 正式包的开发 venv（uv sync --extra nvidia --group dev）：跑的就是 $CODE/src
export PYTHONPATH="$(cd "$CODE" && (pwd -W 2>/dev/null || pwd))/src"   # 跑的一定是 $CODE/src：槽里的 .venv 若是指回主 checkout 的 junction，editable 安装会导回主 checkout 的代码
[ -x "$PY" ] || { echo "没有 $PY：先在 $CODE 里 uv sync --frozen --extra nvidia --group dev" >&2; exit 2; }
cd "$("$PY" -m flowocr.paths)"
# 清单是**代码侧的配置**，不是产物——所以从 $CODE 读，不从产物根读：
# 在槽里改完 samples.json 就能直接跑，不用等合回 main（owner 2026-09-09）。
MANIFEST="data/samples.json"
source "$CODE/explore/env.sh"          # 缓存（uv / HF 等）指到数据根下 explore 的缓存目录，不落用户目录（残留清单）；开发机的 FLOWOCR_GAMETEXT

SET=${1:-list}
case "$SET" in list|quick|evidence|holdout2|zh) ;; *)
  echo "用法：bash dev_tools/sampleset.sh list|quick|evidence|holdout2|zh [id…]"; exit 2;; esac
shift || true
ONLY="$*"                   # 给了 id 就只跑这几段（**素材时长照旧按整个集子打印**，别以为跑了全套）

OCR_ARGS=${OCR_ARGS:-}      # A/B 用：额外传给 run_ocr2 的旋钮
ARM=${ARM:-}                # A/B 用：产物后缀，两臂因此不会互相覆盖
if [ -n "$OCR_ARGS" ] && [ -z "$ARM" ]; then
  echo "**给了 OCR_ARGS 却没给 ARM**——两臂会写进同一个文件，互相覆盖。停。"
  exit 2
fi

# 清单 + 总时长。end_s=0 的用 ffprobe 问真实时长（游戏切片是独立文件）。
# **编码也一并核**：2026-09-09 发现 18 条里 11 条的 `codec` 标错了（yuka 五部是 vp9 被标成
# h264，gi-s1/zzz-s1/wuwa-s1 是 av1，wuwa-s2 是 h264），而"h264 上解码只占 22%"这类
# 成本结论正是按这个字段归因的。手写的字段会烂，ffprobe 不会。
"$PY" - "$SET" "$MANIFEST" <<'PY'
import json, subprocess, sys
sel = sys.argv[1]
d = json.load(open(sys.argv[2], encoding='utf-8'))
want = [s for s in d['samples'] if sel == 'list' or sel in s['sets']]
tot = 0.0
bad = []
print(f"{'id':22s} {'秒':>6} {'源':<16} {'画面':<20} {'剧本':<4} {'编码':<5}")
for s in want:
    dur = s['end_s'] - s['start_s']
    if dur <= 0:
        try:
            dur = float(subprocess.run(
                ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'csv=p=0', s['video']], capture_output=True, text=True,
                timeout=30).stdout.strip()) - s['start_s']
        except Exception:
            dur = 0.0
    try:
        real = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
             'stream=codec_name', '-of', 'csv=p=0', s['video']],
            capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        real = ''
    if real and real != s['codec']:
        bad.append((s['id'], s['codec'], real))
    tot += dur
    print(f"{s['id']:22s} {dur:6.0f} {s['source'][:16]:<16} {s['scene'][:20]:<20} "
          f"{'有' if s['script'] else '—':<4} {s['codec']:<5}"
          + (f"  ⚠ 实际是 {real}" if real and real != s['codec'] else ""))
print(f"\n**{len(want)} 段，素材总时长 {tot/60:.0f} 分钟**"
      + (f"（预算 {d['sets'][sel]['budget_min']} 分钟）" if sel in d['sets'] else ""))
if sel in d['sets']:
    print(f"  用途：{d['sets'][sel]['why']}")
if bad:
    print(f"\n⚠ **{len(bad)} 条的 codec 和 ffprobe 对不上**——按编码归因的成本结论会跟着错：")
    for i, want_c, real in bad:
        print(f"    {i:22s} 标 {want_c} / 实 {real}")
    sys.exit(4)
PY
RC=$?
# 编码对不上就停：按 codec 归因的结论（"h264 上解码只占 22%"那类）会静默变成假的。
[ "$RC" -eq 4 ] && { echo "先修 $MANIFEST 再跑。"; exit 4; }
[ "$SET" = list ] && exit 0

mkdir -p out/samples
FAILED=0
IDS=$("$PY" -c "
import json,sys
d=json.load(open(sys.argv[2],encoding='utf-8'))
sel=[s['id'] for s in d['samples'] if sys.argv[1] in s['sets']]
only=sys.argv[3].split()
if only:
    bad=[x for x in only if x not in sel]
    if bad: sys.exit('这几个 id 不在 ' + sys.argv[1] + ' 里：' + ' '.join(bad))
    sel=[x for x in sel if x in only]
print(' '.join(sel))" "$SET" "$MANIFEST" "$ONLY") || exit 2
[ -n "$ONLY" ] && echo "⚠ **只跑子集**：$IDS（不是整个 $SET 集，别拿它当全集的结论）"

for id in $IDS; do
  # 那个 tr 不是装饰：Windows 上 python 的 print 出的是 CRLF，而 read 只吃 LF，
  # **CR 会留在最后一个变量里**。于是 [ "$E" = "0" ] 恒不相等——
  # 而 end_s = 0 正是"到片尾"的约定（游戏切片就这么记的）。
  # $(...) 会把它一起吃掉，所以只有 read 这一处需要。
  # ⚠ 必须写成两字符转义 '\r'：脚本里放一个**字面**的 CR，Git Bash 的 tr 删的是 LF，
  #   看着像修好了，其实一点没变（实测 od -c 逐字节确认过）。
  read -r VIDEO S E < <("$PY" -c "
import json,sys
d=json.load(open(sys.argv[2],encoding='utf-8'))
s=[x for x in d['samples'] if x['id']==sys.argv[1]][0]
print(s['video'], s['start_s'], s['end_s'])" "$id" "$MANIFEST" | tr -d '\r')
  OBS="out/samples/$id$ARM.jsonl"
  # 只看文件在不在会把换了窗口或换了旋钮的旧产物静默拿来用（audit-4 C7 的同一形状）。
  # 判据只有一份，在共享层（audit-4 C8）；窗口和旋钮都在**解析后的生效值**里比
  if why=$("$PY" -m flowocr.extract.ocr_complete "$OBS" \
             --argv "$VIDEO" --out "$OBS" --start "$S" --end "$E" $OCR_ARGS \
                    --progress-every 2000); then
    echo "[skip] $id$ARM 的观测已在（同一窗口、同一组生效参数、已跑完）"
    # 可复用但有话说（**代码指纹不同**：旋钮一样、代码变了）——别把它吞掉，
    # 这正是 09-19 那次"重跑忘了换臂名就会拿旧产物冒充新的"（FP_CHECK=1 可升级成重建）
    [ -n "$why" ] && echo "  $why"
  else
    [ -s "$OBS" ] && echo "[redo] $OBS 在，但不能复用：$why"
    echo "=== $id$ARM OCR $(date '+%H:%M:%S') ==="
    "$PY" -m flowocr.extract.run_ocr2 \
      "$VIDEO" --out "$OBS" --start "$S" --end "$E" $OCR_ARGS --progress-every 2000 \
      2>&1 | grep -v --line-buffered "Warning\|warn\|INFO\|ccache\|Model files\|WARNING"
    [ "${PIPESTATUS[0]}" = 0 ] || { echo "**[fail] $id$ARM OCR**"; FAILED=$((FAILED+1)); continue; }
  fi
  "$PY" -m flowocr.analyze.build_tracks "$OBS" --outdir "out/samples/$id$ARM" --tag "$id" \
    2>&1 | grep -E '主轨候选|主轨并带|槽位最终一致性|常驻 UI 剔除' \
    || { echo "**[fail] $id build_tracks**"; FAILED=$((FAILED+1)); continue; }
  MAIN=$("$PY" -m flowocr.artifacts.tracksio "out/samples/$id$ARM/$id-tracks.json" main_srt)
  if [ -n "$MAIN" ]; then
    # **--hours 必须给**：不给就按产物自己的时间跨度算，短窗口上"条/h"会虚高
    # 一个数量级（yuka 那两段密窗打出 996–2040，而整片正常值是 570–670），
    # 拿它和文档里的数比就是错的（docs/dev-guide/verification.md「报数口径」：写进文档的每个数都要能数回来）。
    HRS=$("$PY" -c "
import json, sys
m = json.loads(open(sys.argv[1], encoding='utf-8').readline())['_meta']
lo = m.get('start_sec') or 0.0
hi = m.get('end_sec') or 0.0
print(round(max(hi - lo, 1e-6) / 3600, 6))" "$OBS")
    # 体检只在出错时非零（报警照样返回 0）；出错要计进失败，别让驱动照样报成功（2026-09-24 整理复审）
    "$PY" "$CODE"/dev_tools/track_health.py "out/samples/$id$ARM/$MAIN" --hours "$HRS" \
      || { echo "**[fail] $id track_health**"; FAILED=$((FAILED+1)); }
  else
    echo "  **没有主轨**"
  fi
done
echo
echo "SAMPLESET DONE $(date '+%H:%M:%S')"
[ "$FAILED" = 0 ] || { echo "**$FAILED 处失败**——上面的 [fail] 行"; exit 1; }
