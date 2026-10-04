"""ORT 路要的 ONNX 模型：**全用官方发布的**（owner 2026-09-22），钉版本、验 sha256、缺了就取。

PaddlePaddle 自己在 Hugging Face 上发了 PP-OCRv6 的 ONNX（Apache-2.0，`LICENSES/PP-OCRv6-NOTICE.md`）。
以前用的是我们自己拿 paddle2onnx 转的那几份（`tools/export_*_onnx.sh`，落在 onnxrt 实验目录的 `models/` 下，
按约定随时可删、发行包里也没有）；换成官方的之后，源码安装和发行包取的是同一份、谁都能从上游核对。
owner 同日定：**旧模型产的数据和新模型的区别按噪音级算**（`ocr_args.NOISE_EQUIV` 里有 `det_onnx` / `rec_onnx`），不重量。

**我们只动一处**：rec 的图末尾加 `ArgMax` + `ReduceMax` 两个节点（CTC 贪心解码只要这两样），**权重一个字节不动**。
不融的话 worker 每批要把 `[batch, T, 18710]` 的 fp32 logits 整个拷回主机（批 32 约 191 MB，recort 文件头），
融了只回两个 `[batch, T]` 小张量——融合是 ORT 那条路快的主要原因（inference-runtime 第 2 条）。
**不再转半精度**：官方只发 fp32；端到端 fp32 + 融合 53.7 s 对 fp16 + 融合 54.9 s（yuka-f5 10 分钟，同一处），
半精度没买到墙钟，还在 ORT 1.26 / 1.27 的 CPU EP 上打不开（project-structure 记录）。

放在哪：`paths.models_root()`（`FLOWOCR_MODELS` > 数据根的 `models/`），每个仓库一个子目录。
下载走 `HF_ENDPOINT`（默认 `https://huggingface.co`，国内可设镜像）。先写 `.part` 再改名，中途断了不留半个文件。

    python -m flowocr.models fetch            # 默认要的（rec + det，默认都走 ORT）
    python -m flowocr.models fetch rec        # 只取点名的
    python -m flowocr.models list
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from flowocr import paths


@dataclass(frozen=True)
class Model:
    repo: str
    rev: str
    """Hugging Face 上的**提交号**（不是分支名）：`resolve/<rev>/` 下的内容不可变。"""
    files: dict[str, str] = field(default_factory=dict)
    """文件名 -> sha256。下载完逐个核，对不上就删掉、报错。"""
    fuse_argmax: bool = False

    @property
    def dir(self) -> Path:
        return paths.models_root() / self.repo.split("/")[-1]

    @property
    def onnx(self) -> Path:
        """管线真正加载的那个文件。"""
        return self.dir / ("inference_argmax.onnx" if self.fuse_argmax else "inference.onnx")


MODELS = {
    "rec": Model("PaddlePaddle/PP-OCRv6_medium_rec_onnx", "50c7eacafc52fa7bcf4194e8cd08e46f8558504b", {
        "inference.onnx": "9c09abf0957f7968c7586464b7397b84ad2387a0497a351af40e9acc71b673ba",
        "inference.yml": "991b700facf5b50a7de193468207d5f4255b538dde0d312ae3b7c7a9b6873129",
    }, fuse_argmax=True),
    "det": Model("PaddlePaddle/PP-OCRv6_medium_det_onnx", "61323801669c338b7891481ec7bac61ce31b576a", {
        "inference.onnx": "eb13b44b25bb36f89528b68720af8a61d9cf381176107f465db1757b65d086e1",
        "inference.yml": "7298d5ead546584af2504d03355f881ac7a7bc0eb1e282d3e159277c1d0af871",
    }),
}
"""rec 的字表就用它自己仓库里的 `inference.yml`（`recort` 按模型所在目录找），模型和字表出自同一个提交。
2026-09-22 核过：它和 PaddleX 模型目录（`official_models/PP-OCRv6_medium_rec`）里那份 `inference.yml` **逐字节相同**（18,710 类）。
rec 的 `inference.onnx` 的 sha256 和 Hugging Face 上 LFS 记的 oid 一致；其余三个是取下来时算的（提交号钉住，内容本就不可变）。"""

DEFAULT = ("rec", "det")
"""默认配置要的：rec 和 det（2026-09-24 起 det 也走 ORT，D1）。原来只列 rec，翻默认时没跟着改，
断网的机器上 `python -m flowocr.models fetch` 之后默认配置仍缺 det 的 ONNX（2026-09-24 Codex 审计）。"""


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dst: Path, want_sha: str) -> None:
    tmp = dst.with_name(dst.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "flowocr"})
    print(f"[模型] 下载 {url}", flush=True)
    h = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            f.write(chunk)
            h.update(chunk)
    got = h.hexdigest()
    if want_sha and got != want_sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{url} 的 sha256 是 {got}，钉的是 {want_sha}——没装，别往下跑")
    os.replace(tmp, dst)


def fuse_argmax(src: Path, dst: Path) -> None:
    """rec 图末尾加 `ArgMax` / `ReduceMax`：输出从 `[batch, T, C]` 的 logits 变成 `cls`（int64）+ `prob`（float），形状 `[batch, T]`。
    只加节点、改输出声明，**不碰权重和已有节点**。和原来 一次性探针 `export_rec_onnx.sh` 里那段是同一个做法。"""
    import onnx
    from onnx import TensorProto, helper
    m = onnx.load(str(src))
    g = m.graph
    y = g.output[0]
    g.node.append(helper.make_node("ArgMax", [y.name], ["cls"], axis=-1, keepdims=0))
    g.node.append(helper.make_node("ReduceMax", [y.name], ["prob"], axes=[-1], keepdims=0))
    dims = [d.dim_param or d.dim_value for d in y.type.tensor_type.shape.dim][:2]
    del g.output[:]
    g.output.extend([helper.make_tensor_value_info("cls", TensorProto.INT64, dims),
                     helper.make_tensor_value_info("prob", TensorProto.FLOAT, dims)])
    tmp = dst.with_name(dst.name + ".part")
    onnx.save(m, str(tmp))
    os.replace(tmp, dst)


def _src_mark(m: Model) -> Path:
    """派生文件旁边记"它是从哪份源文件派生的"（源文件的 sha256）。"""
    return m.onnx.with_name(m.onnx.name + ".src-sha256")


def derived_stale(m: Model) -> bool:
    """派生的 argmax 图要不要重做：不在、没有来源记录、或来源不是现在钉的那份源文件。
    ⚠ 只看"派生文件在不在"是错的（Codex 复审）：表里换了提交号 / 源文件重新下载之后，旧的派生图照样被拿去用。"""
    if not m.fuse_argmax:
        return False
    mark = _src_mark(m)
    return not (m.onnx.is_file() and mark.is_file()
                and mark.read_text(encoding="utf-8").strip() == m.files["inference.onnx"])


def ensure(m: Model) -> Path:
    """一个模型就位：源文件缺了 / sha 对不上就（重新）下载，派生图过期就重做。返回管线要加载的那个文件。"""
    m.dir.mkdir(parents=True, exist_ok=True)
    base = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    for fn, want in m.files.items():
        p = m.dir / fn
        if p.is_file() and (not want or sha256(p) == want):
            continue
        _download(f"{base}/{m.repo}/resolve/{m.rev}/{fn}", p, want)
    if derived_stale(m):
        fuse_argmax(m.dir / "inference.onnx", m.onnx)
        _src_mark(m).write_text(m.files["inference.onnx"] + "\n", encoding="utf-8")
        print(f"[模型] {m.onnx.name}：融进 argmax（{m.onnx.stat().st_size / 2**20:.1f} MB）", flush=True)
    return m.onnx


def fetch(name: str) -> Path:
    """按名字取一个模型（`ensure(MODELS[name])`）。"""
    return ensure(MODELS[name])


_ready: dict[str, str] = {}
"""本进程里核过的（每个进程核一次源文件 sha256，76 MB 约 0.15 s；run_ocr2 一趟会问好几次）。"""


def path(name: str) -> str:
    """管线用的入口：默认模型的路径，**缺了 / 过期了就当场取**（同 PaddleX 首次运行下它自己的模型）。
    取不到（断网、sha 对不上）就抛，报错里带手动取的命令。"""
    if name in _ready:
        return _ready[name]
    m = MODELS[name]
    try:
        _ready[name] = str(ensure(m))
    except Exception as exc:
        raise RuntimeError(f"默认 {name} 模型不在（或核不上）{m.onnx}，自动下载失败：{exc}。"
                           f"联网后跑 `flowocr-models fetch {name}`（镜像用 HF_ENDPOINT），或用 GitHub Release 上的模型包 `flowocr-models install`") from exc
    return _ready[name]


def meta(p: str) -> dict:
    """写进 obs `_meta.models` 的那一条：文件名 + sha256——复用判据只比路径字符串，**同名文件换了内容它看不出来**，这里记下内容。"""
    q = Path(p)
    return {"file": q.name, "sha256": sha256(q) if q.is_file() else None}


def pack(dst: Path) -> list[str]:
    """离线包（发版时由维护者打，挂在 GitHub Release）：默认模型的**源文件**（不含派生的 argmax 图，`install` 之后首次用时就地融）
    + `LICENSES/` 里的许可与改动说明。只打核过 sha256 的文件；源码 checkout 里才有 `LICENSES/`。"""
    lic = paths.CODE_ROOT / "LICENSES"
    if not lic.is_dir():
        raise SystemExit(f"找不到 {lic}：离线包要在源码 checkout 里打（要带上模型的许可说明）")
    names = []
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_STORED) as z:
        for n in DEFAULT:
            m = MODELS[n]
            for fn, want in m.files.items():
                src = m.dir / fn
                if not src.is_file() or sha256(src) != want:
                    raise SystemExit(f"{src} 不在或 sha256 对不上：先 `flowocr-models fetch {n}`")
                z.write(src, f"{m.dir.name}/{fn}")
                names.append(f"{m.dir.name}/{fn}")
        for f in sorted(lic.iterdir()):
            z.write(f, f"LICENSES/{f.name}")
    return names


def install(src: Path) -> list[str]:
    """把 `pack` 打的离线包装进模型根：只认表里的模型文件、逐个核 sha256，对不上就不写；`LICENSES/<文件名>` 一并放进模型根。
    名字之外的一律拒绝（`LICENSES/../x` 这类穿出模型根的也算）；写文件先写 `.part` 再改名，中途断了不留半个文件。"""
    known = {f"{m.dir.name}/{fn}": (m, fn, want) for m in MODELS.values() for fn, want in m.files.items()}
    root = paths.models_root()
    done = []
    with zipfile.ZipFile(src) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename
            data = z.read(info)
            if name in known:
                m, fn, want = known[name]
                if hashlib.sha256(data).hexdigest() != want:
                    raise SystemExit(f"{name} 的 sha256 对不上：包坏了，重新下一份")
                dst = m.dir / fn
            else:
                head, sep, leaf = name.partition("/")
                if not (head == "LICENSES" and sep and leaf and leaf not in (".", "..")
                        and not any(c in leaf for c in '/\\:')):
                    raise SystemExit(f"包里有不认识的文件 {name}：不是 `flowocr-models pack` 打的包")
                dst = root / "LICENSES" / leaf
            dst.parent.mkdir(parents=True, exist_ok=True)
            part = dst.with_name(dst.name + ".part")
            part.write_bytes(data)
            os.replace(part, dst)
            done.append(str(dst))
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flowocr-models", description="取 / 列官方 ONNX 模型")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="下载并核对（已在就跳过）")
    f.add_argument("names", nargs="*", help=f"{' / '.join(MODELS)}，默认 {' '.join(DEFAULT)}")
    sub.add_parser("list", help="列出模型根里有什么")
    sub.add_parser("install", help="装离线包（GitHub Release 上的模型 zip）：核过 sha256 再放进模型根").add_argument("zip")
    sub.add_parser("pack", help="打离线包（维护者发版用）").add_argument("zip")
    a = ap.parse_args(argv)
    if a.cmd == "install":
        for p in install(Path(a.zip)):
            print(p)
        print(f"装好了：{paths.models_root()}（第一次跑会就地融 argmax 图，不联网）")
        return 0
    if a.cmd == "pack":
        print("\n".join(pack(Path(a.zip))))
        print(f"-> {a.zip}（{Path(a.zip).stat().st_size / 2**20:.1f} MB）")
        return 0
    if a.cmd == "fetch":
        bad = [n for n in a.names if n not in MODELS]
        if bad:
            ap.error(f"不认识的模型 {bad}（有 {list(MODELS)}）")
        for n in a.names or DEFAULT:
            print(f"{n}: {fetch(n)}")
        return 0
    print(f"模型根：{paths.models_root()}")
    for n, m in MODELS.items():
        src_ok = all((m.dir / fn).is_file() for fn in m.files)
        state = ("在" if m.onnx.is_file() and not derived_stale(m) else
                 "源文件在，argmax 图第一次用时就地融（不联网）" if src_ok else "缺")
        print(f"  {n}: {m.repo}@{m.rev[:8]} -> {m.onnx}（{state}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
