"""명령줄 진입점.

  gksave spike                         # T0: 실제 API 실측 검증 (키 필요)
  gksave collect --seed-pages 5 --max-matches 5000
  gksave build                         # raw_match 재파싱 → gk_match/shot 재생성
  gksave export --gate 50 --out out    # 리더보드 JSON/CSV
  gksave leaderboard --gate 50 --top 20
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import agg, collect, export as export_mod, meta, spike
from .config import DEFAULT, MIN_MATCHES_GATE, ZONE_CUTS_M
from .db import connect, raw_match_count
from .http import ResilientClient


def _resolve_since(args) -> datetime | None:
    """--since(YYYY-MM-DD) 우선, 없으면 --days N. naive UTC 반환."""
    if getattr(args, "since", None):
        return datetime.fromisoformat(args.since)
    if getattr(args, "days", None):
        return (datetime.now(timezone.utc) - timedelta(days=args.days)).replace(tzinfo=None)
    return None


def _cmd_spike(_args) -> None:
    print(spike.run(DEFAULT))


def _cmd_collect(args) -> None:
    nicks = [n.strip() for n in (args.seed_nicknames or "").split(",") if n.strip()]
    since = _resolve_since(args)
    if args.concurrency and args.concurrency > 1:
        asyncio.run(collect.run_async(
            DEFAULT, seed_nicknames=nicks, max_new_matches=args.max_matches,
            since=since, refresh=args.refresh, concurrency=args.concurrency,
        ))
    else:
        collect.run(
            DEFAULT, seed_nicknames=nicks, max_new_matches=args.max_matches,
            since=since, refresh=args.refresh,
        )


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


def _cmd_meta(_args) -> None:
    con = connect(DEFAULT)
    try:
        with ResilientClient(DEFAULT) as client:
            n_sp, n_se = meta.refresh(con, client)
        print(f"메타 캐시 갱신: 선수 {n_sp}, 시즌 {n_se}")
    finally:
        con.close()


def _cmd_export(args) -> None:
    con = connect(DEFAULT, read_only=True)
    try:
        payload = export_mod.export(con, Path(args.out), gate=args.gate, since=_resolve_since(args))
        span = f" (since {payload['since']})" if payload.get("since") else ""
        print(f"리더보드 {payload['leaderboard_count']}장{span} → "
              f"{args.out}/leaderboard.{{json,csv}} + index.html")
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
        lb = agg.grade_leaderboard(con, gate=args.gate, since=_resolve_since(args))
        if meta.has_meta(con):
            meta.enrich(con, lb)
        print(f"# 리더보드 (선수×시즌×강화, 게이트 {args.gate}경기, 상위 {args.top})")
        print("주의: raw 선방률 — 유저 실력 교란 포함, 카드 추천 아님")
        for c in lb[: args.top]:
            pct = "  N/A" if c["save_pct"] is None else f"{c['save_pct'] * 100:5.1f}%"
            who = c.get("player_name") or f"spId={c['gk_sp_id']}"
            season = f" [{c['season_name']}]" if c.get("season_name") else ""
            print(f"  {c['rank']:>3}. {who}{season} {c['grade']}강  {pct}  "
                  f"({c['saves']}/{c['saves'] + c['goals']} 세이브, {c['matches']}경기)")
    finally:
        con.close()


def _cmd_gsax(args) -> None:
    con = connect(DEFAULT, read_only=True)
    try:
        min_d = ZONE_CUTS_M[0] if args.exclude_shortest else 0.0
        lb = agg.gsax_leaderboard(con, gate=args.gate, since=_resolve_since(args), min_dist_m=min_d)
        if meta.has_meta(con):
            meta.enrich(con, lb)
        tag = " · 초근거리(<5m) 제외" if args.exclude_shortest else ""
        print(f"# GSAx 리더보드 (난이도 보정{tag}, 게이트 {args.gate}경기, 상위 {args.top})")
        print("GSAx = 실제선방 − 기대선방. 슛 난이도 교란 제거(양수=기대보다 잘 막음).")
        for c in lb[: args.top]:
            who = c.get("player_name") or f"spId={c['gk_sp_id']}"
            season = f" [{c['season_name']}]" if c.get("season_name") else ""
            gps = c["gsax_per_shot"]
            print(f"  {c['rank']:>3}. {who}{season} {c['grade']}강  "
                  f"GSAx {c['gsax']:+.1f} ({gps * 100:+.1f}/100슛)  "
                  f"raw {c['save_pct'] * 100:.1f}% · {c['shots']}슛 · {c['matches']}경기")
    finally:
        con.close()


def _cmd_card(args) -> None:
    con = connect(DEFAULT, read_only=True)
    try:
        since = _resolve_since(args)
        who = {"gk_sp_id": args.sp_id}
        if meta.has_meta(con):
            meta.enrich(con, [who])
        title = who.get("player_name") or f"spId {args.sp_id}"
        season = f" [{who['season_name']}]" if who.get("season_name") else ""
        grade = f" {args.grade}강" if args.grade else " (전체 강화)"
        print(f"# {title}{season}{grade}")

        print("\n[거리 구간별 선방률] (근사 미터)")
        for z in agg.zone_breakdown(con, args.sp_id, grade=args.grade, since=since):
            pct = "N/A" if z["save_pct"] is None else f"{z['save_pct'] * 100:5.1f}%"
            print(f"  {z['zone']:16} {pct}  ({z['saves']}/{z['shots']}슛)")

        print("\n[슛 타입별 선방률]")
        tb = agg.type_breakdown(con, args.sp_id, grade=args.grade, since=since)
        for t in tb["by_type"]:
            pct = "N/A" if t["save_pct"] is None else f"{t['save_pct'] * 100:5.1f}%"
            print(f"  {t['name']:10} {pct}  ({t['saves']}/{t['shots']}슛)")
        h, f = tb["header"], tb["foot"]
        hp = "N/A" if h["save_pct"] is None else f"{h['save_pct'] * 100:.1f}%"
        fp = "N/A" if f["save_pct"] is None else f"{f['save_pct'] * 100:.1f}%"
        print(f"  ── 헤더 {hp} ({h['saves']}/{h['shots']}) | 발 {fp} ({f['saves']}/{f['shots']})")
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gksave", description="FC온라인 GK 선방률 분석")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("spike", help="T0 실측 검증").set_defaults(func=_cmd_spike)

    c = sub.add_parser("collect", help="시드(닉네임)+스노우볼 수집")
    c.add_argument("--seed-nicknames", default="",
                   help="쉼표로 구분한 시드 닉네임 (예: '아이콘,랭커2'). 재개 시 생략 가능")
    c.add_argument("--max-matches", type=int, default=5000)
    c.add_argument("--concurrency", type=int,
                   default=int(os.environ.get("GKSAVE_CONCURRENCY", "10")),
                   help="동시 요청 수 (기본 10, 1 이하=순차). 레이트리밋은 그대로 지켜짐")
    c.add_argument("--refresh", action="store_true",
                   help="처리 끝난 유저를 다시 열어 새 경기 보충 (중복은 자동 차단)")
    c.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후 매치만 수집 "
                   "(미지정 시 기본 하한 config.COLLECT_MIN_DATE 적용)")
    c.add_argument("--days", type=int, help="최근 N일 매치만 수집")
    c.set_defaults(func=_cmd_collect)

    sub.add_parser("build", help="재파싱(gk_match/shot 재생성)").set_defaults(func=_cmd_build)

    sub.add_parser("meta", help="선수명·시즌 메타 캐시 갱신").set_defaults(func=_cmd_meta)

    e = sub.add_parser("export", help="JSON/CSV/HTML 산출")
    e.add_argument("--gate", type=int, default=MIN_MATCHES_GATE)
    e.add_argument("--out", default="out")
    e.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후 경기만 집계")
    e.add_argument("--days", type=int, help="최근 N일 경기만 집계")
    e.set_defaults(func=_cmd_export)

    lb = sub.add_parser("leaderboard", help="리더보드 콘솔 출력")
    lb.add_argument("--gate", type=int, default=MIN_MATCHES_GATE)
    lb.add_argument("--top", type=int, default=20)
    lb.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후 경기만 집계")
    lb.add_argument("--days", type=int, help="최근 N일 경기만 집계")
    lb.set_defaults(func=_cmd_leaderboard)

    gx = sub.add_parser("gsax", help="GSAx(난이도 보정) 리더보드")
    gx.add_argument("--gate", type=int, default=MIN_MATCHES_GATE)
    gx.add_argument("--top", type=int, default=20)
    gx.add_argument("--exclude-shortest", action="store_true",
                    help="초근거리(<5m) 뽀록성 슛 제외")
    gx.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후 경기만 집계")
    gx.add_argument("--days", type=int, help="최근 N일 경기만 집계")
    gx.set_defaults(func=_cmd_gsax)

    cd = sub.add_parser("card", help="카드 상세 (거리 존별·타입별 선방률)")
    cd.add_argument("sp_id", type=int, help="선수 spId (리더보드/CSV에서 확인)")
    cd.add_argument("--grade", type=int, help="특정 강화단계만 (미지정=전체)")
    cd.add_argument("--since", help="이 날짜(YYYY-MM-DD) 이후 경기만")
    cd.add_argument("--days", type=int, help="최근 N일만")
    cd.set_defaults(func=_cmd_card)

    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except RuntimeError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
