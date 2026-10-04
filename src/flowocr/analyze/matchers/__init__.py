"""内置匹配器。一个模块一个匹配器，入口形状见 `flowocr.extensions`（`match` 必有、`cluster_patch` 可选）。

现在只有 `gametext`（游戏文本库：原神 / 星铁 / 绝区零）。用户自己写的匹配器不必放进来——
`extensions.load("<路径或模块名>", "matcher")` 同样能加载。
"""
