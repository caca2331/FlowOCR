#!/usr/bin/env bash
# ORT 推理服务挂 Nsight Systems：nsys 只挂服务进程（det + rec 都在里面），run_ocr2 在外面照常跑、用 FLOWOCR_ORT_ADDR 接上去；
# 跑完导出 sqlite，给 dev_tools/nsys_report.py 读。
#
#   bash dev_tools/nsys_server.sh <输出目录> <tag> <视频> [run_ocr2 参数...]
#   例：bash dev_tools/nsys_server.sh tmp/meas/nsys w4 D:/…/hsr-s2.mp4 --start 0 --end 300
#       bash dev_tools/nsys_server.sh tmp/meas/nsys w32 D:/…/hsr-s2.mp4 --start 0 --end 300 --rec-window 32
#
# 产物（都在输出目录里）：<tag>.nsys-rep / <tag>.sqlite、<tag>.jsonl（obs）、<tag>.tl.jsonl 和服务端那份 <tag>.tl.jsonl.ort.jsonl
# （--timeline）、<tag>.log（run_ocr2）、<tag>.nsys.log。5 分钟片段的 sqlite 约 1~2 GB，开调度记录时约 7 GB——放 tmp/，用完删。
# 环境变量：
#   NSYS        nsys 可执行文件（默认取 Program Files 下版本号最大的 Nsight Systems）
#   NSYS_ADMIN  =1 时加线程调度记录（--cpuctxsw）和 CPU 采样：nsys_report.py stalls 要它；Windows 上要在管理员终端里跑
#               （Git Bash 的 `bash.exe -l -c "…"`；PowerShell 里直接 `bash` 会进 WSL）
#   NSYS_PORT   服务监听的端口（默认现找一个空闲的，免得两趟同时跑时撞端口、收尾杀错进程）
#   NSYS_OFF    =1 时不挂 Nsight、服务照样单独起、照样走 TCP：和采集那趟配对的无采集对照（只出 obs / 时间线 / 日志）
# 和默认运行的差别：rec 主连接走 TCP 不走管道；det 是单独接进来的 client（默认是同一服务的旁路连接）；rec 在途数仍是 2。
# 只在 Windows 上用过（netstat / taskkill 收服务进程）。先跑 hw status；跑着的时候别的 GPU 活会混进整卡指标。
set -uo pipefail
[ $# -ge 3 ] || { sed -n '2,8p' "$0"; exit 2; }
OUT=$1; TAG=$2; VIDEO=$3; shift 3
# nsys_report.py 按 CUDA EP 的核名分 det / rec：没显式给 --backend 时固定走 CUDA EP（装了 TensorRT 时 auto 会走 TRT）
case " $* " in *" --backend "*|*" --backend="*) ;; *) set -- --backend cuda "$@" ;; esac
CODE=$(cd "$(dirname "$0")/.." && pwd)
source "$CODE/dev_tools/pyenv.sh"   # PY = pyrun.py 挑的解释器（槽里没有 .venv 就用主 checkout 的），PYTHONPATH 钉到 $CODE/src
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
win() { cygpath -m "$1" 2>/dev/null || echo "$1"; }
PORT=${NSYS_PORT:-$("$PY" -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])")}
N=${NSYS:-$(ls -d "/c/Program Files/NVIDIA Corporation/Nsight Systems "*/target-windows-x64/nsys.exe 2>/dev/null | sort -V | tail -1)}
[ "${NSYS_OFF:-0}" = 1 ] || [ -x "$N" ] || { echo "找不到 nsys（设 NSYS=<路径>）" >&2; exit 2; }
CPU="--sample=none --cpuctxsw=none"
[ "${NSYS_ADMIN:-0}" = 1 ] && CPU="--sample=process-tree --cpuctxsw=process-tree"
up() { "$PY" -c "import socket;socket.create_connection(('127.0.0.1',$PORT),1).close()" 2>/dev/null; }
up && { echo "端口 $PORT 已经有人在听（上一趟没收干净？），换 NSYS_PORT 或先关掉它" >&2; exit 2; }
mapfile -t CMD < <("$PY" "$CODE/dev_tools/nsys_report.py" server-cmd "$PORT" "$@" | tr -d '\r')
[ ${#CMD[@]} -gt 0 ] || { echo "拼服务命令失败（run_ocr2 参数不对？）" >&2; exit 2; }
GPU=0
for i in "${!CMD[@]}"; do [ "${CMD[$i]}" = --device ] && GPU=${CMD[$((i + 1))]#gpu:}; done   # 整卡指标采服务所在的那张卡
if [ "${NSYS_OFF:-0}" = 1 ]; then
  # 无采集对照：同一条服务命令直接起，收尾按 PID 杀它自己（不按端口找进程）
  FLOWOCR_TIMELINE="$(win "$OUT/$TAG.tl.jsonl")" "${CMD[@]}" > "$OUT/$TAG.srv.log" 2>&1 &
else
  FLOWOCR_TIMELINE="$(win "$OUT/$TAG.tl.jsonl")" "$N" profile -t cuda $CPU --gpu-metrics-devices=$GPU --gpu-metrics-set=gb20x \
    --gpu-metrics-frequency=2000 --session-new="fo_$TAG" -o "$OUT/$TAG" --force-overwrite=true "${CMD[@]}" > "$OUT/$TAG.nsys.log" 2>&1 &
fi
NS=$!
stop_srv() {
  if [ "${NSYS_OFF:-0}" = 1 ]; then
    taskkill //F //T //PID "$(cat /proc/$NS/winpid 2>/dev/null || echo $NS)" > /dev/null 2>&1 || kill $NS 2>/dev/null
  else
    "$N" stop --session="fo_$TAG" >> "$OUT/$TAG.nsys.log" 2>&1
    # 由 Nsight 结束自己的目标进程：不用跨权限 taskkill，也不会按端口误杀其他进程。
    "$N" shutdown --kill=true --session="fo_$TAG" >> "$OUT/$TAG.nsys.log" 2>&1
  fi
}
for i in $(seq 1 120); do
  up && break
  kill -0 $NS 2>/dev/null || break
  sleep 1
done
up || { echo "$TAG：服务没起来，看 $OUT/$TAG.nsys.log / $OUT/$TAG.srv.log" >&2; stop_srv; wait $NS; exit 1; }
echo "$TAG：服务 ${i}s 后可连"
FLOWOCR_ORT_ADDR="rec=127.0.0.1:$PORT,det=127.0.0.1:$PORT" "$PY" -m flowocr.extract.run_ocr2 "$VIDEO" --quality high "$@" \
  --out "$(win "$OUT/$TAG.jsonl")" --timeline "$(win "$OUT/$TAG.tl.jsonl")" > "$OUT/$TAG.log" 2>&1
RUN_RC=$?
echo "$TAG：run_ocr2 rc=$RUN_RC"
stop_srv
SHUTDOWN_RC=$?
wait $NS
NS_RC=$?
[ "${NSYS_OFF:-0}" = 1 ] && { [ "$RUN_RC" = 0 ]; exit; }
echo "$TAG：nsys rc=$NS_RC"
grep -qi "administrative\|disabling" "$OUT/$TAG.nsys.log" && [ "${NSYS_ADMIN:-0}" = 1 ] && echo "⚠ 没拿到管理员权限：调度记录和 CPU 采样是空的"
"$N" export --type sqlite --force-overwrite=true -o "$OUT/$TAG.sqlite" "$OUT/$TAG.nsys-rep" > /dev/null 2>&1
EXPORT_RC=$?
echo "$TAG：导出 rc=$EXPORT_RC → $OUT/$TAG.sqlite"
[ "$RUN_RC" = 0 ] && [ "$NS_RC" = 0 ] && [ "$EXPORT_RC" = 0 ] && [ "$SHUTDOWN_RC" = 0 ]
