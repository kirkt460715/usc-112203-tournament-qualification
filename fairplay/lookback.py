"""往届同一赛程阶段回看：用已完成赛季校准当前推算的可信度。

做法：取往届赛季，按当前赛季各赛区的已赛进度，把该阶段之后的对局
结果隐去（视为待赛、五五开枚举），重算当时的晋级概率，再与该队
最终实际晋级结果对照。这给出的是"同一赛程阶段、同样可用信息"
下的回看，而不是事后结论。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .chance import qualification_distribution
from .core import DECIDED, SeasonView, qualifier_branches
from .records import Records


@dataclass
class LookbackRow:
    season: int
    area: str
    decided_games: int
    total_games: int
    projected: list[tuple[str, float]]  # 当时推算的晋级概率（降序前若干）
    actual: list[str]                   # 最终实际晋级（赛区名额）
    hit: list[str]                      # 当时概率前二且实际晋级的队
    missed: list[str]                   # 实际晋级但当时概率不在前二的队


def _decided_counts(view: SeasonView) -> dict[str, tuple[int, int]]:
    counts = {}
    for area in view.areas:
        teams = set(view.area_teams(area))
        games = [
            g
            for g in view.all_regional_games()
            if g.a in teams and g.b in teams and g.status != "withdrawal"
        ]
        done = [g for g in games if g.status in DECIDED]
        counts[area] = (len(done), len(games))
    return counts


def _actual_qualifiers(view: SeasonView) -> list[str]:
    """用完整结果按规则重算的实际晋级（赛区名额部分）。"""
    games = [g for g in view.all_regional_games() if g.status in DECIDED]
    branches = qualifier_branches(view, games)
    branches.sort(key=lambda b: -b[0])
    return sorted(branches[0][1])


def lookback(records: Records, current_season: int) -> list[LookbackRow]:
    """对每个往届赛季、每个赛区，在当前进度下重算并比对实际结果。"""
    current = SeasonView.from_records(records, current_season)
    progress = _decided_counts(current)
    rows: list[LookbackRow] = []
    for season in records.seasons:
        if season == current_season:
            continue
        full = SeasonView.from_records(records, season)
        actual = _actual_qualifiers(full)
        for area in full.areas:
            done_target, total = progress[area]
            teams = set(full.area_teams(area))
            area_games = sorted(
                (
                    g
                    for g in full.all_regional_games()
                    if g.a in teams and g.b in teams
                ),
                key=lambda g: g.slot,
            )
            hide = {g.id for g in area_games[done_target:]}
            truncated = SeasonView(
                season=full.season,
                teams=full.teams,
                rules=full.rules,
                rulings=[],
                court_slots=full.court_slots,
                decided=[g for g in full.decided if g.id not in hide],
                open_games=[
                    replace(g, status="scheduled", winner=None, locked=False)
                    for g in full.decided
                    if g.id in hide
                ],
                pending_games=[],
                withdrawal_games=[],
                locked_promoted=frozenset(),
                withdrawn=frozenset(),
                appeals=[],
            )
            dist = qualification_distribution(truncated, label="回看")
            area_rank = sorted(
                ((t, dist.total[t]) for t in full.area_teams(area)),
                key=lambda kv: (-kv[1], kv[0]),
            )
            top2 = {t for t, _ in area_rank[: full.rules.rank_slots[area]]}
            actual_area = [t for t in actual if t in teams]
            rows.append(
                LookbackRow(
                    season=season,
                    area=area,
                    decided_games=done_target,
                    total_games=total,
                    projected=area_rank[:4],
                    actual=sorted(actual_area),
                    hit=sorted(top2 & set(actual_area)),
                    missed=sorted(set(actual_area) - top2),
                )
            )
    return rows
