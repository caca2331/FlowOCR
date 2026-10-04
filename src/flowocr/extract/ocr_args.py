"""`run_ocr2` 的参数定义。**单独放一个文件是为了让复用判据读得到"生效的配置"。**

来历（2026-09-10 审查）：复用判据原来比的是 `_meta.argv`——**命令行字面**。
字面比不出默认值：`--rec-bucket` 一旦翻成默认，对照臂的旧产物 argv 一字不变，
就会被当成"当前默认"复用，而它是旧默认产的——audit-4 C7 同一个形状。
main 为解码器单独打过一个补丁（`framesource.DEFAULT_DECODER`），
说明这个洞每翻一次默认就会再开一次。

所以判据改成比**解析之后的值**：`run_ocr2` 把 `product_config(args)` 写进
`_meta.config`，`ocr_complete.obs_reusable(argv=...)` 拿同一个解析器把
"这一趟要跑的 argv"解析出来再比。默认值变了，解析结果就变了，旧产物自然对不上。
解析器只有这一份，所以两边不会各自漂。

这个文件**不许 import 重的东西**（cv2 / onnxruntime）：`ocr_complete` 在系统 Python 里跑。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from flowocr.extract import framesource
from flowocr.provenance import code_fp as _code_fp

CODE_FP_FILES = (
    "src/flowocr/extract/ocr_args.py", "src/flowocr/extract/ocr_complete.py", "src/flowocr/extract/ocr_parallel.py",
    "src/flowocr/extract/framesource.py", "src/flowocr/extract/edge_refine.py", "src/flowocr/extract/recpack.py",
    "src/flowocr/extract/ptsclock.py", "src/flowocr/extract/typewriter_fuse.py",
    # ORT 这条路的产数代码（2026-09-18 审计补上）：前后处理 + CTC 解码在 recdecode、后端封装在 recort、IPC 在 ortclient，
    # 少一个就是"换了产数的代码而指纹没动"
    "src/flowocr/extract/recdecode.py", "src/flowocr/extract/recort.py", "src/flowocr/extract/ortclient.py",
    "src/flowocr/extract/recprep.py",     # rec 前处理（管线进程和 ORT 服务进程共用这一份，2026-09-23）
    "src/flowocr/extract/run_ocr2.py", "src/flowocr/extract/reuse_v2.py", "src/flowocr/extract/fast_det.py",
    "src/flowocr/extract/detpost.py",     # det 的配置与 DB 后处理
    "src/flowocr/extract/recpool.py",
    "src/flowocr/extract/regions.py",     # 自选 OCR 范围：遮罩 / 拆框改 det 与 rec 的输入（--regions）
    "src/flowocr/extract/edge_proc.py",   # --edge-proc：回抠换进程跑（消息往返替换了行对象，证据经它挂回去）
    "src/flowocr/extract/framegrid.py",   # 取哪些帧（采样 / 辅助流的时间网格）：采样帧和证据窗口的帧都是它选的
    "src/flowocr/extract/decode_proc.py",  # --decode-shards：只解码的子进程（帧经共享内存槽过来）
    "src/flowocr/extract/childproc.py",    # 拆进程的共用胶水（起子进程、口令、收尾）
    "src/flowocr/extract/decode_shards.py",  # --decode-shards：几路 ffmpeg 分块解、按帧号接缝
    "src/flowocr/extract/ort_server.py",   # 真跑推理的那半在另一个进程里，同样要进指纹
    "src/flowocr/extract/supervisor.py",   # 外层监督者：硬解失败重不重跑、`_meta.hwaccel_fallback` 由它决定
    "src/flowocr/extract/ffcheck.py",      # 起跑前的 ffmpeg 检查与 `_meta.runtime`（不改读数，但被 run_ocr2 直接 import）
    "src/flowocr/models.py",               # 默认 ONNX 模型是哪一份（钉的提交号）、rec 的 argmax 怎么融
    "src/flowocr/provenance.py",      # 算指纹的代码本身（各级的表都带它，build_tracks 那条注释）
)
"""产出 obs 的代码，**仓库相对路径**（口径同 `build_tracks.code_fp`，实现在 `flowocr.provenance`；
2026-09-21 从 `(目录, (文件…))` 改成平表，所以这天之后的 `_meta.code_fp` 和之前的不可比）。

为什么 OCR 这一级也要指纹（2026-09-17 审计）：obs 以前只装文本和框，现在 `--refine-fused` 把**测量结果**
（首字 / 全字 / 消失）也写进去了。`edge_refine` / `typewriter_fuse.signals` 一改，新产物的数就变，
而旧产物的 `config` 一字不动、照旧"可复用"；回抠那一级把 `edge_refine.py` 算进自己的 `code_fp` 反而更误导——
它指纹的不是产出这些数的那份代码。**指纹只写进 `_meta.code_fp`（追溯用），不进 `config`**：
进了 config 就等于"动一下 tools/ 就重跑所有整片"，代价和收益不成比例。"""


def code_fp() -> str:
    """产这份 obs 的代码的内容指纹（`flowocr.provenance.code_fp` 带上这张表）。"""
    return _code_fp(CODE_FP_FILES)

def check_device(device: str) -> str:
    """`--device` 的合法值：`auto`（默认）/ `gpu` / `gpu:N` / `cpu`。解析时就拦，别等建模型时才炸。"""
    d = (device or "").lower()
    if d in ("auto", "cpu", "gpu") or (d.startswith("gpu:") and d[4:].isdigit()):
        return d
    raise SystemExit(f"--device 只认 auto / gpu / gpu:N / cpu：{device!r}")


def visible_gpu_count(smi_list: str | None = None) -> int | None:
    """CUDA 看得见几张卡，不 import 推理库（`run_ocr2.gpu_blocker` 用，给 `--device gpu:N` 早报错）。
    设了 `CUDA_VISIBLE_DEVICES` 就按它数（`nvidia-smi` 不认这个变量）；否则数 `nvidia-smi -L` 的 `GPU n:` 行。
    问不到（没有 nvidia-smi、超时、出错）返回 None——交给建 session 时报，和原来一样。`smi_list` 是守卫注入的输出。"""
    vis = os.environ.get("CUDA_VISIBLE_DEVICES")
    if vis is not None:
        ids = [x for x in vis.replace(" ", "").split(",") if x]
        return 0 if not ids or ids[0].startswith("-") else len(ids)
    if smi_list is None:
        import subprocess
        try:
            r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        smi_list = r.stdout
    return sum(1 for ln in smi_list.splitlines() if ln.strip().startswith("GPU "))


GPU_UNAVAILABLE_MARKS = (
    "no kernel image is available",            # 卡的架构不在轮子的 SASS / PTX 里（cudaErrorNoKernelImageForDevice，老卡）
    "nokernelimagefordevice",
    "driver version is insufficient",          # 驱动比 CUDA 运行库旧（cudaErrorInsufficientDriver）
    "insufficientdriver",
    "no cuda-capable device", "cuda_error_no_device", "cudaerrornodevice",
    "loadlibrary",                             # Windows 上 CUDA / cuDNN / ORT CUDA EP 的 DLL 加载不了（error 126 / 127）
    "dll load failed",
    "有 cuda ep 却没用上",                      # ort_server：CUDA EP 注册失败、session 回落到 CPU EP（多半同样是库加载不了）
    "没有 cudaexecutionprovider",               # ort_server：这份 onnxruntime 是 CPU 包
)
"""GPU 试推理的异常里（`run_ocr2.resolve_device`；放共享层是为了守卫够得着——run_ocr2 顶层 import 推理库），哪些说明的是**这台机器上 GPU 用不了**（退回 CPU 合理），而不是 GPU 上出了错。
`--device auto` 只对这些退回 CPU；其余（显存不够、捕图失败、运行时报错……）报错退出——静默换 CPU 跑完会让
一次环境 / 配置问题变成"慢 8 倍但标完整"的产物（2026-09-25 CUDA Graph 二分时就这样过了一整段）。
认不全的"用不了"会落到报错那边：用户看到原因、显式给 `--device cpu` 即可，比反过来悄悄降级安全。"""


def gpu_unavailable(msg: str) -> bool:
    m = msg.lower()
    # 另一种库加载失败的说法（`Error loading "…cudnn64_9.dll" or one of its dependencies`）：要带 `.dll`，
    # 光有 "error loading" 会把"加载模型出错"也当成 GPU 不可用、静默降级
    return any(k in m for k in GPU_UNAVAILABLE_MARKS) or (".dll" in m and "load" in m)


def ort_device(device: str) -> str:
    """**解析过的** `--device` -> ORT worker 的 `--device`（`cpu` / `gpu:N`，`gpu` = `gpu:0`）。det / rec 都听这一个 `--device`，
    **卡号也要带上**（复审 P2，2026-09-22：原来 `gpu:1` 到这里成了 `gpu`，跑到了 0 号卡）。
    和 `ort_server.norm_device` 是同一个口径（那边不 import flowocr，只好写两份；守卫核两边一致）。
    `auto` 不在这里解析——它要试推理才定得下来（`run_ocr2.resolve_device`），传进来就是调用方漏了那一步。"""
    d = check_device(device)
    if d == "auto":
        raise ValueError("ort_device 要的是解析过的设备（gpu / cpu），`auto` 先过 run_ocr2.resolve_device")
    if d == "cpu":
        return "cpu"
    return "gpu:0" if d == "gpu" else f"gpu:{int(d[4:])}"


NON_PRODUCT = frozenset({"out", "progress_every", "record_corr", "refine_ring", "timeline", "trace"})
SCHEDULING = frozenset({"det_prefetch", "edge_proc", "decode_shards", "decode_block", "front_proc", "det_proc", "det_lookahead"})
"""**只改"什么时候算"、不改"算出什么"的旋钮**：写进 `config`（所以 `same_arm` 分得开两条臂、
产物也留着它的追溯），但 `config_differs` **不看**它们——翻它的默认值不该让磁盘上的现成 obs 全部重建。

和 `NON_PRODUCT` 的区别：那边是**压根不写进 config**（于是墙钟 A/B 的两条臂会塌成一条，
`arm_check` 拦不住）。`--det-prefetch` 恰恰要两头都占：它是 2026-09-20 量到的最大一笔加速
（ORT rec 之上再 −15%~−25%，六段全中），所以必须能开臂比；而它**产物逐字节相同**
（当时的 Paddle 确定性路径上 `obs_identical` 两段验过：q-yuka-f5-dialogue / q-gi-s2 除 `_meta` 外逐行逐字节相同），
所以不该进复用判据。

⚠ **`--hwaccel` 当天进来过、又被拿出去了**（2026-09-20，Codex 复审 P2 第 3 条，核过属实）：
我当时的依据是"NVDEC bit-exact + 采样格错位是硬门 + 交错 A/B 上 21,587 行文本改 0"，
**可那只证明了 `pipe_pix=nv12` 下采样帧的像素相同**——①`bgr24` 下软硬两条路的色彩转换不同，
像素本来就不一样（hw-decode-results 的假阳性就是这么来的），而 `SCHEDULING` 对所有配置都忽略它；
②辅助流一边是 CPU `scale`、一边是 `scale_cuda`，**缩放器不同，`edge` 就不能自动算等价**——
我只比过 `edge.on` 的条数（481 / 481），没比过数值。等价范围没证全就不该豁免，所以它回到普通产物参数：
软硬解的 obs 互相不复用，用到时按需重建。

`edge_proc`（2026-09-23）：回抠换个进程跑、算法不动。Paddle rec + 同步窗口（确定性路径）三段上对过：gi-s2 60 s 778/778 行、
wuwa-s2 60 s 5,176 行、自选范围时间门 716/716 行，**逐行解析后相同**（wuwa 有 3 行 `edge` 子字典的键序不同——证据到达的先后，值相同）。
`front_proc`（同日）：解码 + det 换进程跑。同样三段、同一条确定性路径上 `--edge-proc --front-proc` 对管线进程内：778 / 5,176 / 716 行逐行解析后相同；
硬解中途交付不对（`FLOWOCR_FAULT_HWACCEL_AT` 注入）照旧退出码 75 -> 监督者确认整棵进程树（含两个子进程）清空 -> 软解重跑完成。
默认 ORT 路径上打字机真值（yuka-f1 / yuka-f5 / gi-s2）和进程内逐项相同。`edge_proc` **2026-09-23 翻成默认开**（decode-buffer）——
正因为在这张表里，翻默认不让磁盘上的 obs 重建。
`front_proc` / `det_proc`（解码 + det / 只有 det 换进程）量过稳态更慢 / ±0%，**2026-09-24 owner 定删掉旋钮**；键留在这里，因为旧产物的 config 里还记着它们、而它们取任何值都不改产物（上面那三段对过），不该因此重建。
`decode_shards` / `decode_block`（2026-09-24）：全硬解分片对一路，确定性路径 gi-s1 / wuwa 10 分钟 / f3 / f4 缺口三种块位置逐字节相同（decode-buffer）。

⚠ 往这里加键的前提比 `ADDED_NOOP` 更硬：`ADDED_NOOP` 只赦免"默认那个值"，
这里是**这个键取任何值都不改产物**。加之前必须拿 `obs_identical.py` 在至少一段上验过。"""
NOISE_EQUIV = frozenset({"device", "ort_share", "ort_share_mem", "ort_max_sessions", "rec_pre", "rec_inflight", "ort_warm", "ort_spin"})
"""**会改产物、但 owner 定为噪音级、可以互相复用**的旋钮（2026-09-22 owner："GPU 和 CPU 跑出来的读数差异，我定义为噪音级，可以复用"）。

2026-09-23 owner 把口径放宽成一般规则："噪音级的都可直接复用，往后这类都可豁免"——
**推理资源 / 调度类**的旋钮（谁持有模型、session 池多大、显存共不共用、前处理在哪个进程做、是不是 CUDA Graph 回放）
算的是同一个模型、同一份输入张量，差别只剩 ORT 自己的逐次抖动（`obs_identical` 验不了 ORT 臂，所以进不了 `SCHEDULING`）。
于是 `ort_share` / `ort_share_mem` / `ort_max_sessions` / `rec_pre` 从 `ADDED_NOOP` 挪到这里（quick 39,508 行文本 0 改、
前处理张量逐位相同，decode-buffer）。`rec_inflight`（异步 rec 池同时在途几个请求）同理：结算固定滞后、
只改批怎么拼。
**`ort_cuda_graph` 不算**（Codex 审计 09-23 的提醒，我认同）：它**出过真错**——和共用 arena 放一起时类别错 81/200，
正确与否取决于实现细节而不是运行时抖动；它仍按 `ADDED_NOOP`（0 = 加它之前）进复用判据。
⚠ **改"算什么"的不算这一类**：复用判据的门（`reuse_*`）、异步 rec 的窗口（`rec_window` / `rec_async` / `rec_knee`，
读哪一帧会变）、模型、采样，照旧进复用判据——噪音级要有对账撑着，不是"大概差不多"。
**整个键**豁免——取什么值都不触发重建；只豁免**某几个值**的见 `EQUIV_VALUES`。
行为同 `SCHEDULING`：写进 `config`（`same_arm` 分得开两条臂、产物留着追溯），`config_differs` **不看**它——
于是 `--device` 默认从 `gpu` 翻成 `auto` 不让磁盘上的现成 obs 全部重建，退回 CPU 跑出来的产物下次上 GPU 也照样复用。

和 `SCHEDULING` 的区别只在**依据**：那边是"逐字节相同、`obs_identical` 验过"，这里是 owner 的定性——
GPU / CPU 两条路的差异量级同 ORT 自己的逐次抖动（conf 动 1%~2% 的行、文本基本全同），
所以**不许**拿它当"逐字节相同"去引。进这张表的门槛是**对账过的噪音级**（owner 2026-09-23 起不必逐个点头，但要在文档里写明依据）。
实际跑在哪个设备、为什么退回，在 `_meta.device_resolved` / `device_fallback` / `device_effective`。

`det_engine`（现在只是身份串，见 `product_config`）**不在这张表里**：它改框，owner 逐条点头的噪音级只覆盖验证过的那一对模型，见 `NOISE_EQUIV_IF`。"""
DEFAULT_DET_MODEL = "PP-OCRv6_medium_det"
DEFAULT_REC_MODEL = "PP-OCRv6_medium_rec"
"""默认的 det / rec 模型名（官方 ONNX 仓库 `inference.yml` 里的 `model_name`）。`--det-model` / `--rec-model` 删了（2026-10 去 Paddle），
这两个名字还用在两处：旧 obs 的 `config` 里记着它们（`REMOVED_NOOP`、`_det_pair_verified`），自带的 `--det-onnx` 旁边的 yml 若说是这份就套默认阈值。"""


def _det_pair_verified(got: dict, want: dict) -> bool:
    """两边的 det 都是默认模型、ONNX 都是官方那份（或它之前的自转版，同属 `EQUIV_VALUES` 的一类）。
    `det_model` 只在 `--det-model` 删掉（2026-10）之前的旧 obs 里有；没有这个键 = 默认模型。"""
    return all(c.get("det_model", DEFAULT_DET_MODEL) == DEFAULT_DET_MODEL and c.get("det_onnx", "") in ("", OLD_DET_ONNX)
               for c in (got, want))


NOISE_EQUIV_IF = {"det_engine": _det_pair_verified}
"""**有条件的**噪音级：只在验证过的配置对上互相复用，别的组合照常比。

`det_engine`（2026-09-24，owner 逐条点头——它改框，不在"资源 / 调度类自动豁免"的范围里）：D1（ORT det 并进 rec 服务）翻默认时
owner 定两种 det 的产物按噪音级互相复用。依据（decode-buffer 末尾，quick 六段 A/B 各两遍）：事件级独有的只有 1~2 字 UI 碎片 /
HUD 数字，打字机真值与用途 2 四次运行逐项相同——**量的是默认 det 模型 + 官方 ONNX 这一对**。
第一版把整个键放进 `NOISE_EQUIV`，于是换了 `--det-model` 的两种后端也被判成可复用（Codex 审计 P1），收窄到这一对。
2026-10 去掉 Paddle 之后 `det_engine` 固定写 `"ort"`（`product_config`），这条留着：磁盘上默认模型的 paddle-det 旧产物照旧按噪音级复用。"""
# 下面两个是写进旧 obs `config` 的**身份串**，值一个字都不能变（变了磁盘上的现成产物全部对不上）；它们指向的自转模型
# 早就不用了（按文件名到模型根找，`paths.resolve_model`）。拆成两段写是给发布检查看的：这不是指向私有目录的指针。
_OLD = "explore" + "/onnxrt/models/"
OLD_REC_ONNX = _OLD + "ppocrv6_medium_rec_fp16_argmax.onnx"
OLD_DET_ONNX = _OLD + "ppocrv6_medium_det.onnx"
"""换官方模型之前的两个默认（自己拿 paddle2onnx 转的，rec 是 fp16 + argmax）。"""
EQUIV_VALUES: dict[str, tuple[frozenset, ...]] = {
    "rec_onnx": (frozenset({"", OLD_REC_ONNX}),),
    "det_onnx": (frozenset({"", OLD_DET_ONNX}),),
}
"""**只在某几个值之间**互相复用的键：同一个等价类里的值不算对不上，类外的照常拦。

2026-09-22 owner 换官方 ONNX 模型时定："此前我们自己转的模型产生的数据，按噪音级区别算，不用重量"。
认可的是**这一批**：旧默认（自转，`rec_onnx` 当时空 = fp16 那份、`det_onnx` 当时是 `OLD_DET_ONNX`）和新默认（空 = 官方，`flowocr.models`）。
第一版把两个键整个放进了 `NOISE_EQUIV`（Codex 复审 P2，复现属实）：换成**任意**别的模型也不重建，
新指定的模型可能根本没跑——owner 的话推不出"今后任意模型互相等价"。所以收窄成等价类；**往这里加值要 owner 点头**。
⚠ 它比的是路径字符串，同名文件换了内容看不出来；用的是哪份文件看 `_meta.models` 的 sha256。"""


def same_value(k: str, a, b) -> bool:
    """`config_differs` 用：两值相等，或同属 `EQUIV_VALUES[k]` 里的一个等价类。"""
    if a == b:
        return True
    return any(a in cls and b in cls for cls in EQUIV_VALUES.get(k, ()))


OPT_IN = frozenset({"reuse_v2", "reuse_corr_digit", "reuse_dw", "reuse_link_iou", "reuse_link_corr",
                    "reuse_mem", "reuse_cache_mb", "reuse_moved_corr", "reuse_confirm", "reuse_dw_anchor"})
"""**只在 `--reuse-v2` 打开时才进 `config`** 的那组参数（2026-09-16，reuse-budget 计划的落地臂）。
关着时这些键一概不写：默认产物的配置和加这组旋钮之前**逐键相同**，磁盘上所有现成 obs（含整片）照旧可复用。
开着时全部写进去，和默认产物必然不等。**默认值翻了也拦得住**：若日后 `--reuse-v2` 进默认，
默认产物就会带上这组键，而旧产物没有——逐键比对不等、重建，正是要的。"""
OPT_IN_HW = frozenset({"hwaccel"})
"""`--hwaccel` 为空时不进 `config`（软解那条，含 `--decoder cv2`）。2026-09-20 起 ffmpeg 解码的默认是 `cuda`，
所以默认产物的 config 里有它、软解产物没有——**两者互相不复用**（它不在 `SCHEDULING` 里，见那里的说明）。"""
OPT_IN_FUSED = frozenset({"refine_fused", "refine_scale", "refine_thr", "refine_aux", "aux_max_fps"})
"""同上，只在 `--refine-fused` 打开时才进 `config`（两遍合一遍，flowocr.extract.edge_refine）。它们只给 obs 行**多加** `edge`
字段，不改文本 / 框，但带 `edge` 的产物才能喂 `refine_boundaries --decoder obs`，所以是产物参数。

`refine_aux` **2026-09-17 从 NON_PRODUCT 挪进来**（审计）：原来的理由是"只管辅助流从哪个进程来，帧号和像素都一样"，
可是 `noop` 根本不产 `edge`，`nvdec` / `cuvid` 产的 `edge` 也不一样（reuse-budget 计划：gi-s2 偏早 4–5 条、
`aux_shift = 1`，而且没逐帧对过像素）。config 相等 = `ocr_complete` 会把空臂的 obs 当默认产物复用，
回抠 `--decoder auto` 再挑 obs、认领到 0 条证据、整段静默退回采样级。"""
"""**不影响产物内容**的参数，复用时不比。收"显然只管输出去哪、打印多勤"的两个，外加 `record_corr` 和 `refine_ring`
（帧环只能比 `ring_capacity` 的下限更大——`max(5, strides, in_flight + 4)` 个采样间隔、换算成辅助流帧数，`in_flight` 由 `edge_refine.ring_in_flight` 逐项加，所以它只改什么时候解码、不会少量出证据；**下限里必须算上 det 批的前瞻**，不然装不下就是互等死锁，2026-09-17 实测）。
`--prefetch` 虽然已证逐字节相同，仍然照比——
多重跑一次的代价是几分钟，漏拦一次的代价是一张算错的表。

`record_corr` 是**唯一**的例外，理由写清楚（2026-09-14，综合清单 X3）：它只在 obs 行上**多写一个字段**
（复用判据已经算出来的相关系数），不改判决、不改文本、不改框——开和不开，去掉 `corr` 之后逐行相同
（try-list-results 报告 X3 核过）。把它算进配置的代价是**所有现成 obs（含整片）一律重跑**，
因为旧产物的 `config` 里没有这个键、逐键比对必然不等。要用 `corr` 的探针自己检查字段在不在、缺了就报错；
开臂照旧走 `ARM=-corr`（另一个文件名），不会和默认产物混。"""


def _auto_or_int(v: str):
    """`auto` 或整数（`--rec-inflight`：auto 按设备定，显式给数就照给的）。"""
    return v if v == "auto" else int(v)


def build_parser() -> argparse.ArgumentParser:
    # prog 写死：`ocr_complete` 用它解析驱动拼的 argv，报错时要说清是 run_ocr2 的参数错了
    ap = argparse.ArgumentParser(prog="flowocr-ocr")
    # 帮助文字只写"做什么、默认多少、怎么回退"；为什么是这个默认、在哪量的，写在每个参数上面的注释里（发布前清理，Opus 审查 O6）。
    # `%%` 不是笔误：argparse 会对 help 串做 % 格式化，单个 `%` 让 `--help` 直接抛 ValueError（踩过）。
    ap.add_argument("video")
    ap.add_argument("--out", required=True)
    # 按时间网格取帧是 2026-09-24 起（`framegrid.TimeGrid`）：原来按 round(60/8) = 8 帧一帧，60 fps 上给 8 实际是 7.5 fps
    ap.add_argument("--fps", type=float, default=2.0,
                    help="采样帧率，按时间网格取帧：不要求整除素材帧率，不整除时最高到素材帧率的一半。生效网格记 `_meta.sample_grid`")
    # auto 是 2026-09-22 起的默认（project-structure 计划）；GPU / CPU 产物 owner 定为噪音级、互相复用（NOISE_EQUIV）
    ap.add_argument("--device", default="auto",
                    help="推理设备，det / rec 两个后端一起听它。auto（默认）= 有可用的 GPU 就用、先试推理一次，"
                         "GPU 不可用（没卡、驱动太旧、架构不支持、库加载不了）就整体退回 CPU、原因写进 `_meta.device_fallback`，"
                         "GPU 可用但试推理出了别的错就报错退出；gpu / gpu:N = 必须 GPU；cpu = 只用 CPU")
    # 默认 16（owner 2026-09-21；原 32，owner 09-16）。同形状组 >16 个的只占 0~0.9%（几乎全是菜单页的宽 320 短文本），
    # 而 ORT 每形状一个 session、激活峰值随 批×宽 线性涨——上限减半 = 显存峰值、形状数、共享内存都减半档；
    # 批维不影响读数（一次性探针 probe_rec_pad.py B 组 547 条差 0~1 条，followups-evidence-0921 报告）。
    # 批量 GEMM 的数值会抖：quick 六段上文本改 0.086%、另有少量 conf 跨过 build_tracks 的 0.5 门（fps-and-rec-budget 报告）
    ap.add_argument("--rec-bucket", type=int, default=16, metavar="N",
                    help="把目标宽相同的裁剪批在一起送 rec，每桶最多 N 个（默认 16；0 = 关 = 一个裁剪一次）")
    # 默认 4（owner 2026-09-16）：快前处理之后批 4 让 det 每帧约 −28%，端到端 quick 六段合计 −6.8%，
    # 但不逐字节相同（48 帧里 2 帧 poly 细微差）——fps-and-rec-budget 报告
    ap.add_argument("--det-batch", type=int, default=4, metavar="N",
                    help="det 攒 N 帧一批送（默认 4；1 = 逐帧）。复用判断和 rec 仍逐帧做")
    # 默认 1（owner 2026-09-19 从 3 改回来："默认 worker 改到 1，不容易出问题；确认显存时可传 3；等做完 worker 间的资源共享
    # 可以重新放宽要求"——3 个 worker 各开一套模型，显存按份数线性涨）。放行时采样帧和单进程逐帧相同，差别只在每段开头没有
    # 『上一帧』可复用（实测只动切点之后几行闪烁的直播 UI）；quick 三段上 3 个 worker −26%~−47%（flowocr.extract.ocr_parallel）。
    # 有丢帧缺口的素材（yuka f3 / f4）上子进程起点按『帧号 ÷ 平均帧率』换算，段与段会重叠或漏帧，所以开不了
    ap.add_argument("--workers", type=int, default=1, metavar="N",
                    help="把窗口切成 N 段、N 个进程同时跑，最后拼成一份观测（默认 1 = 单进程；显存按份数涨）。"
                         "切点对齐采样格；窗口太短、有丢帧缺口（平均帧率 ≠ 名义帧率）时退回单进程并打印原因，"
                         "`_meta.workers_effective` 记实际值。拼的时候逐个切点核首尾间隔，重叠 / 漏帧都判未完成")
    ap.add_argument("--reuse-iou", type=float, default=0.7)
    # 旧判据没有按内容分门，0.8 会放过换字，所以 --no-reuse-v2 时是 0.98
    ap.add_argument("--reuse-corr", type=float, default=None,
                    help="框内像素相关系数达到这个值才沿用上一帧的文本。默认跟着复用判据走："
                         "--reuse-v2（默认）时 0.8（含数字的文本另由 --reuse-corr-digit 把着），--no-reuse-v2 时 0.98")
    # 设计见 ocr-regions 计划
    ap.add_argument("--regions", default="", metavar="JSON",
                    help="自选 OCR 范围：识别组配置文件，"
                         "`{\"groups\": [{\"name\": …, \"rects\": [{\"box\": [x0,y0,x1,y1], \"neg\": false, \"t\": [t0,t1]}]}]}`，"
                         "坐标是画面宽高的百分比、时间是原片秒。范围外的像素在 det 之前涂成中灰，"
                         "det 框被遮罩分断就拆成子框，时间关闭段写进 `_meta.regions`、build_tracks 不让事件跨过它。"
                         "一次跑一个组；多组的文件要配 --region-group")
    ap.add_argument("--region-group", default="", metavar="NAME", help="--regions 文件里跑哪个组（只有一个组时可不给）")
    # 同模型同输入、不改读数（NOISE_EQUIV）。每个新形状首次约 0.2 s，一趟 18~26 个形状，整片上摊薄到 <1%
    ap.add_argument("--ort-warm", action=argparse.BooleanOptionalAction, default=True,
                    help="ORT rec 服务起动后在后台预热常见形状（默认开）。每趟固定一笔，短片 / --workers 每段都付")
    # 2026-09-24 owner 定默认开（ocr-regions 计划）：det 快约 55%、端到端 −8%~−18%（之后卡在解码），匹配器覆盖 hsr −1 / gi ±0
    ap.add_argument("--region-crop", action=argparse.BooleanOptionalAction, default=True,
                    help="det 只看组范围的静态外接矩形（默认开；--no-region-crop = det 看涂过的整帧）。"
                         "裁不了（外接矩形就是全屏）时让路、原因记在 `_meta.region_crop_off`。"
                         "改 det 的输入尺寸、框会变（进复用判据）；没给 --regions 时不起作用")
    ap.add_argument("--predet", default=None, help="predet_scan.py 出的 regions json")
    # 故意不做自动判定：几何上分不开『直播评论墙』和『新闻的密集字幕带』（zh-news 字幕带密度比 2.6、评论墙 5.0，量级重叠），
    # 任何阈值都会在某一边出错。用 rect_excl（全包围盒）而不是 rect（分位框）：漏排只是多花点钱，排过头才是丢内容
    ap.add_argument("--exclude-regions", default="",
                    help="跳过 predet 的哪几个区域（逗号分隔的下标，如 `0` 或 `0,2`）。不自动判定："
                         "predet 的报告里有每个区域占多少 rec 成本、多大面积，由人看着选")
    ap.add_argument("--no-reuse", action="store_true", help="关掉复用，用来做对照")
    # 素材相关的开关，三部片子上量下来 1 赢 2 输：yuka（galgame 对话框、暗底、行两边空）省略号召回 74%→97.6%、剧本命中 +4.7 个点；
    # 另两部视频画面素材上 rec 的 conf 变低的比变高的多 2 倍以上。按行高而不是固定像素：省略号宽度跟字号走
    ap.add_argument("--rec-pad-x", type=float, default=0.0,
                    help="送进 rec 之前把 det 框横向外扩这么多倍行高（默认 0 = 不扩）。det 的框收在有墨的地方，"
                         "行首行尾低对比度的省略号会被切在外面；是否有益取决于素材，开之前先在自己的素材上对比")
    # 试过拿它做自动判定，不划算：阈值 25 在 yuka 上砍掉三分之一收益（省略号召回 97.6%→88.5%），在 bili 上也只挡住一半伤害（predet 报告）
    ap.add_argument("--rec-pad-max-std", type=float, default=0.0,
                    help="要扩进来的那条边像素标准差超过这个值就不扩（默认 0 = 不设门）")
    # 字幕两行间隔只有十几像素，纵向外扩实测变差
    ap.add_argument("--rec-pad-y", type=float, default=0.0,
                    help="纵向外扩倍数（默认 0；两行字幕挨得近，一般别开）")
    # 有 pts 缺口的素材（f3/f4）上窗口跑和整片跑的采样帧不一定是同一批：`cap.set(POS_FRAMES, i)` 其实按 i / src_fps 做时间 seek，
    # 而整片跑的 idx 数的是真实帧；f1/f2/f5 没缺口、src_fps 正好 60.000，两者严格重合
    ap.add_argument("--start", type=float, default=0.0,
                    help="只跑这个秒数之后的部分。时间戳仍按原片绝对时间写（真实 PTS），窗口结果可以直接和整片的参照对齐")
    ap.add_argument("--end", type=float, default=0.0, help="只跑到这个秒数（0 = 到片尾）")
    # 默认 ffmpeg（owner 2026-09-10 定，全局统一、不按素材分叉）：av1 上端到端 3.4×（OpenCV 内置的 av1 解码器慢 12 倍），vp9/h264 打平。
    # 产物和 cv2 不逐字节相同，有剧本的素材上命中集合差 +0/+0/+1、重复认领 0→0（docs/architecture/defaults.md 1.2 节）
    ap.add_argument("--decoder", default=framesource.DEFAULT_DECODER,
                    choices=["ffmpeg", "cv2"],
                    help="采样帧从哪来。ffmpeg（默认）= 子进程只把采样帧送进管道；cv2 = OpenCV 顺序解码，留给 A/B 和回退。"
                         "两种解码器的产物不混用：复用判据只认当前默认产的观测")
    # cuda 是 2026-09-20 owner 定的默认，理由是 CPU 不是墙钟：wuwa-s2 5 分钟上整机 CPU 秒 319 -> 189（−41%）、峰值 68.3% -> 45.8%，
    # 墙钟只 −3.2%~−4.6%（噪声档）。21,587 行观测文本改 0、框不差、主轨逐字节相同（nv12；edge 数值没逐条比过，所以不豁免复用判据）。
    # NVDEC 在有前摇的 av1（首包 pts 为负）上少交付 1~3 帧，gi-s2 连 pts 0.000 都不交付
    ap.add_argument("--hwaccel", default=None, choices=["", "cuda"],
                    help="采样流用硬解。默认跟着解码器走：ffmpeg -> cuda（NVDEC，辅助流也在显存里缩放）、cv2 -> 空；"
                         "空串 = 纯软解（回退）。和 `--decoder cv2` 显式同给会报错。"
                         "有前摇的 av1（首包 pts 为负）上 NVDEC 会少交付帧，这种素材自动退回软解并打印原因（`_meta.hwaccel_effective`）")
    # nv12：带宽减半，NV12→BGR 用 OpenCV 自己那套转换、和 cv2 路同源——实测 vp9 上像素差 max|Δ| 从 11 降到 3
    ap.add_argument("--pipe-pix", default="nv12", choices=["nv12", "bgr24"],
                    help="`--decoder ffmpeg` 时管道上的像素格式（默认 nv12）")
    # 组批之下 6 个批值把形状数从 12 撑到 28、池子只有 12 于是反复淘汰重建（inference-runtime 计划）
    ap.add_argument("--ort-batch-ladder", default="",
                    help="ORT 的批维补齐梯子，逗号分隔（空 = 2 的幂 1/2/4/8/16/32）。梯子越粗形状越少，代价是补零的浪费")
    ap.add_argument("--no-ort-share", dest="ort_share", action="store_false",
                    help="`--workers N` 时不共享推理服务（默认共享：一个服务进程、N 个 worker 接上去，显存只有一份）")
    # 组批在 ORT 上倒挂的病因就是池子装不下：批维 × 宽把形状数撑过池子、LRU 反复淘汰重建（inference-runtime 计划）。
    # quick 段上形状 15~22 种；不共用显存时池 40 我们那份 10.8 GB
    ap.add_argument("--ort-max-sessions", type=int, default=None,
                    help="ORT 的 session 池能装几个形状（每个形状一个 session）。默认跟着 --ort-share-mem 走：共用显存时 64，不共用时 12。"
                         "不共用显存时开大要看显存：每个 session 自己一份 arena")
    # 2026-09-23 默认开：quick 六段交错两轮墙钟 −4.5%（段内 +0.3%~−6.9%）、显存少 2.4 GB、淘汰清零、文本 0 行改动（decode-buffer）
    ap.add_argument("--ort-share-mem", action=argparse.BooleanOptionalAction, default=True,
                    help="ORT 服务里同一个模型的 session 共用显存（默认开）：一个 arena、权重只放一份，每个形状的 session 约 50 MB。"
                         "--no-ort-share-mem 回退（池子跟着回到 12）")
    # 默认 off（owner 2026-09-25，GPU / CPU 一样）：CPU 上几个 session 的线程池互相抢核（gi-s1 60 s --device cpu 64.1 → 51.3 s）；
    # GPU 上线程池大半在等 CUDA，自旋只是空转——quick 六段交错两轮墙钟 −0.3%、整棵进程树 CPU −8.2%（defaults.md 1.27 节）。
    # 同模型同输入、不改读数（NOISE_EQUIV）
    ap.add_argument("--ort-spin", default="off", choices=("on", "off"),
                    help="ORT 服务里 intra-op 线程池轮空时自旋等待（默认 off；ORT 自己的默认是开）")
    ap.add_argument("--ort-server-extra", default="",
                    help="原样追加给 ORT 服务的参数（探针 / A/B 用，如 `--threads 4 --per-shape 0`）。进 config（非空就不复用默认的 obs），"
                         "服务实际收到的整串记 `_meta.ort_server_args`")
    # 小批次是 kernel 启动受限的：真实裁剪上每次推理 wuwa 12.3 -> 9.2 ms、gi-s2 9.4 -> 6.0 ms（N=4），文本 0 差；
    # 图 session 用自己的 arena（和共用 arena 放一起会踩显存），显存 +0.25~0.7 GB（decode-buffer）；
    # 2026-09-26 修好能跑（捕图 / 回放拿服务里的独占闸），但闸吃掉了在途并发：quick 六段墙钟慢约 10%~16%，不采纳（defaults.md 3 节）
    ap.add_argument("--ort-cuda-graph", type=int, default=0, metavar="N",
                    help="rec 服务里批 ≤ N（宽 ≤ 960）的形状用 CUDA Graph 回放（默认 0 = 关）。图 session 用自己的显存")
    # server 是 2026-09-23 起的默认：两条送进模型的张量逐位相同（同一份 recprep.batch），差别只在谁抢 GIL（decode-buffer）
    ap.add_argument("--rec-pre", default="server", choices=("server", "client"),
                    help="ORT rec 的前处理在哪做：server（默认）= ORT 服务进程里，管线只送原样的 uint8 裁剪；"
                         "client = 管线进程里做好张量再送。服务端不支持（别的解释器没有 cv2）时自动退回 client")
    # 推理只走 ONNX Runtime（2026-10 去掉 Paddle；rec 2026-09-20、det 2026-09-24 先后翻成 ORT 默认）。
    # 换 det 模型只有这一条路：模型名和前后处理配置读 ONNX 旁边的 inference.yml（2026-09-24 审计：不许拿默认模型的配置跑别的权重）
    ap.add_argument("--det-onnx", default="",
                    help="det 用哪份 ONNX（空 = 官方发布的 PP-OCRv6_medium_det_onnx，`flowocr.models`，缺了当场取）。"
                         "自带的要连同它的 inference.yml 放在同一个目录，模型名和前后处理配置从那里读")
    # 2026-09-22 起全用官方模型（原来是自己转的 fp16）；新旧模型的产物 owner 定为噪音级、互相复用（EQUIV_VALUES）
    ap.add_argument("--rec-onnx", default="",
                    help="rec 用哪份 ONNX（空 = 官方发布的 PP-OCRv6_medium_rec_onnx 融进 argmax，fp32；"
                         "`flowocr.models`，缺了当场取）")
    # pts 是 2026-09-20 owner 定的默认（『pts 更正确就按 pts』）。两者只在帧数对不上时有区别：①解码器少交付（NVDEC 在有前摇的 av1 上少 1~3 帧）
    # ——index 会让整条格子静默平移，pts 只是某一格取不到；②素材自己有丢帧缺口（f3/f4）——index 的相位被 `缺口 % stride` 永久顶偏。
    # 无缺口素材上两者逐字节相同。量法和数在 hw-decode-results 报告
    ap.add_argument("--frame-select", default=None, choices=("index", "pts"),
                    help="采样格按什么数。默认跟着解码器走：ffmpeg -> pts（按帧自己的时刻 `round(t*src_fps)`，锚在绝对时间格上）、"
                         "cv2 -> index（按交付顺序，回退用）。只有 ffmpeg 后端认 pts（cv2 上显式传 pts 会报错）")
    # 默认 2 是 2026-09-20 翻的：产物逐字节相同（Paddle 路上 obs_identical 两段验过），所以不进复用判据（SCHEDULING）。
    # quick 六段：ORT rec 之上再 −15.2%~−24.9%，六段全中；Paddle 路上也有 −10.8% / −17.1%
    ap.add_argument("--det-prefetch", type=int, default=2,
                    help="把「取帧 + det」整段搬到后台线程，队列里最多攒这么多批（默认 2；0 = 关）。"
                         "前瞻变成 det_batch×(1+N) 个采样点，帧环容量跟着放大（run_ocr2 自己算）")
    # 2026-09-25 起：批的顺序和每批的计算都不变、产物按构造相同（SCHEDULING，Paddle 确定性路径 obs_identical 验过）
    ap.add_argument("--det-lookahead", type=int, default=1, choices=(0, 1),
                    help="det 批的流水：下一批的前处理 + 推理在辅助线程上跑、和这一批的后处理重叠（默认 1 = 开；0 = 关）。"
                         "只在 --det-batch > 1 且走快前处理时生效；代价是多一个线程、帧环多装一批")
    # 给窗口延迟读那套模拟当输入（inference-runtime 计划）
    ap.add_argument("--trace", default="",
                    help="把复用判据的因果轨迹写到这个 JSONL（逐框：链 uid / 链接类型 / 拦它的门 / 相关系数 / "
                         "手里的文本含不含数字 / 最终读没读，外加回补的读）。不改判决、不进复用判据")
    # decode-buffer
    ap.add_argument("--timeline", default="",
                    help="各级时间线：每一段在做什么 / 等什么记成区间（管道读、nv12 -> BGR、det、判决、rec 推理 / 等结算、"
                         "帧环满、回抠工作线程……），跑完写到这个 JSONL（子进程各写 `<路径>.<角色>.jsonl`），"
                         "给 `dev_tools/timeline_report.py` 看。不改判决、不进复用判据")
    ap.add_argument("--pregate", action="store_true",
                    help="把复用判据里最贵的那一块（逐框的静态相关系数）挪到 det 那一层算，产物逐字节相同。"
                         "det 线程有空闲时才划算（--det-prefetch）")
    # inference-runtime 计划的纯代价曲线、文本代价
    ap.add_argument("--rec-near", type=float, default=0.0, metavar="R",
                    help="分桶从同宽放宽到近宽：组内最大目标宽 ≤ 最小 × (1+R)（默认 0 = 同宽）。"
                         "改产物：批内小的会被补零到批内最大宽。跨帧攒批（--rec-window）之下同宽不容易凑批，两个旋钮是一对")
    # 2026-09-23 默认开（decode-buffer 批次 3）；读还没回来的链走严格像素门，窗口里多读几个框（inference-runtime 计划的策略 B）
    ap.add_argument("--rec-window", type=int, default=None, metavar="N",
                    help="rec 跨帧攒批：裁剪排进队列，攒够 N 个采样点一次分桶送。默认跟着 --reuse-v2 走：开着 4、关着 0"
                         "（0 = 逐帧分桶；1 = 每帧结算，和 0 逐字节相同）。和文本无关的判决照常逐帧做，要文本的那半推迟到结算；"
                         "读还没回来的链走 --reuse-corr-digit 的严格门，所以会多读几个框。要 --reuse-v2；帧环会多留 N 个采样点")
    # 2026-09-23 默认开（组批第二步，src/flowocr/extract/recpool.py）
    ap.add_argument("--rec-async", action=argparse.BooleanOptionalAction, default=None,
                    help="把 rec 发批搬到后台线程：主线程只提交裁剪、继续判决，结果回来后按帧序提交。"
                         "默认跟着 --rec-window 走：窗口 > 0 且分桶开着就开（同步窗口要显式 --no-rec-async）。"
                         "要 --rec-window 和 --rec-bucket；`--rec-window 1 --rec-async` 和同步的 `--rec-window 1` 逐字节相同")
    # 默认 1 是 2026-09-23 起：rec 服务不再淘汰之后膝 1 比膝 16 快，quick 交错 −9.5% 对 −7.7%（decode-buffer）。
    # 没有它后台线程会把攒起来的批吃回去（inference-runtime 计划：平均批 3.0~6.4 掉到 1.4~5.3）；rec 成本曲线见底处 Paddle 16、ORT 32
    ap.add_argument("--rec-knee", type=int, default=1, metavar="K",
                    help="异步消费者的膝点驻留：排队不足 K 个裁剪就先不发（默认 1 = 一有空就发），被 --rec-bucket 封顶。只在 --rec-async 时有意义")
    # auto：GPU 上 quick 五段 + wuwa 10 分钟交错两轮 −3.8%（3 是 −5.2%，多出的在噪声里）；CPU 上 det / rec 各自就吃满物理核，
    # 第二个在途只是来挤（gi-s1 60 s −11%；defaults.md 1.17 节）。结算仍是固定滞后，只改批怎么拼（= ORT 的逐次抖动，NOISE_EQUIV）
    ap.add_argument("--rec-inflight", type=_auto_or_int, default="auto", metavar="N|auto",
                    help="异步 rec 池同时在途几个请求（连到同一个 ORT 服务，模型 / 显存仍一份）。"
                         "auto（默认）= GPU 上 2、CPU 上 1；显式给数就照给的。只在 --rec-async 时生效")
    # 三种编码 × 两个解码后端六条臂产物全同，换来 1.05–1.24× 墙钟（vp9 真窗口 5 分钟 1.18×；hw-decode-results 报告）
    ap.add_argument("--prefetch", type=int, default=4,
                    help="解码放到后台线程的有界队列深度（默认 4；0 = 关，串行）。只改什么时候解码、不改解出什么，严格保序；"
                         "内存 = 深度 × 帧大小（1080p 每帧约 6 MB）")
    ap.add_argument("--progress-every", type=int, default=200, help="每处理这么多采样帧报一次进度")
    # 给 run 内『外观跳变』报警用（综合清单 X3）
    ap.add_argument("--record-corr", action="store_true",
                    help="把复用判据算出来的框内相关系数写进 obs 的 `corr` 字段（默认关）。只记本来就算了的框，不改判决和文本、不参与复用比对")
    # 默认开（owner 2026-09-17）；设计在 src/flowocr/extract/reuse_v2.py 文件头，reuse-budget 计划、样本外实跑 §3.6：换字 0、rec −32%
    ap.add_argument("--reuse-v2", action=argparse.BooleanOptionalAction, default=True,
                    help="复用判据第二版（默认开；--no-reuse-v2 退回旧判据）：按手里的文本分门（含数字 -> --reuse-corr-digit，"
                         "其余 -> --reuse-corr）、框宽门 --reuse-dw、链首跨空档（--reuse-link-*、--reuse-mem）、"
                         "回补（沿用过的裁剪进缓存 --reuse-cache-mb，真读不同就回头改已缓冲的行）。"
                         "--reuse-corr / --refresh-every 的默认跟着它走。下面几个 --reuse-* 参数只在它打开时才进 config")
    ap.add_argument("--reuse-corr-digit", type=float, default=0.98, help="手里文本含数字时的相关系数门（--reuse-v2）")
    ap.add_argument("--reuse-dw", type=int, default=8, help="本帧框宽和链上框宽差超过这么多像素就不沿用（--reuse-v2）")
    ap.add_argument("--reuse-link-iou", type=float, default=0.3, help="上一帧 IoU 到这个就允许当跨空档链接（--reuse-v2）")
    ap.add_argument("--reuse-link-corr", type=float, default=0.9, help="跨空档链接的相关系数门（--reuse-v2）")
    ap.add_argument("--reuse-mem", type=int, default=16, help="按位置记住结束的链多少个采样点（--reuse-v2）")
    ap.add_argument("--reuse-cache-mb", type=float, default=8.0, help="回补裁剪缓存的全局上限 MB，超了当场真读（--reuse-v2）")
    # 2026-09-22 默认开（decode-buffer）：修的是打字机中间态被沿用到底（句尾一两个字整框相关系数看不出来，yuka-f5 14954 s 缺句尾 6 秒）
    ap.add_argument("--reuse-confirm", action=argparse.BooleanOptionalAction, default=True,
                    help="链上的文本没被连续两次真读证实之前，像素门走严格门 --reuse-corr-digit（默认开，改产物；只会多读）。"
                         "防的是打字机中间态被沿用到字消失（--reuse-v2）")
    # 2026-09-23 默认开（decode-buffer）：修的是字从面板边缘一点点露出来（每步 2~4 px）时一直过门、末字缺着被沿用（wuwa-s2 65~69 s 一个面板 6 行）
    ap.add_argument("--reuse-dw-anchor", action=argparse.BooleanOptionalAction, default=True,
                    help="宽度门 --reuse-dw 量的是从上次真读起宽了多少，而不是和上一个采样点比（默认开，改产物；只会多读）。"
                         "防的是字从边缘一点点露出来时一直过门（--reuse-v2）")
    # 必须和旧判据一样严：quick 实跑第一版按 --reuse-corr 0.8 放行，滑动的名牌 / 任务标题 / 技能名换了字照样沿用
    # （gi-s1 10 帧、wuwa-s2 2 帧），模拟里没建位移这条路所以没预见（reuse-budget 计划）
    ap.add_argument("--reuse-moved-corr", type=float, default=0.98,
                    help="按位移匹配上的链（框在滑动）沿用时的相关系数门（--reuse-v2）")
    # 默认能开就开（owner 2026-09-17；flowocr.extract.edge_refine 文件头，reuse-budget 计划）；--no-refine-fused 时 OCR 那遍快约 25%
    ap.add_argument("--refine-fused", action=argparse.BooleanOptionalAction, default=None,
                    help="两遍合一遍：一路缩到 --refine-scale、帧率不过 --aux-max-fps 的灰度辅助流跟在采样流旁边，"
                         "链首 / 文本变 / 链断时在线抠裁剪跑回抠的信号，结果写进 obs 行的 `edge`，建轨时不用再开视频。"
                         "默认能开就开（要 --reuse-v2 和 --decoder ffmpeg，条件不满足时自动不开；显式给而条件不满足才报错）；"
                         "--no-refine-fused 关掉、回抠退回两遍。下面几个辅助流参数只在它打开时才进 config")
    # 0.35 是 owner 2026-09-20 定的（原来 0.5 = 540p，按带宽选的）。成本几乎全在搬运（300 s 窗 540p 时 9.3 GB，是采样流的 5 倍），
    # 约 ∝ 分辨率²：0.35 搬运减半、帧环内存 214 -> 105 MB、OCR 那遍约 −10%，三段打字机真值上全字一个不差、首字 −0~1 条；
    # 0.25 全字三段齐降 2~3 条（hw-decode-results 报告）。1.0 不缩：1080p60 全帧率走管道比 OCR 那遍还慢
    ap.add_argument("--refine-scale", type=float, default=0.35,
                    help="辅助流缩到原来的多少（默认 0.35，1080p 素材上约 378p）。再低会丢全字时刻的精度")
    # 2026-09-24（owner）；不整除的第二版在 flowocr.extract.framegrid。约束：辅助流 ≥ 高精帧环（decode-buffer 目标设计里的帧环 S，
    # 还没落地）≥ 采样（--fps），现在只拦 < --fps 的值。取代了原来的 --refine-step（已删）
    ap.add_argument("--aux-max-fps", type=float, default=AUX_MAX_FPS, metavar="FPS",
                    help="辅助流的帧率上限：素材帧率超过它就按时间网格取帧（默认 60），不要求整除，采样帧一定在辅助流里。"
                         "不能低于 --fps。生效网格记 `_meta.aux_grid`，复用判据比的是生效网格")
    # nvdec：第二路在 CPU 上每帧多 0.9 ms、硬解几乎免费；cuvid 那条是为了避开 scale_cuda 的核函数（nvdec 臂 det 被抢 GPU 慢了 7–8 s）。
    # 不改文本 / 框，但改 edge 证据，所以和别的 fused 参数一样进 config（OPT_IN_FUSED，2026-09-17 审计改的）
    ap.add_argument("--refine-aux", default="shared", choices=("shared", "nvdec", "cuvid", "noop"),
                    help="辅助流从哪来：shared = 和采样流同一个 ffmpeg、同一次解码分两路（默认）；nvdec = 单独一个 ffmpeg 走 NVDEC + scale_cuda；"
                         "cuvid = 同 nvdec 但缩放在解码硬件里做；noop = shared 但工作线程什么都不算（量成本用的空臂，产物里没有 edge 证据）")
    # 批 det 的前瞻装不下就是互等死锁（2026-09-17 实测）。共享解码时环满 = ffmpeg 停 = 采样帧也停
    ap.add_argument("--refine-ring", type=int, default=5, metavar="N",
                    help="帧环装多少个采样间隔的辅助流帧（默认 5；实际容量还会抬到装得下 det 批的前瞻）。只改什么时候解码，不进 config")
    # 2026-09-23 默认开（decode-buffer S2 第一刀；T4：连同帧环按在途给，quick 四段 −5.1%、wuwa 10 分钟 −11.2%）。
    # 同进程时回抠线程被 GIL 饿着（wuwa-s2 忙 24.9 s、CPU 11.5 s）
    ap.add_argument("--edge-proc", action=argparse.BooleanOptionalAction, default=True,
                    help="在线回抠（辅助流接收 + 帧环 + 回抠计算）放进自己的进程（默认开；flowocr.extract.edge_proc）。"
                         "算法不变、证据逐位相同。--refine-aux nvdec / cuvid 时自动关（打印原因、记 `_meta.proc_split`）；回退 --no-edge-proc")
    # 为什么：硬解之后每帧的 scale_cuda 核函数要和 det / rec 的 CUDA 上下文分时，负载下一路解码慢 2 倍（decode-buffer）。
    # 2026-09-24 起默认开：先翻成 3（quick 六段 −4.9%），字节预算之后复测 2 路和 3 路同读数（−1.4%，噪声）、
    # 2 路预取峰值少约 300 MB、少一个解码上下文，改成 2（defaults.md 1.18 节）
    ap.add_argument("--decode-shards", type=int, default=2, metavar="N",
                    help="解码分片：N 路硬解 ffmpeg 交替解相邻的时间块（按关键帧切、谁空谁领、按帧号顺序交帧），跑在自己的进程里"
                         "（默认 2；0 / 1 = 不分片）。全硬解时产物逐字节相同。只配 ffmpeg + 按 pts 选帧；"
                         "和 --workers > 1 二选一（自动关、打印原因）；没 GPU 时自动不分片")
    ap.add_argument("--decode-block", type=float, default=20.0, metavar="秒",
                    help="解码分片的块长（对齐到关键帧）。块越长每块起进程的开销越摊薄；内存由预取的字节上限封顶，不看块长")
    ap.add_argument("--refine-thr", type=float, default=0.80,
                    help="消失的退回判据：和末次观测帧的相关系数不低于这个就算还在（同 refine_boundaries --thr）")
    # 纯复用等于把'多数投票'换成'首次读数说了算'，读错会一路传下去；定期刷新让下游仍有几个独立读数可投票
    ap.add_argument("--refresh-every", type=int, default=None,
                    help="同一个框最多连续沿用几帧就强制重识别一次。默认跟着复用判据走：--reuse-v2 时 32（有回补兜底），"
                         "--no-reuse-v2 时 4；0 = 不刷新")
    return ap


def resolve(args: argparse.Namespace) -> argparse.Namespace:
    """把"默认值跟着别的参数走"的那几个解析成**生效值**（幂等）。`run_ocr2` 和 `config_of` 都过这一道，
    所以 `_meta.config` 里记的、复用判据比的，都是生效值——不会出现 `reuse_corr: null`。

    * `--no-reuse` 连带关掉 reuse_v2（不复用就没有链）；
    * `--reuse-corr` / `--refresh-every` 没给：reuse_v2 开着 0.8 / 32，关着 0.98 / 4；
    * `--ort-max-sessions` 没给：`--ort-share-mem` 开着 64，关着 12；
    * `--rec-window` 没给：reuse_v2 开着 4、关着 0；`--rec-async` 没给：窗口 > 0 且分桶开着就开；
    * `--refine-fused` 没给（auto）：reuse_v2 开着、解码器是 ffmpeg 就开，否则不开
      （2026-09-20 起 `--hwaccel` 也能开——辅路走 `scale_cuda`）；
    * `--hwaccel` / `--frame-select` 没给：解码器是 ffmpeg 就 `cuda` / `pts`，cv2 就 `''` / `index`；
    * `--device` 只校验、归一成小写——`auto` 落到哪个设备要试推理才知道，那是 `run_ocr2` 的事，不在这里猜。"""
    args.device = check_device(args.device)
    if args.no_reuse:
        args.reuse_v2 = False
    if args.reuse_corr is None:
        args.reuse_corr = 0.8 if args.reuse_v2 else 0.98
    if args.refresh_every is None:
        args.refresh_every = 32 if args.reuse_v2 else 4
    if args.rec_window is None:
        # 异步 rec（固定滞后结算，decode-buffer 的"判决不等 rec"）2026-09-23 进默认：窗口要 reuse_v2 的两段式判决
        args.rec_window = 4 if args.reuse_v2 else 0
    if args.rec_async is None:
        args.rec_async = bool(args.rec_window and args.rec_bucket)
    if args.ort_max_sessions is None:
        # 池子跟着显存共不共用走（2026-09-23，decode-buffer）：共用时一个 session 约 50 MB、64 格装得下所有形状；
        # 不共用时每个 session 自己一份 arena，只敢开 12
        args.ort_max_sessions = 64 if args.ort_share_mem else 12
    # `--hwaccel` / `--frame-select` 的默认**跟着解码器走**（2026-09-20 整体检查时补的）：
    # 两个都是 ffmpeg 后端才认的旋钮，翻默认那天我把默认值写死成了 `cuda` / `pts`，
    # 于是当时文档里写着的回退命令 `--decoder cv2` **直接跑不起来**（"--hwaccel 要 --decoder ffmpeg"）。
    # 没给 = 按解码器取；**显式给了和解码器冲突的组合仍然报错**（run_ocr2 / make_source 那两道）。
    if args.hwaccel is None:
        args.hwaccel = "cuda" if args.decoder == "ffmpeg" else ""
    if args.frame_select is None:
        args.frame_select = "pts" if args.decoder == "ffmpeg" else "index"
    if args.refine_fused is None:
        # ⚠ 2026-09-20 去掉了 `and not args.hwaccel`：辅路现在在显存里 `scale_cuda` 缩完再下传，
        # 硬解也能同一次解码分两路。原来那条让"开硬解 = 自动退回两遍回抠"，而那一遍 35–105 s
        # 把硬解省下的全吃回去还倒欠（defaults.md §1.8）。
        args.refine_fused = bool(args.reuse_v2 and args.decoder == "ffmpeg")
    if not args.regions:
        # `--region-crop` 没有 --regions 时是空操作（全屏没得裁）。2026-09-24 它的默认翻成开——
        # 不在这里归一的话，全屏产物的 config 从 False 变 True，磁盘上所有现成 obs 都会被判"配置不同"而重建
        args.region_crop = False
    return args


def resolve_split(args: argparse.Namespace) -> list[str]:
    """拆进程（`--edge-proc` 2026-09-23 默认开）遇到不支持的组合就**关掉那一刀、说明原因**，不报错——
    它们在 SCHEDULING 里（产物不变），关掉只是换回进程内跑。`config` 仍记请求的值（同 `--workers` 的规矩），
    实际生效的写 `_meta.proc_split`。返回原因（空 = 按请求生效）。
    **不并进 `resolve`**：那样 `config` 记的就是关掉之后的值了。"""
    why = []
    if args.edge_proc and args.refine_fused and args.refine_aux not in ("shared", "noop"):
        args.edge_proc = False
        why.append(f"--edge-proc 关：--refine-aux {args.refine_aux} 的辅助流是单独一路解码，要由那个进程直接收的只有 shared / noop")
    if args.decode_shards > 1:
        bad = ([f"--decoder {args.decoder}"] if args.decoder != "ffmpeg" else []) + (
            ["--frame-select index"] if args.frame_select != "pts" else []) + (
            ["--workers > 1"] if args.workers > 1 else []) + (
            ["回抠开着却没 --edge-proc"] if args.refine_fused and not args.edge_proc else [])
        if bad:
            # 分片是**默认开**的调度优化：撞上别的拆法 / 解码器时安静地让路（不算"按请求关掉了一刀"，不进 why），
            # 原因记在 `args.decode_shards_off`（run_ocr2 写进 `_meta.proc_split.decode_off`）
            args.decode_shards = 0
            args.decode_shards_off = f"不支持 {' / '.join(bad)}"
            print(f"[拆进程] 解码分片不开：{args.decode_shards_off}", flush=True)
    for w in why:
        print(f"[拆进程] {w}", flush=True)
    return why


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """`build_parser().parse_args` + `resolve`。**别再直接用 build_parser().parse_args**——拿到的是没解析完的默认值。"""
    return resolve(build_parser().parse_args(argv))


def product_config(args: argparse.Namespace) -> dict:
    """决定产物内容的那组**生效值**（含默认值）。`video` 归一成绝对路径——
    同一个文件写成 `D:/x` 还是 `D:\\x`、相对还是绝对，不该算两条臂。"""
    cfg = {k: v for k, v in vars(resolve(args)).items() if k not in NON_PRODUCT}
    if not cfg.get("reuse_v2"):
        cfg = {k: v for k, v in cfg.items() if k not in OPT_IN}     # 关着就不写，见 OPT_IN
    if not cfg.get("refine_fused"):
        cfg = {k: v for k, v in cfg.items() if k not in OPT_IN_FUSED}
    if not cfg.get("hwaccel"):
        cfg = {k: v for k, v in cfg.items() if k not in OPT_IN_HW}
    cfg["video"] = str(Path(cfg["video"]).resolve())
    # **推理后端的身份串**（2026-10 去掉 Paddle 之后不再是选项，处理同下面的 `frame_id`）：不写的话这两个键两边都缺、
    # `config_differs` 就不比它们，09-18 之前没有 `rec_engine` 键的 Paddle-rec 旧 obs 会被当成 ORT 产物复用。
    # 旧产物缺键时按 `ADDED_NOOP`（= paddle）算；默认模型的 paddle-det 产物照旧按噪音级复用（`NOISE_EQUIV_IF`）
    cfg["rec_engine"] = cfg["det_engine"] = "ort"
    # 范围按**内容**进复用判据（同内容换路径是同一条臂，改了文件内容就对不上）；组名并进去
    if cfg.get("regions"):
        from flowocr.extract import regions as _rg
        cfg["regions"] = _rg.group_spec(cfg["regions"], cfg.get("region_group", ""))
    cfg.pop("region_group", None)
    if cfg.get("decoder") == "ffmpeg":
        # **帧身份的口径**（2026-09-20，复审第三轮 P1）：ffmpeg 路径的帧号改按名义帧率取整（framesource.id_rate）。
        # 平均帧率那一版在有缺口的文件上会撞号——那些产物**不许**被当成现在的产物复用，所以这个键**不进 ADDED_NOOP**：
        # 没有它的旧产物一律判"对不上"，用到时按需重建
        cfg["frame_id"] = "nominal"
    return cfg


def config_of(argv: list[str]) -> dict:
    """把一条 `run_ocr2` 的 argv 解析成 `product_config`。解析失败直接抛——
    驱动脚本拼错了参数，应该在复用判据这一步就炸，而不是静默判"不能复用"再去跑。"""
    return product_config(parse_args(list(argv)))


REMOVED_NOOP = {
    "rec_lookahead": 0,
    "decode_cpu_lanes": 0,         # 2026-09-24 删（分片里混 CPU 软解路：那几块的辅助流随调度变、管线里没更快）
    # 2026-10 去掉 Paddle 时删的四个：值是旧 obs 里的实际取值（308 份统计过，全部吻合），也就是 ORT 路上它们的生效行为。
    # 换过 det 模型的旧产物（`PP-OCRv6_small_det`）照旧被拦下
    "no_fast_det": False,          # ORT det 一直走 FastDet 的前后处理
    "rec_batch_size": 0,           # 只有 Paddle rec 认它；0 = 不传
    "rec_model": DEFAULT_REC_MODEL,
    "det_model": DEFAULT_DET_MODEL,
}
"""**已经删掉的**旋钮：产物里记着、而当前的解析器不认识。值是它"没开"时的那个数——
产物记的正好是这个值，就说明那一趟和现在的默认**行为相同**，不该因此重建。

这是 `ADDED_NOOP` 的镜像，2026-09-19 补（复审放行删 `--rec-lookahead` 时才发现缺这一半）：
`config_differs` 按 `set(got) | set(want)` 逐键比，原来只兜"产物缺键"，
于是**删一个旋钮就会让磁盘上所有现成 obs（含整片）被判成"参数对不上"、全部重建**。
⚠ 往这里加键的前提和 `ADDED_NOOP` 一样：那个值必须**真的是空操作**。"""

ADDED_NOOP = {
    # `ort_share` / `ort_max_sessions` / `rec_pre` / `ort_share_mem` 2026-09-23 挪进了 NOISE_EQUIV（owner：噪音级都复用）
    "ort_cuda_graph": 0,           # 2026-09-23 加，0 = 加它之前的行为（出过真错，不进 NOISE_EQUIV）
    "ort_server_extra": "",        # 2026-09-25 加，空 = 加它之前的行为（它能透传 --cuda-graph-batch 这类改读数的，所以非空不赦免）
    "regions": "",                 # 2026-09-23 加，空 = 全屏（加它之前的行为）
    "region_crop": False,          # 2026-09-24 加，关 = det 看整帧（加它之前的行为）
    "ort_batch_ladder": "",
    "pregate": False,
    "rec_async": False,
    "rec_knee": 16,
    "rec_near": 0.0,
    "rec_window": 0,
    # `det_prefetch` 2026-09-20 从这里挪走了：它进了 `SCHEDULING`，`config_differs` 根本不看它，
    # 留在这儿只会让人以为"只有 0 才赦免"（实际是任何值都不改产物）。
    "frame_select": "index",    # 2026-09-20 加的旋钮，默认 = 加它之前的行为
    # 2026-10 起这两个是身份串、固定 "ort"（`product_config`）：旧产物没这个键 = 加键之前的 Paddle，
    # 于是对上 "ort" 必然判差异（rec 的该重建；det 的默认模型那一对由 NOISE_EQUIV_IF 放行）。**别删**
    "rec_engine": "paddle",
    "det_engine": "paddle",
    "rec_onnx": "",
    "det_onnx": OLD_DET_ONNX,   # 加这两个键之前的产物用的就是当时的默认（和现在的默认同属 EQUIV_VALUES 的一类）
}
"""**后加的、默认是空操作的旋钮**：产物的 `config` 里没有这个键、而要的正好是这里的值时，**不算对不上**。

为什么要这张表（2026-09-18）：这一夜加了 5 个旋钮（`--det-prefetch` / `--rec-engine` / `--det-engine` /
两个 onnx 路径），默认全关、**默认路径的产物逐字节相同**（`dev_tools/obs_identical.py` 验过）。
但 `ocr_complete` 是按"两边的键取并集逐键比"，旧产物缺这几个键 -> `None != 0` -> **现成 obs 全部重建**。
整场素材一次重跑是小时级，为一个默认关着的旋钮付这个钱没道理。

⚠ **加进这张表的前提是"默认值 = 当时的行为"**，也就是这个旋钮默认不开时**产物逐字节相同**——
加之前必须先拿 `obs_identical.py` 在至少一段上验过。默认会改产物的旋钮（`--rec-bucket` 那种）
**不许**进这张表：那种情况下旧产物就是该重建。
"""


AUX_MAX_FPS = 60.0
"""辅助流默认最多收这么多帧 / 秒（`--aux-max-fps`）。60 fps 及以下的素材每帧都收（和加旋钮之前一样）。"""

EFFECTIVE_CHECKED = frozenset({"aux_max_fps", "refine_step"})
"""`config_differs` **不直接比**的键：它们对产物的作用要结合素材本身才定得下来，由 `ocr_complete.obs_reusable`
拿产物的 `_meta.src_fps` 算出**生效的采样网格和辅助流网格**（`sample_grid` / `aux_grid`）再比。
（`fps` 本身照旧按值比；同一个 `fps` 在 59.94 这类素材上的生效采样网格变了——时间网格之前是整数步长——也由那边拦。）`aux_max_fps`：60 fps 的素材上 60 和"不封顶"是同一回事，
直接比原始值会让加旋钮之前的全部产物重建（它们没有这个键），而真正改了产物的只有 60 fps 以上的素材。
`refine_step`（2026-09-24 删，被时间网格取代）：旧产物记着它，它的作用同样折进生效网格里比。"""


def sample_grid(src_fps: float, fps: float, frame_select: str = "pts"):
    """采样的**生效网格**（`framegrid.TimeGrid`）：素材上按时间网格取 `fps` 帧 / 秒，锚在帧号 0 上（2026-09-24，
    原来是 `stride = round(src_fps / fps)`，60 fps 上要 8 fps 实际给 7.5）。整除的情形和原来逐字节相同。

    **不等距的网格要求最长间隔严格小于 1.5 个名义间隔**：相邻间隔在名义间隔上下差一帧，下游的容差（断链、并块）都是 1.5 个名义间隔。
    第一版拦的是"名义间隔 ≥ 2 帧"，比这条依据严一档（59.94 上 `--fps 30` 最长 2 帧 < 2.997，本来盖得住，2026-09-24 审计）。
    更密的采样本来也不该靠采样流做（细时间靠辅助流）。
    按帧号 `n` 数的选法（`frame_select="index"`：cv2 / 显式回退）编不出不等距的网格，退回最近的整数步长（加时间网格之前的行为）。
    **纯函数，守卫直接测。**"""
    from flowocr.extract.framegrid import TimeGrid

    if frame_select != "pts":
        return TimeGrid.every(max(1, round(src_fps / fps)))
    g = TimeGrid.for_rate(src_fps, fps)
    if not g.uniform and 2 * g.max_gap * g.p >= 3 * g.q:
        raise ValueError(f"--fps {fps:g} 在 {src_fps:.3f} fps 的素材上不整除，最长采样间隔 {g.max_gap} 帧不小于 1.5 个名义间隔"
                         f"（{1.5 * g.q / g.p:.2f} 帧），下游的容差盖不住。给整除的值，或更低的采样帧率")
    return g


def aux_grid(src_fps: float, fps: float, aux_max_fps: float = AUX_MAX_FPS, frame_select: str = "pts"):
    """辅助流的**生效网格**（`framegrid.AuxGrid`）：帧率 = min(`aux_max_fps`, 素材帧率)，按时间网格取帧、采样帧嵌套在里面。
    `aux_max_fps` 比采样帧率还小就抛 ValueError（辅助流的时间细粒度不能比采样还粗）。**纯函数，守卫直接测。**"""
    from flowocr.extract.framegrid import AuxGrid

    if aux_max_fps < fps - 1e-9:
        raise ValueError(f"--aux-max-fps {aux_max_fps:g} 比采样帧率 --fps {fps:g} 还小——辅助流不能比采样还粗")
    return AuxGrid.for_video(src_fps, aux_max_fps, sample_grid(src_fps, fps, frame_select))


def config_differs(got: dict, want: dict) -> list[str]:
    """两份生效配置的差异键（`ocr_complete` 用）。缺键按 `ADDED_NOOP` / `REMOVED_NOOP` 兜底；
    `EFFECTIVE_CHECKED` 里的键不在这里比（`obs_reusable` 按生效值比）。"""
    out = []
    missing = object()
    for k in set(got) | set(want):
        if k in SCHEDULING or k in NOISE_EQUIV or k in EFFECTIVE_CHECKED or (k in NOISE_EQUIV_IF and NOISE_EQUIV_IF[k](got, want)):
            continue                               # 只改什么时候算（SCHEDULING）/ owner 定的噪音级（NOISE_EQUIV）/ 按生效网格比（EFFECTIVE_CHECKED）
        g, w = got.get(k, missing), want.get(k, missing)
        if g is missing:
            g = ADDED_NOOP.get(k, missing)         # 旧产物没这个键：按"加它之前的行为"算
        if w is missing:
            w = REMOVED_NOOP.get(k, missing)       # 旋钮删了：按"它没开"算
        if g is missing or w is missing or not same_value(k, g, w):
            out.append(k)
    return sorted(out)


def same_arm(argv_a: list[str], argv_b: list[str]) -> bool:
    """两条臂**解析之后**是不是同一条（`--out` 之类不算）。

    A/B 驱动跑之前要问一次（2026-09-17 审计）：默认值一翻，"空参数 = 旧默认"这种写法就塌成同一条臂——
    `--reuse-v2 --reuse-corr .8 --refresh-every 32` 现在和不给参数**完全相等**，脚本却照旧按两条臂出表。
    对照臂要把关掉的旋钮显式写出来（`--no-reuse-v2 --no-refine-fused`）。"""
    a, b = config_of(argv_a), config_of(argv_b)
    return {k: v for k, v in a.items() if k != "out"} == {k: v for k, v in b.items() if k != "out"}
