"""수집기 (T1 시드 + T2 스노우볼 BFS + T3 복원력).

전략: /v1/match 전역 피드로 시드 매치를 잡고, 각 match-detail에서 양 팀
ouid를 harvest 해 frontier 큐에 넣은 뒤, 그 ouid들의 /v1/user/match로
BFS 확장한다. frontier와 raw_match가 DuckDB에 영속되므로 크롤이 중간에
끊겨도 다시 실행하면 pending 상태부터 이어서 재개한다.

dedup: matchId는 raw_match PK, ouid는 frontier PK로 자동 중복 제거.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import duckdb

from . import api
from .codec import encode_payload
from .config import COLLECT_WINDOW_DAYS, DEFAULT, MATCHTYPE_OFFICIAL, Settings
from .db import have_match
from .http import ApiError, AsyncResilientClient, ResilientClient
from .parse import parse_match_date

Logger = Callable[[str], None]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _default_since() -> datetime:
    """--since/--days 미지정 시 수집 하한. naive UTC (match_date 와 같은 기준)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now - timedelta(days=COLLECT_WINDOW_DAYS)


def _pct(n: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{n / total * 100:.1f}%"


def _log_progress(
    con: duckdb.DuckDBPyConnection,
    *,
    stored: int,
    max_new_matches: int,
    log: Logger,
) -> None:
    done = con.execute(
        "SELECT count(*) FROM frontier WHERE state = 'done'"
    ).fetchone()[0]
    total = con.execute("SELECT count(*) FROM frontier").fetchone()[0]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(
        f"{ts} | 매치 {stored:,}/{max_new_matches:,} ({_pct(stored, max_new_matches)}) "
        f"· 유저 {done:,}/{total:,} ({_pct(done, total)})"
    )


def match_id_time(match_id: str) -> datetime | None:
    """matchId(ObjectId) 앞 4바이트 = 생성 unix초 → naive UTC datetime.

    matchDate 와 ±10분 이내라, match-detail 을 받기 전에 날짜 컷오프에 쓸 수 있다.
    """
    try:
        ts = int(match_id[:8], 16)
        return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


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
        "INSERT INTO raw_match (match_id, match_date, payload) VALUES (?, ?, ?) "
        "ON CONFLICT DO NOTHING",
        [match_id, parse_match_date(detail), encode_payload(detail)],
    )
    _harvest_ouids(con, detail)
    return True


def reset_done(con: duckdb.DuckDBPyConnection) -> int:
    """갱신 모드: done 상태 ouid 를 pending 으로 되돌려 새 경기를 다시 줍는다.

    옛 매치는 raw_match dedup 으로 스킵되므로 중복은 생기지 않는다.
    """
    n = con.execute("SELECT count(*) FROM frontier WHERE state = 'done'").fetchone()[0]
    con.execute("UPDATE frontier SET state = 'pending' WHERE state = 'done'")
    return n


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
    since: datetime | None = None,
    log: Logger = _log,
) -> int:
    """frontier의 pending ouid를 BFS로 소모하며 유저별 매치로 확장.

    max_new_matches 개의 신규 매치를 모으면 멈춘다. frontier는 영속이라
    다음 실행 때 남은 pending부터 재개된다. since 를 주면 그 날짜보다 오래된
    매치는 받지 않는다(user/match는 최신순이라 옛 매치에 닿으면 그 유저는 중단).
    """
    stored = 0
    while stored < max_new_matches:
        row = con.execute(
            "SELECT ouid FROM frontier WHERE state = 'pending' LIMIT 1"
        ).fetchone()
        if row is None:
            _log_progress(con, stored=stored, max_new_matches=max_new_matches, log=log)
            log(f"{datetime.now():%Y-%m-%d %H:%M:%S} | 유저 큐 소진")
            break
        ouid = row[0]
        reached_old = False
        for p in range(user_pages):
            try:
                ids = api.list_user_matches(client, ouid, offset=p * limit, limit=limit)
            except ApiError as e:
                log(f"{datetime.now():%Y-%m-%d %H:%M:%S} | user/match 오류(ouid={ouid[:8]}…): {e}")
                break
            if not ids:
                break
            for mid in ids:
                if since is not None:
                    t = match_id_time(mid)
                    if t is not None and t < since:
                        reached_old = True  # 최신순이라 이후는 더 오래됨
                        break
                if _store_match(con, client, mid):
                    stored += 1
                    if stored >= max_new_matches:
                        break
            if reached_old or stored >= max_new_matches:
                break
        con.execute("UPDATE frontier SET state = 'done' WHERE ouid = ?", [ouid])
        _log_progress(con, stored=stored, max_new_matches=max_new_matches, log=log)
    return stored


# ── 동시 요청(async) 수집 ──────────────────────────────────────

async def _a_user_matches(client: AsyncResilientClient, ouid: str, offset: int, limit: int):
    return await client.get(
        "/fconline/v1/user/match",
        {"ouid": ouid, "matchtype": MATCHTYPE_OFFICIAL, "offset": offset, "limit": limit},
    )


async def _a_detail(client: AsyncResilientClient, mid: str):
    try:
        return await client.get("/fconline/v1/match-detail", {"matchid": mid})
    except ApiError:
        return None


async def snowball_async(
    con: duckdb.DuckDBPyConnection,
    client: AsyncResilientClient,
    *,
    max_new_matches: int = 5000,
    user_pages: int = 3,
    limit: int = 100,
    since: datetime | None = None,
    log: Logger = _log,
) -> int:
    """스노우볼 — 한 ouid의 신규 match-detail 을 동시에 가져와 지연을 겹친다.

    user/match 리스트는 순차(콜 적음), match-detail(대다수)은 동시 fetch 로
    레이트 예산을 꽉 채운다. dedup·frontier·재개는 동기 버전과 동일.
    """
    stored = 0
    while stored < max_new_matches:
        row = con.execute(
            "SELECT ouid FROM frontier WHERE state = 'pending' LIMIT 1"
        ).fetchone()
        if row is None:
            _log_progress(con, stored=stored, max_new_matches=max_new_matches, log=log)
            log(f"{datetime.now():%Y-%m-%d %H:%M:%S} | 유저 큐 소진")
            break
        ouid = row[0]

        new_ids: list[str] = []
        reached_old = False
        for p in range(user_pages):
            try:
                ids = await _a_user_matches(client, ouid, p * limit, limit)
            except ApiError as e:
                log(f"{datetime.now():%Y-%m-%d %H:%M:%S} | user/match 오류(ouid={ouid[:8]}…): {e}")
                break
            if not ids:
                break
            for mid in ids:
                if since is not None:
                    t = match_id_time(mid)
                    if t is not None and t < since:
                        reached_old = True
                        break
                if not have_match(con, mid):
                    new_ids.append(mid)
            if reached_old:
                break

        # 신규 매치 상세를 동시에 가져오기
        results = await asyncio.gather(*(_a_detail(client, m) for m in new_ids)) if new_ids else []
        for mid, detail in zip(new_ids, results):
            if detail is None or have_match(con, mid):
                continue
            con.execute(
                "INSERT INTO raw_match (match_id, match_date, payload) VALUES (?, ?, ?) "
                "ON CONFLICT DO NOTHING",
                [mid, parse_match_date(detail), encode_payload(detail)],
            )
            _harvest_ouids(con, detail)
            stored += 1
            if stored >= max_new_matches:
                break

        con.execute("UPDATE frontier SET state = 'done' WHERE ouid = ?", [ouid])
        _log_progress(con, stored=stored, max_new_matches=max_new_matches, log=log)
    return stored


async def run_async(
    settings: Settings = DEFAULT,
    *,
    seed_nicknames: list[str] | None = None,
    max_new_matches: int = 5000,
    since: datetime | None = None,
    refresh: bool = False,
    concurrency: int = 10,
    log: Logger = _log,
) -> None:
    """동시 요청 수집. 동기 run 과 동작 동일하되 match-detail 을 병렬 fetch."""
    from .db import connect, raw_match_count

    if since is None:
        since = _default_since()
    log(f"수집 하한 날짜: {since.date()} 이전 제외 · 동시성 {concurrency}")

    con = connect(settings)
    try:
        async with AsyncResilientClient(settings, concurrency=concurrency) as client:
            for nick in seed_nicknames or []:
                try:
                    ouid = (await client.get("/fconline/v1/id", {"nickname": nick}))["ouid"]
                except ApiError as e:
                    log(f"[seed] 닉네임 '{nick}' → ouid 실패: {e}")
                    continue
                con.execute(
                    "INSERT INTO frontier (ouid, state) VALUES (?, 'pending') ON CONFLICT DO NOTHING",
                    [ouid],
                )
                log(f"[seed] '{nick}' → ouid {ouid[:8]}… 큐 추가")
            if refresh:
                n = reset_done(con)
                log(f"=== 갱신 모드: done ouid {n}개를 pending 으로 ===")
            pending = con.execute(
                "SELECT count(*) FROM frontier WHERE state = 'pending'"
            ).fetchone()[0]
            if pending == 0:
                log("시드도 없고 pending ouid도 없음 — 닉네임을 넘겨 시드하세요.")
                return
            log("=== 스노우볼 확장 (동시) ===")
            await snowball_async(con, client, max_new_matches=max_new_matches, since=since, log=log)
        log(f"총 raw_match: {raw_match_count(con)}건")
    finally:
        con.close()


def run(
    settings: Settings = DEFAULT,
    *,
    seed_nicknames: list[str] | None = None,
    max_new_matches: int = 5000,
    since: datetime | None = None,
    refresh: bool = False,
    log: Logger = _log,
) -> None:
    """닉네임 시드 → 스노우볼 확장. frontier가 이미 차 있으면 시드 없이도 재개된다.

    refresh=True 면 이미 처리한(done) ouid 를 다시 열어 새 경기를 보충한다.
    since 를 주면 그 날짜 이후 매치만 수집한다.
    """
    from .db import connect, raw_match_count

    # 수집 하한 날짜: --since/--days 미지정이면 롤링 COLLECT_WINDOW_DAYS 적용
    if since is None:
        since = _default_since()
    log(f"수집 하한 날짜: {since.date()} 이전 매치 제외")

    con = connect(settings)
    try:
        with ResilientClient(settings) as client:
            if seed_nicknames:
                log("=== 시드(닉네임→ouid) ===")
                seed_from_nicknames(con, client, seed_nicknames, log=log)
            if refresh:
                n = reset_done(con)
                log(f"=== 갱신 모드: done ouid {n}개를 pending 으로 되돌림 ===")
            pending = con.execute(
                "SELECT count(*) FROM frontier WHERE state = 'pending'"
            ).fetchone()[0]
            if pending == 0:
                log("시드도 없고 pending ouid도 없음 — 닉네임을 넘겨 시드하세요.")
                return
            log("=== 스노우볼 확장 ===")
            snowball(con, client, max_new_matches=max_new_matches, since=since, log=log)
        log(f"총 raw_match: {raw_match_count(con)}건")
    finally:
        con.close()
