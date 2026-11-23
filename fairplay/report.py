"""中文报告渲染：把赛前比对、赛中补赛影响与名额复核连成一份结论。

报告只给区间与条件结论：任何"概率"都来自精确枚举与显式假设，
不构成确定排名；所有关键数字都能在 records 中追溯到来源 id。
"""
from __future__ import annotations

from . import chance, lookback, remedy
from .core import SeasonView, qualifier_branches, season_points
from .records import Records


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _pct1(x: float) -> str:
    return f"{x * 100:.1f}%"


def _signed(x: float) -> str:
    return f"{'+' if x >= 0 else ''}{x * 100:.0f}%"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _display_len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append(
            "  ".join(_pad(cell, widths[i]) for i, cell in enumerate(row))
        )
    return lines


def _display_len(s: str) -> int:
    return sum(2 if ord(c) > 0x2E7F else 1 for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * (width - _display_len(s))


# ---------------------------------------------------------------- 各节内容

def _section_standings(view: SeasonView) -> list[str]:
    lines = ["二、当前积分与名额状态（regional 阶段，积分可按对局 id 逐条复核）"]
    points, played_wins = season_points(view.decided, view.rules, sorted(view.teams))
    for area in view.areas:
        rows = []
        for t in view.area_teams(area):
            ev = chance.team_evidence(view, t)
            marks = []
            if t in view.locked_promoted:
                marks.append("资格已锁定")
            if t in view.withdrawn:
                marks.append("已退赛")
            rows.append(
                [
                    t,
                    str(view.teams[t].seed),
                    str(points[t]),
                    str(played_wins[t]),
                    str(ev["admin_wins"]),
                    f"{ev['wins_vs_first']}/{ev['wins_vs_bench']}",
                    ",".join(ev["remaining"]) or "-",
                    ",".join(ev["pending"] + ev["withdrawal"]) or "-",
                    "；".join(marks),
                ]
            )
        lines.append(f"[{area} 区]")
        lines += _table(
            ["队", "种子", "积分", "已开打胜", "判分胜", "胜正选/替补", "待赛", "待裁/退赛", "状态"],
            rows,
        )
    locked = sorted(view.locked_promoted)
    if locked:
        refs = [r.id for r in view.rulings if r.operation == "promoted_locked"]
        lines.append(
            f"已锁定名额：{', '.join(locked)}（来源裁决单 {', '.join(refs)}，锁定优先占用本区名额）"
        )
    if view.withdrawn:
        refs = [r.id for r in view.rulings if r.operation == "withdraw"]
        lines.append(
            f"已退赛：{', '.join(sorted(view.withdrawn))}（裁决单 {', '.join(refs)}；"
            "已赛结果仍计入对手积分，未赛场次待裁，不参与名额分配）"
        )
    return lines


def _section_result_types(view: SeasonView) -> list[str]:
    lines = ["三、结果类型拆分：对实力证据与晋级积分的不同作用"]
    counts = {"played": 0, "administrative": 0, "withdrawal": 0, "scheduled": 0}
    for g in view.all_regional_games():
        counts[g.status] = counts.get(g.status, 0) + 1
    lines.append(
        "已开打 {played} 场（积分+实力证据）；行政判分 {administrative} 场（只计积分，"
        "不作实力证据）；退赛 {withdrawal} 场（不给分、待裁）；待赛 {scheduled} 场".format(**counts)
    )
    for g in view.decided:
        if g.status == "administrative":
            lines.append(
                f"  判分场次 {g.id}：{g.a}-{g.b} 判 {g.winner} 胜（{view.rules.admin_points} 分，"
                "不计入任何一方的实力样本）"
            )
    for g in view.withdrawal_games:
        lock_note = "已锁定，不可改" if g.locked else "未锁定，仍可裁决"
        lines.append(f"  退赛场次 {g.id}：{g.a}-{g.b}（{lock_note}）")
    for g in view.pending_games:
        lines.append(f"  待裁场次 {g.id}：{g.a}-{g.b}（一方已退赛，原排期失效，等待处置）")
    return lines


def _section_chances(view: SeasonView, dist0, gaps, gaps_cf) -> list[str]:
    lines = [
        "四、机会比较（H0 机会基数：全部待赛五五开，只反映赛程与裁决结构；"
        "为精确枚举而非一次模拟）"
    ]
    rows = []
    for area in view.areas:
        for t in view.area_teams(area):
            if t in view.withdrawn:
                rows.append([t, area, "退赛", "-", "-", "-"])
                continue
            mark = "（已锁定）" if t in view.locked_promoted else ""
            rows.append(
                [t, area, mark or "-", _pct(dist0.rank[t]), _pct(dist0.shared[t]), _pct(dist0.total[t])]
            )
    lines += _table(["队", "区", "状态", "P(区名额)", "P(共享)", "P(晋级)"], rows)
    limit = view.rules.max_opp_gap
    lines.append(f"章程机会差上限 {_pct(limit)}；按名额池汇总超限组合（每池仅列差距最大的 3 对）：")
    if gaps:
        by_pool: dict[str, list] = {}
        for g in gaps:
            by_pool.setdefault(g.pool, []).append(g)
        for pool, pool_gaps in by_pool.items():
            desc = pool_gaps[0].slot_desc
            lines.append(f"  [{desc}] 超限 {len(pool_gaps)} 对：")
            for g in pool_gaps[:3]:
                lines.append(f"    {g.team_hi} 高于 {g.team_lo}，差距 {_pct1(g.gap)}")
    else:
        lines.append("  无。")
    lines.append(
        "反事实对照：若判分/退赛/待裁场次全部按正常对局进行（双方各 50%），"
        f"超限组合数 {len(gaps)} → {len(gaps_cf)}。"
        "两者之差即裁决处置本身造成的机会差，与队伍实力无关。"
    )
    return lines


def _section_hypotheses(view: SeasonView, dists: dict[str, object]) -> list[str]:
    lines = [
        "五、强度解释集合与排序稳定性（跨赛区可比对局不足，保留多种说得通的解释；"
        "以下为区间，不是确定排名）"
    ]
    names = list(dists)
    teams = [t for t in sorted(view.teams) if t not in view.withdrawn]
    rows = []
    for t in teams:
        vals = [dists[h].total[t] for h in names]
        lo, hi = min(vals), max(vals)
        flip = "是" if lo < 0.5 <= hi else ""
        rows.append(
            [t]
            + [_pct(dists[h].total[t]) for h in names]
            + [f"{_pct(lo)}~{_pct(hi)}", flip]
        )
    lines += _table(["队"] + names + ["区间", "解释间翻转"], rows)
    lines.append("解释口径：")
    for key, desc, _ in chance.hypothesis_set(view):
        lines.append(f"  {key}：{desc}")
    flips = [r[0] for r in rows if r[-1]]
    if flips:
        lines.append(
            "注意：以下队伍的晋级与否随强度解释改变（任一单一解释都不足以定案）："
            + ", ".join(flips)
        )
    return lines


def _section_shared_slots(view: SeasonView, records: Records) -> list[str]:
    lines = ["六、共享名额的赛区强度情景（结论随假设变化，需章程层面选定口径）"]
    past = [
        SeasonView.from_records(records, s)
        for s in records.seasons
        if s != view.season
    ]
    bridge = chance.bridge_record(past, records.games)
    for season in sorted(bridge["record"]):
        rec = bridge["record"][season]
        ros = bridge["rosters"].get(season, {})
        lines.append(
            f"往届桥接对局 {season}："
            + ", ".join(f"{area} 区胜 {n} 场" for area, n in sorted(rec.items()))
            + "；出场阵容 "
            + ", ".join(f"{k}×{v}" for k, v in sorted(ros.items()))
        )
    lines.append(
        "提示：桥接对局样本小，且北区全部以替补阵容出场、南区全部正选，"
        "不能直接当作赛区实力差；以下按修正区间分别给结论。"
    )
    rows = []
    membership: dict[str, set] = {}
    for label, offset in chance.offset_scenarios(view.rules.win_points, view.areas):
        dist = chance.qualification_distribution(view, label=label, area_offset=offset)
        top = sorted(
            ((t, p) for t, p in dist.shared.items() if p > 0),
            key=lambda kv: (-kv[1], kv[0]),
        )[: view.rules.shared_slots + 2]
        holders = {t for t, p in top[: view.rules.shared_slots] if p >= 0.5}
        membership[label] = holders
        rows.append(
            [
                label,
                ", ".join(f"{t} {_pct(p)}" for t, p in top) or "-",
                ", ".join(sorted(holders)) or "（无过半者）",
            ]
        )
    lines += _table(["强度情景", "共享名额概率靠前", "过半占位"], rows)
    base = membership.get("均衡（不修正）", set())
    changed = [k for k, v in membership.items() if v != base]
    if changed:
        lines.append(
            "分歧提示：共享名额归属随强度情景变化（与均衡情景不一致的情景："
            + "、".join(changed)
            + "）。这部分分歧影响的是跨区共享名额，不能当作实力差异定论。"
        )
    return lines


def _section_makeup(view: SeasonView, dist0) -> list[str]:
    lines = ["七、补赛窗口安排（不拆散已锁定对阵与休息保护；场地惯例 北区=roomA 南区=roomB）"]
    open_ids = [g.id for g in view.open_games]
    plans = remedy.makeup_plans(view, open_ids)
    if not plans:
        lines.append("  现有场地时间槽下没有任何可行排法。")
        return lines
    lines.append(f"可用场地槽：{len([s for s in view.court_slots if not s.blocked])} 个；待赛 {len(open_ids)} 场。")
    for area in view.areas:
        arena = remedy.AREA_ARENA.get(area)
        n_games = sum(1 for g in view.open_games if view.teams[g.a].area == area)
        n_rows = len(
            [s for s in view.court_slots if not s.blocked and s.arena == arena]
        )
        verdict = "可全部排入" if n_games <= n_rows else f"超出 {n_games - n_rows} 场，需取消或改期"
        lines.append(f"  {area} 区：待赛 {n_games} 场 / 可用 {arena} 槽 {n_rows} 个 → {verdict}")
    for plan in plans:
        if plan.dropped:
            lines.append(f"方案（取消 {', '.join(plan.dropped)}）：")
        else:
            lines.append("方案（全部排入）：")
        assign = sorted(plan.assignments.items(), key=lambda kv: (kv[1].slot, kv[0]))
        for gid, row in assign:
            g = next(x for x in view.all_regional_games() if x.id == gid)
            lines.append(f"  {gid}（{g.a}-{g.b}）→ slot {row.slot} @{row.arena}")
        if plan.alternatives:
            lines.append(f"  （同一放弃集合下另有 {plan.alternatives} 种排法）")
        dist = chance.qualification_distribution(
            view, label="补赛方案", void_ids=frozenset(plan.dropped)
        )
        gaps = chance.opportunity_gaps(view, dist)
        lines.append(
            f"  该方案下最大机会差 {_pct1(gaps[0].gap) if gaps else 0.0}"
            f"（上限 {_pct(view.rules.max_opp_gap)}），超限组合 {len(gaps)} 个"
        )
        aff = remedy.affected_teams(dist0, dist)
        if aff:
            lines.append(
                "  相对基线受影响队伍：" + ", ".join(f"{t} {_signed(d)}" for t, d in aff)
            )
    return lines


def _section_pending(view: SeasonView, dist0) -> list[str]:
    lines = ["八、退赛与申诉的待决影响"]
    for g in view.pending_games + [x for x in view.withdrawal_games if not x.locked]:
        non_w = [t for t in (g.a, g.b) if t not in view.withdrawn]
        lines.append(f"场次 {g.id}（{g.a}-{g.b}）的处置选项：")
        opts = [("确认退赛", remedy.apply_void(view, [g.id]))]
        if len(non_w) == 1:
            opts.append((f"行政判分给 {non_w[0]}", remedy.apply_admin_points(view, [g.id])))
        for title, v2 in opts:
            dist = chance.qualification_distribution(v2, label=title)
            gaps = chance.opportunity_gaps(v2, dist)
            aff = remedy.affected_teams(dist0, dist)
            impact = (
                ", ".join(f"{t} {_signed(d)}" for t, d in aff)
                if aff
                else "与基线一致（这些场次当前本就不计分）"
            )
            lines.append(
                f"  {title}：最大机会差 {_pct1(gaps[0].gap) if gaps else 0.0}；受影响 {impact}"
            )
        lines.append("  补赛不可行：退赛队伍无法出场，且不能拆散已锁定对阵另找对手。")
    for r in view.appeals:
        lines.append(f"申诉 {r.id}（{r.team} 申请重议行政判分）的情景：")
        admin_games = [g for g in view.decided if g.status == "administrative"]
        for g in admin_games:
            v_void = remedy.apply_void(view, [g.id])
            dist_void = chance.qualification_distribution(v_void, label="取消判分")
            aff = remedy.affected_teams(dist0, dist_void)
            lines.append(
                f"  取消 {g.id}（{g.a}-{g.b}）判分：受影响 "
                + (", ".join(f"{t} {_signed(d)}" for t, d in aff) or "无明显变化")
            )
            open_ids = [x.id for x in view.open_games]
            plans = remedy.makeup_plans(view, open_ids + [g.id])
            placed = [p for p in plans if g.id in p.assignments]
            if placed and not placed[0].dropped:
                lines.append(f"  改判重赛 {g.id}：可排入补赛窗口。")
            elif placed:
                lines.append(
                    f"  改判重赛 {g.id}：可排入，但需同时取消 "
                    + "、".join(placed[0].dropped)
                    + "（各区待赛场数超出对应场地槽数）。"
                )
            else:
                lines.append(
                    f"  改判重赛 {g.id}：补赛窗口排不下（受休息保护/场地惯例/已锁定对阵限制），"
                    "在现有场地与时间下不可行。"
                )
    return lines


def _section_lookback(records: Records, season: int) -> list[str]:
    lines = ["九、往届同一赛程阶段回看（校准当前推算，不是事后结论）"]
    for row in lookback.lookback(records, season):
        proj = ", ".join(f"{t} {_pct(p)}" for t, p in row.projected)
        lines.append(
            f"{row.season} 赛季 {row.area} 区（按当前赛季同区进度，已赛 {row.decided_games} 场时）："
            f"当时推算 {proj}"
        )
        lines.append(
            f"  最终实际晋级 {', '.join(row.actual)}；"
            f"当时前二命中 {', '.join(row.hit) or '无'}"
            + (f"；实际晋级但当时不在前二：{', '.join(row.missed)}" if row.missed else "")
        )
    lines.append("回看含义：同一赛程阶段的概率排序与最终结果可以不一致，当前推算应按区间使用。")
    return lines


def _section_decisions(view: SeasonView, gaps) -> list[str]:
    lines = ["十、待赛事组确认的决定（附受影响队伍与追溯来源）"]
    n = 0
    for g in view.pending_games:
        n += 1
        opp = [t for t in (g.a, g.b) if t not in view.withdrawn]
        lines.append(
            f"{n}. 场次 {g.id}（{g.a}-{g.b}）处置：确认退赛或行政判分给 "
            f"{', '.join(opp)}；影响 {', '.join(opp)} 及同区名额池。"
            f"（来源：退赛裁决单、对局记录 {g.id}）"
        )
    for g in view.withdrawal_games:
        if not g.locked:
            n += 1
            lines.append(
                f"{n}. 退赛场次 {g.id}（{g.a}-{g.b}）尚未锁定，需确认维持退赛或改判；"
                f"影响 {g.a}、{g.b} 及同区名额池。（来源：对局记录 {g.id}）"
            )
    for r in view.appeals:
        n += 1
        lines.append(
            f"{n}. 申诉 {r.id}（{r.team}）：维持原判 / 取消判分 / 改判重赛三者择一；"
            "改判重赛需以取消其它待赛场次为代价（见第八节）。（来源：裁决单 "
            f"{r.id}）"
        )
    if gaps:
        n += 1
        by_pool: dict[str, list] = {}
        for g in gaps:
            by_pool.setdefault(g.slot_desc, []).append(g)
        detail = "；".join(
            f"{desc} 超限 {len(gs)} 对（最大 {gs[0].team_hi} 高于 {gs[0].team_lo} {_pct1(gs[0].gap)}）"
            for desc, gs in by_pool.items()
        )
        lines.append(
            f"{n}. 机会差超出章程上限 {_pct(view.rules.max_opp_gap)}：{detail}。"
            "需确认接受差距，或选择第七、八节中的处置方案。"
        )
    locked = sorted(view.locked_promoted)
    if locked:
        lines.append(
            f"已确定名额（可追溯）：{', '.join(locked)} ← 裁决单 "
            + ", ".join(r.id for r in view.rulings if r.operation == "promoted_locked")
        )
    waiting = sorted(
        {g.a for g in view.pending_games} | {g.b for g in view.pending_games}
        | {g.a for g in view.withdrawal_games if not g.locked}
        | {g.b for g in view.withdrawal_games if not g.locked}
        | {r.team for r in view.appeals}
    )
    waiting = [t for t in waiting if t not in view.withdrawn]
    if waiting:
        lines.append(f"等待裁决的候选（可追溯）：{', '.join(waiting)} ← 上述待确认决定")
    return lines


# ---------------------------------------------------------------- 入口

def build_report(records: Records, season: int) -> str:
    view = SeasonView.from_records(records, season)
    lines: list[str] = [
        f"晋级机会与补赛决策报告（赛季 {season}，规则 {view.rules.id}）",
        "口径：积分可复核已公布成绩；名额推算为区间结论，非确定排名。",
        "",
        "一、资料核对",
        f"队伍 {len(view.teams)} 支；regional 对局 {len(view.all_regional_games())} 场；"
        f"catalog 校验通过（载入时已逐表核对 sha256 与行数）。",
        "",
    ]
    dist0 = chance.qualification_distribution(view, label="H0")
    gaps = chance.opportunity_gaps(view, dist0)
    # 反事实：判分/退赛/待裁场次全部还原为正常对局，隔离裁决结构的贡献
    ruling_games = [
        g
        for g in view.all_regional_games()
        if g.status in ("administrative", "withdrawal") or g in view.pending_games
    ]
    ruling_ids = frozenset(g.id for g in ruling_games)
    cf_dist = chance.qualification_distribution(
        view,
        label="反事实",
        extra_open=ruling_games,
        void_ids=ruling_ids,
    )
    gaps_cf = chance.opportunity_gaps(view, cf_dist)

    lines += _section_standings(view) + [""]
    lines += _section_result_types(view) + [""]
    lines += _section_chances(view, dist0, gaps, gaps_cf) + [""]
    dists = {"H0": dist0}
    for key, desc, probs in chance.hypothesis_set(view):
        if key == "H0":
            continue
        dists[key] = chance.qualification_distribution(view, label=key, win_prob=probs)
    lines += _section_hypotheses(view, dists) + [""]
    lines += _section_shared_slots(view, records) + [""]
    lines += _section_makeup(view, dist0) + [""]
    lines += _section_pending(view, dist0) + [""]
    lines += _section_lookback(records, season) + [""]
    lines += _section_decisions(view, gaps)
    return "\n".join(lines)


def standings_text(records: Records, season: int) -> str:
    """积分复核：按规则重算某季积分与名额，供核对已公布成绩。"""
    view = SeasonView.from_records(records, season)
    lines = [f"赛季 {season} 积分复核（规则 {view.rules.id}）", ""]
    lines += _section_standings(view)
    games = [g for g in view.all_regional_games() if g.status in ("played", "administrative")]
    branches = qualifier_branches(view, games)
    branches.sort(key=lambda b: -b[0])
    p, rank_set, shared_set = branches[0]
    lines += [
        "",
        f"按已确定结果重算：赛区名额 {', '.join(sorted(rank_set)) or '（未定）'}；"
        f"共享名额 {', '.join(sorted(shared_set)) or '（未定）'}",
    ]
    if len(branches) > 1 or p < 1.0:
        lines.append("存在需抽签的同分情形，上表为概率最大的分支；抽签结果以官方记录为准。")
    return "\n".join(lines)


def event_text(
    records: Records,
    season: int,
    team: str | None = None,
    game_id: str | None = None,
) -> str:
    """录入下一次退赛或延期后的影响预览：需要确认的决定与受影响队伍。"""
    view = SeasonView.from_records(records, season)
    dist0 = chance.qualification_distribution(view, label="基线")
    lines: list[str] = []
    if team:
        if team not in view.teams:
            raise KeyError(f"未知队伍 {team}")
        v2 = remedy.apply_withdrawal(view, team)
        newly = [g for g in v2.pending_games if g not in view.pending_games]
        dist2 = chance.qualification_distribution(v2, label="退赛假设")
        lines.append(f"假设 {team} 退赛（what-if，不写入记录）：")
        lines.append(
            "新待裁场次：" + (", ".join(g.id for g in newly) or "无")
        )
        aff = remedy.affected_teams(dist0, dist2)
        lines.append(
            "受影响队伍（晋级概率变化）："
            + (", ".join(f"{t} {_signed(d)}" for t, d in aff) or "无明显变化")
        )
        gaps2 = chance.opportunity_gaps(v2, dist2)
        lines.append(
            f"该假设下最大机会差 {_pct1(gaps2[0].gap) if gaps2 else 0.0}"
            f"（上限 {_pct(view.rules.max_opp_gap)}）"
        )
        lines.append("需要确认的决定：")
        for g in newly:
            opp = [t for t in (g.a, g.b) if t not in v2.withdrawn]
            lines.append(
                f"  - {g.id}（{g.a}-{g.b}）：确认退赛，或行政判分给 {', '.join(opp)}；"
                "补赛不可行（退赛队伍无法出场）。"
            )
    if game_id:
        g = next((x for x in view.all_regional_games() if x.id == game_id), None)
        if g is None:
            raise KeyError(f"未知对局 {game_id}")
        if g.status in ("played", "administrative"):
            lines.append(f"对局 {game_id} 已产生结果（{g.status}），无需延期安排。")
            return "\n".join(lines)
        lines.append(f"假设对局 {game_id}（{g.a}-{g.b}）延期（what-if，不写入记录）：")
        open_ids = [x.id for x in view.open_games if x.id != game_id]
        plans = remedy.makeup_plans(view, open_ids + [game_id])
        usable = sorted(
            {row.id for p in plans if game_id in p.assignments for row in [p.assignments[game_id]]}
        )
        if usable:
            slots = {s.id: s for s in view.court_slots}
            lines.append(
                "可补入的场地槽："
                + ", ".join(f"{sid}(slot {slots[sid].slot} @{slots[sid].arena})" for sid in usable)
            )
        else:
            lines.append("补赛窗口无可行槽位（受休息保护/场地惯例/已锁定对阵限制）。")
        v_void = remedy.apply_void(view, [game_id])
        dist_void = chance.qualification_distribution(v_void, label="取消该场")
        aff = remedy.affected_teams(dist0, dist_void)
        lines.append(
            "若该场最终取消，受影响队伍："
            + (", ".join(f"{t} {_signed(d)}" for t, d in aff) or "无明显变化")
        )
    return "\n".join(lines)
