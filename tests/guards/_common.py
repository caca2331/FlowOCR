"""守卫共用的工具：路径、`check` / `raises`、合成样本。各领域模块 `from guards._common import *`。"""
from __future__ import annotations

import importlib
import subprocess
import sys
import inspect
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dev_tools"))
sys.path.insert(0, str(ROOT / "src"))     # 正式包（tools/ 里的 shim 自己也会把它放进来；这里显式给守卫用）

from flowocr.artifacts import evalkit  # noqa: E402
from flowocr.artifacts import srtio  # noqa: E402

FAILED: list[str] = []


def raises(fn, exc: type[BaseException] = Exception) -> bool:
    """`fn()` 抛不抛。**"守卫真的会响"和"正常路径没坏"是两件事**，两边都要测。"""
    try:
        fn()
    except exc:
        return True
    return False


def _every(k: int):
    """每 k 帧一帧的采样网格（取帧层现在收网格、不收整数步长；`framegrid` 只有标准库）。"""
    from flowocr.extract.framegrid import TimeGrid
    return TimeGrid.every(k)


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def fake_widths(pairs, known=None):
    """守卫里不起 ffmpeg 量字宽：每个字宽 1（字号 1 时）。"""
    return {**(known or {}), **{(f, ln): 1.0 * len(ln) for f, t in pairs for ln in t.split("\n")}}


def render_overlay(path: Path, items, doc: dict, look: str = "dev", typewriter: bool = True, fade: bool = True,
                   quiet: bool = True, **look_changes):
    """叠加两步走一遍：阶段 3 写字幕稿（`path` 旁边的 `.script.ass`），阶段 4 生成 `path`。返回阶段 3 的计数。
    `look_changes` 覆盖阶段 4 预设的字段（比如 `label=False`）。字宽用 `fake_widths`。"""
    from dataclasses import replace
    from unittest.mock import patch

    from flowocr.output import export, script
    from flowocr.typeset import assfile, core
    path = Path(path)
    draft = path.with_name(path.stem + ".script.ass")
    with patch.object(export, "ink_widths", fake_widths):
        n, _widths = script.export_script(draft, items, doc, typewriter, fade)
        ts = core.Typesetter(replace(core.LOOKS[look], **look_changes))
        d = assfile.loads(draft.read_text(encoding="utf-8"))
        ts.run(d)
    path.write_text(d.dumps(), encoding="utf-8")
    return n


SRT_OK = """1
00:00:01,000 --> 00:00:02,500
第一条
第二行

2
00:00:03,000 --> 00:00:04,000
3

"""


def _shard_behaviour_checks(dsh, fsx) -> None:
    """`ShardedSource` 的**行为**（2026-09-24 两份审计：原来只测了三条 ValueError，而它是默认开的路径）。
    假 `FfmpegSource`：按块交 `[lo, hi)` 里的格（源缺的 `missing` 不交、解码器吞的 `eaten` 不交），辅助流逐帧推进 sink；
    `source_has_frames` 按"源里有 = 不在 missing"答。窗口 [0, 240)、stride 30、4 块 × 60 帧。"""
    import threading as _th
    from types import SimpleNamespace as NS
    from unittest import mock

    import numpy as np

    class Fake:
        missing: set = set()
        eaten: set = set()
        aux_cut: dict = {}          # 块起点 -> 辅助流推到哪一帧为止（模拟半路断）
        delay = 0.0
        probe_fails = False

        def __init__(self, video, **kw):
            self.kw, self.grid_off, self.grid_skipped = kw, {}, 0
            self.stopped_early, self.stderr_tail, self.preroll_ticks = False, [], 0
            self.aux = NS(join=lambda t=None: None, th=NS(is_alive=lambda: False), misaligned="") if kw["aux"] else None

        def __iter__(self):
            lo, hi, st = self.kw["start_idx"], self.kw["end_idx"], self.kw["grid"].step
            sink = self.kw["aux"]["sink"] if self.kw["aux"] else None
            g0 = -(-lo // st) * st
            got = [i for i in range(g0, hi, st) if i not in Fake.missing and i not in Fake.eaten]
            if got:                                   # 块自己核"起点 -> 最后一帧"之间的缺格（同真的 FfmpegSource）
                self.grid_skipped = len([i for i in range(g0, got[-1], st) if i not in got])
            cut = Fake.aux_cut.get(lo, hi)
            for i in range(lo, hi):
                if i in Fake.missing:
                    continue
                if sink is not None and i < cut:
                    sink.push(i, np.zeros(4, np.uint8))
                if i in got:
                    if Fake.delay:
                        time.sleep(Fake.delay)
                    yield i, i / 60.0, np.zeros(8, np.uint8)
            if sink is not None:
                sink.close()

        def close(self):
            pass

    class Sock:
        def __init__(self, fail_after=None):
            self.n, self.fail_after = 0, fail_after

        def settimeout(self, t):
            pass

        def sendall(self, b):
            if self.fail_after is not None and self.n >= self.fail_after:
                raise ConnectionResetError("假的：回抠进程断了")
            self.n += 1

        def close(self):
            pass

    import time

    def run(nblocks=4, lanes=("hw", "hw", "hw"), aux=True, sock=None, conn_err=None, budget_mb=1024, blocks=None,
            timeout=10.0):
        """跑一遍，返回 (交出的帧号, pts 表, 源, 抛的异常, 卡没卡住)。"""
        pts: list = []
        bl = blocks if blocks is not None else [(k * 60, (k + 1) * 60, None if k == 0 else k + 0.0005) for k in range(nblocks)]
        remote = NS(url="tcp://127.0.0.1:1", pts_put=pts.append) if aux else None
        s = dsh.ShardedSource("v", lanes=list(lanes), blocks=bl, start_idx=0, end_idx=nblocks * 60, grid=_every(30), src_fps=60.0,
                              width=4, height=4, hwaccel="cuda", aux_remote=remote, budget_mb=budget_mb)
        out, err = [], []

        def consume():
            try:
                for idx, _t, _f in s:
                    out.append(idx)
            except BaseException as exc:          # noqa: BLE001
                err.append(exc)

        cc = (mock.patch.object(dsh.socket, "create_connection", side_effect=conn_err) if conn_err is not None
              else mock.patch.object(dsh.socket, "create_connection", return_value=sock or Sock()))
        with mock.patch.object(dsh.framesource, "FfmpegSource", Fake), cc, \
                mock.patch.object(dsh.framesource, "probe_source_frames",
                                  side_effect=lambda v, idxs, fps: (None if Fake.probe_fails
                                                                    else [i for i in idxs if i not in Fake.missing])):
            th = _th.Thread(target=consume, daemon=True)
            th.start()
            th.join(timeout)
        stuck = th.is_alive()
        if stuck:
            s.close()
        return out, pts, s, (err[0] if err else None), stuck

    def reset():
        Fake.missing, Fake.eaten, Fake.aux_cut, Fake.delay, Fake.probe_fails = set(), set(), {}, 0.0, False

    reset()
    out, pts, s, e, stuck = run()
    keys = [p[1] * 60 for p in pts if p is not dsh.framesource.QUEUE_SENTINEL]
    check("ShardedSource 正常路径：4 块 3 路，采样帧按帧号全交、辅助流 240 帧按帧号顺序转发、末尾有 pts 结束标记",
          not stuck and e is None and out == list(range(0, 240, 30)) and [round(k) for k in keys] == list(range(240))
          and pts and pts[-1] is dsh.framesource.QUEUE_SENTINEL and s.grid_skipped == 0 and s.advanced == 240,
          (stuck, e, out, len(keys)))

    reset()
    Fake.eaten = set(range(0, 60))
    out, pts, s, e, stuck = run()
    check("ShardedSource：**首块整块没交**、源里有 -> 硬解那路抛 HwaccelMismatch（Codex 审计 P1：原来 complete=True、缺格 0）",
          not stuck and isinstance(e, fsx.HwaccelMismatch), (stuck, e, out))
    out, pts, s, e, stuck = run(lanes=("sw", "sw"))
    check("ShardedSource：首块没交、软解路 -> RuntimeError（不当成素材缺口放过去）",
          not stuck and isinstance(e, RuntimeError) and not isinstance(e, fsx.HwaccelMismatch), (stuck, e))
    Fake.probe_fails = True
    out, pts, s, e, stuck = run(lanes=("sw", "sw"))
    check("ShardedSource：软解路块尾缺格、**源探不出来** -> 放行、记 probe_unknown（2026-09-24 审核：探失败 ≠ 真吞帧）",
          not stuck and e is None and s.probe_unknown == 1, (stuck, e, s.probe_unknown))
    out, pts, s, e, stuck = run()
    check("ShardedSource：硬解路块尾缺格、源探不出来 -> 保守当吞帧（整段退软解）",
          not stuck and isinstance(e, fsx.HwaccelMismatch), (stuck, e))

    reset()
    Fake.missing = set(range(0, 60))
    out, pts, s, e, stuck = run()
    check("ShardedSource：首块整块是**素材缺口**（源里也没有）-> 放行，缺格记 2 个",
          not stuck and e is None and out == [60, 90, 120, 150, 180, 210] and s.grid_skipped == 2, (stuck, e, out, s.grid_skipped))

    reset()
    Fake.missing = set(range(100, 150))          # 缺口从第 1 块尾（100~119，没有格）跨进第 2 块头（缺格 120）
    out, pts, s, e, stuck = run()
    check("ShardedSource：跨接缝的缺口**只数一次**（块尾归这里核、块头归块自己核；原来接缝核从上一帧数到下一帧、和块自己的重了）",
          not stuck and e is None and out == [0, 30, 60, 90, 150, 180, 210] and s.grid_skipped == 1, (e, out, s.grid_skipped))

    reset()
    Fake.eaten = {210}
    out, pts, s, e, stuck = run()
    check("ShardedSource：**末块尾**源里有却没交 -> 照样抛（只是不数进缺格）",
          not stuck and isinstance(e, fsx.HwaccelMismatch), (stuck, e, out))
    reset()
    Fake.missing = {210}
    out, pts, s, e, stuck = run()
    check("ShardedSource：末块尾是素材缺口 -> 放行、不数缺格（同单路：窗口尾归覆盖率那道门）",
          not stuck and e is None and out[-1] == 180 and s.grid_skipped == 0 and s.advanced == 210, (e, out, s.grid_skipped))

    for nb in (3, 4):
        reset()
        out, pts, s, e, stuck = run(nblocks=nb, conn_err=OSError("假的：连不上"))
        check(f"ShardedSource：辅助流转发**连不上**（{nb} 块、3 路）-> 及时抛，不静默少辅助流、不卡住（Codex 审计 P1）",
              not stuck and isinstance(e, RuntimeError) and "连不上" in str(e), (nb, stuck, e))
        out, pts, s, e, stuck = run(nblocks=nb, sock=Sock(fail_after=70))
        check(f"ShardedSource：辅助流**写到一半断了**（{nb} 块）-> 抛 RuntimeError、结束标记照发",
              not stuck and isinstance(e, RuntimeError) and "转发失败" in str(e)
              and pts and pts[-1] is dsh.framesource.QUEUE_SENTINEL, (nb, stuck, e))

    reset()
    Fake.aux_cut = {60: 80}
    out, pts, s, e, stuck = run()
    check("ShardedSource：某一块的辅助流半路断了（没盖到这块最后一个采样帧）-> 这一块的错，不当成读完了",
          not stuck and isinstance(e, RuntimeError) and "半路断了" in str(e), (stuck, e))

    reset()
    Fake.delay = 0.001
    out, pts, s, e, stuck = run(nblocks=6, budget_mb=0.00002)   # 20 字节：几乎一有东西就超
    check("ShardedSource：字节预算小到几乎为 0 也不互等（有消费者在等的块永远放行），帧照样按序交全",
          not stuck and e is None and out == list(range(0, 360, 30)) and s.peak_mb * (1 << 20) < 6 * 256, (stuck, e, out, s.peak_mb))   # 全部 6 块共 1,536 字节：从没攒满过

    # 主流交到块的最后一个格之后，辅助流不再按预算挡（审核第三轮：FfmpegSource 读完主流会自己收摊、等 10 s 就杀，
    # 这时还被预算挡着的辅助帧会让 ffmpeg 卡在写上被杀掉）。直接测 `_admit`：预算 0、没人在等
    s0 = dsh.ShardedSource("v", lanes=["hw"], blocks=[(0, 60, None)], start_idx=0, end_idx=60, grid=_every(30), src_fps=60.0,
                           width=4, height=4, hwaccel="cuda", budget_mb=0)
    b0 = dsh._Block(s0, 0, "hw", 60)
    s0._buffered = 10

    def try_admit(last_main):
        b0.last_main = last_main
        th = _th.Thread(target=lambda: (s0._cv.acquire(), s0._admit(b0, 5), s0._cv.release()), daemon=True)
        th.start()
        th.join(1.0)
        blocked = th.is_alive()
        if blocked:
            with s0._cv:
                s0._closed = True
                s0._cv.notify_all()
            th.join(2)
            s0._closed = False
        return blocked
    check("ShardedSource._admit：主流还没到块尾时按预算挡；主流交到块的最后一个格之后放行（块尾辅助帧不是预取）",
          try_admit(0) and not try_admit(30))

    reset()

    def slow_plan():
        for k in range(4):
            time.sleep(0.01)
            yield (k * 60, (k + 1) * 60, None if k == 0 else k + 0.0005)
    out, pts, s, e, stuck = run(blocks=slow_plan())
    check("ShardedSource：块表边探边给（可迭代对象 + 规划线程）-> 结果同整张表",
          not stuck and e is None and out == list(range(0, 240, 30)) and len(s.blocks) == 4, (stuck, e, out))
    out, pts, s, e, stuck = run(blocks=iter([(0, 60, None), (90, 240, 1.5)]))
    check("ShardedSource：块表不连续 -> 规划失败、抛出来（不往下解）",
          not stuck and isinstance(e, RuntimeError) and "不连续" in str(e), (stuck, e))
    reset()


COMMON = "ありがとうございました本当に"
"""合成语料里的常用句：放进 5 个单元（> max_units 3），查询撞上它不许拿来圈单元。"""


def synthetic_gamescript(d: Path) -> dict:
    """检索层的合成语料（audit-6 §3：守卫原来唯独没盖检索层，而分母就是那一层定的）。

    A：一段带分支的对话——a1 / a2 是两个选项之后的回复，obs 只读到 a1；a4 在最后一个锚点之后；
       中间夹一条常用句。B：一段 obs 从没读到的对话。C：绝区零式的变体组（c0 / c1 两种说法，
       obs 读到的那一截同时包含于两行）；c3 读在画面中间（不在字幕带）。D1–D4：只装着那条常用句。
       返回 gamescript.build 的结果。"""
    import json as _json
    from flowocr.analyze import gamescript as GS

    def L(key, text, **kw):
        return GS.Line(key=key, kind="Talk", role="NPC", speaker=None, raw=text, cn=None, **kw)
    units = [
        GS.Unit("A", None, [L("a0", "最初の台詞はここから始まる"),
                            L("as", "うん、そうだね"),          # 短行：只能靠 short_anchors 锚
                            L("a1", "選択肢Aの後の返事ですよ", memberships=[("g", "A")]),
                            L("a2", "選択肢Bの後の返事ですよ", memberships=[("g", "B")]),
                            L("ac", COMMON),
                            L("a3", "最後にみんなで帰りましょう"),
                            L("a4", "そして誰もいなくなったのでした")]),
        GS.Unit("B", None, [L("b0", "この対話は一度も画面に出ていない"),
                            L("b1", "だから台本にも入らないはずです")]),
        GS.Unit("C", None, [L("c0", "リンさん、ビリーさん、聞こえるかしら", variant="C#v0"),
                            L("c1", "アキラさん、ビリーさん、聞こえるかしら", variant="C#v0"),
                            L("c2", "いいわね、では実戦訓練に移行するわ"),
                            L("c3", "次の目的地はルミナスクエアです")]),
        *[GS.Unit(f"D{i}", None, [L(f"d{i}", COMMON), L(f"d{i}x", f"別の場所の台詞その{i}番目です")])
          for i in range(1, 5)],
    ]
    obs = d / "syn.jsonl"
    # (文字, 秒, 框中心 y / 画面高)：字幕带在 0.85，c3 读在画面中间（选项 / 居中旁白那种）
    rows = [("最初の台詞はここから始まる", 10, .85), ("うん、そうだね", 11, .4),
            ("選択肢Aの後の返事ですよ", 12, .85), (COMMON, 14, .3),
            ("最後にみんなで帰りましょう", 16, .86), ("ビリーさん、聞こえるかしら", 30, .84),
            ("いいわね、では実戦訓練に移行するわ", 32, .85), ("次の目的地はルミナスクエアです", 34, .4)]
    obs.write_text("\n".join([_json.dumps({"_meta": {"height": 1000}})]
                             + [_json.dumps({"text": t, "t_us": s * 1_000_000,
                                             "box": [100, y * 1000 - 10, 900, y * 1000 + 10]},
                                            ensure_ascii=False) for t, s, y in rows]) + "\n",
                   encoding="utf-8")
    saved = dict(GS.LOADERS)
    GS.LOADERS["syn"] = lambda: units
    try:
        from contextlib import redirect_stdout
        import io
        with redirect_stdout(io.StringIO()):
            return GS.build("syn", obs, qmin=8, min_contain=0.85, max_units=3, min_anchors=2,
                            solo_len=20)
    finally:
        GS.LOADERS.clear()
        GS.LOADERS.update(saved)


def _mini_bundle(d: Path, game: str = "genshin", tables=("quests", "reminders"),
                 langs=(("ja", "jp"), ("zh-Hans", "chs")), compress: bool = True, n: int = 3) -> Path:
    """按 `gtd-bundle/1` 手搭一个迷你包目录（清单、两种哈希、指纹都按协议算）。返回目录。"""
    import hashlib
    import json as _json
    import zstandard
    from flowocr.analyze import gtdbundle as GB
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for t in tables:
        for code, suf in langs:
            body = "".join(_json.dumps({"id": i, "text": f"{t}-{code}-{i}"}, ensure_ascii=False) + "\n"
                           for i in range(n)).encode("utf-8")
            name = f"{t}_{suf}.jsonl" + (".zst" if compress else "")
            stored = zstandard.ZstdCompressor(level=3).compress(body) if compress else body
            (d / name).write_bytes(stored)
            files.append({"path": name, "table": t, "lang": code, "rows": n, "bytes": len(stored),
                          "sha256": hashlib.sha256(stored).hexdigest(),
                          "content_sha256": hashlib.sha256(body).hexdigest()})
    rep = b'{"hard_failures": []}\n'
    (d / "report.json").write_bytes(rep)
    files.append({"path": "report.json", "bytes": len(rep), "sha256": hashlib.sha256(rep).hexdigest()})
    m = {"schema": GB.SCHEMA, "game": game, "fingerprint": GB.fingerprint_of(files), "rev": 1,
         "upstream": {"repo": "r", "commit": "c", "version": "1.0", "integrity_checked": False},
         "extractor": {"subproject": game, "git_head": "h", "dirty": False},
         "langs": {code: suf for code, suf in langs}, "base_lang": "zh-Hans",
         "tables": {t: {"rows": n, "contract": f"{game}.{t}/1"} for t in tables}, "files": files}
    (d / "manifest.json").write_text(_json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return d


def _zip_bundle(src: Path, dst: Path, game: str = "genshin", tamper: str | None = None) -> Path:
    """把迷你包目录装进 `<game>/` 前缀的 zip；`tamper` 给了就把那个成员的最后一个字节改掉（清单不动）。"""
    import zipfile
    dst.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst, "w") as zf:
        for p in sorted(src.iterdir()):
            data = p.read_bytes()
            if p.name == tamper:
                data = data[:-1] + bytes([data[-1] ^ 1])
            zf.writestr(f"{game}/{p.name}", data)
    return dst


__all__ = ['ROOT', 'FAILED', 'evalkit', 'srtio', 'importlib', 'subprocess', 'sys', 'inspect', 'tempfile', 'Path', 'raises', '_every', 'check', 'fake_widths', 'render_overlay', 'SRT_OK', '_shard_behaviour_checks', 'COMMON', 'synthetic_gamescript', '_mini_bundle', '_zip_bundle']
