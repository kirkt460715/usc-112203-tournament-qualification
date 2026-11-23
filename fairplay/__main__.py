"""命令行入口。

用法：
  python3 -m fairplay report [--season 2026]     全流程报告（默认最新赛季）
  python3 -m fairplay standings --season 2024    积分复核，核对已公布成绩
  python3 -m fairplay event --team S3            录入下一次退赛的 what-if 预览
  python3 -m fairplay event --game 2026-60       录入下一次延期的 what-if 预览
"""
from __future__ import annotations

import argparse

from . import report
from .records import load_records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fairplay", description="晋级偏差与补赛决策支持")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_season(p):
        p.add_argument("--season", type=int, default=None, help="赛季，默认最新")

    p_report = sub.add_parser("report", help="赛前比对+赛中补赛+名额复核全流程报告")
    add_season(p_report)
    p_stand = sub.add_parser("standings", help="按规则重算积分，复核已公布成绩")
    add_season(p_stand)
    p_event = sub.add_parser("event", help="录入下一次退赛/延期的影响预览（不写入记录）")
    add_season(p_event)
    p_event.add_argument("--team", help="假设退赛的队伍")
    p_event.add_argument("--game", help="假设延期的对局 id")

    args = parser.parse_args(argv)
    records = load_records()
    season = args.season if args.season is not None else max(records.seasons)

    if args.cmd == "report":
        print(report.build_report(records, season))
    elif args.cmd == "standings":
        print(report.standings_text(records, season))
    elif args.cmd == "event":
        if not args.team and not args.game:
            parser.error("event 需要 --team 或 --game 至少一项")
        print(report.event_text(records, season, team=args.team, game_id=args.game))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
