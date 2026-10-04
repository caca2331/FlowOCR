"""进程内解码探针（dev_tools/inproc_decode_probe.py）的纯函数契约：Codex 复审 P2 的两条最小复现。纯 CPU，不解视频。"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
spec = importlib.util.spec_from_file_location("inproc_probe", ROOT / "dev_tools" / "inproc_decode_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

FRAMEMD5 = """#format: frame checksums
#tb 0: 1/15360
#media_type 0: video
#tb 1: 1/48000
#media_type 1: audio
0,          0,          0,      256,  3110400, aaaa
1,          0,          0,     1024,     4096, bbbb
0,        256,        256,      256,  3110400, cccc
1,       1024,       1024,     1024,     4096, dddd
"""


class ParseTests(unittest.TestCase):
    def test_audio_lines_do_not_overwrite_video(self):
        # 音轨的 pts 按视频时间基算会撞上视频帧号、把视频的 md5 覆盖掉——只认第 0 路
        got = probe.parse_framemd5(FRAMEMD5, 60.0)
        self.assertEqual(got, {0: "aaaa", 1: "cccc"})

    def test_duplicate_frame_id_is_an_error(self):
        dup = "#tb 0: 1/15360\n0, 0, 0, 256, 1, aaaa\n0, 10, 10, 256, 1, bbbb\n"
        with self.assertRaises(SystemExit):
            probe.parse_framemd5(dup, 60.0)


class ShiftTests(unittest.TestCase):
    def test_static_scene_duplicates_do_not_drag_shift(self):
        # 静止画面：同一个 md5 出现在基准的第 0~9 帧；原来 {md5: 最后一次出现} 会把偏移拉到 +9
        by_md5 = {"same": list(range(10)), "x": [11], "y": [12]}
        raw = [(-1, "same"), (0, "same"), (10, "x"), (11, "y")]
        self.assertEqual(probe.vote_shift(raw, by_md5), 1)

    def test_no_match_means_zero(self):
        self.assertEqual(probe.vote_shift([(0, "q")], {"z": [5]}), 0)


if __name__ == "__main__":
    unittest.main()
