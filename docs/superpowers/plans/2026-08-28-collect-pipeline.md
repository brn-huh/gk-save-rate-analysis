# Collect Pipeline Parallelism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-user serial `snowball_async` with a User→match_id queue→Detail→Writer pipeline so match-detail requests stay near `GKSAVE_RATE` even when individual users have few new matches.

**Architecture:** Single asyncio process, one shared `AsyncResilientClient`, one DuckDB writer. User workers claim `pending→in_progress`, push new match IDs into a bounded queue; detail consumers fetch; a single writer inserts `raw_match`, harvests ouids, decrements per-ouid outstanding, and marks `done` only when scan finished and outstanding is 0. On max/shutdown, discard queued IDs and reset related `in_progress→pending`.

**Tech Stack:** Python 3.10+, asyncio, httpx MockTransport, DuckDB, pytest

**Spec:** `docs/superpowers/specs/2026-08-28-collect-pipeline-design.md`

## Global Constraints

- Single DuckDB writer only (no concurrent `con.execute` from multiple tasks without a shared `asyncio.Lock`).
- `raw_match.match_id` PK + `ON CONFLICT DO NOTHING`; frontier ouid PK unchanged.
- CLI contracts unchanged: `--since` / `--days` / `--seed-nicknames` / `--refresh` / `--max-matches` / `--concurrency`.
- `scripts/collect.sh` keeps the same flags; no git push from update path.
- On run start: all `in_progress` → `pending` (crash recovery).
- On `max_new_matches`: keep committed rows; discard unstored queue items; `in_progress` → `pending`.
- Defaults: `GKSAVE_USER_WORKERS=8`, `GKSAVE_MATCH_QUEUE=2000`.
- Prefer one async pipeline path (even when concurrency is low); do not maintain a second divergent snowball algorithm unless tests force it.

## File map

| File | Responsibility |
|---|---|
| `src/gksave/collect.py` | Frontier helpers, outstanding tracking, pipeline `snowball_async` / `run_async` |
| `src/gksave/db.py` | Schema comment: `pending \| in_progress \| done` |
| `src/gksave/config.py` | Read `GKSAVE_USER_WORKERS`, `GKSAVE_MATCH_QUEUE` |
| `.env.local.example` | Document new env vars |
| `tests/test_frontier_stats.py` | Counts include `in_progress` |
| `tests/test_collect_pipeline.py` | New: recover, claim, outstanding→done, e2e mock, max, backpressure, dedup |

---

### Task 1: Frontier `in_progress` accounting + crash recovery

**Files:**
- Modify: `src/gksave/db.py` (frontier state comment only)
- Modify: `src/gksave/collect.py` (`FrontierCounts`, `frontier_counts`, add `recover_in_progress`)
- Modify: `tests/test_frontier_stats.py`
- Create: `tests/test_collect_pipeline.py` (first tests)

**Interfaces:**
- Produces:
  - `FrontierCounts(done: int, pending: int, in_progress: int)` with `total = done + pending + in_progress`
  - `recover_in_progress(con) -> int` — rows reset `in_progress → pending`

- [ ] **Step 1: Write failing tests for counts + recover**

Add to `tests/test_frontier_stats.py`:

```python
def test_counts_include_in_progress():
    con = connect_memory()
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('a', 'pending')")
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('b', 'done')")
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('c', 'in_progress')")
    c = frontier_counts(con)
    assert (c.pending, c.done, c.in_progress, c.total) == (1, 1, 1, 3)
```

Create `tests/test_collect_pipeline.py`:

```python
from gksave.collect import frontier_counts, recover_in_progress
from gksave.db import connect_memory


def test_recover_in_progress_resets_to_pending():
    con = connect_memory()
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('x', 'in_progress')")
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('y', 'done')")
    n = recover_in_progress(con)
    assert n == 1
    rows = dict(con.execute("SELECT ouid, state FROM frontier").fetchall())
    assert rows == {"x": "pending", "y": "done"}
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_frontier_stats.py::test_counts_include_in_progress tests/test_collect_pipeline.py::test_recover_in_progress_resets_to_pending -v`

Expected: FAIL (`in_progress` missing / `recover_in_progress` undefined)

- [ ] **Step 3: Minimal implementation**

In `db.py` SCHEMA frontier comment, change to:

```python
    state    VARCHAR DEFAULT 'pending',   -- pending | in_progress | done
```

In `collect.py`:

```python
@dataclass(frozen=True)
class FrontierCounts:
    done: int
    pending: int
    in_progress: int = 0

    @property
    def total(self) -> int:
        return self.done + self.pending + self.in_progress
```

```python
def frontier_counts(con: duckdb.DuckDBPyConnection) -> FrontierCounts:
    rows = dict(
        con.execute("SELECT state, count(*) FROM frontier GROUP BY state").fetchall()
    )
    return FrontierCounts(
        done=rows.get("done", 0),
        pending=rows.get("pending", 0),
        in_progress=rows.get("in_progress", 0),
    )


def recover_in_progress(con: duckdb.DuckDBPyConnection) -> int:
    n = con.execute(
        "SELECT count(*) FROM frontier WHERE state = 'in_progress'"
    ).fetchone()[0]
    con.execute("UPDATE frontier SET state = 'pending' WHERE state = 'in_progress'")
    return int(n)
```

Update `_log_progress` to keep showing `done/total` and `pending` (claimed users stay out of `대기`).

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/test_frontier_stats.py tests/test_collect_pipeline.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gksave/db.py src/gksave/collect.py tests/test_frontier_stats.py tests/test_collect_pipeline.py
git commit -m "feat(collect): frontier in_progress counts and crash recovery"
```

---

### Task 2: Claim + outstanding → done helpers

**Files:**
- Modify: `src/gksave/collect.py`
- Modify: `tests/test_collect_pipeline.py`

**Interfaces:**
- Consumes: `recover_in_progress`, `frontier_counts`
- Produces:
  - `claim_pending(con, n: int) -> list[str]`
  - `class OuidsOutstanding` with `mark_scan_done`, `add`, `complete_one` → bool, `should_done`, `discard`, `tracked_ouids`
  - `mark_done(con, ouid: str) -> None`
  - `requeue_in_progress(con, ouids: Iterable[str]) -> None`

- [ ] **Step 1: Write failing tests**

```python
from gksave.collect import (
    OuidsOutstanding,
    claim_pending,
    mark_done,
    requeue_in_progress,
)


def test_claim_pending_moves_n_rows():
    con = connect_memory()
    for i in range(5):
        con.execute("INSERT INTO frontier (ouid, state) VALUES (?, 'pending')", [f"u{i}"])
    got = claim_pending(con, 2)
    assert len(got) == 2
    assert frontier_counts(con).in_progress == 2
    assert frontier_counts(con).pending == 3


def test_outstanding_done_only_after_scan_and_zero():
    o = OuidsOutstanding()
    o.add("u1", 2)
    assert o.should_done("u1") is False
    o.mark_scan_done("u1")
    assert o.should_done("u1") is False
    assert o.complete_one("u1") is False
    assert o.complete_one("u1") is True
    assert o.should_done("u1") is True


def test_requeue_in_progress():
    con = connect_memory()
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('a', 'in_progress')")
    requeue_in_progress(con, ["a"])
    assert con.execute("SELECT state FROM frontier WHERE ouid='a'").fetchone()[0] == "pending"
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_collect_pipeline.py::test_claim_pending_moves_n_rows tests/test_collect_pipeline.py::test_outstanding_done_only_after_scan_and_zero tests/test_collect_pipeline.py::test_requeue_in_progress -v`

- [ ] **Step 3: Implement helpers in `collect.py`**

```python
from collections import defaultdict
from typing import Iterable


def claim_pending(con: duckdb.DuckDBPyConnection, n: int) -> list[str]:
    if n <= 0:
        return []
    rows = con.execute(
        "SELECT ouid FROM frontier WHERE state = 'pending' LIMIT ?", [n]
    ).fetchall()
    ouids = [r[0] for r in rows]
    for ouid in ouids:
        con.execute(
            "UPDATE frontier SET state = 'in_progress' WHERE ouid = ? AND state = 'pending'",
            [ouid],
        )
    return ouids


def mark_done(con: duckdb.DuckDBPyConnection, ouid: str) -> None:
    con.execute(
        "UPDATE frontier SET state = 'done' WHERE ouid = ? AND state = 'in_progress'",
        [ouid],
    )


def requeue_in_progress(con: duckdb.DuckDBPyConnection, ouids: Iterable[str]) -> None:
    for ouid in ouids:
        con.execute(
            "UPDATE frontier SET state = 'pending' WHERE ouid = ? AND state = 'in_progress'",
            [ouid],
        )


class OuidsOutstanding:
    def __init__(self) -> None:
        self._n: dict[str, int] = defaultdict(int)
        self._scan_done: set[str] = set()

    def add(self, ouid: str, k: int = 1) -> None:
        self._n[ouid] += k

    def mark_scan_done(self, ouid: str) -> None:
        self._scan_done.add(ouid)

    def should_done(self, ouid: str) -> bool:
        return ouid in self._scan_done and self._n.get(ouid, 0) == 0

    def complete_one(self, ouid: str) -> bool:
        if self._n.get(ouid, 0) <= 0:
            return self.should_done(ouid)
        self._n[ouid] -= 1
        if self._n[ouid] == 0:
            del self._n[ouid]
        return self.should_done(ouid)

    def discard(self, ouid: str) -> None:
        self._n.pop(ouid, None)
        self._scan_done.discard(ouid)

    def tracked_ouids(self) -> set[str]:
        return set(self._n) | set(self._scan_done)
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_collect_pipeline.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/gksave/collect.py tests/test_collect_pipeline.py
git commit -m "feat(collect): claim pending and outstanding-done helpers"
```

---

### Task 3: Config for user workers + match queue

**Files:**
- Modify: `src/gksave/config.py`
- Modify: `.env.local.example`
- Create: `tests/test_collect_config.py`

**Interfaces:**
- Produces: `config.user_workers() -> int`, `config.match_queue_size() -> int` (functions so monkeypatch works)

- [ ] **Step 1: Failing test**

```python
# tests/test_collect_config.py
from gksave import config


def test_user_workers_and_queue_defaults(monkeypatch):
    monkeypatch.delenv("GKSAVE_USER_WORKERS", raising=False)
    monkeypatch.delenv("GKSAVE_MATCH_QUEUE", raising=False)
    assert config.user_workers() == 8
    assert config.match_queue_size() == 2000


def test_user_workers_env(monkeypatch):
    monkeypatch.setenv("GKSAVE_USER_WORKERS", "12")
    monkeypatch.setenv("GKSAVE_MATCH_QUEUE", "100")
    assert config.user_workers() == 12
    assert config.match_queue_size() == 100
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_collect_config.py -v`

- [ ] **Step 3: Implement in `config.py`**

```python
def user_workers() -> int:
    return max(1, int(os.environ.get("GKSAVE_USER_WORKERS", "8")))


def match_queue_size() -> int:
    return max(1, int(os.environ.get("GKSAVE_MATCH_QUEUE", "2000")))
```

Append to `.env.local.example`:

```bash
# 수집 파이프라인: 유저 병렬 수 / match_id 큐 깊이
# GKSAVE_USER_WORKERS=8
# GKSAVE_MATCH_QUEUE=2000
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_collect_config.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/gksave/config.py .env.local.example tests/test_collect_config.py
git commit -m "feat(config): GKSAVE_USER_WORKERS and GKSAVE_MATCH_QUEUE"
```

---

### Task 4: Pipeline `snowball_async` (end-to-end with mock transport)

**Files:**
- Modify: `src/gksave/collect.py` — replace body of `snowball_async`; call `recover_in_progress` from `run_async`
- Modify: `tests/test_collect_pipeline.py`

**Interfaces:**
- Consumes: Tasks 1–3 helpers, `AsyncResilientClient`, existing encode/parse/harvest/`have_match`
- Produces: `async def snowball_async(..., user_workers: int | None = None, queue_size: int | None = None) -> int`

**Runtime shape:**

```text
db_lock = asyncio.Lock()
match_q: asyncio.Queue[(match_id, source_ouid)]  # maxsize=queue_size
result_q: asyncio.Queue[(match_id, source_ouid, detail|None)]
outstanding = OuidsOutstanding()
stop = asyncio.Event()
stored = 0

A: under lock claim_pending(1) → user/match pages → for new ids: outstanding.add;
   await match_q.put; mark_scan_done; if should_done → mark_done
B: get match_q → client fetch detail → result_q.put
C: get result_q → insert+harvest under lock → complete_one → maybe mark_done;
   stored++; if stored>=max → stop + requeue tracked in_progress
```

Before writing the mock, open `src/gksave/api.py` and copy exact paths/query keys (`/fconline/v1/user/match`, match-detail param names).

- [ ] **Step 1: Write failing e2e test**

```python
import asyncio
from datetime import datetime, timezone

import httpx

from gksave.collect import frontier_counts, snowball_async
from gksave.config import Settings
from gksave.db import connect_memory, raw_match_count
from gksave.http import AsyncResilientClient


def _oid_at(dt: datetime, suffix_nibble: str = "0") -> str:
    ts = int(dt.replace(tzinfo=timezone.utc).timestamp())
    return f"{ts:08x}" + ("0" * 15) + suffix_nibble


async def test_snowball_pipeline_two_users():
    con = connect_memory()
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('U1', 'pending')")
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('U2', 'pending')")
    now = datetime.now(timezone.utc)
    m1, m2, m3 = _oid_at(now, "1"), _oid_at(now, "2"), _oid_at(now, "3")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/user/match" in url and "ouid=U1" in url:
            return httpx.Response(200, json=[m1, m2])
        if "/user/match" in url and "ouid=U2" in url:
            return httpx.Response(200, json=[m3])
        if "match-detail" in url:
            mid = request.url.params.get("matchid")
            return httpx.Response(
                200,
                json={
                    "matchId": mid,
                    "matchDate": now.strftime("%Y-%m-%dT%H:%M:%S"),
                    "matchInfo": [
                        {"ouid": "U1", "player": []},
                        {"ouid": "U9", "player": []},
                    ],
                },
            )
        return httpx.Response(404, json={})

    s = Settings(max_requests_per_sec=100, backoff_base_sec=0.0, backoff_max_sec=0.0)
    async with AsyncResilientClient(
        s, transport=httpx.MockTransport(handler), concurrency=4
    ) as client:
        n = await snowball_async(
            con, client, max_new_matches=10, since=None,
            user_workers=2, queue_size=10,
        )
    assert n == 3
    assert raw_match_count(con) == 3
    assert frontier_counts(con).in_progress == 0
    assert frontier_counts(con).pending >= 1  # harvested U9
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_collect_pipeline.py::test_snowball_pipeline_two_users -v`

- [ ] **Step 3: Implement pipeline body in `snowball_async`**

Replace the serial per-ouid loop. Use `asyncio.Lock` for all DuckDB access. Defaults: `user_workers or config.user_workers()`, `queue_size or config.match_queue_size()`.

On `user/match` `ApiError`: log, `requeue_in_progress`, `outstanding.discard` — never silent done.

On max: `stop.set()`, discard remaining queue items, `requeue_in_progress(con, outstanding.tracked_ouids())`, return `stored`.

- [ ] **Step 4: Wire recover in `run_async`**

```python
n_rec = recover_in_progress(con)
if n_rec:
    log(f"이전 in_progress {n_rec}개 → pending 복구")
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_collect_pipeline.py tests/test_frontier_stats.py tests/test_http.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/gksave/collect.py tests/test_collect_pipeline.py
git commit -m "feat(collect): parallel user/detail pipeline for snowball_async"
```

---

### Task 5: Max abort, dedup, backpressure

**Files:**
- Modify: `tests/test_collect_pipeline.py`
- Modify: `src/gksave/collect.py` only if tests expose gaps

**Interfaces:** unchanged

- [ ] **Step 1: Write tests**

```python
async def test_max_matches_requeues_in_progress():
    # one user, many match ids, max_new_matches=2
    # assert raw_match_count == 2, ouid state == 'pending', in_progress == 0


async def test_duplicate_match_id_from_two_users_stores_once():
    # U1 and U2 list same mid → raw_match_count == 1, return value == 1


async def test_queue_backpressure_no_deadlock():
    async with asyncio.timeout(5):
        await snowball_async(
            con, client, max_new_matches=20, user_workers=2, queue_size=1, since=None
        )
```

Fill mocks the same way as Task 4 (real API paths).

- [ ] **Step 2: Run**

Run: `pytest tests/test_collect_pipeline.py -v`

- [ ] **Step 3: Fix until PASS**

- [ ] **Step 4: Full suite**

Run: `pytest -q`

Expected: all green (existing node skips OK)

- [ ] **Step 5: Commit**

```bash
git add src/gksave/collect.py tests/test_collect_pipeline.py
git commit -m "test(collect): max abort, dedup, and queue backpressure"
```

---

### Task 6: RUNBOOK note + smoke checklist

**Files:**
- Modify: `RUNBOOK.md` (short collect env note)

- [ ] **Step 1: Add 3–5 lines under collect** documenting `GKSAVE_USER_WORKERS` / `GKSAVE_MATCH_QUEUE` and that detail work is queue-fed across users

- [ ] **Step 2: Manual smoke (API key required)**

```bash
GKSAVE_USER_WORKERS=8 GKSAVE_RATE=15 GKSAVE_CONCURRENCY=24 \
  gksave collect --days 1 --max-matches 500
```

Compare rough match/sec to prior serial runs; watch for `! 429 rate limit` lines.

- [ ] **Step 3: Commit docs**

```bash
git add RUNBOOK.md
git commit -m "docs: note collect pipeline user-worker env knobs"
```

---

## Spec coverage (self-review)

| Spec requirement | Task |
|---|---|
| 3-stage pipeline | Task 4 |
| Single writer | Task 4 |
| Shared client / RATE / 429 | Task 4 (+ existing http) |
| `in_progress` + start recover | Tasks 1, 4 |
| done after outstanding 0 | Tasks 2, 4 |
| max discard + requeue | Task 5 |
| env knobs | Task 3 |
| backpressure | Task 5 |
| user/match error not silent done | Task 4 |
| collect.sh unchanged | constraint (no code task) |
| listed tests | Tasks 1–5 |

## Placeholder scan

No TBD steps. Task 4 mocks must follow `api.py` exactly.

## Type consistency

- `recover_in_progress(con) -> int`
- `claim_pending(con, n: int) -> list[str]`
- `OuidsOutstanding.complete_one(ouid: str) -> bool`
- `snowball_async(...) -> int`
- `user_workers() -> int`, `match_queue_size() -> int`
