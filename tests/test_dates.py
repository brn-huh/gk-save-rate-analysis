"""날짜/갱신: matchDate 파싱, matchId 시각 디코드, since 필터, refresh."""

from datetime import datetime

import pytest

from gksave import agg
from gksave.codec import encode_payload
from gksave.collect import match_id_time, reset_done
from gksave.db import connect_memory
from gksave.parse import parse_match_date


def test_parse_match_date():
    assert parse_match_date({"matchDate": "2026-06-29T14:06:15"}) == datetime(2026, 6, 29, 14, 6, 15)
    assert parse_match_date({}) is None
    assert parse_match_date({"matchDate": "이상한값"}) is None


def test_match_id_time_decodes_objectid():
    # ObjectId 앞 4바이트 = 생성 unix초
    t = match_id_time("6a2547059433871b4eb77b84")
    assert t is not None and t.year == 2026 and t.month == 6
    assert match_id_time("짧음") is None


def test_reset_done():
    con = connect_memory()
    con.executemany("INSERT INTO frontier (ouid, state) VALUES (?, ?)",
                    [("a", "done"), ("b", "done"), ("c", "pending")])
    assert reset_done(con) == 2
    n_pending = con.execute("SELECT count(*) FROM frontier WHERE state='pending'").fetchone()[0]
    assert n_pending == 3
    con.close()


def _dated_match(mid, date_str, saves, goals, sp_id=500, grade=10):
    return {
        "matchId": mid, "matchType": 50, "matchDate": date_str,
        "matchInfo": [
            {"ouid": "U", "player": [{"spId": sp_id, "spPosition": 0, "spGrade": grade}],
             "shootDetail": []},
            {"ouid": "O", "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [{"result": 1, "type": 1}] * saves + [{"result": 3, "type": 1}] * goals},
        ],
    }


def test_date_range_respects_since():
    """페이지 상단 '데이터 기간'은 집계에 실제로 쓰인 창을 보여줘야 한다.

    롤링 30일 창을 켜기 전에는 since 가 없어 이 불일치가 드러나지 않았다.
    since 를 무시하면 '데이터 기간 6/1~7/10' 이라 써놓고 6/10 부터만 집계하게 된다.
    """
    from gksave import export

    con = connect_memory()
    for d in (_dated_match("old", "2026-01-01T00:00:00", saves=0, goals=3),
              _dated_match("new", "2026-06-20T00:00:00", saves=4, goals=0)):
        con.execute("INSERT INTO raw_match (match_id, payload) VALUES (?, ?)",
                    [d["matchId"], encode_payload(d)])
    agg.rebuild(con)

    full = export.build_payload(con, gate=1)
    assert full["date_range"]["min"] == "2026-01-01"

    windowed = export.build_payload(con, gate=1, since=datetime(2026, 6, 1))
    assert windowed["date_range"]["min"] == "2026-06-20"   # 창 밖 경기는 기간에서 빠진다
    assert windowed["date_range"]["max"] == "2026-06-20"


def test_since_filters_leaderboard():
    con = connect_memory()
    for d in (_dated_match("old", "2026-01-01T00:00:00", saves=0, goals=3),
              _dated_match("new", "2026-06-20T00:00:00", saves=4, goals=0)):
        con.execute("INSERT INTO raw_match (match_id, payload) VALUES (?, ?)",
                    [d["matchId"], encode_payload(d)])
    agg.rebuild(con)

    # since 없으면 두 경기 다: 4/7
    full = next(c for c in agg.card_leaderboard(con, gate=1) if c["gk_sp_id"] == 500)
    assert full["matches"] == 2 and full["saves"] == 4 and full["goals"] == 3

    # since=6월 → 최근 경기만: 4/4, 1경기
    recent = agg.card_leaderboard(con, gate=1, since=datetime(2026, 6, 1))
    card = next(c for c in recent if c["gk_sp_id"] == 500)
    assert card["matches"] == 1 and card["goals"] == 0
    con.close()
