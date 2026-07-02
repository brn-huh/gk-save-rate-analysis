"""메타 enrich: 선수명·시즌 해석, 동일선수 시즌 비교."""

import pytest

from gksave import meta
from gksave.db import connect_memory


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
