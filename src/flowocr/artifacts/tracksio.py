"""**一级产物只有一份**：富信息 JSON 的读、写、校验。

形状与契约的正式文档：`docs/architecture/artifacts.md`（需求原文与四轮复审的过程在
`output-format-plan（已归档）`）。以前一条链上散着三份
产物——`-tracks.json`（没过滤的 run）、逐区域 `.srt`（过滤和切分发生在"写 SRT"那一步）、
`-provenance.json`（版本戳），**信息在往下走的每一步都在丢**，两个用途看的是两份不同的
东西，回抠还得把过滤再抄一遍（audit-4 C6）。

现在的契约：

* `build_tracks` 写**一份** JSON，它是唯一的事实来源；
* **切分（cue）、常驻 UI 的判定、并带的结果都已经写在 JSON 里**；
* 导出器（`flowocr.output.export`）只做**投影**：按 flag 取舍、按 cue 排版，不重新判断。

`srtio` 是同一个思路（"一条 = 一个 cue"只有一处口径），这里照抄它的做法：
**读的时候就校验**，对不上直接抛，不静默少算。

## 形状

```text
schema / tag / meta / frame_us / size / window_sec / n_windows
provenance : git_head, code_fp, argv, args, main_track, main_srt, main_cues, main_candidates
regions[]  : index, label, lang, primary_score, n_windows_present, features,
             align?（left / center / right，叠加导出锚哪条边，flowocr.analyze.align 判的）,
             align_how?（by = stats / vote / none、n、spread、votes：判定依据）
tracks[]   : id("r00" / "main"), kind, region|regions, label, srt, ui_lines[], cues[]
             cues[] : id, t_start, t_end, events[]（事件 id，**顺序就是导出时的行序**）
events[]   : id, region, t_start, t_full, t_end, box, text, conf, n_obs,
             n_independent, flags[], boxes[]?（只有 moving 的才落）, texts[]?（可选）,
             t_full_sampled?（采样级"全字出现"，build_tracks.sampled_full；t_full 只由回抠填）,
             t_full_how?（回抠填：{"first", "full"} 各是 agree / single / none，全字还可能是 capped，见 typewriter_fuse）,
             t_full_end?（回抠填：完全显示结束，淡出开始的前一帧；没淡出时 ≈ t_end）, t_end_how?（curve / ncc / open：open = 窗里没看到消失，t_end 只是下界）
text_effect? : 回抠写的整段素材的效果统计：samples, verdict?（instant / typewriter / likely-typewriter；
             样本 < 8 时不写）, ms_per_char_median?, iqr_over_median?, fade_out_*?
```

`align` / `align_how` 可选：旧产物没有，不读对齐的下游（SRT、匹配、体检）照常能读；
叠加预设读到没有 `align` 的产物当场报错、提示重跑 build_tracks（`flowocr.output.script`）。

两处**故意**的设计，改之前先读：

1. **过滤不删事件，只打标。** 用途 1 要留、用途 2 要剔，同一份数据。
   常驻 UI 的判定是**逐轨**的（同一行在区域轨里够比例、并进主轨后不一定够），
   所以权威清单是 `tracks[].ui_lines`；`events[].flags` 里的 `ui_filtered`
   是"在它自己的区域轨里被剔了"的方便标记，别拿它去投影主轨。
2. **cue 里的事件顺序就是 SRT 的行序**，不要在导出器里重排——
   区域轨按 `(cy, cx)` 排、`segment` 模式另有排法，重排一次就对不上原产物了。
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA = "flowocr-tracks/1"

ALIGNS = ("left", "center", "right")
"""`regions[].align` 的取值。"""


class SchemaError(ValueError):
    """产物和契约对不上。**抛出去，不要静默降级**——静默降级正是这个项目栽过的那类事故。"""


def dump(path: Path, doc: dict) -> dict:
    """写一份产物（写前先自校验：坏产物不许落盘）。"""
    doc = {"schema": SCHEMA, **doc}
    validate(doc)
    Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def load(path: Path) -> dict:
    """读一份产物并校验。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(doc)
    return doc


def validate(doc: dict) -> None:
    """结构自检。**只查"自己说的和自己有的对不对得上"**，不查数值好坏。"""
    if doc.get("schema") != SCHEMA:
        raise SchemaError(f"schema 不是 {SCHEMA}：{doc.get('schema')!r}"
                          "（旧产物不做兼容，重跑 build_tracks）")
    for key in ("meta", "frame_us", "size", "provenance", "regions", "tracks", "events"):
        if key not in doc:
            raise SchemaError(f"缺字段 {key}")
    n = len(doc["events"])
    for i, ev in enumerate(doc["events"]):
        if ev.get("id") != i:
            raise SchemaError(f"events[{i}].id={ev.get('id')}——id 必须就是下标")
        if ev["t_end"] < ev["t_start"]:
            raise SchemaError(f"events[{i}] 时间反了")
        if ev.get("t_full") is not None and not (ev["t_start"] <= ev["t_full"] <= ev["t_end"]):
            raise SchemaError(f"events[{i}].t_full 不在 [t_start, t_end] 里")
        if ev.get("t_full_sampled") is not None and not (ev["t_start"] <= ev["t_full_sampled"] <= ev["t_end"]):
            raise SchemaError(f"events[{i}].t_full_sampled 不在 [t_start, t_end] 里")
        if ev.get("t_full_end") is not None and not (ev["t_start"] <= ev["t_full_end"] <= ev["t_end"]):
            raise SchemaError(f"events[{i}].t_full_end 不在 [t_start, t_end] 里")
    regions = {r["index"] for r in doc["regions"]}
    for r in doc["regions"]:
        if "align" in r and (r["align"] not in ALIGNS or "align_how" not in r):
            raise SchemaError(f"regions[{r['index']}].align={r['align']!r}：只能取 {ALIGNS}，而且要带 align_how")
    for ev in doc["events"]:
        if ev["region"] not in regions:
            raise SchemaError(f"events[{ev['id']}].region={ev['region']} 没有对应的区域")
    seen: set[str] = set()
    # **非区域轨装的必须是区域轨里已有的事件**（2026-09-20 复审第 1 条）：
    # `kind="main"`（并带的聚合投影）和 `kind="nameplate"`（名牌轨）都是
    # **同一批 run 的另一种投影**——"打标不删"的核心不变量就是这个子集关系。
    # 放在 schema 校验里而不是守卫里：每次 `load` 都核一遍，覆盖面更广。
    in_region = {eid for tr in doc["tracks"] if tr.get("kind") == "region"
                 for cue in tr["cues"] for eid in cue["events"]}
    for tr in doc["tracks"]:
        if tr["id"] in seen:
            raise SchemaError(f"轨 id 重复：{tr['id']}")
        seen.add(tr["id"])
        if tr.get("kind") != "region":
            extra = {eid for cue in tr["cues"] for eid in cue["events"]} - in_region
            if extra:
                raise SchemaError(
                    f"{tr['id']}（kind={tr.get('kind')}）里有 {len(extra)} 个事件不在任何区域轨里"
                    f"（如 {sorted(extra)[:5]}）——非区域轨只能是区域轨的另一种投影")
        for cue in tr["cues"]:
            if not cue["events"]:
                raise SchemaError(f"{tr['id']} 的 cue {cue['id']} 是空的")
            for eid in cue["events"]:
                if not 0 <= eid < n:
                    raise SchemaError(f"{tr['id']}/{cue['id']} 指向不存在的事件 {eid}")


def track(doc: dict, track_id: str) -> dict:
    for tr in doc["tracks"]:
        if tr["id"] == track_id:
            return tr
    raise SchemaError(f"没有这条轨：{track_id}（有的是 {[t['id'] for t in doc['tracks']]}）")


def main_track(doc: dict) -> dict | None:
    """provenance 指定的那条主轨。**不许 glob 猜文件名**（2026-09-07 的教训）。"""
    tid = doc["provenance"].get("main_track") or ""
    return track(doc, tid) if tid else None


def cue_lines(doc: dict, tr: dict, cue: dict, drop_filtered: bool = True) -> list[dict]:
    """一条 cue 在这条轨上要导出的事件。**投影，不是判断**：

    `drop_filtered=True`（SRT、`default` 叠加用）剔常驻 UI；`False`（`dev` / `default_all` 叠加用）全给，
    由调用方按 flag 换外观。

    剔谁由**两条判据**合起来定（2026-09-20）：

    * `ui_lines` —— 文本那条，**逐轨**（同一行在区域轨里够比例、在主轨里不一定够）；
    * 事件上的 `ui_footprint` 标 —— 框位那条，**全局**（这个位置上的东西就是 UI，
      和它落在哪条轨无关）。框位判决**没法用"哪些文本"表达**——同一个文本在别的位置是正文。

    ⚠ 两者**作用域不同，别混成一个 flag**：混了之后导出器会把"区域轨里判的 UI"
    带进主轨，主轨 592 → 541 条、和 `build_tracks` 自己写的对不上（守卫当场抓到）。
    旧产物（只有 ui_lines、事件没标）照旧能投影。

    事件上的 `ruby` 标（振り仮名，`build_tracks.ruby_runs`，全局、按几何）也只在 `drop_filtered=True` 时剔：
    它不是 UI，是正文的注音，SRT / 匹配器 / `default` 叠加不单独画它；可判据是几何的、会误标
    （任务 HUD 里的假名、面板小标题），所以全部文字的视图（`False`）照给，由调用方按标换外观——
    分类写进产物，藏不藏是输出的策略（2026-09-26 审计）。
    """
    ui = set(tr.get("ui_lines") or ())
    evs = [doc["events"][i] for i in cue["events"]]
    return [e for e in evs
            if not (drop_filtered and (e["text"].strip() in ui
                                       or {"ui_footprint", "ruby"} & set(e.get("flags") or ())))]


def ui_flagged(ev: dict) -> bool:
    """这个事件身上**有没有任何一种 UI 标**。

    ⚠⚠ **只给"作用域就是事件自己的区域轨"的地方用**——`flowocr.analyze.align` 按区域做统计时。
    叠加 ASS 按轨取事件、按那条轨的 `ui_lines` 标 UI，不用它。**`cue_lines` 绝对不能用它**：`ui_filtered` 是
    **逐轨**判决留在事件上的方便标记（它是在**区域轨**那一遍盖的），
    而 `ui_lines` 才是权威清单；拿它当全局判据，就会把"区域轨里判的 UI"带进主轨。

    这个坑 2026-09-20 一天之内踩了两次：第一次是把两种判决混进同一个 flag
    （主轨 592 → 541，守卫抓到）；第二次是我在审计七修 ASS 那条路时，
    顺手让 `cue_lines` 也走了这个函数——`e-yuka-f5-w8n5` 的并带主轨
    **build 写 457 条、重导只剩 329 条**（复审抓到；上一次的守卫没挡住，
    因为它跑的那段主轨就是一条区域轨，`ui_filtered` 和 `ui_lines` 恰好等价）。

    **判决的作用域写在这里，别再合并**：
    `ui_lines`（逐轨、文本）→ `cue_lines`；`ui_footprint`（全局、框位）→ 两边都算；
    `ui_filtered`（逐轨判决在事件上的影子）→ **只在没有轨的时候**当参考。
    """
    return bool({"ui_filtered", "ui_footprint"} & set(ev.get("flags") or ()))


def events_of_region(doc: dict, index: int) -> list[dict]:
    return [e for e in doc["events"] if e["region"] == index]


def regions_with_runs(doc: dict) -> list[dict]:
    """"按区域看事件"的视图：每个区域一份 `runs`（就是它的事件，按 `t_start` 排）。

    **这不是兼容层**——旧的 `regions[].runs` 那份产物已经没了；
    只关心几何和文本的工具（画框、覆盖率、名牌判据…）用这个视图最省事，
    而且**拿到的是同一批事件对象**，改不改都指向 `doc["events"]` 里那一份。
    """
    by_region: dict[int, list[dict]] = {}
    for ev in doc["events"]:
        by_region.setdefault(ev["region"], []).append(ev)
    return [{**r, "runs": sorted(by_region.get(r["index"], []),
                                 key=lambda e: e["t_start"])} for r in doc["regions"]]


def event_times(doc: dict) -> dict[int, tuple[int, int]]:
    """回抠**之前**把每个事件的首尾抄一份。`refresh_cue_bounds` 要拿它配对边界。"""
    return {e["id"]: (e["t_start"], e["t_end"]) for e in doc["events"]}


def refresh_cue_bounds(doc: dict, before: dict[int, tuple[int, int]] | None = None) -> None:
    """事件的时间被改过（回抠）之后，把每条 cue 的首尾按成员重算。

    **只算时间，不重新切分**——切分是 `build_tracks` 那一步定下来的，
    回抠只更新时间信息（audit-4 C6 要的就是这个：回抠不该有第二套导出规则）。

    三条规矩，每条都是一次复审换来的：

    1. **一条 cue 的边界只认它自己的成员**（第三轮复审）。靠 `before`
       （回抠前的快照，`event_times()`）配对：当初落在这条边界上的成员，
       现在挪到哪儿了。起点取最早、终点取最晚——**cue 要盖住自己的成员**。
       ⚠ 只按"旧时间戳相同"去合并是错的：别的轨、别的 cue 里碰巧同一时刻
       开始/结束的事件会被当成同一个切点，于是**只回抠了 r00，r01 的 cue 也跟着变**；
       `raw` 档里两条各自回抠到 0.75 / 1.25 s，也会被一起压到 0.75 s。
    2. **真正共享切点的两段要一起挪**（第二、四轮复审）。判据有两半：同一条轨内
       `前一段的旧终点 == 后一段的旧起点`，**并且两段有共同成员**——也就是有一条
       run 跨过了这个切点，切点是"别的东西变了"切出来的。
       ⚠ 只看时间相接是错的：相接的两条**独立**字幕之间是真边界，
       回抠说前一条 1.8 s 消失、后一条 1.9 s 出现，中间 0.1 s 屏幕本来就是空的，
       绑成一个切点就会**在空白期提前显示**（第四轮复审的反例，
       它同时推翻了这里原先那句"两者都不会显示屏幕上没有的东西"）。
       两边给出的新值不一致时取**最早**那个证据；代价是跨过切点的那条被提前切掉一点。
    3. **切点必须单调**：每条边界各自配对，相邻两个完全可能一个往后挪、一个往前挪，
       不夹一道就会出负时长的 cue。

    没有 `before` 就一个边界都不动——那时无从知道是谁生成了这条边界。

    为什么不能按"成员生命周期的 min/max"重算（第一轮复审）：`segment` 档在
    **每个首尾边界**都切一刀，于是一条常驻行（`Auto` 挂 40 秒）同时是 20 条 2 秒 cue
    的成员，min/max 会把 **20 条字幕全部撑成 0–40 秒**——切分等于被丢掉了。
    """
    if not before:
        return
    for tr in doc["tracks"]:
        cues = tr["cues"]
        if not cues:
            continue
        order = sorted(range(len(cues)), key=lambda k: (cues[k]["t_start"], cues[k]["t_end"]))
        old = [(c["t_start"], c["t_end"]) for c in cues]
        lo: list[int | None] = [None] * len(cues)
        hi: list[int | None] = [None] * len(cues)
        for k, cue in enumerate(cues):
            evs = [doc["events"][i] for i in cue["events"] if doc["events"][i]["id"] in before]
            s_cand = [e["t_start"] for e in evs if before[e["id"]][0] == old[k][0]]
            e_cand = [e["t_end"] for e in evs if before[e["id"]][1] == old[k][1]]
            lo[k] = min(s_cand) if s_cand else None
            hi[k] = max(e_cand) if e_cand else None
        # 相邻且**共享切点**的，两边合成一个值；顺带保证它不早于前一段的起点
        members = [{doc["events"][i]["id"] for i in c["events"]} for c in cues]
        for u, v in zip(order, order[1:]):
            # 判据是**有成员跨过这个切点**，不是"旧时间戳首尾相接"（第四轮复审）：
            # 相接的两条**独立**字幕（A 0–2、B 2–4，没有共同成员）之间是真边界，
            # 回抠说 A 1.8 s 消失、B 1.9 s 出现，中间那 0.1 s 屏幕本来就是空的——
            # 硬把它们绑成一个切点，B 就会在空白期提前显示。
            # 反过来，A 挂 0–10 s、B 只有 0–2 s 时，切点在 A 的生命期**里面**，
            # 那才是同一个切点：不一起挪，A 会在交界处重复显示。
            if old[u][1] != old[v][0] or not (members[u] & members[v]):
                continue
            both = [x for x in (hi[u], lo[v]) if x is not None]
            if not both:
                continue
            shared = max(min(both), lo[u] if lo[u] is not None else old[u][0])
            hi[u] = lo[v] = shared
        for k in order:
            a = lo[k] if lo[k] is not None else old[k][0]
            b = hi[k] if hi[k] is not None else old[k][1]
            # 夹只夹**这一条自己**（首尾不许倒挂）。**不许跨 cue 夹**：
            # `raw` / `cue` 档的 cue 本来就可以时间重叠——两条字幕同屏是常态，
            # 跨 cue 夹单调会把后一条的起点硬推到前一条的终点上（第三轮复审的第二个反例）。
            cues[k]["t_start"], cues[k]["t_end"] = a, max(a, b)


DRIVER_OPTS = ("--outdir", "--tag")
"""驱动（head2head.sh / gametext_eval.sh）自己填的选项，不属于"这一臂的旋钮"。"""


def build_args(prov: dict) -> list[str]:
    """provenance 的 `argv` 去掉驱动自己填的那几样（obs 位置参数、`--outdir`、`--tag`），剩下的就是
    这一臂的 BUILD_ARGS，驱动拿它和这一趟要的比，对不上就重建。

    以前两个驱动各写 `argv[5:]`，依赖"恰好按 obs / --outdir / --tag 的顺序、用空格形式传"
    （audit-6）；这里按名剥，obs 按 provenance 里记的那个字符串认。
    ⚠ provenance 里的 `argv` 经过 `provenance.portable`（绝对路径记成相对数据根 / `~/…`）：驱动传的 BUILD_ARGS 若含绝对路径，
    要先过同一个函数再比，否则白重建一次（现有驱动都传相对路径，不受影响）。"""
    out, argv, obs_seen, i = [], list(prov["argv"]), False, 0
    while i < len(argv):
        x = argv[i]
        if x in DRIVER_OPTS:
            i += 2
            continue
        if not obs_seen and x == prov.get("obs"):
            obs_seen = True
        elif not any(x.startswith(o + "=") for o in DRIVER_OPTS):
            out.append(x)
        i += 1
    return out


def load_regions(path: Path) -> tuple[dict, list[dict]]:
    """`load` + `regions_with_runs`，一步到位。"""
    doc = load(path)
    return doc, regions_with_runs(doc)


def _main(argv: list[str] | None = None) -> int:
    """给 shell 脚本用的取值口：

        python -m flowocr.artifacts.tracksio <tracks.json>            # 整个 provenance（JSON）
        python -m flowocr.artifacts.tracksio <tracks.json> main_srt   # 其中一个字段

    **驱动脚本别再自己 `json.load` 一份**——校验也就跟着丢了。
    产物坏了这里会抛，退出码非零，脚本当场停，而不是拿空字符串往下走。
    """
    import argparse

    ap = argparse.ArgumentParser(description="读 -tracks.json 里的 provenance")
    ap.add_argument("tracks")
    ap.add_argument("key", nargs="?", default="")
    a = ap.parse_args(argv)
    prov = load(Path(a.tracks))["provenance"]
    if not a.key:
        print(json.dumps(prov, ensure_ascii=False, indent=1))
    else:
        v = prov.get(a.key, "")
        print(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
