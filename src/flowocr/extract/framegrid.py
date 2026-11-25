"""取哪些帧：**按时间桶取第一帧**（frame-pts 计划，2026-11-23）。帧的身份就是它自己的 pts（`t_us`，`frame_key`），
不再猜"帧身份用的帧率"。

* **时间轴**（`Axis`）：`t_us` = 绝对 pts − S，S 是容器的 `format.start_time`（整数 µs）——整片跑一直以来的语义，
  也是播放器和阶段 5 烧录用的时间轴。桶号、窗口、`-ss` 都在减过 S 的时间上；ffprobe 读的包 pts 是绝对的，
  两边的换算只在 `Axis` 里做。素材库里 S 几乎全是 0。

* **桶**：生效帧率 f = N/D（有理数）上，第 k 个目标点 `T_k = k / f`，锚在 0 上；一帧的桶号 `floor((t + δ) · f)`，δ = `DELTA`。
  一帧被选中 ⇔ 它的桶号和**上一个输入帧**的不同（首帧没有上一帧，算新桶）——等价于"每个非空桶取第一帧"，
  也就是"在目标点取该时刻及之后的第一帧、取到同一帧只留一帧"。
* **为什么要 δ**：很多容器的 pts 被取整（yuka 1/16000 时间基按毫秒取整、gi / hsr 的 1/15360 有几十 µs 的舍入），本该正好落在目标点上的帧
  会早零点几毫秒、被算进上一个桶。δ ≈ 1 ms 在 quick 集上和旧规则逐帧相同、0.5 ms 不够；多出的半微秒让毫秒、1/16000、1/15360、
  1/24000、1/90000 这类时间基上最近的帧也离桶边界 0.5 µs（片段 4 有一帧正好早 1 ms）。
* **两边同一个算法**（一致性靠构造，不靠余量）：ffmpeg 的 `select` 写 `floor((t-S+0.0010005)*N/D)`，`t = pts × av_q2d(time_base)`；
  Python **逐位复刻这串双精度运算**（`Axis.sec` + `Clock.cell_t`），运算顺序同表达式的解析顺序。精确有理数只留在守卫里当参照。
* `SampleClock`：采样流（`--fps`）。`AuxClock`：辅助流，自己的桶 ∪ 采样桶（两种桶的公共细分里每格取第一帧），采样帧一定在辅助流里。

**纯函数，守卫直接测。**
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

DELTA = "0.0010005"
"""桶号里的 δ（秒）：写进 ffmpeg 表达式的字面，Python 侧 `float(DELTA)` 是同一个双精度数。"""
DELTA_US = 1000.5
"""同一个 δ（微秒），写进 `_meta.cell_tol_us`。"""
_DELTA = float(DELTA)

FPS_DEN = 10_000
"""浮点帧率换成有理数时分母的上限：59.94（60000/1001）、23.976（24000/1001）这类都精确落得下。"""
CAP_TOL = 1e-3
"""辅助流上限落到"名义帧率 / k"的容差（相对）：60.0000x 这类名义帧率不算超。"""
SNAP_DEN = 100
SNAP_REL = 1e-5
"""`--fps` 和名义帧率之比离一个**分母 ≤ `SNAP_DEN` 的简单分数**不到 `SNAP_REL`（相对）就吸附成"名义帧率 × 那个分数"（2026-09-24 审计）。
为什么：用户敲的是 `29.97` / `59.94`，素材的名义帧率是 30000/1001 / 60000/1001——比值 999999/1000000 离 1 只差 1e-6，
不吸附的话目标点每 4.6 h 和帧错开一帧。真不整除的（59.94 上 2 fps，比值离 1/30 差 1e-3）不受影响。"""


def rate_of(x) -> Fraction | None:
    """帧率 -> 有理数：ffprobe 的 `60000/1001` 串原样精确取；浮点数按 `FPS_DEN` 取最近的分数；0 / 空 / `0/0` 给 None。"""
    if x is None or x == "":
        return None
    if isinstance(x, Fraction):
        return x if x > 0 else None
    if isinstance(x, str):
        try:
            num, den = (x.split("/") + ["1"])[:2]
            f = Fraction(int(num), int(den))
        except (ValueError, ZeroDivisionError):
            return None
        return f if f > 0 else None
    f = Fraction(float(x)).limit_denominator(FPS_DEN)
    return f if f > 0 else None


def tb_of(x) -> tuple[int, int]:
    """时间基 -> `(num, den)`：`1/15360` 串或二元组。"""
    if isinstance(x, str):
        a, b = x.split("/")
        return int(a), int(b)
    return int(x[0]), int(x[1])


def frame_key(pts_time, origin_us: int = 0) -> int:
    """帧身份：`round(pts_time × 1e6) − origin_us`（整数微秒）。**唯一换算处**：帧环的键、证据里的时刻、obs 行都用它。
    showinfo 打的是绝对时刻，取帧层经 `Axis.key` 减 S；已经是相对时刻的（`PtsClock` 收的 `Frame.t`）不用给 `origin_us`。"""
    return int(round(float(pts_time) * 1_000_000)) - origin_us


@dataclass(frozen=True)
class Axis:
    """一路视频流的时间轴：时间基 `tb` + 原点 S（`origin_us`，容器的 `format.start_time`，整数 µs）。
    **绝对 pts 和相对时刻（`t_us`、桶、窗口用的那把尺）之间的换算只在这里做**：
    ffprobe 读的包 pts、showinfo 的 `pts:` / `pts_time` 都是绝对的（取帧层一律 `-copyts`），`sec` / `key` 减 S，`abs_sec` 加回去。"""
    tb: tuple[int, int]
    origin_us: int = 0

    @property
    def origin_sec(self) -> float:
        return self.origin_us / 1_000_000

    def pts_abs(self, pts: int) -> float:
        """整数 pts -> 绝对秒，逐位同 ffmpeg 的 `t`：`(double)pts × av_q2d(tb)`，`av_q2d = num / (double)den`。"""
        return float(pts) * (self.tb[0] / self.tb[1])

    def sec(self, pts: int) -> float:
        """整数 pts -> 相对秒，**逐位同 ffmpeg 表达式里的 `t-S`**（`t_expr`）：S 写成 `整数/1000000`，两边是同一个双精度数；
        `t-S` 在 ffmpeg 里解析成 `t + (−S)`，和这里的减法逐位相同。"""
        return self.pts_abs(pts) - self.origin_sec

    def abs_sec(self, rel: float) -> float:
        """相对秒 -> 绝对秒（给 ffprobe 的 `-read_intervals`：它不按 `start_time` 平移）。"""
        return rel + self.origin_sec

    def key(self, pts_time) -> int:
        """showinfo 的绝对 `pts_time` -> 帧身份 `t_us`（减 S）。"""
        return frame_key(pts_time, self.origin_us)

    def t_expr(self, var: str = "t") -> str:
        """ffmpeg 表达式里的相对时刻：S = 0 时就是 `var`，否则 `(var-S)`（S 写成整数 µs / 1000000）。"""
        o = self.origin_us
        if o == 0:
            return var
        return f"({var}{'-' if o > 0 else '+'}{abs(o)}/1000000)"


class Clock:
    """一种桶：生效帧率 `N/D`。"""
    __slots__ = ("n", "d")

    def __init__(self, rate) -> None:
        f = rate_of(rate)
        if f is None:
            raise ValueError(f"桶的帧率要为正：{rate!r}")
        self.n, self.d = f.numerator, f.denominator

    @property
    def rate(self) -> Fraction:
        return Fraction(self.n, self.d)

    @property
    def fps(self) -> float:
        return self.n / self.d

    @property
    def interval_us(self) -> float:
        """名义间隔（微秒）。"""
        return 1e6 * self.d / self.n

    def as_list(self) -> list[int]:
        return [self.n, self.d]

    # ---------- 桶号（Python 逐位复刻 ffmpeg）----------
    def cell_t(self, t: float) -> int:
        """相对秒 -> 桶号：`floor(((t + δ) * N) / D)`，和 ffmpeg 表达式 `floor((t-S+0.0010005)*N/D)` 同一串双精度运算（`t` 已减过 S）。"""
        return math.floor(((t + _DELTA) * self.n) / self.d)

    def cell(self, pts: int, axis: Axis) -> int:
        """整数 pts（绝对，`axis` 的时间基）-> 桶号。"""
        return self.cell_t(axis.sec(pts))

    def cell_exact(self, pts: int, axis: Axis) -> int:
        """同一个桶号的**精确有理数**版本（守卫里当参照；不在生产路径上用）。"""
        t = Fraction(pts * axis.tb[0], axis.tb[1]) - Fraction(axis.origin_us, 1_000_000)
        return math.floor((t + Fraction(DELTA)) * self.rate)

    def expr(self, t: str = "t") -> str:
        """ffmpeg 表达式：相对时刻 `t`（`Axis.t_expr` 给的）的桶号（逗号不用转义：里面没有）。"""
        return f"floor(({t}+{DELTA})*{self.n}/{self.d})"

    def changed(self, axis: Axis) -> str:
        """ffmpeg 表达式：这一帧的桶号和上一个输入帧（`prev_t`）的不同。首帧 `prev_t` 是 NaN、`eq` 为假，按新桶选中。"""
        return rf"not(eq({self.expr(axis.t_expr('t'))}\,{self.expr(axis.t_expr('prev_t'))}))"

    # ---------- 目标点 ----------
    def start_sec(self, k: int) -> Fraction:
        """第 k 个目标点 `T_k = k / f`（精确秒）。桶 k 是 `[T_k − δ, T_{k+1} − δ)`。"""
        return Fraction(k * self.d, self.n)

    def cell_start(self, k: int) -> float:
        """桶 k 的起点（秒）：`T_k − δ`。"""
        return float(self.start_sec(k)) - _DELTA

    def start_us(self, k: int) -> int:
        """第 k 个目标点（整数微秒，四舍五入）。"""
        return round(self.start_sec(k) * 1_000_000)

    def nearest(self, sec: float) -> int:
        """离 `sec` 秒最近的目标点的桶号（窗口起止吸附用）。"""
        return round(Fraction(sec) * self.rate)

    def __eq__(self, other) -> bool:
        return isinstance(other, Clock) and type(other) is type(self) and (self.n, self.d) == (other.n, other.d)

    def __hash__(self) -> int:
        return hash((type(self).__name__, self.n, self.d))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.n}/{self.d})"


class SampleClock(Clock):
    """采样流的桶（`--fps`）。"""
    __slots__ = ()

    @classmethod
    def for_rate(cls, nominal, fps: float) -> "SampleClock":
        """生效采样帧率：就是 `--fps`；它和名义帧率之比离一个简单分数不到 `SNAP_REL` 时吸附成"名义帧率 × 那个分数"。
        名义帧率在这里只决定目标点的间距，报错了也不会撞号。"""
        want = rate_of(fps)
        if want is None:
            raise ValueError(f"采样帧率要为正：{fps!r}")
        nom = rate_of(nominal)
        if nom is not None:
            r = want / nom
            s = r.limit_denominator(SNAP_DEN)
            if abs(s - r) <= SNAP_REL * r:
                want = nom * s
        return cls(want)

    def describe(self) -> str:
        return f"按时间桶 {self.fps:.4g} fps（{self.n}/{self.d}）"


class AuxClock:
    """辅助流：自己的桶 ∪ 采样桶。一帧入选 ⇔ 辅助桶换了**或**采样桶换了（两种桶的公共细分里每格取第一帧），采样帧一定在里面。"""
    __slots__ = ("own", "sample")

    def __init__(self, own: Clock, sample: SampleClock) -> None:
        self.own = own if isinstance(own, Clock) else Clock(own)
        self.sample = sample

    @classmethod
    def for_video(cls, nominal, cap: float, sample: SampleClock) -> "AuxClock":
        """辅助帧率 = 名义帧率 / k（k = max(1, round(名义 / 上限))），结果不超过上限的 `CAP_TOL` 时；否则就是上限本身。
        名义帧率 ≤ 上限时 k = 1：CFR 素材上每帧都要；119.88 fps 给 60 就是隔帧。名义帧率不可信的素材也一样按名义 / k
        （owner 2026-11-23 暂按建议：时间基推出来的 `1000/1` 这类会丢约 2% 辅助帧，素材库里没有这种文件）。"""
        nom = rate_of(nominal)
        top = rate_of(cap)
        if top is None:
            raise ValueError(f"辅助流的帧率上限要为正：{cap!r}")
        if nom is not None:
            k = max(1, round(nom / top))
            if nom / k <= top * (1 + Fraction(CAP_TOL)):
                return cls(Clock(nom / k), sample)
        return cls(Clock(top), sample)

    @classmethod
    def from_list(cls, g: list[int]) -> "AuxClock":
        """`as_list()` 的逆：`[N, D, 采样 N, 采样 D]`（跨进程 / 进 `_meta` 的写法）。"""
        return cls(Clock(Fraction(g[0], g[1])), SampleClock(Fraction(g[2], g[3])))

    def as_list(self) -> list[int]:
        return self.own.as_list() + self.sample.as_list()

    @property
    def fps(self) -> float:
        return self.own.fps

    def cells(self, pts: int, axis: Axis) -> tuple[int, int]:
        """`(采样桶号, 辅助桶号)`，Python 逐位复刻 ffmpeg。"""
        t = axis.sec(pts)
        return self.sample.cell_t(t), self.own.cell_t(t)

    def changed(self, axis: Axis) -> str:
        """ffmpeg 表达式：辅助桶或采样桶和上一个输入帧的不同。"""
        o, s, t, p = self.own, self.sample, axis.t_expr("t"), axis.t_expr("prev_t")
        return rf"not(eq({o.expr(t)}\,{o.expr(p)})*eq({s.expr(t)}\,{s.expr(p)}))"

    def targets_us(self, lo: int, hi: int) -> list[int]:
        """`[lo, hi]`（微秒）里辅助流的**目标点**（辅助桶与采样桶的 `T_k`，整数微秒、升序去重）。
        阶段 2 结算首字 / 全字时把候选时刻吸附到这上面（证据里不存每帧的真实时刻，见 `edge_refine.sig_from_records`）。"""
        out: set[int] = set()
        for c in (self.own, self.sample):
            k = max(0, math.floor(Fraction(lo, 1_000_000) * c.rate) - 1)     # 往前多退一格，下面按 [lo, hi] 筛
            while True:
                t = c.start_us(k)
                if t > hi:
                    break
                if t >= lo:
                    out.add(t)
                k += 1
        return sorted(out)

    def describe(self) -> str:
        return f"按时间桶 {self.own.fps:.4g} fps（{self.own.n}/{self.own.d}，含采样帧）"

    def __eq__(self, other) -> bool:
        return isinstance(other, AuxClock) and (self.own.n, self.own.d, self.sample) == (other.own.n, other.own.d, other.sample)

    def __hash__(self) -> int:
        return hash((self.own.n, self.own.d, self.sample))

    def __repr__(self) -> str:
        return f"AuxClock({self.own.n}/{self.own.d} ∪ 采样 {self.sample.n}/{self.sample.d})"


class Window:
    """窗口 = 整桶的并集 `[a, b)`（采样桶号）；`b = None` 不设上限、读到文件尾。`--start` / `--end` 吸附到最近的目标点。"""
    __slots__ = ("clock", "a", "b")

    def __init__(self, clock: SampleClock, a: int, b: int | None) -> None:
        if a < 0 or (b is not None and b <= a):
            raise ValueError(f"窗口是空的：桶 [{a}, {b})")
        self.clock, self.a, self.b = clock, a, b

    # 按值比：广播解码的 hub 拿各组经连接 pickle 过来的取帧参数（含 Window）互相比，按身份比会让 2 组以上必然判"不一致"
    def __eq__(self, other) -> bool:
        return isinstance(other, Window) and (self.clock, self.a, self.b) == (other.clock, other.a, other.b)

    def __hash__(self) -> int:
        return hash((self.clock, self.a, self.b))

    @classmethod
    def of(cls, clock: SampleClock, start: float, end: float) -> "Window":
        a = clock.nearest(max(0.0, start))
        b = clock.nearest(end) if end > 0 else None
        if b is not None and b <= a:
            raise ValueError(f"--start {start:g} / --end {end:g} 吸附到目标点之后是空窗口（桶 [{a}, {b})）")
        return cls(clock, a, b)

    @property
    def start_sec(self) -> float:
        return float(self.clock.start_sec(self.a))

    @property
    def end_sec(self) -> float | None:
        return None if self.b is None else float(self.clock.start_sec(self.b))

    @property
    def start_us(self) -> int:
        return self.clock.start_us(self.a)

    def seek_sec(self) -> float:
        """精确 seek 落在 `T_a − δ − 1 ms`（起点之前；≤ 0 时从文件头读）。起点就是桶边界，窗口里第一个落进范围的帧一定是它那个桶的第一帧：
        它的上一帧早于边界，要么被精确 seek 剪掉（select 把这一帧当首帧、算新桶），要么在多退的那 1 ms 里、进了 select 但被范围挡掉。"""
        s = float(self.clock.start_sec(self.a)) - _DELTA - 0.001
        return s if s > 0 else 0.0

    def read_sec(self, seek: float, pad: float = 1.0) -> float | None:
        """输入端 `-t`：从 seek 点读到 `T_b − δ` 所需的时长 + `pad` 秒；不设上限时 None。"""
        if self.b is None:
            return None
        return max(0.0, float(self.clock.start_sec(self.b)) - _DELTA - seek) + pad

    def contains(self, cell: int) -> bool:
        return cell >= self.a and (self.b is None or cell < self.b)

    def span_us(self, k: int) -> int:
        """从窗口起点到桶 k 起点的时长（微秒）。"""
        return self.clock.start_us(k) - self.clock.start_us(self.a)

    def split(self, n: int, b_est: int) -> list[int]:
        """`--workers N` 的切点：`[a, b)` 按桶号等分成 n 段，返回 n−1 个内部切点桶号（严格递增）。`b = None` 时按 `b_est`（容器时长）切。
        段太短切不开时抛。"""
        if n < 2:
            return []
        b = self.b if self.b is not None else b_est
        cuts = [self.a + round((b - self.a) * k / n) for k in range(1, n)]
        pts = [self.a, *cuts, b]
        if any(y <= x for x, y in zip(pts, pts[1:])):
            raise ValueError(f"窗口太短，切不成 {n} 段：桶 {pts}")
        return cuts

    def __repr__(self) -> str:
        return f"Window({self.clock!r}, [{self.a}, {self.b}))"
