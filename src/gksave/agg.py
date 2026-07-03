"""집계 (T5 카드 리더보드 + T9 within-ouid 강화효과).

헤드라인 = 종합선방률(단순), PK 제외. 표본 경기수 게이트(기본 50)는 카드
(gk_sp_id = 시즌×선수) 단위에 건다. 강화단계는 카드 안에서 세부 분해한다.

within-ouid 강화효과: 같은 유저(ouid)가 같은 카드(sp_id)를 서로 다른 강화단계로
쓴 경우만 비교해, "강화하면 더 막나"에서 유저 실력 교란을 제거한다.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from datetime import datetime
from typing import Any

import duckdb

from .config import (
    DEFAULT,
    MIN_MATCHES_GATE,
    PITCH_SCALE_M,
    SHOT_TYPE_HEADER,
    SHOT_TYPE_NAMES,
    ZONE_CUTS_M,
    ZONE_NAMES,
    Settings,
)
from .parse import ParseStats, parse_match

_GK_MATCH_COLS = ["match_id", "match_date", "gk_ouid", "gk_sp_id", "gk_sp_grade"]
_SHOT_COL_LIST = [
    "match_id", "match_date", "gk_ouid", "gk_sp_id", "gk_sp_grade", "shot_type",
    "result", "is_pk", "in_penalty", "assist", "hit_post", "x", "y",
]
_SHOT_COLS = ", ".join(_SHOT_COL_LIST)


def _new_csv():
    f = tempfile.NamedTemporaryFile(
        mode="w", newline="", encoding="utf-8", suffix=".csv", delete=False
    )
    return f, csv.writer(f)


def rebuild(con: duckdb.DuckDBPyConnection) -> ParseStats:
    """raw_match 전체를 재파싱해 gk_match·shot 을 재생성.

    원본을 스트리밍(fetchmany)으로 읽어 임시 CSV에 쓰고 DuckDB COPY 로 벌크 적재.
    행별 executemany(28만행 = ~14분) 대신이라 수십 초로 줄고, 1.5GB를 한 번에
    메모리로 올리지도 않는다. None→'' (NULL), datetime/bool 은 CSV 문자열로 자동 파싱.
    """
    con.execute("DELETE FROM gk_match")
    con.execute("DELETE FROM shot")
    stats = ParseStats()

    af, aw = _new_csv()
    sf, sw = _new_csv()
    try:
        aw.writerow(_GK_MATCH_COLS)
        sw.writerow(_SHOT_COL_LIST)
        cur = con.execute("SELECT match_id, payload FROM raw_match")
        while True:
            batch = cur.fetchmany(1000)
            if not batch:
                break
            for _mid, payload in batch:
                detail = json.loads(payload) if isinstance(payload, str) else payload
                apps, shots = parse_match(detail, stats)
                for x in apps:
                    aw.writerow([x.match_id, x.match_date, x.gk_ouid, x.gk_sp_id, x.gk_sp_grade])
                for x in shots:
                    sw.writerow([
                        x.match_id, x.match_date, x.gk_ouid, x.gk_sp_id, x.gk_sp_grade,
                        x.shot_type, x.result, x.is_pk, x.in_penalty, x.assist, x.hit_post,
                        x.x, x.y,
                    ])
        af.close()
        sf.close()
        con.execute(
            f"COPY gk_match ({', '.join(_GK_MATCH_COLS)}) FROM '{af.name}' "
            "(HEADER true, NULLSTR '')"
        )
        con.execute(f"COPY shot ({_SHOT_COLS}) FROM '{sf.name}' (HEADER true, NULLSTR '')")
    finally:
        for f in (af, sf):
            if not f.closed:
                f.close()
            try:
                os.unlink(f.name)
            except OSError:
                pass
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


def gsax_leaderboard(
    con: duckdb.DuckDBPyConnection,
    *,
    gate: int = MIN_MATCHES_GATE,
    since: datetime | None = None,
    dist_bins: int = 5,
) -> list[dict[str, Any]]:
    """GSAx(난이도 보정) 리더보드.

    각 슛을 (타입 × 정규화거리 구간)으로 묶어 리그 전체의 그 구간 선방률 =
    기대선방확률로 본다. 카드별 기대선방 = 자기가 마주한 슛들의 기대확률 합,
    GSAx = 실제선방 − 기대선방. 슛 난이도 교란(쉬운 슛 많이 마주한 이점)을 제거한다.
    (유저 실력 교란은 within_ouid 가 담당 — 상호보완)

    순위는 GSAx/슛(난이도보정 선방 레이트) 기준. 리그 전체 Σ GSAx = 0.
    """
    s_pred, s_params = _date_pred(since, first=False)
    m_pred, m_params = _date_pred(since, first=True)
    rows = con.execute(
        f"""
        WITH s AS (
            SELECT gk_sp_id, gk_sp_grade, result, shot_type,
                   sqrt((1 - x) * (1 - x) + (0.5 - y) * (0.5 - y)) AS dist
            FROM shot
            WHERE NOT is_pk AND x IS NOT NULL AND y IS NOT NULL{s_pred}
        ),
        b AS (SELECT *, ntile({int(dist_bins)}) OVER (ORDER BY dist) AS dbin FROM s),
        bin_rate AS (
            SELECT shot_type, dbin, avg(CASE WHEN result = 1 THEN 1.0 ELSE 0 END) AS p_save
            FROM b GROUP BY shot_type, dbin
        ),
        ps AS (
            SELECT b.gk_sp_id, b.gk_sp_grade, b.result, br.p_save
            FROM b JOIN bin_rate br USING (shot_type, dbin)
        ),
        a AS (
            SELECT gk_sp_id, gk_sp_grade, count(*) AS shots,
                   sum(CASE WHEN result = 1 THEN 1 ELSE 0 END) AS saves,
                   sum(p_save) AS exp_saves
            FROM ps GROUP BY 1, 2
        ),
        m AS (
            SELECT gk_sp_id, gk_sp_grade, count(DISTINCT match_id) AS matches
            FROM gk_match{m_pred} GROUP BY 1, 2
        )
        SELECT a.gk_sp_id, a.gk_sp_grade, m.matches, a.shots, a.saves, a.exp_saves
        FROM a JOIN m USING (gk_sp_id, gk_sp_grade)
        WHERE m.matches >= ?
        """,
        s_params + m_params + [gate],
    ).fetchall()

    out = []
    for sp_id, grade, matches, shots, saves, exp in rows:
        gsax = saves - exp
        out.append({
            "gk_sp_id": sp_id, "grade": grade, "matches": matches, "shots": shots,
            "saves": saves, "exp_saves": round(exp, 2), "gsax": round(gsax, 2),
            "gsax_per_shot": gsax / shots if shots else None,
            "save_pct": saves / shots if shots else None,
        })
    out.sort(key=lambda d: (d["gsax_per_shot"] is not None, d["gsax_per_shot"] or 0.0), reverse=True)
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


def _card_pred(sp_id: int, grade: int | None, since: datetime | None) -> tuple[str, list]:
    clauses = ["gk_sp_id = ?"]
    params: list = [sp_id]
    if grade is not None:
        clauses.append("gk_sp_grade = ?")
        params.append(grade)
    if since is not None:
        clauses.append("match_date >= ?")
        params.append(since)
    return " AND ".join(clauses), params


def zone_breakdown(
    con: duckdb.DuckDBPyConnection, sp_id: int, *,
    grade: int | None = None, since: datetime | None = None,
) -> list[dict[str, Any]]:
    """거리 구간(초근/근/중/원)별 선방률 (명세서 §2②). 거리 = 정규화×PITCH_SCALE_M(근사)."""
    pred, params = _card_pred(sp_id, grade, since)
    c = PITCH_SCALE_M
    lo, mid, hi = ZONE_CUTS_M
    rows = con.execute(
        f"""
        WITH s AS (
            SELECT result, sqrt((1-x)*(1-x)+(0.5-y)*(0.5-y)) * {c} AS dm
            FROM shot WHERE NOT is_pk AND x IS NOT NULL AND {pred}
        )
        SELECT CASE WHEN dm < {lo} THEN 0 WHEN dm < {mid} THEN 1
                    WHEN dm < {hi} THEN 2 ELSE 3 END AS zone,
               count(*), sum(CASE WHEN result = 1 THEN 1 ELSE 0 END), avg(dm)
        FROM s GROUP BY zone
        """,
        params,
    ).fetchall()
    got = {r[0]: r for r in rows}
    out = []
    for z in range(4):
        r = got.get(z)
        n = r[1] if r else 0
        saves = r[2] if r else 0
        out.append({
            "zone": ZONE_NAMES[z], "shots": n, "saves": saves, "goals": n - saves,
            "save_pct": _save_pct(saves, n - saves),
            "avg_dist_m": round(r[3], 1) if r else None,
        })
    return out


def type_breakdown(
    con: duckdb.DuckDBPyConnection, sp_id: int, *,
    grade: int | None = None, since: datetime | None = None,
) -> dict[str, Any]:
    """슛 타입별 선방률 (명세서 §3) + 헤더/발 슈팅 집계."""
    pred, params = _card_pred(sp_id, grade, since)
    rows = con.execute(
        f"""
        SELECT shot_type, count(*), sum(CASE WHEN result = 1 THEN 1 ELSE 0 END)
        FROM shot WHERE NOT is_pk AND {pred} GROUP BY shot_type ORDER BY count(*) DESC
        """,
        params,
    ).fetchall()
    by_type = [
        {"type": t, "name": SHOT_TYPE_NAMES.get(t, str(t)), "shots": n, "saves": sv,
         "save_pct": _save_pct(sv, n - sv)}
        for t, n, sv in rows
    ]
    # 헤더(type==3) vs 발(그 외)
    hd = con.execute(
        f"""
        SELECT (shot_type = {SHOT_TYPE_HEADER}) AS is_header,
               count(*), sum(CASE WHEN result = 1 THEN 1 ELSE 0 END)
        FROM shot WHERE NOT is_pk AND {pred} GROUP BY 1
        """,
        params,
    ).fetchall()
    agg_hf = {bool(r[0]): (r[1], r[2]) for r in hd}
    def _pack(key):
        n, sv = agg_hf.get(key, (0, 0))
        return {"shots": n, "saves": sv, "save_pct": _save_pct(sv, n - sv)}
    return {"by_type": by_type, "header": _pack(True), "foot": _pack(False)}


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
