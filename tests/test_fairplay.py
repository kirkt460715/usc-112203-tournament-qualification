"""fairplay 工具的单元与集成测试。

单元测试用合成视图（不依赖 records 目录）；集成测试用真实发行资料，
覆盖积分口径、同分规则、名额分配、补赛可行性与往届回看。
"""
from __future__ import annotations

import unittest

from fairplay import chance, lookback, remedy, report
from fairplay.core import (
    SeasonView,
    allocate_branch,
    qualifier_branches,
    rank_groups,
    season_points,
)
from fairplay.records import Game, Records, Rules, Team, load_records


def make_team(tid, area, seed):
    return Team(id=tid, area=area, seed=seed, starter=f"{tid}-first", backup=f"{tid}-bench")


def make_game(gid, a, b, status="played", winner=None, slot=1, locked=True,
              stage="regional", season=2099, arena="roomA"):
    return Game(
        id=gid, season=season, stage=stage, a=a, b=b,
        a_roster=f"{a}-first", b_roster=f"{b}-first",
        slot=slot, known_slot=0, status=status, winner=winner,
        locked=locked, arena=arena,
    )


def make_rules(**kw):
    base = dict(
        id="t", season=2099, win_points=3, admin_points=3,
        withdrawal="no_points_pending_ruling",
        tie_order=("head_to_head", "played_wins", "official_draw"),
        rank_slots={"A": 1, "B": 1}, shared_slots=1,
        min_rest_slots=2, max_opp_gap=0.18, seed_protect=(1, 2),
    )
    base.update(kw)
    return Rules(**base)


def make_view(teams, games, rules=None, withdrawn=(), locked=(), court_slots=()):
    teams = {t.id: t for t in teams}
    withdrawn = frozenset(withdrawn)
    scheduled = [g for g in games if g.status == "scheduled"]
    pending = [g for g in scheduled if g.a in withdrawn or g.b in withdrawn]
    pending_ids = {g.id for g in pending}
    return SeasonView(
        season=2099,
        teams=teams,
        rules=rules or make_rules(),
        rulings=[],
        court_slots=list(court_slots),
        decided=[g for g in games if g.status in ("played", "administrative")],
        open_games=[g for g in scheduled if g.id not in pending_ids],
        pending_games=pending,
        withdrawal_games=[g for g in games if g.status == "withdrawal"],
        locked_promoted=frozenset(locked),
        withdrawn=withdrawn,
        appeals=[],
    )


class PointsTests(unittest.TestCase):
    def test_result_types_points(self):
        rules = make_rules()
        games = [
            make_game("g1", "X", "Y", status="played", winner="X"),
            make_game("g2", "X", "Z", status="administrative", winner="X"),
            make_game("g3", "X", "W", status="withdrawal"),
            make_game("g4", "X", "V", status="scheduled"),
        ]
        points, played_wins = season_points(games, rules, ["X", "Y", "Z", "W", "V"])
        # played 胜 3 分并计已开打胜场；判分胜 3 分不计；退赛/待赛 0 分
        self.assertEqual(points["X"], 6)
        self.assertEqual(played_wins["X"], 1)
        self.assertEqual(points["Y"], 0)

    def test_tiebreak_order(self):
        # 同分：相互战绩优先于已开打胜场
        rules = make_rules()
        games = [
            make_game("g1", "X", "Y", status="played", winner="X"),
            make_game("g2", "X", "Z", status="played", winner="Z"),
            make_game("g3", "Y", "Z", status="administrative", winner="Y"),
        ]
        points, played_wins = season_points(games, rules, ["X", "Y", "Z"])
        groups = rank_groups(["X", "Y", "Z"], games, rules, points, played_wins)
        # X/Y/Z 各 3 分；相互战绩各 3 分；played_wins：X=1,Z=1,Y=0 → Y 垫底，X/Z 并列
        self.assertEqual(groups[-1], ["Y"])
        self.assertEqual(set(groups[0]), {"X", "Z"})

    def test_h2h_skipped_when_not_all_pairs_met(self):
        # 跨赛区同分组缺少相互对局时跳过 head_to_head，落到下一条
        rules = make_rules()
        games = [
            make_game("g1", "X", "Y", status="played", winner="X"),
            make_game("g2", "X", "Z", status="played", winner="X"),
            make_game("g4", "W", "V", status="played", winner="W"),
            make_game("g5", "W", "U", status="administrative", winner="W"),
        ]
        points, played_wins = season_points(games, rules, ["X", "Y", "Z", "W", "U", "V"])
        groups = rank_groups(["W", "X"], games, rules, points, played_wins)
        # W 与 X 同 6 分、无相互对局：不按 h2h 拆，按 played_wins（X=2, W=1）
        self.assertEqual(groups, [["X"], ["W"]])

    def test_official_draw_splits_evenly(self):
        groups = [["X", "Y"]]
        branches = allocate_branch(groups, 1)
        self.assertAlmostEqual(sum(p for p, _, _ in branches), 1.0)
        for p, chosen, rest in branches:
            self.assertAlmostEqual(p, 0.5)
            self.assertEqual(len(chosen), 1)


class QualificationTests(unittest.TestCase):
    def test_locked_team_takes_slot_first(self):
        teams = [make_team(t, "A", i + 1) for i, t in enumerate(["X", "Y", "Z"])]
        teams += [make_team(t, "B", i + 1) for i, t in enumerate(["P", "Q", "R"])]
        games = [
            make_game("g1", "X", "Y", status="played", winner="Y"),
            make_game("g2", "X", "Z", status="played", winner="Z"),
            make_game("g3", "Y", "Z", status="played", winner="Z"),
            make_game("g4", "P", "Q", status="played", winner="P"),
            make_game("g5", "P", "R", status="played", winner="P"),
            make_game("g6", "Q", "R", status="played", winner="Q"),
        ]
        rules = make_rules(rank_slots={"A": 2, "B": 1})
        view = make_view(teams, games, rules=rules, locked=("X",))
        branches = qualifier_branches(view, view.decided)
        rank_set = branches[0][1]
        # X 战绩最差但资格已锁定，优先占用 A 区 2 个名额之一
        self.assertIn("X", rank_set)
        self.assertIn("Z", rank_set)  # A 区剩余名额给战绩最好的 Z
        self.assertIn("P", rank_set)

    def test_withdrawn_team_excluded_but_results_count(self):
        teams = [make_team(t, "A", i + 1) for i, t in enumerate(["X", "Y", "Z"])]
        teams += [make_team(t, "B", i + 1) for i, t in enumerate(["P", "Q", "R"])]
        games = [
            make_game("g1", "X", "Y", status="played", winner="Y"),  # 退赛队已赛结果保留
            make_game("g2", "X", "Z", status="withdrawal"),
            make_game("g3", "Y", "Z", status="played", winner="Y"),
            make_game("g4", "P", "Q", status="played", winner="P"),
            make_game("g5", "P", "R", status="played", winner="P"),
            make_game("g6", "Q", "R", status="played", winner="Q"),
        ]
        view = make_view(teams, games, withdrawn=("X",))
        points, _ = season_points(view.decided, view.rules, sorted(view.teams))
        self.assertEqual(points["Y"], 6)  # 含对退赛队的已赛胜场
        branches = qualifier_branches(view, view.decided)
        for _p, rank_set, shared_set in branches:
            self.assertNotIn("X", rank_set)
            self.assertNotIn("X", shared_set)

    def test_enumeration_probability_conservation(self):
        # 每区名额概率之和 = 剩余名额数；共享名额概率之和 = shared_slots
        teams = [make_team(f"A{i}", "A", i + 1) for i in range(3)]
        teams += [make_team(f"B{i}", "B", i + 1) for i in range(3)]
        games = [
            make_game("g1", "A0", "A1", status="played", winner="A0"),
            make_game("g2", "A1", "A2", status="scheduled", slot=2),
            make_game("g3", "A0", "A2", status="scheduled", slot=3),
            make_game("g4", "B0", "B1", status="played", winner="B0"),
            make_game("g5", "B1", "B2", status="scheduled", slot=2),
            make_game("g6", "B0", "B2", status="scheduled", slot=3),
        ]
        view = make_view(teams, games)
        dist = chance.qualification_distribution(view, label="t")
        for area in ("A", "B"):
            s = sum(dist.rank[t] for t in view.area_teams(area))
            self.assertAlmostEqual(s, view.rules.rank_slots[area])
        self.assertAlmostEqual(sum(dist.shared.values()), view.rules.shared_slots)
        self.assertEqual(dist.paths, 2 ** 4)

    def test_form_probs_ignore_admin_and_withdrawal(self):
        teams = [make_team(t, "A", i + 1) for i, t in enumerate(["X", "Y", "Z"])]
        games = [
            make_game("g1", "X", "Y", status="played", winner="X"),
            make_game("g2", "X", "Z", status="administrative", winner="X"),
            make_game("g3", "X", "Z", status="scheduled", slot=2),
        ]
        view = make_view(teams, games)
        probs = chance.win_probs_form(view)
        # X 只有 1 场已开打样本（判分胜不计）：s_X=2/3；Z 无样本：s_Z=1/2
        self.assertAlmostEqual(probs["g3"], (2 / 3) / (2 / 3 + 1 / 2))


class MakeupTests(unittest.TestCase):
    def setUp(self):
        self.records = load_records()
        self.season = max(self.records.seasons)
        self.view = SeasonView.from_records(self.records, self.season)

    def test_blocked_and_rest_and_arena_rules(self):
        from fairplay.records import CourtSlot

        teams = [make_team("X", "N", 1), make_team("Y", "N", 2)]
        slots = [
            CourtSlot(id="B1", slot=10, arena="roomA", blocked=True),   # 被占用
            CourtSlot(id="B2", slot=11, arena="roomA", blocked=False),  # 休息不足
            CourtSlot(id="B3", slot=12, arena="roomB", blocked=False),  # 场地不符
            CourtSlot(id="B4", slot=13, arena="roomA", blocked=False),  # 可行
        ]
        games = [make_game("g0", "X", "Y", status="played", winner="X", slot=10)]
        view = make_view(teams, games, court_slots=slots)
        game = make_game("g1", "X", "Y", status="scheduled", slot=1)
        rows = remedy.feasible_rows(view, game, {}, remedy._fixed_slots(view, {"g1"}))
        self.assertEqual([r.id for r in rows], ["B4"])

    def test_current_season_makeup_capacity(self):
        plans = remedy.makeup_plans(self.view)
        self.assertTrue(plans)
        # 北区 4 场全部排入；南区 4 场只能排 3 场，每个方案恰好取消 1 场南区对局
        for plan in plans:
            self.assertEqual(len(plan.dropped), 1)
            dropped = plan.dropped[0]
            g = next(x for x in self.view.all_regional_games() if x.id == dropped)
            self.assertEqual(self.view.teams[g.a].area, "S")
            n_games = {"2026-4", "2026-11", "2026-12", "2026-13"}
            self.assertTrue(n_games <= set(plan.assignments))

    def test_withdrawal_and_admin_remedies(self):
        view = self.view
        # 退赛假设：S3 的待赛转为待裁
        v2 = remedy.apply_withdrawal(view, "S3")
        self.assertIn("S3", v2.withdrawn)
        moved = {g.id for g in v2.pending_games} - {g.id for g in view.pending_games}
        self.assertEqual(moved, {"2026-60", "2026-61"})
        # 行政判分：对方得 admin_points
        v3 = remedy.apply_admin_points(view, ["2026-62"])
        points, played_wins = season_points(v3.decided, v3.rules, sorted(v3.teams))
        self.assertEqual(points["S3"], 3)
        self.assertEqual(played_wins["S3"], 0)  # 判分胜不计已开打胜场
        # 取消：不产生积分
        v4 = remedy.apply_void(view, ["2026-62"])
        points4, _ = season_points(v4.decided, v4.rules, sorted(v4.teams))
        self.assertEqual(points4["S3"], 0)


class LookbackTests(unittest.TestCase):
    def test_lookback_rows(self):
        records = load_records()
        season = max(records.seasons)
        rows = lookback.lookback(records, season)
        self.assertEqual(len(rows), 2 * 2)  # 两个往届 × 两个赛区
        for row in rows:
            self.assertEqual(len(row.actual), row.projected and 2)
            self.assertTrue(all(0.0 <= p <= 1.0 for _, p in row.projected))


class ReportTests(unittest.TestCase):
    def test_full_report_sections(self):
        records = load_records()
        text = report.build_report(records, max(records.seasons))
        for marker in (
            "当前积分与名额状态",
            "结果类型拆分",
            "机会比较",
            "强度解释集合",
            "共享名额的赛区强度情景",
            "补赛窗口安排",
            "退赛与申诉的待决影响",
            "往届同一赛程阶段回看",
            "待赛事组确认的决定",
            "已确定名额（可追溯）：N3",
        ):
            self.assertIn(marker, text)

    def test_event_preview(self):
        records = load_records()
        season = max(records.seasons)
        text = report.event_text(records, season, team="S3")
        self.assertIn("假设 S3 退赛", text)
        self.assertIn("2026-60", text)
        text = report.event_text(records, season, game_id="2026-60")
        self.assertIn("延期", text)

    def test_standings_verification(self):
        records = load_records()
        text = report.standings_text(records, 2024)
        self.assertIn("赛区名额 N3, N4, S1, S4", text)


if __name__ == "__main__":
    unittest.main()
