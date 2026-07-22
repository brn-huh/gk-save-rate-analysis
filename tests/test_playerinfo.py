"""fc-info 선수 정보 파싱·적재.

fc-info.com 의 GK 검색 API 응답에서 우리가 쓸 필드(급여·기본OVR·키·몸무게·체형)만
뽑아 player_info 에 캐시한다. 우리가 이미 가진 GK spid 만, 카드당 한 번만 받는다.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from gksave.config import FC_RECHECK_DAYS
from gksave.db import connect_memory
from gksave.playerinfo import (
    FCINFO_BASE,
    PlayerInfo,
    attach_bio,
    attach_trait,
    parse_bio,
    parse_player,
    parse_traits,
    resync_bio,
    season_of,
    season_img_url,
    sync_player_detail,
    sync_player_info,
    sync_season_img,
)

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

def _mock_by_name(catalog):
    """catalog: {name: [player dict, ...]}. names 배열 요청에 매칭 카드를 돌려주는 목."""
    import json as _json
    calls = {"n": 0, "names": []}

    def handler(request):
        calls["n"] += 1
        names = _json.loads(request.content).get("names", [])
        calls["names"].extend(names)
        items = [p for nm in names for p in catalog.get(nm, [])]
        return httpx.Response(200, json={"items": items, "nextCursor": None, "hasNext": False})

    client = httpx.Client(base_url=FCINFO_BASE, transport=httpx.MockTransport(handler))
    client._calls = calls
    return client


def _seed_gk(con, spid, name):
    con.execute("INSERT INTO gk_match (match_id, gk_sp_id, gk_sp_grade) VALUES (?, ?, ?)",
                [f"m{spid}", spid, 1])
    con.execute("INSERT INTO meta_spid (sp_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                [spid, name])


def test_searches_by_our_player_names_and_stores_matches():
    con = connect_memory()
    _seed_gk(con, 100, "가")
    _seed_gk(con, 200, "나")
    client = _mock_by_name({
        "가": [{"id": 100, "name": "가", "salary": 5, "height": 190, "weight": 80,
                "bodyType": "보통", "positions": [{"ovr": 100}]},
               {"id": 999, "name": "가", "salary": 9, "positions": [{"ovr": 130}]}],  # 우리 것 아님
        "나": [{"id": 200, "name": "나", "salary": 7, "positions": [{"ovr": 110}]}],
    })
    r = sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    rows = con.execute("SELECT spid, salary FROM player_info ORDER BY spid").fetchall()
    assert rows == [(100, 5), (200, 7)]          # 999(우리 것 아님)는 저장 안 됨
    assert r["new"] == 2
    assert set(client._calls["names"]) == {"가", "나"}   # 우리 이름만 물어봄


def test_skips_already_cached_and_short_circuits_network():
    con = connect_memory()
    _seed_gk(con, 100, "가")
    con.execute("INSERT INTO player_info (spid, salary) VALUES (100, 5)")   # 이미 있음
    client = _mock_by_name({"가": [{"id": 100, "salary": 999, "positions": []}]})
    r = sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["n"] == 0               # 채울 게 없으니 fc-info 호출 0
    assert r["new"] == 0
    assert con.execute("SELECT salary FROM player_info WHERE spid=100").fetchone()[0] == 5


def test_batches_names_max_10_per_request():
    con = connect_memory()
    cat = {}
    for i in range(23):
        _seed_gk(con, 1000 + i, f"p{i}")
        cat[f"p{i}"] = [{"id": 1000 + i, "salary": i, "positions": [{"ovr": 90}]}]
    client = _mock_by_name(cat)
    r = sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None,
                         name_batch=10)
    assert r["new"] == 23
    assert client._calls["n"] == 3               # 23개 이름 → 10,10,3 → 3 요청


def test_season_of_is_first_three_digits():
    assert season_of(861048940) == 861      # WG 861...
    assert season_of(100238380) == 100      # ICON 100...


def test_season_img_url_from_classimg():
    # fc-info classImg 에서 시즌 엠블럼 URL 을 그대로 뽑는다(넥슨 CDN).
    it = {"id": 861048940, "classImg":
          "https://ssl.nexon.com/s2/game/fc/online/obt/externalAssets/new/season/WG.png"}
    assert season_img_url(it) == \
        "https://ssl.nexon.com/s2/game/fc/online/obt/externalAssets/new/season/WG.png"


def test_season_img_url_none_when_missing():
    assert season_img_url({"id": 1}) is None
    assert season_img_url({"id": 1, "classImg": ""}) is None


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


def test_export_splits_detail_into_details_json(tmp_path):
    """페이지 경량화: index.html 엔 상세(zones/types/extras) 없이 slim, details.json 에 분리."""
    import json as _json

    from gksave import agg, export
    from gksave.codec import encode_payload

    con = connect_memory()
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

    export.export(con, tmp_path, gate=1)

    # details.json: (spid_grade) 키로 상세만
    details = _json.loads((tmp_path / "details.json").read_text(encoding="utf-8"))
    assert "500_10" in details
    assert set(details["500_10"].keys()) <= {"zones", "types", "extras"}
    assert "zones" in details["500_10"]

    # index.html 임베드(slim): 리더보드 카드에 상세 필드 없음
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    import re as _re
    embed = _json.loads(_re.search(
        r'<script id="gk-data"[^>]*>(.*?)</script>', html, _re.S).group(1).replace("\\u003c", "<"))
    card = embed["leaderboard"][0]
    assert "zones" not in card and "types" not in card and "extras" not in card
    assert card["gk_sp_id"] == 500                       # 목록 필드는 남아 있음

    # leaderboard.json(다운로드용)은 전체 유지
    full = _json.loads((tmp_path / "leaderboard.json").read_text(encoding="utf-8"))
    assert "zones" in full["leaderboard"][0]

    # 슬림 카드엔 상황별 정렬용 sit 요약(근/중거리+상황 {pct,shots})이 있고, 분모도 채워짐.
    # 근거리=박스 안, 중거리=박스 밖(넥슨 inPenalty 원본값).
    assert "sit" in card
    assert set(card["sit"]) == {"near", "mid", "oneone", "assisted"}
    assert "shots" in card["sit"]["near"] and "pct" in card["sit"]["near"]
    # agg 가 상황 분모(*_shots)를 내보내야 sit 상황 게이트가 동작한다
    assert "in_pen_shots" in full["leaderboard"][0]["extras"]
    assert "unassisted_shots" in full["leaderboard"][0]["extras"]


def test_export_attaches_season_img():
    from gksave import agg, export
    from gksave.codec import encode_payload

    con = connect_memory()
    detail = {
        "matchId": "s1", "matchType": 50, "matchDate": "2026-06-20T00:00:00",
        "matchInfo": [
            {"ouid": "U", "player": [{"spId": 861048940, "spPosition": 0, "spGrade": 9}], "shootDetail": []},
            {"ouid": "O", "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [{"result": 1, "type": 1, "x": 0.9, "y": 0.5}]},
        ],
    }
    con.execute("INSERT INTO raw_match (match_id, payload) VALUES (?, ?)", ["s1", encode_payload(detail)])
    agg.rebuild(con)
    con.execute("INSERT INTO season_img (season_id, img) VALUES (861, 'https://x/WG.png')")

    c = export.build_payload(con, gate=1)["leaderboard"][0]
    assert c["season_img"] == "https://x/WG.png"   # season_id 861 로 매칭


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
    _seed_gk(con, 825192448, "M. 테어슈테겐")
    client = _mock_by_name({"M. 테어슈테겐": [{
        "id": 825192448, "name": "M. 테어슈테겐", "salary": 24,
        "height": 187, "weight": 85, "bodyType": "보통",
        "positions": [{"ovr": 113}],
    }]})
    sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    row = con.execute(
        "SELECT salary, ovr, height, weight, body_type FROM player_info WHERE spid=825192448"
    ).fetchone()
    assert row == (24, 113, 187, 85, "보통")     # 급여 포함 전부 저장


# ── 국가·클럽 파싱 (fc-info 상세페이지 HTML) ─────────────────────────────────

def _detail_html(nation_code, nation_name, clubs):
    """실제 fc-info 상세페이지의 국가·클럽 마크업을 최소 재현한 HTML.

    클럽 경력은 실제처럼 __next_f 스트리밍 JSON(백슬래시 이스케이프)으로 재현한다.
    startYear 를 내림차순으로 줘 clubs 입력 순서가 그대로 최신순 출력이 되게 한다.
    league 를 중첩해 클럽명만(리그명 아님) 잡히는지도 함께 검증한다.
    """
    flag = (f'<img src="https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/'
            f'countries/smallflags/{nation_code}.png" alt="nationality"/>'
            f'<span>{nation_name}</span>') if nation_code is not None else ''
    items = ','.join(
        f'\\"id\\":{100 + i},\\"club\\":{{\\"id\\":{i},\\"name\\":\\"{c}\\",'
        f'\\"league\\":{{\\"id\\":1,\\"name\\":\\"어떤리그\\"}}}},\\"startYear\\":{2020 - i}'
        for i, c in enumerate(clubs))
    return f'<div>{flag}</div><div>클럽 경력 [{items}]</div>'


def test_parse_bio_single_club():
    code, name, clubs = parse_bio(_detail_html(40, "러시아", ["디나모 모스크바"]))
    assert code == 40 and name == "러시아" and clubs == ["디나모 모스크바"]


def test_parse_bio_multiple_clubs_in_order():
    _, _, clubs = parse_bio(_detail_html(27, "이탈리아", ["파르마", "유벤투스", "파리 생제르맹"]))
    assert clubs == ["파르마", "유벤투스", "파리 생제르맹"]


def test_parse_bio_dedupes_repeated_club():
    # 임대 복귀 등으로 같은 클럽이 두 번 표기되면 첫 등장만 남긴다.
    _, _, clubs = parse_bio(_detail_html(45, "스페인", ["아스널", "브렌트퍼드", "아스널"]))
    assert clubs == ["아스널", "브렌트퍼드"]


def test_parse_bio_missing_nation_keeps_code_none_but_clubs():
    code, name, clubs = parse_bio(_detail_html(None, None, ["FC 포르투"]))
    assert code is None and name is None and clubs == ["FC 포르투"]


# ── sync_player_detail: per-spid 단일 패스로 특성 + 국가·클럽 통합 수집 ────────

def _mock_full(by_spid):
    """{spid: (code, name, [clubs], [traits])} → /player/{spid} 에 국가·클럽+특성 HTML."""
    calls = {"paths": []}

    def handler(request):
        calls["paths"].append(request.url.path)
        spid = int(request.url.path.rsplit("/", 1)[-1])
        code, name, clubs, traits = by_spid.get(spid, (None, None, [], []))
        return httpx.Response(200, text=_detail_html(code, name, clubs) + _trait_html(traits))

    client = httpx.Client(base_url=FCINFO_BASE, transport=httpx.MockTransport(handler))
    client._calls = calls
    return client


def test_sync_detail_stores_traits_and_bio_in_one_pass():
    con = connect_memory()
    _seed_gk(con, 100238380, "야신")     # pid 238380
    _seed_gk(con, 100001179, "부폰")     # pid 1179
    client = _mock_full({
        100238380: (40, "러시아", ["디나모 모스크바"], [(60, "GK 공중볼 장악"), (20, "GK 능숙한 펀칭")]),
        100001179: (27, "이탈리아", ["유벤투스"], [(43, "스위퍼 키퍼")]),
    })
    r = sync_player_detail(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert r["new"] == 2 and r["bio_new"] == 2 and r["failed"] == 0
    # 특성(spid별, is_new 코드 판별)
    traits = con.execute(
        "SELECT spid, trait_code, is_new FROM player_trait ORDER BY spid, ord").fetchall()
    assert traits == [(100001179, 43, False), (100238380, 60, True), (100238380, 20, False)]
    # 국가·클럽(pid별)
    bios = con.execute("SELECT pid, nation_name FROM player_bio ORDER BY pid").fetchall()
    assert bios == [(1179, "이탈리아"), (238380, "러시아")]
    clubs = con.execute("SELECT pid, club_name FROM player_club ORDER BY pid").fetchall()
    assert clubs == [(1179, "유벤투스"), (238380, "디나모 모스크바")]


def test_sync_detail_traits_per_spid_bio_per_pid():
    # 같은 선수(pid 167495) 두 시즌: 특성은 카드별 저장, 국가·클럽은 pid당 1회.
    con = connect_memory()
    _seed_gk(con, 848167495, "노이어A")
    _seed_gk(con, 272167495, "노이어B")
    client = _mock_full({
        848167495: (21, "독일", ["바이에른 뮌헨"], [(57, "GK 빠른 반응"), (15, "긴 패스 선호")]),
        272167495: (21, "독일", ["바이에른 뮌헨"], [(21, "GK 멀리 던지기")]),
    })
    sync_player_detail(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert len(client._calls["paths"]) == 2                     # 특성은 카드(spid)별 요청
    assert con.execute("SELECT count(*) FROM player_trait WHERE spid=848167495").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM player_trait WHERE spid=272167495").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM player_bio").fetchone()[0] == 1   # pid 167495 하나


def test_sync_detail_incremental_skips_trait_cached_spid():
    con = connect_memory()
    _seed_gk(con, 100238380, "야신")
    con.execute("INSERT INTO player_trait (spid, ord, trait_code, trait_name, is_new) "
                "VALUES (100238380, 0, 60, 'X', TRUE)")
    con.execute("INSERT INTO player_bio (pid, nation_code, nation_name) VALUES (238380, 40, '러시아')")
    client = _mock_full({100238380: (99, "바뀜", ["X"], [(99, "바뀜")])})
    r = sync_player_detail(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["paths"] == [] and r["new"] == 0


def test_sync_detail_revisits_for_missing_bio_only():
    # 특성은 이미 있지만 국가·클럽(bio)이 없는 pid → 대표 spid 로 재방문해 bio 만 채운다
    # (클럽 파싱 개선 후 bio 재수집 경로). 특성은 다시 저장하지 않는다.
    con = connect_memory()
    _seed_gk(con, 100238380, "야신")     # pid 238380
    con.execute("INSERT INTO player_trait (spid, ord, trait_code, trait_name, is_new) "
                "VALUES (100238380, 0, 60, 'X', TRUE)")
    # player_bio 는 비어 있음 → 특성 캐시됐어도 bio 때문에 재방문해야 한다
    client = _mock_full({100238380: (40, "러시아", ["디나모 모스크바", "스파르타크"], [(99, "무시")])})
    r = sync_player_detail(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["paths"] == ["/player/100238380"]       # 재방문함
    assert r["new"] == 0 and r["bio_new"] == 1                    # 특성 신규 아님, bio 신규
    assert con.execute(                                          # 특성은 원래 것 유지(99 로 안 덮음)
        "SELECT trait_code FROM player_trait WHERE spid=100238380").fetchall() == [(60,)]
    assert con.execute("SELECT nation_name FROM player_bio WHERE pid=238380").fetchone()[0] == "러시아"
    assert [r[0] for r in con.execute(
        "SELECT club_name FROM player_club WHERE pid=238380 ORDER BY ord").fetchall()] \
        == ["디나모 모스크바", "스파르타크"]


def test_resync_bio_uses_majority_nation():
    # 같은 선수(pid 234642)의 여러 시즌 카드 국적이 세네갈 3 vs 프랑스 1(fc-info 오류) →
    # 다수결로 세네갈 채택. 클럽은 가장 많이 담긴 카드(3개)를 채택.
    con = connect_memory()
    for spid in (216234642, 260234642, 502234642, 845234642):
        _seed_gk(con, spid, "멘디")
    client = _mock_full({
        216234642: (18, "프랑스", ["첼시"], []),                       # 오류 카드
        260234642: (136, "세네갈", ["첼시", "스타드 렌"], []),
        502234642: (136, "세네갈", ["첼시"], []),
        845234642: (136, "세네갈", ["첼시", "스타드 렌", "스타드 랭스"], []),
    })
    r = resync_bio(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert r["got"] == 4 and r["split"] == 1                          # 1명은 카드 간 불일치
    assert con.execute(
        "SELECT nation_code, nation_name FROM player_bio WHERE pid=234642").fetchone() == (136, "세네갈")
    assert [r[0] for r in con.execute(
        "SELECT club_name FROM player_club WHERE pid=234642 ORDER BY ord").fetchall()] \
        == ["첼시", "스타드 렌", "스타드 랭스"]


def test_sync_detail_limit_caps_requests():
    con = connect_memory()
    for i in range(5):
        _seed_gk(con, 100000000 + i, f"p{i}")
    client = _mock_full({100000000 + i: (i, f"N{i}", [f"C{i}"], [(20, "t")]) for i in range(5)})
    r = sync_player_detail(con, client=client, limit=2, sleep=lambda _x: None, log=lambda _m: None)
    assert r["new"] == 2 and len(client._calls["paths"]) == 2


def test_export_attaches_bio_by_pid():
    from gksave import agg, export
    from gksave.codec import encode_payload

    con = connect_memory()
    # 카드 spid 846193080 (pid 193080). bio 는 pid 193080 로 저장.
    detail = {
        "matchId": "b1", "matchType": 50, "matchDate": "2026-06-20T00:00:00",
        "matchInfo": [
            {"ouid": "U", "player": [{"spId": 846193080, "spPosition": 0, "spGrade": 10}], "shootDetail": []},
            {"ouid": "O", "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [{"result": 1, "type": 1, "x": 0.9, "y": 0.5}]},
        ],
    }
    con.execute("INSERT INTO raw_match (match_id, payload) VALUES (?, ?)", ["b1", encode_payload(detail)])
    agg.rebuild(con)
    con.execute("INSERT INTO player_bio (pid, nation_code, nation_name) VALUES (193080, 21, '독일')")
    con.execute("INSERT INTO player_club (pid, ord, club_name) VALUES (193080, 0, '바이에른 뮌헨')")

    c = export.build_payload(con, gate=1)["leaderboard"][0]
    assert c["bio"]["nation_code"] == 21
    assert c["bio"]["nation_name"] == "독일"
    assert c["bio"]["clubs"] == ["바이에른 뮌헨"]


def test_attach_bio_none_when_absent():
    con = connect_memory()
    cards = [{"gk_sp_id": 846193080}]
    attach_bio(con, cards)          # player_bio·player_club 비어있음
    assert cards[0]["bio"] is None


# ── 특성(트레잇): 파싱 · is_new · per-spid 수집 · attach ─────────────────────

def _trait_html(traits):
    """fc-info 상세페이지 특성 마크업 최소 재현. traits = [(code, name), ...]."""
    items = ''.join(
        f'<div class="PlayerSkills_tooltip__VemEh">'
        f'<img class="PlayerSkills_skillImg___uM3S" src="https://fco.dn.nexoncdn.co.kr/'
        f'live/externalAssets/common/traits/trait_icon_{code:02d}.png" alt="{name}" '
        f'width="100" height="100"/>'
        f'<div class="PlayerSkills_tooltipText__TsiLA">{name}</div></div>' for code, name in traits)
    return f'<div><span>특성</span></div><div class="PlayerSkills_skillList__X">{items}</div>'


def test_parse_traits_extracts_code_and_name_in_order():
    html = _trait_html([(60, "GK 공중볼 장악"), (20, "GK 능숙한 펀칭"), (23, "GK 침착한 1:1 수비")])
    assert parse_traits(html) == [(60, "GK 공중볼 장악"), (20, "GK 능숙한 펀칭"), (23, "GK 침착한 1:1 수비")]


def test_parse_traits_empty_when_none():
    assert parse_traits("<div>특성 없음</div>") == []


def test_attach_trait_by_spid():
    con = connect_memory()
    con.execute("INSERT INTO player_trait (spid, ord, trait_code, trait_name, is_new) VALUES "
                "(500, 0, 60, 'GK 공중볼 장악', TRUE), (500, 1, 43, '스위퍼 키퍼', FALSE)")
    cards = [{"gk_sp_id": 500}, {"gk_sp_id": 999}]
    attach_trait(con, cards)
    assert cards[0]["traits"] == [
        {"code": 60, "name": "GK 공중볼 장악", "is_new": True},
        {"code": 43, "name": "스위퍼 키퍼", "is_new": False},
    ]
    assert cards[1]["traits"] == []      # 특성 없는 카드


# ── 조회 기록(negative cache): "받아봤지만 없더라" 를 기억해 재조회를 막는다 ──────
# fc-info 에 없는 카드(이름 미등재)나 특성이 0개인 카드는 저장할 행이 없어서, 증분 판정을
# "결과 테이블에 행이 있냐" 로만 하면 매 실행마다 다시 조회된다. 조회 시도 자체를 기록한다.

def test_sync_info_remembers_attempt_when_player_not_found():
    con = connect_memory()
    _seed_gk(con, 100, "없는선수")
    client = _mock_by_name({})                    # fc-info 가 아무것도 안 돌려줌
    sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["n"] == 1                # 1회는 물어봤다
    sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["n"] == 1                # 두 번째엔 안 물어본다


def test_sync_detail_remembers_fetch_of_traitless_card():
    con = connect_memory()
    _seed_gk(con, 100238380, "야신")
    # 페이지는 정상인데 특성이 0개 → player_trait 에 남는 행이 없다
    client = _mock_full({100238380: (40, "러시아", ["디나모 모스크바"], [])})
    sync_player_detail(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["paths"] == ["/player/100238380"]
    sync_player_detail(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["paths"] == ["/player/100238380"]   # 재조회 없음


def test_sync_detail_does_not_remember_failed_fetch():
    # 일시적 실패까지 기억하면 그 카드는 TTL 동안 영영 못 받는다 → 성공한 것만 기록.
    con = connect_memory()
    _seed_gk(con, 100238380, "야신")
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(500)

    client = httpx.Client(base_url=FCINFO_BASE, transport=httpx.MockTransport(handler))
    r = sync_player_detail(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert r["failed"] == 1
    sync_player_detail(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert calls["n"] == 2                        # 실패는 기억 안 하므로 다시 시도


def test_fetch_log_expires_so_new_cards_get_a_retry():
    # fc-info 는 신규 카드를 늦게 올린다. 영구 차단하면 그 카드는 영영 못 받는다.
    con = connect_memory()
    _seed_gk(con, 100, "가")
    stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=FC_RECHECK_DAYS + 1)
    con.execute("INSERT INTO fc_fetch_log (kind, spid, fetched_at) VALUES ('info', 100, ?)",
                [stale])
    client = _mock_by_name({"가": [{"id": 100, "salary": 5, "positions": [{"ovr": 90}]}]})
    r = sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["n"] == 1 and r["new"] == 1   # 기간이 지났으면 다시 물어본다


def test_sync_season_img_remembers_seasons_missing_from_catalog():
    # 카탈로그에 없는 시즌이 하나라도 남으면 매 실행마다 목록을 최대 60페이지까지 훑는다.
    # 훑어봤다는 사실을 기록해 다음 실행에서 건너뛴다.
    con = connect_memory()
    _seed_gk(con, 861048940, "가")          # 시즌 861 — 카탈로그에 없음
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(200, json={"items": [], "nextCursor": None, "hasNext": False})

    client = httpx.Client(base_url=FCINFO_BASE, transport=httpx.MockTransport(handler))
    r = sync_season_img(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert calls["n"] == 1 and r["need"] == 1     # 못 찾음
    sync_season_img(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert calls["n"] == 1                        # 두 번째엔 목록을 안 훑는다
