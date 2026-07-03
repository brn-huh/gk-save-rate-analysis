"""인터랙티브 HTML 표출: 데이터 embed, 정적 라벨, 스크립트 주입 방지."""

import json
import re

from gksave import render

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
