"""共享层的自检测试。`.venv/Scripts/python.exe tests/test_guards.py` 直接跑，不依赖 pytest（用项目的 venv：系统 python 的依赖版本不同会带出假报错）。

为什么有这个文件（methodology-audit 报告建议 2 / 4）：
项目此前**一行自动化验证都没有**，所有等价性检查都是手搓的一次性脚本，跑完就散。
而"守卫没弄坏正常路径"和"守卫真的会响"是两回事——
上一次口径事故正是**该报错的地方静默返回了 0**，退出码还是 0。

这里只测**机制会不会响**，不测具体数值：数值验证靠端到端 A/B（见 docs/）。

守卫按领域分在 `tests/guards/` 下（extract / analyze / gametext / output / infra），共用的在 `_common.py`；
新守卫写成对应模块里的 `t_*` 函数就会被跑到（按模块、再按定义顺序），不用在这里登记。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from guards import analyze, extract, gametext, infra, output  # noqa: E402
from guards._common import FAILED  # noqa: E402

MODULES = (infra, extract, analyze, gametext, output)


def main() -> int:
    for mod in MODULES:
        for name, fn in list(vars(mod).items()):       # 有的守卫会往模块里放全局名，先拷一份再遍历
            if name.startswith("t_") and callable(fn) and fn.__module__ == mod.__name__:
                fn()
    print()
    if FAILED:
        print(f"**{len(FAILED)} 项未通过**：" + "、".join(FAILED))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
