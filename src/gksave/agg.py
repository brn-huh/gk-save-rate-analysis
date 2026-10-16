"""집계 (T5 카드 리더보드 + T9 within-ouid 강화효과).

헤드라인 = 종합선방률(단순), PK 제외. 표본 경기수 게이트(기본 50)는 카드
(gk_sp_id = 시즌×선수) 단위에 건다. 강화단계는 카드 안에서 세부 분해한다.

within-ouid 강화효과: 같은 유저(ouid)가 같은 카드(sp_id)를 서로 다른 강화단계로
쓴 경우만 비교해, "강화하면 더 막나"에서 유저 실력 교란을 제거한다.
"""

from __future__ import annotations

import csv
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, Callable

import duckdb

from . import defense
from .codec import decode_payload
from .db import raw_match_batches
from .config import (
    DEFAULT,
    MAX_USER_MATCH_PERCENT,
    MIN_MATCHES_GATE,
    PITCH_SCALE_M,
    SHOT_TYPE_HEADER,
    ZONE_CUTS_M,
    ZONE_NAMES,
    Settings,
    shot_type_name,
)
from .parse import ParseStats, parse_match
from .progress import format_duration

_GK_MATCH_COLS = ["match_id", "match_date", "gk_ouid", "gk_sp_id", "gk_sp_grade",
                  "sp_rating", "pass_try", "pass_success", "aerial_try", "aerial_success"]
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


def rebuild(con: duckdb.DuckDBPyConnection, *, full: bool = False) -> ParseStats:
    """raw_match 를 파싱해 gk_match·shot 에 적재.

    full=False (기본, 증분): parsed_at IS NULL 인 미파싱 매치만 처리 → 수집한 만큼만.
    full=True             : 전체 재파싱. gk_match/shot 를 날리고 parsed_at 초기화 후 재생성.

    쿼리부터 1000행씩 제한하고, 임시 CSV + DuckDB COPY로 원자적으로 적재한다.
    """
    predicate = "TRUE" if full else "parsed_at IS NULL"
    unparsed = con.execute(
        f"SELECT count(*) FROM raw_match WHERE {predicate}"
    ).fetchone()[0]
    if unparsed == 0 and not full:
        return ParseStats()

    stats = ParseStats()
    af, aw = _new_csv()
    sf, sw = _new_csv()
    df, dw = _new_csv()
    pf, pw = _new_csv()
    try:
        aw.writerow(_GK_MATCH_COLS)
        sw.writerow(_SHOT_COL_LIST)
        dw.writerow(defense.COLUMNS)
        pw.writerow(["match_id"])
        for batch in raw_match_batches(con, predicate):
            for mid, payload in batch:
                detail = decode_payload(payload)
                apps, shots = parse_match(detail, stats)
                dw.writerows(defense.rows(apps))
                for x in apps:
                    aw.writerow([x.match_id, x.match_date, x.gk_ouid, x.gk_sp_id, x.gk_sp_grade,
                                 x.sp_rating, x.pass_try, x.pass_success,
                                 x.aerial_try, x.aerial_success])
                for x in shots:
                    sw.writerow([
                        x.match_id, x.match_date, x.gk_ouid, x.gk_sp_id, x.gk_sp_grade,
                        x.shot_type, x.result, x.is_pk, x.in_penalty, x.assist, x.hit_post,
                        x.x, x.y,
                    ])
                pw.writerow([mid])
        af.close()
        sf.close()
        df.close()
        pf.close()
        con.execute("BEGIN TRANSACTION")
        try:
            if full:
                con.execute("DELETE FROM gk_match")
                con.execute("DELETE FROM shot")
                con.execute("DELETE FROM defense_match")
                con.execute("UPDATE raw_match SET parsed_at = NULL, defense_parsed_at = NULL")
            con.execute(f"COPY gk_match ({', '.join(_GK_MATCH_COLS)}) FROM ? "
                        "(HEADER true, NULLSTR '')", [af.name])
            con.execute(f"COPY shot ({_SHOT_COLS}) FROM ? (HEADER true, NULLSTR '')", [sf.name])
            con.execute(f"COPY defense_match ({', '.join(defense.COLUMNS)}) FROM ? "
                        "(HEADER true, NULLSTR '')", [df.name])
            # 처리한 ID도 CSV에 두어 전체 ID 목록과 거대한 바인드 인수를 메모리에 쌓지 않는다.
            con.execute(
                "UPDATE raw_match SET parsed_at = now(), defense_parsed_at = now() "
                "WHERE match_id IN (SELECT match_id FROM read_csv(?, header=true, "
                "columns={'match_id': 'VARCHAR'}))", [pf.name],
            )
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    finally:
        for f in (af, sf, df, pf):
            if not f.closed:
                f.close()
            try:
                os.unlink(f.name)
            except OSError:
                pass
    return stats


def _date_pred(
    since: datetime | None, until: datetime | None = None, *, first: bool
) -> tuple[str, list]:
    """날짜 술어. first=True 면 WHERE, 아니면 AND 로 시작.

    until 을 주면 [since, until) 닫힌 창이 된다. 상한은 미포함이라
    인접 창이 경계 경기를 겹쳐 세지 않는다(순위 추이의 시점별 집계).
    """
    conds: list[str] = []
    params: list = []
    if since is not None:
        conds.append("match_date >= ?")
        params.append(since)
    if until is not None:
        conds.append("match_date < ?")
        params.append(until)
    if not conds:
        return "", []
    kw = "WHERE" if first else "AND"
    return f" {kw} " + " AND ".join(conds), params


def _save_pct(saves: int, goals: int) -> float | None:
    denom = saves + goals
    return saves / denom if denom > 0 else None


def _match_counts_cte(
    since: datetime | None, until: datetime | None = None, *, by_grade: bool = True,
) -> tuple[str, list]:
    keys = "gk_sp_id, gk_sp_grade" if by_grade else "gk_sp_id"
    pred, params = _date_pred(since, until, first=True)
    return f"""
        user_matches AS (
            SELECT {keys}, gk_ouid, count(DISTINCT match_id) AS n
            FROM gk_match{pred} GROUP BY {keys}, gk_ouid
        ),
        m AS (
            SELECT totals.*, users, max_user_matches
            FROM (
                SELECT {keys}, count(DISTINCT match_id) AS matches
                FROM gk_match{pred} GROUP BY {keys}
            ) totals JOIN (
                SELECT {keys}, count(*) AS users, max(n) AS max_user_matches
                FROM user_matches GROUP BY {keys}
            ) concentration USING ({keys})
        )
    """, params + params


def _rank_leaderboard(rows: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    """편중된 표본도 통계는 보존하고 순위만 부여하지 않는다."""
    rows.sort(key=lambda r: (r[metric] is not None, r[metric] or 0.0), reverse=True)
    rank = 0
    for r in rows:
        top = r.pop("max_user_matches")
        r["max_user_share"] = top / r["matches"]
        r["rank_eligible"] = top * 100 < r["matches"] * MAX_USER_MATCH_PERCENT
        if r["rank_eligible"]:
            rank += 1
        r["rank"] = rank if r["rank_eligible"] else None
    return rows


def card_leaderboard(
    con: duckdb.DuckDBPyConnection,
    *,
    gate: int = MIN_MATCHES_GATE,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """카드(sp_id) 단위 종합선방률 리더보드. gate 미만 표본은 제외.

    since 를 주면 match_date >= since 인 경기만 집계한다(최근 N일 등).
    사용자 편중으로 제외된 카드도 통계는 반환하며 rank=None 이다.
    """
    m_cte, m_params = _match_counts_cte(since, by_grade=False)
    s_pred, s_params = _date_pred(since, first=False)
    rows = con.execute(
        f"""
        WITH {m_cte},
        s AS (
            SELECT gk_sp_id,
                   sum(CASE WHEN result = 1 THEN 1 ELSE 0 END) AS saves,
                   sum(CASE WHEN result = 3 THEN 1 ELSE 0 END) AS goals
            FROM shot WHERE NOT is_pk{s_pred} GROUP BY gk_sp_id
        )
        SELECT m.gk_sp_id, m.matches,
               COALESCE(s.saves, 0) AS saves, COALESCE(s.goals, 0) AS goals,
               m.users, m.max_user_matches
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
            "users": r[4],
            "max_user_matches": r[5],
        }
        for r in rows
    ]
    return _rank_leaderboard(out, "save_pct")


def grade_leaderboard(
    con: duckdb.DuckDBPyConnection,
    *,
    gate: int = MIN_MATCHES_GATE,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    """(선수×시즌=sp_id) × 강화단계 단위 리더보드. 강화단계를 퉁치지 않는다.

    각 (sp_id, spGrade) 조합이 한 행. gate 는 그 조합의 표본 경기수에 건다.
    until 을 주면 [since, until) 창만 집계한다(순위 추이의 과거 시점).
    사용자 편중으로 제외된 조합도 통계는 반환하며 rank=None 이다.
    """
    m_cte, m_params = _match_counts_cte(since, until)
    s_pred, s_params = _date_pred(since, until, first=False)
    rows = con.execute(
        f"""
        WITH {m_cte},
        s AS (
            SELECT gk_sp_id, gk_sp_grade,
                   sum(CASE WHEN result = 1 THEN 1 ELSE 0 END) AS saves,
                   sum(CASE WHEN result = 3 THEN 1 ELSE 0 END) AS goals
            FROM shot WHERE NOT is_pk{s_pred} GROUP BY 1, 2
        )
        SELECT m.gk_sp_id, m.gk_sp_grade, m.matches,
               COALESCE(s.saves, 0), COALESCE(s.goals, 0), m.users, m.max_user_matches
        FROM m LEFT JOIN s USING (gk_sp_id, gk_sp_grade)
        WHERE m.matches >= ?
        """,
        m_params + s_params + [gate],
    ).fetchall()

    out = [
        {"gk_sp_id": r[0], "grade": r[1], "matches": r[2],
         "saves": r[3], "goals": r[4], "save_pct": _save_pct(r[3], r[4]),
         "users": r[5], "max_user_matches": r[6]}
        for r in rows
    ]
    return _rank_leaderboard(out, "save_pct")


def rank_timeseries(
    con: duckdb.DuckDBPyConnection,
    *,
    gate: int = MIN_MATCHES_GATE,
    window_days: int = 30,
    step_days: int = 3,
    end: datetime,
    start: datetime | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """롤링 window_days 창을 step_days 간격으로 소급 샘플링한 카드별 원자료 시계열.

    시점 t 의 창은 [t-window_days, t). 창 하한이 데이터 시작보다 앞서는 시점은
    표본이 창 길이를 못 채워 순위가 튀므로 버린다.

    **순위를 담지 않는다.** 클라이언트가 검색·필터 결과 안에서 다시 매겨야
    "검색 조건에 맞춘 추이"가 되기 때문(render.py 의 순위는 필터 뒤에 붙는다).
    여기서는 matches/saves/goals 원자료만 주고 순위 계산은 브라우저에 맡긴다.

    비겹침 주간 창이 아니라 롤링 창을 쓰는 이유: 같은 주를 무작위 반분한
    노이즈 바닥(|Δ순위| 중앙값 6.4%)과 인접 주간 변동(6.4~8.8%)이 구분되지 않는다.
    롤링 30일 창은 1주 이동 시 스피어만 0.968 로 훨씬 안정적이다.
    """
    row = con.execute("SELECT min(match_date) FROM gk_match").fetchone()
    data_start = row[0] if row else None
    base = {"window_days": window_days, "step_days": step_days, "gate": gate}
    if data_start is None:
        return {**base, "points": [], "cards": {}}
    # 데이터 시작을 날짜 경계로 내린다. 실측 시작이 2026-06-01 15:01 이라
    # 그대로 쓰면 [6/01 00:00, 7/01) 창이 "하한이 데이터보다 이르다"는 이유로
    # 통째로 버려져 시점을 하나 잃는다. 30일 창에서 반나절 부족은 무해한 반면
    # 추이가 한 칸 짧아지는 손해는 눈에 보인다.
    data_start = data_start.replace(hour=0, minute=0, second=0, microsecond=0)

    span, step = timedelta(days=window_days), timedelta(days=step_days)
    points: list[datetime] = []
    t = end
    while t - span >= data_start and (start is None or t >= start):
        points.append(t)
        t -= step
    points.reverse()

    cards: dict[str, list] = {}
    for i, t in enumerate(points):
        if log is not None:
            log(f"export: 순위 추이 {i+1}/{len(points)} · {t:%Y-%m-%d} 집계 중")
        for c in grade_leaderboard(con, gate=gate, since=t - span, until=t):
            if not c["rank_eligible"]:
                continue
            key = f"{c['gk_sp_id']}_{c['grade']}"
            slot = cards.get(key)
            if slot is None:
                slot = cards[key] = [None] * len(points)
            slot[i] = [c["matches"], c["saves"], c["goals"]]

    return {**base, "points": [p.date().isoformat() for p in points], "cards": cards}


def _expected_shots_cte(
    since: datetime | None, *, dist_bins: int = 5, min_dist_m: float = 0.0,
) -> tuple[str, list]:
    """카드 순위와 수비 조합이 같은 전체 표본·난이도 모델을 사용한다."""
    pred, params = _date_pred(since, first=False)
    dist_filter = (
        f" AND sqrt((1 - x) * (1 - x) + (0.5 - y) * (0.5 - y)) * {PITCH_SCALE_M} >= {min_dist_m}"
        if min_dist_m > 0 else ""
    )
    return f"""
        WITH s AS (
            SELECT match_id, gk_ouid, gk_sp_id, gk_sp_grade, result, shot_type,
                   sqrt((1 - x) * (1 - x) + (0.5 - y) * (0.5 - y)) AS dist
            FROM shot
            WHERE NOT is_pk AND x IS NOT NULL AND y IS NOT NULL{pred}{dist_filter}
        ),
        -- 동일 거리에서도 입력 순서·병렬 실행에 따라 구간이 바뀌지 않게 한다.
        b AS (SELECT *, ntile({int(dist_bins)}) OVER (
            ORDER BY dist, match_id, gk_ouid, gk_sp_id, gk_sp_grade, shot_type, result
        ) AS dbin FROM s),
        bin_rate AS (
            SELECT shot_type, dbin, avg(CASE WHEN result = 1 THEN 1.0 ELSE 0 END) AS p_save
            FROM b GROUP BY shot_type, dbin
        ),
        ps AS (
            SELECT b.*, br.p_save FROM b JOIN bin_rate br USING (shot_type, dbin)
        )
    """, params


_MATCH_KEYS = "match_id, gk_ouid, gk_sp_id, gk_sp_grade"
_DUO_KEYS = "gk_sp_id, gk_sp_grade, cb1_sp_id, cb1_grade, cb2_sp_id, cb2_grade"
_DEFENSE_BATCH_SIZE = 32


@contextmanager
def _match_shots(con, since, *, dist_bins=5, min_dist_m=0.0, eligible_keys=None,
                 defense_gate=None, min_users=5, log=None):
    """전체 슛 모델은 한 번 계산하고, 듀오에 필요한 경기만 개별 통계로 보존한다."""
    log = log or (lambda _: None)
    eligible_join = ""
    try:
        if eligible_keys is not None:
            keys = sorted(eligible_keys)
            con.execute("CREATE TEMP TABLE _gksave_eligible AS SELECT "
                        "unnest(?::BIGINT[]) AS gk_sp_id, unnest(?::INTEGER[]) AS gk_sp_grade",
                        [[k[0] for k in keys], [k[1] for k in keys]])
            eligible_join = "JOIN _gksave_eligible USING (gk_sp_id, gk_sp_grade)"
        match_columns = "NULL::VARCHAR AS match_id, NULL::VARCHAR AS gk_ouid"
        match_join = ""
        if defense_gate is not None:
            log("export: 센터백 듀오 대상 선정 중 (경기수·사용자 수 조건 적용)")
            pred, params = _date_pred(since, first=True)
            con.execute(f"""
                CREATE TEMP TABLE _gksave_duo_candidates AS
                SELECT {_DUO_KEYS}, count(*) AS matches, count(DISTINCT gk_ouid) AS users
                FROM defense_match {eligible_join}{pred} GROUP BY {_DUO_KEYS}
                HAVING count(*) >= ? AND count(DISTINCT gk_ouid) >= ?
            """, params + [defense_gate, min_users])
            con.execute(f"""
                CREATE TEMP TABLE _gksave_duo_matches AS
                SELECT d.match_id, d.gk_ouid, {', '.join('d.' + k.strip() for k in _DUO_KEYS.split(','))}
                FROM defense_match d JOIN _gksave_duo_candidates c USING ({_DUO_KEYS}){pred}
            """, params)
            counts = con.execute("SELECT count(DISTINCT (gk_sp_id,gk_sp_grade)), count(*), "
                                 "coalesce(sum(matches),0) FROM _gksave_duo_candidates").fetchone()
            log(f"export: 센터백 듀오 대상 GK 카드·강화 {counts[0]:,}개 · 조합 {counts[1]:,}개 · 출전 경기 {counts[2]:,}건")
            match_columns = ("CASE WHEN d.match_id IS NOT NULL THEN e.match_id END AS match_id, "
                             "CASE WHEN d.match_id IS NOT NULL THEN e.gk_ouid END AS gk_ouid")
            match_join = f"LEFT JOIN _gksave_duo_matches d USING ({_MATCH_KEYS})"
        model_started = monotonic()
        log("export: 공통 GSAx 모델·경기별 슛 통계 준비 중 (전체 표본으로 1회 계산 · 남은 시간 미산정)")
        cte, params = _expected_shots_cte(since, dist_bins=dist_bins, min_dist_m=min_dist_m)
        pred, raw_params = _date_pred(since, first=False)
        unmodelled = "x IS NULL OR y IS NULL OR shot_type IS NULL"
        if min_dist_m > 0:
            unmodelled += (f" OR NOT (sqrt((1-x)*(1-x)+(0.5-y)*(0.5-y)) * "
                          f"{PITCH_SCALE_M} >= {min_dist_m})")
        con.execute(f"""
            CREATE TEMP TABLE _gksave_match_shots AS
            {cte}, events AS (
                SELECT {_MATCH_KEYS}, result, true AS modelled, p_save FROM ps
                UNION ALL
                SELECT {_MATCH_KEYS}, result, false AS modelled, NULL::DOUBLE AS p_save
                FROM shot WHERE NOT is_pk AND ({unmodelled}){pred}
            )
            SELECT {match_columns}, e.gk_sp_id, e.gk_sp_grade, count(*) AS shots,
                   sum(CASE WHEN result = 1 THEN 1 ELSE 0 END) AS saves,
                   sum(CASE WHEN result = 3 THEN 1 ELSE 0 END) AS goals,
                   sum(CASE WHEN modelled THEN 1 ELSE 0 END) AS gsax_shots,
                   sum(CASE WHEN modelled AND result = 1 THEN 1 ELSE 0 END) AS gsax_saves,
                   sum(CASE WHEN modelled THEN p_save ELSE 0 END) AS exp_saves
            FROM events e {eligible_join} {match_join} GROUP BY 1, 2, 3, 4
        """, params + raw_params)
        log(f"export: 공통 GSAx 모델·경기별 슛 통계 준비 완료 · 경과 {format_duration(monotonic() - model_started)}")
        yield
    finally:
        con.execute("DROP TABLE IF EXISTS temp.main._gksave_match_shots")
        con.execute("DROP TABLE IF EXISTS temp.main._gksave_duo_matches")
        con.execute("DROP TABLE IF EXISTS temp.main._gksave_duo_candidates")
        con.execute("DROP TABLE IF EXISTS temp.main._gksave_eligible")


def _gsax_from_match_shots(con, *, gate, since):
    m_cte, params = _match_counts_cte(since)
    rows = con.execute(f"""
        WITH {m_cte}, a AS (
            SELECT gk_sp_id, gk_sp_grade, sum(gsax_shots) AS shots,
                   sum(gsax_saves) AS saves, sum(exp_saves) AS exp_saves
            FROM _gksave_match_shots GROUP BY 1, 2 HAVING sum(gsax_shots) > 0
        )
        SELECT a.gk_sp_id, a.gk_sp_grade, m.matches, a.shots, a.saves, a.exp_saves,
               m.users, m.max_user_matches
        FROM a JOIN m USING (gk_sp_id, gk_sp_grade) WHERE m.matches >= ?
    """, params + [gate]).fetchall()
    out = []
    for sp_id, grade, matches, shots, saves, exp, users, top in rows:
        gsax = saves - exp
        out.append({
            "gk_sp_id": sp_id, "grade": grade, "matches": matches, "shots": shots,
            "saves": saves, "exp_saves": round(exp, 2), "gsax": round(gsax, 2),
            "gsax_per_shot": gsax / shots, "save_pct": saves / shots,
            "users": users, "max_user_matches": top,
        })
    return _rank_leaderboard(out, "gsax_per_shot")


def gsax_leaderboard(
    con: duckdb.DuckDBPyConnection, *, gate: int = MIN_MATCHES_GATE,
    since: datetime | None = None, dist_bins: int = 5, min_dist_m: float = 0.0,
) -> list[dict[str, Any]]:
    """전체 표본의 타입×거리 모델에 따른 GSAx/슛 순위. 편중 카드는 rank=None.

    min_dist_m보다 가까운 슛은 모델과 GSAx에서 제외한다. PK·좌표 누락 슛도 제외한다.
    """
    with _match_shots(con, since, dist_bins=dist_bins, min_dist_m=min_dist_m):
        return _gsax_from_match_shots(con, gate=gate, since=since)


def _defense_from_match_shots(con, *, log=None):
    log = log or (lambda _: None)
    started = monotonic()
    rows = []
    try:
        cards = con.execute("SELECT gk_sp_id, gk_sp_grade, sum(matches) FROM _gksave_duo_candidates "
                            "GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
        if not cards:
            log("export: 센터백 듀오 집계 완료 · 조건을 통과한 대상 없음")
            return []
        total_matches = sum(c[2] for c in cards)
        log(f"export: 센터백 듀오 입력 준비 중 · GK 카드·강화 {len(cards):,}개 · 출전 경기 {total_matches:,}건")
        # GK 순으로 저장해 묶음마다 전체 원본을 다시 읽지 않고 구간만 조회한다.
        con.execute(f"""
            CREATE TEMP TABLE _gksave_duo_input AS
            SELECT {', '.join('d.' + k.strip() for k in _DUO_KEYS.split(','))},
                   coalesce(s.shots, 0) AS shots, coalesce(s.saves, 0) AS saves,
                   coalesce(s.goals, 0) AS goals, coalesce(s.gsax_shots, 0) AS gsax_shots,
                   coalesce(s.gsax_saves - s.exp_saves, 0) AS gsax
            FROM _gksave_duo_matches d
            LEFT JOIN _gksave_match_shots s USING ({_MATCH_KEYS})
            ORDER BY d.gk_sp_id, d.gk_sp_grade
        """)
        completed_matches = 0
        batch_started = monotonic()
        log(f"export: 센터백 듀오 GK 카드·강화 0/{len(cards):,}개 완료 (0.0%) · 예상 남은 시간 계산 중")
        for offset in range(0, len(cards), _DEFENSE_BATCH_SIZE):
            batch = cards[offset:offset + _DEFENSE_BATCH_SIZE]
            rows.extend(con.execute(f"""
                WITH totals AS (
                    SELECT {_DUO_KEYS}, sum(shots) AS shots, sum(saves) AS saves,
                           sum(goals) AS goals, sum(gsax_shots) AS gsax_shots, sum(gsax) AS gsax
                    FROM _gksave_duo_input
                    JOIN (SELECT unnest(?::BIGINT[]) AS gk_sp_id,
                                 unnest(?::INTEGER[]) AS gk_sp_grade) batch
                    USING (gk_sp_id, gk_sp_grade)
                    WHERE gk_sp_id BETWEEN ? AND ? GROUP BY {_DUO_KEYS}
                )
                SELECT c.*, t.shots, t.saves, t.goals, t.gsax_shots, t.gsax
                FROM _gksave_duo_candidates c JOIN totals t USING ({_DUO_KEYS})
            """, [[c[0] for c in batch], [c[1] for c in batch], batch[0][0], batch[-1][0]]).fetchall())
            completed_matches += sum(c[2] for c in batch)
            done = offset + len(batch)
            remaining = (monotonic() - batch_started) * (total_matches - completed_matches) / completed_matches
            log(f"export: 센터백 듀오 GK 카드·강화 {done:,}/{len(cards):,}개 완료 ({done / len(cards) * 100:.1f}%) · "
                f"출전 경기 {completed_matches:,}/{total_matches:,}건 · 듀오 {len(rows):,}개 · "
                f"단계 경과 {format_duration(monotonic() - started)} · "
                f"예상 남은 시간 약 {format_duration(remaining)}")
    finally:
        con.execute("DROP TABLE IF EXISTS temp.main._gksave_duo_input")
    rows.sort(key=lambda r: (-r[6], *r[:6]))
    return [{
        "gk_sp_id": r[0], "grade": r[1],
        "cb1_sp_id": r[2], "cb1_grade": r[3], "cb2_sp_id": r[4], "cb2_grade": r[5],
        "matches": r[6], "users": r[7], "shots": r[8], "saves": r[9], "goals": r[10],
        "gsax_shots": r[11], "gsax_per_shot": r[12] / r[11] if r[11] else None,
        "save_pct": _save_pct(r[9], r[10]), "shots_per_match": r[8] / r[6],
        "goals_per_match": r[10] / r[6],
    } for r in rows]


def defense_combinations(
    con: duckdb.DuckDBPyConnection, *, gate: int = MIN_MATCHES_GATE,
    min_users: int = 5, since: datetime | None = None,
) -> list[dict[str, Any]]:
    """전체 표본 모델을 사용하는 센터백 듀오 통계. 사용자 식별자는 반환하지 않는다."""
    with _match_shots(con, since, defense_gate=gate, min_users=min_users):
        return _defense_from_match_shots(con)


def export_rankings(
    con: duckdb.DuckDBPyConnection, *, eligible_keys: set[tuple[int, int]],
    gate: int = MIN_MATCHES_GATE, since: datetime | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """공통 모델을 한 연결에서 한 번 준비하고 GSAx와 듀오 통계를 함께 반환한다."""
    log = log or (lambda _: None)
    if not eligible_keys:
        log("export: 순위 대상 없음 — 공통 모델·센터백 듀오 계산 생략")
        return [], []
    with _match_shots(con, since, eligible_keys=eligible_keys, defense_gate=gate, log=log):
        log("export: GSAx 카드 순위 집계 중 (공통 모델 재사용)")
        started = monotonic()
        gsax = _gsax_from_match_shots(con, gate=gate, since=since)
        log(f"export: GSAx 카드 순위 집계 완료 · 경과 {format_duration(monotonic() - started)}")
        combos = _defense_from_match_shots(con, log=log)
        return gsax, combos


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
        {"type": t, "name": shot_type_name(t), "shots": n, "saves": sv,
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


def zone_breakdown_all(
    con: duckdb.DuckDBPyConnection, *, since: datetime | None = None,
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """모든 (sp_id, 강화)의 거리 존별 선방률을 한 번에. 키=(gk_sp_id, grade)."""
    dp, params = _date_pred(since, first=False)
    c = PITCH_SCALE_M
    lo, mid, hi = ZONE_CUTS_M
    rows = con.execute(
        f"""
        WITH s AS (
            SELECT gk_sp_id, gk_sp_grade, result,
                   CASE WHEN d < {lo} THEN 0 WHEN d < {mid} THEN 1
                        WHEN d < {hi} THEN 2 ELSE 3 END AS zone
            FROM (SELECT gk_sp_id, gk_sp_grade, result,
                         sqrt((1-x)*(1-x)+(0.5-y)*(0.5-y)) * {c} AS d
                  FROM shot WHERE NOT is_pk AND x IS NOT NULL{dp})
        )
        SELECT gk_sp_id, gk_sp_grade, zone, count(*),
               sum(CASE WHEN result = 1 THEN 1 ELSE 0 END)
        FROM s GROUP BY 1, 2, 3
        """,
        params,
    ).fetchall()
    acc: dict[tuple[int, int], dict[int, tuple[int, int]]] = {}
    for sp, gr, zone, n, sv in rows:
        acc.setdefault((sp, gr), {})[zone] = (n, sv)
    out: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for key, zmap in acc.items():
        out[key] = [
            {"zone": ZONE_NAMES[z], "shots": zmap.get(z, (0, 0))[0],
             "saves": zmap.get(z, (0, 0))[1],
             "save_pct": _save_pct(zmap.get(z, (0, 0))[1],
                                   zmap.get(z, (0, 0))[0] - zmap.get(z, (0, 0))[1])}
            for z in range(4)
        ]
    return out


def type_breakdown_all(
    con: duckdb.DuckDBPyConnection, *, since: datetime | None = None,
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """모든 (sp_id, 강화)의 타입별 선방률을 한 번에. 키=(gk_sp_id, grade)."""
    dp, params = _date_pred(since, first=False)
    rows = con.execute(
        f"""
        SELECT gk_sp_id, gk_sp_grade, shot_type, count(*),
               sum(CASE WHEN result = 1 THEN 1 ELSE 0 END)
        FROM shot WHERE NOT is_pk{dp} GROUP BY 1, 2, 3
        """,
        params,
    ).fetchall()
    acc: dict[tuple[int, int], list[tuple]] = {}
    for sp, gr, t, n, sv in rows:
        acc.setdefault((sp, gr), []).append((t, n, sv))
    out: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for key, lst in acc.items():
        lst.sort(key=lambda r: r[1], reverse=True)
        out[key] = [
            {"type": t, "name": shot_type_name(t), "shots": n, "saves": sv,
             "save_pct": _save_pct(sv, n - sv)}
            for t, n, sv in lst
        ]
    return out


def _ratio(a, b):
    return a / b if b else None


def card_extras_all(
    con: duckdb.DuckDBPyConnection, *, since: datetime | None = None,
) -> dict[tuple[int, int], dict[str, Any]]:
    """모든 (sp_id, 강화)의 상황별·수비맥락·GK 본인 스탯을 한 번에. 키=(gk_sp_id, grade).

    상황별(명세서 §1·§5): 박스 안/밖·1대1(독대)·연계/컷백 선방률.
    수비맥락(§2·§4): 경기당 마주한 유효슛(노출도), 마주한 평균 거리, 실점 평균 거리.
    GK 본인(§6): 엔진 평점·패스 성공률·공중볼 성공률.
    """
    dp_shot, p_shot = _date_pred(since, first=False)
    dp_gk, p_gk = _date_pred(since, first=True)
    c = PITCH_SCALE_M

    shot_rows = con.execute(
        f"""
        WITH s AS (
            SELECT gk_sp_id, gk_sp_grade, result, in_penalty, assist,
                   sqrt((1-x)*(1-x)+(0.5-y)*(0.5-y)) * {c} AS dm
            FROM shot WHERE NOT is_pk AND x IS NOT NULL{dp_shot}
        )
        SELECT gk_sp_id, gk_sp_grade, count(*),
               sum(CASE WHEN in_penalty THEN 1 ELSE 0 END),
               sum(CASE WHEN in_penalty AND result=1 THEN 1 ELSE 0 END),
               sum(CASE WHEN NOT in_penalty THEN 1 ELSE 0 END),
               sum(CASE WHEN NOT in_penalty AND result=1 THEN 1 ELSE 0 END),
               sum(CASE WHEN in_penalty AND NOT assist THEN 1 ELSE 0 END),
               sum(CASE WHEN in_penalty AND NOT assist AND result=1 THEN 1 ELSE 0 END),
               sum(CASE WHEN assist THEN 1 ELSE 0 END),
               sum(CASE WHEN assist AND result=1 THEN 1 ELSE 0 END),
               avg(dm), avg(CASE WHEN result=3 THEN dm END)
        FROM s GROUP BY 1, 2
        """,
        p_shot,
    ).fetchall()

    gk_rows = con.execute(
        f"""
        SELECT gk_sp_id, gk_sp_grade, count(DISTINCT match_id),
               avg(sp_rating), sum(pass_success), sum(pass_try),
               sum(aerial_success), sum(aerial_try)
        FROM gk_match{dp_gk} GROUP BY 1, 2
        """,
        p_gk,
    ).fetchall()

    out: dict[tuple[int, int], dict[str, Any]] = {}
    for r in shot_rows:
        (sp, gr, shots, in_n, in_sv, out_n, out_sv, u_n, u_sv, a_n, a_sv,
         faced, conceded) = r
        out[(sp, gr)] = {
            "shots": shots,
            "in_pen_save": _ratio(in_sv, in_n), "out_pen_save": _ratio(out_sv, out_n),
            "unassisted_save": _ratio(u_sv, u_n), "assisted_save": _ratio(a_sv, a_n),
            # 분모(슛 수) — 상황별 정렬에서 표본 적은 카드를 거르는 게이트에 쓴다.
            "in_pen_shots": in_n, "out_pen_shots": out_n,
            "unassisted_shots": u_n, "assisted_shots": a_n,
            "faced_dist_m": round(faced, 1) if faced is not None else None,
            "conceded_dist_m": round(conceded, 1) if conceded is not None else None,
        }
    for sp, gr, matches, rating, ps, pt, asuc, atr in gk_rows:
        d = out.setdefault((sp, gr), {"shots": 0})
        d["matches"] = matches
        d["exposure"] = _ratio(d.get("shots", 0), matches)   # 경기당 유효슛
        d["gk_rating"] = round(rating, 2) if rating is not None else None
        d["pass_pct"] = _ratio(ps, pt)
        d["aerial_pct"] = _ratio(asuc, atr)
        d["aerial_try"] = atr
        d["aerial_success"] = asuc
    return out


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
    # 평균의 표준오차(SE) — 95% 신뢰구간이 0을 포함하면 '유의미한 차이 없음'.
    # 강화 효과는 페어 표본이 얇아 노이즈가 커서, 맨숫자만 보면 방향을 오해하기 쉽다.
    se = None
    if mean_delta is not None and len(deltas) >= 2:
        var = sum((d - mean_delta) ** 2 for d in deltas) / len(deltas)  # 모분산
        se = (var / len(deltas)) ** 0.5
    up = sum(1 for d in deltas if d > 1e-9)
    down = sum(1 for d in deltas if d < -1e-9)
    return {
        "paired_users": len([c for c in cells.values() if len(c) >= 2]),
        "pairs": pairs,
        "mean_save_pct_delta_per_grade": mean_delta,
        "se_per_grade": se,
        "up_pairs": up,
        "down_pairs": down,
        "note": (
            "같은 유저·같은 카드에서 강화단계가 1 오를 때 평균 선방률 변화. "
            "유저 실력 교란이 페어 안에서 상쇄됨. 표본 적으면 신뢰 낮음. "
            "95% 신뢰구간(평균±1.96·SE)이 0을 포함하면 유의미한 차이 없음."
        ),
    }
