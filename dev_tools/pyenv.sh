# 驱动脚本的解释器与代码路径：在 `CODE=$(cd "$(dirname "$0")/.." && pwd)` 之后 `source "$CODE/dev_tools/pyenv.sh"`。设两样：
#   PY          解释器：pyrun.py 挑的那个（本 checkout 的 .venv，没有就用主 checkout 的；Windows 的 Scripts/python.exe 或 Linux 的 bin/python）
#   PYTHONPATH  钉成 $CODE/src：槽里的 .venv 若是指回主 checkout 的 junction，editable 安装会导回主 checkout 的代码
# 挑解释器的判据只在 pyrun.py 一处，驱动里不写 .venv 的路径（守卫查）。外层用 PATH 上随便哪个 python，它只负责挑。
_pyenv_outer=$(command -v python || command -v python3) || { echo "pyenv.sh：PATH 上没有 python，挑不了解释器" >&2; exit 2; }
_pyenv_which=$("$_pyenv_outer" "$CODE/dev_tools/pyrun.py" --which | tr -d '\r') && [ -n "$_pyenv_which" ] ||
  { echo "pyenv.sh：用 $_pyenv_outer 跑 pyrun.py --which 没拿到解释器（报错见上；没有 .venv 时 pyrun 会说怎么建）" >&2; exit 2; }
PY=$(sed -n 's/^interpreter  *//p' <<<"$_pyenv_which")
export PYTHONPATH=$(sed -n 's/^code  *//p' <<<"$_pyenv_which")
unset _pyenv_outer _pyenv_which
