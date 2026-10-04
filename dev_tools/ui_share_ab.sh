#!/usr/bin/env bash
# **常驻 UI 剔除这条规则会不会乱咬**——在异构素材上一旋钮 A/B。
#
# 存在的理由（methodology-audit-2 报告 §5.2）：这条规则的阈值是照着
# yuka 那五部调出来的（下界由名牌占比 27-28% 定、上界由 `Auto` 的 97% 定），
# 然后**在同一批素材上评估**。换到短行天然多的素材（歌词、新闻标题条、英文
# `OK.`）会不会乱咬，没人验过。这个脚本就是那次验证，跑一遍几十秒。
#
# 只动 `--ui-share` 一个旋钮，其它全默认；比三个数：
#   总 cue（丢了多少内容）/ 空的区域 SRT（有没有整块被清空）/ 被剔掉的行数。
#
# 用法：bash dev_tools/ui_share_ab.sh                      # 全部 60 秒切片
#       bash dev_tools/ui_share_ab.sh out/zh-news/paddle-v6medium.jsonl
#       bash dev_tools/ui_share_ab.sh out/yuka/i3-full.jsonl   # **整片**（十几分钟，两次全量重建）
#
# **切片上的结论不能外推到整片**（docs/dev-guide/verification.md「小样本上的结论不外推」）：60 秒里"某短行占了过半 cue"和
# 整片里是完全不同的事件——切片只有几十条 cue，分母小，判据天然更容易触发。
# 所以整片至少要跑一部。整片的 obs 都在 out/yuka/ 下，名字取 obs 文件名而不是目录名。
set -uo pipefail
# **代码在哪、产物在哪，是两件事**（照搬 finesub 的 paths.py）：
# `CODE` 是这个脚本所在的 checkout —— 在 worktree 里就是槽，改完直接跑的就是它；
# `cd` 过去的是**产物根**，worktree 会解析回主 checkout（那里才有 out/ tmp/ 素材）。
CODE=$(cd "$(dirname "$0")/.." && pwd)
PY="$CODE/.venv/Scripts/python.exe"   # 正式包的开发 venv（uv sync --extra nvidia --group dev）：跑的就是 $CODE/src
export PYTHONPATH="$(cd "$CODE" && (pwd -W 2>/dev/null || pwd))/src"   # 跑的一定是 $CODE/src：槽里的 .venv 若是指回主 checkout 的 junction，editable 安装会导回主 checkout 的代码
[ -x "$PY" ] || { echo "没有 $PY：先在 $CODE 里 uv sync --frozen --extra nvidia --group dev" >&2; exit 2; }
cd "$("$PY" -m flowocr.paths)"
OBS_LIST=${*:-$(ls out/*/paddle-v6medium.jsonl 2>/dev/null)}
OUT=tmp/ui-ab
mkdir -p "$OUT"

cnt() {   # $1 = 目录；打印 "总cue 空文件数"
  local tot=0 zero=0 n
  for f in "$1"/*.srt; do
    [ -e "$f" ] || continue
    n=$(grep -c -- '-->' "$f")
    tot=$((tot + n))
    [ "$n" = 0 ] && zero=$((zero + 1))
  done
  echo "$tot $zero"
}

printf "%-18s %10s %8s  %10s %8s %10s\n" 素材 关-总cue 关-空 开-总cue 开-空 被剔的行
for OBS in $OBS_LIST; do
  NAME=$(basename "$OBS" .jsonl)
  [ "$NAME" = paddle-v6medium ] && NAME=$(basename "$(dirname "$OBS")")
  ok=1
  for U in 0 0.5; do
    D="$OUT/$NAME-ui$U"
    "$PY" -m flowocr.analyze.build_tracks "$OBS" --outdir "$D" --tag "$NAME" --ui-share $U \
      > "$OUT/$NAME-ui$U.log" 2>&1 || { echo "[fail] $NAME ui=$U"; ok=0; }
  done
  [ "$ok" = 1 ] || continue
  read -r C0 Z0 <<< "$(cnt "$OUT/$NAME-ui0")"
  read -r C5 Z5 <<< "$(cnt "$OUT/$NAME-ui0.5")"
  DROP=$(grep -o '共删掉 [0-9]* 行' "$OUT/$NAME-ui0.5.log" | head -1 | tr -dc '0-9')
  MARK=""
  [ "$Z5" -gt "$Z0" ] && MARK="  ← **有区域被整个清空**"
  printf "%-18s %10s %8s  %10s %8s %10s%s\n" \
    "$NAME" "$C0" "$Z0" "$C5" "$Z5" "${DROP:-0}" "$MARK"
done
echo
echo "被剔掉的是什么（开 0.5 那一侧的日志）："
grep -h -A3 '常驻 UI 剔除' "$OUT"/*-ui0.5.log 2>/dev/null | grep -E '^    ' | sort -u | head -20
