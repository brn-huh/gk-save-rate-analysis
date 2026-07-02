"""T0 스파이크 프로브 — 아키텍처 확정 전 선행 게이트.

발급 키(NEXON_API_KEY)로 실제 API를 몇 번 때려서, 설계가 추측으로 잠근
부분을 실측 검증한다. 이 프로브가 부정하면 잠근 수집 아키텍처를 재개방한다.

검증 항목:
  1. /v1/match(matchtype=50) 실재·페이지·offset 상한 (시드 전략 성립)
  2. match-detail 구조가 명세와 일치 (shootDetail x/y/type/result, player spPosition/spGrade)
  3. result==1 이 '키퍼 선방'인가 '수비수 블록 포함'인가
     → shootDetail 카운트 vs 팀집계(effectiveShootTotal/goalTotal) 대조로 추정
  4. GK(spPosition==0)의 spGrade 실제 분포 (8~13 매핑 확인)
  5. spId → 시즌 디코드 (/metadata/seasonid 접두 매칭)

실행:  python -m gksave.cli spike   (또는 gksave spike)
"""

from __future__ import annotations

from typing import Any

from . import api
from .config import DEFAULT, EFFECTIVE_RESULTS, GK_POSITION, RESULT_GOAL, RESULT_SAVE
from .http import ApiError, ResilientClient


def _gk_players(match_info: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in match_info.get("player", []) if p.get("spPosition") == GK_POSITION]


def _probe_feed(client: ResilientClient, report: list[str]) -> str | None:
    report.append("── 1. /v1/match 전역 피드 ──")
    try:
        page0 = api.list_matches(client, offset=0, limit=100)
        report.append(f"  offset=0 limit=100 → {len(page0)}건 반환. 첫 matchId: {page0[0] if page0 else '없음'}")
    except ApiError as e:
        report.append(f"  ❌ 실패: {e} → 시드 전략 재검토 필요")
        return None

    # offset 상한/보존범위 탐침
    for off in (1000, 5000, 10000, 50000):
        try:
            page = api.list_matches(client, offset=off, limit=100)
            report.append(f"  offset={off} → {len(page)}건")
            if not page:
                report.append(f"  ⇒ offset {off} 부근에서 고갈(보존/캡 경계)")
                break
        except ApiError as e:
            report.append(f"  offset={off} → 오류 {e.status} (offset 상한으로 추정)")
            break
    return page0[0] if page0 else None


def _probe_detail_structure(detail: dict[str, Any], report: list[str]) -> None:
    report.append("── 2. match-detail 구조 ──")
    report.append(f"  matchType={detail.get('matchType')}  (공식경기=50 기대)")
    infos = detail.get("matchInfo", [])
    report.append(f"  matchInfo 항목 수: {len(infos)}  (2 기대; ≠2 이면 스킵 대상)")
    if not infos:
        report.append("  ❌ matchInfo 비어 있음")
        return
    sd = infos[0].get("shootDetail", [])
    report.append(f"  shootDetail 샷 수(플레이어0): {len(sd)}")
    if sd:
        keys = set(sd[0].keys())
        for need in ("x", "y", "type", "result", "inPenalty", "assist", "hitPost", "spId", "spGrade"):
            mark = "✅" if need in keys else "❌ 없음"
            report.append(f"    shootDetail.{need}: {mark}")
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
            f"  [플레이어{idx}] shootDetail: 유효(1,3)={eff} 선방(1)={saves} 실점(3)={goals}"
            f"  | 팀집계 effectiveShootTotal={eff_agg} goalTotal={goal_agg}"
        )
        if eff_agg is not None and eff != eff_agg:
            report.append(
                f"    ⚠️ 불일치: shootDetail 유효슛({eff}) ≠ effectiveShootTotal({eff_agg}). "
                f"result==1 정의(블록 포함?)와 공식 단일화 재확인 필요."
            )
        elif eff_agg is not None:
            report.append("    ✅ shootDetail 유효슛 = effectiveShootTotal (카운트 공식 일관)")


def _probe_gk_grade(detail: dict[str, Any], report: list[str]) -> int | None:
    report.append("── 4. GK spGrade 분포 ──")
    sample_sp_id: int | None = None
    for idx, info in enumerate(detail.get("matchInfo", [])):
        gks = _gk_players(info)
        grades = [g.get("spGrade") for g in gks]
        ids = [g.get("spId") for g in gks]
        report.append(f"  [플레이어{idx}] GK 수={len(gks)} spGrade={grades} spId={ids}")
        if gks and sample_sp_id is None:
            sample_sp_id = gks[0].get("spId")
    return sample_sp_id


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
    # spId 앞자리와 일치하는 가장 긴 seasonId 접두 찾기
    best = None
    for s in seasons:
        code = str(s.get("seasonId"))
        if sid.startswith(code) and (best is None or len(code) > len(str(best.get("seasonId")))):
            best = s
    if best:
        report.append(f"  spId={sp_id} → seasonId={best.get('seasonId')} ({best.get('className')})")
    else:
        report.append(f"  spId={sp_id} 접두와 매칭되는 seasonId 없음 → 디코드 규칙 재확인")


def run(settings=DEFAULT) -> str:
    report: list[str] = ["", "===== T0 스파이크 결과 =====", ""]
    with ResilientClient(settings) as client:
        seed_match = _probe_feed(client, report)
        if seed_match:
            try:
                detail = api.get_match_detail(client, seed_match)
            except ApiError as e:
                report.append(f"❌ match-detail 실패: {e}")
                return "\n".join(report)
            _probe_detail_structure(detail, report)
            _probe_result_definition(detail, report)
            sp_id = _probe_gk_grade(detail, report)
            _probe_season_decode(client, sp_id, report)
    report.append("")
    report.append("판정: 위 ⚠️/❌ 가 하나라도 있으면 해당 항목을 해결하기 전까지 수집 아키텍처 확정 보류.")
    return "\n".join(report)
