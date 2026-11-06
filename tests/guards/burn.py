"""阶段 5（烧进视频）的守卫。"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from guards._common import *  # noqa: F401,F403


def t_burn_encode() -> None:
    """编码器按先后挑、质量值什么时候搜、输出名。"""
    print("阶段 5：编码器与质量值")
    from flowocr.burn import encode as E, pipeline as P
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=8", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(Path(td) / "v.mp4")], check=True)
        here = os.getcwd()
        os.chdir(td)
        try:
            with E.Prober("v.mp4", 0, 8, "libx264", (160, 90), Fraction(10)) as pr:
                got = (len(pr.starts), all(f > 0 for f in pr.frames))
        finally:
            os.chdir(here)
    check("质量值搜索的抽样认相对路径的视频（抽样在临时目录里跑）", got == (4, True), got)

    def only(*ok):
        return lambda n: n in ok
    with patch.object(E, "usable", only("h264_nvenc", "libx264", "hevc_amf")):
        check("auto 按先后挑第一个能用的：H.265 硬件优先于 H.264 硬件", E.resolve("auto") == "hevc_amf")
    with patch.object(E, "usable", only("h264_qsv", "libx264")):
        check("没有 H.265 硬件就挑 H.264 硬件", E.resolve("auto") == "h264_qsv")
    with patch.object(E, "usable", only("libx264", "libx265")):
        check("都没有硬件就挑 libx264（libx265 太慢、不进自动选择）", E.resolve("auto") == "libx264")
        check("指定的编码器：不认识、压不了的当场报错，能用的照用",
              raises(lambda: E.resolve("vp9"), ValueError) and raises(lambda: E.resolve("hevc_nvenc"), ValueError)
              and E.resolve("libx265") == "libx265")
    with patch.object(E, "usable", only()):
        check("一个能用的编码器都没有：报带说明的 ValueError（不是光秃秃的 StopIteration）", raises(lambda: E.resolve("auto"), ValueError))

    class R:
        def __init__(self, avg, nom):
            self.stdout = '{"streams": [{"avg_frame_rate": "%s", "r_frame_rate": "%s"}]}' % (avg, nom)
    fps = lambda avg, nom: (patch.object(E.subprocess, "run", lambda *a, **k: R(avg, nom)), lambda: E.src_fps("v"))  # noqa: E731
    got = []
    for avg, nom in (("1630078001/27167966", "60/1"), ("24000/1001", "24000/1001"), ("45/1", "60/1"), ("60000/1001", "60/1"), ("0/0", "30/1")):
        ctx, f = fps(avg, nom)
        with ctx:
            got.append(f())
    check("源帧率：平均和标称相差不到 0.01% 取标称的（录像的 59.9998 -> 60），59.94 对 60 和真可变帧率取平均的，只记一个就用那个",
          got == [60, Fraction(24000, 1001), 45, Fraction(60000, 1001), 30], got)
    check("默认质量值按同一画质保守取：硬件 26、libx264 14；目标 VMAF 95、不短于 30 分钟才默认搜",
          E.ENCODERS["hevc_nvenc"]["q"] == E.ENCODERS["h264_nvenc"]["q"] == 26 and E.ENCODERS["libx264"]["q"] == 14
          and E.TARGET == 95 and E.AUTO_MIN_S == 1800)
    check("抽样段数：每分钟一段，4–10 段", [E.n_samples(x) for x in (30, 299, 301, 3600)] == [4, 5, 6, 10])

    calls = []

    def plan(dur, quality, start=None, end=None, vmaf=True):
        calls.clear()
        with patch.object(P, "video_of", lambda a, v: "v.mp4"), patch.object(P.platecolor, "duration", lambda v: dur), \
                patch.object(E, "src_size", lambda v: (1920, 1080)), patch.object(E, "src_fps", lambda v: Fraction(60)), \
                patch.object(E, "resolve", lambda c: "hevc_nvenc"), patch.object(E, "has_vmaf", lambda: vmaf), \
                patch.object(E, "search", lambda *a: calls.append(a) or {"q": 31}):
            return P.plan(Path("x.ass"), None, None, False, start, end, None, None, "auto", quality, 95.0)
    j = plan(1799, None)
    check("不给质量值、不到 30 分钟：用编码器的默认值，不搜", j.quality == 26 and not calls and j.probe is None)
    j = plan(7200, None, 0, 1800)
    check("不给质量值、范围不短于 30 分钟：搜（按输出的尺寸、源帧率）", j.quality == 31 and calls and calls[0][5:] == ((1920, 1080), Fraction(60)))
    j = plan(7200, None, 100, 200)
    check("长视频只烧其中一段：按这一段的长短定搜不搜", j.quality == 26 and not calls)
    check("--quality auto 不管长短都搜，给数就用这个数", plan(60, "auto").quality == 31 and plan(7200, "20").quality == 20 and not calls)
    j = plan(7200, None, vmaf=False)
    check("ffmpeg 没带 libvmaf：长视频退回默认值并提示，--quality auto 报错",
          j.quality == 26 and not calls and j.notes and raises(lambda: plan(60, "auto", vmaf=False), ValueError))
    check("范围不对（终点不在起点之后、读不出时长）报错", raises(lambda: plan(60, None, 30, 10), ValueError)
          and raises(lambda: plan(float("inf"), None), ValueError))
    check("输出名：x.ass -> x.mp4，inpainting 是 x.inpaint.mp4，和 ASS 放在一起",
          P.default_out(Path("d/x.ass"), False) == Path("d/x.mp4") and P.default_out(Path("d/x.ass"), True) == Path("d/x.inpaint.mp4"))


def t_burn_plate() -> None:
    """底板路线端到端：小视频 + 一行字，烧出来的尺寸、帧率、音轨；失败不留文件。"""
    print("阶段 5：底板路线")
    from flowocr.burn import pipeline as P
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)

        def src(name, audio, lavfi="testsrc2=s=320x180:r=30:d=2"):
            a = {"aac": ["-f", "lavfi", "-i", "sine=f=440:d=2", "-c:a", "aac"],
                 "vorbis": ["-f", "lavfi", "-i", "sine=f=440:d=2", "-c:a", "libvorbis"], None: []}[audio]
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", lavfi, *a,
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(d / name)], check=True)
            return str(d / name)
        ass = d / "x.ass"
        ass.write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n\n[V4+ Styles]\n"
                       "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
                       "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
                       "MarginR, MarginV, Encoding\nStyle: B,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,"
                       "0,0,1,2,0,2,10,10,10,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                       "Effect, Text\nDialogue: 0,0:00:00.50,0:00:01.50,B,,0,0,0,,x\n", encoding="utf-8")

        def probe(p):
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate",
                                "-of", "csv=p=0", str(p)], capture_output=True, text=True, check=True)
            return sorted(r.stdout.split())

        def burn(video, **kw):
            o = {"start": None, "end": None, "size": None, "fps": None, "quality": None, **kw}
            job = P.plan(ass, video, d / "o.mp4", False, o["start"], o["end"], o["size"], o["fps"], "libx264", o["quality"], 95.0)
            return P.burn_plate(job), job
        out, job = burn(src("a.mp4", "aac"))
        check("按源尺寸、帧率出，音轨照拷", probe(out) == ["aac,audio,0/0", "h264,video,320,180,30/1"] and not job.notes, probe(out))
        out, job = burn(src("n.mp4", None), size=(160, 90), fps=Fraction(15), start=0.5, end=1.5)
        check("没有音轨就不带；尺寸、帧率可以覆盖；只烧一段", probe(out) == ["h264,video,160,90,15/1"]
              and abs(float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                                           capture_output=True, text=True).stdout) - 1.0) < 0.1, probe(out))
        out, job = burn(src("v.mkv", "vorbis"))
        check("mp4 收不下的音轨重编成 AAC 并提示", probe(out)[0].startswith("aac,audio") and job.notes, (probe(out), job.notes))
        (d / "o.mp4").unlink()
        black = src("k.mp4", None, "color=c=black:s=320x180:r=30:d=3")
        box = ass.read_text(encoding="utf-8").replace("Dialogue: 0,0:00:00.50,0:00:01.50,B,,0,0,0,,x",
                                                       "Dialogue: 0,0:00:01.00,0:00:02.00,B,,0,0,0,,"
                                                       "{\\an7\\pos(0,0)\\p1\\bord0\\shad0\\1c&HFFFFFF&}m 0 0 l 320 0 320 180 0 180{\\p0}")
        ass2 = d / "box.ass"
        ass2.write_text(box, encoding="utf-8")
        job = P.plan(ass2, black, d / "o.mp4", False, 0.5, 2.5, None, None, "libx264", None, 95.0)
        out = P.burn_plate(job)

        def luma(t):
            r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                                "-pix_fmt", "gray", "-"], capture_output=True, check=True)
            return sum(r.stdout) / len(r.stdout)
        lum = [round(luma(t)) for t in (0.25, 1.0, 1.75)]
        check("只烧一段：ASS 的时间按原片算（原片 1–2 s 的白框落在输出的 0.5–1.5 s）", lum[0] < 30 < 200 < lum[1] and lum[2] < 30, lum)
        before = (d / "a.mp4").read_bytes()
        same = d / "a.ass"
        same.write_text(ass.read_text(encoding="utf-8"), encoding="utf-8")
        check("输出就是源视频（ASS 和视频同名同目录时的默认输出名）或 ASS：报错，源视频一个字节不动",
              raises(lambda: P.plan(same, str(d / "a.mp4"), None, False, None, None, None, None, "libx264", None, 95.0), ValueError)
              and raises(lambda: P.plan(ass, str(d / "a.mp4"), ass, False, None, None, None, None, "libx264", None, 95.0), ValueError)
              and (d / "a.mp4").read_bytes() == before)
        (d / "o.mp4").unlink()
        with patch.object(P.encode, "venc", lambda n, q: ["-c:v", "no_such_encoder"]):
            failed = raises(lambda: burn(str(d / "a.mp4")), RuntimeError)
        check("ffmpeg 失败：报错，不留成品也不留 .part", failed and not (d / "o.mp4").exists() and not (d / "o.mp4.part").exists())


def t_burn_lamagraph() -> None:
    """LaMa 派生图的来源标记：源文件、做法版本、ORT 版本有一样变了就重做，都没变不重做（真模型 200 MB，这里换成一个小图）。"""
    print("阶段 5：LaMa 派生图")
    import onnx
    import onnxruntime as ort
    from onnx import TensorProto, helper
    from flowocr import models, paths
    from flowocr.burn import lamagraph as L
    tiny = helper.make_model(helper.make_graph([helper.make_node("Identity", ["x"], ["y"])], "g",
                                               [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1])],
                                               [helper.make_tensor_value_info("y", TensorProto.FLOAT, [1])]))
    made = []
    with tempfile.TemporaryDirectory() as td, patch.object(paths, "models_root", lambda: Path(td)), \
            patch.object(models, "path", lambda n: str(Path(td) / "src.onnx")), \
            patch.object(L, "folded", lambda src: made.append(1) or onnx.ModelProto.FromString(tiny.SerializeToString())), \
            patch.object(L, "flat", lambda m, h, w: m), patch.object(L, "scaled", lambda m, k: m):
        models.MODELS["lama"].dir.mkdir()                 # 真流程里取源文件时建
        first = L.path(320, 768, 64)
        L.path(320, 768, 64)
        n_same = len(made)
        with patch.object(ort, "__version__", "0.0.0"):
            L.path(320, 768, 64)
        n_ort = len(made)
        with patch.object(L, "VERSION", L.VERSION + 1):
            other = L.path(320, 768, 64)
        check("派生图：第一次做、没变不重做；ORT 版本变了重做；做法版本变了另起一个文件名重做",
              n_same == 1 and n_ort == 2 and len(made) == 3 and other != first and Path(first).is_file(), (n_same, n_ort, len(made)))


ASS_HEAD = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\nStyle: B,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,"
            "0,0,1,2,0,2,10,10,10,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def plate_line(start: str, end: str, pos: str, w: int, h: int, extra: str = "", effect: str = "fo-fx|plate") -> str:
    return (f"Dialogue: 0,{start},{end},B,,0,0,0,{effect},{{\\an7{pos}\\p1\\bord0\\shad0\\1c&H000000&\\1a&H20&{extra}}}"
            f"m 0 0 l {w} 0 {w} {h} 0 {h}{{\\p0}}")


def t_burn_plates() -> None:
    """inpainting 读底板：按开始时间滑动筛出的和逐个扫的相同（集合和行序，含提前量的边界、\\move、打字机的 \\clip）；PlayRes 换像素；旧稿、取过色的稿报错。"""
    print("阶段 5：inpainting 读底板")
    from flowocr.burn import plates as PL
    lines = [plate_line("0:00:01.50", "0:00:03.00", "\\move(0,0,100,0,0,1000)", 40, 20),          # 行序在前、开始得晚
             plate_line("0:00:01.00", "0:00:02.00", "\\pos(10,100)", 50, 30),
             plate_line("0:00:00.50", "0:00:02.50", "\\pos(10,10)", 200, 20, "\\clip(10,10,10,30)\\t(0,1000,\\clip(10,10,60,30))"),
             plate_line("0:00:00.00", "0:00:03.00", "\\pos(0,0)", 320, 180, effect="fo-fx"),           # 扩展效果的框：不是底板
             "Dialogue: 0,0:00:00.50,0:00:01.50,B,,0,0,0,fo-fx,{\\pos(160,90)}译文"]
    text = ASS_HEAD + "\n".join(lines) + "\n"
    pl, rest = PL.load(text, (320, 180))
    check("只认打了标的行：三块底板；其余的行（扩展效果的框、正文）留给烧录，底板行不留",
          len(pl.evs) == 3 and len(rest) == len(text.splitlines()) - 3 and not any("fo-fx|plate" in ln for ln in rest))

    def brute(t):
        on = [ev for ev in pl.evs if ev.start - PL.LEAD <= t < ev.end + PL.LEAD]
        return ([pl.px(ev.rect_at(t)) for ev in on],
                [pl.px(r) for r in (PL.shown_at(ev, t) for ev in on) if r is not None])
    edges = {b + d for ev in pl.evs for b in (ev.start - PL.LEAD, ev.end + PL.LEAD) for d in (-1e-9, 0.0, 1e-9)}
    ts = sorted({n / 30 for n in range(106)} | {t for t in edges if t >= 0})
    bad = [t for t in ts if pl.at(t) != brute(t)]
    check("滑动筛出的底板和逐个扫的相同（集合、行序；提前量边界两侧、\\move、打字机）", not bad, bad[:5])
    at = lambda t, size=(320, 180): PL.load(text, size)[0].at(t)      # noqa: E731 —— at 只往前滑，每个时刻用新的
    check("\\move 按时段插值（1.5 s 起、1 s 走 100 px，2.0 s 在 50）；打字机：要抹的部分取这一刻的裁剪框（1.0 s 时拉到一半），不按提前量往前展开",
          at(2.0)[0][0] == (50, 0, 90, 20) and at(1.0)[1] == [(10, 100, 60, 130), (10, 10, 35, 30)], (at(2.0), at(1.0)))
    check("打字机底板在提前量里已经在屏（分组、推理按它），但裁剪框还是起始的空框：不抹（0.3 s，底板 0.5 s 起）",
          at(0.3)[0] == [(10, 10, 210, 30)] and at(0.3)[1] == [], at(0.3))
    check("移动的底板：要抹的部分就是这一刻的矩形（不随提前量挪到前面去；1.4 s 还没起步、2.0 s 走到 50）",
          at(1.4)[1][0] == at(1.4)[0][0] == (0, 0, 40, 20) and at(2.0)[1][0] == at(2.0)[0][0] == (50, 0, 90, 20), (at(1.4), at(2.0)))
    check("PlayRes 和视频尺寸不同：按比例换成像素", at(1.25, (640, 360))[0] == [(0, 0, 80, 40), (20, 200, 120, 260), (20, 20, 420, 60)],
          at(1.25, (640, 360)))
    old = text.replace("fo-fx|plate", "fo-fx")
    check("旧稿（没有标、有绘图）报错；没有绘图的稿不报错、什么都不抹",
          raises(lambda: PL.load(old, (320, 180)), ValueError) and not PL.load(ASS_HEAD, (320, 180))[0].evs)
    cells = text.replace("m 0 0 l 50 0 50 30 0 30{\\p0}", "m 0 0 l 50 0 50 30 0 30")
    check("取过色的稿（底板换成了小格）报错，提示关掉取色重出", raises(lambda: PL.load(cells, (320, 180)), ValueError))


def t_burn_inpaint() -> None:
    """inpainting 路线端到端（推理换成全零输出的假会话：补丁是黑的）：抹的地方和时刻、非 1080p 的换算；解码、编码失败不留文件；
    lama 的 TRT 选项和引擎缓存（flowocr-trt remove 认得）。"""
    print("阶段 5：inpainting 路线")
    import numpy as np
    from flowocr import trt as T
    from flowocr.burn import inpaint as I, pipeline as P
    from flowocr.extract import trt_engine as E

    class Zero:
        def run(self, _, feed):
            return [np.zeros_like(feed["image"])]
    with tempfile.TemporaryDirectory() as td, patch.object(I, "session", lambda *a: Zero()), \
            patch.object(I, "backend_of", lambda b="auto": ("cpu", "")):
        d = Path(td)
        video = str(d / "w.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=white:s=320x180:r=30:d=3",
                        "-f", "lavfi", "-i", "sine=f=440:d=3", "-c:a", "aac", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", video],
                       check=True)
        ass = d / "x.ass"
        ass.write_text(ASS_HEAD + plate_line("0:00:01.00", "0:00:02.00", "\\pos(100,60)", 120, 60) + "\n", encoding="utf-8")
        out = d / "o.mp4"

        def burn(**kw):
            job = P.plan(ass, video, out, True, kw.get("start"), kw.get("end"), kw.get("size"), None, "libx264", "10", 95.0)
            return P.burn_inpaint(job), job

        def gray(t, box):
            r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-f", "rawvideo",
                                "-pix_fmt", "gray", "-"], capture_output=True, check=True)
            w, h = (int(v) for v in subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                                                    "-select_streams", "v", str(out)], capture_output=True, text=True).stdout.split(","))
            x0, y0, x1, y1 = box
            return round(float(np.frombuffer(r.stdout, np.uint8).reshape(h, w)[y0:y1, x0:x1].mean()))
        burn()
        inside, outside = (110, 70, 210, 110), (10, 10, 60, 40)
        got = [gray(t, inside) for t in (0.5, 1.5, 2.5)] + [gray(1.5, outside)]
        tags = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=color_space,color_range,color_primaries,color_transfer", "-of", "json", str(out)],
                              capture_output=True, text=True).stdout
        check("inpainting 的输出标上色彩信息：源没标、高不到 720 时按 BT.601（smpte170m）、有限范围",
              all(f'"{k}": "{v}"' in tags for k, v in (("color_space", "smpte170m"), ("color_range", "tv"),
                                                       ("color_primaries", "smpte170m"), ("color_transfer", "smpte170m"))), tags)
        check("底板在屏时抹掉底板里的像素（提前 0.25 s 展开、延后收起），别处和别的时刻不动；音轨照拷",
              got[0] > 200 and got[1] < 30 and got[2] > 200 and got[3] > 200
              and "aac" in subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
                                          capture_output=True, text=True).stdout, got)
        burn(size=(640, 360), start=1.0, end=2.0)
        got = [gray(0.5, (220, 140, 420, 220)), gray(0.5, (20, 20, 120, 80))]
        check("给了 --size（非 1080p、和 PlayRes 不同）、只烧一段：底板换到输出像素上抹，时刻按原片", got[0] < 30 and got[1] > 200, got)
        out.unlink()
        with patch.object(P.encode, "venc", lambda n, q: ["-c:v", "no_such_encoder"]):
            failed = raises(lambda: burn(), RuntimeError)
        check("编码失败：报错，不留成品也不留 .part", failed and not out.exists() and not Path(str(out) + ".part").exists())
        with patch.object(P, "audio_args", lambda v: ([], None)), patch.object(P, "seek_args", lambda j: ["-ss", "bogus"]):
            failed = raises(lambda: burn(), RuntimeError)       # 解码的 ffmpeg 一启动就出错退出
        check("解码失败：报错，不留成品也不留 .part", failed and not out.exists() and not Path(str(out) + ".part").exists())

        class Boom:
            def run(self, _, feed):
                raise RuntimeError("推理出错")
        base = threading.active_count()
        with patch.object(I, "session", lambda *a: Boom()):
            failed = raises(lambda: burn(), RuntimeError)       # 推理在主线程上出错：读、写两个线程都卡在队列上
        time.sleep(0.5)
        check("推理出错：报错，不留文件，读解码、写编码两个线程都停下（不留在进程里）",
              failed and not out.exists() and not Path(str(out) + ".part").exists() and threading.active_count() <= base,
              (threading.active_count(), base))
        out.mkdir()
        failed = raises(lambda: burn(), OSError)                # 输出名是个目录：最后改名失败
        check("最后改名失败（输出名被目录占着）：报错，.part 删掉", failed and not Path(str(out) + ".part").exists())
        out.rmdir()
        rows = d / "rows.ass"
        rows.write_text(ASS_HEAD.replace("PlayResY: 180\n", "PlayResY: 180\nFlowOCR Typeset: plate=rows alpha=0x20\n")
                        + plate_line("0:00:01.00", "0:00:02.00", "\\pos(100,60)", 120, 60) + "\n", encoding="utf-8")
        bare = d / "bare.ass"
        bare.write_text(ASS_HEAD, encoding="utf-8")
        notes = [P.plan(x, video, out, True, None, None, None, None, "libx264", "10", 95.0).notes for x in (ass, rows, bare)]
        check("inpainting 开跑前就提示：底板不是 plate=src 出的（会连译文伸出的部分一起抹）、稿里没有底板（没有要抹的）",
              notes[0] == [] and len(notes[1]) == 1 and "plate=rows" in notes[1][0] and len(notes[2]) == 1 and "没有要抹" in notes[2][0], notes)

    tall = []
    for preset, rect in (("default", (200, 100, 1700, 900)), ("hq", (200, 300, 1700, 800))):
        img = I.Inpainter(I.PRESETS[preset], Zero(), 60.0, "cpu")(np.full((1080, 1920, 3), 255, np.uint8), [rect], [rect])
        x0, y0, x1, y1 = rect
        inside = img[y0 + 2:y1 - 2, x0 + 2:x1 - 2].copy()         # 缩小 2 倍的档放大回来时底板边上一圈混进原图，往里缩 2 px 看
        img[y0:y1, x0:x1] = 255
        tall.append((int(inside.max()), int(img.min())))
    check("比推理窗还高的单块底板（default 800 px、hq 500 px）：竖向切几行窗，整块补完、只贴回底板里", tall == [(0, 255), (0, 255)], tall)
    class MaskShare:                                            # 输出 = 窗里掩码的占比：掩码多标了一块就看得出来
        def run(self, _, feed):
            return [np.full_like(feed["image"], float(feed["mask"].mean()) * 255)]
    white = np.full((1080, 1920, 3), 255, np.uint8)
    on, off = (200, 800, 600, 900), (-200, 820, -100, 880)
    one = I.Inpainter(I.PRESETS["default"], MaskShare(), 60.0, "cpu")(white, [on], [on])
    both = I.Inpainter(I.PRESETS["default"], MaskShare(), 60.0, "cpu")(white, [off, on], [off, on])
    gone = I.Inpainter(I.PRESETS["default"], MaskShare(), 60.0, "cpu")(white, [off], [off])
    check("整块出画的底板：不抹画面里别的地方（和只有画内那块时逐像素相同；只有它时整帧不动）",
          np.array_equal(one, both) and np.array_equal(gone, white))

    class Probe:
        def __init__(self, st):
            self.stdout = '{"streams": [%s]}' % json.dumps(st)
    got = []
    for st in ({"height": 1080}, {"height": 480}, {"height": 1080, "color_space": "bt2020nc", "color_range": "pc",
                                                   "color_primaries": "bt2020", "color_transfer": "smpte2084"}):
        with patch.object(P.subprocess, "run", lambda *a, **k: Probe(st)):
            got.append(P.colors("v"))
    check("源的色彩：标了的照抄，没标的高 ≥ 720 当 BT.709、否则 BT.601，范围当有限",
          got[0] == {"space": "bt709", "range": "tv", "primaries": "bt709", "trc": "bt709"}
          and got[1] == {"space": "smpte170m", "range": "tv", "primaries": "smpte170m", "trc": "smpte170m"}
          and got[2] == {"space": "bt2020nc", "range": "pc", "primaries": "bt2020", "trc": "smpte2084"}
          and P.MATRIX[got[2]["space"]] == "bt2020", got)

    o = E.provider_options("lama", "fp16", "p", Path("c"))
    check("lama 的 TRT 选项：形状固定不带 profile、workspace 2 GB；det / rec 照旧带 profile",
          not any(k.startswith("trt_profile") for k in o) and o["trt_max_workspace_size"] == str(2 << 30)
          and all("trt_profile_min_shapes" in E.provider_options(k, "fp32", "p", Path("c")) for k in ("det", "rec")))
    with tempfile.TemporaryDirectory() as td:
        eng = Path(td) / "engines"
        eng.mkdir()
        for n in ("lama_3dda85f72d1765c6_t1016111_o1290_sm120_fp16_cb4a90f6.lock",
                  "lama_3dda85f72d1765c6_t1016111_o1290_sm120_fp16_cb4a90f6_5960910791567646261_0_fp16_sm120.engine"):
            (eng / n).write_bytes(b"")
        check("lama 的引擎缓存（.engine、.lock）flowocr-trt remove 认得、会删", T.ours(eng))


def t_burn_chain() -> None:
    """`flowocr-render --burn`：叠加预设写完接着烧；只配叠加预设；inpainting 替用户带 plate=src、拒收取色（出文件之前就报）。"""
    print("阶段 5：阶段 3 的 --burn 连跑")
    import numpy as np
    from flowocr.artifacts import tracksio
    from flowocr.burn import inpaint as I
    from flowocr.output import render as R

    class Zero:
        def run(self, _, feed):
            return [np.zeros_like(feed["image"])]
    with tempfile.TemporaryDirectory() as td, patch.object(I, "session", lambda *a: Zero()), \
            patch.object(I, "backend_of", lambda b="auto": ("cpu", "")):
        d = Path(td)
        video = d / "v.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30:d=2",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
        tp = d / "t-tracks.json"
        tracksio.dump(tp, {"meta": {}, "frame_us": 500_000, "size": [320, 180],
                           "provenance": {"main_track": "r00", "main_srt": "t-region00-subtitle.srt", "video": str(video)},
                           "regions": [{"index": 0, "align": "center", "align_how": {"by": "none"}}],
                           "events": [{"id": 0, "region": 0, "t_start": 500_000, "t_end": 1_500_000, "text": "台词", "flags": [],
                                       "box": [100, 140, 220, 165]}],
                           "tracks": [{"id": "r00", "kind": "region", "region": 0, "label": "subtitle", "srt": "t-region00-subtitle.srt",
                                       "cues": [{"id": "c0", "t_start": 500_000, "t_end": 1_500_000, "events": [0]}]}]})

        def dims(p):
            return subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
                                   "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout.strip()
        rc = R.main([str(tp), "--outdir", str(d / "a"), "--burn", "plate"])
        made = sorted(p.name for p in (d / "a").iterdir())
        check("--burn plate：字幕稿、最终 ASS、烧好的视频（按源尺寸）都在，底板默认按预设（不改成 plate=src）",
              rc == 0 and made == ["tracks-default.ass", "tracks-default.mp4", "tracks-default.script.ass"] and dims(d / "a" / "tracks-default.mp4") == "320,180"
              and "plate=src" not in (d / "a" / "tracks-default.ass").read_text(encoding="utf-8-sig"), made)
        rc = R.main([str(tp), "--outdir", str(d / "b"), "--burn", "inpaint"])
        final = (d / "b" / "tracks-default.ass").read_text(encoding="utf-8-sig")
        check("--burn inpaint：没给 plate 时带上 plate=src，出 .inpaint.mp4",
              rc == 0 and "plate=src" in final and (d / "b" / "tracks-default.inpaint.mp4").is_file(), sorted(p.name for p in (d / "b").iterdir()))
        check("--burn 配非叠加预设、inpainting 配 plate_color=on：报错，一个文件都不写",
              raises(lambda: R.main([str(tp), "--outdir", str(d / "c"), "--preset", "script", "--burn", "plate"]), SystemExit)
              and raises(lambda: R.main([str(tp), "--outdir", str(d / "c"), "--burn", "hq", "--opt", "plate_color=on"]), SystemExit)
              and not (d / "c").exists())
