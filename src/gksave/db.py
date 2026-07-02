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
    match_id     VARCHAR,
    gk_ouid      VARCHAR,
    gk_sp_id     BIGINT,
    gk_sp_grade  INTEGER
);

-- 파싱 결과: 우리 GK가 마주한 상대 유효슛 1개 = 1행
CREATE TABLE IF NOT EXISTS shot (
    match_id     VARCHAR,
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
"""


def connect(settings: Settings = DEFAULT, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path: Path = settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    if not read_only:
        con.execute(SCHEMA)
    return con


def connect_memory() -> duckdb.DuckDBPyConnection:
    """테스트용 인메모리 연결 (스키마 적용)."""
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA)
    return con


def have_match(con: duckdb.DuckDBPyConnection, match_id: str) -> bool:
    row = con.execute("SELECT 1 FROM raw_match WHERE match_id = ?", [match_id]).fetchone()
    return row is not None


def raw_match_count(con: duckdb.DuckDBPyConnection) -> int:
    return con.execute("SELECT count(*) FROM raw_match").fetchone()[0]
