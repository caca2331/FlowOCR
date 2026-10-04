"""采样缺口探针（decode-buffer §8.1 第 1 步）的分类契约；纯 CPU，不加载 OCR 模型。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev_tools"))
import probe_fps_gap as gap

BOX = [400, 900, 800, 940]


def a(frame, text, box=BOX):
    return {"frame": frame, "t_us": frame * 16_667, "box": list(box), "text": text, "conf": 0.9}


def by_frame(*rows):
    out = {}
    for o in rows:
        out.setdefault(o["frame"], []).append(o)
    return out


def classify(frames, text, base, track=None):
    return gap.classify(frames, track or [(frames[0], BOX)], text, base, 30, 0)


class CompareTests(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(gap.compare("今日はいい天気", "今日はいい天気。"), "covered")
        self.assertEqual(gap.compare("今日は", "今日はいい天気"), "partial")      # 半截打字机
        self.assertEqual(gap.compare("明日も雨です", "今日はいい天気"), "differs")
        self.assertEqual(gap.compare("……", "……！"), "partial")                 # 纯标点按原文比
        self.assertEqual(gap.compare("", "今日"), "differs")
        # 促音读成 `つ` / `っ` 的抖动：归一化里折小写，不能判成半截
        self.assertEqual(gap.compare("ちょつとちょっと、まさか本気にしちやった？",
                                     "ちょつとちょつと、まさか本気にしちやつた？"), "covered")
        # 少了句尾（Codex 审计 P2 第 4 条的反例）：相似度 0.96 也是半截，必须进人工清单
        self.assertEqual(gap.compare("今日は晴れですが明日は", "今日は晴れですが明日は雨"), "partial")
        # 同长度的单字识别差（不是缺字）仍算读到
        self.assertEqual(gap.compare("今日は晴れですが明日は両", "今日は晴れですが明日は雨"), "covered")


class ClassifyTests(unittest.TestCase):
    def test_between_when_no_grid_point_inside(self):
        # 参考 run 在 40..50 帧（stride 10），2 fps 格点是 30 / 60，都在存在期外
        self.assertEqual(classify([40, 50], "閃光テキスト", {})[0], "between")

    def test_fragment_when_neighbour_reads_same_text(self):
        # 参考臂自己断档：紧邻的格点 60 上同位置有同一段文本 -> 不是缺口
        cat, got = classify([40, 50], "閃光テキスト", by_frame(a(60, "閃光テキスト")))
        self.assertEqual((cat, got), ("fragment", "閃光テキスト"))

    def test_between_busy_when_neighbour_has_other_text(self):
        # 同一位置紧邻格点上有框、文本不同（乱码抖动 / 换句）-> 不算真空白里的闪现
        cat, got = classify([40, 50], "Duinini", by_frame(a(30, "Bsm y")))
        self.assertEqual((cat, got), ("between_busy", "Bsm y"))

    def test_fragment_typewriter_midstate_inside_full_line_box(self):
        # 参考臂的打字机中间态：框只是 2 fps 整行框的左段，文本是它的前缀
        base = by_frame(a(60, "一歩前へ進む"))
        self.assertEqual(classify([40, 50], "一歩前", base, [(40, [400, 900, 520, 940])])[0], "fragment")

    def test_fragment_midstate_with_dropped_char(self):
        # 中间态上 OCR 丢了一个字（`せ`），严格子串判不出来
        base = by_frame(a(60, "何せミステリマニアですからね！"))
        self.assertEqual(classify([40, 50], "何ミステ", base)[0], "fragment")
        self.assertFalse(gap.midstate("明日も雨", "何せミステリマニア"))

    def test_edge_when_neighbour_outside_window(self):
        # 对照臂采样到 60 帧为止；70..80 帧的 run 下一个格点 90 不存在
        self.assertEqual(gap.classify([70, 80], [(70, BOX)], "窓の外", {}, 30, 0, (0, 60))[0], "edge")
        self.assertEqual(gap.classify([40, 50], [(40, BOX)], "窓の中", {}, 30, 0, (0, 60))[0], "between")

    def test_partial_and_best_wins(self):
        base = by_frame(a(30, "今日は", [400, 900, 520, 940]), a(60, "今日はいい天気"))
        self.assertEqual(classify([30, 40, 50, 60], "今日はいい天気", base)[0], "covered")
        base = by_frame(a(30, "今日は", [400, 900, 520, 940]))
        self.assertEqual(classify([30, 40, 50, 60], "今日はいい天気", base)[0], "partial")

    def test_sampled_missed(self):
        # 格点 30 在存在期内，但对照臂那里只有别处的框
        base = by_frame(a(30, "UID", [1600, 1040, 1800, 1070]))
        self.assertEqual(classify([20, 30, 40], "今日はいい天気", base)[0], "sampled_missed")

    def test_split_boxes_joined_by_x(self):
        base = by_frame(a(30, "天気", [600, 900, 800, 940]), a(30, "今日はいい", [400, 900, 590, 940]))
        self.assertEqual(classify([30], "今日はいい天気", base), ("covered", "今日はいい天気"))

    def test_moving_run_uses_box_at_time(self):
        # 滚动文字：60 帧时已经上移到 y=500，用首框去找会落空
        moved = [400, 500, 800, 540]
        track = [(30, BOX), (60, moved)]
        base = by_frame(a(60, "スクロール文字", moved))
        self.assertEqual(classify([30, 60], "スクロール文字", base, track)[0], "covered")


if __name__ == "__main__":
    unittest.main()
