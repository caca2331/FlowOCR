"""辅助流复用探针（decode-buffer §8.3）的配对与分类契约；纯 CPU，不解视频。"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev_tools"))
import probe_aux_reuse as aux


def o(frame, text, box=(100, 100, 300, 130), reused=False, conf=0.9):
    return {"frame": frame, "t_us": frame * 16_667, "box": list(box), "text": text,
            "conf": conf, "reused": reused}


class LabelTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(aux.label(o(0, "こんにちは"), o(30, "こんにちは")), "same")
        self.assertEqual(aux.label(o(0, "こんにちは"), o(30, "こんにちは", reused=True)), "reused")
        self.assertEqual(aux.label(o(0, "こんにちは世界"), o(30, "こんにちは世晃")), "near")
        self.assertEqual(aux.label(o(0, "こんにちは"), o(30, "さようなら")), "changed")


class PairTests(unittest.TestCase):
    def test_pairs_by_end_frame_and_iou(self):
        obs = [o(0, "A文字"), o(30, "A文字"),
               o(30, "遠くの框", box=(900, 900, 1000, 930)),     # 前一帧同位置没有 -> 不成对
               o(60, "A文字", conf=0.3)]                         # conf 门下 -> 不参与
        got = aux.pairs_of(obs, 0.5, 30)
        self.assertEqual(list(got), [30])
        self.assertEqual([k for _, _, k in got[30]], ["same"])

    def test_skipped_grid_point_is_not_paired(self):
        # 只有第 0、60 帧有合格 obs（30 帧那格空）：像素只在 [30, 60] 上量，所以 0 -> 60 不能配（Codex 审计 P2 第 3 条）
        obs = [o(0, "前のセリフ"), o(60, "次のセリフ")]
        self.assertEqual(aux.pairs_of(obs, 0.5, 30), {})

    def test_ncc_flat_blocks_count_as_unchanged(self):
        flat = np.full((4, 8), 7, np.uint8)
        self.assertEqual(aux.ncc(flat, flat), 1.0)
        a = np.arange(32, dtype=np.uint8).reshape(4, 8)
        self.assertAlmostEqual(aux.ncc(a, a), 1.0, places=5)
        self.assertLess(aux.ncc(a, a[::-1].copy()), 0.0)


if __name__ == "__main__":
    unittest.main()
