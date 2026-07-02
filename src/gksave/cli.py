"""명령줄 진입점.

  gksave spike                         # T0: 실제 API 실측 검증 (키 필요)
  gksave collect --seed-pages 5 --max-matches 5000
  gksave build                         # raw_match 재파싱 → gk_match/shot 재생성
  gksave export --gate 50 --out out    # 리더보드 JSON/CSV
  gksave leaderboard --gate 50 --top 20
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import agg, collect, export as export_mod, spike
from .config import DEFAULT, MIN_MATCHES_GATE
from .db import connect, raw_match_count


def _cmd_spike(_args) -> None:
    print(spike.run(DEFAULT))


def _cmd_collect(args) -> None:
    collect.run(DEFAULT, seed_pages=args.seed_pages, max_new_matches=args.max_matches)


def _cmd_build(_args) -> None:
    con = connect(DEFAULT)
    try:
        stats = agg.rebuild(con)
        print(
            f"raw_match {raw_match_count(con)}건 → 파싱: 매치 {stats.matches}, "
            f"GK출전 {stats.appearances}, 슛 {stats.shots} | "
            f"스킵(2팀아님 {stats.skipped_not_two_teams}, "
            f"GK≠1 {stats.skipped_no_single_gk}, "
            f"강화범위밖 {stats.skipped_grade_out_of_range})"
        )
    finally:
        con.close()


def _cmd_export(args) -> None:
    con = connect(DEFAULT, read_only=True)
    try:
        payload = export_mod.export(con, Path(args.out), gate=args.gate)
        print(f"리더보드 {payload['leaderboard_count']}장 → {args.out}/leaderboard.{{json,csv}}")
        ge = payload["grade_effect"]
        print(
            f"강화효과(유저 내): 페어유저 {ge['paired_users']}, "
            f"단계당 Δ선방률 {ge['mean_save_pct_delta_per_grade']}"
        )
    finally:
        con.close()


def _cmd_leaderboard(args) -> None:
    con = connect(DEFAULT, read_only=True)
    try:
        lb = agg.card_leaderboard(con, gate=args.gate)
        print(f"# 카드 리더보드 (게이트 {args.gate}경기, 상위 {args.top})")
        print("주의: raw 선방률 — 유저 실력 교란 포함, 카드 추천 아님")
        for c in lb[: args.top]:
            pct = "  N/A" if c["save_pct"] is None else f"{c['save_pct'] * 100:5.1f}%"
            print(f"  {c['rank']:>3}. spId={c['gk_sp_id']}  {pct}  "
                  f"({c['saves']}/{c['saves'] + c['goals']} 세이브, {c['matches']}경기)")
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gksave", description="FC온라인 GK 선방률 분석")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("spike", help="T0 실측 검증").set_defaults(func=_cmd_spike)

    c = sub.add_parser("collect", help="시드+스노우볼 수집")
    c.add_argument("--seed-pages", type=int, default=5)
    c.add_argument("--max-matches", type=int, default=5000)
    c.set_defaults(func=_cmd_collect)

    sub.add_parser("build", help="재파싱(gk_match/shot 재생성)").set_defaults(func=_cmd_build)

    e = sub.add_parser("export", help="JSON/CSV 산출")
    e.add_argument("--gate", type=int, default=MIN_MATCHES_GATE)
    e.add_argument("--out", default="out")
    e.set_defaults(func=_cmd_export)

    lb = sub.add_parser("leaderboard", help="리더보드 콘솔 출력")
    lb.add_argument("--gate", type=int, default=MIN_MATCHES_GATE)
    lb.add_argument("--top", type=int, default=20)
    lb.set_defaults(func=_cmd_leaderboard)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except RuntimeError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
