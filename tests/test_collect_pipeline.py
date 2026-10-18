import asyncio
from datetime import datetime, timezone

import pytest

from gksave import collect
from gksave.db import connect_memory
from gksave.http import ApiError


def _detail(mid):
    return {"matchId": mid, "matchDate": "2026-08-28T00:00:00", "matchInfo": []}


def test_seed_reports_duplicate_without_counting_it(monkeypatch):
    con = connect_memory()
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('u1', 'pending')")
    monkeypatch.setattr(collect.api, "get_ouid", lambda _client, nick: {"old": "u1", "new": "u2"}[nick])
    logs = []

    added = collect.seed_from_nicknames(con, object(), ["old", "new"], log=logs.append)

    assert added == 1
    assert "이미 등록됨" in logs[0]
    assert "큐 추가" in logs[1]


class FakeClient:
    def __init__(self, matches, *, fail_detail=False):
        self.matches = matches
        self.fail_detail = fail_detail
        self.detail_calls = 0
        self.active_users = 0
        self.max_active_users = 0
        self._both_users = asyncio.Event()

    async def get(self, path, params=None):
        if path.endswith("user/match"):
            if params["offset"]:
                return []
            self.active_users += 1
            self.max_active_users = max(self.max_active_users, self.active_users)
            if self.active_users == 2:
                self._both_users.set()
            await asyncio.wait_for(self._both_users.wait(), 1)
            self.active_users -= 1
            return self.matches[params["ouid"]]
        self.detail_calls += 1
        if self.fail_detail:
            raise ApiError(503, "failed")
        return _detail(params["matchid"])


class CountingConnection:
    def __init__(self, con):
        self.con = con
        self.match_queries = 0
        self.pending_rows = []
        self.reading_pending = False

    def execute(self, query, parameters=None):
        self.reading_pending = query.startswith("SELECT ouid FROM frontier")
        if query.startswith("SELECT") and "FROM raw_match" in query:
            self.match_queries += 1
        self.con.execute(query, parameters)
        return self

    def fetchall(self):
        rows = self.con.fetchall()
        if self.reading_pending:
            self.pending_rows.append(len(rows))
        return rows

    def __getattr__(self, name):
        return getattr(self.con, name)


def test_existing_matches_are_checked_once_per_page():
    con = CountingConnection(connect_memory())
    con.executemany(
        "INSERT INTO frontier (ouid, state) VALUES (?, 'pending')", [("u1",), ("u2",)]
    )
    known = [f"known-{i}" for i in range(99)]
    con.executemany(
        "INSERT INTO raw_match (match_id, payload) VALUES (?, ?)",
        [(mid, b"unused") for mid in known],
    )
    client = FakeClient({"u1": known + ["new"], "u2": known + ["new"]})

    stored = asyncio.run(collect.snowball_async(
        con, client, user_pages=1, user_workers=2, detail_workers=2,
        log=lambda _msg: None,
    ))

    assert stored == 1
    assert client.detail_calls == 1
    assert con.match_queries == 2


def test_pending_fetch_is_bounded_and_skips_failed_users():
    con = CountingConnection(connect_memory())
    con.executemany(
        "INSERT INTO frontier (ouid, state, added_at) VALUES (?, 'pending', ?)",
        [(f"u{i}", f"2026-01-01T00:00:0{i}") for i in range(7)],
    )

    class Client:
        async def get(self, _path, params=None):
            if params["ouid"] in {"u0", "u1"}:
                raise ApiError(503, "failed")
            return []

    asyncio.run(collect.snowball_async(
        con, Client(), user_pages=1, user_workers=2, detail_workers=1,
        log=lambda _msg: None,
    ))

    states = dict(con.execute("SELECT ouid, state FROM frontier").fetchall())
    assert states == {f"u{i}": "pending" if i < 2 else "done" for i in range(7)}
    assert max(con.pending_rows) <= 2


@pytest.mark.parametrize("fail_first", [False, True])
def test_page_snapshot_handles_matches_finished_while_queue_waits(fail_first):
    con = connect_memory()
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('u1', 'pending')")
    detail_ids = []

    class Client:
        async def get(self, path, params=None):
            if path.endswith("user/match"):
                return ["shared", "m2", "m3", "m4", "shared"]
            mid = params["matchid"]
            detail_ids.append(mid)
            await asyncio.sleep(0)
            if fail_first and mid == "shared" and detail_ids.count(mid) == 1:
                raise ApiError(503, "failed")
            return _detail(mid)

    stored = asyncio.run(collect.snowball_async(
        con, Client(), user_pages=1, user_workers=1,
        match_queue_size=1, detail_workers=1, log=lambda _msg: None,
    ))

    assert stored == 4
    assert detail_ids == ["shared", "m2", "m3", "m4"] + (["shared"] if fail_first else [])
    assert con.execute("SELECT state FROM frontier").fetchone()[0] == (
        "pending" if fail_first else "done"
    )


def test_page_bulk_check_preserves_date_cutoff_and_opponent_expansion():
    con = connect_memory()
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('u1', 'pending')")
    recent = f"{int(datetime(2026, 10, 18, tzinfo=timezone.utc).timestamp()):08x}" + "0" * 16
    old = f"{int(datetime(2026, 10, 16, tzinfo=timezone.utc).timestamp()):08x}" + "0" * 16
    detail_ids = []
    user_ids = []

    class Client:
        async def get(self, path, params=None):
            if path.endswith("user/match"):
                user_ids.append(params["ouid"])
                return [recent, old, "after-cutoff"] if params["ouid"] == "u1" else []
            detail_ids.append(params["matchid"])
            return {**_detail(params["matchid"]), "matchInfo": [{"ouid": "u2"}]}

    stored = asyncio.run(collect.snowball_async(
        con, Client(), since=datetime(2026, 10, 17), user_workers=1,
        detail_workers=1, log=lambda _msg: None,
    ))

    assert stored == 1
    assert detail_ids == [recent]
    assert user_ids == ["u1", "u2"]
    assert con.execute("SELECT count(*) FROM frontier WHERE state='done'").fetchone()[0] == 2


def test_cancellation_before_storage_leaves_user_pending():
    con = connect_memory()
    con.execute("INSERT INTO frontier (ouid, state) VALUES ('u1', 'pending')")

    async def run():
        detail_started = asyncio.Event()

        class Client:
            async def get(self, path, params=None):
                if path.endswith("user/match"):
                    return ["m1"]
                detail_started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(collect.snowball_async(
            con, Client(), user_pages=1, user_workers=1, detail_workers=1,
            log=lambda _msg: None,
        ))
        try:
            await asyncio.wait_for(detail_started.wait(), 1)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())

    assert con.execute("SELECT state FROM frontier").fetchone()[0] == "pending"
    assert con.execute("SELECT count(*) FROM raw_match").fetchone()[0] == 0


def _run(client, *, max_matches=10):
    con = connect_memory()
    con.executemany(
        "INSERT INTO frontier (ouid, state) VALUES (?, 'pending')", [("u1",), ("u2",)]
    )
    stored = asyncio.run(
        collect.snowball_async(
            con,
            client,
            max_new_matches=max_matches,
            user_pages=1,
            user_workers=2,
            match_queue_size=1,
            detail_workers=2,
            log=lambda _msg: None,
        )
    )
    return con, stored


def test_users_are_scanned_in_parallel_and_shared_match_is_fetched_once():
    client = FakeClient({"u1": ["m1"], "u2": ["m1"]})
    con, stored = _run(client)

    assert client.max_active_users == 2
    assert client.detail_calls == 1
    assert stored == 1
    assert con.execute("SELECT count(*) FROM raw_match").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM frontier WHERE state='done'").fetchone()[0] == 2


def test_detail_failure_leaves_related_users_pending():
    con, stored = _run(FakeClient({"u1": ["m1"], "u2": ["m1"]}, fail_detail=True))

    assert stored == 0
    assert con.execute("SELECT count(*) FROM frontier WHERE state='pending'").fetchone()[0] == 2


def test_max_leaves_unstored_work_pending():
    con, stored = _run(FakeClient({"u1": ["m1"], "u2": ["m2"]}), max_matches=1)

    assert stored == 1
    assert con.execute("SELECT count(*) FROM raw_match").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM frontier WHERE state='pending'").fetchone()[0] == 1


def test_store_and_harvest_roll_back_together(monkeypatch):
    con = connect_memory()

    def fail_harvest(_con, _detail):
        raise RuntimeError("boom")

    monkeypatch.setattr(collect, "_harvest_ouids", fail_harvest)
    with pytest.raises(RuntimeError):
        collect._store_detail(con, "m1", _detail("m1"))
    assert con.execute("SELECT count(*) FROM raw_match").fetchone()[0] == 0


def test_writer_error_stops_run_and_recovers_users(monkeypatch):
    con = connect_memory()
    con.executemany(
        "INSERT INTO frontier (ouid, state) VALUES (?, 'pending')", [("u1",), ("u2",)]
    )

    def fail_store(*_args):
        raise RuntimeError("writer failed")

    monkeypatch.setattr(collect, "_store_detail", fail_store)
    with pytest.raises(RuntimeError, match="writer failed"):
        asyncio.run(
            collect.snowball_async(
                con,
                FakeClient({"u1": ["m1"], "u2": ["m2"]}),
                user_pages=1,
                user_workers=2,
                match_queue_size=1,
                detail_workers=2,
                log=lambda _msg: None,
            )
        )
    assert con.execute("SELECT count(*) FROM raw_match").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM frontier WHERE state='pending'").fetchone()[0] == 2
