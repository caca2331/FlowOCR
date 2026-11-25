"""采样帧时间戳的唯一判据：只认解码器给的真实 PTS，且必须严格递增。

**单独放一个文件是为了让守卫够得着**——和 `flowocr.extract.ocr_complete` 同一个理由
（audit-4 C8）：判据写在 `run_ocr2.py`（当时在 paddleocr 实验目录）里的话，那个文件
import cv2 和 paddleocr，共享层的自检够不着，够不着就等于测不了。
而这条判据恰好属于**错了不会报错**的一类：一条坏时间轴不会让任何下游抛异常，
只会让所有时间都错一点。

## 为什么不能再用 `idx / src_fps`

当时的 `src_fps` 是 `CAP_PROP_FPS`，即**容器的平均帧率**。直播录像有丢帧缺口时它低于
标称的 60，于是帧计数 × (1/59.978) 在缺口**之前**就已经比真实 pts 慢，
缺口之后还要再跳一次。实测（fps-and-rec-budget 报告）：

| 素材 | `src_fps` | pts 缺口 | 管线比真实 pts 晚 |
| --- | ---: | --- | --- |
| f1 / f2 / f5 | 60.000 | 无 | 0 |
| f3 | 59.978 | 两处（5.2 s + 7.0 s） | 7.2 h 处 **9.5 s** |
| f4 | 59.987 | 一处（6.0 s） | 6.4 h 处 **5.0 s** |

旧产物**不能**乘 `src_fps / round(src_fps)` 修回来：那只消掉伸缩，
缺口之后仍缺累计丢帧时长。

## 和帧身份的关系

`t_us` 由 `framegrid.frame_key` 换算（唯一换算处）；喂进来的是取帧层交出的相对秒（`Frame.t`，已减过容器的 `start_time`），
所以这里推出来的 `t_us` 和取帧层交出的帧身份是同一个数。
"""
from __future__ import annotations

import math

from flowocr.extract.framegrid import frame_key   # 帧身份的唯一换算处


class BadPts(ValueError):
    """解码器给的时间戳不可信。**不要吞掉它**——产物必须判为未完成。"""


class PtsClock:
    """按顺序喂进每个采样帧的 pts（秒），换出 `t_us`（微秒整数）。

    三道门，任何一道不过就抛 `BadPts`：非有限、为负、不严格递增。

    严格递增而不是非递减：两个采样帧拿到同一个 `t_us`，下游会把两次观测
    叠成一个时刻——这既不会报错，也数不出来。
    """

    def __init__(self) -> None:
        self.first_us: int | None = None
        self.last_us: int | None = None
        self.n: int = 0

    def push(self, t_sec: float, *, where: str = "") -> int:
        tag = f"{where}：" if where else ""
        if not math.isfinite(t_sec) or t_sec < 0:
            raise BadPts(f"{tag}解码器给的 PTS 无效（{t_sec!r}）")
        t_us = frame_key(t_sec)
        if self.last_us is not None and t_us <= self.last_us:
            raise BadPts(f"{tag}PTS 没有严格递增"
                         f"（{self.last_us / 1e6:.6f}s -> {t_sec:.6f}s）")
        if self.first_us is None:
            self.first_us = t_us
        self.last_us = t_us
        self.n += 1
        return t_us

    @property
    def span_sec(self) -> float:
        """覆盖到的 pts 跨度（秒）。一帧都没有时是 0。"""
        if self.first_us is None or self.last_us is None:
            return 0.0
        return (self.last_us - self.first_us) / 1e6

    def meta(self) -> dict:
        """写进 `_meta` 的那几个键。`timebase` 是下游认时间轴的唯一凭据。"""
        return {"timebase": "pts",
                "pts_first_sec": self.first_us / 1e6 if self.first_us is not None else None,
                "pts_last_sec": self.last_us / 1e6 if self.last_us is not None else None}
