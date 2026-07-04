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


def test_grade_leaderboard_splits_by_grade(con):
    # 같은 카드(sp 500)를 10강·11강으로 → 퉁치지 않고 2행
    _insert(con, _match("a", "U", 500, 10, saves=3, goals=1))
    _insert(con, _match("b", "W", 500, 11, saves=4, goals=0))
    agg.rebuild(con)
    lb = agg.grade_leaderboard(con, gate=1)
    by_grade = {(r["gk_sp_id"], r["grade"]): r for r in lb}
    assert (500, 10) in by_grade and (500, 11) in by_grade   # 강화별 분리
    assert by_grade[(500, 11)]["save_pct"] == pytest.approx(1.0)
    assert by_grade[(500, 10)]["save_pct"] == pytest.approx(0.75)


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


def _match_faced(mid, ouid, sp_id, grade, faced):
    """우리 카드(GK sp_id/grade)가 상대의 슛 faced=[(result,type,x,y)]를 마주하는 매치."""
    return {
        "matchId": mid, "matchType": 50, "matchDate": "2026-06-20T00:00:00",
        "matchInfo": [
            {"ouid": ouid, "player": [{"spId": sp_id, "spPosition": 0, "spGrade": grade}],
             "shootDetail": []},
            {"ouid": "O" + ouid, "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [{"result": r, "type": t, "x": x, "y": y} for (r, t, x, y) in faced]},
        ],
    }


def test_gsax_league_sum_is_zero(con):
    # 서로 다른 난이도(타입·거리)의 슛을 두 카드가 마주 → GSAx 리그 합은 0
    _insert(con, _match_faced("g1", "A", 500, 10, [
        (1, 2, 0.9, 0.5), (3, 2, 0.95, 0.5), (1, 6, 0.85, 0.4), (1, 1, 0.6, 0.5)]))
    _insert(con, _match_faced("g2", "B", 700, 9, [
        (3, 2, 0.92, 0.5), (1, 6, 0.88, 0.55), (3, 1, 0.7, 0.5), (1, 1, 0.65, 0.6)]))
    agg.rebuild(con)
    lb = agg.gsax_leaderboard(con, gate=1, dist_bins=3)
    assert len(lb) == 2
    assert abs(sum(r["gsax"] for r in lb)) < 1e-6          # 리그 Σ GSAx = 0
    assert all(r["gsax_per_shot"] is not None for r in lb)
    assert lb[0]["rank"] == 1 and lb[0]["gsax_per_shot"] >= lb[1]["gsax_per_shot"]


def test_card_extras(con):
    # GK 본인 스탯 + 상황별(박스 안/밖·1대1·연계) 검증
    gk_status = {"spRating": 7.5, "passTry": 20, "passSuccess": 18,
                 "aerialTry": 4, "aerialSuccess": 3}
    detail = {
        "matchId": "e1", "matchType": 50, "matchDate": "2026-06-20T00:00:00",
        "matchInfo": [
            {"ouid": "U", "player": [{"spId": 500, "spPosition": 0, "spGrade": 10,
                                      "status": gk_status}], "shootDetail": []},
            {"ouid": "O", "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [
                 {"result": 1, "type": 1, "inPenalty": True, "assist": False, "x": 0.9, "y": 0.5},
                 {"result": 3, "type": 1, "inPenalty": True, "assist": False, "x": 0.95, "y": 0.5},
                 {"result": 1, "type": 1, "inPenalty": True, "assist": True, "x": 0.88, "y": 0.5},
                 {"result": 1, "type": 1, "inPenalty": False, "assist": False, "x": 0.7, "y": 0.5},
             ]},
        ],
    }
    _insert(con, detail)
    agg.rebuild(con)
    e = agg.card_extras_all(con)[(500, 10)]
    assert e["shots"] == 4 and e["matches"] == 1 and e["exposure"] == pytest.approx(4.0)
    assert e["in_pen_save"] == pytest.approx(2 / 3)      # 박스 안 3중 2막
    assert e["out_pen_save"] == pytest.approx(1.0)
    assert e["unassisted_save"] == pytest.approx(0.5)    # 1대1: 2중 1
    assert e["assisted_save"] == pytest.approx(1.0)      # 연계: 1중 1
    assert e["gk_rating"] == pytest.approx(7.5)
    assert e["pass_pct"] == pytest.approx(0.9)           # 18/20
    assert e["aerial_pct"] == pytest.approx(0.75)        # 3/4


def test_zone_and_type_breakdown(con):
    # 거리·타입 다른 슛들: (result,type,x,y)
    _insert(con, _match_faced("z1", "A", 500, 10, [
        (1, 1, 0.97, 0.5),   # 초근(2.75m) 노멀 선방
        (3, 1, 0.90, 0.5),   # 근(9.2m)   노멀 실점
        (1, 1, 0.83, 0.5),   # 중(15.6m)  노멀 선방
        (1, 2, 0.70, 0.5),   # 원(27.5m)  감아차기 선방
        (1, 3, 0.90, 0.4),   # 중(13m)    헤더 선방
    ]))
    agg.rebuild(con)

    zones = {z["zone"]: z for z in agg.zone_breakdown(con, 500)}
    assert zones["초근거리(0-5m)"]["shots"] == 1
    assert zones["근거리(5-11m)"]["shots"] == 1 and zones["근거리(5-11m)"]["saves"] == 0
    assert zones["중거리(11-16.5m)"]["shots"] == 2   # 노멀 + 헤더
    assert zones["원거리(16.5m+)"]["shots"] == 1

    tb = agg.type_breakdown(con, 500)
    by = {t["name"]: t for t in tb["by_type"]}
    assert by["노멀"]["shots"] == 3 and by["노멀"]["saves"] == 2
    assert by["감아차기"]["shots"] == 1
    assert tb["header"] == {"shots": 1, "saves": 1, "save_pct": 1.0}
    assert tb["foot"]["shots"] == 4 and tb["foot"]["saves"] == 3


def test_gsax_exclude_shortest(con):
    # 초근(<5m: x≈0.98) + 원거리 섞기 → min_dist_m=5 면 초근 제외로 슛 수 감소, Σ=0 유지
    _insert(con, _match_faced("s1", "A", 500, 10, [
        (1, 1, 0.98, 0.5), (3, 1, 0.985, 0.5),   # 초근(~1.5m)
        (1, 2, 0.80, 0.5), (1, 6, 0.75, 0.55)]))  # 원거리
    _insert(con, _match_faced("s2", "B", 700, 9, [
        (3, 1, 0.98, 0.5),                          # 초근
        (1, 2, 0.82, 0.5), (3, 1, 0.70, 0.5), (1, 6, 0.78, 0.6)]))
    agg.rebuild(con)
    full = agg.gsax_leaderboard(con, gate=1, dist_bins=2)
    ex = agg.gsax_leaderboard(con, gate=1, dist_bins=2, min_dist_m=5.0)
    assert sum(r["shots"] for r in ex) < sum(r["shots"] for r in full)  # 초근 빠짐
    assert abs(sum(r["gsax"] for r in ex)) < 1e-6                        # 필터셋에서도 Σ=0


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
