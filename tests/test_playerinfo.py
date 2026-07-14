"""fc-info 선수 정보 파싱·적재.

fc-info.com 의 GK 검색 API 응답에서 우리가 쓸 필드(급여·기본OVR·키·몸무게·체형)만
뽑아 player_info 에 캐시한다. 우리가 이미 가진 GK spid 만, 카드당 한 번만 받는다.
"""

import httpx
import pytest

from gksave.db import connect_memory
from gksave.playerinfo import (
    FCINFO_BASE,
    PlayerInfo,
    attach_bio,
    attach_trait,
    parse_bio,
    parse_player,
    parse_traits,
    season_of,
    season_img_url,
    sync_player_bio,
    sync_player_info,
    sync_player_trait,
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
    """실제 fc-info 상세페이지의 국가·클럽 마크업을 최소 재현한 HTML."""
    flag = (f'<img src="https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/'
            f'countries/smallflags/{nation_code}.png" alt="nationality"/>'
            f'<span>{nation_name}</span>') if nation_code is not None else ''
    items = ''.join(
        f'<div class="PlayerClubHistory_clubItem__n146_">'
        f'<div class="PlayerClubHistory_year__SKkzV">1900 ~ 1901</div>'
        f'<div>{c}</div></div>' for c in clubs)
    return f'<div>{flag}</div><div><span>클럽 경력</span></div><div>{items}</div>'


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


# ── sync_player_bio: pid당 1회 · 증분 · 중복제거 ─────────────────────────────

def _mock_detail(by_spid):
    """{spid: (code, name, [clubs])} → /player/{spid}?grade=1 에 상세 HTML 을 돌려주는 목."""
    calls = {"paths": []}

    def handler(request):
        calls["paths"].append(request.url.path)
        spid = int(request.url.path.rsplit("/", 1)[-1])
        code, name, clubs = by_spid.get(spid, (None, None, []))
        return httpx.Response(200, text=_detail_html(code, name, clubs))

    client = httpx.Client(base_url=FCINFO_BASE, transport=httpx.MockTransport(handler))
    client._calls = calls
    return client


def test_sync_bio_fetches_per_pid_and_stores():
    con = connect_memory()
    _seed_gk(con, 100238380, "야신")     # pid 238380
    _seed_gk(con, 100001179, "부폰")     # pid 1179
    client = _mock_detail({
        100238380: (40, "러시아", ["디나모 모스크바"]),
        100001179: (27, "이탈리아", ["파르마", "유벤투스"]),
    })
    r = sync_player_bio(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert r["new"] == 2 and r["failed"] == 0
    bios = con.execute("SELECT pid, nation_code, nation_name FROM player_bio ORDER BY pid").fetchall()
    assert bios == [(1179, 27, "이탈리아"), (238380, 40, "러시아")]
    clubs = con.execute("SELECT pid, ord, club_name FROM player_club ORDER BY pid, ord").fetchall()
    assert clubs == [(1179, 0, "파르마"), (1179, 1, "유벤투스"), (238380, 0, "디나모 모스크바")]


def test_sync_bio_one_request_per_pid_across_seasons():
    con = connect_memory()
    # 같은 실선수(pid 1179)의 두 시즌 카드 → pid당 1요청만
    _seed_gk(con, 100001179, "부폰")
    _seed_gk(con, 300001179, "부폰(다른 시즌)")
    client = _mock_detail({100001179: (27, "이탈리아", ["유벤투스"]),
                           300001179: (27, "이탈리아", ["유벤투스"])})
    sync_player_bio(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert len(client._calls["paths"]) == 1                # pid 1179 하나만 요청
    assert con.execute("SELECT count(*) FROM player_bio").fetchone()[0] == 1


def test_sync_bio_incremental_skips_cached():
    con = connect_memory()
    _seed_gk(con, 100238380, "야신")
    con.execute("INSERT INTO player_bio (pid, nation_code, nation_name) VALUES (238380, 40, '러시아')")
    client = _mock_detail({100238380: (99, "바뀜", ["X"])})
    r = sync_player_bio(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["paths"] == []                    # 이미 있으니 호출 0
    assert r["new"] == 0


def test_sync_bio_limit_caps_requests():
    con = connect_memory()
    for i in range(5):
        _seed_gk(con, 100000000 + i, f"p{i}")
    client = _mock_detail({100000000 + i: (i, f"N{i}", [f"C{i}"]) for i in range(5)})
    r = sync_player_bio(con, client=client, limit=2, sleep=lambda _x: None, log=lambda _m: None)
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


def _mock_trait(by_spid):
    """{spid: [(code,name),...]} → /player/{spid} 에 특성 HTML 을 돌려주는 목."""
    calls = {"paths": []}

    def handler(request):
        calls["paths"].append(request.url.path)
        spid = int(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, text=_trait_html(by_spid.get(spid, [])))

    client = httpx.Client(base_url=FCINFO_BASE, transport=httpx.MockTransport(handler))
    client._calls = calls
    return client


def test_sync_trait_per_spid_with_is_new_flag():
    con = connect_memory()
    _seed_gk(con, 100238380, "야신")
    _seed_gk(con, 100000488, "칸")
    client = _mock_trait({
        100238380: [(60, "GK 공중볼 장악"), (20, "GK 능숙한 펀칭")],   # 60=신규
        100000488: [(43, "스위퍼 키퍼")],                            # 43=일반
    })
    r = sync_player_trait(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert r["new"] == 2 and r["failed"] == 0
    rows = con.execute(
        "SELECT spid, ord, trait_code, trait_name, is_new FROM player_trait ORDER BY spid, ord"
    ).fetchall()
    assert rows == [
        (100000488, 0, 43, "스위퍼 키퍼", False),
        (100238380, 0, 60, "GK 공중볼 장악", True),    # 코드 60 → 신규
        (100238380, 1, 20, "GK 능숙한 펀칭", False),
    ]


def test_sync_trait_is_per_spid_not_per_pid():
    # 같은 실선수(pid)의 두 시즌 카드는 특성이 달라 각각 저장된다(spid별).
    con = connect_memory()
    _seed_gk(con, 848167495, "노이어A")
    _seed_gk(con, 272167495, "노이어B")   # 같은 pid 167495, 다른 시즌
    client = _mock_trait({
        848167495: [(57, "GK 빠른 반응"), (15, "긴 패스 선호")],
        272167495: [(21, "GK 멀리 던지기")],
    })
    sync_player_trait(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert len(client._calls["paths"]) == 2                 # 카드별 요청
    n1 = con.execute("SELECT count(*) FROM player_trait WHERE spid=848167495").fetchone()[0]
    n2 = con.execute("SELECT count(*) FROM player_trait WHERE spid=272167495").fetchone()[0]
    assert n1 == 2 and n2 == 1


def test_sync_trait_incremental_skips_cached():
    con = connect_memory()
    _seed_gk(con, 100238380, "야신")
    con.execute("INSERT INTO player_trait (spid, ord, trait_code, trait_name, is_new) "
                "VALUES (100238380, 0, 60, 'X', TRUE)")
    client = _mock_trait({100238380: [(99, "바뀜")]})
    r = sync_player_trait(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["paths"] == [] and r["new"] == 0


def test_sync_trait_limit_caps_requests():
    con = connect_memory()
    for i in range(5):
        _seed_gk(con, 100000000 + i, f"p{i}")
    client = _mock_trait({100000000 + i: [(20, "t")] for i in range(5)})
    r = sync_player_trait(con, client=client, limit=2, sleep=lambda _x: None, log=lambda _m: None)
    assert r["new"] == 2 and len(client._calls["paths"]) == 2


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
