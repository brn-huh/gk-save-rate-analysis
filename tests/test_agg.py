"""집계 테스트: 게이트 경계, 0나눗셈, within-ouid."""

import json

import pytest

from gksave import agg
from gksave.db import connect_memory


def _insert(con, detail):
    con.execute(
        "INSERT INTO raw_match (match_id, payload) VALUES (?, ?)",
        [detail["matchId"], json.dumps(detail, ensure_ascii=False)],
    )


def _match(match_id, u_ouid, u_sp_id, u_grade, saves, goals,
           o_ouid="O", o_sp_id=600, o_grade=10):
    """U(GK u_sp_id/u_grade)가 상대 O의 유효슛 saves선방+goals실점을 마주하는 2팀 매치."""
    return {
        "matchId": match_id,
        "matchType": 50,
        "matchInfo": [
            {"ouid": u_ouid, "player": [{"spId": u_sp_id, "spPosition": 0, "spGrade": u_grade}],
             "shootDetail": []},
            {"ouid": o_ouid, "player": [{"spId": o_sp_id, "spPosition": 0, "spGrade": o_grade}],
             "shootDetail": [{"result": 1, "type": 1} for _ in range(saves)]
                            + [{"result": 3, "type": 1} for _ in range(goals)]},
        ],
    }


@pytest.fixture
def con():
    c = connect_memory()
    yield c
    c.close()


def test_leaderboard_ranks_by_save_pct(con, sample_detail):
    _insert(con, sample_detail)
    agg.rebuild(con)
    lb = agg.card_leaderboard(con, gate=1)
    ids = [r["gk_sp_id"] for r in lb]
    assert ids == [280000002, 300000001]      # 0.75 > 0.667
    assert lb[0]["rank"] == 1
    assert lb[0]["save_pct"] == pytest.approx(0.75)
    assert lb[1]["save_pct"] == pytest.approx(2 / 3)
    assert lb[0]["matches"] == 1


def test_gate_excludes_thin_samples(con, sample_detail):
    _insert(con, sample_detail)
    agg.rebuild(con)
    assert agg.card_leaderboard(con, gate=2) == []      # 1경기짜리는 게이트 미달
    assert len(agg.card_leaderboard(con, gate=1)) == 2


def test_zero_division_guarded(con):
    # card 700 이 offtarget만 마주 → 유효슛 0. 크래시 없이 save_pct None, 표본은 잡힘.
    detail = {
        "matchId": "Z1", "matchType": 50,
        "matchInfo": [
            {"ouid": "U", "player": [{"spId": 700, "spPosition": 0, "spGrade": 9}],
             "shootDetail": []},
            {"ouid": "O", "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [{"result": 2, "type": 1}, {"result": 2, "type": 1}]},
        ],
    }
    _insert(con, detail)
    agg.rebuild(con)
    lb = agg.card_leaderboard(con, gate=1)
    card = next(r for r in lb if r["gk_sp_id"] == 700)
    assert card["save_pct"] is None
    assert card["matches"] == 1
    # None(유효슛 0)은 실수치 뒤로: None 카드 앞에 실수치 카드가 오면 안 됨
    first_none = next(i for i, r in enumerate(lb) if r["save_pct"] is None)
    assert all(r["save_pct"] is None for r in lb[first_none:])


def test_within_ouid_grade_effect(con):
    # 같은 유저 U · 같은 카드 500: 10강 선방률 0.75, 11강 1.0 → 단계당 Δ +0.25
    _insert(con, _match("g10a", "U", 500, 10, saves=3, goals=1))
    _insert(con, _match("g10b", "U", 500, 10, saves=3, goals=1))
    _insert(con, _match("g11a", "U", 500, 11, saves=4, goals=0))
    _insert(con, _match("g11b", "U", 500, 11, saves=4, goals=0))
    agg.rebuild(con)
    eff = agg.within_ouid_grade_effect(con, min_matches_per_cell=1)
    assert eff["paired_users"] == 1
    assert eff["pairs"] == 1
    assert eff["mean_save_pct_delta_per_grade"] == pytest.approx(0.25)


def test_grade_breakdown(con):
    _insert(con, _match("m1", "U", 500, 10, saves=3, goals=1))
    _insert(con, _match("m2", "U", 500, 11, saves=4, goals=0))
    agg.rebuild(con)
    br = agg.grade_breakdown(con, 500)
    grades = {r["grade"]: r for r in br}
    assert grades[10]["save_pct"] == pytest.approx(0.75)
    assert grades[11]["save_pct"] == pytest.approx(1.0)
