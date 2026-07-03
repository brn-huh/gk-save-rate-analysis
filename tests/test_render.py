"""HTML 표출 테스트: 필수 요소·이스케이프·빈 리더보드."""

from gksave import render

_PAYLOAD = {
    "generated_at": "2026-07-02T00:00:00Z",
    "gate_min_matches": 50,
    "warning": "raw 선방률 — 카드 추천 아님",
    "leaderboard_count": 1,
    "leaderboard": [
        {"rank": 1, "gk_sp_id": 101190053, "player_name": "야신", "grade": 10,
         "season_name": "ICON", "save_pct": 0.75, "saves": 3, "goals": 1, "matches": 60},
    ],
    "grade_effect": {"mean_save_pct_delta_per_grade": 0.02, "paired_users": 4, "pairs": 6},
    "gsax": [
        {"rank": 1, "gk_sp_id": 101190053, "player_name": "야신", "grade": 10,
         "season_name": "ICON", "gsax": 12.3, "gsax_per_shot": 0.05, "shots": 240},
    ],
    "same_player": [
        {"player_name": "노이어", "cards": [
            {"season_name": "CAP", "grade": 11, "save_pct": 0.53, "matches": 80},
            {"season_name": "BLD", "grade": 8, "save_pct": None, "matches": 1},
        ]},
    ],
}


def test_html_contains_key_elements():
    html = render.build_html(_PAYLOAD)
    assert html.startswith("<!doctype html>")
    assert "카드 추천 아님" in html          # 경고 배너
    assert "야신" in html and "ICON" in html  # 리더보드 이름·시즌
    assert "75.0%" in html                    # 선방률 포맷
    assert "60" in html                       # 표본경기
    assert "노이어" in html                   # 동일선수 비교
    assert "10강" in html                     # 강화단계 표기
    assert "GSAx" in html and "+12.3" in html # GSAx 섹션
    assert "N/A" in html                      # None 선방률 안전 표기
    assert "+2.00%p" in html                  # 강화효과


def test_html_escapes_player_name():
    payload = dict(_PAYLOAD)
    payload["leaderboard"] = [
        {"rank": 1, "gk_sp_id": 1, "player_name": "<script>x</script>",
         "season_name": "", "save_pct": 0.5, "saves": 1, "goals": 1, "matches": 60},
    ]
    payload["same_player"] = []
    html = render.build_html(payload)
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_leaderboard():
    payload = dict(_PAYLOAD)
    payload["leaderboard"] = []
    payload["same_player"] = []
    html = render.build_html(payload)
    assert "게이트를 통과한 카드가 없습니다" in html
