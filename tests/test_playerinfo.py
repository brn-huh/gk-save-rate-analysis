"""공식 데이터센터 선수 정보 파싱·적재 및 기존 캐시/export 회귀 검증."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from gksave.config import FC_RECHECK_DAYS
from gksave.db import connect_memory
from gksave.playerinfo import (
    DATACENTER_BASE,
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


def _ability_html(item):
    from html import escape
    positions=item.get('positions') or []
    ovr=positions[0].get('ovr') if positions else None
    e=lambda value:escape(str(value),quote=True)
    return f"""<div class="ovr value">{e(ovr)}</div><div class="pay_side">{e(item.get('salary'))}</div>
<div class="name">{e(item.get('name','테스트 선수'))}</div>
<span class="etc height">{e(item.get('height',''))}cm</span><span class="etc weight">{e(item.get('weight',''))}kg</span>
<span class="etc physical">{e(item.get('bodyType',''))}</span>
<div class="season"><img src="{e(item.get('classImg',''))}"></div>
<script>var playerData = {{spid: {item.get('id',0)}, strong: 1}};</script>"""


def _seed_export_matches(con, detail):
    """카드마다 세 유저로 분산해 35% 편중 기준을 통과하는 export 표본."""
    from copy import deepcopy
    from gksave import agg
    from gksave.codec import encode_payload

    for i in range(3):
        match = deepcopy(detail)
        match["matchId"] = f"{detail['matchId']}-{i}"
        for team, info in enumerate(match["matchInfo"]):
            info["ouid"] = f"synthetic-export-{i}-{team}"
        con.execute("INSERT INTO raw_match (match_id, payload) VALUES (?, ?)",
                    [match["matchId"], encode_payload(match)])
    agg.rebuild(con)


def test_parse_extracts_wanted_fields():
    p=parse_player(_ability_html(_ITEM),_ITEM['id'])
    assert p == PlayerInfo(spid=825192448,name="M. 테어슈테겐",salary=24,ovr=113,height=187,weight=85,body_type="보통")


def test_missing_or_wrong_card_id_returns_none():
    assert parse_player(_ability_html(_ITEM),300000010) is None
    assert parse_player('<html>점검 중</html>',_ITEM['id']) is None


def test_missing_physical_is_tolerated_as_none():
    p=parse_player(_ability_html({'id':1,'name':'x','salary':5,'positions':[{'ovr':90}]}),1)
    assert p.spid==1 and p.ovr==90 and p.height is None


def test_missing_base_ovr_is_not_cached():
    assert parse_player(_ability_html({**_ITEM,'positions':[]}),_ITEM['id']) is None


def test_non_gk_card_uses_primary_base_ovr():
    assert parse_player(_ability_html({**_ITEM,'positions':[{'positionId':4,'ovr':120}]}),_ITEM['id']).ovr==120


def _mock_cards(catalog):
    import json
    calls = {'n': 0, 'names': []}
    def handler(request):
        assert request.url.host == 'fc-info.com'
        assert request.url.path == '/local-api/v2/players/search'
        calls['n'] += 1
        name = json.loads(request.content)['names'][0]
        calls['names'].append(name)
        items = [{'name': name, 'salary': 5, 'positions': [{'ovr': 90}], 'grade': 1, **p}
                 for p in catalog.get(name, [])]
        for item in items:
            if not item['positions']:
                item['positions'] = [{'ovr': 90}]
        return httpx.Response(200, json={'items': items, 'hasNext': False})
    client = httpx.Client(base_url=FCINFO_BASE, transport=httpx.MockTransport(handler))
    client._calls = calls
    return client


def _seed_gk(con, spid, name):
    con.execute("INSERT INTO gk_match (match_id, gk_sp_id, gk_sp_grade) VALUES (?, ?, ?)",
                [f"m{spid}", spid, 1])
    con.execute("INSERT INTO meta_spid (sp_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                [spid, name])


def test_searches_names_and_stores_only_our_exact_card_ids():
    con=connect_memory()
    _seed_gk(con,100,'가');_seed_gk(con,200,'나')
    client=_mock_cards({'가':[{'id':100,'salary':5},{'id':999,'salary':9}], '나':[{'id':200,'salary':7}]})
    delays=[]
    assert sync_player_info(con,client=client,sleep=delays.append,log=lambda _:None)['new']==2
    assert con.execute('SELECT spid,salary FROM player_info ORDER BY spid').fetchall()==[(100,5),(200,7)]
    assert client._calls['names']==['가','나'] and delays==[3.0,3.0]


def test_skips_already_cached_and_short_circuits_network():
    con=connect_memory();_seed_gk(con,100,'가')
    con.execute('INSERT INTO player_info (spid,salary) VALUES (100,5)')
    client=_mock_cards({'가':[{'id':100,'salary':999}]})
    assert sync_player_info(con,client=client,sleep=lambda _:None,log=lambda _:None)['new']==0
    assert client._calls['n']==0 and con.execute('SELECT salary FROM player_info WHERE spid=100').fetchone()[0]==5


def test_defender_collection_caches_only_our_cards_once():
    con=connect_memory();_seed_gk(con,300000001,'골키퍼')
    con.execute('INSERT INTO player_info (spid) VALUES (300000001)')
    con.execute("INSERT INTO defense_match (match_id,gk_ouid,cb1_sp_id,cb2_sp_id) VALUES ('synthetic','synthetic-user',300000010,300000020)")
    for spid in (300000010,300000020):
        con.execute("INSERT INTO meta_spid (sp_id,name) VALUES (?, '센터백')", [spid])
    client=_mock_cards({'센터백':[{'id':s,'salary':20,'classImg':'https://ssl.nexon.com/season.png'} for s in [300000010,300000020,300000999]]})
    assert sync_player_info(con,client=client,sleep=lambda _:None,log=lambda _:None)['new']==0
    assert sync_player_info(con,client=client,include_defenders=True,sleep=lambda _:None,log=lambda _:None)['new']==2
    assert client._calls['names']==['센터백']
    assert con.execute('SELECT img FROM season_img WHERE season_id=300').fetchone()[0].endswith('season.png')
    assert sync_player_info(con,client=client,include_defenders=True,sleep=lambda _:None,log=lambda _:None)['new']==0
    assert client._calls['n']==1


def test_season_of_is_first_three_digits():
    assert season_of(861048940) == 861      # WG 861...
    assert season_of(100238380) == 100      # ICON 100...


def test_season_img_url_from_official_markup():
    url='https://ssl.nexon.com/s2/game/fc/online/obt/externalAssets/new/season/WG.png'
    assert season_img_url(_ability_html({**_ITEM,'classImg':url}))==url


def test_season_img_url_none_when_missing():
    assert season_img_url(_ability_html(_ITEM)) is None
    assert season_img_url('<html>점검</html>') is None


def test_export_attaches_player_info_by_spid():
    from gksave import export

    con = connect_memory()
    # 카드 하나(spid 500, 10강)를 게이트 통과시킬 만큼 넣는다
    detail = {
        "matchId": "e1", "matchType": 50, "matchDate": "2026-06-20T00:00:00",
        "matchInfo": [
            {"ouid": "U", "player": [{"spId": 500, "spPosition": 0, "spGrade": 10}], "shootDetail": []},
            {"ouid": "O", "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [{"result": 1, "type": 1, "x": 0.9, "y": 0.5}]},
        ],
    }
    _seed_export_matches(con, detail)
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

    from gksave import export

    con = connect_memory()
    detail = {
        "matchId": "e1", "matchType": 50, "matchDate": "2026-06-20T00:00:00",
        "matchInfo": [
            {"ouid": "U", "player": [{"spId": 500, "spPosition": 0, "spGrade": 10}], "shootDetail": []},
            {"ouid": "O", "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [{"result": 1, "type": 1, "x": 0.9, "y": 0.5}]},
        ],
    }
    _seed_export_matches(con, detail)

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
    from gksave import export

    con = connect_memory()
    detail = {
        "matchId": "s1", "matchType": 50, "matchDate": "2026-06-20T00:00:00",
        "matchInfo": [
            {"ouid": "U", "player": [{"spId": 861048940, "spPosition": 0, "spGrade": 9}], "shootDetail": []},
            {"ouid": "O", "player": [{"spId": 600, "spPosition": 0, "spGrade": 10}],
             "shootDetail": [{"result": 1, "type": 1, "x": 0.9, "y": 0.5}]},
        ],
    }
    _seed_export_matches(con, detail)
    con.execute("INSERT INTO season_img (season_id, img) VALUES (861, 'https://x/WG.png')")

    c = export.build_payload(con, gate=1)["leaderboard"][0]
    assert c["season_img"] == "https://x/WG.png"   # season_id 861 로 매칭


def test_export_backfills_physical_by_pid():
    """키·몸무게·체형은 실선수(pid) 속성 → 다른 시즌 카드에도 pid 로 채운다.

    급여·OVR 은 카드별이라 역채움하지 않는다.
    """
    from gksave import export

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
    _seed_export_matches(con, detail)
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
    client = _mock_cards({"M. 테어슈테겐": [{
        "id": 825192448, "name": "M. 테어슈테겐", "salary": 24,
        "height": 187, "weight": 85, "bodyType": "보통",
        "positions": [{"ovr": 113}],
    }]})
    sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    row = con.execute(
        "SELECT salary, ovr, height, weight, body_type FROM player_info WHERE spid=825192448"
    ).fetchone()
    assert row == (24, 113, 187, 85, "보통")     # 급여 포함 전부 저장


# ── 국가·클럽 파싱 (공식 데이터센터 상세페이지 HTML) ─────────────────────────────────

def _detail_html(nation_code,nation_name,clubs):
    from html import escape
    nation=(f'<div class="etc nation"><span class="img"><img src="https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/countries/smallflags/{nation_code}.png"></span><span class="txt">{escape(nation_name)}</span></div>') if nation_code is not None else ''
    history=''.join(f'<div class="td club">{escape(c)}</div>' for c in clubs)
    return f'{nation}<div class="content data_detail_club">{history}</div>'


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
    from urllib.parse import parse_qs
    calls={'paths':[], 'detail_paths':[]}
    def handler(request):
        spid=int(parse_qs(request.content.decode())['spid'][0]) if request.method=='POST' else int(request.url.params['spid'])
        code,name,clubs,traits=by_spid.get(spid,(None,None,[],[]))
        if request.method=='POST':
            calls['paths'].append(f'{request.url.path}?spid={spid}')
            html=_ability_html({'id':spid,'salary':5,'positions':[{'ovr':90}]})+_detail_html(code,name,[])+_trait_html(traits)
        else:
            calls['detail_paths'].append(f'{request.url.path}?spid={spid}')
            html=_detail_html(None,None,clubs)
        return httpx.Response(200,text=html)
    client=httpx.Client(base_url=DATACENTER_BASE,transport=httpx.MockTransport(handler));client._calls=calls
    return client


def test_sync_detail_stores_traits_and_bio_in_one_pass():
    con = connect_memory()
    _seed_gk(con, 100238380, "야신")     # pid 238380
    _seed_gk(con, 100001179, "부폰")     # pid 1179
    client = _mock_full({
        100238380: (40, "러시아", ["디나모 모스크바"], [(60, "GK 공중볼 장악"), (20, "GK 능숙한 펀칭")]),
        100001179: (27, "이탈리아", ["유벤투스"], [(43, "스위퍼 키퍼")]),
    })
    logs = []
    r = sync_player_detail(con, client=client, sleep=lambda _x: None, log=logs.append)
    assert r["new"] == 2 and r["bio_new"] == 2 and r["failed"] == 0
    assert any("대상 2장" in line for line in logs)
    assert any("1/2 카드" in line and "조회 중" in line for line in logs)
    assert any("2/2 완료" in line for line in logs)
    # 특성(spid별, is_new 코드 판별)
    traits = con.execute(
        "SELECT spid, trait_code, is_new FROM player_trait ORDER BY spid, ord").fetchall()
    assert traits == [(100001179, 43, False), (100238380, 60, True), (100238380, 20, False)]
    # 국가·클럽(pid별)
    bios = con.execute("SELECT pid, nation_name FROM player_bio ORDER BY pid").fetchall()
    assert bios == [(1179, "이탈리아"), (238380, "러시아")]
    clubs = con.execute("SELECT pid, club_name FROM player_club ORDER BY pid").fetchall()
    assert clubs == [(1179, "유벤투스"), (238380, "디나모 모스크바")]


def test_sync_detail_optionally_collects_defender_bio_and_keeps_incremental_cache():
    con = connect_memory()
    con.execute("INSERT INTO defense_match (match_id,gk_ouid,cb1_sp_id,cb2_sp_id) "
                "VALUES ('synthetic-defense','synthetic-user',300000010,300000020)")
    client = _mock_full({
        300000010: (21, "독일", ["테스트 클럽"], [(15, "긴 패스 선호")]),
        300000020: (21, "독일", ["테스트 클럽"], [(15, "긴 패스 선호")]),
    })
    options = dict(client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert sync_player_detail(con, **options)["spids"] == 0
    assert not client._calls["paths"]
    first = sync_player_detail(con, include_defenders=True, limit=1, **options)
    assert first["spids"] == 2 and first["bio_new"] == 1
    second = sync_player_detail(con, include_defenders=True, **options)
    assert second["bio_new"] == 1
    assert con.execute("SELECT pid,nation_name FROM player_bio ORDER BY pid").fetchall() == [(10, "독일"), (20, "독일")]
    assert con.execute("SELECT club_name FROM player_club ORDER BY pid").fetchall() == [("테스트 클럽",), ("테스트 클럽",)]
    assert sync_player_detail(con, include_defenders=True, **options)["need"] == 0
    assert len(client._calls["paths"]) == 2


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
    con.execute("INSERT INTO player_club VALUES (238380,0,'디나모 모스크바')")
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
    assert client._calls["paths"] == ["/datacenter/PlayerAbility?spid=100238380"]       # 재방문함
    assert r["new"] == 0 and r["bio_new"] == 1                    # 특성 신규 아님, bio 신규
    assert con.execute(                                          # 특성은 원래 것 유지(99 로 안 덮음)
        "SELECT trait_code FROM player_trait WHERE spid=100238380").fetchall() == [(60,)]
    assert con.execute("SELECT nation_name FROM player_bio WHERE pid=238380").fetchone()[0] == "러시아"
    assert [r[0] for r in con.execute(
        "SELECT club_name FROM player_club WHERE pid=238380 ORDER BY ord").fetchall()] \
        == ["디나모 모스크바", "스파르타크"]


@pytest.mark.parametrize('nation,clubs', [(None, []), ('', []), ('  ', []), ('러시아', []), (None, ['디나모 모스크바'])])
def test_sync_detail_repairs_incomplete_bio_even_after_recent_success(nation, clubs):
    con = connect_memory()
    _seed_gk(con, 100238380, '야신')
    con.execute("INSERT INTO player_bio (pid,nation_name) VALUES (238380,?)", [nation])
    for i, club in enumerate(clubs):
        con.execute("INSERT INTO player_club VALUES (238380,?,?)", [i, club])
    con.execute("INSERT INTO player_trait VALUES (100238380,0,60,'기존 특성',true)")
    con.execute("INSERT INTO fc_fetch_log (kind,spid) VALUES ('nexon-detail',100238380)")
    client = _mock_full({100238380: (40, '러시아', ['디나모 모스크바'], [(99, '무시')])})
    options = dict(client=client, sleep=lambda _: None, log=lambda _: None)
    result = sync_player_detail(con, **options)
    assert result['bio_new'] == 1
    assert con.execute("SELECT nation_name FROM player_bio WHERE pid=238380").fetchone() == ('러시아',)
    assert con.execute("SELECT club_name FROM player_club WHERE pid=238380").fetchall() == [('디나모 모스크바',)]
    assert con.execute("SELECT trait_code FROM player_trait").fetchall() == [(60,)]
    assert sync_player_detail(con, **options)['need'] == 0


def test_sync_detail_prioritizes_missing_bio_before_trait_refresh_with_limit():
    con = connect_memory()
    _seed_gk(con, 100001179, '골키퍼')
    con.execute("INSERT INTO player_bio (pid,nation_name) VALUES (1179,'이탈리아')")
    con.execute("INSERT INTO player_club VALUES (1179,0,'유벤투스')")
    con.execute("INSERT INTO defense_match (match_id,gk_ouid,cb1_sp_id,cb2_sp_id) "
                "VALUES ('synthetic-defense','synthetic-user',300000010,300000010)")
    client = _mock_full({300000010: (21, '독일', ['테스트 클럽'], [])})
    result = sync_player_detail(con, client=client, include_defenders=True, limit=1,
                                sleep=lambda _: None, log=lambda _: None)
    assert result['bio_new'] == 1
    assert client._calls['paths'] == ['/datacenter/PlayerAbility?spid=300000010']


def test_sync_detail_keeps_known_nation_when_backfilling_missing_clubs():
    con = connect_memory()
    _seed_gk(con, 100238380, '야신')
    con.execute("INSERT INTO player_bio (pid,nation_code,nation_name) VALUES (238380,40,'러시아')")
    client = _mock_full({100238380: (99, '다른 시즌의 국적', ['디나모 모스크바'], [])})
    sync_player_detail(con, client=client, sleep=lambda _: None, log=lambda _: None)
    assert con.execute("SELECT nation_code,nation_name FROM player_bio").fetchone() == (40, '러시아')
    assert con.execute("SELECT club_name FROM player_club").fetchall() == [('디나모 모스크바',)]


def test_sync_detail_keeps_known_clubs_when_response_only_repairs_nation():
    con = connect_memory()
    _seed_gk(con, 100238380, '야신')
    con.execute("INSERT INTO player_bio (pid) VALUES (238380)")
    con.execute("INSERT INTO player_club VALUES (238380,0,'디나모 모스크바')")
    client = _mock_full({100238380: (40, '러시아', [], [])})
    sync_player_detail(con, client=client, sleep=lambda _: None, log=lambda _: None)
    assert con.execute("SELECT nation_name FROM player_bio").fetchone() == ('러시아',)
    assert con.execute("SELECT club_name FROM player_club").fetchall() == [('디나모 모스크바',)]


def test_resync_bio_uses_majority_nation():
    # 같은 선수(pid 234642)의 여러 시즌 카드 국적이 세네갈 3 vs 프랑스 1(공식 데이터센터 오류) →
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
    from gksave import export

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
    _seed_export_matches(con, detail)
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
    """공식 데이터센터 상세페이지 특성 마크업 최소 재현. traits = [(code, name), ...]."""
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
# 공식 데이터센터 에 없는 카드(이름 미등재)나 특성이 0개인 카드는 저장할 행이 없어서, 증분 판정을
# "결과 테이블에 행이 있냐" 로만 하면 매 실행마다 다시 조회된다. 조회 시도 자체를 기록한다.

def test_sync_info_remembers_attempt_when_player_not_found():
    con = connect_memory()
    _seed_gk(con, 100, "없는선수")
    client = _mock_cards({})                    # 공식 데이터센터 가 아무것도 안 돌려줌
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
    assert client._calls["paths"] == ["/datacenter/PlayerAbility?spid=100238380"]
    sync_player_detail(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["paths"] == ["/datacenter/PlayerAbility?spid=100238380"]   # 재조회 없음


def test_sync_detail_does_not_remember_failed_fetch():
    # 일시적 실패까지 기억하면 그 카드는 TTL 동안 영영 못 받는다 → 성공한 것만 기록.
    con = connect_memory()
    _seed_gk(con, 100238380, "야신")
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(500)

    client = httpx.Client(base_url=DATACENTER_BASE, transport=httpx.MockTransport(handler))
    r = sync_player_detail(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert r["failed"] == 1
    sync_player_detail(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert calls["n"] == 2                        # 실패는 기억 안 하므로 다시 시도


def test_fetch_log_expires_so_new_cards_get_a_retry():
    # 공식 데이터센터 는 신규 카드를 늦게 올린다. 영구 차단하면 그 카드는 영영 못 받는다.
    con = connect_memory()
    _seed_gk(con, 100, "가")
    stale = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=FC_RECHECK_DAYS + 1)
    con.execute("INSERT INTO fc_fetch_log (kind, spid, fetched_at) VALUES ('fc-info-info', 100, ?)",
                [stale])
    client = _mock_cards({"가": [{"id": 100, "salary": 5, "positions": [{"ovr": 90}]}]})
    r = sync_player_info(con, client=client, sleep=lambda _x: None, log=lambda _m: None)
    assert client._calls["n"] == 1 and r["new"] == 1   # 기간이 지났으면 다시 물어본다


def test_retry_missing_ignores_recent_fetch_log_once():
    con = connect_memory()
    _seed_gk(con, 100, "가")
    con.execute("INSERT INTO fc_fetch_log (kind, spid) VALUES ('fc-info-info', 100)")
    client = _mock_cards({"가": [{"id": 100, "salary": 5, "positions": []}]})

    r = sync_player_info(
        con, client=client, retry_missing=True, sleep=lambda _x: None, log=lambda _m: None,
    )

    assert client._calls["n"] == 1 and r["new"] == 1


def test_sync_season_img_remembers_a_valid_card_without_season_image():
    con = connect_memory()
    _seed_gk(con, 861048940, '가')
    client = _mock_cards({'가': [{'id': 861048940}]})
    result = sync_season_img(con, client=client, sleep=lambda _: None, log=lambda _: None)
    assert result['need'] == 1 and client._calls['n'] == 1
    sync_season_img(con, client=client, sleep=lambda _: None, log=lambda _: None)
    assert client._calls['n'] == 1
