"""载入并核对 records 目录中的业务记录。

catalog.json 记录每张表的行数与 sha256；载入时逐项复核，
资料被改动会直接报错，避免在错误资料上得出晋级结论。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

RECORD_DIR = Path(__file__).resolve().parents[1] / "records"


@dataclass(frozen=True)
class Team:
    id: str
    area: str
    seed: int
    starter: str
    backup: str


@dataclass(frozen=True)
class Game:
    id: str
    season: int
    stage: str
    a: str
    b: str
    a_roster: str
    b_roster: str
    slot: int
    known_slot: int
    status: str
    winner: str | None
    locked: bool
    arena: str

    def involves(self, team: str) -> bool:
        return self.a == team or self.b == team

    def opponent(self, team: str) -> str:
        return self.b if self.a == team else self.a

    def roster_of(self, team: str) -> str:
        return self.a_roster if self.a == team else self.b_roster


@dataclass(frozen=True)
class CourtSlot:
    id: str
    slot: int
    arena: str
    blocked: bool


@dataclass(frozen=True)
class Rules:
    id: str
    season: int
    win_points: int
    admin_points: int
    withdrawal: str
    tie_order: tuple[str, ...]
    rank_slots: dict[str, int]
    shared_slots: int
    min_rest_slots: int
    max_opp_gap: float
    seed_protect: tuple[int, ...]


@dataclass(frozen=True)
class Ruling:
    id: str
    team: str
    operation: str
    known_slot: int
    stage: str


@dataclass
class Records:
    teams: list[Team]
    games: list[Game]
    slots: list[CourtSlot]
    rules: list[Rules]
    rulings: list[Ruling]

    @property
    def seasons(self) -> list[int]:
        return sorted({g.season for g in self.games})

    def rules_for(self, season: int) -> Rules:
        for r in self.rules:
            if r.season == season:
                return r
        raise KeyError(f"缺少赛季 {season} 的规则记录")

    def games_of(self, season: int) -> list[Game]:
        return [g for g in self.games if g.season == season]


def _read_table(directory: Path, name: str) -> list[dict]:
    raw = (directory / f"{name}.jsonl").read_bytes()
    catalog = json.loads((directory / "catalog.json").read_bytes())
    expected = catalog[name]
    if sha256(raw).hexdigest() != expected["sha256"]:
        raise ValueError(f"{name}.jsonl 校验值与 catalog.json 不符，资料可能已被改动")
    rows = [json.loads(line) for line in raw.splitlines()]
    if len(rows) != expected["rows"]:
        raise ValueError(f"{name}.jsonl 行数与 catalog.json 不符")
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{name}.jsonl 存在重复 id")
    return rows


def load_records(directory: str | Path | None = None) -> Records:
    """载入全部记录并复核 catalog 校验值。"""
    directory = Path(directory) if directory else RECORD_DIR
    teams = [Team(**row) for row in _read_table(directory, "teams")]
    games = [Game(**row) for row in _read_table(directory, "games")]
    slots = [CourtSlot(**row) for row in _read_table(directory, "slots")]
    rules = [
        Rules(
            id=row["id"],
            season=row["season"],
            win_points=row["win_points"],
            admin_points=row["admin_points"],
            withdrawal=row["withdrawal"],
            tie_order=tuple(row["tie_order"]),
            rank_slots=dict(row["rank_slots"]),
            shared_slots=row["shared_slots"],
            min_rest_slots=row["min_rest_slots"],
            max_opp_gap=row["max_opp_gap"],
            seed_protect=tuple(row["seed_protect"]),
        )
        for row in _read_table(directory, "rules")
    ]
    rulings = [Ruling(**row) for row in _read_table(directory, "rulings")]
    return Records(teams=teams, games=games, slots=slots, rules=rules, rulings=rulings)
