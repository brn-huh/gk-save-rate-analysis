"""인터랙티브 HTML 표출: 데이터 embed, 정적 라벨, 스크립트 주입 방지, 선수 이미지."""

import json
import re
import shutil
import subprocess

import pytest

from gksave import render

_NODE = shutil.which("node")
requires_node = pytest.mark.skipif(_NODE is None, reason="node 없음 — JS 동작 검증 생략")


def _eval_js(expr: str) -> str:
    """페이지에 실제로 실려 나가는 이미지 JS를 그대로 실행해 표현식 값을 얻는다."""
    js = f"{render.IMAGE_JS}\nprocess.stdout.write(String({expr}));"
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


def test_leaderboard_table_scrolls_inside_its_own_container():
    # 375px 에서 표는 606px 다. 표를 감싸지 않으면 페이지 본문이 통째로 가로 스크롤된다.
    html = render.build_html(_PAYLOAD)
    assert "overflow-x:auto" in html.replace(" ", "")
    # 표가 래퍼 안에 들어 있어야 한다
    assert re.search(r'<div class="tw">\s*<table id="lb">', html)
