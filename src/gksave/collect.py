"""수집기 (T1 시드 + T2 스노우볼 BFS + T3 복원력).

전략: /v1/match 전역 피드로 시드 매치를 잡고, 각 match-detail에서 양 팀
ouid를 harvest 해 frontier 큐에 넣은 뒤, 그 ouid들의 /v1/user/match로
BFS 확장한다. frontier와 raw_match가 DuckDB에 영속되므로 크롤이 중간에
끊겨도 다시 실행하면 pending 상태부터 이어서 재개한다.

dedup: matchId는 raw_match PK, ouid는 frontier PK로 자동 중복 제거.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
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


def _memory_free_percent() -> float | None:
    """macOS 시스템 메모리 여유율. 센서 온도 대신 메모리 압박을 보여준다."""
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(
            ["/usr/bin/memory_pressure", "-Q"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r"free percentage:\s*(\d+)%", out)
    return float(match.group(1)) if match else None


def _default_since() -> datetime:
    """--since/--days 미지정 시 수집 하한. naive UTC (match_date 와 같은 기준)."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return now - timedelta(days=COLLECT_WINDOW_DAYS)


def _pct(n: int, total: int) -> str:
    if total <= 0:
        return "—"
    return f"{n / total * 100:.1f}%"


@dataclass(frozen=True)
class FrontierCounts:
    done: int
    pending: int
    in_progress: int = 0

    @property
    def total(self) -> int:
        return self.done + self.pending + self.in_progress

    @property
    def done_pct(self) -> float:
        return self.done / self.total * 100 if self.total else 0.0


def frontier_counts(con: duckdb.DuckDBPyConnection) -> FrontierCounts:
    """수집 대기열 요약: 완료·대기 유저 수. 한 번의 GROUP BY 로 센다."""
    rows = dict(
        con.execute("SELECT state, count(*) FROM frontier GROUP BY state").fetchall()
    )
    return FrontierCounts(
        done=rows.get("done", 0),
        pending=rows.get("pending", 0),
        in_progress=rows.get("in_progress", 0),
    )


def _log_progress(
    con: duckdb.DuckDBPyConnection,
    *,
    stored: int,
    max_new_matches: int,
    log: Logger,
) -> None:
    c = frontier_counts(con)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log(
        f"{ts} | 매치 {stored:,}/{max_new_matches:,} ({_pct(stored, max_new_matches)}) "
        f"· 유저 {c.done:,}/{c.total:,} ({_pct(c.done, c.total)}) "
        f"· 처리중 {c.in_progress:,} · 대기 {c.pending:,}"
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


def _add_frontier(con: duckdb.DuckDBPyConnection, ouid: str) -> bool:
    """새 OUID를 frontier에 넣고 실제 신규 삽입 여부를 반환한다."""
    return con.execute(
        "INSERT INTO frontier (ouid, state) VALUES (?, 'pending') "
        "ON CONFLICT DO NOTHING RETURNING ouid",
        [ouid],
    ).fetchone() is not None


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
        if _add_frontier(con, ouid):
            added += 1
            log(f"[seed] '{nick}' → ouid {ouid[:8]}… 큐 추가")
        else:
            log(f"[seed] '{nick}' → ouid {ouid[:8]}… 이미 등록됨")
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
    return await client.get("/fconline/v1/match-detail", {"matchid": mid})


@dataclass
class _UserRun:
    ouid: str
    outstanding: int = 0
    scan_complete: bool = False
    failed: bool = False


@dataclass
class _MatchWork:
    match_id: str
    waiters: set[str] = field(default_factory=set)


def _store_detail(
    con: duckdb.DuckDBPyConnection, match_id: str, detail: dict[str, Any]
) -> bool:
    """raw_match와 frontier 확장을 함께 커밋하고 실제 신규 삽입 여부를 반환한다."""
    con.execute("BEGIN")
    try:
        inserted = con.execute(
            "INSERT INTO raw_match (match_id, match_date, payload) VALUES (?, ?, ?) "
            "ON CONFLICT DO NOTHING RETURNING match_id",
            [match_id, parse_match_date(detail), encode_payload(detail)],
        ).fetchone()
        if inserted is not None:
            _harvest_ouids(con, detail)
        con.execute("COMMIT")
        return inserted is not None
    except BaseException:
        con.execute("ROLLBACK")
        raise


async def snowball_async(
    con: duckdb.DuckDBPyConnection,
    client: AsyncResilientClient,
    *,
    max_new_matches: int = 5000,
    user_pages: int = 3,
    limit: int = 100,
    since: datetime | None = None,
    user_workers: int = 8,
    match_queue_size: int = 2000,
    detail_workers: int = 10,
    log: Logger = _log,
) -> int:
    """여러 유저 탐색과 match-detail 요청을 겹치고 저장은 한 곳에서 처리한다."""
    if user_workers <= 0 or match_queue_size <= 0 or detail_workers <= 0:
        raise ValueError("worker와 queue 크기는 양의 정수여야 합니다")

    stored = 0
    deferred: set[str] = set()
    average_window_started = time.monotonic()
    average_window_stored = 0
    average_cpu_started = os.times().user + os.times().system
    resource_sample_started = average_window_started
    average_memory_free_total = 0.0
    average_memory_samples = 0

    def log_average(*, force: bool = False) -> None:
        nonlocal average_window_started, average_window_stored
        nonlocal average_cpu_started, resource_sample_started
        nonlocal average_memory_free_total, average_memory_samples
        now = time.monotonic()
        if now - resource_sample_started >= 60 or force:
            memory_free = _memory_free_percent()
            if memory_free is not None:
                average_memory_free_total += memory_free
                average_memory_samples += 1
            resource_sample_started = now

        average_elapsed = now - average_window_started
        if average_elapsed >= 600 or (force and average_elapsed >= 60):
            average_delta = stored - average_window_stored
            cpu_now = os.times().user + os.times().system
            average_cpu_percent = (cpu_now - average_cpu_started) / average_elapsed * 100
            average_memory_note = ""
            if average_memory_samples:
                average_memory_note = (
                    f" · 메모리 여유 평균 "
                    f"{average_memory_free_total / average_memory_samples:.0f}%"
                )
            log("")
            log("#$$$$$$$$$$$$$$$$$$$$$$$$$$$$$#")
            log("#          10분 처리량 평균          #")
            log(
                f"{datetime.now():%Y-%m-%d %H:%M:%S} | "
                f"최근 {average_elapsed / 60:.1f}분 평균 신규 {average_delta:,}매치 · "
                f"분당 평균 {average_delta / average_elapsed * 60:.1f}매치 · "
                f"CPU 평균 {average_cpu_percent:.1f}%{average_memory_note}"
            )
            log("#$$$$$$$$$$$$$$$$$$$$$$$$$$$$$#")
            log("")
            average_window_started = now
            average_window_stored = stored
            average_cpu_started = cpu_now
            resource_sample_started = now
            average_memory_free_total = 0.0
            average_memory_samples = 0

    con.execute("UPDATE frontier SET state = 'pending' WHERE state = 'in_progress'")

    try:
        while stored < max_new_matches:
            pending = [
                row[0]
                for row in con.execute(
                    "SELECT ouid FROM frontier WHERE state = 'pending' ORDER BY added_at"
                ).fetchall()
                if row[0] not in deferred
            ][:user_workers]
            if not pending:
                _log_progress(con, stored=stored, max_new_matches=max_new_matches, log=log)
                log(f"{datetime.now():%Y-%m-%d %H:%M:%S} | 유저 큐 소진")
                break

            con.executemany(
                "UPDATE frontier SET state = 'in_progress' WHERE ouid = ? AND state = 'pending'",
                [(ouid,) for ouid in pending],
            )
            users = {ouid: _UserRun(ouid) for ouid in pending}
            works: dict[str, _MatchWork] = {}
            match_queue: asyncio.Queue[_MatchWork] = asyncio.Queue(match_queue_size)
            result_queue: asyncio.Queue[tuple[_MatchWork, dict[str, Any] | None]] = (
                asyncio.Queue(max(detail_workers * 2, 1))
            )
            stop = asyncio.Event()
            fatal: list[BaseException] = []

            async def register(ouid: str, mid: str) -> None:
                if stop.is_set() or have_match(con, mid):
                    return
                work = works.get(mid)
                if work is not None:
                    if ouid not in work.waiters:
                        work.waiters.add(ouid)
                        users[ouid].outstanding += 1
                    return
                work = _MatchWork(mid, {ouid})
                works[mid] = work
                users[ouid].outstanding += 1
                await match_queue.put(work)

            async def scan_user(user: _UserRun) -> None:
                reached_old = False
                try:
                    for page in range(user_pages):
                        if stop.is_set():
                            user.failed = True
                            return
                        ids = await _a_user_matches(client, user.ouid, page * limit, limit)
                        if not ids:
                            break
                        for mid in ids:
                            if since is not None:
                                match_time = match_id_time(mid)
                                if match_time is not None and match_time < since:
                                    reached_old = True
                                    break
                            await register(user.ouid, mid)
                        if reached_old:
                            break
                    user.scan_complete = True
                except ApiError as exc:
                    user.failed = True
                    log(
                        f"{datetime.now():%Y-%m-%d %H:%M:%S} | "
                        f"user/match 오류(ouid={user.ouid[:8]}…): {exc}"
                    )

            async def fetch_details() -> None:
                while True:
                    work = await match_queue.get()
                    try:
                        try:
                            detail = await _a_detail(client, work.match_id)
                        except ApiError:
                            detail = None
                        except Exception as exc:  # 내부/transport 계약 위반은 전체 중단
                            fatal.append(exc)
                            stop.set()
                            detail = None
                        await result_queue.put((work, detail))
                    finally:
                        match_queue.task_done()

            async def write_results() -> None:
                nonlocal stored
                while True:
                    work, detail = await result_queue.get()
                    try:
                        failed = detail is None or stored >= max_new_matches or bool(fatal)
                        if not failed:
                            try:
                                if _store_detail(con, work.match_id, detail):
                                    stored += 1
                            except BaseException as exc:
                                fatal.append(exc)
                                stop.set()
                                failed = True
                        if stored >= max_new_matches:
                            stop.set()
                        for ouid in work.waiters:
                            user = users[ouid]
                            user.outstanding -= 1
                            user.failed |= failed
                        works.pop(work.match_id, None)
                    finally:
                        result_queue.task_done()

            detail_tasks = [asyncio.create_task(fetch_details()) for _ in range(detail_workers)]
            writer_task = asyncio.create_task(write_results())
            try:
                await asyncio.gather(*(scan_user(user) for user in users.values()))
                await match_queue.join()
                await result_queue.join()
            finally:
                for task in [*detail_tasks, writer_task]:
                    task.cancel()
                await asyncio.gather(*detail_tasks, writer_task, return_exceptions=True)

            for user in users.values():
                done = user.scan_complete and not user.failed and user.outstanding == 0
                state = "done" if done else "pending"
                con.execute("UPDATE frontier SET state = ? WHERE ouid = ?", [state, user.ouid])
                if not done:
                    deferred.add(user.ouid)
            _log_progress(con, stored=stored, max_new_matches=max_new_matches, log=log)
            log_average()
            if fatal:
                raise fatal[0]
    finally:
        con.execute("UPDATE frontier SET state = 'pending' WHERE state = 'in_progress'")
    log_average(force=True)
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
            con.execute("UPDATE frontier SET state = 'pending' WHERE state = 'in_progress'")
            for nick in seed_nicknames or []:
                try:
                    ouid = (await client.get("/fconline/v1/id", {"nickname": nick}))["ouid"]
                except ApiError as e:
                    log(f"[seed] 닉네임 '{nick}' → ouid 실패: {e}")
                    continue
                if _add_frontier(con, ouid):
                    log(f"[seed] '{nick}' → ouid {ouid[:8]}… 큐 추가")
                else:
                    log(f"[seed] '{nick}' → ouid {ouid[:8]}… 이미 등록됨")
            if refresh:
                n = reset_done(con)
                log(f"=== 갱신 모드: done ouid {n}개를 pending 으로 ===")
            start = frontier_counts(con)
            if start.pending == 0:
                log("시드도 없고 pending ouid도 없음 — 닉네임을 넘겨 시드하세요.")
                return
            log(f"=== 스노우볼 확장 (동시) · 시작 대기 {start.pending:,} "
                f"(완료 {start.done:,} / 전체 {start.total:,}, {start.done_pct:.1f}%) ===")
            await snowball_async(
                con,
                client,
                max_new_matches=max_new_matches,
                since=since,
                user_workers=settings.user_workers,
                match_queue_size=settings.match_queue_size,
                detail_workers=concurrency,
                log=log,
            )
            end = frontier_counts(con)
            delta = end.pending - start.pending
            log(f"=== 수집 종료 · 대기 {start.pending:,} → {end.pending:,} "
                f"({delta:+,}) · 완료 {end.done:,} / 전체 {end.total:,} ({end.done_pct:.1f}%) ===")
            # 백오프에 삼켜진 429/5xx 를 드러낸다 — 동시성을 올릴 여지가 있는지 판단용.
            # 429 가 나면 레이트를 반토막내고 천천히 회복하므로, 끝 레이트도 함께 찍는다.
            rate_note = ""
            if client.rate.current < client.rate.base:
                rate_note = f" · 레이트 {client.rate.base:.0f}→{client.rate.current:.1f}/s 감속됨"
            log(f"레이트리밋 429 {client.rate_limited_count}회 · 서버오류 5xx "
                f"{client.server_error_count}회 (백오프가 재시도로 흡수){rate_note}")
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
            con.execute("UPDATE frontier SET state = 'pending' WHERE state = 'in_progress'")
            if seed_nicknames:
                log("=== 시드(닉네임→ouid) ===")
                seed_from_nicknames(con, client, seed_nicknames, log=log)
            if refresh:
                n = reset_done(con)
                log(f"=== 갱신 모드: done ouid {n}개를 pending 으로 되돌림 ===")
            start = frontier_counts(con)
            if start.pending == 0:
                log("시드도 없고 pending ouid도 없음 — 닉네임을 넘겨 시드하세요.")
                return
            log(f"=== 스노우볼 확장 · 시작 대기 {start.pending:,} "
                f"(완료 {start.done:,} / 전체 {start.total:,}, {start.done_pct:.1f}%) ===")
            snowball(con, client, max_new_matches=max_new_matches, since=since, log=log)
            end = frontier_counts(con)
            delta = end.pending - start.pending
            log(f"=== 수집 종료 · 대기 {start.pending:,} → {end.pending:,} "
                f"({delta:+,}) · 완료 {end.done:,} / 전체 {end.total:,} ({end.done_pct:.1f}%) ===")
        log(f"총 raw_match: {raw_match_count(con)}건")
    finally:
        con.close()
