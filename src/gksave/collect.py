"""수집기 (T1 시드 + T2 스노우볼 BFS + T3 복원력).

전략: /v1/match 전역 피드로 시드 매치를 잡고, 각 match-detail에서 양 팀
ouid를 harvest 해 frontier 큐에 넣은 뒤, 그 ouid들의 /v1/user/match로
BFS 확장한다. frontier와 raw_match가 DuckDB에 영속되므로 크롤이 중간에
끊겨도 다시 실행하면 pending 상태부터 이어서 재개한다.

dedup: matchId는 raw_match PK, ouid는 frontier PK로 자동 중복 제거.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import duckdb

from . import api
from .config import DEFAULT, Settings
from .db import have_match
from .http import ApiError, ResilientClient

Logger = Callable[[str], None]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _harvest_ouids(con: duckdb.DuckDBPyConnection, detail: dict[str, Any]) -> None:
    for info in detail.get("matchInfo", []):
        ouid = info.get("ouid")
        if not ouid:
            continue
        con.execute(
            "INSERT INTO frontier (ouid, state) VALUES (?, 'pending') ON CONFLICT DO NOTHING",
            [ouid],
        )


def _store_match(
    con: duckdb.DuckDBPyConnection, client: ResilientClient, match_id: str
) -> bool:
    """match-detail을 받아 raw_match에 저장하고 ouid를 harvest. 이미 있으면 False."""
    if have_match(con, match_id):
        return False
    try:
        detail = api.get_match_detail(client, match_id)
    except ApiError as e:
        _log(f"  match-detail 실패({match_id}): {e}")
        return False
    con.execute(
        "INSERT INTO raw_match (match_id, payload) VALUES (?, ?) ON CONFLICT DO NOTHING",
        [match_id, json.dumps(detail, ensure_ascii=False)],
    )
    _harvest_ouids(con, detail)
    return True


def seed_from_nicknames(
    con: duckdb.DuckDBPyConnection,
    client: ResilientClient,
    nicknames: list[str],
    *,
    log: Logger = _log,
) -> int:
    """닉네임들을 ouid로 바꿔 frontier에 시드로 넣는다.

    T0 실측 결과 전역 피드(/v1/match)의 matchId는 match-detail 로 안 풀린다(400).
    유효 경로는 닉네임 → /v1/id → ouid → /v1/user/match 뿐이므로, 시드는
    ouid 로만 심고 나머지는 스노우볼(snowball)이 user/match 로 확장한다.
    """
    added = 0
    for nick in nicknames:
        try:
            ouid = api.get_ouid(client, nick)
        except ApiError as e:
            log(f"[seed] 닉네임 '{nick}' → ouid 실패: {e}")
            continue
        con.execute(
            "INSERT INTO frontier (ouid, state) VALUES (?, 'pending') ON CONFLICT DO NOTHING",
            [ouid],
        )
        added += 1
        log(f"[seed] '{nick}' → ouid {ouid[:8]}… 큐 추가")
    return added


def snowball(
    con: duckdb.DuckDBPyConnection,
    client: ResilientClient,
    *,
    max_new_matches: int = 5000,
    user_pages: int = 3,
    limit: int = 100,
    log: Logger = _log,
) -> int:
    """frontier의 pending ouid를 BFS로 소모하며 유저별 매치로 확장.

    max_new_matches 개의 신규 매치를 모으면 멈춘다. frontier는 영속이라
    다음 실행 때 남은 pending부터 재개된다.
    """
    stored = 0
    while stored < max_new_matches:
        row = con.execute(
            "SELECT ouid FROM frontier WHERE state = 'pending' LIMIT 1"
        ).fetchone()
        if row is None:
            log("[snowball] pending ouid 소진 — 완료")
            break
        ouid = row[0]
        for p in range(user_pages):
            try:
                ids = api.list_user_matches(client, ouid, offset=p * limit, limit=limit)
            except ApiError as e:
                log(f"[snowball] user/match 오류(ouid={ouid[:8]}…): {e}")
                break
            if not ids:
                break
            for mid in ids:
                if _store_match(con, client, mid):
                    stored += 1
                    if stored >= max_new_matches:
                        break
            if stored >= max_new_matches:
                break
        con.execute("UPDATE frontier SET state = 'done' WHERE ouid = ?", [ouid])
        pending = con.execute(
            "SELECT count(*) FROM frontier WHERE state = 'pending'"
        ).fetchone()[0]
        log(f"[snowball] ouid 완료. 신규매치 누적 {stored} | pending {pending}")
    return stored


def run(
    settings: Settings = DEFAULT,
    *,
    seed_nicknames: list[str] | None = None,
    max_new_matches: int = 5000,
    log: Logger = _log,
) -> None:
    """닉네임 시드 → 스노우볼 확장. frontier가 이미 차 있으면 시드 없이도 재개된다."""
    from .db import connect, raw_match_count

    con = connect(settings)
    try:
        with ResilientClient(settings) as client:
            if seed_nicknames:
                log("=== 시드(닉네임→ouid) ===")
                seed_from_nicknames(con, client, seed_nicknames, log=log)
            pending = con.execute(
                "SELECT count(*) FROM frontier WHERE state = 'pending'"
            ).fetchone()[0]
            if pending == 0:
                log("시드도 없고 pending ouid도 없음 — 닉네임을 넘겨 시드하세요.")
                return
            log("=== 스노우볼 확장 ===")
            snowball(con, client, max_new_matches=max_new_matches, log=log)
        log(f"총 raw_match: {raw_match_count(con)}건")
    finally:
        con.close()
