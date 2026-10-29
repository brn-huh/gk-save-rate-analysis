"""인터랙티브 HTML 표출: 데이터 embed, 정적 라벨, 스크립트 주입 방지, 선수 이미지."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gksave import render
from gksave.config import SITE_URL

_NODE = shutil.which("node")
requires_node = pytest.mark.skipif(_NODE is None, reason="node 없음 — JS 동작 검증 생략")


def _eval_js(expr: str, src: str | None = None) -> str:
    """페이지에 실제로 실려 나가는 JS를 그대로 실행해 표현식 값을 얻는다."""
    js = f"{render.IMAGE_JS if src is None else src}\nprocess.stdout.write(String({expr}));"
    r = subprocess.run([_NODE, "-e", js], capture_output=True, text=True, check=True)
    return r.stdout

_PAYLOAD = {
    "generated_at": "2026-07-04T00:00:00Z",
    "gate_min_matches": 50,
    "warning": "raw 선방률 — 카드 추천 아님",
    "leaderboard": [
        {"rank": 1, "gk_sp_id": 101190053, "player_name": "야신", "grade": 10,
         "season_name": "ICON", "save_pct": 0.75, "saves": 3, "goals": 1, "matches": 60,
         "gsax": 12.3, "gsax_per_shot": 0.05,
         "zones": [{"zone": "초근거리(0-5m)", "shots": 10, "saves": 3, "save_pct": 0.3}],
         "types": [{"type": 2, "name": "감아차기", "shots": 20, "saves": 12, "save_pct": 0.6}]},
    ],
    "grade_effect": {"mean_save_pct_delta_per_grade": 0.02, "paired_users": 4, "pairs": 6},
    "same_player": [
        {"player_name": "노이어", "cards": [
            {"season_name": "CAP", "grade": 11, "save_pct": 0.53, "gsax_per_shot": 0.04, "matches": 80},
        ]},
    ],
}


def _embedded(html):
    m = re.search(r'id="gk-data"[^>]*>(.*?)</script>', html, re.S)
    assert m, "embed JSON 없음"
    return json.loads(m.group(1))


@requires_node
def test_defense_filters_exact_gk_and_grade_and_sorts_missing_last():
    rows = [
        {"gk_sp_id": 1, "grade": 10, "cb1_sp_id": 10, "cb1_grade": 8,
         "cb2_sp_id": 20, "cb2_grade": 8, "matches": 100, "save_pct": None, "goals_per_match": 0},
        {"gk_sp_id": 1, "grade": 10, "cb1_sp_id": 11, "cb1_grade": 8,
         "cb2_sp_id": 20, "cb2_grade": 8, "matches": 200, "save_pct": 0.7, "goals_per_match": 1},
        {"gk_sp_id": 1, "grade": 10, "cb1_sp_id": 12, "cb1_grade": 8,
         "cb2_sp_id": 20, "cb2_grade": 8, "matches": 80, "save_pct": 0.9, "goals_per_match": 2},
        {"gk_sp_id": 1, "grade": 11, "cb1_sp_id": 13, "cb1_grade": 8,
         "cb2_sp_id": 20, "cb2_grade": 8, "matches": 300, "save_pct": 1.0, "goals_per_match": 0},
    ]
    source = render.DEFENSE_JS + "\nconst rows=" + json.dumps(rows) + ";"
    assert _eval_js("filterDefenseRows(rows,'1_10',100,'save_pct').map(r=>r.cb1_sp_id).join(',')", source) == "11,10"
    assert _eval_js("filterDefenseRows(rows,'1_10',50,'goals_per_match').map(r=>r.cb1_sp_id).join(',')", source) == "10,11,12"
    assert _eval_js("rows[0].cb1_sp_id", source) == "10"


@requires_node
def test_defense_team_color_search_requires_shared_gk_and_defender_metadata():
    rows = [{"gk_sp_id": 1, "grade": 10, "cb1_sp_id": 10, "cb2_sp_id": second,
             "matches": 100, "save_pct": 0.7} for second in (20, 30, 40)]
    defenders = {
        "10": {"bio": {"nation_name": "프랑스", "clubs": ["테스트", "테스트 FC"]}, "season_name": "ICON"},
        "20": {"bio": {"nation_name": "프랑스", "clubs": ["테스트"]}, "season_name": "ICON"},
        "30": {"bio": {"nation_name": "독일", "clubs": ["테스트 FC"]}},
    }
    gk = {"bio": {"nation_name": "프랑스", "clubs": ["테스트", "테스트 FC"]}, "season_name": "ICON"}
    source = render.FILTER_JS + render.DEFENSE_JS + "\nconst rows=" + json.dumps(rows) + ";const defenders=" + json.dumps(defenders) + ";const gk=" + json.dumps(gk) + ";"
    for color, expected in [("프랑스", "20"), ("프랑", "20"), ("테스트", "20,30"), ("테스트 FC", "30"),
                            ("  iCoN  ", "20"), ("없는 팀", ""), ("", "20,30,40"), ("   ", "20,30,40")]:
        expr = f"filterDefenseRows(rows,'1_10',100,'save_pct',{json.dumps(color)},defenders,gk).map(r=>r.cb2_sp_id).join(',')"
        assert _eval_js(expr, source) == expected
    assert _eval_js("filterDefenseRows(rows,'1_10',100,'save_pct','프랑',defenders,{bio:{nation_name:'독일'}}).length", source) == "0"
    assert _eval_js("filterDefenseRows(rows,'1_10',100,'save_pct','테스트',defenders,{bio:{clubs:['테스트 United']}}).length", source) == "0"
    assert _eval_js("filterDefenseRows(rows,'1_10',100,'save_pct','ICON',defenders,{}).length", source) == "0"


@requires_node
def test_defense_top_ten_filters_before_limiting_and_resets_on_changes():
    html = render.build_html(_PAYLOAD)
    source = "function filterDefenseRows(" + html.split("function filterDefenseRows(", 1)[1].split("defenseRetry.onclick=loadDefense;", 1)[0]
    script = """
const fields={};
const document={getElementById:id=>fields[id]||(fields[id]={value:'',min:50,options:[],
  appendChild(option){this.options.push(option);}}),createElement:()=>({})};
const location={reload(){}};
const esc=String,pct=String,gps=String,ciText=()=>'',heroImg=()=>'',gradeBadge=()=>'',seasonIcon=()=>'';
const D={leaderboard:[{gk_sp_id:1,grade:10,bio:{nation_name:'프랑스'}}]};
""" + render.FILTER_JS + source + """
defenseData={min_users:5,defenders:{99:{bio:{nation_name:'프랑스'}}},combinations:[]};
for(let i=0;i<25;i++){
  defenseData.defenders[i+100]={bio:{nation_name:i<11?'독일':'프랑스'}};
  defenseData.combinations.push({gk_sp_id:1,grade:10,cb1_sp_id:i+100,cb2_sp_id:99,
    matches:100,users:5,save_pct:1-i/100,gsax_shots:100,shots:100,
    shots_per_match:1,goals_per_match:i/100});
}
defenseGk.value='1_10'; defenseGate.value=100; defenseSort.value='save_pct';
const states=[];
function record(){states.push({count:(fields.defenseResults.innerHTML.match(/<article/g)||[]).length,
  status:defenseStatus.textContent,more:!defenseMore.hidden,html:fields.defenseResults.innerHTML,
  selected:defenseGk.value,query:defenseTeamColor.value});}
renderDefense(); record();
defenseMore.onclick(); record();
defenseTeamColor.value='프랑'; defenseTeamColor.oninput(); record();
defenseMore.onclick(); record();
defenseSort.value='goals_per_match'; defenseSort.onchange(); record();
defenseGate.value=101; defenseGate.onchange(); record();
defenseGate.value=100; defenseGate.onchange();
defenseTeamColor.value='없는 팀'; defenseTeamColor.oninput(); record();
defenseTeamColor.value=''; defenseTeamColor.oninput(); record();
defenseGk.value=''; defenseGk.onchange();
process.stdout.write(JSON.stringify({states,colorDisabled:defenseTeamColor.disabled,color:defenseTeamColor.value}));
"""
    result = subprocess.run([_NODE, "-e", script], capture_output=True, text=True, check=True)
    result = json.loads(result.stdout)
    states = result["states"]
    assert [s["count"] for s in states] == [10, 20, 10, 14, 10, 0, 0, 10]
    assert [s["more"] for s in states] == [True, True, True, False, True, False, False, True]
    assert "25개 중 상위 10개" in states[0]["status"]
    assert "프랑 · 듀오 14개 중 상위 10개" in states[2]["status"]
    assert "선수 111" in states[2]["html"] and "선수 100" not in states[2]["html"]
    assert '공통 팀컬러:</strong> 프랑스' in states[2]["html"]
    assert "팀컬러나 골키퍼" in states[5]["html"]
    assert states[6]["query"] == "없는 팀"
    assert "전체 팀컬러" in states[7]["status"]
    assert all(s["selected"] == "1_10" for s in states)
    assert result["colorDisabled"] and result["color"] == ""


def test_defense_has_team_color_search_field():
    html = render.build_html(_PAYLOAD)
    assert 'id="defenseTeamColor" type="search"' in html
    assert 'for="defenseTeamColor">3. 공통 팀컬러 검색' in html


@requires_node
def test_defense_team_color_info_prioritizes_club_and_nation_shared_by_all_three():
    html = render.build_html(_PAYLOAD)
    helper = 'function defenseTeamColorInfo(' + html.split('function defenseTeamColorInfo(', 1)[1].split('async function loadDefense', 1)[0]
    source = "const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');\n" + render.DEFENSE_JS + helper
    gk = {"bio": {"nation_name": "프랑스", "clubs": ["레알 마드리드", "파리 생제르맹"]}, "season_name": "ICON"}
    first = {"bio": {"nation_name": "프랑스", "clubs": ["레알 마드리드", "레알 마드리드", "유벤투스"]}, "season_name": "ICON"}
    second = {"bio": {"nation_name": "프랑스", "clubs": ["레알 마드리드", "첼시"]}, "season_name": "ICON"}
    shared = _eval_js(f"defenseTeamColorInfo({json.dumps(gk)},{json.dumps(first)},{json.dumps(second)})", source)
    assert '공통 팀컬러:</strong> 레알 마드리드 · 프랑스 · ICON</p>' in shared
    assert shared.count('레알 마드리드') == 1
    assert '유벤투스' not in shared and '첼시' not in shared and '미확인' not in shared
    unrelated = {"bio": {"nation_name": "독일", "clubs": ["테스트 클럽"]}, "season_name": "TEST"}
    assert '공통 팀컬러:</strong> 없음' in _eval_js(f"defenseTeamColorInfo({json.dumps(unrelated)},{json.dumps(first)},{json.dumps(second)})", source)
    missing = _eval_js("defenseTeamColorInfo({season_name:'ICON'},{season_name:'ICON'},{season_name:'ICON'})", source)
    assert '공통 팀컬러:</strong> ICON ' in missing and '국가·클럽 정보 미확인' in missing
    assert '확인 가능한 정보 없음' in _eval_js("defenseTeamColorInfo()", source)
    malicious = {"bio": {"nation_name": "<script>", "clubs": ["<img src=x>"]}, "season_name": "<svg>"}
    escaped = _eval_js(f"defenseTeamColorInfo({json.dumps(malicious)},{json.dumps(malicious)},{json.dumps(malicious)})", source)
    assert '<script>' not in escaped and '<img' not in escaped and '<svg>' not in escaped
    assert '&lt;script&gt;' in escaped and '&lt;img src=x&gt;' in escaped and '&lt;svg&gt;' in escaped


@requires_node
@pytest.mark.parametrize("version", ["2026-07-04T00:00:00Z", "new-version", None])
def test_defense_version_mismatch_blocks_results_and_offers_reload(version):
    html = render.build_html(_PAYLOAD)
    source = html.split("function filterDefenseRows(", 1)[1].split("defenseRetry.onclick=loadDefense;", 1)[0]
    source = "function filterDefenseRows(" + source + "defenseRetry.onclick=loadDefense;"
    data = {"generated_at": version, "gate_min_matches": 50, "min_users": 5,
            "defenders": {}, "combinations": [{"gk_sp_id": 101190053, "grade": 10}]}
    script = """
const fields={};
const document={getElementById:id=>fields[id]||(fields[id]={value:'',options:[],hidden:true,
  appendChild(option){this.options.push(option);}}),createElement:()=>({})};
const location={reload:()=>{fields.reloaded=true;}};
const esc=String,pct=String,gps=String,ciText=()=>'';
""" + "\nconst D=" + json.dumps(_PAYLOAD) + ";\nconst incoming=" + json.dumps(data) + ";\n"
    script += "const fetch=async()=>({ok:true,json:async()=>incoming});\n" + render.FILTER_JS + source
    script += """
loadDefense().then(()=>{
  const blocked=defenseData===null;
  if(blocked) fields.defenseReload.onclick();
  process.stdout.write(JSON.stringify({blocked,reloadVisible:!fields.defenseReload.hidden,
    reloaded:!!fields.reloaded,status:fields.defenseStatus.textContent,
    selected:fields.defenseGk.value,firstOption:fields.defenseGk.options[0]?.value,
    options:fields.defenseGk.options.length,gateDisabled:fields.defenseGate.disabled}));
});
"""
    result = subprocess.run([_NODE, "-e", script], capture_output=True, text=True, check=True)
    state = json.loads(result.stdout)
    assert state["blocked"] == (version != _PAYLOAD["generated_at"])
    assert state["reloadVisible"] == state["blocked"]
    assert state["reloaded"] == state["blocked"]
    if state["blocked"]:
        assert "새로고침" in state["status"]
    else:
        assert state["options"] == 2 and state["firstOption"] == ""
        assert state["selected"] == "" and state["gateDisabled"]
        assert "선택" in state["status"]


@requires_node
def test_defense_player_uses_card_images_and_escapes_metadata_with_missing_info():
    html = render.build_html(_PAYLOAD)
    image_helpers = 'const thumbImg=' + html.split('const thumbImg=', 1)[1].split('const dr=', 1)[0]
    player_helper = 'function defensePlayer(' + html.split('function defensePlayer(', 1)[1].split('async function loadDefense', 1)[0]
    source = "const esc=s=>String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');\n"
    source += "const escAttr=s=>esc(s).replace(/\"/g,'&quot;');\n" + render.IMAGE_JS + image_helpers + player_helper
    card = {"player_name": '<img src=x onerror="alert(1)">', "season_name": 'TEST',
            "season_img": 'https://example.test/season.png',
            "info": {"salary": 25, "ovr": 110, "height": 190, "body_type": '<script>'}}
    output = _eval_js('defensePlayer(' + json.dumps(card) + ',300000010,8,"센터백")', source)
    assert '/playersAction/p300000010.png' in output
    assert '/players/p10.png' in output and 'onerror="imgFallback(this)"' in output
    assert 'https://example.test/season.png' in output
    assert '급여 25' in output and '기본 OVR 110' in output and '190cm' in output
    assert '<img src=x' not in output and '<script>' not in output
    assert '&lt;img' in output and '&lt;script&gt;' in output
    brief, detail = output.split('<p class="defense-player-info defense-player-info-extra">', 1)
    assert '급여 25' in brief and '190cm' in brief
    assert '기본 OVR 110' not in brief and '기본 OVR 110' in detail
    assert '<details' not in output and '<summary' not in output
    missing = _eval_js('defensePlayer({},300000020,8,"센터백")', source)
    assert '선수 300000020' in missing and 'undefined' not in missing
    assert 'defense-player-info' not in missing


def test_html_structure_and_data():
    html = render.build_html(_PAYLOAD)
    assert html.startswith("<!doctype html>")
    assert "카드 추천 아님" in html          # 경고(데이터)
    assert "야신" in html and "노이어" in html
    assert "GSAx" in html                    # 정적 라벨/정렬버튼
    assert "N/A" in html                     # JS null 처리 리터럴
    # embed 된 데이터가 파싱되고 존/타입까지 실려 있어야
    data = _embedded(html)
    c = data["leaderboard"][0]
    assert c["player_name"] == "야신"
    assert c["zones"][0]["zone"].startswith("초근")
    assert c["types"][0]["name"] == "감아차기"
    assert data["same_player"][0]["cards"][0]["gsax_per_shot"] == 0.04


def test_script_injection_neutralized():
    payload = dict(_PAYLOAD)
    payload["leaderboard"] = [
        {"rank": 1, "gk_sp_id": 1, "player_name": "</script><b>evil", "grade": 8,
         "season_name": "", "save_pct": 0.5, "saves": 1, "goals": 1, "matches": 60,
         "gsax_per_shot": 0.0, "zones": [], "types": []},
    ]
    payload["same_player"] = []
    html = render.build_html(payload)
    assert "</script><b>evil" not in html     # '<' → < 로 무력화
    assert "evil" in html                      # 데이터 자체는 보존


def test_empty_leaderboard_ok():
    payload = dict(_PAYLOAD)
    payload["leaderboard"] = []
    payload["same_player"] = []
    html = render.build_html(payload)
    assert _embedded(html)["leaderboard"] == []
    assert html.startswith("<!doctype html>")


def test_initial_table_matches_default_gate_and_save_pct_order():
    card = _PAYLOAD["leaderboard"][0]
    payload = {**_PAYLOAD, "leaderboard": [
        {**card, "player_name": "미달", "matches": 499, "save_pct": 1},
        {**card, "player_name": "편중", "matches": 500, "save_pct": 1,
         "rank_eligible": False, "max_user_share": 0.35},
        {**card, "player_name": "결측", "matches": 500, "save_pct": None},
        {**card, "player_name": "하위", "matches": 500, "save_pct": 0.5, "gsax_per_shot": -0.01},
        {**card, "player_name": "공동1", "matches": 500, "save_pct": 0.75},
        {**card, "player_name": "공동2", "matches": 500, "save_pct": 0.75},
    ]}
    html = render.build_html(payload)
    table = re.search(r"<tbody>(.*?)</tbody>", html, re.S)[1]
    assert "미달" not in table
    assert "편중" not in table
    assert table.index("공동1") < table.index("공동2") < table.index("하위") < table.index("결측")
    assert "+5.0" in table and "-1.0" in table and "75.0%" in table
    assert table.count("<tr>") == 4
    assert table.count("<td ") == 4 * 9
    assert _embedded(html)["leaderboard"] == payload["leaderboard"]


def test_initial_table_caps_rows_and_escapes_external_text():
    name = '<img src=x onerror="alert(1)">__DATA__ & __INITIAL_ROWS__'
    card = {**_PAYLOAD["leaderboard"][0], "matches": 500, "player_name": name}
    payload = {**_PAYLOAD, "leaderboard": [card] * 101,
               "date_range": {"min": "2026-08-12", "max": "2026-10-10"},
               "total_collected_matches": 1234}
    html = render.build_html(payload)
    table = re.search(r"<tbody>(.*?)</tbody>", html, re.S)[1]
    assert table.count("<tr>") == 100
    assert '<img src=x' not in table
    assert '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;__DATA__ &amp; __INITIAL_ROWS__' in table
    assert '<p class="meta" id="meta">기간 2026-08-12 ~ 2026-10-10 · 총 경기 1,234건</p>' in html
    assert _embedded(html)["leaderboard"][0]["player_name"] == name


@requires_node
def test_render_excludes_concentrated_cards_from_shared_ranking_pool():
    html = render.build_html(_PAYLOAD)
    start = html.split("function render(){", 1)[1].split("renderChampions(pool);", 1)[0]
    source = render.FILTER_JS + """
const document={querySelector:()=>({}),getElementById:()=>({})};
const q='',minGate=500,gradeFilter='',natClubQ='',salMin=null,salMax=null;
const D={leaderboard:[
 {player_name:'편중',matches:1000,rank_eligible:false},
 {player_name:'통과',matches:500,rank_eligible:true},
 {player_name:'미달',matches:499,rank_eligible:true}
]};
function poolNames(){
""" + start + "return pool.map(c=>c.player_name).join(',');}"
    assert _eval_js("poolNames()", source) == "통과"
    assert "35% 이상이면 순위에서 제외" in html


# ── 선수 이미지 ────────────────────────────────────────────────────────────
# pid = spid 뒤 6자리, 선행 0 제거. 리더보드 2,051장 중 279장(13.6%)이 선행 0을
# 갖고, p000488.png 는 403 / p488.png 는 200 임을 라이브 CDN 으로 실측했다.


@requires_node
def test_portrait_url_strips_leading_zeros():
    # spid 848000488 = 올리버 칸(WS). pid "000488" 그대로 쓰면 CDN 이 403 을 준다.
    assert _eval_js("portraitUrl(848000488)").endswith("/players/p488.png")


@requires_node
def test_portrait_url_is_last_six_digits_of_spid():
    # 앞 3자리(844)는 season_id 이므로 떨어져야 한다.
    assert _eval_js("portraitUrl(844224836)").endswith("/players/p224836.png")


@requires_node
def test_action_url_keeps_full_spid():
    # 액션샷은 시즌별로 다르므로 spid 전체를 쓴다(pid 로 자르면 안 된다).
    assert _eval_js("actionUrl(844224836)").endswith("/playersAction/p844224836.png")


@requires_node
def test_urls_point_at_nexon_cdn():
    assert _eval_js("portraitUrl(844224836)").startswith(
        "https://fco.dn.nexoncdn.co.kr/live/externalAssets/common/"
    )


@requires_node
def test_wilson_ci_center_and_width():
    f = render.STATS_JS
    # 선방 60 / 실점 40 = 유효슛 100, p=0.6
    lo = float(_eval_js("wilson(60,40)[0]", f))
    hi = float(_eval_js("wilson(60,40)[1]", f))
    assert lo < 0.6 < hi                      # 점추정을 감싼다
    assert 0.49 < lo < 0.52 and 0.68 < hi < 0.71   # 알려진 Wilson 95% 값 근처
    # 표본이 크면 좁아진다
    lo2 = float(_eval_js("wilson(600,400)[0]", f))
    hi2 = float(_eval_js("wilson(600,400)[1]", f))
    assert (hi2 - lo2) < (hi - lo)            # 유효슛 10배 → 구간 좁아짐


@requires_node
def test_wilson_handles_zero_shots():
    f = render.STATS_JS
    assert _eval_js("wilson(0,0)===null", f) == "true"   # 유효슛 0이면 계산 불가


@requires_node
def test_wilson_handles_missing_counts_no_nan():
    # saves/goals 키가 없는 카드(undefined) → '±NaN%p' 가 아니라 빈 문자열이어야
    f = render.STATS_JS
    assert _eval_js("wilson(undefined,undefined)===null", f) == "true"
    assert _eval_js("ciText(undefined,undefined)", f) == ""
    assert _eval_js("ciText(undefined,5)", f) == ""       # 한쪽 키만 없어도 안전(NaN 차단)


def test_gate_input_present():
    html = render.build_html(_PAYLOAD)
    # 경기수 게이트를 버튼 여러 개 대신 100~50000 숫자 입력 하나로 제공한다.
    assert 'id="gateInput"' in html
    assert 'min="100"' in html and 'max="50000"' in html
    assert 'data-gate=' not in html
    assert "minGate=500" in html.replace(" ", "")   # 기본 게이트 500


def test_ci_shown_in_list_and_hero():
    html = render.build_html(_PAYLOAD)
    assert "wilson" in html                    # 카드 선방률 CI 계산 사용
    assert "ciText" in html or "ciLabel" in html


def test_same_player_table_shows_ci():
    payload = dict(_PAYLOAD)
    payload["same_player"] = [{"player_name": "야신", "cards": [
        {"season_name": "ICON", "grade": 10, "save_pct": 0.7, "gsax_per_shot": 0.05,
         "matches": 60, "saves": 200, "goals": 86},
        {"season_name": "TOTS", "grade": 8, "save_pct": 0.6, "gsax_per_shot": 0.04,
         "matches": 50, "saves": 150, "goals": 100},
    ]}]
    html = render.build_html(payload)
    # 동일선수 표 rows 조립부(gsax_per_shot 을 쓰는 줄)에 CI 가 함께 있어야
    sp_rows = html.split("g.cards.map")[1].split("join('')")[0]
    assert "ciText(c.saves,c.goals)" in sp_rows.replace(" ", "")


def test_list_uses_icon_only_season_cell_with_title():
    html = render.build_html(_PAYLOAD)
    row = html.split("vis.forEach((c,i)=>{")[1].split("if(searching) return")[0]
    assert "seasonIcon(c.season_img,c.season_name)" in row
    assert 'class="pn-row"' in row
    assert row.count("gradeBadge(c.grade)") == 2  # 모바일 이름 옆 + PC 강화 열
    assert '<td class="num mobile-optional">${c.grade}강</td>' not in row
    assert 'class="mobile-card-meta"' not in row


@requires_node
def test_grade_badge_uses_grade_bands_and_embedded_platinum_image():
    html = render.build_html(_PAYLOAD)
    src = re.search(r"const gradeKind=.*?;", html).group(0)
    grades = (1, 2, 4, 5, 7, 8, 10, 11, 13)
    assert [_eval_js(f"gradeKind({grade})", src) for grade in grades] == [
        "none", "bronze", "bronze", "silver", "silver", "gold", "gold", "platinum", "platinum",
    ]
    assert "background-image:url(data:image/webp;base64," in html
    assert "cdn.fc-info.com" not in html
    assert ".grade-platinum" in html and "background-size:cover" in html


def test_season_name_shows_emblem_icon_when_present():
    payload = dict(_PAYLOAD)
    c = dict(payload["leaderboard"][0])
    c["season_img"] = "https://ssl.nexon.com/…/season/ICON.png"
    payload["leaderboard"] = [c]
    payload["same_player"] = [{"player_name": "야신", "cards": [
        {"season_name": "ICON", "grade": 10, "save_pct": 0.7, "gsax_per_shot": 0.05,
         "matches": 60, "season_img": "https://ssl.nexon.com/…/season/ICON.png"},
    ]}]
    html = render.build_html(payload)
    assert "seasonIcon" in html or "season-ico" in html   # 시즌 아이콘 렌더 로직
    assert "c.season_img" in html                          # 메인 목록이 시즌 이미지를 그림


def test_mobile_width_cap_scoped_to_table_not_same_player_summary():
    # 모바일 핵심 4열만 남기므로 선수 셀을 118px 로 자를 이유가 없다.
    # 동일선수 summary 에 전역 제한이 걸리면 총경기 배지가 폭을 다 먹어 이름이 잘린다.
    html = render.build_html(_PAYLOAD)
    assert "td .pcell{max-width:118px}" not in html
    assert ".pcell{gap:7px;max-width:118px}" not in html   # 전역 제한이면 회귀


def test_same_player_summary_shows_total_games():
    payload = dict(_PAYLOAD)
    payload["same_player"] = [{"player_name": "노이어", "cards": [
        {"season_name": "CAP", "grade": 11, "save_pct": 0.53, "gsax_per_shot": 0.04, "matches": 80},
        {"season_name": "TOTS", "grade": 8, "save_pct": 0.6, "gsax_per_shot": 0.05, "matches": 72},
    ]}]
    html = render.build_html(payload)
    # 시즌 합산 총 경기수(80+72=152)를 summary 에서 계산해 보여줘야
    assert "sp-total" in html
    assert "reduce" in html or "totalGames" in html   # 카드 matches 합산 로직


def test_matches_column_labeled_경기수_not_표본():
    html = render.build_html(_PAYLOAD)
    # 메인 목록 경기수는 정렬 가능한 헤더, 동일선수 표는 일반 헤더.
    assert 'data-col="matches">경기수' in html      # 메인 목록(정렬 헤더)
    assert "<th>경기수</th>" in html                # 동일선수 표(일반 헤더)
    assert "<th>표본</th>" not in html              # 컬럼 라벨에 표본 없음


def test_main_list_and_compare_have_salary_column():
    payload = dict(_PAYLOAD)
    c = dict(payload["leaderboard"][0])
    c["info"] = {"salary": 24}
    payload["leaderboard"] = [c]
    payload["same_player"] = [{"player_name": "야신", "cards": [
        {"season_name": "ICON", "grade": 10, "save_pct": 0.7, "gsax_per_shot": 0.05,
         "matches": 60, "info": {"salary": 24}},
    ]}]
    html = render.build_html(payload)
    # 메인 목록 헤더에 급여(정렬 헤더), 동일선수 표에 일반 급여 헤더, 행에 c.info.salary
    assert 'data-col="salary">급여' in html       # 메인 목록(정렬 헤더)
    assert "<th>급여</th>" in html                # 동일선수 mini 표
    assert "c.info" in html                       # 메인 행이 급여를 그림
    # 빈 목록도 실제 리더보드의 9개 열에 맞춰야 한다.
    head = re.search(r"<thead>(.*?)</thead>", html, re.S)[1]
    assert len(re.findall(r"<th(?:\s|>)", head)) == 9
    assert 'colspan="9"' in html
    assert 'colspan="10"' not in html


def test_hero_shows_player_info_chips_when_present():
    payload = dict(_PAYLOAD)
    c = dict(payload["leaderboard"][0])
    c["info"] = {"salary": 24, "ovr": 113, "height": 187, "weight": 85, "body_type": "보통"}
    payload["leaderboard"] = [c]
    html = render.build_html(payload)
    # 칩 렌더 로직과 라벨이 템플릿에 있어야
    assert "chips" in html and "info.salary" in html
    assert "급여" in html and "체형" in html and "기본 OVR" in html


def test_nexon_analytics_script_is_present_and_async():
    """넥슨 Open API 애널리틱스. app_id 는 스크립트가 자기 src 에서 읽으므로 공개가 정상.

    async 가 빠지면 4.2MB 페이지의 첫 렌더를 외부 요청이 막는다.
    """
    html = render.build_html(_PAYLOAD)
    assert "openapi.nexon.com/js/analytics.js?app_id=307467" in html
    m = re.search(r"<script[^>]*analytics\.js[^>]*>", html)
    assert m, "analytics 스크립트 태그 없음"
    assert "async" in m.group(0)


def test_external_scripts_are_limited_to_adsense_and_analytics():
    """자기완결형 HTML 기조: 광고·애널리틱스 외에 외부 리소스를 추가하지 않는다.

    선수 이미지는 <img src> 라 여기 걸리지 않는다(렌더 차단 아님, 폴백 있음).
    파비콘은 data URI 라 https 리소스가 아니므로 여기 걸리지 않는다.
    canonical 은 '가져오는' 리소스가 아니라 자기 주소 선언(SEO 메타)이라 제외한다.
    """
    html = render.build_html(_PAYLOAD)
    html = re.sub(r'<link rel="canonical"[^>]*>', "", html)
    srcs = re.findall(r'<(?:script|link)[^>]*(?:src|href)="(https?://[^"]+)"', html)
    assert srcs == [
        "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-1645070512024900",
        "https://openapi.nexon.com/js/analytics.js?app_id=307467",
    ]
    head = html.split('</head>', 1)[0]
    assert head.count('adsbygoogle.js') == 1
    assert '<script async src="' + srcs[0] + '" crossorigin="anonymous"></script>' in head
    assert '현재 광고는 게재하지 않으며' not in html


def test_seo_meta_points_at_the_deployed_url():
    """색인용 메타는 sitemap·robots 와 같은 주소를 가리켜야 한다(불일치 시 색인 누락)."""
    html = render.build_html(_PAYLOAD)
    assert f'<link rel="canonical" href="{SITE_URL}">' in html
    assert f'<meta property="og:url" content="{SITE_URL}">' in html
    desc = re.search(r'<meta name="description" content="([^"]+)"', html)
    assert desc and 50 <= len(desc.group(1)) <= 160   # 검색결과 스니펫 길이
    assert SITE_URL == "https://gk-save.com/"
    out = Path(__file__).resolve().parents[1] / "out"
    assert f"Sitemap: {SITE_URL}sitemap.xml" in (out / "robots.txt").read_text()
    assert f"<loc>{SITE_URL}</loc>" in (out / "sitemap.xml").read_text()


def test_image_src_attributes_are_escaped():
    """이미지 URL 은 외부(넥슨·fc-info) 출처 값이 섞인다 → src 속성도 escAttr 필수."""
    assert 'src="${escAttr' in render._TEMPLATE
    assert not re.search(r'src="\$\{(?!escAttr)', render._TEMPLATE)


def test_drilldown_shows_traits_with_new_badge():
    payload = dict(_PAYLOAD)
    c = dict(payload["leaderboard"][0])
    c["traits"] = [
        {"code": 60, "name": "GK 공중볼 장악", "is_new": True},
        {"code": 43, "name": "스위퍼 키퍼", "is_new": False},
    ]
    payload["leaderboard"] = [c]
    html = render.build_html(payload)
    assert "const traitUrl=" in html                      # 특성 아이콘 URL(넥슨 CDN)
    assert "traits/trait_icon_" in html                    # 아이콘 경로
    assert "c.traits" in html.replace(" ", "")             # 드릴다운이 특성을 그림
    assert "trait-new" in html                             # 신규 배지 클래스
    assert "신규" in html                                   # 신규 배지 라벨


def test_list_shows_new_trait_icons_only_with_tooltip():
    html = render.build_html(_PAYLOAD).replace(" ", "")
    assert "filter(t=>t.is_new)" in html          # 목록엔 신규특성만
    assert 'class="lb-trait"' in html             # 목록 신규특성 아이콘
    assert 'class="lb-trait-w"data-tip="${escAttr(t.name)}"' in html   # hover 툴팁(특성명)
    assert ".lb-trait-w:hover::after" in html      # 툴팁 말풍선 스타일


def test_drilldown_lazy_loads_details():
    html = render.build_html(_PAYLOAD)
    assert "fetch('details.json')" in html.replace('"', "'")   # 상세는 클릭 시 fetch
    assert "function loadDetails(" in html                     # 1회 캐시 로더
    assert "detail-loading" in html                            # 로딩/에러 표기
    assert "c.gk_sp_id+'_'+c.grade" in html.replace(" ", "").replace('"', "'")  # spid_grade 키
    assert "requestIdleCallback" not in html                   # 행을 열기 전 상세 데이터 요청 금지


@requires_node
def test_drilldown_hides_undefined_shot_types():
    html = render.build_html(_PAYLOAD)
    helper = re.search(r"const visibleShotTypes=.*?;", html)
    assert helper
    rows = "[{type:1,shots:2},{type:2,shots:3},{type:12,shots:3},{type:13,shots:30},{type:14,shots:30}]"
    assert _eval_js(f"visibleShotTypes({rows}).map(t=>t.type).join(',')", helper.group()) == "2,12"


def test_has_reset_filters_button():
    html = render.build_html(_PAYLOAD)
    assert 'id="resetFilters"' in html                         # 필터 초기화 아이콘 버튼
    assert "필터 초기화" in html                                # title/aria 라벨


# ── 지표 드롭다운: 상황별 선방률 + 가성비 ────────────────────────────────────


def test_metric_dropdown_and_value_toggle_present():
    html = render.build_html(_PAYLOAD)
    assert 'id="metricSel"' in html                            # 지표 드롭다운
    for v in ('value="gsax"', 'value="near"', 'value="mid"', 'value="oneone"', 'value="value"'):
        assert v in html                                       # 근/중거리·상황·가성비 옵션
    assert 'id="valueBasis"' in html                           # 가성비 GSAx/선방률 토글
    assert "SIT_GATE" in html                                  # 상황 표본 게이트
    assert "function metricEligible(" in html


@requires_node
def test_default_and_reset_sort_by_save_pct():
    html = render.build_html(_PAYLOAD)
    assert re.search(r'<select id="metricSel">\s*<option value="save_pct">선방률</option>', html)
    initial = html.split("const PAGE=", 1)[1].split("// ── 순위 추이", 1)[0]
    helpers = html.split("function metricVal(c){", 1)[1].split("const esc=", 1)[0]
    sorting = html.split("  let rows=pool.filter", 1)[1].split("  const mobileMetricLabels=", 1)[0]
    reset = html.split("document.getElementById('resetFilters').onclick=()=>{", 1)[1].split("\n};", 1)[0]
    js = render.STATS_JS + "const PAGE=" + initial + "function metricVal(c){" + helpers + """
const pool=[
  {name:'선방률 1위',save_pct:0.8,gsax_per_shot:-0.01},
  {name:'보정 1위',save_pct:0.5,gsax_per_shot:0.06},
  {name:'보정 없음',save_pct:0.9,gsax_per_shot:null}
];
function snapshot(){
""" + "  let rows=pool.filter" + sorting + """
  return {metric, sortDir, order:rows.map(c=>c.name), value:metricCell(rows[0]),
    secondary:sortVal(rows[0],'secondary'), missing:metricCell(pool[2])};
}
const first=snapshot(), fields={};
const document={getElementById:id=>fields[id]||(fields[id]={})};
const gSel={}, gateInput={};
function syncValueBasisBtn(){}
function render(){}
metric='gsax'; sortCol='matches'; sortDir='asc';
""" + reset
    result = json.loads(_eval_js(
        "JSON.stringify({first, reset:snapshot(), selected:fields.metricSel.value})", js,
    ))
    expected = {
        "metric": "save_pct", "sortDir": "desc",
        "order": ["보정 없음", "선방률 1위", "보정 1위"],
        "value": '90.0%<span class="ci"></span>', "secondary": None,
        "missing": '90.0%<span class="ci"></span>',
    }
    assert result == {"first": expected, "reset": expected, "selected": "save_pct"}


@requires_node
def test_metric_value_and_situational_gate_logic():
    # metricVal/metricEligible 핵심 로직을 그대로 재현해 값·게이트·가성비 공식을 고정.
    js = """
let metric='save_pct', valueBasis='gsax';
const SIT_GATE=30;
const METRICS={save_pct:{kind:'pct'},near:{kind:'sit'},value:{kind:'value'}};
function metricVal(c){
  if(metric==='save_pct') return c.save_pct;
  if(metric==='value'){ const sal=c.info&&c.info.salary; if(sal==null) return null;
    const perf = valueBasis==='gsax' ? c.gsax_per_shot : c.save_pct;
    return perf==null ? null : perf/sal; }
  const s=(c.sit||{})[metric]; return (s && s.shots>=SIT_GATE) ? s.pct : null;
}
function metricEligible(c){
  if(METRICS[metric].kind==='sit'){ const s=(c.sit||{})[metric]; return !!(s && s.shots>=SIT_GATE); }
  if(metric==='value') return !!(c.info && c.info.salary!=null);
  return true;
}
const big={save_pct:0.8,gsax_per_shot:0.06,info:{salary:20},sit:{near:{pct:0.9,shots:400}}};
const few={save_pct:0.8,gsax_per_shot:0.06,info:{salary:null},sit:{near:{pct:1.0,shots:5}}};
"""
    # 상황: 표본 충분하면 pct, 미달이면 null+제외
    assert _eval_js("(metric='near',metricVal(big))", js) == "0.9"
    assert _eval_js("(metric='near',metricVal(few))", js) == "null"     # 5슛 < 30 게이트
    assert _eval_js("(metric='near',metricEligible(few))", js) == "false"
    # 가성비: 급여당 GSAx, 토글 시 급여당 선방률, 급여 null 제외
    assert _eval_js("(metric='value',valueBasis='gsax',metricVal(big))", js) == "0.003"   # 0.06/20
    assert _eval_js("(metric='value',valueBasis='save_pct',metricVal(big))", js) == "0.04"   # 0.8/20
    assert _eval_js("(metric='value',metricEligible(few))", js) == "false"                  # 급여 null


def test_favicon_is_embedded_data_uri():
    """브라우저 탭 아이콘: 자기완결 유지를 위해 data URI 로 임베드(외부 파일 아님)."""
    html = render.build_html(_PAYLOAD)
    assert '<link rel="icon" type="image/png" href="data:image/png;base64,' in html
    assert 'rel="apple-touch-icon"' in html
    assert render.FAVICON.startswith("data:image/png;base64,")


def test_meta_line_does_not_leak_raw_since_timestamp():
    """롤링 창을 켜자 since 접미사가 '2026-06-10T05:52:24.383976 이후' 로 노출됐다.

    date_range 가 이미 같은 창을 사람이 읽는 형식으로 보여주므로 접미사는 중복이다.
    """
    payload = dict(_PAYLOAD)
    payload["since"] = "2026-06-10T05:52:24.383976"
    payload["date_range"] = {"min": "2026-06-10", "max": "2026-07-10"}
    html = render.build_html(payload)
    assert "T05:52:24" not in html.split('id="gk-data"')[0]  # 템플릿(스크립트 로직)에 없어야
    assert "${D.since}" not in html


def test_thumbnails_are_lazy_with_intrinsic_size():
    # lazy 없으면 초기 100행이 전부 요청되고, width/height 없으면 레이아웃이 튄다.
    html = render.build_html(_PAYLOAD)
    assert 'loading="lazy"' in html
    assert 'decoding="async"' in html


@requires_node
def test_fallback_chain_walks_to_next_source_then_placeholder():
    # 가짜 <img> 로 체인을 실제로 돌린다: 액션샷 실패 → 얼굴 → 플레이스홀더 → 정지.
    out = _eval_js(
        "(()=>{"
        "const el={dataset:{fb:'FACE'},src:'ACTION',onerror:null,"
        "removeAttribute(){delete this.dataset.fb}};"
        "imgFallback(el);"                       # 1차 실패: 얼굴로 교체
        "const first=el.src;"
        "el.onerror();"                          # 2차 실패: 플레이스홀더로 교체
        "const isPh=el.src.startsWith('data:image/svg+xml');"
        "return [first, isPh, String(el.onerror)].join('|');"
        "})()"
    )
    first, is_placeholder, onerror = out.split("|")
    assert first == "FACE"                       # 액션샷 실패 시 얼굴로 폴백
    assert is_placeholder == "true"              # 얼굴도 실패하면 플레이스홀더
    assert onerror == "null"                     # 체인이 끊겨 무한 재귀하지 않는다


@requires_node
def test_placeholder_is_self_contained_data_uri():
    # 자기완결형 HTML 기조: 플레이스홀더도 외부 의존이 없어야 한다.
    assert _eval_js("PLACEHOLDER").startswith("data:image/svg+xml")


@requires_node
def test_thumbnail_uses_portrait_not_action():
    # 목록에 액션샷을 쓰면 커버리지 62% 라 초기 렌더에서 403 이 쏟아진다.
    assert _eval_js("thumbUrl(844224836)") == _eval_js("portraitUrl(844224836)")
    assert _eval_js("thumbUrl(844224836)") != _eval_js("actionUrl(844224836)")


# ── 동일 선수 비교 탭 검색 ─────────────────────────────────────────────────


@requires_node
def test_match_name_is_case_insensitive_substring():
    f = render.FILTER_JS
    assert _eval_js("matchName('Petr Cech','cech')", f) == "true"
    assert _eval_js("matchName('노이어','노이')", f) == "true"
    assert _eval_js("matchName('노이어','칸')", f) == "false"


@requires_node
def test_match_name_treats_empty_query_as_match_all():
    f = render.FILTER_JS
    assert _eval_js("matchName('아무개','')", f) == "true"
    assert _eval_js("matchName(null,'')", f) == "true"       # 이름 없는 그룹도 통과
    assert _eval_js("matchName(null,'x')", f) == "false"      # 질의가 있으면 탈락


# ── 리더보드 탭 여러 이름 검색(쉼표 OR) ──────────────────────────────────────


@requires_node
def test_match_names_ors_comma_separated_terms():
    f = render.FILTER_JS
    # "노이어, 칸" → 두 선수 중 하나라도 이름에 들어가면 통과
    assert _eval_js("matchNames('마누엘 노이어','노이어, 칸')", f) == "true"
    assert _eval_js("matchNames('올리버 칸','노이어, 칸')", f) == "true"
    assert _eval_js("matchNames('부폰','노이어, 칸')", f) == "false"


@requires_node
def test_match_names_single_term_and_empty():
    f = render.FILTER_JS
    assert _eval_js("matchNames('노이어','노이')", f) == "true"    # 쉼표 없으면 단일 부분일치
    assert _eval_js("matchNames('아무개','')", f) == "true"        # 빈 질의는 전부 통과
    assert _eval_js("matchNames('아무개',' , ')", f) == "true"     # 쉼표만/공백만도 전부 통과


@requires_node
def test_match_names_ignores_whitespace_around_commas():
    f = render.FILTER_JS
    # 쉼표 주변 공백은 무시하되, 이름 내부 공백(마누엘 노이어)은 보존한다.
    assert _eval_js("matchNames('마누엘 노이어','  노이어  ,  칸  ')", f) == "true"
    assert _eval_js("matchNames('마누엘 노이어','마누엘 노이어')", f) == "true"


def test_leaderboard_uses_multi_name_matcher():
    html = render.build_html(_PAYLOAD)
    # 리더보드 필터는 여러 이름 매처를, 동일 선수 비교 탭은 단일 matchName 을 쓴다.
    assert "matchNames(c.player_name,q)" in html.replace(" ", "")
    assert "쉼표로" in html                        # 검색창 placeholder 힌트


# ── 리더보드 탭 팀컬러 검색(국가·클럽·시즌) ─────────────────────────────────


@requires_node
def test_match_team_color_matches_nation_club_or_season():
    f = render.FILTER_JS
    # 실제 호출부는 질의를 소문자화하므로 테스트도 소문자 q 로 넣는다.
    c = "{bio:{nation_name:'이탈리아',clubs:['파르마','유벤투스']},season_name:'WG (Warriors of Glory)'}"
    assert _eval_js(f"matchTeamColor({c},'이탈리아')", f) == "true"    # 국가명
    assert _eval_js(f"matchTeamColor({c},'유벤투스')", f) == "true"    # 클럽명
    assert _eval_js(f"matchTeamColor({c},'파르')", f) == "true"        # 클럽 부분일치
    assert _eval_js(f"matchTeamColor({c},'wg')", f) == "true"          # 시즌 약칭(팀컬러)
    assert _eval_js(f"matchTeamColor({c},'warriors')", f) == "true"    # 시즌 부분일치
    assert _eval_js(f"matchTeamColor({c},'브라질')", f) == "false"     # 국가·클럽·시즌 다 불일치


@requires_node
def test_match_team_color_empty_query_and_season_without_bio():
    f = render.FILTER_JS
    assert _eval_js("matchTeamColor({bio:null,season_name:null},'')", f) == "true"       # 빈 질의 통과
    assert _eval_js("matchTeamColor({bio:null,season_name:null},'이탈리아')", f) == "false"  # 아무것도 없으면 탈락
    # bio 가 없어도 시즌명(팀컬러)으로 통과
    assert _eval_js("matchTeamColor({bio:null,season_name:'LE (Legend of Europa)'},'le')", f) == "true"


def test_leaderboard_has_team_color_search_input():
    html = render.build_html(_PAYLOAD)
    assert 'id="natClubSearch"' in html               # 팀컬러 검색 입력(이름과 별개)
    assert "팀컬러 검색" in html                       # placeholder
    assert "matchTeamColor(c,natClubQ)" in html.replace(" ", "")   # 필터에 결합


def test_recent_team_color_controls_and_storage_present():
    html = render.build_html(_PAYLOAD)
    assert 'id="recentTeamColors"' in html             # 검색창 아래 최근 검색 목록
    assert 'id="clearRecentTeamColors"' in html        # 전체 삭제
    assert "gksave_recent_team_colors_v1" in html      # 브라우저별 저장 키
    assert "localStorage.setItem" in html               # 서버·쿠키 없이 로컬 저장
    assert "D.leaderboard.some(c=>matchTeamColor" in html  # 결과가 있는 완성 검색어만 저장


@requires_node
def test_recent_team_colors_dedupe_and_keep_latest_three():
    f = render.FILTER_JS
    expr = "JSON.stringify(addRecentTeamColor(['프랑스','WS','파리 생제르맹'],'ws'))"
    assert _eval_js(expr, f) == '["ws","프랑스","파리 생제르맹"]'


@requires_node
def test_recent_team_colors_ignore_invalid_storage_values():
    f = render.FILTER_JS
    expr = "JSON.stringify(normalizeRecentTeamColors([' 프랑스 ','',null,'프랑스','WS','파리','첼시']))"
    assert _eval_js(expr, f) == '["프랑스","WS","파리"]'


# ── 리더보드 탭 급여 범위 필터 ──────────────────────────────────────────────


@requires_node
def test_match_salary_bounds():
    f = render.FILTER_JS
    # 이상(lo)만
    assert _eval_js("matchSalary(20,15,null)", f) == "true"
    assert _eval_js("matchSalary(10,15,null)", f) == "false"
    # 이하(hi)만
    assert _eval_js("matchSalary(10,null,15)", f) == "true"
    assert _eval_js("matchSalary(20,null,15)", f) == "false"
    # 범위(둘 다) — 경계 포함
    assert _eval_js("matchSalary(15,15,20)", f) == "true"
    assert _eval_js("matchSalary(20,15,20)", f) == "true"
    assert _eval_js("matchSalary(21,15,20)", f) == "false"


@requires_node
def test_match_salary_empty_passes_and_null_salary_excluded_when_bounded():
    f = render.FILTER_JS
    assert _eval_js("matchSalary(5,null,null)", f) == "true"      # 범위 미지정 → 전부 통과
    assert _eval_js("matchSalary(null,null,null)", f) == "true"   # 급여 미상도 필터 없으면 통과
    assert _eval_js("matchSalary(null,15,null)", f) == "false"    # 범위 켜지면 미상은 제외
    assert _eval_js("matchSalary(null,null,20)", f) == "false"


def test_leaderboard_has_salary_filter_inputs():
    html = render.build_html(_PAYLOAD)
    assert 'id="salMin"' in html and 'id="salMax"' in html
    assert "matchSalary(c.info&&c.info.salary,salMin,salMax)" in html.replace(" ", "")


# ── 리더보드 OVR 컬럼 + 헤더 클릭 정렬 ───────────────────────────────────────


def test_leaderboard_has_ovr_column():
    payload = dict(_PAYLOAD)
    c = dict(payload["leaderboard"][0]); c["info"] = {"ovr": 113}
    payload["leaderboard"] = [c]
    html = render.build_html(payload)
    assert 'data-col="ovr">OVR' in html               # OVR 정렬 헤더
    assert "c.info&&c.info.ovr" in html.replace(" ", "")   # 행이 OVR 을 그림


def test_headers_are_clickable_sort_with_arrows():
    html = render.build_html(_PAYLOAD)
    # 숫자 컬럼이 정렬 가능 헤더(data-col)로, 화살표 표기 로직·방향 토글이 있어야
    for col in ("grade", "salary", "ovr", "save_pct", "secondary", "matches"):
        assert f'data-col="{col}"' in html
    assert "function updateHeaders(" in html
    assert "sortDir==='asc'?'▲':'▼'" in html.replace(" ", "").replace('"', "'")
    assert "sortDir==='asc'?'desc':'asc'" in html.replace(" ", "").replace('"', "'")
    # 옛 정렬 버튼(data-sort)은 없어야
    assert "data-sort=" not in html
    assert 'aria-sort' in html and 'tabindex="0"' in html


@requires_node
def test_sort_val_reads_nested_info_and_gsax():
    # sortVal 은 급여·OVR 을 c.info 에서, GSAx 는 카드 값에서 읽는다.
    js = render.FILTER_JS + render.STATS_JS + """
function sortVal(c,col){
  if(col==='salary') return c.info&&c.info.salary;
  if(col==='ovr') return c.info&&c.info.ovr;
  if(col==='gsax') return c.gsax_per_shot;
  return c[col];
}
const c={grade:9,save_pct:0.7,matches:120,gsax_per_shot:0.05,info:{salary:24,ovr:113}};
"""
    assert _eval_js("sortVal(c,'salary')", js) == "24"
    assert _eval_js("sortVal(c,'ovr')", js) == "113"
    assert _eval_js("sortVal(c,'matches')", js) == "120"
    assert _eval_js("sortVal(c,'gsax')", js) == "0.05"


def test_gsax_ex_short_toggle_removed():
    html = render.build_html(_PAYLOAD)
    assert 'id="exShort"' not in html
    assert "gsax_ex_short_per_shot" not in html


# ── 리더보드 탭 강화단계 필터(드랍박스) ─────────────────────────────────────


def test_grade_filter_dropdown_present():
    html = render.build_html(_PAYLOAD)
    assert 'id="gradeFilter"' in html
    assert '<option value="">강화</option>' in html
    assert "gradeFilter" in html                # 필터 상태 변수·onchange 핸들러


def test_matches_sort_button_removed():
    html = render.build_html(_PAYLOAD)
    # 강화 필터가 드랍박스로 분리되면서 "경기수" 정렬 버튼은 제거한다(사용자 요청).
    assert 'data-sort="matches"' not in html


def test_compare_tab_has_search_input():
    html = render.build_html(_PAYLOAD)
    assert 'id="spSearch"' in html
    assert 'id="spCount"' in html


def test_compare_groups_carry_name_for_filtering():
    # display 토글로 거르므로 각 그룹이 자기 이름을 들고 있어야 한다(재렌더 시 펼침 상태가 날아간다).
    html = render.build_html(_PAYLOAD)
    assert "data-name=" in html


# ── 강화 효과: 배너에서 내리고 지표 설명 탭에만 남긴다 ──────────────────────


def test_grade_effect_not_pinned_to_top_banner():
    # 귀무 결과를 최상단에 상시 고정하면 '카드 추천 아님' 경고의 주목도를 갉아먹는다.
    html = render.build_html(_PAYLOAD)
    assert 'id="ge"' not in html
    assert "⚡ 강화 효과" not in html


def test_grade_effect_survives_in_help_tab():
    # 배너에서 뺐다고 계산·설명까지 잃으면 안 된다.
    html = render.build_html(_PAYLOAD)
    assert 'id="geDetail"' in html
    assert "geLong" in html
    assert "grade_effect" in html


@requires_node
def test_gsax_champion_selection_and_click():
    html = render.build_html(_PAYLOAD)
    champion_js = html.split("function renderChampions(L){", 1)[1].split("\nrender();", 1)[0]
    js = next(line for line in html.splitlines() if line.startswith("const gps=")) + """
const cards=[], button={}, selector={};
let metric='oneone', sortCol='matches', sortDir='asc', limit=200;
let rendered=false, scrolled=false;
const PAGE=100, SIT_GATE=30;
const pct=v=>(v*100).toFixed(1)+'%';
const el={style:{}, querySelectorAll:()=>[button]};
const document={getElementById:id=>id==='champions'?el:
  id==='metricSel'?selector:{scrollIntoView:()=>{scrolled=true;}}};
function champCard(badge, sub, c, value, unit, act){
  cards.push({badge, name:c?.player_name, value, unit, act});
  if(cards.length===1) button.dataset={act};
  return '';
}
function syncValueBasisBtn(){}
function render(){rendered=true;}
""" + "function renderChampions(L){" + champion_js + """
renderChampions([
  {player_name:'보정 1위', save_pct:0.6, gsax_per_shot:0.3},
  {player_name:'선방률 1위', save_pct:0.8, gsax_per_shot:null},
  {player_name:'선방률 없음', save_pct:null, gsax_per_shot:0.2}
]);
button.onclick();
"""
    result = json.loads(_eval_js(
        "JSON.stringify({card:cards[0], metric, selected:selector.value, sortCol, "
        "sortDir, limit, rendered, scrolled})", js,
    ))
    assert result == {
        "card": {"badge": "GSAx 킹", "name": "보정 1위", "value": "+30.0", "unit": "GSAx/100", "act": "gsax"},
        "metric": "gsax", "selected": "gsax", "sortCol": "save_pct",
        "sortDir": "desc", "limit": 100, "rendered": True, "scrolled": True,
    }


def test_mobile_leaderboard_keeps_only_core_columns():
    html = render.build_html(_PAYLOAD)
    assert "#lb .mobile-optional{display:none}" in html
    assert 'class="sortable mobile-metric"' in html
    assert 'class="sortable mobile-secondary"' in html
    assert 'class="sortable mobile-matches"' in html
    headers = html.split('<table id="lb">', 1)[1].split('</thead>', 1)[0]
    assert headers.index('>선방률 ') < headers.index('>GSAx/100 ') < headers.index('>경기수 ')
    row = html.split('tr.innerHTML=', 1)[1].split('tr.onclick=', 1)[0]
    assert '<td class="num">${metric===\'gsax\'?pct(c.save_pct):gps(c.gsax_per_shot)}</td>' in row
    assert "window.innerWidth<640?mobileMetricLabels[metric]" in html


def test_mobile_header_meta_and_collapsible_controls():
    html = render.build_html(_PAYLOAD)
    assert ">FC온라인 GK 선방률 순위<" in html
    assert "`기간 ${dr.min} ~ ${dr.max} · `" in html
    assert "`${period}총 경기 ${totalMatches}건`" in html
    assert "총 수집 경기" not in html
    assert 'id="readingGuide"' not in html and 'aria-label="읽는 법 보기"' not in html
    assert '<div class="reading-note" id="warn"></div>' in html
    assert 'id="advancedFilters"' in html
    assert "window.matchMedia('(min-width:641px)').matches" in html


def test_mobile_names_have_their_own_line_and_metric_headers_can_wrap():
    html = render.build_html(_PAYLOAD)
    assert '#lb .thumb,#lb .lb-trait-w{display:none}' in html
    assert '#lb .ptext,#lb .pn-row{display:contents}' in html
    assert '#lb .pn{grid-column:1/-1;white-space:normal;overflow-wrap:anywhere}' in html
    assert '#lb .season-ico{grid-row:2}' in html
    assert '#lb .grade-badge{grid-row:2;justify-self:end}' in html
    assert '#lb thead th{height:44px;white-space:normal}' in html


def test_mobile_filter_priority_and_champion_card_meta():
    html = render.build_html(_PAYLOAD)
    advanced_start = html.index('<details class="advanced-filters"')
    advanced_end = html.index("</details>", advanced_start)
    primary = html[html.index('<div class="controls primary-controls">'):advanced_start]
    advanced = html[advanced_start:advanced_end]
    assert 'id="metricSel"' in advanced
    assert all(f'id="{field}"' in primary for field in ("teamColorField", "gradeFilter", "salMin", "salMax"))
    assert ".champ .csub,.champ img.hero-img,.champ .cunit{display:none}" in html
    assert 'class="cseason-name"' not in html
    assert 'class="cgrade">${c.grade}강' in html
    assert '<option value="">강화</option>' in html
    assert ".leaderboard-tip{display:none}" in html


def test_mobile_touch_targets_and_row_keyboard_support():
    html = render.build_html(_PAYLOAD)
    assert "min-height:44px" in html
    assert 'class="row-toggle"' in html
    assert "e.key==='Enter'" in html and "e.key===' '" in html


def test_mobile_detail_row_spans_only_visible_columns():
    html = render.build_html(_PAYLOAD)
    assert "const detailColspan=window.innerWidth<=640?5:9;" in html
    assert 'colspan="${detailColspan}"' in html


def test_detail_hero_is_compact_and_aligns_season_with_grade():
    html = render.build_html(_PAYLOAD)
    assert ".hero-img{width:88px;height:88px" in html
    assert ".hero-meta .sub{display:flex;align-items:center" in html
    assert '<span>· ${c.grade}강</span>' in html
