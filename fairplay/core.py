"""积分、同分规则与名额分配。

口径约定（均可在 rules / games 记录中复核）：
- 只有 regional 阶段对局计入晋级积分；bridge 对局只作跨赛区实力证据
  （本赛季跨赛区对局尚未排期，往届 bridge 见 lookback / chance 模块）。
- played 结果给 win_points 且计入实力证据；administrative 给 admin_points、
  不计入实力证据；withdrawal 按规则 "no_points_pending_ruling" 不给分、待裁。
- 同分规则按 tie_order 依次适用；official_draw 表示抽签，本工具把抽签
  展开为等概率分支，绝不把一次模拟结果当成确定排名。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import combinations

from .records import CourtSlot, Game, Records, Rules, Ruling, Team

#: 已确定积分的结果类型（待赛 scheduled 与退赛 withdrawal 不产生积分）
DECIDED = ("played", "administrative")


@dataclass
class SeasonView:
    """一个赛季在某个时点的可分析状态。

    decided          已确定积分的对局（played / administrative）
    open_games       待赛且双方均未退赛的对局（推算时枚举其结果）
    pending_games    涉及退赛队伍、等待裁决的 scheduled 对局
    withdrawal_games 已记为退赛的对局（按规则不给分、待裁）
    locked_promoted  裁决单已锁定资格的队伍（promoted_locked）
    withdrawn        已登记退赛的队伍
    appeals          待决的申诉类裁决单
    """

    season: int
    teams: dict[str, Team]
    rules: Rules
    rulings: list[Ruling]
    court_slots: list[CourtSlot]
    decided: list[Game]
    open_games: list[Game]
    pending_games: list[Game]
    withdrawal_games: list[Game]
    locked_promoted: frozenset[str]
    withdrawn: frozenset[str]
    appeals: list[Ruling]

    @classmethod
    def from_records(cls, records: Records, season: int) -> "SeasonView":
        """按赛季构造视图。裁决单未记赛季号，约定归属最新赛季。"""
        teams = {t.id: t for t in records.teams}
        rules = records.rules_for(season)
        rulings = list(records.rulings) if season == max(records.seasons) else []
        games = [g for g in records.games_of(season) if g.stage == "regional"]
        withdrawn = frozenset(r.team for r in rulings if r.operation == "withdraw")
        locked = frozenset(r.team for r in rulings if r.operation == "promoted_locked")
        appeals = [r for r in rulings if r.operation == "appeal_admin_points"]
        scheduled = [g for g in games if g.status == "scheduled"]
        pending = [g for g in scheduled if g.a in withdrawn or g.b in withdrawn]
        pending_ids = {g.id for g in pending}
        return cls(
            season=season,
            teams=teams,
            rules=rules,
            rulings=rulings,
            court_slots=list(records.slots),
            decided=[g for g in games if g.status in DECIDED],
            open_games=[g for g in scheduled if g.id not in pending_ids],
            pending_games=pending,
            withdrawal_games=[g for g in games if g.status == "withdrawal"],
            locked_promoted=locked,
            withdrawn=withdrawn,
            appeals=appeals,
        )

    @property
    def areas(self) -> list[str]:
        return sorted({t.area for t in self.teams.values()})

    def area_teams(self, area: str) -> list[str]:
        return sorted(t.id for t in self.teams.values() if t.area == area)

    def all_regional_games(self) -> list[Game]:
        return self.decided + self.open_games + self.pending_games + self.withdrawal_games


def resolve_open_games(open_games: list[Game], outcomes: dict[str, str]) -> list[Game]:
    """把待赛对局按给定结果落定为 played，保留原 id 以便追溯。"""
    resolved = []
    for g in open_games:
        winner = outcomes[g.id]
        if winner not in (g.a, g.b):
            raise ValueError(f"{g.id} 的结果 {winner} 不是参赛队伍")
        resolved.append(replace(g, status="played", winner=winner, locked=True))
    return resolved


def season_points(games: list[Game], rules: Rules, team_ids) -> tuple[dict, dict]:
    """按规则重算积分与"已开打胜场"。

    played 胜场给 win_points 并计入 played_wins；administrative 给
    admin_points 但不计 played_wins（同分时 played_wins 靠后于相互战绩）。
    withdrawal / scheduled 不产生积分。
    """
    points = {t: 0 for t in team_ids}
    played_wins = {t: 0 for t in team_ids}
    for g in games:
        if g.winner is None:
            continue
        if g.status == "played":
            points[g.winner] += rules.win_points
            played_wins[g.winner] += 1
        elif g.status == "administrative":
            points[g.winner] += rules.admin_points
    return points, played_wins


def _h2h_points(games: list[Game], rules: Rules, group: list[str]) -> dict | None:
    """同分队伍之间相互对局的积分（played 与 administrative 都算结果）。

    组内两两未全部交手（如跨赛区同分组没有相互对局）时返回 None：
    缺少可比相互战绩，不能把"没有对局"算成"相互战绩差"，
    该条同分规则应跳过，落到下一条。
    """
    members = set(group)
    covered = set()
    h2h = {t: 0 for t in group}
    for g in games:
        if g.status not in DECIDED or g.a not in members or g.b not in members:
            continue
        covered.add(tuple(sorted((g.a, g.b))))
        if g.winner in members:
            value = rules.win_points if g.status == "played" else rules.admin_points
            h2h[g.winner] += value
    if len(covered) < len(group) * (len(group) - 1) // 2:
        return None
    return h2h


def _split(teams: list[str], key) -> list[list[str]]:
    """按 key 降序分组，同值同组；组内按 id 排序保证确定性。"""
    order = sorted(teams, key=lambda t: (-key(t), t))
    groups: list[list[str]] = []
    for t in order:
        if groups and key(groups[-1][0]) == key(t):
            groups[-1].append(t)
        else:
            groups.append([t])
    return groups


def rank_groups(
    teams: list[str],
    games: list[Game],
    rules: Rules,
    points: dict,
    played_wins: dict,
    score_override: dict | None = None,
) -> list[list[str]]:
    """按 tie_order 把队伍排成有序组；仍并列的留在同组（抽签组）。

    score_override 用于共享名额的跨赛区比较（积分加赛区修正）。
    head_to_head 只统计同组队伍之间的对局；跨赛区同分组没有相互对局，
    自然落到下一条规则。
    """
    score = score_override if score_override is not None else points
    groups = _split(teams, key=lambda t: score[t])
    for step in rules.tie_order:
        if step == "official_draw":
            break
        refined: list[list[str]] = []
        for g in groups:
            if len(g) == 1:
                refined.append(g)
                continue
            if step == "head_to_head":
                h2h = _h2h_points(games, rules, g)
                if h2h is None:
                    refined.append(g)
                else:
                    refined.extend(_split(g, key=lambda t: h2h[t]))
            elif step == "played_wins":
                refined.extend(_split(g, key=lambda t: played_wins[t]))
            else:
                raise ValueError(f"未实现的同分规则 {step}")
        groups = refined
    return groups


def allocate_branch(groups: list[list[str]], k: int) -> list[tuple[float, set, set]]:
    """把 k 个名额分配给有序组，抽签组展开为等概率分支。

    返回 [(概率, 获得名额集合, 未获得集合)]，概率和为 1。
    """
    chosen: set = set()
    rest: set = set()
    left = k
    for i, g in enumerate(groups):
        if left == 0:
            rest |= set(g)
        elif len(g) <= left:
            chosen |= set(g)
            left -= len(g)
        else:
            tail: set = set().union(*groups[i + 1 :]) if i + 1 < len(groups) else set()
            combos = list(combinations(sorted(g), left))
            p = 1.0 / len(combos)
            return [
                (p, chosen | set(c), rest | (set(g) - set(c)) | tail)
                for c in combos
            ]
    return [(1.0, chosen, rest)]


def qualifier_branches(
    view: SeasonView,
    games: list[Game],
    area_offset: dict[str, float] | None = None,
) -> list[tuple[float, set, set]]:
    """沿真实赛制展开晋级分支。

    返回 [(概率, 赛区名额晋级集合, 共享名额晋级集合)]。
    - 锁定资格（promoted_locked）优先占用本赛区名额，可追溯裁决单；
    - 退赛队伍不参与名额分配，其已赛结果仍计入对手积分；
    - 共享名额在所有未晋级且未退赛队伍间按 积分+赛区修正 比较，
      修正值 area_offset 是显式的强度解释参数。
    """
    rules = view.rules
    team_ids = sorted(view.teams)
    points, played_wins = season_points(games, rules, team_ids)
    offset = area_offset or {}
    branches: list[tuple[float, set, set]] = [(1.0, set(), set())]
    for area in view.areas:
        contenders = [t for t in view.area_teams(area) if t not in view.withdrawn]
        locked = {t for t in contenders if t in view.locked_promoted}
        rest = [t for t in contenders if t not in locked]
        k = max(0, rules.rank_slots.get(area, 0) - len(locked))
        groups = rank_groups(rest, games, rules, points, played_wins) if k else []
        nxt = []
        for p, qual, pool in branches:
            for q, ch, miss in allocate_branch(groups, k):
                nxt.append((p * q, qual | locked | ch, pool | miss))
        branches = nxt
    score = {t: points[t] + offset.get(view.teams[t].area, 0.0) for t in team_ids}
    out: list[tuple[float, set, set]] = []
    for p, qual, pool in branches:
        shared_pool = sorted(t for t in pool if t not in view.withdrawn)
        groups = rank_groups(
            shared_pool, games, rules, points, played_wins, score_override=score
        )
        for q, ch, _miss in allocate_branch(groups, rules.shared_slots):
            out.append((p * q, qual, ch))
    return out
