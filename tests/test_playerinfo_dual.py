"""fc-info 검색 응답 검증과 CLI 연결."""

import re

import httpx
import pytest

from gksave import playerinfo
from gksave.db import connect_memory


def card(spid):
    return {"id": spid, "name": "테스트 선수", "grade": 1, "salary": 20,
            "positions": [{"positionId": 4, "ovr": 100}], "height": 190,
            "weight": 80, "bodyType": "보통", "classImg": "https://ssl.nexon.com/season.png"}


def seed(con, count=2):
    spids = [300000010 + i for i in range(count)]
    for spid in spids:
        con.execute("INSERT INTO gk_match (match_id,gk_sp_id) VALUES (?,?)", [f"synthetic-{spid}", spid])
        con.execute("INSERT INTO meta_spid (sp_id,name) VALUES (?,?)", [spid, "테스트 선수"])
    return spids


def test_fcinfo_pagination_reuses_same_name_catalog_and_rejects_wrong_grade():
    calls, waits = [], []

    def handler(request):
        import json
        body = json.loads(request.content)
        calls.append(body)
        if "cursor" not in body:
            return httpx.Response(200, json={"items": [{**card(300000010), "grade": 8}], "hasNext": True, "nextCursor": "next"})
        return httpx.Response(200, json={"items": [card(300000011)], "hasNext": False})

    with httpx.Client(base_url=playerinfo.FCINFO_BASE, transport=httpx.MockTransport(handler)) as client:
        result = playerinfo._fcinfo_cards(client, "테스트 선수", delay=3, sleep=waits.append, log=lambda _: None)
    assert list(result) == [300000011]
    assert calls == [{"names": ["테스트 선수"]}, {"names": ["테스트 선수"], "cursor": "next"}]
    assert waits == [3.0, 3.0]


def test_same_player_search_is_not_repeated_for_other_seasons():
    con = connect_memory()
    spids = seed(con, 6)
    calls = []

    def fc_handler(request):
        calls.append(request)
        return httpx.Response(200, json={"items": [card(s) for s in spids], "hasNext": False})

    with httpx.Client(base_url=playerinfo.FCINFO_BASE, transport=httpx.MockTransport(fc_handler)) as fc:
        assert playerinfo.sync_player_info(con, client=fc, sleep=lambda _: None, log=lambda _: None)["new"] == 6
    assert len(calls) == 1
    con.close()


@pytest.mark.parametrize("data", [{}, {"items": "broken"}, {"items": [], "hasNext": True, "nextCursor": ""}])
def test_invalid_fcinfo_response_is_not_cached(data):
    with httpx.Client(base_url=playerinfo.FCINFO_BASE, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data))) as client:
        with pytest.raises(ValueError):
            playerinfo._fcinfo_cards(client, "테스트 선수", delay=3, sleep=lambda _: None, log=lambda _: None)


def test_terminal_metadata_log_has_current_local_time(capsys):
    playerinfo._log("player_info: 조회 20/100 · 신규 20")
    assert re.fullmatch(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] player_info: 조회 20/100 · 신규 20\n", capsys.readouterr().out)


def test_cli_playerinfo_shares_fcinfo_client_with_season_collection(monkeypatch):
    from gksave import cli
    from types import SimpleNamespace

    con = connect_memory()
    seen = []
    fc = httpx.Client(base_url=playerinfo.FCINFO_BASE)
    monkeypatch.setattr(cli, "connect", lambda _: con)
    monkeypatch.setattr(playerinfo, "fcinfo_client", lambda: fc)
    monkeypatch.setattr(playerinfo, "sync_player_info", lambda _con, **kwargs: seen.append(kwargs))
    monkeypatch.setattr(playerinfo, "sync_season_img", lambda _con, **kwargs: seen.append(kwargs))
    cli._cmd_playerinfo(SimpleNamespace(delay=3, retry_missing=False, defenders=True))
    assert seen[0]["client"] is fc and seen[0]["batch_delay"] == 3
    assert seen[0]["include_defenders"] is True and fc.is_closed
    assert seen[1]["client"] is fc and seen[1]["page_delay"] == 3
