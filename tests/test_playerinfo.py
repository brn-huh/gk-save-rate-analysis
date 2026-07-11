"""fc-info 선수 정보 파싱·적재.

fc-info.com 의 GK 검색 API 응답에서 우리가 쓸 필드(급여·기본OVR·키·몸무게·체형)만
뽑아 player_info 에 캐시한다. 우리가 이미 가진 GK spid 만, 카드당 한 번만 받는다.
"""

import httpx
import pytest

from gksave.db import connect_memory
from gksave.playerinfo import FCINFO_BASE, PlayerInfo, parse_player, sync_player_info

_ITEM = {
    "id": 825192448,
    "name": "M. 테어슈테겐",
    "salary": 24,
    "height": 187,
    "weight": 85,
    "bodyType": "보통",
    "positions": [{"positionId": 20, "name": "GK", "ovr": 113}],
    "price": 999, "img": "…", "grade": 1,   # 안 쓰는 필드
}


def test_parse_extracts_wanted_fields():
    p = parse_player(_ITEM)
    assert p == PlayerInfo(
        spid=825192448, name="M. 테어슈테겐", salary=24,
        ovr=113, height=187, weight=85, body_type="보통",
    )


def test_ovr_comes_from_first_position():
    p = parse_player(_ITEM)
    assert p.ovr == 113


def test_missing_id_returns_none():
    assert parse_player({**_ITEM, "id": None}) is None
    item = dict(_ITEM); del item["id"]
    assert parse_player(item) is None


def test_missing_physical_is_tolerated_as_none():
    # 급여/체격이 빠진 카드도 크래시 없이 부분 저장 (있는 것만)
    p = parse_player({"id": 1, "name": "x", "positions": [{"ovr": 90}]})
    assert p.spid == 1 and p.ovr == 90
    assert p.salary is None and p.height is None and p.body_type is None


def test_empty_positions_leaves_ovr_none():
    p = parse_player({**_ITEM, "positions": []})
    assert p.ovr is None
    assert p.salary == 24        # 나머지는 그대로


def test_non_gk_position_ovr_still_taken_from_first():
    # 검색을 GK(20)로 걸었으므로 positions[0] 이 GK. 그대로 신뢰.
    p = parse_player({**_ITEM, "positions": [{"positionId": 20, "ovr": 120}]})
    assert p.ovr == 120


# ── sync_player_info: 우리 spid만 · 카드당 한 번 · 반복 안 함 ─────────────────

def _gk_page(items, cursor=None):
    return httpx.Response(200, json={"items": items, "nextCursor": cursor, "hasNext": bool(cursor)})


def _mock_client(pages):
    """pages: 응답 리스트. 순서대로 반환하는 fc-info 목 클라이언트."""
    calls = {"n": 0}

    def handler(request):
        i = calls["n"]
        calls["n"] += 1
        return pages[min(i, len(pages) - 1)]

    client = httpx.Client(base_url=FCINFO_BASE, transport=httpx.MockTransport(handler))
    client._calls = calls
    return client


def _seed_gk(con, *spids):
    for sp in spids:
        con.execute(
            "INSERT INTO gk_match (match_id, gk_sp_id, gk_sp_grade) VALUES (?, ?, ?)",
            [f"m{sp}", sp, 1],
        )


def test_stores_only_our_spids():
    con = connect_memory()
    _seed_gk(con, 100, 200)                       # 우리는 100, 200 만 가짐
    client = _mock_client([_gk_page([
        {"id": 100, "name": "가", "salary": 5, "height": 190, "weight": 80,
         "bodyType": "보통", "positions": [{"ovr": 100}]},
        {"id": 999, "name": "남", "salary": 9, "positions": [{"ovr": 130}]},   # 우리 것 아님
    ])])
    r = sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    rows = con.execute("SELECT spid, salary FROM player_info ORDER BY spid").fetchall()
    assert rows == [(100, 5)]                     # 999 는 저장 안 됨
    assert r["new"] == 1


def test_skips_already_cached_and_short_circuits_network():
    con = connect_memory()
    _seed_gk(con, 100)
    con.execute("INSERT INTO player_info (spid, salary) VALUES (100, 5)")   # 이미 있음
    client = _mock_client([_gk_page([{"id": 100, "salary": 999, "positions": []}])])
    r = sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["n"] == 0               # 받을 게 없으니 fc-info 호출 0
    assert r["new"] == 0
    assert con.execute("SELECT salary FROM player_info WHERE spid=100").fetchone()[0] == 5  # 덮어쓰지 않음


def test_paginates_with_cursor():
    con = connect_memory()
    _seed_gk(con, 1, 2)
    client = _mock_client([
        _gk_page([{"id": 1, "salary": 5, "positions": [{"ovr": 90}]}], cursor="c1"),
        _gk_page([{"id": 2, "salary": 7, "positions": [{"ovr": 92}]}], cursor=None),
    ])
    r = sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert r["new"] == 2
    assert client._calls["n"] == 2               # 두 페이지 다 넘김


def test_export_attaches_player_info_by_spid():
    from gksave import agg, export

    con = connect_memory()
    # 카드 하나(spid 500, 10강)를 게이트 통과시킬 만큼 넣는다
    from gksave.codec import encode_payload
    detail = {
        "matchId": "e1", "matchType": 50, "matchDate": "2026-06-20T00:00:00",
        "matchInfo": [
            {"ouid": "U", "player": [{"spId": 500, "spPosition": 0, "spGrade": 10}], "shootDetail": []},
            {"ouid": "O", "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [{"result": 1, "type": 1, "x": 0.9, "y": 0.5}]},
        ],
    }
    con.execute("INSERT INTO raw_match (match_id, payload) VALUES (?, ?)", ["e1", encode_payload(detail)])
    agg.rebuild(con)
    con.execute("INSERT INTO player_info (spid, salary, ovr, height, weight, body_type) "
                "VALUES (500, 24, 113, 187, 85, '보통')")

    c = export.build_payload(con, gate=1)["leaderboard"][0]
    assert c["info"]["salary"] == 24
    assert c["info"]["ovr"] == 113
    assert c["info"]["height"] == 187
    assert c["info"]["body_type"] == "보통"


def test_export_backfills_physical_by_pid():
    """키·몸무게·체형은 실선수(pid) 속성 → 다른 시즌 카드에도 pid 로 채운다.

    급여·OVR 은 카드별이라 역채움하지 않는다.
    """
    from gksave import agg, export
    from gksave.codec import encode_payload

    con = connect_memory()
    # 우리 카드: spid 846193080 (pid 193080). player_info 엔 다른 시즌 카드 231193080 만 있음.
    detail = {
        "matchId": "e2", "matchType": 50, "matchDate": "2026-06-20T00:00:00",
        "matchInfo": [
            {"ouid": "U", "player": [{"spId": 846193080, "spPosition": 0, "spGrade": 10}], "shootDetail": []},
            {"ouid": "O", "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [{"result": 1, "type": 1, "x": 0.9, "y": 0.5}]},
        ],
    }
    con.execute("INSERT INTO raw_match (match_id, payload) VALUES (?, ?)", ["e2", encode_payload(detail)])
    agg.rebuild(con)
    con.execute("INSERT INTO player_info (spid, salary, ovr, height, weight, body_type) "
                "VALUES (231193080, 30, 125, 192, 76, '보통')")

    c = export.build_payload(con, gate=1)["leaderboard"][0]
    assert c["info"]["height"] == 192          # pid 로 역채움
    assert c["info"]["weight"] == 76
    assert c["info"]["body_type"] == "보통"
    assert c["info"].get("salary") is None      # 급여는 카드별 → 역채움 안 함
    assert c["info"].get("ovr") is None


def test_salary_and_all_fields_persisted():
    con = connect_memory()
    _seed_gk(con, 825192448)
    client = _mock_client([_gk_page([{
        "id": 825192448, "name": "M. 테어슈테겐", "salary": 24,
        "height": 187, "weight": 85, "bodyType": "보통",
        "positions": [{"ovr": 113}],
    }])])
    sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    row = con.execute(
        "SELECT salary, ovr, height, weight, body_type FROM player_info WHERE spid=825192448"
    ).fetchone()
    assert row == (24, 113, 187, 85, "보통")     # 급여 포함 전부 저장
