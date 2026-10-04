"""复用判据第二版（`run_ocr2 --reuse-v2`，**2026-09-17 起默认开**，`--no-reuse-v2` 退回旧判据）：reuse-budget 计划的落地配方。

和默认判据（上一帧同位置 IoU >= 0.7、整框 ncc >= --reuse-corr、连续沿用 --refresh-every 帧强制重读）比，多四样：
  1. **按手里的文本分门**：上次真读到的文本含任何数字 -> 门是 --reuse-corr-digit（0.98），其余 -> --reuse-corr（v2 下默认 0.8）。
  2. **框宽门**：|本帧框宽 - 链上框宽| > --reuse-dw 就不沿用（打字机在长字、det 框在变宽，像素判据看不见）。
  3. **链首跨空档**：上一帧没有 IoU >= 0.7 的框时，依次试上一帧 IoU >= --reuse-link-iou 的框、再上一帧同位置的框、
     最近 --reuse-mem 个采样点内在同一位置结束的链；像素门在**本帧的框**上对那一帧算，门 --reuse-link-corr。
     **拦下就当新链、不回补旧链**（回补旧链尾巴会把收益吃光）。
  4. **回补**：沿用过的帧把送进 rec 的那块裁剪留在缓存里（全局 --reuse-cache-mb，超了当场真读——
     **预留和放行是一个原子动作**，见 decide 里那一段）；真读出来的文本 != 手里的，就在缓存里递归二分
     把变化点找出来（端点相同就剪枝），改掉已缓冲、还没落盘的行。
     所以 obs 行**延迟落盘**（refresh + mem + 2 个采样点），顺序不变。
     回补一旦把变化点前移，这一段的**在线回抠证据就作废**（`edge_stale`，见 commit 里那一段）。

产物格式不变：沿用的行仍是 `reused: true`；跨空档沿用的多一个 `link` 字段；回补时被真读的那几帧变成非沿用行。
这些参数只在 `--reuse-v2` 打开时才进 `_meta.config`（flowocr.extract.ocr_args 的 OPT_IN）；`--reuse-corr` / `--refresh-every` 的默认跟着它走（`ocr_args.resolve`）。
"""
from __future__ import annotations

import itertools
import json
import os
import re
import time

import numpy as np

from flowocr.extract import framesource          # `pts_frame`：obs 的 `frame` 认 pts（判据只有那一份）

_PUNCT = re.compile(r"[\W_]+")


def same_text(a: str, b: str) -> bool:
    """回补里"文本变没变"的判据：**去掉标点和空白之后**比（2026-09-24，ocr-regions 计划末尾）。
    框抖一下 rec 就可能掉一个 `、`：`…如く、` 重读成 `…如く`，原来逐字比 -> 判"变化点前移" -> 这一段的在线证据作废、
    那一句不回抠（yuka-f1 遮罩臂首字晚 13 帧）。两边都只剩标点时仍逐字比（`……！` 和 `？？` 是两句）。
    ⚠ 去空白这一半只对 CJK 口径成立：拉丁文里 `no where` 和 `nowhere` 会被判成同一句（本项目语料是日 / 中文，暂不区分）。"""
    na, nb = _PUNCT.sub("", a), _PUNCT.sub("", b)
    return na == nb if (na or nb) else a == b

_UID = itertools.count(1)


def iou(a: list[int], b: list[int]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def hasdigit(t: str) -> bool:
    return any(ch.isdigit() for ch in t)


class Chain:
    __slots__ = ("box", "text", "conf", "age", "last_seq", "pending", "cache_bytes", "ended_at", "uid", "last_row",
                 "last_idx", "inflight", "confirmed", "read_w", "severed")

    def __init__(self, box, text, conf, seq):
        self.box, self.text, self.conf, self.age, self.last_seq = box, text, conf, 0, seq
        self.pending: list[list] = []          # [seq, row(dict), crop(np.ndarray)]，从上次真读起沿用过的帧
        self.cache_bytes = 0
        self.ended_at: int | None = None
        self.uid = next(_UID)                  # 两遍合一遍（edge_refine）按它认链：id() 会被回收复用
        self.last_row: dict | None = None
        self.last_idx = -1
        self.inflight = 0
        """这条链上**还没结算的真读**有几个（`--rec-window` 的窗口里才会大于 0）。见 `text_pending`。

        2026-09-23（Codex 审计 P1）：原来是一个布尔，结算**任何一个**读就清掉——W=4 下同一条链连着三帧都真读时，
        最老那个结算完、后两个还在途，链却已经是"文本已知、已证实"，下一帧按 0.8 放行了。计数才对得上"有结果待返回"。"""
        self.severed = False
        """自选范围的时间切点切到了这条链（`ReuseV2.sever`）：之后不许再被接上（owner："时间关闭段不可续链"）。"""
        self.confirmed = False
        """这条链的文本**被连续两次真读证实过**（后一次真读和手里的文本逐字相同）。没证实之前像素门走严格门（`--reuse-confirm`）。

        为什么（2026-09-22，decode-buffer）：打字机的中间态被真读之后，句尾再长出一两个字只占整框很小的像素份额，
        整框相关系数 0.94 过得了非数字的 0.8 门，于是**半截读数被一路沿用到字消失**（yuka-f5 14954 s 缺句尾 `も`，
        6 秒里没有一次真读）。文字刚出现、还没被第二次读证实的那段时间正是打字机在长的时候，这时只认 0.98 的严门。
        证实之后照旧 0.8——长期稳定的字背景一动就要重读，那是 reuse-v2 当初放宽的理由，不动它。"""
        self.read_w: int | None = None
        """最近一次**真读**时这个框的宽（`--reuse-dw-anchor`）。宽度门原来和上一个采样点的框比（`chain.box` 每帧都跟着推），
        字从面板边缘一点点露出来时每一步只挪 2~4 px，永远过不了 `--reuse-dw 8`，而从真读到现在已经累计宽了 10 px
        （wuwa-s2 65~69 s `白銀みさ` → `白銀みさき`，decode-buffer）。"""

    @property
    def text_pending(self) -> bool:
        """**还有真读在途**（策略 B，inference-runtime 计划）：像素门因此走严格门，判决不依赖未知文本。"""
        return self.inflight > 0


class ReuseV2:
    def __init__(self, args, corr_fn, estimate_shift_fn, rec_one):
        self.src_fps = 0.0        # run_ocr2 起完就填（obs 的 `frame` 认 pts 要用）
        self.a = args
        self.corr, self.estimate_shift = corr_fn, estimate_shift_fn
        self.rec_one = rec_one                 # crop -> (text, conf)，回补用；调用方自己数 rec
        self.rec_many = None                   # [crop] -> [(text, conf)]；给了就**按层**发回补（见 _recover）
        self.edge = None                       # 两遍合一遍：edge_refine.EdgeRefiner（--refine-fused 时由 run_ocr2 挂上）
        self.live: list[Chain] = []
        self.ended: list[Chain] = []
        self.grays: dict[int, np.ndarray] = {}   # 采样点 -> 灰度帧（uint8，`_crop` 取出时再转 float32）
        self.buf: dict[int, list[dict]] = {}     # 采样点 -> 该帧的行（延迟落盘）
        self.cache = 0
        self.reserved: dict[int, int] = {}       # 本帧放行沿用的框 -> 已经**预留**的缓存字节（见 decide）
        self.cache_cap = int(args.reuse_cache_mb * (1 << 20))
        self.horizon = (args.refresh_every or 0) + args.reuse_mem + 2
        self.seq = -1
        self.stats = {"reuse_prev": 0, "reuse_moved": 0, "reuse_assoc": 0, "reuse_gap1": 0, "reuse_memory": 0,
                      "recover_reads": 0, "recover_events": 0, "cache_forced": 0, "cache_peak": 0,
                      "cache_overrun": 0, "edge_stale": 0,
                      "gate_digit": 0, "gate_unconfirmed": 0, "gate_dw": 0, "gate_pix": 0, "refresh": 0}
        # `decide` / `commit` 内部的分段计时（进 `_meta.reuse_v2_sec`）。**先量再挪**：
        # "把 decide / commit 挪出主线程"之前要知道里头到底是谁占时间——这个项目在"凭印象归因"上栽过两次
        # （docs/dev-guide/pitfalls.md「优化之前先 profile」）。`gate` 里就是逐框那次相关系数，是搬得动的那一块。
        self.sec = {"match": 0.0, "gate": 0.0, "rows": 0.0, "pending": 0.0, "recover": 0.0,
                    "edge": 0.0, "flush": 0.0}
        self.trace: list | None = None
        """`run_ocr2 --trace` 时挂上一个 list：**逐框记下真实的因果**——链的 uid、链接类型、
        拦下它的是哪道门、相关系数、手里的文本含不含数字、以及最终是沿用还是真读。

        为什么要它（2026-09-18 审计第 5 条）：窗口延迟读的收益原来是拿 **obs** 模拟的，而 obs 里
        没有链 id（只能用框坐标近似）、没有门的类型、而且**文本已经是回补之后的**——
        审计指出这几点都会让模拟偏。有了这份轨迹就能**按因果顺序重放**，不用再近似。
        ⚠ 只记、不改判决；`--trace` 在 `NON_PRODUCT` 里，不进复用判据。"""

    @staticmethod
    def _crop(gray: np.ndarray, box: list[int]):
        """灰度帧上按框裁（同 run_ocr2.gray_crop 的尺寸门）。存的是 uint8、取出转 float32：
        和存 float32 整帧**逐位相同**（cvtColor 出来就是 uint8，astype 是精确的），而 `grays` 要留
        `--reuse-mem + 2` 帧，1080p 下 8.3 MB/帧 × 18 × 3 worker = 450 MB，4K 上四倍——
        这和"缓存上限让内存与分辨率无关"的设计目标相抵（2026-09-17 审计）。"""
        h, w = gray.shape[:2]
        x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])
        if x1 - x0 < 6 or y1 - y0 < 6:
            return None
        return gray[y0:y1, x0:x1].astype(np.float32)

    def crop_bytes(self, shape, box: list[int]) -> int:
        """回补要缓存的那块裁剪有多少字节，**口径同 run_ocr2.rec_crop 但按上界算**
        （外扩按 `--rec-pad-x/y` 满算、不管邻框和 std 门会不会把它砍掉），所以 commit 里必然放得下。"""
        h, w = shape[0], shape[1]
        bh = max(1, box[3] - box[1])
        px, py = int(self.a.rec_pad_x * bh), int(self.a.rec_pad_y * bh)
        y0, y1 = max(0, box[1] - py), min(h, box[3] + py)
        x0, x1 = max(0, box[0] - px), min(w, box[2] + px)
        return max(0, y1 - y0) * max(0, x1 - x0) * 3

    # ---- 每帧第一步：决定哪些框沿用、哪些送 rec ----
    def _match(self, seq: int, boxes, live, ended) -> tuple[dict, tuple[int, int]]:
        """返回 (框下标 -> (链, 链接类型, 参照采样点, 参照框), 整帧位移)。**decide 和跨空档接链共用这一份。**

        **一帧内一条链只能被一个框认领，匹配分高者得**（2026-09-16 实跑排障）：链是共享的可变状态，
        两个框同一帧认领同一条链时，后处理的那个真读会把链上的文本改成自己的——gi-s1 里下方那块名牌
        经位移匹配（整列上移 96 px）接到上方名牌的链上，把 `サンドローネ` 改成了 `シャルロット`，再沿用 3 s。
        默认逻辑没有这个问题（prev 是不可变的列表，真读另起一条）。先给每个框算候选，再按分数排序独占分配。
        """
        a = self.a
        shift = self.estimate_shift([c.box for c in live], boxes) if live else (0, 0)
        cand = []                               # (score, i, chain, link, ref_seq, ref_box)
        for i, b in enumerate(boxes):
            if not live:
                continue
            b_back = [b[0] - shift[0], b[1] - shift[1], b[2] - shift[0], b[3] - shift[1]]
            for c in live:
                s_static, s_moved = iou(b, c.box), iou(b_back, c.box)
                if max(s_static, s_moved) >= a.reuse_iou:
                    moved = s_moved > s_static
                    # 静止匹配优先于位移匹配（同分时静止的排前面）
                    cand.append((max(s_static, s_moved) + (0.0 if moved else 1.0), i, c,
                                 "moved" if moved else "prev", seq - 1, (c.box if moved else b)))
                elif s_static >= a.reuse_link_iou:
                    cand.append((s_static, i, c, "assoc", seq - 1, b))
        cand.sort(key=lambda x: -x[0])
        pick: dict[int, tuple] = {}
        taken: set[int] = set()
        for _, i, c, link, ref_seq, ref_box in cand:
            if i in pick or id(c) in taken:
                continue
            pick[i] = (c, link, ref_seq, ref_box)
            taken.add(id(c))
        for i, b in enumerate(boxes):
            if i in pick:
                continue
            for c in ended:                     # 最近结束的在后面；先找 k-2（gap1）再找更早的（memory）
                if id(c) in taken or c.last_seq not in self.grays:
                    continue
                if iou(b, c.box) >= a.reuse_iou:
                    pick[i] = (c, ("gap1" if c.last_seq == seq - 2 else "memory"), c.last_seq, b)
                    taken.add(id(c))
                    break
        return pick, shift          # shift 只给 FLOWOCR_REUSE_DEBUG 那一行用（漏了它就是个只在排障时发作的 NameError）

    def _gate(self, chain, link, b, gray, ref_gray, ref_box,
              pre_corr: float | None = None) -> tuple[str, float]:
        """这个框能不能沿用：返回 (""=能沿用 / 拦下它的那道门的 stats 键, 算出来的相关系数)。

        **decide 和跨空档接链共用这一份。** 不碰 stats、不碰缓存预算——那两样只有 decide 该做。

        （`age_add` / `pixel` 两个参数是投机用的，`--rec-lookahead` 一删就没有调用方了，
        跟着删掉——留着死参数比留着死函数更难发现，2026-09-19 复审第 4 条。）
        """
        a = self.a
        if a.refresh_every and chain.age + 1 >= a.refresh_every:
            return "refresh", 0.0
        # 宽度变化量**和真读那一帧比**（`--reuse-dw-anchor`，默认开）：和上一个采样点比时，一点点长宽永远过门（见 Chain.read_w）
        ref_w = chain.read_w if (a.reuse_dw_anchor and chain.read_w is not None) else chain.box[2] - chain.box[0]
        if abs((b[2] - b[0]) - ref_w) > a.reuse_dw:
            return "gate_dw", 0.0
        if pre_corr is not None:
            c_val = pre_corr                   # det 那一层预算的（`--pregate`），同函数同输入，值按位相同
        else:
            c_val = (self.corr(self._crop(gray, b), self._crop(ref_gray, ref_box))
                     if ref_gray is not None else -1.0)
        # **文本待定 = 一律严格门**（策略 B）：这条链最近一次真读还没回来，`hasdigit(chain.text)` 问的是
        # **旧**文本的数字性，拿它选门就等于让判决依赖一个未知量。取严格的那一档，判决从此是确定的——
        # 代价是多读几次（实测 +2.6%~8.9% 的读），方向是"更新的文本"
        digit = hasdigit(chain.text) or chain.text_pending
        # **没被第二次真读证实的文本也走严门**（`--reuse-confirm`）：打字机中间态的句尾在长时，整框相关系数看不出来
        unconfirmed = a.reuse_confirm and not chain.confirmed
        thr = a.reuse_corr_digit if (digit or unconfirmed) else a.reuse_corr
        if link == "moved":
            thr = max(thr, a.reuse_moved_corr)
        elif link != "prev":
            thr = max(thr, a.reuse_link_corr)
        if c_val < thr:
            return ("gate_digit" if digit else "gate_unconfirmed" if unconfirmed else "gate_pix"), c_val
        return "", c_val

    def decide(self, seq: int, gray: np.ndarray, boxes: list[list[int]], pre: dict | None = None):
        """返回 (decided, todo, assign)：decided = [(i, text, conf, age)]，todo = [i]，
        assign[i] = (链, 链接类型) 给 commit 用（真读的框也可能挂在链上）。

        `pre`：`run_ocr2.pregate_of` 在 det 那一层预先算好的**静态相关系数**（框 -> 值，`--pregate`）。
        只在"参照就是上一个采样点的同一个框"时能用（`prev` / `assoc`），值按位相同——所以产物不变。"""
        a = self.a
        self.seq = seq
        self.grays[seq] = gray
        for k in [k for k in self.grays if k < seq - a.reuse_mem - 2]:
            del self.grays[k]
        self._release_reservations()
        decided, todo, assign = [], [], {}
        self.last_corr: dict[int, float] = {}       # 这一帧算过的相关系数（--record-corr 取它，见下面那处）
        live = self.live
        dbg = os.environ.get("FLOWOCR_REUSE_DEBUG")          # "x0,y0,x1,y1"：打出落在这个框上的决定（排障用，不进 config）
        dbg_box = [int(x) for x in dbg.split(",")] if dbg else None
        t_m = time.perf_counter()
        pick, shift = self._match(seq, boxes, live, self.ended)
        pick = {i: p for i, p in pick.items() if not p[0].severed}      # 切点切过的链不许续（`sever`）
        self.sec["match"] += time.perf_counter() - t_m
        for i, b in enumerate(boxes):
            chain, link, ref_seq, ref_box = pick.get(i, (None, None, None, None))
            if dbg_box is not None and iou(b, dbg_box) >= 0.5:
                print(f"[dbg] seq {seq} box {b} shift {shift} -> {link} chain.text={chain.text if chain else None!r} "
                      f"chain.box={chain.box if chain else None} age={chain.age if chain else None} "
                      f"live={[(c.text, c.box) for c in live if iou(b, c.box) >= 0.3 or iou([b[0]-shift[0], b[1]-shift[1], b[2]-shift[0], b[3]-shift[1]], c.box) >= 0.3]}", flush=True)
            if chain is None:
                if self.trace is not None:
                    self.trace.append({"seq": seq, "box": list(b), "chain": None, "link": None,
                                       "gate": "new", "corr": None, "digit": None, "read": True})
                todo.append(i)
                continue
            assign[i] = (chain, link)
            t_g = time.perf_counter()
            # 预算好的那一格只在"参照 = 上一个采样点的同一个框"时对得上（静止匹配，占沿用的 97%）
            pc = (pre.get(tuple(b)) if pre is not None and ref_seq == seq - 1 and ref_box == b else None)
            gate, c_val = self._gate(chain, link, b, gray, self.grays.get(ref_seq), ref_box, pre_corr=pc)
            self.sec["gate"] += time.perf_counter() - t_g
            # `--record-corr` 要的就是这个值。**以前它在 v2 路径上是静默空转的**（2026-09-18 发现）：
            # `corr_of` 只有老的 prev 路径会填，而 v2 早就是默认——旋钮给了、产物里一个 `corr` 都没有。
            # 只记"本来就算了的"，不多算一次、也不改判决。
            self.last_corr[i] = c_val
            tr_row = None
            if self.trace is not None:
                tr_row = {"seq": seq, "box": list(b), "chain": chain.uid, "link": link,
                          "gate": gate or "", "corr": round(c_val, 4),
                          "digit": hasdigit(chain.text), "read": bool(gate)}
                self.trace.append(tr_row)
            if gate:
                self.stats[gate] += 1
                todo.append(i)
                continue
            # 缓存预算：超了就当场真读（cache_budget.py 量的就是这个语义：只多几次 rec，不换质量）。
            # **预留和放行必须是一个原子动作**（2026-09-17 审计的最小复现）：第一版每个框各自看同一个余额、
            # 又都不预留，只剩 400 B 时同一帧两个各 300 B 的框**双双获准沿用**，commit 里第二个的裁剪放不下
            # 就被丢掉、行却已经是 reused——下一次发现变化时它回补不了，错文本留在产物里，
            # 而 `cache_forced` 记的是"改成真读"，两种语义混在一个计数器上。现在预留按 `crop_bytes` 的上界算。
            est = self.crop_bytes(gray.shape, b)
            if self.cache + est > self.cache_cap:
                self.stats["cache_forced"] += 1
                if self.trace is not None:
                    self.trace[-1].update(gate="cache_forced", read=True)
                todo.append(i)
                continue
            self.cache += est
            self.reserved[i] = est
            self.stats["cache_peak"] = max(self.stats["cache_peak"], self.cache)
            decided.append((i, chain.text, chain.conf, chain.age + 1))
        return decided, todo, assign

    def sever(self, area) -> int:
        """自选范围的时间切点（`regions.cuts_us`）：框和 `area`（H×W bool，那一刻遮罩变了的像素）有交的链，
        活链和记着的已结束链都**不许再被接上**——下一帧同一位置按新链真读，旧链在 commit 里照常结束（结束事件照发）。
        owner 定的"时间关闭段不可续链"在提取层的落点（ocr-regions 计划；Codex 审计 P1-2）。返回切到的链数。"""
        n = 0
        for c in (*self.live, *self.ended):
            x0, y0, x1, y1 = (int(v) for v in c.box)
            if not c.severed and area[max(0, y0):max(y0 + 1, y1), max(0, x0):max(x0 + 1, x1)].any():
                c.severed = True
                n += 1
        return n

    def _release_reservations(self) -> None:
        """没走到 commit 的预留（正常路径上是空的）还给预算。"""
        for i in list(self.reserved):
            self.cache -= self.reserved.pop(i)

    # ---- 每帧第二步：建行、更新链、落盘。**拆成两半**（2026-09-19，inference-runtime 计划组批那节的 B）----
    # stage1 = 和"读回来的文本"无关的那一半（建行骨架、更新 box/age、占缓存、算哪些链结束）；
    # stage2 = 要文本的那一半（回填行、回补、给 edge 事件定 changed/same、推落盘水位）。
    # **判据只有一份**：`commit` 就是 stage1 + 马上 stage2，窗口模式只是把 stage2 推迟到窗口结算。
    def commit(self, seq: int, idx: int, t_us: int, boxes, polys, decided, read_set: set[int],
               assign: dict, rec_crop, corr_of: dict, record_corr: bool, fh) -> int:
        """decided 含沿用的和真读的（真读的 age=0）。read_set = 真送进 rec 且拿到结果的框。返回回补用掉的 rec 次数。"""
        st = self.commit_stage1(seq, idx, t_us, boxes, polys, decided, read_set, assign,
                                rec_crop, corr_of, record_corr)
        texts = {i: (text, score) for i, text, score, _age in decided if i in read_set}
        return self.commit_stage2(st, texts, fh)

    def commit_stage1(self, seq: int, idx: int, t_us: int, boxes, polys, decided,
                      read_set: set[int], assign: dict, rec_crop, corr_of: dict,
                      record_corr: bool) -> dict:
        """不需要"读回来的文本"的那一半。返回一个 state，交给 `commit_stage2`。

        真读的框在这里只建**行骨架**（`text` 先空着）、把链的 `box`/`age`/`last_seq` 推到这一帧，
        并把链标成 `text_pending`——窗口模式下接下来几帧的像素门会因此走**严格门**
        （判决从此不依赖未知文本，inference-runtime 计划的策略 B）。
        """
        a = self.a
        rows: list[dict] = []
        new_live: list[Chain] = []
        touched: set[int] = set()
        events: list[list] = []                # (链 uid, 行, 框, 文本, kind)；真读那几条 kind 先留空，stage2 填
        deferred: list[dict] = []              # 真读的框：等文本回来才做得了的那一半
        inherit: list[tuple] = []              # 沿用的框：(行, 链, 事件)——文本由 stage2 从链上取
        t_r = time.perf_counter()
        for i, text, score, age in sorted(decided):
            b = boxes[i]
            reused = i not in read_set
            chain, link = assign.get(i, (None, None))
            # ⚠ **写 obs 的第二处**（主路在 run_ocr2）。`frame` 认 pts，判据只有 framesource 那一份——
            # 2026-09-20 第一版只改了主路，缺口之后一半的行还是旧编号，而没有任何一层报错。
            row = {"frame": framesource.pts_frame(t_us, idx, self.src_fps), "t_us": t_us, "box": b,
                   "poly": [[round(float(p[0])), round(float(p[1]))] for p in polys[i]],
                   "text": text, "conf": round(score, 4)}
            if reused:
                row["reused"] = True
                if link != "prev":
                    row["link"] = link
            if record_corr and i in corr_of:
                row["corr"] = round(corr_of[i], 4)
            rows.append(row)
            if reused:
                # 跨空档接回旧链（gap1 / memory）：框消失过又出现，对回抠是一次**新出现**（build_tracks 在这里起新 run）
                ev = [chain.uid, row, b, text, "new" if link in ("gap1", "memory") else "same"]
                events.append(ev)
                inherit.append((row, chain, ev))
                chain.last_row, chain.last_idx = row, idx
                # 缓存裁剪给回补用。预算已经在 decide 里**预留**过（`crop_bytes` 是上界），所以这里必然放得下；
                # `cache_overrun` 是"上界算错了"的报警，守卫钉着它必须是 0——绝不能再走回"放不下就不缓存、
                # 行却仍是 reused"那条路（那等于承诺了可回补又偷偷收回）
                t_p = time.perf_counter()
                crop = rec_crop(i)
                nb = int(crop.nbytes)
                res = self.reserved.pop(i, 0)
                self.cache -= res
                if self.cache + nb <= self.cache_cap:
                    chain.pending.append([seq, row, crop.copy()])   # 切片持有整帧，必须拷
                    chain.cache_bytes += nb
                    self.cache += nb
                    self.sec["pending"] += time.perf_counter() - t_p
                    self.stats["cache_peak"] = max(self.stats["cache_peak"], self.cache)
                else:
                    self.stats["cache_overrun"] += 1
                chain.age = age
                chain.box, chain.last_seq = b, seq
                self.stats[f"reuse_{link}"] += 1
                if id(chain) not in touched:
                    new_live.append(chain)
                    touched.add(id(chain))
                continue
            # 真读：文本还不知道。这里只把**和文本无关的**那些推进去
            ev = [None, row, b, None, None]
            events.append(ev)
            deferred.append({"i": i, "row": row, "box": b, "chain": chain, "link": link, "ev": ev})
            if chain is not None and link in ("prev", "moved"):
                chain.read_w = b[2] - b[0]
                chain.box, chain.last_seq, chain.age = b, seq, 0
                chain.last_row, chain.last_idx = row, idx
                chain.inflight += 1            # 策略 B：文本没回来之前，这条链的像素门走严格门
                if id(chain) not in touched:
                    new_live.append(chain)
                    touched.add(id(chain))
            else:
                # 没链、或跨空档链接被门拦下 / 到期：都当新链，不回补旧链
                nc = Chain(b, "", 0.0, seq)
                nc.read_w = b[2] - b[0]
                nc.last_row, nc.last_idx = row, idx
                nc.inflight = 1
                new_live.append(nc)
                deferred[-1]["new_chain"] = nc
        # 本帧没接上的活链 -> 结束（缓存丢掉）；结束太久的丢掉
        alive = {id(c) for c in new_live}
        ended_now: list[tuple] = []
        for c in self.live:
            if id(c) not in alive:
                self._drop_pending(c)
                c.ended_at = seq
                self.ended.append(c)
                if c.last_row is not None:
                    # 文本**不在这里抄**：异步窗口下这条链最后几次真读可能还在途，此刻的 c.text 是旧的；
                    # stage2（按帧序、那些读都已结算）再取——见 commit_stage2 里交给回抠的那一处
                    ended_now.append((c.uid, c.last_row, c.box, c, c.last_idx))
        self.ended = [c for c in self.ended if id(c) not in alive and seq - c.last_seq <= a.reuse_mem + 1]
        self.live = new_live
        self.buf[seq] = rows
        self.sec["rows"] += time.perf_counter() - t_r
        return {"seq": seq, "idx": idx, "t_us": t_us, "rows": rows, "events": events,
                "ended_now": ended_now, "deferred": deferred, "inherit": inherit}

    def commit_stage2(self, st: dict, texts: dict, fh) -> int:
        """要"读回来的文本"的那一半：回填行、回补、给 edge 事件定 kind、推落盘水位。返回回补用掉的 rec 次数。

        `texts`：框下标 -> (文本, 分数)。**窗口模式下这一步推迟到窗口结算**，于是 stage1 那会儿
        链的首读可能还没回来（`text_pending`）、沿用行里写的是空文本——所以**沿用行的文本在这里才定**，
        从链上取。stage2 严格按帧序跑，而沿用意味着这条链上一次真读在更早的帧，那一帧的 stage2 已经跑过，
        因此此刻链上的文本必然是最终的。（W=0 时这一步是恒等变换：链的文本就是 stage1 写进行里的那个。）
        """
        seq = st["seq"]
        extra_rec = 0
        stale: set[int] = set()                # 这一批里历史文本被回补改过的链 uid（见下面 _recover 那一处）
        t_r = time.perf_counter()
        for row, chain, ev in st["inherit"]:
            row["text"], row["conf"], ev[3] = chain.text, round(chain.conf, 4), chain.text
        # **两遍**（2026-09-26）：先收齐这一帧所有要回补的链，一起按层二分（`_recover`），再逐链收尾。
        # 链之间互不依赖（回补只改这条链自己的历史行，收尾只动这条链），所以和逐链做完全等价，
        # 差别只在同一层的读并成一次发——原来每条链每层各发一次、几乎全是批 1（wuwa-s2 320 次调用读 323 个框）
        todo: list[tuple] = []                 # (d, 文本, 分数, 回补任务下标或 None)
        jobs: list[tuple] = []                 # (chain, 新文本, 新分数, 这一帧之前的沿用行)
        for d in st["deferred"]:
            text, score = texts.get(d["i"], ("", 0.0))
            row, chain, ev = d["row"], d["chain"], d["ev"]
            row["text"], row["conf"] = text, round(score, 4)
            nc = d.get("new_chain")
            if nc is not None:
                nc.text, nc.conf = text, score
                nc.inflight -= 1
                ev[0], ev[3], ev[4] = nc.uid, text, "new"
                continue
            # 回补只看**这一帧之前**的沿用行；更晚的那些是"读还没回来时按旧文本写出去的"，
            # 由下面的前向回填统一改（窗口模式才会有）。⚠ **别手工切 `chain.pending`**：
            # 缓存的账在 `_drop_pending` 里，切了不退就是虚高（见 `_recover` 的说明）
            before = [pnd for pnd in chain.pending if pnd[0] < seq]
            j = None
            if before and not same_text(text, chain.text):
                j = len(jobs)
                jobs.append((chain, text, score, before))
            todo.append((d, text, score, j))
        moved = []
        if jobs:
            t_rc = time.perf_counter()
            moved = self._recover(jobs)
            self.sec["recover"] += time.perf_counter() - t_rc
        for d, text, score, j in todo:
            chain, ev = d["chain"], d["ev"]
            if j is not None:
                moved_back = moved[j]
                if self.trace is not None:
                    self.trace.append({"seq": seq, "box": list(d["box"]), "chain": chain.uid,
                                       "link": "recover", "gate": "recover", "corr": None,
                                       "digit": None, "read": True, "n_reads": moved_back[0]})
                extra_rec += moved_back[0]
                # **回补改的是过去，链事件只能往前发**（2026-09-17 审计）：文本从 `AAA / AAA / BBB` 被改成
                # `AAA / BBB / BBB` 之后，变化点其实在上一个采样点，而在线回抠这一批才收到 "changed"——
                # 它开的打字机窗晚一个采样点，旧句的结尾窗也挂在一行已经被改写的文本上。
                # 帧环只留 5 个采样间隔，回补能往回摸 --refresh-every 个采样点，重新量是不可能的；
                # 所以**把这一批受影响的证据标成 stale**，第二遍当没量到、该 run 退回采样级（口径同 short）。
                # 彻底修法（事件重放 / 按链保留像素）写在 reuse-budget 计划第 2 条。
                if moved_back[1]:
                    stale.add(chain.uid)
                    self.stats["edge_stale"] += 1
            self._drop_pending(chain, upto=seq)
            ev[0], ev[3], ev[4] = chain.uid, text, "changed" if text != chain.text else "same"
            # 两次真读逐字相同才算证实；变了（打字机在长 / 换句 / 回补改了历史）就回到未证实，下一帧起重新走严门
            chain.confirmed = text == chain.text
            chain.text, chain.conf = text, score
            chain.inflight -= 1
        self.sec["rows"] += time.perf_counter() - t_r
        t_e = time.perf_counter()
        if self.edge is not None:
            # 链断的文本在这里才取（2026-09-23）：原来 stage1 抄的是在途读之前的旧文本，回抠认不出"长字续接"，
            # 段被拆开、全字偏早（gi-s2 全字代价 22 -> 75、relinked 184 -> 51）。同步时两者相同
            ended = [(u, row, bx, ch.text, li) for u, row, bx, ch, li in st["ended_now"]]
            self.edge.sample(seq, st["idx"], st["t_us"], [tuple(e) for e in st["events"]],
                             ended, stale)
        self.sec["edge"] += time.perf_counter() - t_e
        t_f = time.perf_counter()
        self._flush(fh, seq - self.horizon)
        self.sec["flush"] += time.perf_counter() - t_f
        return extra_rec

    def _recover(self, jobs: list[tuple]) -> list[tuple[int, bool]]:
        """递归二分：每个任务 `(chain, 新文本, 新分数, pend)` 里 pend[j] 的文本未知，端点是 held（chain.text）和新读。
        逐任务返回 (用掉的 rec 次数, 有没有把历史行的文本改掉)——改过就说明变化点比这一帧早，调用方要作废那一段的边界证据。

        `pend` 是**这一帧之前**的沿用行（窗口模式下更晚的那些是"读还没回来时按旧文本写出去的"，由调用方前向回填）。
        **缓存的回收不在这里做**——统一交给 `_drop_pending`，不然会出现"条目丢了、`cache_bytes` 没退"的虚高
        （2026-09-19 实测：缓存虚高 -> `cache_forced` 多触发 -> 判决变了，14 行产物不同）。"""
        # 每个任务自己的端点表和栈；同一层里所有任务的中点**一次**发出去（`rec_many`）
        texts = [{-1: (ch.text, ch.conf), len(pend): (t, c)} for ch, t, c, pend in jobs]
        stacks = [[(-1, len(pend))] for _, _, _, pend in jobs]
        reads = [0] * len(jobs)
        changed = [False] * len(jobs)
        # **按层走**（2026-09-19，首轮审计第 2 条）：同一层的几个中点互不依赖（每个区间只看自己的端点），
        # 所以可以一次性发出去；2026-09-26 起同一帧里各条链的同一层也并在一起发。`rec_many` 没给就退化成逐个读——
        # 两条路的**读和结果完全一样**，差别只在"发几次"
        while True:
            layer = []                         # (任务, lo, mid, hi)
            for k, stack in enumerate(stacks):
                tx = texts[k]
                for lo, hi in stack:
                    if hi - lo <= 1 or same_text(tx[lo][0], tx[hi][0]):
                        continue
                    layer.append((k, lo, (lo + hi) // 2, hi))
                stacks[k] = []
            if not layer:
                break
            crops = [jobs[k][3][m][2] for k, _, m, _ in layer]
            got = (self.rec_many(crops) if self.rec_many is not None
                   else [self.rec_one(c) for c in crops])
            for (k, lo, mid, hi), (t, c) in zip(layer, got):
                pend = jobs[k][3]
                reads[k] += 1
                texts[k][mid] = (t, c)
                row = pend[mid][1]
                changed[k] = changed[k] or not same_text(t, row["text"])
                row["text"], row["conf"] = t, round(c, 4)
                row.pop("reused", None)
                row.pop("link", None)
                row["recovered"] = True
                # 中点这一行是**真读过的**：`same_text` 判"没改历史"（标点变体）时也换成这次的读数——它是一次独立观测，
                # 留旧写法反而是拿沿用的文本冒充真读（2026-09-26 查过，不改）。中点到 hi 之间推断的那几行改成"相同就不覆盖"
                # 也不会改变任何输出：`same_text` 可传递，一行最后没被读到，说明它所在区间两端的读数"相同"、父区间却不同，
                # 所以最后一次覆盖它的读数一定和它当时的文本"不同"，两种写法留下的值一样
                for j in range(mid + 1, hi):
                    changed[k] = changed[k] or not same_text(t, pend[j][1]["text"])
                    pend[j][1]["text"], pend[j][1]["conf"] = t, round(c, 4)
                stacks[k] += [(lo, mid), (mid, hi)]
        self.stats["recover_reads"] += sum(reads)
        self.stats["recover_events"] += len(jobs)
        return list(zip(reads, changed))

    def _drop_pending(self, chain: Chain, upto: int | None = None) -> None:
        """丢掉这条链缓存着的裁剪（回补用的）。`upto` 给了就只丢 `seq <= upto` 的那些——
        窗口模式下，读还没回来的那几帧里这条链**又攒了新的沿用行**，那些不能一起丢
        （它们还要回填文本、还可能被下一次回补用到）。"""
        if upto is None:
            self.cache -= chain.cache_bytes
            chain.pending, chain.cache_bytes = [], 0
            return
        keep, freed = [], 0
        for pnd in chain.pending:
            if pnd[0] <= upto:
                freed += int(pnd[2].nbytes)
            else:
                keep.append(pnd)
        chain.pending = keep
        chain.cache_bytes -= freed
        self.cache -= freed

    def _flush(self, fh, upto: int) -> None:
        if self.edge is not None and any(k <= upto for k in self.buf):
            # 写之前等 edge 证据挂上：不是等那一批本身，是等它开的段都停变了（edge_refine.settled_by）；
            # 主线程因此按辅助流的节奏背压，辅助流慢于 OCR 时墙钟跟着慢，但证据不丢
            self.edge.wait_batch(min(self.edge.settled_by(upto), self.seq))
        for s in sorted(k for k in self.buf if k <= upto):
            for row in self.buf.pop(s):
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def flush_all(self, fh) -> None:
        self._flush(fh, 10 ** 12)
