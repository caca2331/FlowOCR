# explore/env.ps1 的 bash 版，给 Git Bash / 脚本用。用法： source explore/env.sh
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 缓存落**数据根**（flowocr.paths 的 data_root()）：槽里 source 这份也落回主 checkout，
# 不会每开一个槽就把模型重下一遍（2026-09-21，旧槽里实测多出一份 133 MB 的 PP-OCRv6）。
# ⚠ 驱动要 source **代码 checkout 里的这份**（`source "$CODE/explore/env.sh"`），别 source 数据根下的：
# 专用数据根（FLOWOCR_DATA_ROOT）不是 checkout、没有 src/，那里算不出数据根，缓存会落到 `/explore/_cache` 这种野路径
_FLOWOCR_DATA="$(PYTHONPATH="$REPO/src" python -m flowocr.paths | tr -d '\r')"
if [ -z "$_FLOWOCR_DATA" ]; then
  echo "env.sh：算不出数据根（$REPO/src 里没有 flowocr？要 source 代码 checkout 里的这份）" >&2
  return 1 2>/dev/null || exit 1
fi
CACHE="$_FLOWOCR_DATA/explore/_cache"
# 游戏文本包（flowocr.paths.gametext_root）：数据根没有 gametext/ 时，开发机用 game-text-data 那边 bundle 出的 out/
# （生产代码不认这个兄弟目录布局，只在开发环境里设）
if [ -z "${FLOWOCR_GAMETEXT:-}" ] && [ ! -d "$_FLOWOCR_DATA/gametext" ] && [ -d "$_FLOWOCR_DATA/../game-text-data/out" ]; then
  export FLOWOCR_GAMETEXT="$_FLOWOCR_DATA/../game-text-data/out"
fi
unset _FLOWOCR_DATA
export HF_HOME="$CACHE/hf"
export TORCH_HOME="$CACHE/torch"
export EASYOCR_MODULE_PATH="$CACHE/easyocr"
export MODELSCOPE_CACHE="$CACHE/modelscope"
export XDG_CACHE_HOME="$CACHE/xdg"
export UV_CACHE_DIR="$CACHE/uv"
mkdir -p "$HF_HOME" "$TORCH_HOME" "$EASYOCR_MODULE_PATH" "$MODELSCOPE_CACHE" "$XDG_CACHE_HOME" "$UV_CACHE_DIR"
