"""用户代码扩展的加载：匹配器（`match` + 可选 `cluster_patch`）、输出预设（`render`）、阶段 4 的扩展效果（`fx`）。

project-structure 计划："少量入口，直接可改"——普通 Python 模块 / 文件，按**内置名字**、
**点分模块名**或**文件路径**加载；内置实现和用户实现走同一条路，没有注册商店、没有基类、没有配置 DSL。

    from flowocr import extensions
    preset = extensions.load("srt_main", "preset")             # 内置：flowocr.output.presets.srt_main
    matcher = extensions.load("gametext", "matcher")            # 内置：flowocr.analyze.matchers.gametext
    mine = extensions.load("D:/mine/my_preset.py", "preset")    # 文件
    theirs = extensions.load("some_pkg.their_matcher", "matcher")   # 已安装的模块

加载完只核一件事：该有的可调用入口在不在（`REQUIRED`）。缺了就报 `TypeError`，把缺的名字列出来——
"加载失败、依赖缺失与输出失败可定位，失败不冒充成功"（计划）。
本地代码扩展不做隔离执行；第三方扩展要的依赖自己装、自己说明。

入口的形状（类型名在实现时收敛，计划 只给了最小形状）：

* 匹配器：`cluster_patch(context: dict, options: dict | None) -> dict`（可选；返回 build_tracks 的参数覆盖，
  键是参数名如 `region_time_gate`，由默认聚类器应用）、`match(document: dict, context: dict, options: dict | None) -> dict`
  （输入阶段 2 的分析产物 `*-tracks.json` 的 dict，返回带匹配结果的产物 `*-matched.json` 的 dict）；
* 输出预设：`render(document: dict, output_dir: Path | str, options: dict | None) -> list[Path]`
  （输入分析产物，写文件，返回写了哪些）；
* 扩展效果：`NAME`（它在字幕稿 Effect 字段 `fo:…` 里的参数名）+ `elements(line, value)` / `tags(line, value, element)` 至少一个
  （见 `flowocr.typeset.fx` 的文件头和 `examples/fx_minimal.py`）。
"""
from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from pathlib import Path
from types import ModuleType

BUILTIN = {"matcher": "flowocr.analyze.matchers", "preset": "flowocr.output.presets", "fx": "flowocr.typeset.fx"}
"""内置实现所在的包：`load("srt_main", "preset")` 就是 `flowocr.output.presets.srt_main`。"""
REQUIRED = {"matcher": ("match",), "preset": ("render",), "fx": ()}
OPTIONAL = {"matcher": ("cluster_patch",), "preset": (), "fx": ("elements", "tags")}
"""`fx`（阶段 4 的扩展效果）两个挂点至少要有一个，另要 `NAME`——`flowocr.typeset.core.load_fx` 核。"""


def builtin_names(kind: str) -> list[str]:
    pkg = importlib.import_module(BUILTIN[kind])
    return sorted(m.name for m in pkgutil.iter_modules(pkg.__path__) if not m.name.startswith("_"))


def load(spec: str, kind: str) -> ModuleType:
    """按 `spec` 加载一个 `kind`（`matcher` / `preset`）扩展模块并核入口。见文件头。"""
    if kind not in BUILTIN:
        raise ValueError(f"扩展种类只有 {sorted(BUILTIN)}，给的是 {kind!r}")
    p = Path(spec)
    if spec.endswith(".py") or p.is_file():
        if not p.is_file():
            raise FileNotFoundError(f"{kind} 文件不存在：{spec}")
        name = f"flowocr_ext_{kind}_{p.stem}_{abs(hash(str(p.resolve()))) % 10**8}"
        s = importlib.util.spec_from_file_location(name, p)
        if s is None or s.loader is None:
            raise ImportError(f"加载不了 {spec}")
        mod = importlib.util.module_from_spec(s)
        sys.modules[name] = mod          # dataclass / pickle 之类要按模块名找得回来
        s.loader.exec_module(mod)
    elif "." in spec or "/" in spec or "\\" in spec:
        mod = importlib.import_module(spec)
    else:
        full = f"{BUILTIN[kind]}.{spec}"
        try:
            mod = importlib.import_module(full)
        except ModuleNotFoundError as e:
            if e.name in (full, BUILTIN[kind]):
                raise LookupError(f"没有内置的 {kind} `{spec}`；内置的有：{builtin_names(kind)}。"
                                  f"自己写的给模块名或 .py 路径") from None
            raise
    missing = [f for f in REQUIRED[kind] if not callable(getattr(mod, f, None))]
    if missing:
        raise TypeError(f"{kind} `{spec}` 缺入口 {missing}（要的是 {list(REQUIRED[kind])}，"
                        f"可选 {list(OPTIONAL[kind])}）")
    return mod
