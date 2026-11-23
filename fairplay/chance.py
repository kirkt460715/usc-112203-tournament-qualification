"""晋级机会推算：精确枚举 + 多种强度解释 + 机会差检查。

推算原则：
- 待赛对局逐一枚举（默认每场 2 种结果），概率按解释假设加权，
  结果是精确值而非一次模拟；抽签同样展开为等概率分支。
- 跨赛区缺少可比对局时，不选定单一强度结论：H0–H3 各给出一个
  说得通的解释，报告每队在不同解释下的概率区间。
- 机会差（章程 max_opp_gap）用 H0 机会基数衡量：它只反映赛程、
  判分与退赛处置带来的结构差异，不掺入实力假设。
"""
from __future__ import annotations

from dataclasses import dataclass

from .core import SeasonView, qualifier_branches, resolve_open_games
from .records import Game

#: 精确枚举的待赛对局上限（2**24 条路径以内）
MAX_OPEN_GAMES = 24


@dataclass
class Distribution:
    """一套假设下的晋级概率。rank/shared/total 均为 {队: 概率}。"""

    label: str
    rank: dict[str, float]
    shared: dict[str, float]
    total: dict[str, float]
    paths: int


def _accumulate(view, games, area_offset, acc, weight):
    for p, rank_set, shared_set in qualifier_branches(view, games, area_offset):
        w = weight * p
        for t in rank_set:
            acc["rank"][t] += w
        for t in shared_set:
            acc["shared"][t] += w
        for t in rank_set | shared_set:
            acc["total"][t] += w


def qualification_distribution(
    view: SeasonView,
    label: str,
    win_prob: dict[str, float] | None = None,
    area_offset: dict[str, float] | None = None,
    extra_open: list[Game] | None = None,
    void_ids: frozenset[str] = frozenset(),
) -> Distribution:
    """精确枚举待赛对局，返回每队晋级概率拆分。

    win_prob    {game_id: P(a 队胜)}，缺省 0.5（机会基数口径）。
    area_offset 共享名额比较时各赛区积分的修正（强度解释参数）。
    extra_open  额外视为待赛的对局（如把退赛场次改判为补赛的情景）。
    void_ids    视为取消、不产生积分的对局 id。
    """
    open_games = [g for g in view.open_games if g.id not in void_ids]
    open_games += list(extra_open or [])
    if len(open_games) > MAX_OPEN_GAMES:
        raise ValueError(f"待赛对局 {len(open_games)} 场超出精确枚举上限")
    decided = [g for g in view.decided if g.id not in void_ids]
    win_prob = win_prob or {}
    acc = {
        "rank": {t: 0.0 for t in view.teams},
        "shared": {t: 0.0 for t in view.teams},
        "total": {t: 0.0 for t in view.teams},
    }
    outcomes: dict[str, str] = {}
    paths = 0

    def walk(i: int, weight: float) -> None:
        nonlocal paths
        if i == len(open_games):
            paths += 1
            games = decided + resolve_open_games(open_games, outcomes)
            _accumulate(view, games, area_offset, acc, weight)
            return
        g = open_games[i]
        pa = win_prob.get(g.id, 0.5)
        for winner, w in ((g.a, pa), (g.b, 1.0 - pa)):
            if w == 0.0:
                continue
            outcomes[g.id] = winner
            walk(i + 1, weight * w)
        outcomes.pop(g.id, None)

    walk(0, 1.0)
    clamp = lambda x: min(1.0, max(0.0, x))  # 吸收逐路径累加的浮点误差
    return Distribution(
        label=label,
        rank={t: clamp(p) for t, p in acc["rank"].items()},
        shared={t: clamp(p) for t, p in acc["shared"].items()},
        total={t: clamp(p) for t, p in acc["total"].items()},
        paths=paths,
    )


def win_probs_form(view: SeasonView) -> dict[str, float]:
    """H1：区内已开打战绩外推（仅用 played，判分/退赛不作实力证据）。

    拉普拉斯平滑 s=(胜+1)/(场+2)，p(a胜)=s_a/(s_a+s_b)。
    """
    wins = {t: 0 for t in view.teams}
    played = {t: 0 for t in view.teams}
    for g in view.decided:
        if g.status != "played":
            continue
        loser = g.opponent(g.winner)
        wins[g.winner] += 1
        played[g.winner] += 1
        played[loser] += 1
    s = {t: (wins[t] + 1.0) / (played[t] + 2.0) for t in view.teams}
    return {g.id: s[g.a] / (s[g.a] + s[g.b]) for g in view.open_games}


def win_probs_seed(view: SeasonView) -> dict[str, float]:
    """H3：种子先验，强度与种子序号成反比。"""
    n = max(t.seed for t in view.teams.values()) + 1
    strength = {t.id: n - t.seed for t in view.teams.values()}
    return {
        g.id: strength[g.a] / (strength[g.a] + strength[g.b])
        for g in view.open_games
    }


def bridge_record(views: list[SeasonView], all_games: list[Game]) -> dict:
    """往届 bridge 对局的跨赛区战绩（仅有的跨赛区可比对局）。"""
    record: dict[str, dict[str, int]] = {}
    rosters: dict[str, dict[str, int]] = {}
    for g in all_games:
        if g.stage != "bridge" or g.status != "played":
            continue
        for view in views:
            if g.season != view.season:
                continue
            area_a = view.teams[g.a].area
            area_b = view.teams[g.b].area
            rec = record.setdefault(g.season, {})
            rec[view.teams[g.winner].area] = rec.get(view.teams[g.winner].area, 0) + 1
            rec.setdefault(area_a, rec.get(area_a, 0))
            rec.setdefault(area_b, rec.get(area_b, 0))
            ros = rosters.setdefault(g.season, {})
            for team, roster in ((g.a, g.a_roster), (g.b, g.b_roster)):
                kind = "bench" if roster == view.teams[team].backup else "first"
                key = f"{view.teams[team].area}_{kind}"
                ros[key] = ros.get(key, 0) + 1
    return {"record": record, "rosters": rosters}


#: 共享名额的赛区强度修正情景（积分单位，一场胜场=win_points）。
#: 跨赛区可比对局不足时，结论按该区间分别列出，不取单一值。
def offset_scenarios(win_points: int, areas: list[str]) -> list[tuple[str, dict[str, float]]]:
    """以排序后的第二个赛区为参照对象给出正负修正。"""
    ref = areas[-1] if areas else "S"
    name = {"N": "北", "S": "南"}.get(ref, ref)
    return [
        ("均衡（不修正）", {}),
        (f"桥接点估计（{name}区 +半场）", {ref: win_points / 2.0}),
        (f"{name}区优势边界（+一场）", {ref: float(win_points)}),
        (f"{name}区劣势边界（-一场）", {ref: -float(win_points)}),
    ]


def hypothesis_set(view: SeasonView) -> list[tuple[str, str, dict | None]]:
    """说得通的强度解释集合：(代号, 说明, win_prob)。"""
    return [
        ("H0", "机会基数：全部对局五五开，只反映赛程与裁决结构", None),
        ("H1", "区内战绩外推：仅用已开打对局估计胜率", win_probs_form(view)),
        ("H3", "种子先验：强度与种子序号成反比", win_probs_seed(view)),
    ]


@dataclass
class GapRow:
    pool: str
    slot_desc: str
    team_hi: str
    team_lo: str
    gap: float


def opportunity_gaps(view: SeasonView, dist: Distribution) -> list[GapRow]:
    """章程机会差检查：同一名额池内两两比较，超出 max_opp_gap 的列出。"""
    rows: list[GapRow] = []
    pools = []
    for area in view.areas:
        locked = [
            t
            for t in view.area_teams(area)
            if t in view.locked_promoted and t not in view.withdrawn
        ]
        teams = [
            t
            for t in view.area_teams(area)
            if t not in view.withdrawn and t not in view.locked_promoted
        ]
        total_slots = view.rules.rank_slots.get(area, 0)
        desc = f"{area}区名额（{total_slots} 个"
        if locked:
            desc += f"，{','.join(locked)} 已锁定占用 {len(locked)}，剩余 {total_slots - len(locked)}"
        desc += "）"
        pools.append((f"{area}区名额池", desc, teams, dist.total))
    shared_pool = [
        t
        for t in sorted(view.teams)
        if t not in view.withdrawn and t not in view.locked_promoted
    ]
    pools.append(("共享名额池", f"跨区共享名额（{view.rules.shared_slots} 个）", shared_pool, dist.shared))
    for pool_name, slot_desc, teams, probs in pools:
        for i in range(len(teams)):
            for j in range(i + 1, len(teams)):
                a, b = teams[i], teams[j]
                gap = probs[a] - probs[b]
                if abs(gap) > view.rules.max_opp_gap + 1e-9:
                    hi, lo = (a, b) if gap > 0 else (b, a)
                    rows.append(
                        GapRow(
                            pool=pool_name,
                            slot_desc=slot_desc,
                            team_hi=hi,
                            team_lo=lo,
                            gap=abs(gap),
                        )
                    )
    rows.sort(key=lambda r: (-r.gap, r.team_hi, r.team_lo))
    return rows


def team_evidence(view: SeasonView, team: str) -> dict:
    """每队的机会来源证据：积分构成、对手阵容、退赛影响，全部可追溯。"""
    info = view.teams[team]
    rows = []
    for g in view.decided:
        if not g.involves(team):
            continue
        opp = g.opponent(team)
        opp_roster = g.roster_of(opp)
        rows.append(
            {
                "game": g.id,
                "status": g.status,
                "result": "胜" if g.winner == team else "负",
                "points": (
                    (view.rules.win_points if g.status == "played" else view.rules.admin_points)
                    if g.winner == team
                    else 0
                ),
                "opponent": opp,
                "opponent_seed": view.teams[opp].seed,
                "opponent_roster": "替补" if opp_roster == view.teams[opp].backup else "正选",
            }
        )
    wins_first = sum(1 for r in rows if r["result"] == "胜" and r["status"] == "played" and r["opponent_roster"] == "正选")
    wins_bench = sum(1 for r in rows if r["result"] == "胜" and r["status"] == "played" and r["opponent_roster"] == "替补")
    admin_wins = sum(1 for r in rows if r["result"] == "胜" and r["status"] == "administrative")
    return {
        "team": team,
        "area": info.area,
        "seed": info.seed,
        "games": rows,
        "points": sum(r["points"] for r in rows),
        "wins_vs_first": wins_first,
        "wins_vs_bench": wins_bench,
        "admin_wins": admin_wins,
        "remaining": [g.id for g in view.open_games if g.involves(team)],
        "pending": [g.id for g in view.pending_games if g.involves(team)],
        "withdrawal": [g.id for g in view.withdrawal_games if g.involves(team)],
    }
