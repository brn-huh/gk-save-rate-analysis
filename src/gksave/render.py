"""정적 HTML 표출.

export 페이로드를 자기완결형 HTML 한 장으로 렌더한다(외부 의존 없음, inline CSS).
파일을 그냥 열어도 되고 GitHub Pages 등에 그대로 올려도 된다.

교란 주의 라벨을 페이지 최상단 배너로 크게 노출한다(설계 D1 결정).
"""

from __future__ import annotations

from html import escape
from typing import Any


def _pct(v: float | None) -> str:
    return "N/A" if v is None else f"{v * 100:.1f}%"


def _leaderboard_rows(cards: list[dict[str, Any]]) -> str:
    if not cards:
        return '<tr><td colspan="6" class="empty">게이트를 통과한 카드가 없습니다.</td></tr>'
    out = []
    for c in cards:
        who = escape(c.get("player_name") or f"spId {c['gk_sp_id']}")
        season = escape(c.get("season_name") or "")
        total = c["saves"] + c["goals"]
        out.append(
            f"<tr><td class='rank'>{c['rank']}</td>"
            f"<td>{who}</td><td class='season'>{season}</td>"
            f"<td class='pct'>{_pct(c['save_pct'])}</td>"
            f"<td class='num'>{c['saves']}/{total}</td>"
            f"<td class='num'>{c['matches']}</td></tr>"
        )
    return "\n".join(out)


def _same_player_section(groups: list[dict[str, Any]]) -> str:
    if not groups:
        return ""
    blocks = []
    for g in groups:
        rows = "\n".join(
            f"<tr><td>{escape(c.get('season_name') or '')}</td>"
            f"<td class='pct'>{_pct(c['save_pct'])}</td>"
            f"<td class='num'>{c['matches']}</td></tr>"
            for c in g["cards"]
        )
        blocks.append(
            f"<details><summary>{escape(g['player_name'])}</summary>"
            f"<table class='mini'><thead><tr><th>시즌</th><th>선방률</th><th>표본</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></details>"
        )
    return (
        "<h2>동일 선수 · 시즌 비교</h2>"
        "<p class='muted'>같은 선수의 시즌별 선방률. (여전히 raw — 유저 교란 포함)</p>"
        + "\n".join(blocks)
    )


def build_html(payload: dict[str, Any]) -> str:
    ge = payload.get("grade_effect", {})
    delta = ge.get("mean_save_pct_delta_per_grade")
    delta_txt = "표본 부족" if delta is None else f"{delta * 100:+.2f}%p / 강화단계"
    same_player = _same_player_section(payload.get("same_player", []))

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FC온라인 GK 선방률 리더보드</title>
<style>
  :root {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  body {{ max-width: 860px; margin: 0 auto; padding: 24px 16px; color: #1a1a1a; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
  .warn {{ background: #fff4e5; border: 1px solid #ffb84d; border-radius: 8px;
           padding: 12px 14px; font-size: .9rem; line-height: 1.5; margin: 16px 0; }}
  .meta {{ color: #666; font-size: .85rem; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0 24px; font-size: .92rem; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; }}
  th {{ color: #666; font-weight: 600; font-size: .82rem; }}
  td.rank {{ color: #999; width: 2.5rem; }}
  td.pct {{ font-variant-numeric: tabular-nums; font-weight: 600; }}
  td.num, td.season {{ color: #555; font-variant-numeric: tabular-nums; }}
  td.empty {{ text-align: center; color: #999; padding: 24px; }}
  .muted {{ color: #888; font-size: .85rem; }}
  details {{ margin: 6px 0; }}
  summary {{ cursor: pointer; font-weight: 600; }}
  table.mini {{ width: auto; margin: 6px 0 12px 16px; font-size: .85rem; }}
</style></head><body>
<h1>FC온라인 골키퍼 선방률 리더보드</h1>
<p class="meta">생성 {escape(str(payload.get("generated_at", "")))} ·
   게이트 {payload.get("gate_min_matches")}경기↑ · {payload.get("leaderboard_count", 0)}장</p>

<div class="warn"><b>⚠️ 읽는 법:</b> {escape(payload.get("warning", ""))}</div>

<h2>카드 리더보드</h2>
<table><thead><tr>
  <th>#</th><th>선수</th><th>시즌</th><th>선방률</th><th>세이브</th><th>표본경기</th>
</tr></thead><tbody>
{_leaderboard_rows(payload.get("leaderboard", []))}
</tbody></table>

<h2>강화 효과 (유저 내 비교)</h2>
<p class="muted">같은 유저·같은 카드에서 강화단계 1 상승당 평균 선방률 변화 —
   유저 실력 교란을 제거한 값: <b>{delta_txt}</b>
   (페어유저 {ge.get("paired_users", 0)}, 페어 {ge.get("pairs", 0)})</p>

{same_player}
</body></html>
"""
