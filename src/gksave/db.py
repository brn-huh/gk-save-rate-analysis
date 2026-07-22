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
    payload    BLOB     NOT NULL,  -- zlib-6 압축 JSON (codec.encode_payload). 20.8KB → 2.77KB
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

-- 선수 부가정보 캐시 (fc-info GK 검색). 우리 gk_sp_id 만, 카드당 1회.
-- 급여·기본OVR 은 카드(spid)별, 키·몸무게·체형은 실선수 속성. 반복 수집 안 함.
CREATE TABLE IF NOT EXISTS player_info (
    spid       BIGINT PRIMARY KEY,
    name       VARCHAR,
    salary     INTEGER,
    ovr        INTEGER,      -- 기본(1강) OVR
    height     INTEGER,      -- cm
    weight     INTEGER,      -- kg
    body_type  VARCHAR,      -- 보통 | 건장 | 마름
    fetched_at TIMESTAMP DEFAULT now()
);

-- 시즌 엠블럼 이미지 (season_id → 넥슨 CDN URL). fc-info classImg 에서 얻어 매핑만 저장,
-- 이미지 자체는 ssl.nexon.com 에서 직접 로드. 시즌은 ~149개뿐이라 가볍다.
CREATE TABLE IF NOT EXISTS season_img (
    season_id INTEGER PRIMARY KEY,
    img       VARCHAR
);

-- 선수 국적 (pid → 국가). fc-info 상세페이지에서 pid당 1회 수집. 국가는 시즌 무관 선수 속성이라
-- spid(카드) 아닌 pid(실선수) 키. nation_code 로 넥슨 CDN 국기(countries/smallflags/{code}.png) 로드.
CREATE TABLE IF NOT EXISTS player_bio (
    pid         BIGINT PRIMARY KEY,
    nation_code INTEGER,
    nation_name VARCHAR,
    fetched_at  TIMESTAMP DEFAULT now()
);

-- 선수 클럽 이력 (pid → 클럽명들, 표기 순서 ord). 한 선수에 여러 클럽 = 여러 행.
-- 클럽으로 선수를 역검색하기 위한 구조. 중복 클럽명은 수집 단계에서 제거.
CREATE TABLE IF NOT EXISTS player_club (
    pid       BIGINT,
    ord       INTEGER,
    club_name VARCHAR,
    PRIMARY KEY (pid, ord)
);

-- 선수 특성(트레잇) (spid → 특성들, 표기 순서 ord). 특성은 시즌(카드)마다 달라 spid 키.
-- trait_code 로 넥슨 CDN 아이콘(traits/trait_icon_{code}.png) 생성. is_new=신규특성(금색 배경).
CREATE TABLE IF NOT EXISTS player_trait (
    spid       BIGINT,
    ord        INTEGER,
    trait_code INTEGER,
    trait_name VARCHAR,
    is_new     BOOLEAN,
    PRIMARY KEY (spid, ord)
);

-- fc-info 조회 시도 기록(negative cache). kind: 'info'(검색) | 'detail'(상세페이지).
-- 미등재 카드나 특성 0개 카드는 결과 테이블에 남는 행이 없어, 결과만 보면 매번 재조회하게 된다.
-- "언제 받아봤는지"를 남겨 config.FC_RECHECK_DAYS 동안 건너뛴다. 성공한 조회만 기록한다
-- (일시적 실패까지 기록하면 그 카드는 TTL 동안 못 받는다).
CREATE TABLE IF NOT EXISTS fc_fetch_log (
    kind       VARCHAR,
    spid       BIGINT,
    fetched_at TIMESTAMP DEFAULT now(),
    PRIMARY KEY (kind, spid)
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


class DbLockedError(duckdb.IOException):
    """다른 프로세스가 DB 쓰기 락을 잡고 있다.

    duckdb.IOException 을 상속해, 이 예외를 모르는 호출부도 기존대로 잡을 수 있다.
    """


def connect(settings: Settings = DEFAULT, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path: Path = settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        con = duckdb.connect(str(path), read_only=read_only)
    except duckdb.IOException as e:
        # DuckDB 단일 파일이라 쓰기 프로세스는 하나뿐이고, 읽기 전용도 쓰기 락과
        # 공존하지 못한다. 원문은 트레이스백뿐이라 무엇을 해야 할지 알려준다.
        if "Conflicting lock" not in str(e):
            raise
        raise DbLockedError(
            f"다른 프로세스가 DB 를 쓰고 있다: {path}\n"
            f"  collect · build · export 는 동시에 못 돈다. 끝날 때까지 기다리거나\n"
            f"  해당 프로세스를 멈춰라 (ps aux | grep gksave).\n"
            f"  원문: {e}"
        ) from e
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
