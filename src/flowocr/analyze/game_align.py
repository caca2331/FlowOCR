"""拿 `flowocr.analyze.gamescript` 圈出来的剧本量一条轨：命中 / 三档漏条 / 重复认领 / 行首行尾缺字。

分桶与三档复用 `script_align.gap_report` 和 `evalkit.triviality`，**不重写第二份**。
和《魔裁》那套差三处，都是游戏直播的形态逼出来的：

1. **一条 cue 有几种读法**。原神 / 星铁的主轨 cue 是 `名牌 \\n 称号 \\n 正文…`
   粘在一起的（gamestream-first-look 报告），剧本行只有正文；
   末尾还常挂一个被吸进来的键位提示（`D`）。所以每条 cue 试"去掉开头 0–3 行 ×
   去不去掉最后一行"几种读法，取和剧本最像的那个。不靠名字表——库里原神的说话人
   只覆盖 4% 的节点，而且屏幕上的名牌和库里的 `speaker` 字段不是一回事。
2. **对齐按时间圈候选，不走游标**（`run` 的说明）。剧本行带 gamescript 从 obs 锚来的时刻，
   各臂共用；时间只圈范围，命中仍看文本。主认领之后还有**第二遍**（`claim_contained`）：
   一条 cue 常常装着两行剧本（选项按钮 + 正文、并带把两个说话人并成一条），整行落在 cue 里
   就算认领，但**不许和主认领占同一段字**——否则库里近似重复的另一行会被白送一个命中。
3. **分母里排掉 `gamescript.NON_BAND_KINDS`**（选项 / 黑屏 / 没走的分支 / 单元外 / 重演拷贝 /
   读到过但不在字幕带，判据见那边）。排掉的条目仍参与对齐、只是不计分母；它们若被主轨命中会单独报出来——
   那说明分支判错了，或者选项文本真的进了这条轨。重演拷贝例外：它连序列都不进。
   绝区零的变体组一组只算一个分母条目，任一种说法被认领都记在代表行上（`score`）。
4. **长 gap 照样算疑似真漏**（`gap_report(long_is_branch=False)`，audit-6）：《魔裁》那边
   长 gap 被解释成"分支没走到"，而这里分支和没播的一截已经排出分母，长 gap 没有分支解释。
   于是疑似真漏 = 全部未命中 = 分母 − 命中，逐档不经过 gap 分桶，分母各臂相同，三档可以跨臂比。

**这里量的是原始轨，不过匹配器。** 所以"对不上剧本"那一档是**幽灵条目的上限**，
而且比《魔裁》那边更松：库收什么因游戏而异（原神只有任务对话；星铁含全量台词；
绝区零含散条目），库外的文字——UI、路人、主播自己压的字幕——都落在这一档。
**重复认领**这里是原始轨口径：一条剧本行被几条 cue 认领，多出来的次数之和
（hit_delta 那个是匹配器输出口径，两者不可混比）。

用法：见 `CLI_DESCRIPTION`。
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from flowocr.artifacts import evalkit  # noqa: E402
from flowocr.analyze import gamescript as GS  # noqa: E402
from flowocr.analyze import script_align as SA  # noqa: E402
from flowocr.artifacts import srtio  # noqa: E402

CLI_DESCRIPTION = """拿 gamescript 圈出来的剧本量一条轨（用途 2 的尺）：命中 / 三档疑似真漏 / 重复认领 / 行首行尾缺字。

分母排掉选项、黑屏旁白、没走到的分支、没播的那截、重演拷贝和不在字幕带的行；量的是原始轨、不过匹配器，
所以"对不上剧本"那一档是幽灵条目的上限（UI、路人、主播自己的字幕都落在这里）。

    python -m flowocr.analyze.game_align out/gametext/gi-s2-ref.json --subs out/gs-gi-s2/gi-s2-main.srt
    # 两条臂比命中集合（按归一化文本的多重集）：
    python -m flowocr.analyze.game_align <ref.json> --subs <A 主轨> --vs <B 主轨>"""


@dataclass
class Ref:
    doc: dict
    script: list[SA.Entry]
    """对齐序列（gamescript 排好的 `sequence`：按 obs 时间排到行一级，重演拷贝不在里面）。"""
    windows: list[list]
    """每行可以被认领的时段——理由见 gamescript.sequence。"""
    role: dict[str, str]
    variant_of: dict[str, str]
    """绝区零变体行 -> 它那组的代表行（gamescript `fold_variants`）。一组只算一个分母条目。"""


def load_ref(p: Path) -> Ref:
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("schema") != GS.SCHEMA:
        raise SystemExit(f"{p} 的 schema 不是 {GS.SCHEMA}：{doc.get('schema')!r}（重跑 gamescript）")
    by_key, role, variant_of = {}, {}, {}
    for u in doc["units"]:
        for ln in u["lines"]:
            if ln["kind"] == "Duplicate":     # 同一个节点的第二份 key 相同，别覆盖原件
                continue
            by_key[ln["key"]] = SA.Entry(key=ln["key"], kind=ln["kind"], cmd="", text=ln["text"],
                                         src=u["uid"])
            role[ln["key"]] = ln["role"]
            if ln.get("variant_of"):
                variant_of[ln["key"]] = ln["variant_of"]
    return Ref(doc=doc, script=[by_key[k] for k, _, _ in doc["sequence"]],
               windows=[w for _, _, w in doc["sequence"]], role=role, variant_of=variant_of)


OBS_READ, OBS_COMMON, OBS_NONE, OBS_SHORT = "obs 读到过", "只撞常用句", "obs 没读到", "太短判不了"
OBS_SIDES = (OBS_READ, OBS_COMMON, OBS_NONE, OBS_SHORT)


def obs_side(doc: dict) -> dict[str, str]:
    """每行剧本在 obs 里读到过没有（gamescript 记的锚点，任何区域、任何一帧）。

    这是**这一处**的判据，不是全局搜：锚点是 obs 里某个框的文字落在**这一行**里
    （同一单元内只锚最像的那行），而单元本身是按时间圈出来的。内容不到
    `qmin` 字的行从来不拿去检索，没有锚点不说明什么，单列。
    * 读到过而主轨没命中 = 丢在**轨这一层**（没进主轨 / cue 读法对不上）；
    * 只撞常用句 = 锚住它的查询**全都**撞了 >`max_units` 个单元（`言ってみるといい。` 这种到处都有的
      句子，别处的同句也会锚上来）——算不得"这一处读到过"，单列（audit-6）；
    * 没读到 = 丢在 **OCR 之前**（没出框、被遮挡、显示太短没采到……分不开，要摆帧）。"""
    qmin = doc["provenance"]["params"]["qmin"]
    out = {}
    for u in doc["units"]:
        for ln in u["lines"]:
            if ln["kind"] == "Duplicate":     # 同 load_ref：同一节点的第二份别覆盖原件
                continue
            a = ln["anchor"]
            out[ln["key"]] = (OBS_READ if a and a["unique"] else OBS_COMMON if a else
                              OBS_NONE if len(GS.cnorm(ln["text"])) >= qmin else OBS_SHORT)
    return out


def resolve(p: str) -> tuple[Path, dict]:
    """轨可以直接给 SRT，也可以给 `*-tracks.json`——后者从 provenance 取 `main_srt`
    （**不许 glob 猜**，字典序事故见 docs/dev-guide/verification.md「追溯」），并带回产它的 `git_head` / `code_fp`。"""
    path = Path(p)
    if not path.name.endswith("-tracks.json"):
        return path, {}
    from flowocr.artifacts import tracksio
    prov = tracksio.load(path)["provenance"]
    if not prov.get("main_srt"):
        raise SystemExit(f"{path} 没有主轨（provenance.main_srt 为空）")
    return path.parent / prov["main_srt"], {k: prov.get(k) for k in ("git_head", "code_fp")}


def code_same(a: dict, b: dict) -> bool | None:
    """两臂的轨是不是同一份 build_tracks 产的：比**代码指纹**不比 HEAD（audit-6——HEAD 移动、
    代码没变时 git_head 会不同；反过来 `+dirty` 相同也证明不了代码相同）。没指纹的算不知道。"""
    fa, fb = (a or {}).get("code_fp"), (b or {}).get("code_fp")
    return None if not (fa and fb) else fa == fb


def read_cues(p: Path) -> list[tuple[float, str, str]]:
    """(起点秒, 各行用换行拼起来的原文, 文件名)。**保留行结构**，读法要按行切。"""
    return [(c.start, "\n".join(c.lines), p.name) for c in srtio.read_subs([p])]


HEAD_MIN_BODIES = 3
"""一行在整条轨里作为**非末行**、配过至少这么多种不同的末行，就是"开头行"（`head_lines`）。"""


def head_lines(subs) -> frozenset[str]:
    """整条轨里的**开头行**（归一化后）：名牌、称号、常驻在正文上面的角标。

    判据只看这条轨自己：一行作为非末行出现、而且配过 ≥ `HEAD_MIN_BODIES` 种不同的末行（正文）。
    名牌天然如此——一个说话人要说很多句（gi1 `カチーナ` 配过 271 种正文，zzz `シーシイア` 217 种）；
    真台词作为非末行出现时，下面跟的几乎总是同一句的续行。不靠名字表（库里原神的说话人只覆盖 4% 的节点）。

    为什么需要（2026-09-12，matcher 计划那 12 条"只由 `extra` 认领"的行逐条看出来的）：
    ①`カチーナ / はあ、はあ` 这种**正文很短**的 cue，去掉末行的那种读法只剩名牌，
    和剧本里恰好存在的喊名字的台词 `カチーナ！` 相似度 0.89，比短正文本身还高，于是主认领被名牌抢走、
    输出成"卡齐娜！"——认错，不只是丢；②第二遍包含认领把名牌 `シーシイア` 整行认成 `シーシィア…？`。"""
    from collections import defaultdict
    bodies: dict[str, set[str]] = defaultdict(set)
    span: dict[str, list[float]] = {}
    for t, raw, _ in subs:
        ls = [x for x in raw.split("\n") if x.strip()]
        for x in ls[:-1]:
            k = SA.norm(x)
            if k and len(k) <= HEAD_MAX_CHARS:
                bodies[k].add(SA.norm(ls[-1]))
                lo, hi = span.get(k, (t, t))
                span[k] = [min(lo, t), max(hi, t)]
    return frozenset(k for k, v in bodies.items()
                     if span[k][1] - span[k][0] >= HEAD_MIN_SPAN
                     and distinct_bodies(v, HEAD_MIN_BODIES) >= HEAD_MIN_BODIES)


HEAD_MAX_CHARS = 16
"""开头行最多这么多字（归一化后）。名牌、称号都短（最长见过 `「プラックウルフ」ロームル` 12 字）；
两行正文的第一行几乎总比这长——长度门是 `distinct_bodies` 之外的第二道保险。"""

HEAD_MIN_SPAN = 30.0
"""开头行在轨里首末两次出现至少隔这么多秒。**第三道保险**：名牌跟着说话人出现在一场又一场对话里；
正文的第一行只在它自己那几秒里出现。zzz-s2 `待った待った！刀を納めろ！ / ちゃんと言うから！` 这条
（11 字、第二行被打字机和 OCR 抖出几种互不相似的末行）前两道都拦不住，被当成开头行剥掉、命中 −1。"""


def distinct_bodies(bodies: set[str], enough: int) -> int:
    """数"真正不同"的末行有几种：**互为前缀、或相似度 ≥ 0.6 的算同一种**，数到 `enough` 就停。

    不这么数的话，两行正文的**第一行**会被当成开头行：打字机把第二行打成 `一緒に` / `一緒にテレ` /
    `一緒にテレビでも…` 好几种半截、OCR 再抖几个字，它就"配过 ≥3 种末行"了——剥掉之后整条 cue 对不上
    （2026-09-12 第一版剥开头行时实测：gi-s1 命中 30 → 23、zzz-s1 46 → 38，逐条看都是这个形状）。

    代表是贪心挑的，结果依赖遍历顺序，所以**等长的按字符串排**：只按长度排时等长的几条按 set 的迭代顺序走，
    而 str 的哈希每个进程随机加盐——同一份轨两次跑出的开头行集合不同（2026-09-26 gi-s1 名牌层 127 / 132 / 133 块）。"""
    reps: list[str] = []
    for b in sorted(bodies, key=lambda s: (-len(s), s)):
        if any(r.startswith(b) or SequenceMatcher(None, b, r, autojunk=False).ratio() >= 0.6 for r in reps):
            continue
        reps.append(b)
        if len(reps) >= enough:
            break
    return len(reps)


INLINE_NAME = re.compile(r"^\s*([^:：]{1,16}?)\s*[:：]\s*(\S.*)$")
"""过场字幕的"名字: 台词"同一行格式（gi1 `カチーナ: ハッ!`、`パイモン: 気持ちいいな。…`）。"""


def strip_heads(raw: str, heads: frozenset[str]) -> tuple[list[str], bool]:
    """剥掉 cue 里的开头行，返回 (剩下的行, 剥掉过没有)。

    * 非末行里的开头行整行剥掉；末行是正文，不剥（`パイモン / カチーナ！` 的末行碰巧和名牌同字）；
    * **只剩一行、而它就是开头行**时整条不留：打字机起步那半秒只有名牌在屏上；
    * 任一行是 `开头行: 台词` 的同一行格式，剥掉前缀。"""
    ls = [x for x in raw.split("\n") if x.strip()]
    if not heads or not ls:
        return ls, False
    out, hit = [], False
    for k, x in enumerate(ls):
        m = INLINE_NAME.match(x)
        if m and SA.norm(m.group(1)) in heads:
            x, hit = m.group(2), True
        elif SA.norm(x) in heads and (k < len(ls) - 1 or len(ls) == 1):
            hit = True
            continue
        out.append(x)
    return out, hit


def variants(raw: str, heads: frozenset[str] = frozenset()) -> list[str]:
    """去掉开头 0–3 行（名牌 / 称号 / 偶尔吸进来的一个角标）× 去不去掉最后一行（键位提示）。

    **开头行（`head_lines`）先剥掉，不进任何读法**（`strip_heads`）：留着它，名牌就会和剧本里喊名字的台词
    配上——去掉末行只剩名牌（gi1 `カチーナ / はあ、はあ` → `カチーナ！`），或者名牌拼上打字机打出的半截
    （gi2 `ナヴィア / (…すご` → `ナヴィア…`，0.82）。两例都是 2026-09-12 摆帧抓到的。
    剥过名牌、末行又是有内容的正文（≥3 字）时，去掉末行后只剩 1–2 字碎片的读法也不要
    （gi1 `カチーナ / L / はあ、はあ` 里夹的 OCR 垃圾行 `L`）；没有名牌的 cue 不受这条影响。"""
    ls, stripped = strip_heads(raw, heads)
    content = evalkit.CLASSES[2]
    body_last = stripped and bool(ls) and evalkit.triviality(ls[-1]) == content
    out = []
    for i in range(min(4, len(ls))):
        for j in {len(ls), len(ls) - 1}:
            if j <= i or (j < len(ls) and body_last
                          and all(evalkit.triviality(x) != content for x in ls[i:j])):
                continue
            v = SA.norm(" ".join(ls[i:j]))
            if v and v not in out:
                out.append(v)
    return out


def band(e: SA.Entry) -> bool:
    return e.kind not in GS.NON_BAND_KINDS


ROLE_ROWS = 16
"""『按上游角色拆』那张表最多列几行（进分母的类总是全列）。"""

BIN = 10.0
"""按时段找候选时的分桶宽度（秒）。只影响速度，不影响结果。"""

CONTAIN_MIN, CONTAIN_MIN_CHARS = 0.85, 3
"""第二遍认领（`claim_contained`）：剧本行有这么多内容字**整行**落在 cue 剩下的字里才算被认领，
且这一行的内容不少于这么多字（1–2 字没有判别力，同 owner 的三档）。包含度的门和
`gamescript` 检索那道一样。"""


def rest_of(v: str, used: str) -> str:
    """cue 的一种读法 `v` 里，挖掉和 `used`（主认领那一行）对上的字之后剩下的。"""
    if not used:
        return v
    keep = [True] * len(v)
    for b in SequenceMatcher(None, v, used, autojunk=False).get_matching_blocks():
        for k in range(b.a, b.a + b.size):
            keep[k] = False
    return "".join(c for c, k in zip(v, keep) if k)


def claim_contained(script: list[SA.Entry], subs, cand, sub_hit: list[int],
                    heads: frozenset[str] = frozenset()) -> list[list[int]]:
    """第二遍：一条 cue 认领**整行落在它里面**的其他剧本行；返回每条 cue 多认领了哪些行。

    为什么需要（2026-09-11 数的）：主认领是一对一的，而屏幕上一条 cue 常常装着两行剧本——
    绝区零的选项按钮和正文同屏（`また後で来るね` + 对白）、星铁并带把两个说话人并进一条 cue；
    还有一类是 cue 里混进了 OCR 垃圾行（`ユーちゃん / キュン / 1`），几种读法都够不着模糊阈值，
    但那一行的字**整行都在**。这两种主轨都确实读到了，算漏是错的。

    **不许和主认领占同一段字**（`rest_of`）：不加这条，库里近似重复的另一行会被白送一个命中——
    绝区零两位主角的两种说法、原神的重演拷贝、星铁改过字的同一句，都只上屏了一种
    （实测：不挖掉主认领的字是 +57/+14/+9/+10，挖掉之后 +15/+7/+4/+8，差的那些逐条看全是这类）。
    **认领了一行就把它的字也挖掉**：同一条 cue 里认两行时，第二行不许再用第一行的字——
    否则库里同文本、不同编号的两行会被同一段字各认一次（zzz `いやああぁぁ————！`，2026-09-12）。
    **开头行（名牌）先剥掉再找**（`head_lines`）：名牌整行落在 cue 里，不等于有人喊了这个名字。"""
    out: list[list[int]] = [[] for _ in subs]
    cn = [GS.cnorm(e.text) for e in script]
    for si, (t, raw, _) in enumerate(subs):
        vs = [GS.cnorm(v) for v in variants(raw, heads)]
        if not vs:
            continue
        used = cn[sub_hit[si]] if sub_hit[si] >= 0 else ""
        rests = [rest_of(v, used) for v in vs]
        for i in cand(t):
            if script[i].matched >= 0 or len(cn[i]) < CONTAIN_MIN_CHARS:
                continue
            if max(GS.contained(cn[i], r) for r in rests) >= CONTAIN_MIN:
                script[i].matched = si
                out[si].append(i)
                rests = [rest_of(r, cn[i]) for r in rests]
    return out


def run(script: list[SA.Entry], windows: list[list], subs, fuzzy: float) -> dict:
    """**按时间圈候选**的对齐：cue 在 t 秒，只和"t 落在它可认领时段里"的剧本行比
    （时段由 gamescript 从 obs 定，见 `gamescript.sequence`）；精确相等优先，否则取相似度
    最高的（≥ fuzzy）；平手时先给还没被认领的行、再按序列先后。
    返回和 `script_align.align` 同形的统计，并就地填 `Entry.matched`（第一条认领它的 cue）。

    为什么不用 `script_align.align` 的游标：那套在一条字幕带 + 有序剧本上是对的，
    在这里会被短句带跑——一条 `うん` 的精确匹配把游标甩到很远的后面，之后读得几乎一样的
    cue 也够不着（gi1 实测 `カチ一ナちゃんを助けて…` 在主轨里只差一个 `ー`，被记成漏）。
    这里的剧本行本来就带 obs 时段，用它圈范围更稳；**时间只圈范围，命中仍看文本**。"""
    from collections import defaultdict
    for e in script:
        e.matched, e.sim = -1, 0.0
    bins: dict[int, list[int]] = defaultdict(list)
    for i, ws in enumerate(windows):
        for lo, hi in ws:
            for b in range(int(lo // BIN), int(hi // BIN) + 1):
                bins[b].append(i)

    def cand(t: float) -> list[int]:
        return sorted({i for i in bins.get(int(t // BIN), ())
                       if any(lo <= t <= hi for lo, hi in windows[i])})
    sn = [SA.norm(e.text) for e in script]
    sm = SequenceMatcher(None)
    heads = head_lines(subs)
    n_exact = n_fuzzy = n_none = 0
    sub_hit: list[int] = []
    for si, (t, raw, _) in enumerate(subs):
        vs = variants(raw, heads)
        if not vs:
            sub_hit.append(-1)
            continue
        cnd = cand(t)
        exact = [i for i in cnd if sn[i] in vs]
        # 逐字相等的全是带外行（选项按钮）时，把**只差标点**的带内行也拉进来一起挑：OCR 读漏了省略号，
        # 台词 `「未着」……` 就只剩按钮 `「未着」` 逐字相等；主轨是字幕带，认成选项就把正文换成了按钮的译文
        # （hsr 2:10，2026-09-12 owner 看预览指出）。只在这种情况触发，别的打平照旧
        if exact and not any(band(script[i]) for i in exact):
            cvs = {GS.cnorm(v) for v in vs}
            exact += [i for i in cnd if i not in exact and band(script[i]) and GS.cnorm(script[i].text) in cvs]
        if exact:
            # 先给还没被认领的：同文本两行同时在窗口里时，打字机拆出的后半条 cue 会去认领
            # 第二行、把一条漏抹平。这和 hit_delta 的多重集口径一致（同文本行按条数算），接受；
            # 再先给该在字幕带的行（见上）
            hit = min(exact, key=lambda i: (script[i].matched >= 0, not band(script[i]), i))
            n_exact += 1
        else:
            # 剪枝不改结果：两个 quick 比值都是 ratio 的上界（同 script_align）。
            # 这里剪的是 `< best_s`，script_align 是 `<= best_s`——**别"统一"**：那边平手不换，
            # 这边平手要让给还没被认领的行（下面那个 `s == best_s` 分支），剪掉等分的就换不成了
            hit, best_s = -1, fuzzy
            for i in cnd:
                sm.set_seq2(sn[i])
                s = 0.0
                for v in vs:
                    sm.set_seq1(v)
                    if sm.real_quick_ratio() < best_s or sm.quick_ratio() < best_s:
                        continue
                    s = max(s, sm.ratio())
                if s > best_s or (s == best_s and hit >= 0
                                  and script[hit].matched >= 0 > script[i].matched):
                    hit, best_s = i, s
            if hit >= 0:
                n_fuzzy += 1
                script[hit].sim = round(best_s, 3)
            else:
                n_none += 1
        sub_hit.append(hit)
        if hit >= 0 and script[hit].matched < 0:
            script[hit].matched = si
    sub_extra = claim_contained(script, subs, cand, sub_hit, heads)
    n_contain = sum(1 for h, ex in zip(sub_hit, sub_extra) if h < 0 and ex)
    return {"subs": len(subs), "exact": n_exact, "fuzzy": n_fuzzy, "contain": n_contain,
            "unmatched_subs": n_none - n_contain, "jumps": 0,
            "sub_hit": sub_hit, "sub_extra": sub_extra, "heads": heads}


def score(ref: Ref, script: list[SA.Entry], subs, fuzzy: float) -> dict:
    """`run` + 把变体组折成一个条目：组里任何一种说法被认领，就记在代表行上
    （`via`：代表行 key -> 实际被认领的那一行）；`sub_hit` 里指向变体的也改指代表行，
    免得同一句话的两种说法各被一条 cue 认领时，重复认领数不出来。
    `script` 可以是 `ref.script` 的深拷贝（两臂各量各的，不互相覆盖 `matched`）。"""
    st = run(script, ref.windows, subs, fuzzy)
    idx = {e.key: i for i, e in enumerate(script)}
    via: dict[str, SA.Entry] = {}
    for e in script:
        rep = ref.variant_of.get(e.key)
        if rep and e.matched >= 0 and script[idx[rep]].matched < 0:
            script[idx[rep]].matched = e.matched
            via[rep] = e
    def rep_of(h):     # 变体被认领时记在代表行上，重复认领才数得出来
        return idx[ref.variant_of[script[h].key]] if script[h].key in ref.variant_of else h
    st["sub_hit"] = [rep_of(h) if h >= 0 else h for h in st["sub_hit"]]
    st["sub_extra"] = [[rep_of(i) for i in ex] for ex in st["sub_extra"]]
    st["via"] = via
    return st


def claims(st: dict) -> list[int]:
    """每一次认领指向的剧本行：主认领 + 第二遍的包含认领（`claim_contained`）。"""
    return [h for h in st["sub_hit"] if h >= 0] + [i for ex in st["sub_extra"] for i in ex]


def repeat_claims(sub_hit: list[int]) -> tuple[int, int]:
    """(被 ≥2 条 cue 认领的剧本行数, 多出来的认领次数之和)。原始轨口径，见模块说明。"""
    c = Counter(h for h in sub_hit if h >= 0)
    return sum(1 for v in c.values() if v > 1), sum(v - 1 for v in c.values())


def head_tail(script: list[SA.Entry], subs, via: dict | None = None,
              skip: set[int] | None = None,
              heads: frozenset[str] = frozenset()) -> list[tuple[int, int, int]]:
    """配上的每一对：(剧本内容字数, 行首缺几个内容字, 行尾缺几个内容字)。

    用内容归一（去标点、小书写假名并大写）比，免得省略号读没了被算成"缺字"——
    那是另一件事（script-corpus 报告文本质量那节）。一条剧本行只取第一条认领它的 cue；
    变体组由另一种说法命中的，拿**被认领的那种说法**比（`score` 的 `via`）。

    判据的洞：取第一个匹配块的起点当"行首缺字"——cue 的读法带着没剥干净的名牌时，第一块
    可能对上剧本中段，行首缺字会被高估。gi-s2 那 61.8% 摆帧证实是立绘遮挡，那次没出错。
    **2026-09-18 起 `heads` 传进来了**（审计 P2 的同一族：报数用的读法要和匹配用的一致），
    名牌不再留在读法里——这个数因此比以前小一点，和以前的读数不能直接比。"""
    out = []
    for k, e in enumerate(script):
        # 包含认领的行按定义整行都在 cue 里，缺字恒为 0，算进去会把这个数压低
        if e.matched < 0 or not band(e) or k in (skip or ()):
            continue
        s = GS.cnorm((via or {}).get(e.key, e).text)
        if not s:
            continue
        best = None
        for v in variants(subs[e.matched][1], heads):
            v = GS.cnorm(v)
            sm = SequenceMatcher(None, v, s, autojunk=False)
            blocks = [b for b in sm.get_matching_blocks() if b.size]
            if not blocks:
                continue
            r = sm.ratio()
            if best is None or r > best[0]:
                best = (r, blocks[0].b, len(s) - (blocks[-1].b + blocks[-1].size))
        if best:
            out.append((len(s), best[1], best[2]))
    return out


def hitset(script: list[SA.Entry]) -> Counter:
    return Counter(SA.norm(e.text) for e in script if band(e) and e.matched >= 0)


def main() -> int:
    # --help 只放用法；和《魔裁》那套的三处差别写在模块文档串里，给读代码的人（发布前清理，Opus 审查 O6）
    ap = argparse.ArgumentParser(description=CLI_DESCRIPTION,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref", help="gamescript.py 的剧本 JSON")
    ap.add_argument("--subs", required=True,
                    help="被量的轨：SRT，或 `*-tracks.json`（取 provenance 里的 main_srt）")
    ap.add_argument("--vs", default=None,
                    help="对比臂的轨（同上两种写法）：报命中集合差（三档）与重复认领的变化")
    ap.add_argument("--fuzzy", type=float, default=0.75, help="同 script_align")
    ap.add_argument("--branch-run", type=int, default=5,
                    help="连续这么多条未命中算『长 gap』。这里**只用来分开报**，长 gap 照样算疑似真漏"
                         "（分支已按对话图排出分母，见 script_align.gap_report 的 long_is_branch）")
    ap.add_argument("--show", type=int, default=8, help="列几条疑似真漏")
    ap.add_argument("--show-unmatched", type=int, default=0,
                    help="列几条对不上剧本的 cue（看幽灵上限里装的是什么）")
    ap.add_argument("--out", default=None, help="数和逐条清单写到这个 JSON")
    a = ap.parse_args()

    ref = load_ref(Path(a.ref))
    doc, script, role = ref.doc, ref.script, ref.role
    pv = doc["provenance"]
    gt = pv["gametext"]
    print(f"剧本：{pv['game']} {gt.get('version') or '?'}（文本包指纹 {gt['fingerprint'][7:19]}），"
          f"{len(doc['units'])} 个对话单元 / {len(script)} 行，由 {Path(pv['obs']).name} 圈出"
          f"（gamescript git_head {pv['git_head']}）")
    if not gt.get("integrity_checked"):
        print("  ⚠ 文本包：上游快照的完整性检查没过或没做")
    subs_path, tp = resolve(a.subs)
    subs = read_cues(subs_path)
    st = score(ref, script, subs, a.fuzzy)
    n_empty = st["subs"] - st["exact"] - st["fuzzy"] - st["contain"] - st["unmatched_subs"]
    print(f"轨 {subs_path.name}"
          + (f"（build_tracks git_head {tp['git_head']}，代码指纹 {tp['code_fp']}）" if tp else "")
          + f"：{st['subs']} 条 cue；精确 {st['exact']}、模糊 {st['fuzzy']}、"
          f"只靠包含认上 {st['contain']}、"
          f"**对不上剧本 {st['unmatched_subs']}（{st['unmatched_subs']/max(1,st['subs']):.1%}，"
          f"幽灵上限——库外的文字：UI / 路人 / 主播自己的字幕…都会落在这里）**"
          + (f"、归一化后为空 {n_empty}" if n_empty else ""))

    dial = [e for e in script if band(e)]
    excl = Counter(e.kind for e in script if not band(e))
    excl["Duplicate"] = sum(1 for u in doc["units"] for ln in u["lines"] if ln["kind"] == "Duplicate")
    excl = +excl
    # 变体被认领是预期内的（记在代表行上），不算"排出分母的却被命中"
    excl_hit = Counter(e.kind for e in script if not band(e) and e.matched >= 0 and e.kind != "Variant")
    hit = [e for e in dial if e.matched >= 0]
    print()
    print(f"剧本侧：**该上屏的 {len(dial)} 条**（另有 "
          + "、".join(f"{k} {v}" for k, v in excl.most_common()) + " 不计），"
          f"命中 {len(hit)}（{len(hit)/max(1,len(dial)):.1%}）")
    if ref.variant_of:
        n_groups = len(set(ref.variant_of.values()))
        print(f"  变体组（同一句话的两种说法，一组算一条）：{n_groups} 组，其中 {len(st['via'])} 组"
              f"由代表行以外的那种说法命中")
    if excl_hit:
        print("  ⚠ 排出分母的条目被主轨命中了："
              + "、".join(f"{k} {v}/{excl[k]}" for k, v in excl_hit.most_common())
              + "——Unwalked 被命中 = 分支判错；Choice / OffBand 被命中 = 字幕带以外的字进了这条轨")
    gr = SA.gap_report(dial, a.branch_run, long_is_branch=False)
    side = obs_side(doc)
    C = evalkit.CLASSES[2]
    missed_all = [(k, "short" if k in gr["short_idx"] else "long") for k, e in enumerate(dial)
                  if e.matched < 0 and evalkit.triviality(e.text) == C]
    attr = Counter((g, side[dial[k].key]) for k, g in missed_all)
    print()
    print(f"未命中的『{C}』按 **obs 里读到过没有** 拆（gamescript 的锚点，只看这一行、这一段）：")
    print(f"  {'':<10}" + "".join(f"{x:>12}" for x in OBS_SIDES))
    for g, name in (("short", "短 gap"), ("long", "长 gap")):
        print(f"  {name:<10}" + "".join(f"{attr[(g, x)]:>14}" for x in OBS_SIDES))
    print(f"  读到过 = 丢在轨这一层；只撞常用句 = 别处的同句也能锚上来，算不得这一处读到过；"
          f"没读到 = 丢在 OCR 之前（没出框 / 遮挡 / 显示太短没采到，要摆帧）")

    print()
    print("按上游角色拆（**用来校准分母**：分母内命中率接近 0 说明它不走字幕带；"
          "『全部』连排出分母的也算，别拿它当分母占比读）：")
    print(f"  {'':<34}{'分母内 命中/条':>18}{'全部 命中/条':>18}")
    rb = Counter(role[e.key] for e in dial)
    rbh = Counter(role[e.key] for e in hit)
    rc = Counter(role[e.key] for e in script)
    rh = Counter(role[e.key] for e in script if e.matched >= 0)
    # 进分母的类全列；不进的只列前几类（绝区零散条目的 head 有上百种），其余并成一行
    shown = [r for r, _ in rc.most_common() if rb[r]]
    shown += [r for r, _ in rc.most_common() if not rb[r]][:max(0, ROLE_ROWS - len(shown))]
    for r in shown:
        print(f"  {r:<34}{evalkit.denom(rbh[r], rb[r]) if rb[r] else '—':>18}"
              f"{evalkit.denom(rh[r], rc[r]):>18}")
    rest = [r for r in rc if r not in shown]
    if rest:
        print(f"  {f'其余 {len(rest)} 类（都不进分母）':<30}{'—':>18}"
              f"{evalkit.denom(sum(rh[r] for r in rest), sum(rc[r] for r in rest)):>18}")

    groups, extra = repeat_claims(claims(st))
    n_extra_lines = sum(len(ex) for ex in st["sub_extra"])
    print(f"\n重复认领（原始轨口径）：{groups} 条剧本行被 ≥2 条 cue 认领，多出 {extra} 次"
          f"（**报警不是错误数**：真实重播 / 打字机拆成两条 cue 都会让它涨）")
    print(f"第二遍『整行落在 cue 里』多认领 {n_extra_lines} 行（一条 cue 装着两行剧本、"
          f"或 cue 混进垃圾行够不着模糊阈值；不许和主认领占同一段字，见 claim_contained）")

    ht = head_tail(script, subs, st["via"], {i for ex in st["sub_extra"] for i in ex},
                   st.get("heads") or frozenset())
    if ht:
        n = len(ht)
        h1 = sum(1 for _, h, _ in ht if h >= 1)
        h2 = sum(1 for _, h, _ in ht if h >= 2)
        t1 = sum(1 for _, _, t in ht if t >= 1)
        ex = sum(1 for _, h, t in ht if h == 0 and t == 0)
        print(f"行首 / 行尾缺字（配上的 {n} 对，按内容字比，不计标点）：首尾都不缺 {evalkit.denom(ex, n)}；"
              f"**行首缺 ≥1 字 {evalkit.denom(h1, n)}**（≥2 字 {evalkit.denom(h2, n)}）；"
              f"行尾缺 ≥1 字 {evalkit.denom(t1, n)}")

    if a.show_unmatched:
        un = [(t, raw) for (t, raw, _), h, ex in zip(subs, st["sub_hit"], st["sub_extra"])
              if h < 0 and not ex]
        print(f"\n对不上剧本的 cue（前 {a.show_unmatched} 条 / 共 {len(un)}）：")
        for t, raw in un[:a.show_unmatched]:
            print(f"  {t:8.1f}s  {raw.replace(chr(10), ' / ')[:80]}")

    short = gr["short"]
    if short and a.show:
        print(f"\n疑似真漏抽样（前 {a.show} 处）：")
        for i, k in short[:a.show]:
            for e in dial[i:i + k]:
                print(f"  [{role[e.key]:<10}] {e.src:<26} {e.text[:56]}")

    delta = None
    if a.vs:
        ha = hitset(script)
        vs_path, vp = resolve(a.vs)
        subs_b = read_cues(vs_path)
        script_b = copy.deepcopy(script)       # B 臂量自己那份，A 的 matched 原样留给下面写 JSON
        st_b = score(ref, script_b, subs_b, a.fuzzy)
        hb = hitset(script_b)
        gained, lost = hb - ha, ha - hb
        g = Counter(evalkit.triviality(t) for t in gained.elements())
        l = Counter(evalkit.triviality(t) for t in lost.elements())
        _, eb = repeat_claims(claims(st_b))
        net = sum(hb.values()) - sum(ha.values())
        print(f"\n命中集合差（归一化文本多重集）：**{a.subs} -> {a.vs}**")
        same = code_same(tp, vp)
        if same is False:
            print(f"  ⚠ 两臂的 build_tracks 代码指纹不同（{tp['code_fp']} / {vp['code_fp']}）——"
                  f"差里混着代码版本的变化")
        elif same is None and tp and vp:
            print("  ⚠ 有一臂的产物没有代码指纹（早于该字段），证明不了两臂是同一份 build_tracks")
        print(f"  命中 {sum(ha.values())} -> {sum(hb.values())}（净 {net:+d}：新增 +{sum(gained.values())}"
              f" / 丢失 -{sum(lost.values())}）；按三档："
              + "  ".join(f"{c} {g[c]-l[c]:+d}" for c in evalkit.CLASSES))
        print(f"  cue {st['subs']} -> {st_b['subs']}；对不上剧本 {st['unmatched_subs']} -> "
              f"{st_b['unmatched_subs']}；重复认领 {extra} -> {eb}")
        ex_l = [t for t in lost if evalkit.triviality(t) == C][:a.show]
        ex_g = [t for t in gained if evalkit.triviality(t) == C][:a.show]
        if ex_l:
            print(f"  丢失的『{C}』：" + " | ".join(t[:24] for t in ex_l))
        if ex_g:
            print(f"  新增的『{C}』：" + " | ".join(t[:24] for t in ex_g))
        delta = {"vs": a.vs, "vs_git_head": vp.get("git_head"), "vs_code_fp": vp.get("code_fp"),
                 "hit_a": sum(ha.values()), "hit_b": sum(hb.values()),
                 "by_class": {c: g[c] - l[c] for c in evalkit.CLASSES},
                 "repeat_extra_b": eb, "cues_b": st_b["subs"],
                 "unmatched_b": st_b["unmatched_subs"]}

    if a.out:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "ref": a.ref, "subs": str(subs_path), "ref_git_head": pv["git_head"],
            "ref_code_fp": pv.get("code_fp"),
            "tracks_git_head": tp.get("git_head"), "tracks_code_fp": tp.get("code_fp"),
            "gametext_fingerprint": pv["gametext"]["fingerprint"],
            "gametext_integrity_checked": bool(pv["gametext"].get("integrity_checked")),
            "cues": st["subs"], "exact": st["exact"], "fuzzy": st["fuzzy"],
            "contain_cues": st["contain"], "contain_lines": n_extra_lines,
            "unmatched_cues": st["unmatched_subs"],
            "band_entries": len(dial), "band_hit": len(hit),
            "excluded": dict(excl), "excluded_hit": dict(excl_hit),
            "variant_groups": len(set(ref.variant_of.values())), "variant_via": len(st["via"]),
            "gap_long_entries": gr["n_long"], "gap_short_entries": gr["n_short"],
            "miss_by_class": gr["by_class"],
            "repeat_groups": groups, "repeat_extra": extra,
            "head_tail": {"pairs": len(ht), "head_ge1": sum(1 for _, h, _ in ht if h >= 1),
                          "head_ge2": sum(1 for _, h, _ in ht if h >= 2),
                          "tail_ge1": sum(1 for _, _, t in ht if t >= 1)},
            "delta": delta,
            "missed": [{"key": e.key, "role": role[e.key], "unit": e.src, "text": e.text,
                        "gap": "short" if k in gr["short_idx"] else "long"}
                       for k, e in enumerate(dial) if e.matched < 0],
            "missed_content": [{"key": dial[k].key, "gap": g, "obs": side[dial[k].key],
                                "role": role[dial[k].key], "unit": dial[k].src,
                                "text": dial[k].text} for k, g in missed_all],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n-> {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
