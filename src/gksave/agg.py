"""집계 (T5 카드 리더보드 + T9 within-ouid 강화효과).

헤드라인 = 종합선방률(단순), PK 제외. 표본 경기수 게이트(기본 50)는 카드
(gk_sp_id = 시즌×선수) 단위에 건다. 강화단계는 카드 안에서 세부 분해한다.

within-ouid 강화효과: 같은 유저(ouid)가 같은 카드(sp_id)를 서로 다른 강화단계로
쓴 경우만 비교해, "강화하면 더 막나"에서 유저 실력 교란을 제거한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import duckdb

from .config import DEFAULT, MIN_MATCHES_GATE, Settings
from .parse import ParseStats, parse_match

# shot 테이블 컬럼 순서 (INSERT 바인딩용)
_SHOT_COLS = (
    "match_id, match_date, gk_ouid, gk_sp_id, gk_sp_grade, shot_type, result, "
    "is_pk, in_penalty, assist, hit_post, x, y"
)
_N_SHOT_COLS = 13


def rebuild(con: duckdb.DuckDBPyConnection) -> ParseStats:
    """raw_match 전체를 다시 파싱해 gk_match·shot 테이블을 재생성."""
    con.execute("DELETE FROM gk_match")
    con.execute("DELETE FROM shot")
    stats = ParseStats()
    apps: list[tuple] = []
    shots: list[tuple] = []
    for mid, payload in con.execute("SELECT match_id, payload FROM raw_match").fetchall():
        detail = json.loads(payload) if isinstance(payload, str) else payload
        a, s = parse_match(detail, stats)
        apps.extend((x.match_id, x.match_date, x.gk_ouid, x.gk_sp_id, x.gk_sp_grade) for x in a)
        shots.extend(
            (
                x.match_id, x.match_date, x.gk_ouid, x.gk_sp_id, x.gk_sp_grade,
                x.shot_type, x.result, x.is_pk, x.in_penalty, x.assist, x.hit_post, x.x, x.y,
            )
            for x in s
        )
    if apps:
        con.executemany(
            "INSERT INTO gk_match (match_id, match_date, gk_ouid, gk_sp_id, gk_sp_grade) "
            "VALUES (?, ?, ?, ?, ?)",
            apps,
        )
    if shots:
        con.executemany(
            f"INSERT INTO shot ({_SHOT_COLS}) VALUES ({', '.join(['?'] * _N_SHOT_COLS)})", shots
        )
    return stats


def _date_pred(since: datetime | None, *, first: bool) -> tuple[str, list]:
    """날짜 하한 술어. first=True 면 WHERE, 아니면 AND 로 시작."""
    if since is None:
        return "", []
    kw = "WHERE" if first else "AND"
    return f" {kw} match_date >= ?", [since]


def _save_pct(saves: int, goals: int) -> float | None:
    denom = saves + goals
    return saves / denom if denom > 0 else None


def card_leaderboard(
    con: duckdb.DuckDBPyConnection,
    *,
    gate: int = MIN_MATCHES_GATE,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """카드(sp_id) 단위 종합선방률 리더보드. gate 미만 표본은 제외.

    since 를 주면 match_date >= since 인 경기만 집계한다(최근 N일 등).
    """
    m_pred, m_params = _date_pred(since, first=True)
    s_pred, s_params = _date_pred(since, first=False)
    rows = con.execute(
        f"""
        WITH m AS (
            SELECT gk_sp_id, count(DISTINCT match_id) AS matches
            FROM gk_match{m_pred} GROUP BY gk_sp_id
        ),
        s AS (
            SELECT gk_sp_id,
                   sum(CASE WHEN result = 1 THEN 1 ELSE 0 END) AS saves,
                   sum(CASE WHEN result = 3 THEN 1 ELSE 0 END) AS goals
            FROM shot WHERE NOT is_pk{s_pred} GROUP BY gk_sp_id
        )
        SELECT m.gk_sp_id, m.matches,
               COALESCE(s.saves, 0) AS saves, COALESCE(s.goals, 0) AS goals
        FROM m LEFT JOIN s USING (gk_sp_id)
        WHERE m.matches >= ?
        """,
        m_params + s_params + [gate],
    ).fetchall()

    out = [
        {
            "gk_sp_id": r[0],
            "matches": r[1],
            "saves": r[2],
            "goals": r[3],
            "save_pct": _save_pct(r[2], r[3]),
        }
        for r in rows
    ]
    # 선방률 desc, None(유효슛 0)은 맨 뒤
    out.sort(key=lambda d: (d["save_pct"] is not None, d["save_pct"] or 0.0), reverse=True)
    for i, d in enumerate(out, 1):
        d["rank"] = i
    return out


def grade_leaderboard(
    con: duckdb.DuckDBPyConnection,
    *,
    gate: int = MIN_MATCHES_GATE,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """(선수×시즌=sp_id) × 강화단계 단위 리더보드. 강화단계를 퉁치지 않는다.

    각 (sp_id, spGrade) 조합이 한 행. gate 는 그 조합의 표본 경기수에 건다.
    """
    m_pred, m_params = _date_pred(since, first=True)
    s_pred, s_params = _date_pred(since, first=False)
    rows = con.execute(
        f"""
        WITH m AS (
            SELECT gk_sp_id, gk_sp_grade, count(DISTINCT match_id) AS matches
            FROM gk_match{m_pred} GROUP BY 1, 2
        ),
        s AS (
            SELECT gk_sp_id, gk_sp_grade,
                   sum(CASE WHEN result = 1 THEN 1 ELSE 0 END) AS saves,
                   sum(CASE WHEN result = 3 THEN 1 ELSE 0 END) AS goals
            FROM shot WHERE NOT is_pk{s_pred} GROUP BY 1, 2
        )
        SELECT m.gk_sp_id, m.gk_sp_grade, m.matches,
               COALESCE(s.saves, 0), COALESCE(s.goals, 0)
        FROM m LEFT JOIN s USING (gk_sp_id, gk_sp_grade)
        WHERE m.matches >= ?
        """,
        m_params + s_params + [gate],
    ).fetchall()

    out = [
        {"gk_sp_id": r[0], "grade": r[1], "matches": r[2],
         "saves": r[3], "goals": r[4], "save_pct": _save_pct(r[3], r[4])}
        for r in rows
    ]
    out.sort(key=lambda d: (d["save_pct"] is not None, d["save_pct"] or 0.0), reverse=True)
    for i, d in enumerate(out, 1):
        d["rank"] = i
    return out


def grade_breakdown(
    con: duckdb.DuckDBPyConnection, sp_id: int, *, since: datetime | None = None
) -> list[dict[str, Any]]:
    """한 카드의 강화단계별 세부 (표본수 포함)."""
    dp, dparams = _date_pred(since, first=False)
    rows = con.execute(
        f"""
        WITH m AS (
            SELECT gk_sp_grade, count(DISTINCT match_id) AS matches
            FROM gk_match WHERE gk_sp_id = ?{dp} GROUP BY gk_sp_grade
        ),
        s AS (
            SELECT gk_sp_grade,
                   sum(CASE WHEN result = 1 THEN 1 ELSE 0 END) AS saves,
                   sum(CASE WHEN result = 3 THEN 1 ELSE 0 END) AS goals
            FROM shot WHERE NOT is_pk AND gk_sp_id = ?{dp} GROUP BY gk_sp_grade
        )
        SELECT m.gk_sp_grade, m.matches,
               COALESCE(s.saves, 0), COALESCE(s.goals, 0)
        FROM m LEFT JOIN s USING (gk_sp_grade)
        ORDER BY m.gk_sp_grade
        """,
        [sp_id, *dparams, sp_id, *dparams],
    ).fetchall()
    return [
        {"grade": r[0], "matches": r[1], "saves": r[2], "goals": r[3],
         "save_pct": _save_pct(r[2], r[3])}
        for r in rows
    ]


def within_ouid_grade_effect(
    con: duckdb.DuckDBPyConnection,
    *,
    min_matches_per_cell: int = 5,
    since: datetime | None = None,
) -> dict[str, Any]:
    """유저 내(같은 ouid·같은 sp_id) 강화단계 상승의 선방률 변화.

    같은 유저가 같은 카드를 여러 강화단계로 쓴 경우만 골라, 인접 강화단계
    간 선방률 차이(Δ)를 유저별로 구한 뒤 평균낸다. 유저 실력은 페어 안에서
    상쇄되므로 '강화 자체'의 효과에 가깝다.
    """
    m_pred, m_params = _date_pred(since, first=True)
    s_pred, s_params = _date_pred(since, first=False)
    rows = con.execute(
        f"""
        WITH m AS (
            SELECT gk_ouid, gk_sp_id, gk_sp_grade,
                   count(DISTINCT match_id) AS matches
            FROM gk_match{m_pred} GROUP BY 1, 2, 3
        ),
        s AS (
            SELECT gk_ouid, gk_sp_id, gk_sp_grade,
                   sum(CASE WHEN result = 1 THEN 1 ELSE 0 END) AS saves,
                   sum(CASE WHEN result = 3 THEN 1 ELSE 0 END) AS goals
            FROM shot WHERE NOT is_pk{s_pred} GROUP BY 1, 2, 3
        )
        SELECT m.gk_ouid, m.gk_sp_id, m.gk_sp_grade, m.matches,
               COALESCE(s.saves, 0), COALESCE(s.goals, 0)
        FROM m LEFT JOIN s USING (gk_ouid, gk_sp_id, gk_sp_grade)
        WHERE m.matches >= ?
        ORDER BY m.gk_ouid, m.gk_sp_id, m.gk_sp_grade
        """,
        m_params + s_params + [min_matches_per_cell],
    ).fetchall()

    # (ouid, sp_id) → {grade: save_pct}
    cells: dict[tuple, dict[int, float]] = {}
    for ouid, sp_id, grade, _matches, saves, goals in rows:
        pct = _save_pct(saves, goals)
        if pct is None:
            continue
        cells.setdefault((ouid, sp_id), {})[grade] = pct

    # 같은 (ouid, sp_id) 안에서 인접 강화단계 쌍의 Δ
    deltas: list[float] = []
    pairs = 0
    for grades in cells.values():
        gs = sorted(grades)
        for lo, hi in zip(gs, gs[1:]):
            deltas.append((grades[hi] - grades[lo]) / (hi - lo))  # 단계당 Δ선방률
            pairs += 1

    mean_delta = sum(deltas) / len(deltas) if deltas else None
    return {
        "paired_users": len([c for c in cells.values() if len(c) >= 2]),
        "pairs": pairs,
        "mean_save_pct_delta_per_grade": mean_delta,
        "note": (
            "같은 유저·같은 카드에서 강화단계가 1 오를 때 평균 선방률 변화. "
            "유저 실력 교란이 페어 안에서 상쇄됨. 표본 적으면 신뢰 낮음."
        ),
    }
