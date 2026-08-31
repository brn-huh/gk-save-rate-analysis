import asyncio

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
