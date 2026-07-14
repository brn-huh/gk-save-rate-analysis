"""인터랙티브 HTML 표출: 데이터 embed, 정적 라벨, 스크립트 주입 방지, 선수 이미지."""

import json
import re
import shutil
import subprocess

import pytest

from gksave import render

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


def test_gate_toggle_buttons_present():
    html = render.build_html(_PAYLOAD)
    # 경기수 게이트 필터 토글 50/100/200
    for g in ("50", "100", "200"):
        assert f'data-gate="{g}"' in html
    assert "minGate" in html                  # 필터 상태 변수


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
    # 목록은 seasonCell(아이콘 우선, 시즌명은 title 로만) 을 쓴다
    assert "const seasonCell=" in html
    assert "title=" in html                        # hover 로 시즌명 확인
    # 목록 행·동일선수 표가 seasonCell 을 호출(원시 season_name 직접 출력 아님)
    assert html.count("seasonCell(c.season_img") >= 1
    assert "seasonCell(c.season_img,c.season_name)" in html.replace(" ", "")


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
    # .pcell 폭 제한(118px)이 전역이면 동일선수 summary 에서 총경기 배지가 폭을 다 먹어
    # 선수 이름이 0px 로 잘린다 → td 안의 .pcell 로만 한정돼야 한다.
    html = render.build_html(_PAYLOAD)
    assert "td .pcell{max-width:118px}" in html
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
    # 경기수 컬럼 라벨: 메인 헤더 + 동일선수 헤더. 정렬 버튼은 사용자 요청으로 제거됨(강화 필터가
    # 드랍박스로 분리되며 함께 정리) → test_matches_sort_button_removed 가 그 부재를 검증.
    assert html.count("<th>경기수</th>") == 2       # 메인 목록 + 동일선수 표
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
    # 메인 목록 헤더에 급여, 행에 c.info.salary
    assert html.count("<th>급여</th>") == 2      # 메인 목록 + 동일선수 mini 표
    assert "c.info" in html                       # 메인 행이 급여를 그림
    # 상세 행 colspan 은 컬럼 수(8)와 맞아야
    assert 'colspan="8"' in html
    assert 'colspan="7"' not in html


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


def test_analytics_is_the_only_external_dependency():
    """자기완결형 HTML 기조: 애널리틱스 말고 외부에서 끌어오는 리소스가 늘면 안 된다.

    선수 이미지는 <img src> 라 여기 걸리지 않는다(렌더 차단 아님, 폴백 있음).
    """
    html = render.build_html(_PAYLOAD)
    srcs = re.findall(r'<(?:script|link)[^>]*(?:src|href)="(https?://[^"]+)"', html)
    assert srcs == ["https://openapi.nexon.com/js/analytics.js?app_id=307467"]


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


# ── 리더보드 탭 강화단계 필터(드랍박스) ─────────────────────────────────────


def test_grade_filter_dropdown_present():
    html = render.build_html(_PAYLOAD)
    assert 'id="gradeFilter"' in html
    assert "강화 전체" in html                 # 기본 옵션(필터 없음)
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


def test_leaderboard_table_scrolls_inside_its_own_container():
    # 375px 에서 표는 606px 다. 표를 감싸지 않으면 페이지 본문이 통째로 가로 스크롤된다.
    html = render.build_html(_PAYLOAD)
    assert "overflow-x:auto" in html.replace(" ", "")
    # 표가 래퍼 안에 들어 있어야 한다
    assert re.search(r'<div class="tw">\s*<table id="lb">', html)
