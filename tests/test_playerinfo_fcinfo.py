"""playerinfo는 데이터센터에 접근하지 않고 fc-info만 사용한다."""

import json

import httpx
import pytest

from gksave import playerinfo
from gksave.cli import build_parser
from gksave.db import connect_memory


def test_default_info_and_season_collection_use_fcinfo_only(monkeypatch):
    calls, waits = [], []

    def handler(request):
        assert request.url.host == 'fc-info.com'
        assert request.url.path == '/local-api/v2/players/search'
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'items': [
            {'id': spid, 'name': '테스트 선수', 'grade': 1, 'salary': 20,
             'positions': [{'ovr': 100}], 'classImg': 'https://ssl.nexon.com/season.png'}
            for spid in (300000010, 300000020)
        ], 'hasNext': False})

    def unexpected():
        raise AssertionError('playerinfo에서 데이터센터 클라이언트를 열면 안 됨')

    monkeypatch.setattr(playerinfo, '_new_client', unexpected)
    monkeypatch.setattr(playerinfo, 'fcinfo_client', lambda: httpx.Client(
        base_url=playerinfo.FCINFO_BASE, transport=httpx.MockTransport(handler)))
    with connect_memory() as con:
        for spid in (300000010, 300000020):
            con.execute('INSERT INTO gk_match (match_id,gk_sp_id) VALUES (?,?)', [str(spid), spid])
            con.execute('INSERT INTO meta_spid (sp_id,name) VALUES (?,?)', [spid, '테스트 선수'])
        # 과거 데이터센터 누락 기록이 fc-info 조회를 막으면 안 된다.
        con.execute("INSERT INTO fc_fetch_log (kind,spid) VALUES ('nexon-info',300000010)")
        result = playerinfo.sync_player_info(con, sleep=waits.append, log=lambda _: None)
        assert result['new'] == 2
        assert con.execute('SELECT salary,ovr FROM player_info ORDER BY spid').fetchall() == [(20, 100)] * 2
        assert playerinfo.sync_season_img(con, sleep=waits.append, log=lambda _: None)['new'] == 0
        # 이미 캐시된 선수라도 빠진 시즌 이미지는 fc-info에서 보충한다.
        con.execute('DELETE FROM season_img')
        assert playerinfo.sync_season_img(con, sleep=waits.append, log=lambda _: None)['new'] == 1
    assert calls == [{'names': ['테스트 선수']}] * 2
    assert waits == [3.0, 3.0]


def test_playerinfo_cli_defaults_to_three_seconds():
    assert build_parser().parse_args(['playerinfo']).delay == 3.0


def test_missing_name_log_explains_search_was_skipped_without_http_request():
    messages = []

    def unexpected(request):
        raise AssertionError("이름이 없으면 fc-info 검색을 호출하면 안 됨")

    with connect_memory() as con, httpx.Client(
        base_url=playerinfo.FCINFO_BASE, transport=httpx.MockTransport(unexpected),
    ) as client:
        con.execute("INSERT INTO gk_match (match_id,gk_sp_id) VALUES ('synthetic-missing-name',300000010)")
        result = playerinfo.sync_player_info(con, client=client, log=messages.append)
    assert result['new'] == 0
    assert any('이름 매핑 없음' in message and '검색 건너뜀' in message for message in messages)
    assert not any('gksave meta 실행 후' in message for message in messages)


def test_missing_card_never_falls_back_and_retry_missing_ignores_its_cache():
    calls = []

    def handler(request):
        assert request.url.host == 'fc-info.com'
        calls.append(request)
        return httpx.Response(200, json={'items': [
            {'id': 300000999, 'name': '다른 카드', 'grade': 1, 'salary': 20,
             'positions': [{'ovr': 100}]}
        ], 'hasNext': False})

    with connect_memory() as con, httpx.Client(
        base_url=playerinfo.FCINFO_BASE, transport=httpx.MockTransport(handler),
    ) as client:
        con.execute("INSERT INTO gk_match (match_id,gk_sp_id) VALUES ('synthetic',300000010)")
        con.execute("INSERT INTO meta_spid (sp_id,name) VALUES (300000010,'테스트 선수')")
        for retry in (False, False, True):
            assert playerinfo.sync_player_info(
                con, client=client, retry_missing=retry, sleep=lambda _: None, log=lambda _: None,
            )['new'] == 0
        assert len(calls) == 2
        assert con.execute('SELECT count(*) FROM player_info').fetchone()[0] == 0


def test_card_without_name_is_not_requested_or_negative_cached():
    with connect_memory() as con, httpx.Client(
        base_url=playerinfo.FCINFO_BASE,
        transport=httpx.MockTransport(lambda _: pytest.fail('이름 없는 카드 요청')),
    ) as client:
        con.execute("INSERT INTO gk_match (match_id,gk_sp_id) VALUES ('synthetic',300000010)")
        assert playerinfo.sync_player_info(
            con, client=client, sleep=lambda _: None, log=lambda _: None,
        )['new'] == 0
        assert con.execute('SELECT count(*) FROM fc_fetch_log').fetchone()[0] == 0


@pytest.mark.parametrize('status', [403, 429])
def test_cli_reports_fcinfo_http_error_without_traceback(monkeypatch, status):
    from gksave import cli

    request = httpx.Request('POST', playerinfo.FCINFO_BASE + '/local-api/v2/players/search')
    response = httpx.Response(status, request=request)

    def fail(_args):
        response.raise_for_status()

    monkeypatch.setattr(cli, '_cmd_playerinfo', fail)
    with pytest.raises(SystemExit, match='fc-info') as result:
        cli.main(['playerinfo'])
    assert f'HTTP {status}' in str(result.value)
