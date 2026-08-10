"""메타 enrich: 선수명·시즌 해석, 동일선수 시즌 비교."""

import pytest

from gksave import meta
from gksave.db import connect_memory


class FakeClient:
    """api.get_metadata 가 쓰는 client.get(path) 만 흉내낸다."""

    def __init__(self, spid, season):
        self._by_kind = {"spid": spid, "seasonid": season}
        self.calls = []

    def get(self, path):
        kind = path.rsplit("/", 1)[-1].removesuffix(".json")
        self.calls.append(kind)
        return self._by_kind[kind]


@pytest.fixture
def empty_con():
    c = connect_memory()
    yield c
    c.close()


def test_refresh_loads_both_tables(empty_con):
    client = FakeClient(
        spid=[{"id": 101190053, "name": "야신"}, {"id": 280190053, "name": "야신"}],
        season=[{"seasonId": 101, "className": "ICON (ICON)"}],
    )
    n_sp, n_se = meta.refresh(empty_con, client)
    assert (n_sp, n_se) == (2, 1)
    assert empty_con.execute(
        "SELECT name FROM meta_spid WHERE sp_id = 101190053").fetchone()[0] == "야신"
    assert empty_con.execute(
        "SELECT class_name FROM meta_season WHERE season_id = 101").fetchone()[0] == "ICON (ICON)"


def test_refresh_replaces_previous_contents(empty_con):
    """전건 교체 — 이전 실행에 있던 행이 새 응답에 없으면 사라진다."""
    meta.refresh(empty_con, FakeClient(
        spid=[{"id": 1, "name": "옛날"}], season=[{"seasonId": 1, "className": "OLD"}]))
    meta.refresh(empty_con, FakeClient(
        spid=[{"id": 2, "name": "신규"}], season=[{"seasonId": 2, "className": "NEW"}]))
    assert empty_con.execute("SELECT sp_id FROM meta_spid").fetchall() == [(2,)]
    assert empty_con.execute("SELECT season_id FROM meta_season").fetchall() == [(2,)]


def test_refresh_handles_special_chars_and_nulls(empty_con):
    """이름에 따옴표·유니코드가 있어도, name 이 null 이어도 적재된다."""
    client = FakeClient(
        spid=[{"id": 10, "name": "O'Brien \"Bob\""}, {"id": 11, "name": None},
              {"id": 12, "name": "김\\민재"}],
        season=[{"seasonId": 1, "className": "TOTY (Team Of The Year)"}],
    )
    meta.refresh(empty_con, client)
    rows = dict(empty_con.execute("SELECT sp_id, name FROM meta_spid").fetchall())
    assert rows == {10: "O'Brien \"Bob\"", 11: None, 12: "김\\민재"}


def test_refresh_keeps_cache_when_response_empty(empty_con):
    """빈 응답(장애)에 기존 캐시를 날리지 않는다 — 날리면 이름·시즌이 통째로 사라진다."""
    meta.refresh(empty_con, FakeClient(
        spid=[{"id": 1, "name": "야신"}], season=[{"seasonId": 1, "className": "ICON"}]))
    meta.refresh(empty_con, FakeClient(spid=[], season=[]))
    assert empty_con.execute("SELECT count(*) FROM meta_spid").fetchone()[0] == 1
    assert empty_con.execute("SELECT count(*) FROM meta_season").fetchone()[0] == 1


@pytest.fixture
def con():
    c = connect_memory()
    c.executemany("INSERT INTO meta_season VALUES (?, ?)",
                  [(101, "ICON (ICON)"), (280, "23TOTS")])
    c.executemany("INSERT INTO meta_spid VALUES (?, ?)", [
        (101190053, "야신"),      # 시즌 101
        (280190053, "야신"),      # 같은 선수 다른 시즌 280
        (101550001, "노이어"),    # 시즌 101, 다른 선수
    ])
    yield c
    c.close()


def test_has_meta(con):
    assert meta.has_meta(con)


def test_enrich_name_and_season(con):
    cards = [
        {"gk_sp_id": 101190053, "save_pct": 0.80, "matches": 5},
        {"gk_sp_id": 280190053, "save_pct": 0.70, "matches": 5},
        {"gk_sp_id": 101550001, "save_pct": 0.60, "matches": 5},
        {"gk_sp_id": 999999999, "save_pct": 0.50, "matches": 5},  # 메타에 없음
    ]
    meta.enrich(con, cards)
    assert cards[0]["player_name"] == "야신"
    assert cards[0]["season_id"] == 101
    assert cards[0]["season_name"] == "ICON (ICON)"
    assert cards[1]["season_id"] == 280
    assert cards[3]["player_name"] is None      # 미상은 None
    assert cards[3]["season_id"] is None


def test_same_player_view_groups_seasons(con):
    cards = [
        {"gk_sp_id": 101190053, "save_pct": 0.70, "matches": 5},
        {"gk_sp_id": 280190053, "save_pct": 0.85, "matches": 5},
        {"gk_sp_id": 101550001, "save_pct": 0.60, "matches": 5},  # 노이어 한 시즌뿐
    ]
    meta.enrich(con, cards)
    view = meta.same_player_view(cards)
    names = [v["player_name"] for v in view]
    assert names == ["야신"]                     # 시즌 2개인 선수만
    # 선방률 desc → 280 시즌(0.85)이 먼저
    assert view[0]["cards"][0]["season_id"] == 280
    assert view[0]["cards"][1]["season_id"] == 101
