"""rec 的**异步消费者**：主线程只提交裁剪，后台线程发批，结果按请求号交回。

组批第二步（inference-runtime 计划那句"做成一个有序提交者"）。
第一步 `--rec-window N` 是**同步**窗口：攒够 N 个采样点，主线程自己把这一批送进 rec——
送的时候整条主循环停着。第二步只搬**发批**这一件事：

* **后台线程只处理不可变的请求**：一块裁剪 -> 一个 `(文本, 分数)`。它不碰链、不碰缓存、不碰 edge。
* **状态转移全留在主线程**，而且仍然**按帧序**（`commit_stage2` 一帧一帧地提交）。
  理由写在 inference-runtime 计划：`_recover` / `_drop_pending` 共享 `pending`、缓存计数和行对象，
  而缓存占用又会回头影响 `decide` 要不要强制真读、回补结果还决定 `stale` 和落盘安不安全——
  "按链加锁"保不住"全局预算 + 落盘顺序"这两个不变量。
* **回补的单框读也走这里**（`rec_one`），于是**每个后端只被它自己那一个线程碰**。
  rec 后端（一条连接的 `OrtRec`）不保证多线程安全，两个线程一起调是会出事的那种 bug。
* **可以有几个后端同时在途**（`--rec-inflight N`，decode-buffer）：每个后端一个发批线程、从同一个队列里挑，
  后端是各自一条连接的 `OrtRec`（接同一个 ORT 服务，模型和显存一份）。结算按请求号、主线程按帧序提交，
  所以几个在途只改"批怎么拼"，不改读哪一帧。

消费判据两条（量出来缺一不可）：

1. **发什么** = `recpack.pick_batch`：取当前排着的请求里最大的那一组（近宽 + 封顶 `--rec-bucket`）。
   **分组键是形状宽**：后端报了 `shape_w`（`OrtRec` 把张量宽收到 320 的倍数）就按它分，和同步路 `recpack.predict_bucketed`
   同一个口径。2026-09-23 之前这里按**精确**张量宽分，330 和 340 两个本来是同一个 `(n, 640)` 形状的裁剪被拆成两次调用——
   异步进默认后 quick 六段的 rec 调用里批 1 占 47%~74%（decode-buffer）。
2. **什么时候发** = 膝点驻留 `--rec-knee K`：**挑出来那一组**不足 K 个就先不发
   （2026-09-19 复审：原来判的是"总排队数"，16 个裁剪散在 8 个宽组时也会触发，发出去只有 2~3 个）。
   队列涨到 `--rec-bucket` 那么深时无条件发一批，免得攒着不动。
   没有这一条时消费者"一有空就抓"，于是**批被吃回去**——实测同步窗口 8 的平均批 3.0~6.4，
   贪心即发只剩 1.4~5.3、调用数反涨 18%~107%（第一版）。膝点取 rec 成本曲线见底的地方
   （Paddle 16、ORT 32）。
   **不需要定时器**：主线程最多领先 `--rec-window` 帧，窗口一满它就带着优先标记来等，
   那时无论攒了多少都发——所以"等"永远有出口，而且**不靠时钟**（少一个不确定性来源）。

主线程快被最老那一帧卡住时把它标成优先（`prio`），含它最多的那一组先走。

⚠ **等待一定要有出口**：`stall_sec` 到了就抛，而不是等到天荒地老（同 `edge_refine.FrameRing` 那条教训）。
"""
from __future__ import annotations

import threading
import time

from flowocr.extract import recpack
from flowocr.extract import timeline as _tl   # --timeline（decode-buffer）


class RecPool:
    """后台发批的 rec 消费者。主线程用 `submit` / `collect`，两者都按**请求号**说话。"""

    def __init__(self, recs: list, cap: int, ratio: float, knee: int = 16,
                 stall_sec: float = 300.0) -> None:
        """`recs`：后端表，一个后端一个发批线程（= 同时在途的请求数）。第一个之外的必须是**独立的**后端（自己的连接）。"""
        self.recs, self.cap, self.ratio = list(recs), max(1, cap), ratio
        self.shape_w = getattr(self.recs[0], "shape_w", None)
        """后端自己把张量宽收到少数几个形状上时（`OrtRec.shape_w`），分组键用**形状宽**、近宽合并不再生效——
        同 `recpack.predict_bucketed`：同形状的本来就该同批，不同形状的并了后端也得拆开。"""
        if self.shape_w is not None:
            self.ratio = 0.0
        self.knee = max(1, min(knee, max(1, cap)))   # 挑出来那一组够这么多才发（1 = 一有空就发）
        self.stall_sec = stall_sec
        self.pend: dict[int, object] = {}       # 请求号 -> 裁剪（还没发出去的）
        self.wid: dict[int, int] = {}           # 请求号 -> 目标宽（分组键）
        self.done: dict[int, tuple | None] = {}  # 请求号 -> (文本, 分数)；后端少返回时是 None
        self.prio: set[int] = set()
        self.calls = 0                          # predict 调用次数（含回补的单框读）
        self.sent = 0                           # 送进去的裁剪数
        self.batch_hist: dict[int, int] = {}    # 批大小 -> 次数（报表用）
        self.shape_hist: dict[int, dict[int, int]] = {}   # 分组宽 -> {批大小 -> 次数}（批填得多满，按宽拆开看）
        self.busy_sec = 0.0                     # 发批线程真在推理的墙钟（几个在途时是各线程之和，可以超过墙钟）
        self.error: BaseException | None = None
        self._next = 0
        self.closed = False
        self.cv = threading.Condition()
        self.ths = [threading.Thread(target=self._run, args=(r,), daemon=True,
                                     name="rec-pool" if k == 0 else f"rec-pool-{k}")
                    for k, r in enumerate(self.recs)]
        for th in self.ths:
            th.start()

    # ---------- 主线程这边 ----------
    def new_ids(self, n: int) -> list[int]:
        """要 n 个请求号。**只在主线程调**（请求号的顺序就是提交顺序）。"""
        ids = list(range(self._next, self._next + n))
        self._next += n
        return ids

    def submit(self, items: list[tuple[int, object]]) -> None:
        """提交 [(请求号, 裁剪)]。裁剪必须是**独立的数组**（整帧的切片会被下一帧覆盖）。"""
        if not items:
            return
        with self.cv:
            self._check()
            for rid, crop in items:
                self.pend[rid] = crop
                tw = recpack.rec_target_w(*crop.shape[:2])
                self.wid[rid] = self.shape_w(tw) if self.shape_w is not None else tw
            self.cv.notify_all()

    def collect(self, rids: list[int], block: bool = True,
                prio: bool = False) -> dict[int, tuple | None] | None:
        """取这些请求的结果。**齐了才给**（没齐时返回 None，不半途取走——取走就再也等不到了）。

        `prio=True`：这些请求卡着落盘水位，插队。
        """
        with self.cv:
            self._check()
            if prio:
                add = {r for r in rids if r not in self.done}
                if add - self.prio:
                    self.prio |= add
                    self.cv.notify_all()
            t0 = time.perf_counter()
            while not all(r in self.done for r in rids):
                if not block:
                    return None
                self.cv.wait(0.5)
                self._check()
                if time.perf_counter() - t0 > self.stall_sec:
                    raise RuntimeError(
                        f"rec 消费者卡了 {self.stall_sec:.0f} s：等 {len(rids)} 个请求，"
                        f"排队 {len(self.pend)}、已完成 {len(self.done)}")
            self.prio -= set(rids)
            return {r: self.done.pop(r) for r in rids}

    def close(self) -> None:
        """收工：排空队列、等后台线程退出。**等不到就抛**——"等某个一定会来的东西"的地方必须有出口
        （2026-09-19 审计：`collect` 有 `stall_sec`，这里原来没有，线程卡住就静默当成功）。"""
        with self.cv:
            self.closed = True
            self.cv.notify_all()
        t_end = time.perf_counter() + self.stall_sec
        for th in self.ths:
            th.join(timeout=max(0.0, t_end - time.perf_counter()))
        self._check()
        if any(th.is_alive() for th in self.ths):
            raise RuntimeError(
                f"rec 消费者 {self.stall_sec:.0f} s 没退出：还排着 {len(self.pend)} 个请求")

    def stats(self) -> dict:
        return {"calls": self.calls, "sent": self.sent,
                "avg_batch": round(self.sent / max(1, self.calls), 2),
                "busy_sec": round(self.busy_sec, 2), "knee": self.knee, "inflight": len(self.recs),
                "batch_hist": {str(k): v for k, v in sorted(self.batch_hist.items())},
                "shape_hist": {str(w): {str(k): v for k, v in sorted(h.items())}
                               for w, h in sorted(self.shape_hist.items())}}

    def _check(self) -> None:
        if self.error is not None:
            raise self.error
        if not self.closed and not all(th.is_alive() for th in self.ths):
            raise RuntimeError("rec 消费者线程已经死了（上面应该有它的异常）")

    # ---------- 后台线程这边 ----------
    def _pick(self) -> tuple[list[int], list[int]]:
        """挑下一批（调用方持锁）：返回 (请求号表, 选中的下标)。`_fire` 和 `_run` 用的是同一份判据。"""
        rids = sorted(self.pend)
        widths = [self.wid[r] for r in rids]
        prio = {k for k, r in enumerate(rids) if r in self.prio}
        return rids, recpack.pick_batch(widths, self.ratio, self.cap, prio)

    def _fire(self) -> bool:
        """现在该发吗（调用方持锁）：关了 / **还排着的**里有优先项 / 挑出来那一组够膝点 / 队列已经很深。

        ⚠ 两处都踩过：①判的是 `prio ∩ pend`，不是 `prio` 本身——优先项做完之后、主线程来取走之前
        它还挂在 `prio` 里，拿整个 `prio` 判会退回"一有空就发"；②膝点要看**挑出来那一组**的大小，
        不是总排队数（2026-09-19 复审）——散在八个宽组里的十六个裁剪，发出去也只有两三个。
        """
        if not self.pend:
            return False
        if self.closed or any(r in self.prio for r in self.pend):
            return True
        if len(self.pend) >= self.cap:          # 队列很深了就别再攒（攒着也拼不出更大的同宽组）
            return True
        return len(self._pick()[1]) >= self.knee

    def _run(self, rec) -> None:
        while True:
            with self.cv:
                while not self._fire():
                    if self.closed and not self.pend:
                        return                  # 关了且排空
                    self.cv.wait(0.5)
                rids, pick = self._pick()
                batch = [(rids[k], self.pend.pop(rids[k])) for k in pick]
                width = max(self.wid.pop(rid) for rid, _ in batch)
            try:
                t0 = time.perf_counter()
                res = list(rec.predict([c for _, c in batch], batch_size=len(batch)))
                dt = _tl.span("rec_infer", t0, a=len(batch)) - t0
            except BaseException as exc:        # noqa: BLE001 —— 线程里的异常必须带回主线程
                with self.cv:
                    self.error = exc
                    self.cv.notify_all()
                return
            # 后端少返回时**不许错位**：缺的位置补 None，由主线程记进 n_lost
            res = res + [None] * (len(batch) - len(res))
            with self.cv:
                self.calls += 1
                self.sent += len(batch)
                self.busy_sec += dt
                self.batch_hist[len(batch)] = self.batch_hist.get(len(batch), 0) + 1
                h = self.shape_hist.setdefault(width, {})
                h[len(batch)] = h.get(len(batch), 0) + 1
                for (rid, _), r in zip(batch, res):
                    self.done[rid] = (None if r is None
                                      else (r["rec_text"], float(r["rec_score"])))
                self.cv.notify_all()
