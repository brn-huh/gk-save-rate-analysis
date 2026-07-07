"""DuckDB 스키마와 연결.

단일 파일에 3 레이어 + 수집 상태를 담는다.
  raw_match  : 원본 무손실 (match_id PK로 자동 dedup)
  frontier   : 스노우볼 BFS 큐를 영속화 (끊겨도 재개)
  shot       : 원본 파싱 결과 (재파싱으로 재생성 가능)
  card_agg   : 카드×강화 집계 (재집계로 재생성 가능)

raw_match 경계 덕에 지표를 추가할 때 원본을 다시 긁지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .config import DEFAULT, Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_match (
    match_id   VARCHAR PRIMARY KEY,
    match_date TIMESTAMP,          -- payload.matchDate (UTC)
    payload    JSON     NOT NULL,
    fetched_at TIMESTAMP DEFAULT now()
);

-- 스노우볼 프론티어: harvest 한 ouid를 영속 큐로 관리
CREATE TABLE IF NOT EXISTS frontier (
    ouid     VARCHAR PRIMARY KEY,
    state    VARCHAR DEFAULT 'pending',   -- pending | done
    added_at TIMESTAMP DEFAULT now()
);

-- 메타데이터 캐시 (정적 파일 /static/fconline/meta/{spid,seasonid}.json)
CREATE TABLE IF NOT EXISTS meta_spid (
    sp_id BIGINT PRIMARY KEY,
    name  VARCHAR
);
CREATE TABLE IF NOT EXISTS meta_season (
    season_id  INTEGER PRIMARY KEY,
    class_name VARCHAR
);

-- GK 출전: 카드가 GK로 뛴 경기 1건 = 1행 (슛 0개 경기도 표본 게이트에 반영)
CREATE TABLE IF NOT EXISTS gk_match (
    match_id       VARCHAR,
    match_date     TIMESTAMP,
    gk_ouid        VARCHAR,
    gk_sp_id       BIGINT,
    gk_sp_grade    INTEGER,
    sp_rating      DOUBLE,      -- GK 엔진 평점 (player.status.spRating)
    pass_try       INTEGER,
    pass_success   INTEGER,
    aerial_try     INTEGER,
    aerial_success INTEGER
);

-- 파싱 결과: 우리 GK가 마주한 상대 유효슛 1개 = 1행
CREATE TABLE IF NOT EXISTS shot (
    match_id     VARCHAR,
    match_date   TIMESTAMP,
    gk_ouid      VARCHAR,
    gk_sp_id     BIGINT,
    gk_sp_grade  INTEGER,
    shot_type    INTEGER,
    result       INTEGER,          -- 1 선방 / 3 실점
    is_pk        BOOLEAN,          -- type==9
    in_penalty   BOOLEAN,
    assist       BOOLEAN,
    hit_post     BOOLEAN,
    x            DOUBLE,
    y            DOUBLE
);

-- ── 조회 편의 뷰 (이름·시즌·강화 조인) ──────────────────────────
-- 시즌은 gk_sp_id 앞자리와 meta_season.season_id 의 longest-prefix 매칭.

CREATE VIEW IF NOT EXISTS shot_readable AS
SELECT s.match_id, s.match_date,
       sp.name AS player_name,
       (SELECT m.class_name FROM meta_season m
         WHERE CAST(s.gk_sp_id AS VARCHAR) LIKE CAST(m.season_id AS VARCHAR) || '%'
         ORDER BY length(CAST(m.season_id AS VARCHAR)) DESC LIMIT 1) AS season,
       s.gk_sp_id, s.gk_sp_grade AS grade,
       s.result, s.is_pk, s.shot_type, s.in_penalty, s.assist, s.hit_post,
       s.x, s.y, s.gk_ouid
FROM shot s
LEFT JOIN meta_spid sp ON sp.sp_id = s.gk_sp_id;

CREATE VIEW IF NOT EXISTS card_stats AS
WITH mm AS (
    SELECT gk_sp_id, gk_sp_grade, count(DISTINCT match_id) AS matches
    FROM gk_match GROUP BY 1, 2
),
ss AS (
    SELECT gk_sp_id, gk_sp_grade,
           sum(CASE WHEN result = 1 THEN 1 ELSE 0 END) AS saves,
           sum(CASE WHEN result = 3 THEN 1 ELSE 0 END) AS goals
    FROM shot WHERE NOT is_pk GROUP BY 1, 2
)
SELECT sp.name AS player_name,
       (SELECT m.class_name FROM meta_season m
         WHERE CAST(mm.gk_sp_id AS VARCHAR) LIKE CAST(m.season_id AS VARCHAR) || '%'
         ORDER BY length(CAST(m.season_id AS VARCHAR)) DESC LIMIT 1) AS season,
       mm.gk_sp_id, mm.gk_sp_grade AS grade, mm.matches,
       COALESCE(ss.saves, 0) AS saves, COALESCE(ss.goals, 0) AS goals,
       CASE WHEN COALESCE(ss.saves, 0) + COALESCE(ss.goals, 0) > 0
            THEN round(100.0 * ss.saves / (ss.saves + ss.goals), 1) END AS save_pct
FROM mm
LEFT JOIN ss USING (gk_sp_id, gk_sp_grade)
LEFT JOIN meta_spid sp ON sp.sp_id = mm.gk_sp_id
ORDER BY mm.matches DESC;
"""


# 구버전 DB 파일에 없을 수 있는 컬럼 (있으면 무시). 이름 지정 INSERT라 위치는 무관.
_MIGRATIONS = (
    "ALTER TABLE raw_match ADD COLUMN IF NOT EXISTS match_date TIMESTAMP",
    "ALTER TABLE gk_match  ADD COLUMN IF NOT EXISTS match_date TIMESTAMP",
    "ALTER TABLE shot      ADD COLUMN IF NOT EXISTS match_date TIMESTAMP",
    "ALTER TABLE gk_match  ADD COLUMN IF NOT EXISTS sp_rating DOUBLE",
    "ALTER TABLE gk_match  ADD COLUMN IF NOT EXISTS pass_try INTEGER",
    "ALTER TABLE gk_match  ADD COLUMN IF NOT EXISTS pass_success INTEGER",
    "ALTER TABLE gk_match  ADD COLUMN IF NOT EXISTS aerial_try INTEGER",
    "ALTER TABLE gk_match  ADD COLUMN IF NOT EXISTS aerial_success INTEGER",
    # 증분 빌드: 파싱 완료된 매치 추적 (NULL = 미파싱)
    "ALTER TABLE raw_match ADD COLUMN IF NOT EXISTS parsed_at TIMESTAMP",
)


def _migrate(con: duckdb.DuckDBPyConnection) -> None:
    for stmt in _MIGRATIONS:
        try:
            con.execute(stmt)
        except Exception:  # noqa: BLE001 - 이미 있거나 지원 안 하면 무시
            pass


def connect(settings: Settings = DEFAULT, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path: Path = settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
        _migrate(con)
    return con


def connect_memory() -> duckdb.DuckDBPyConnection:
    """테스트용 인메모리 연결 (스키마 + 마이그레이션 적용)."""
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    _migrate(con)
    return con


def have_match(con: duckdb.DuckDBPyConnection, match_id: str) -> bool:
    row = con.execute("SELECT 1 FROM raw_match WHERE match_id = ?", [match_id]).fetchone()
    return row is not None


def raw_match_count(con: duckdb.DuckDBPyConnection) -> int:
    return con.execute("SELECT count(*) FROM raw_match").fetchone()[0]
