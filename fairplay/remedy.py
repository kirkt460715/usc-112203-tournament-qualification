"""退赛与延期的处置：补赛可行性、判分方案与影响评估。

约束（来自 rules / slots 记录）：
- 补赛只能放进 slots.jsonl 里未被占用的场地时间槽；
- 同一场地同一 slot 只能安排一局；
- 休息按 slot 差值衡量，新安排与队伍其它场次不得小于 min_rest_slots；
- 已锁定（locked）的对阵与结果不可改动；
- 场地惯例由历史记录归纳：北区 roomA、南区 roomB（可关闭）。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import combinations

from .core import SeasonView
from .records import CourtSlot, Game

#: 历史记录中北区对局全在 roomA、南区全在 roomB，归纳为场地惯例
AREA_ARENA = {"N": "roomA", "S": "roomB"}


@dataclass
class MakeupPlan:
    """一套补赛安排：assignments 为 对局id -> 场地槽，dropped 为排不下而取消的对局。"""

    assignments: dict[str, CourtSlot]
    dropped: tuple[str, ...] = ()
    alternatives: int = 0  # 同一放弃集合下还存在多少其它排法


def _fixed_slots(view: SeasonView, moving_ids: set[str]) -> dict[str, list[int]]:
    """各队不可移动的场次槽位（已确定 + 不搬动的待赛；待裁/退赛不占槽）。"""
    slots = {t: [] for t in view.teams}
    for g in view.decided + view.open_games:
        if g.id in moving_ids:
            continue
        slots[g.a].append(g.slot)
        slots[g.b].append(g.slot)
    return slots


def feasible_rows(
    view: SeasonView,
    game: Game,
    placed: dict[str, CourtSlot],
    fixed: dict[str, list[int]],
    use_arena_convention: bool = True,
) -> list[CourtSlot]:
    """单场补赛的可行场地槽：未被占用、符合场地惯例、满足休息保护。"""
    area = view.teams[game.a].area
    taken = {row.id for row in placed.values()}
    placed_by_team: dict[str, list[int]] = {t: [] for t in view.teams}
    for gid, row in placed.items():
        g = next(x for x in view.all_regional_games() if x.id == gid)
        placed_by_team[g.a].append(row.slot)
        placed_by_team[g.b].append(row.slot)
    ok = []
    for row in view.court_slots:
        if row.blocked or row.id in taken:
            continue
        if use_arena_convention and row.arena != AREA_ARENA.get(area):
            continue
        rest_ok = True
        for team in (game.a, game.b):
            for s in fixed.get(team, []) + placed_by_team.get(team, []):
                if abs(s - row.slot) < view.rules.min_rest_slots:
                    rest_ok = False
                    break
            if not rest_ok:
                break
        if rest_ok:
            ok.append(row)
    return ok


def _search(
    view: SeasonView,
    games: list[Game],
    fixed: dict[str, list[int]],
    use_arena_convention: bool,
    cap: int,
) -> list[dict[str, CourtSlot]]:
    """回溯搜索全部可行排法（封顶 cap 个）。"""
    solutions: list[dict[str, CourtSlot]] = []

    def walk(i: int, placed: dict[str, CourtSlot]) -> None:
        if len(solutions) >= cap:
            return
        if i == len(games):
            solutions.append(dict(placed))
            return
        g = games[i]
        for row in feasible_rows(view, g, placed, fixed, use_arena_convention):
            placed[g.id] = row
            walk(i + 1, placed)
            placed.pop(g.id)

    walk(0, {})
    return solutions


def makeup_plans(
    view: SeasonView,
    game_ids: list[str] | None = None,
    use_arena_convention: bool = True,
    cap: int = 200,
) -> list[MakeupPlan]:
    """为待赛对局寻找补赛排法；排不下时枚举最小放弃集合的各方案。"""
    game_ids = game_ids if game_ids is not None else [g.id for g in view.open_games]
    by_id = {g.id: g for g in view.all_regional_games()}
    games = [by_id[i] for i in game_ids]
    plans: list[MakeupPlan] = []
    for drop_k in range(0, len(games) + 1):
        found: list[MakeupPlan] = []
        for dropped in combinations(game_ids, drop_k):
            keep = [g for g in games if g.id not in dropped]
            fixed = _fixed_slots(view, set(game_ids))
            sols = _search(view, keep, fixed, use_arena_convention, cap)
            for sol in sols:
                found.append(MakeupPlan(assignments=sol, dropped=tuple(sorted(dropped))))
        if found:
            by_drop: dict[tuple, list[MakeupPlan]] = {}
            for p in found:
                by_drop.setdefault(p.dropped, []).append(p)
            for dropped, group in sorted(by_drop.items()):
                first = group[0]
                first.alternatives = len(group) - 1
                plans.append(first)
            return plans
    return plans


@dataclass
class RemedyOption:
    """一种退赛/延期处置方案及其影响评估。"""

    key: str
    title: str
    feasible: bool
    notes: list[str] = field(default_factory=list)
    distribution: object = None  # chance.Distribution，可行时填写
    max_gap: float = 0.0
    affected: list[tuple[str, float]] = field(default_factory=list)  # (队, 概率变化)


def apply_withdrawal(view: SeasonView, team: str) -> SeasonView:
    """假设某队退赛：其待赛对局转为待裁，队伍退出名额分配。"""
    if team in view.withdrawn:
        return view
    withdrawn = frozenset(set(view.withdrawn) | {team})
    scheduled = list(view.open_games)
    pending = list(view.pending_games)
    still_open = []
    for g in scheduled:
        if g.involves(team):
            pending.append(g)
        else:
            still_open.append(g)
    return SeasonView(
        season=view.season,
        teams=view.teams,
        rules=view.rules,
        rulings=view.rulings,
        court_slots=view.court_slots,
        decided=view.decided,
        open_games=still_open,
        pending_games=pending,
        withdrawal_games=view.withdrawal_games,
        locked_promoted=view.locked_promoted,
        withdrawn=withdrawn,
        appeals=view.appeals,
    )


def apply_admin_points(view: SeasonView, game_ids: list[str]) -> SeasonView:
    """把待裁/退赛场次改判行政判分：对方得 admin_points。

    判分对象须是只有一方未退赛的对局；胜方记为未退赛一方。
    """
    by_id = {g.id: g for g in view.all_regional_games()}
    decided = list(view.decided)
    pending = list(view.pending_games)
    withdrawal = list(view.withdrawal_games)
    for gid in game_ids:
        g = by_id[gid]
        non_withdrawn = [t for t in (g.a, g.b) if t not in view.withdrawn]
        if len(non_withdrawn) != 1:
            raise ValueError(f"{gid} 无法判分：双方均未退赛或均已退赛")
        judged = replace(g, status="administrative", winner=non_withdrawn[0], locked=True)
        decided.append(judged)
        pending = [x for x in pending if x.id != gid]
        withdrawal = [x for x in withdrawal if x.id != gid]
    return SeasonView(
        season=view.season,
        teams=view.teams,
        rules=view.rules,
        rulings=view.rulings,
        court_slots=view.court_slots,
        decided=decided,
        open_games=view.open_games,
        pending_games=pending,
        withdrawal_games=withdrawal,
        locked_promoted=view.locked_promoted,
        withdrawn=view.withdrawn,
        appeals=view.appeals,
    )


def apply_void(view: SeasonView, game_ids: list[str]) -> SeasonView:
    """把对局记为取消（不给分、不再补赛），用于退赛场次确认或申诉改判。"""
    decided = [g for g in view.decided if g.id not in game_ids]
    open_games = [g for g in view.open_games if g.id not in game_ids]
    pending = [g for g in view.pending_games if g.id not in game_ids]
    withdrawal = list(view.withdrawal_games)
    known = {g.id for g in view.all_regional_games()}
    for gid in game_ids:
        if gid in known and all(g.id != gid for g in withdrawal):
            g = next(x for x in view.all_regional_games() if x.id == gid)
            withdrawal.append(replace(g, status="withdrawal", winner=None))
    return SeasonView(
        season=view.season,
        teams=view.teams,
        rules=view.rules,
        rulings=view.rulings,
        court_slots=view.court_slots,
        decided=decided,
        open_games=open_games,
        pending_games=pending,
        withdrawal_games=withdrawal,
        locked_promoted=view.locked_promoted,
        withdrawn=view.withdrawn,
        appeals=view.appeals,
    )


def affected_teams(before, after, threshold: float = 0.01) -> list[tuple[str, float]]:
    """两个分布之间晋级概率变化超过阈值的队伍，按变化幅度排序。"""
    rows = []
    for t in before.total:
        delta = after.total.get(t, 0.0) - before.total[t]
        if abs(delta) > threshold:
            rows.append((t, delta))
    rows.sort(key=lambda r: (-abs(r[1]), r[0]))
    return rows
