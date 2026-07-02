"""조회 편의 뷰: shot_readable, card_stats 가 이름·시즌·강화를 붙여 낸다."""

import json

from gksave import agg
from gksave.db import connect_memory


def _match(mid, sp_id, grade, saves, goals):
    return {
        "matchId": mid, "matchType": 50, "matchDate": "2026-06-20T00:00:00",
        "matchInfo": [
            {"ouid": "U", "player": [{"spId": sp_id, "spPosition": 0, "spGrade": grade}],
             "shootDetail": []},
            {"ouid": "O", "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [{"result": 1, "type": 1}] * saves + [{"result": 3, "type": 1}] * goals},
        ],
    }


def _seed(con):
    con.execute("INSERT INTO meta_spid VALUES (?, ?)", [101190053, "야신"])
    con.execute("INSERT INTO meta_season VALUES (?, ?)", [101, "ICON"])
    d = _match("m1", 101190053, 9, saves=3, goals=1)
    con.execute("INSERT INTO raw_match (match_id, payload) VALUES (?, ?)",
                [d["matchId"], json.dumps(d)])
    agg.rebuild(con)


def test_card_stats_view():
    con = connect_memory()
    _seed(con)
    row = con.execute(
        "SELECT player_name, season, grade, matches, saves, goals, save_pct "
        "FROM card_stats WHERE gk_sp_id = 101190053"
    ).fetchone()
    assert row == ("야신", "ICON", 9, 1, 3, 1, 75.0)
    con.close()


def test_shot_readable_view():
    con = connect_memory()
    _seed(con)
    rows = con.execute(
        "SELECT player_name, season, grade, result FROM shot_readable "
        "WHERE gk_sp_id = 101190053 ORDER BY result"
    ).fetchall()
    assert len(rows) == 4                       # 선방3 + 실점1
    assert rows[0][:3] == ("야신", "ICON", 9)
    con.close()
