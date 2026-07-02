"""T0 스파이크 프로브 — 아키텍처 확정 전 선행 게이트.

발급 키(NEXON_API_KEY)로 실제 API를 몇 번 때려서, 설계가 추측으로 잠근
부분을 실측 검증한다.

T0 실측(2026-07)으로 이미 확인된 사실:
  · /v1/match 전역 피드의 matchId는 match-detail 로 안 풀린다(400).
    → 유효 경로는 닉네임 → /v1/id → ouid → /v1/user/match → match-detail 뿐.
  · match-detail 구조는 명세와 일치, shootDetail 카운트 == effectiveShootTotal,
    result==3 카운트 == goalTotal → result==1=선방 확정.

이 프로브는 그 사실을 재확인하고 구조/강화범위/시즌디코드를 점검한다.

실행:  gksave spike   (기본 시드 닉네임 사용, --가 없으므로 코드에서 지정)
"""

from __future__ import annotations

from typing import Any

from . import api
from .config import DEFAULT, EFFECTIVE_RESULTS, GK_POSITION, RESULT_GOAL, RESULT_SAVE
from .http import ApiError, ResilientClient

# 시드로 쓸 기본 닉네임 (아무 실유저나 무방; 스노우볼 시작점일 뿐)
DEFAULT_SEED_NICK = "아이콘"


def _gk_players(match_info: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in match_info.get("player", []) if p.get("spPosition") == GK_POSITION]


def _probe_feed_and_resolve(client: ResilientClient, nick: str, report: list[str]) -> str | None:
    """전역 피드 상태를 기록하고, 실제 풀리는 matchId를 user/match 로 확보."""
    report.append("── 1. 수집 경로 ──")
    try:
        feed = api.list_matches(client, offset=0, limit=1)
        fid = feed[0] if feed else None
        report.append(f"  /v1/match 전역 피드: {len(feed)}건 (첫 {fid})")
        if fid:
            try:
                api.get_match_detail(client, fid)
                report.append("  전역 피드 matchId → match-detail: 200 (전역 시드 사용 가능)")
            except ApiError as e:
                report.append(
                    f"  전역 피드 matchId → match-detail: {e.status} "
                    f"⇒ 전역 시드 무효. ouid/user-match 경로 사용(설계대로)."
                )
    except ApiError as e:
        report.append(f"  /v1/match 오류: {e}")

    # 유효 경로: 닉네임 → ouid → user/match → matchId
    try:
        ouid = api.get_ouid(client, nick)
        report.append(f"  닉네임 '{nick}' → ouid {ouid[:10]}…")
        umatches = api.list_user_matches(client, ouid, offset=0, limit=5)
        report.append(f"  /v1/user/match: {len(umatches)}건")
        for mid in umatches:
            try:
                api.get_match_detail(client, mid)
                report.append(f"  user/match matchId {mid} → match-detail: 200 ✅")
                return mid
            except ApiError as e:
                report.append(f"  user/match matchId {mid} → {e.status}")
    except ApiError as e:
        report.append(f"  ❌ 유효 경로 실패: {e}")
    return None


def _probe_detail_structure(detail: dict[str, Any], report: list[str]) -> None:
    report.append("── 2. match-detail 구조 ──")
    report.append(f"  matchType={detail.get('matchType')} (공식경기=50 기대)")
    infos = detail.get("matchInfo", [])
    report.append(f"  matchInfo 수: {len(infos)} (2 기대)")
    if not infos:
        return
    sd = infos[0].get("shootDetail", [])
    if sd:
        keys = set(sd[0].keys())
        for need in ("x", "y", "type", "result", "inPenalty", "assist", "hitPost", "spId", "spGrade"):
            report.append(f"    shootDetail.{need}: {'✅' if need in keys else '❌ 없음'}")
    players = infos[0].get("player", [])
    if players:
        pk = set(players[0].keys())
        for need in ("spId", "spPosition", "spGrade"):
            report.append(f"    player.{need}: {'✅' if need in pk else '❌ 없음'}")


def _probe_result_definition(detail: dict[str, Any], report: list[str]) -> None:
    report.append("── 3. result==1 정의 (선방 vs 수비수 블록) ──")
    for idx, info in enumerate(detail.get("matchInfo", [])):
        sd = info.get("shootDetail", [])
        eff = sum(1 for s in sd if s.get("result") in EFFECTIVE_RESULTS)
        goals = sum(1 for s in sd if s.get("result") == RESULT_GOAL)
        saves = sum(1 for s in sd if s.get("result") == RESULT_SAVE)
        shoot = info.get("shoot", {})
        eff_agg = shoot.get("effectiveShootTotal")
        goal_agg = shoot.get("goalTotal")
        report.append(
            f"  [{idx}] shootDetail 유효(1,3)={eff} 선방={saves} 실점={goals}"
            f" | 팀집계 effectiveShootTotal={eff_agg} goalTotal={goal_agg}"
        )
        if eff_agg is not None and eff != eff_agg:
            report.append(f"    ⚠️ 불일치: 유효슛({eff}) ≠ effectiveShootTotal({eff_agg}) — 공식 재확인")
        elif eff_agg is not None:
            report.append("    ✅ 유효슛 카운트 = effectiveShootTotal (result==1=선방 확정)")


def _probe_gk_grade(detail: dict[str, Any], report: list[str]) -> int | None:
    report.append("── 4. GK spGrade ──")
    sample: int | None = None
    for idx, info in enumerate(detail.get("matchInfo", [])):
        gks = _gk_players(info)
        report.append(f"  [{idx}] GK수={len(gks)} spGrade={[g.get('spGrade') for g in gks]} "
                      f"spId={[g.get('spId') for g in gks]}")
        if gks and sample is None:
            sample = gks[0].get("spId")
    return sample


def _probe_season_decode(client: ResilientClient, sp_id: int | None, report: list[str]) -> None:
    report.append("── 5. spId → 시즌 디코드 ──")
    if sp_id is None:
        report.append("  샘플 GK spId 없음 → 스킵")
        return
    try:
        seasons = api.get_metadata(client, "seasonid")
    except ApiError as e:
        report.append(f"  ❌ /metadata/seasonid 실패: {e}")
        return
    sid = str(sp_id)
    best = None
    for s in seasons:
        code = str(s.get("seasonId"))
        if sid.startswith(code) and (best is None or len(code) > len(str(best.get("seasonId")))):
            best = s
    if best:
        report.append(f"  spId={sp_id} → seasonId={best.get('seasonId')} ({best.get('className')})")
    else:
        report.append(f"  spId={sp_id} 접두 매칭 seasonId 없음 → 디코드 규칙 재확인")


def run(settings=DEFAULT, seed_nick: str = DEFAULT_SEED_NICK) -> str:
    report: list[str] = ["", "===== T0 스파이크 결과 =====", ""]
    with ResilientClient(settings) as client:
        mid = _probe_feed_and_resolve(client, seed_nick, report)
        if mid:
            detail = api.get_match_detail(client, mid)
            _probe_detail_structure(detail, report)
            _probe_result_definition(detail, report)
            sp_id = _probe_gk_grade(detail, report)
            _probe_season_decode(client, sp_id, report)
    report.append("")
    report.append("판정: ⚠️/❌ 가 하나라도 있으면 해결 전까지 해당 부분 확정 보류.")
    return "\n".join(report)
