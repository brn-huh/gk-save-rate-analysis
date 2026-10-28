"""공식 데이터센터의 실제 마크업 형태와 요청·캐시 동작. 네트워크는 모킹한다."""

import json

import httpx
import pytest

from gksave import playerinfo
from gksave.db import connect_memory


def ability_html(spid=300000010):
    return f'''<div class="content data_detail"><div class="ovr value">109</div>
<div class="pay_side">25</div><div class="name">테스트 선수</div>
<div class="season"><img src="https://ssl.nexon.com/season/TEST.png" alt=""></div>
<span class="etc height">190cm</span><span class="etc weight">80kg</span>
<span class="etc physical">보통(고유)</span>
<div class="etc nation"><span class="img"><img src="https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/countries/smallflags/21.png"></span><span class="txt">독일</span></div>
<div class="skill_wrap"><img src="https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/traits/trait_icon_15.png" alt="긴 패스 선호"></div></div>
<script>var playerData = {{spid: {spid}, strong: 1}};</script>'''


def fcinfo_data(spid=300000010):
    return {'items': [{'id': spid, 'name': str(spid), 'grade': 1, 'salary': 25,
                      'positions': [{'ovr': 109}], 'height': 190, 'bodyType': '보통(고유)'}],
            'hasNext': False}


def info_names(con):
    con.execute("INSERT INTO meta_spid (sp_id,name) SELECT DISTINCT gk_sp_id,CAST(gk_sp_id AS VARCHAR) FROM gk_match ON CONFLICT DO NOTHING")


def test_default_client_uses_only_official_datacenter():
    with playerinfo._new_client() as client:
        assert str(client.base_url).rstrip('/') == 'https://fconline.nexon.com'


@pytest.mark.parametrize('delay,expected',[(0,1.0),(1,1.0),(2,2.0),(3,3.0),(5,5.0)])
def test_request_uses_configured_delay_without_forcing_five_seconds(delay,expected):
    waits=[]
    with httpx.Client(base_url=playerinfo.DATACENTER_BASE,transport=httpx.MockTransport(lambda _:httpx.Response(200,text='ok'))) as client:
        assert playerinfo._request(client,'GET','/datacenter',delay=delay,sleep=waits.append,log=lambda _:None).text=='ok'
    assert waits==[expected]


def test_collection_command_delays():
    from gksave.cli import build_parser
    for command, delay in [('playerinfo',3.0),('playerdetail',2.0),('resyncbio',2.0)]:
        assert build_parser().parse_args([command]).delay==delay
    assert not build_parser().parse_args(['playerdetail']).defenders
    assert build_parser().parse_args(['playerdetail','--defenders']).defenders


def test_fcinfo_collection_searches_each_name_and_waits_between_requests():
    con = connect_memory()
    for i in (10, 20):
        con.execute("INSERT INTO gk_match (match_id,gk_sp_id) VALUES (?,?)", [f'synthetic-{i}', 300000000+i])
    calls, delays = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=fcinfo_data(int(json.loads(request.content)["names"][0])))

    info_names(con)
    with httpx.Client(base_url=playerinfo.FCINFO_BASE, transport=httpx.MockTransport(handler)) as client:
        result = playerinfo.sync_player_info(con, client=client, batch_delay=0, sleep=delays.append, log=lambda _: None)
        assert result['new'] == 2
        assert [r.url.path for r in calls] == ['/local-api/v2/players/search']*2
        assert all(r.method == 'POST' and r.url.host == 'fc-info.com' for r in calls)
        assert delays == [1.0, 1.0]
        assert con.execute('SELECT salary,ovr,height,body_type FROM player_info ORDER BY spid').fetchall() == [(25,109,190,'보통(고유)')]*2
        assert playerinfo.sync_player_info(con, client=client, sleep=delays.append, log=lambda _: None)['new'] == 0
        assert len(calls) == 2
    con.close()


def test_official_response_must_match_requested_card():
    assert playerinfo.parse_player(ability_html(), 300000020) is None
    assert playerinfo.parse_player('<html>일시 점검</html>', 300000010) is None
    assert playerinfo.parse_player(ability_html().replace('strong: 1','strong: 8'),300000010) is None


def test_fcinfo_429_waits_and_retries_without_marking_a_failed_fetch():
    con=connect_memory()
    con.execute("INSERT INTO gk_match (match_id,gk_sp_id) VALUES ('synthetic',300000010)")
    calls, delays, logs = [], [], []
    def handler(request):
        calls.append(request)
        if len(calls)==1:
            return httpx.Response(429,headers={'Retry-After':'90'})
        return httpx.Response(200,json=fcinfo_data())
    info_names(con)
    with httpx.Client(base_url=playerinfo.FCINFO_BASE, transport=httpx.MockTransport(handler)) as client:
        assert playerinfo.sync_player_info(con,client=client,sleep=delays.append,log=logs.append)['new']==1
    assert delays == [3.0,90.0,3.0]
    assert any('90' in line and '제한' in line for line in logs)
    assert con.execute("SELECT kind FROM fc_fetch_log").fetchall()==[('fc-info-info',)]
    con.close()


def test_legacy_negative_cache_does_not_skip_fcinfo_lookup():
    con=connect_memory()
    con.execute("INSERT INTO gk_match (match_id,gk_sp_id) VALUES ('synthetic',300000010)")
    con.execute("INSERT INTO fc_fetch_log (kind,spid) VALUES ('info',300000010)")
    info_names(con)
    with httpx.Client(base_url=playerinfo.FCINFO_BASE,transport=httpx.MockTransport(lambda _:httpx.Response(200,json=fcinfo_data()))) as client:
        assert playerinfo.sync_player_info(con,client=client,sleep=lambda _:None,log=lambda _:None)['new']==1
    con.close()


def test_persistent_request_limit_stops_before_next_card_and_keeps_fetch_pending():
    con=connect_memory()
    for spid in (300000010,300000020):
        con.execute('INSERT INTO gk_match (match_id,gk_sp_id) VALUES (?,?)',[f'synthetic-{spid}',spid])
    calls,delays=[],[]
    def handler(request):
        calls.append(request)
        return httpx.Response(429)
    info_names(con)
    with httpx.Client(base_url=playerinfo.FCINFO_BASE,transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            playerinfo.sync_player_info(con,client=client,sleep=delays.append,log=lambda _:None)
    assert len(calls)==4 and all(json.loads(r.content)['names'] == ['300000010'] for r in calls)
    assert delays==[3.0,60.0,3.0,120.0,3.0,240.0,3.0]
    assert con.execute('SELECT count(*) FROM fc_fetch_log').fetchone()[0]==0
    assert con.execute('SELECT count(*) FROM player_info').fetchone()[0]==0
    con.close()


@pytest.mark.parametrize('error_type',[httpx.ReadError,httpx.ReadTimeout,httpx.ConnectError])
def test_transient_transport_error_retries_same_card_and_saves_it_once(error_type):
    con=connect_memory()
    con.execute("INSERT INTO gk_match (match_id,gk_sp_id) VALUES ('synthetic',300000010)")
    calls,waits,logs=[],[],[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:
            raise error_type('synthetic connection interruption',request=request)
        return httpx.Response(200,json=fcinfo_data())
    info_names(con)
    with httpx.Client(base_url=playerinfo.FCINFO_BASE,transport=httpx.MockTransport(handler)) as client:
        result=playerinfo.sync_player_info(con,client=client,sleep=waits.append,log=logs.append)
    assert result['new']==1 and len(calls)==2
    assert calls[0].content==calls[1].content
    assert waits==[3.0,5.0,3.0]
    assert any(error_type.__name__ in line and '재시도' in line for line in logs)
    assert con.execute('SELECT count(*) FROM player_info').fetchone()[0]==1
    assert con.execute('SELECT kind FROM fc_fetch_log').fetchall()==[('fc-info-info',)]
    con.close()


def test_persistent_read_error_keeps_previous_cards_and_failed_card_can_resume():
    con=connect_memory()
    for spid in (300000010,300000020):
        con.execute('INSERT INTO gk_match (match_id,gk_sp_id) VALUES (?,?)',[f'synthetic-{spid}',spid])
    calls,waits=[],[]
    def handler(request):
        calls.append(request)
        if json.loads(request.content)['names'] == ['300000010']:
            return httpx.Response(200,json=fcinfo_data(300000010))
        raise httpx.ReadError('synthetic persistent read failure',request=request)
    info_names(con)
    with httpx.Client(base_url=playerinfo.FCINFO_BASE,transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadError):
            playerinfo.sync_player_info(con,client=client,sleep=waits.append,log=lambda _:None)
    assert len(calls)==5
    assert waits==[3.0,3.0,5.0,3.0,10.0,3.0,20.0,3.0]
    assert con.execute('SELECT spid FROM player_info').fetchall()==[(300000010,)]
    assert con.execute('SELECT spid FROM fc_fetch_log').fetchall()==[(300000010,)]
    with httpx.Client(base_url=playerinfo.FCINFO_BASE,transport=httpx.MockTransport(lambda request:httpx.Response(200,json=fcinfo_data(300000020)))) as client:
        result=playerinfo.sync_player_info(con,client=client,sleep=lambda _:None,log=lambda _:None)
    assert result['already']==1 and result['new']==1
    assert con.execute('SELECT count(*) FROM player_info').fetchone()[0]==2
    con.close()


def test_invalid_response_does_not_record_success_or_replace_existing_cache():
    con=connect_memory()
    for spid in (300000010,300000020):
        con.execute('INSERT INTO gk_match (match_id,gk_sp_id) VALUES (?,?)',[f'synthetic-{spid}',spid])
    con.execute("INSERT INTO player_info (spid,name,salary) VALUES (300000010,'기존 선수',20)")
    info_names(con)
    with httpx.Client(base_url=playerinfo.FCINFO_BASE,transport=httpx.MockTransport(lambda _:httpx.Response(200,text='일시 점검'))) as client:
        with pytest.raises(ValueError):
            playerinfo.sync_player_info(con,client=client,sleep=lambda _:None,log=lambda _:None)
    assert con.execute('SELECT spid,salary FROM player_info').fetchall()==[(300000010,20)]
    assert con.execute('SELECT count(*) FROM fc_fetch_log').fetchone()[0]==0
    con.close()


def test_resync_bio_failure_preserves_old_bio_and_clubs():
    con=connect_memory()
    con.execute("INSERT INTO gk_match (match_id,gk_sp_id) VALUES ('synthetic',300000010)")
    con.execute("INSERT INTO player_bio (pid,nation_name) VALUES (10,'기존 국적')")
    con.execute("INSERT INTO player_club VALUES (10,0,'기존 클럽')")
    with httpx.Client(base_url=playerinfo.DATACENTER_BASE,transport=httpx.MockTransport(lambda _:httpx.Response(500))) as client:
        with pytest.raises(ValueError,match='기존 캐시'):
            playerinfo.resync_bio(con,client=client,sleep=lambda _:None,log=lambda _:None)
    assert con.execute('SELECT nation_name FROM player_bio').fetchall()==[('기존 국적',)]
    assert con.execute('SELECT club_name FROM player_club').fetchall()==[('기존 클럽',)]
    con.close()


def test_official_retry_after_date_is_respected(monkeypatch):
    from datetime import datetime,timezone
    class FixedDateTime:
        @staticmethod
        def now(_zone):
            return datetime(2026,10,14,tzinfo=timezone.utc)
    monkeypatch.setattr(playerinfo,'datetime',FixedDateTime)
    calls,delays=[],[]
    def handler(request):
        calls.append(request)
        return httpx.Response(429,headers={'Retry-After':'Wed, 14 Oct 2026 00:03:00 GMT'}) if len(calls)==1 else httpx.Response(200,text='ok')
    with httpx.Client(base_url=playerinfo.DATACENTER_BASE,transport=httpx.MockTransport(handler)) as client:
        assert playerinfo._request(client,'GET','/datacenter',delay=5,sleep=delays.append,log=lambda _:None).text=='ok'
    assert delays==[5.0,180.0,5.0]


def test_cli_reports_rate_limit_without_traceback(monkeypatch,capsys):
    from gksave import cli
    request=httpx.Request('POST',playerinfo.DATACENTER_BASE+'/datacenter/PlayerAbility')
    response=httpx.Response(429,request=request)
    def fail(_args):
        raise httpx.HTTPStatusError('Too Many Requests',request=request,response=response)
    monkeypatch.setattr(cli,'_cmd_playerinfo',fail)
    with pytest.raises(SystemExit,match='요청 제한이 지속'):
        cli.main(['playerinfo','--defenders'])
    assert 'Traceback' not in capsys.readouterr().err


def test_cli_read_error_shows_cause_and_marks_http_status_unknown(monkeypatch):
    from gksave import cli
    request=httpx.Request('POST',playerinfo.DATACENTER_BASE+'/datacenter/PlayerAbility')
    def fail(_args):
        try:
            raise ConnectionResetError(54,'Connection reset by peer')
        except ConnectionResetError as cause:
            raise httpx.ReadError('',request=request) from cause
    monkeypatch.setattr(cli,'_cmd_playerinfo',fail)
    with pytest.raises(SystemExit) as result:
        cli.main(['playerinfo'])
    text=str(result.value)
    assert 'ConnectionResetError' in text and 'Connection reset by peer' in text
    assert 'POST /datacenter/PlayerAbility' in text and 'HTTP 상태: 미확인' in text


def test_read_failure_after_headers_retains_real_http_status():
    requests=[]
    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b'<div>'
            raise httpx.ReadError('connection closed during response body',request=requests[-1])
    def handler(request):
        requests.append(request)
        return httpx.Response(200,stream=BrokenBody())
    with httpx.Client(base_url=playerinfo.DATACENTER_BASE,transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadError) as result:
            playerinfo._request(client,'POST','/datacenter/PlayerAbility',delay=2,sleep=lambda _:None,log=lambda _:None)
    assert result.value.request.extensions.get('datacenter_http_status')==200
    assert 'HTTP 200' in playerinfo.describe_error(result.value)
    assert 'connection closed during response body' in playerinfo.describe_error(result.value)


def test_429_is_identified_from_headers_without_reading_broken_error_body():
    calls=[]
    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError('429 응답 본문을 읽지 않아야 합니다')
            yield b''
    def handler(request):
        calls.append(request)
        return httpx.Response(429,stream=BrokenBody()) if len(calls)==1 else httpx.Response(200,text='ok')
    logs=[]
    with httpx.Client(base_url=playerinfo.DATACENTER_BASE,transport=httpx.MockTransport(handler)) as client:
        assert playerinfo._request(client,'POST','/datacenter/PlayerAbility',delay=2,sleep=lambda _:None,log=logs.append).text=='ok'
    assert len(calls)==2 and any('HTTP 429 Too Many Requests' in line for line in logs)


def test_error_details_hide_query_credentials_and_ouids():
    request=httpx.Request('GET',playerinfo.DATACENTER_BASE+'/DataCenter/PlayerInfo?token=synthetic-secret&ouid=synthetic-private-user')
    error=httpx.ReadError('failed for '+str(request.url)+'; api_key=synthetic-api-key',request=request)
    text=playerinfo.describe_error(error)
    assert 'GET /DataCenter/PlayerInfo' in text
    assert 'synthetic-secret' not in text and 'synthetic-private-user' not in text and 'synthetic-api-key' not in text


@pytest.mark.parametrize('status,reason',[(429,'Too Many Requests'),(503,'Service Unavailable')])
def test_http_error_details_show_received_status_and_reason(status,reason):
    request=httpx.Request('POST',playerinfo.DATACENTER_BASE+'/datacenter/PlayerAbility')
    response=httpx.Response(status,request=request)
    error=httpx.HTTPStatusError('request failed',request=request,response=response)
    text=playerinfo.describe_error(error)
    assert f'HTTP {status} {reason}' in text and 'POST /datacenter/PlayerAbility' in text


@pytest.mark.parametrize('status,html,reason',[
    (200,'<html><title>서비스 점검</title></html>','playerData.spid/strong'),
    (201,ability_html(300000020),'카드 ID 불일치: 요청 300000010, 응답 300000020'),
    (200,ability_html().replace('strong: 1','strong: 8'),'강화 단계 불일치: 요청 1강, 응답 8강'),
    (200,ability_html().replace('>25</div>','>없음</div>'),'급여(pay_side)'),
    (200,ability_html().replace('>109</div>','></div>'),'기본 OVR(ovr value)'),
    (200,ability_html().replace('>테스트 선수</div>','></div>'),'선수명(name)'),
    (204,'','playerData.spid/strong'),
])
def test_invalid_ability_response_reports_actual_http_status_and_validation_reason(status,html,reason):
    with httpx.Client(base_url=playerinfo.DATACENTER_BASE,transport=httpx.MockTransport(
            lambda _:httpx.Response(status,text=html,headers={'Content-Type':'text/html; charset=utf-8'}))) as client:
        with pytest.raises(playerinfo.ResponseValidationError) as result:
            playerinfo._ability(client,300000010,delay=2,sleep=lambda _:None,log=lambda _:None)
    text=playerinfo.describe_error(result.value)
    assert f'HTTP {status}' in text and 'POST /datacenter/PlayerAbility' in text
    assert 'Content-Type: text/html; charset=utf-8' in text
    assert f'응답 크기: {len(html.encode()):,} bytes' in text
    assert reason in text and '요청 카드 300000010' in text
    if '서비스 점검' in html:
        assert '응답 제목: 서비스 점검' in text


def test_validation_error_hides_response_body_headers_and_sensitive_title_values():
    html='''<title>점검 token=synthetic-title-token</title>
<script>const ouid="synthetic-private-user";</script><input value="synthetic-body-secret">'''
    with httpx.Client(base_url=playerinfo.DATACENTER_BASE,transport=httpx.MockTransport(
            lambda _:httpx.Response(200,text=html,headers={'Set-Cookie':'synthetic-cookie'}))) as client:
        with pytest.raises(playerinfo.ResponseValidationError) as result:
            playerinfo._ability(client,300000010,delay=2,sleep=lambda _:None,log=lambda _:None)
    text=playerinfo.describe_error(result.value)
    assert 'HTTP 200 OK' in text and '응답 제목: 점검' in text
    assert all(secret not in text for secret in ('synthetic-title-token','synthetic-private-user','synthetic-body-secret','synthetic-cookie'))


def test_missing_club_response_retains_http_context():
    def handler(request):
        return httpx.Response(200,text=ability_html() if request.method=='POST' else '<title>오류 페이지</title>')
    with httpx.Client(base_url=playerinfo.DATACENTER_BASE,transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(playerinfo.ResponseValidationError) as result:
            playerinfo._detail(client,300000010,delay=2,sleep=lambda _:None,log=lambda _:None)
    text=playerinfo.describe_error(result.value)
    assert 'HTTP 200 OK' in text and 'GET /DataCenter/PlayerInfo' in text
    assert 'data_detail_club' in text and '응답 제목: 오류 페이지' in text


def test_fcinfo_json_validation_error_also_retains_http_context():
    with httpx.Client(base_url=playerinfo.FCINFO_BASE,transport=httpx.MockTransport(
            lambda _:httpx.Response(200,text='<title>잠시 후 다시 시도</title>'))) as client:
        with pytest.raises(playerinfo.ResponseValidationError) as result:
            playerinfo._fcinfo_cards(client,'테스트 선수',delay=2,sleep=lambda _:None,log=lambda _:None)
    text=playerinfo.describe_error(result.value)
    assert 'HTTP 200 OK' in text and 'POST /local-api/v2/players/search' in text
    assert 'JSON이 아닙니다' in text and '잠시 후 다시 시도' in text
