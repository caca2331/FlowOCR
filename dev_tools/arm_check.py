"""A/B 驱动跑之前问一句：**这几条臂解析之后是不是同一条**（2026-09-17 审计）。

`ocr_ab.sh` 原来比的是 `X_ARGS` 的**字面**——和 audit-4 C7 / 09-10 那次同一个形状：
默认值一翻，"空参数 = 旧默认"这种写法就塌成同一条臂（`--reuse-v2 --reuse-corr .8 --refresh-every 32`
现在和不给参数完全相等），脚本照旧按两条臂出表、报出来的差就是纯噪声。
判据只有 `ocr_args.same_arm` 一份（解析后的生效值）。

usage: python dev_tools/arm_check.py "<A_ARGS>" "<B_ARGS>" [更多臂…]
       每个参数是一条臂的**额外参数串**（可以为空 = 当前默认）。撞了就非零退出。
"""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # 正式包（没装 flowocr 的 venv 里也能跑）
from flowocr.extract import ocr_args  # noqa: E402


def collisions(arms: list[str], labels: list[str] | None = None) -> list[tuple[str, str]]:
    labels = labels or [chr(ord("A") + i) for i in range(len(arms))]
    base = ["dummy.mp4", "--out", "x.jsonl"]
    bad = []
    for i in range(len(arms)):
        for j in range(i + 1, len(arms)):
            if ocr_args.same_arm(base + shlex.split(arms[i]), base + shlex.split(arms[j])):
                bad.append((labels[i], labels[j]))
    return bad


def main() -> int:
    arms = sys.argv[1:]
    if len(arms) < 2:
        print("要至少两条臂", file=sys.stderr)
        return 2
    bad = collisions(arms)
    for a, b in bad:
        print(f"**{a} 和 {b} 解析之后是同一条臂**（`config` 逐键相同）——同一条臂跑两遍不是 A/B。"
              f"对照臂要把关掉的旋钮显式写出来（如 `--no-reuse-v2 --no-refine-fused`）。停。", file=sys.stderr)
    return 2 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
