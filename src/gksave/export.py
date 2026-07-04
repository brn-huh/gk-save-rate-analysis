"""익스포트 (T7, T7b) — 집계 결과를 정적 JSON/CSV로.

리더보드에는 교란 주의 라벨과 표본 경기수를 반드시 함께 노출한다(T7b).
raw 종합선방률은 카드 성능이 아니라 그 카드를 쓰는 유저 실력이 섞인 값이므로
'카드 추천'이 아님을 산출물에서 명시한다.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from . import agg, meta, render
from .config import MIN_MATCHES_GATE, ZONE_CUTS_M

WARNING = (
    "이 순위는 raw 종합선방률이다. matchtype=50은 사람이 키핑하므로 이 값에는 "
    "카드 성능뿐 아니라 그 카드를 쓴 유저의 실력·수비 라인·상대 슛 난이도가 섞여 있다. "
    "따라서 '카드 추천'이 아니다. 강화 자체의 효과는 grade_effect(유저 내 비교)를 볼 것. "
    "각 순위에는 표본 경기수(matches)를 함께 표기한다."
)


def build_payload(
    con: duckdb.DuckDBPyConnection,
    *,
    gate: int = MIN_MATCHES_GATE,
    since: datetime | None = None,
) -> dict:
    # (선수×시즌×강화단계) 단위 — 강화를 퉁치지 않는다
    leaderboard = agg.grade_leaderboard(con, gate=gate, since=since)

    dr = con.execute(
        "SELECT min(match_date), max(match_date) FROM gk_match WHERE match_date IS NOT NULL"
    ).fetchone()
    date_range = {
        "min": dr[0].date().isoformat() if dr and dr[0] else None,
        "max": dr[1].date().isoformat() if dr and dr[1] else None,
    }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "gate_min_matches": gate,
        "since": since.isoformat() if since else None,
        "date_range": date_range,
        "warning": WARNING,
        "leaderboard_count": len(leaderboard),
        "leaderboard": leaderboard,
        "grade_effect": agg.within_ouid_grade_effect(con, since=since),
    }
    # GSAx(난이도 보정): 전체 + 초근거리(<5m) 제외 두 버전
    gsax = agg.gsax_leaderboard(con, gate=gate, since=since)
    gsax_ex = agg.gsax_leaderboard(con, gate=gate, since=since, min_dist_m=ZONE_CUTS_M[0])
    payload["gsax"] = gsax

    # 리더보드 카드에 두 GSAx 붙이기 (같은 (sp_id, 강화) 키로) → 동일선수·페이지에서도 반영
    gsax_by = {(g["gk_sp_id"], g["grade"]): g for g in gsax}
    gsax_ex_by = {(g["gk_sp_id"], g["grade"]): g for g in gsax_ex}
    for c in leaderboard:
        gk = gsax_by.get((c["gk_sp_id"], c["grade"]))
        c["gsax"] = gk["gsax"] if gk else None
        c["gsax_per_shot"] = gk["gsax_per_shot"] if gk else None
        ge = gsax_ex_by.get((c["gk_sp_id"], c["grade"]))
        c["gsax_ex_short"] = ge["gsax"] if ge else None
        c["gsax_ex_short_per_shot"] = ge["gsax_per_shot"] if ge else None

    # 카드별 거리 존별·타입별 (대량 집계 2쿼리) → 각 카드에 첨부(페이지 드릴다운용)
    zones_all = agg.zone_breakdown_all(con, since=since)
    types_all = agg.type_breakdown_all(con, since=since)
    extras_all = agg.card_extras_all(con, since=since)
    for c in leaderboard:
        key = (c["gk_sp_id"], c["grade"])
        c["zones"] = zones_all.get(key, [])
        c["types"] = types_all.get(key, [])
        c["extras"] = extras_all.get(key, {})

    # 메타 캐시가 있으면 선수명·시즌 붙이고 동일선수 시즌 비교표 추가
    if meta.has_meta(con):
        meta.enrich(con, leaderboard)
        meta.enrich(con, gsax)
        payload["same_player"] = meta.same_player_view(leaderboard)
    return payload


def export(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path,
    *,
    gate: int = MIN_MATCHES_GATE,
    since: datetime | None = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload(con, gate=gate, since=since)

    (out_dir / "leaderboard.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # CSV (평면). 빈 리더보드도 헤더는 남긴다.
    with (out_dir / "leaderboard.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "gk_sp_id", "player_name", "season_id", "season_name",
                    "grade", "matches", "saves", "goals", "save_pct"])
        for c in payload["leaderboard"]:
            pct = "" if c["save_pct"] is None else f"{c['save_pct']:.4f}"
            w.writerow([
                c["rank"], c["gk_sp_id"], c.get("player_name", ""),
                c.get("season_id", ""), c.get("season_name", ""),
                c.get("grade", ""), c["matches"], c["saves"], c["goals"], pct,
            ])

    # 공개용 정적 HTML (자기완결형, 그대로 열거나 호스팅)
    (out_dir / "index.html").write_text(render.build_html(payload), encoding="utf-8")

    return payload
