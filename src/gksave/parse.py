"""파서 (T4) — 원본 match-detail → GK 출전 + 슛 단위 행.

핵심 규칙(설계 문서 Constraints/Validity):
  · GK = 우리 팀 player 중 spPosition==0. 정확히 1명이 아니면(0명·교체로 2명)
    그 경기는 슛 귀속 불가 → 스킵(카운트 로깅).
  · 우리 GK가 마주한 슛 = 상대 matchInfo 의 shootDetail.
  · 유효슛만: result==1(선방)·3(실점). result==2(offtarget)는 분모에서 제외.
  · PK(type==9)는 행으로 남기되 is_pk 플래그 → 헤드라인 집계에서 제외.
  · 자책골은 상대 shootDetail 에 안 나타나므로(내 슛) 자동 제외됨.
  · GK spGrade 가 8~13 밖이면 그 카드는 분석 대상 아님 → 스킵.

parse_match 는 GK 출전(appearances)과 슛(shots)을 함께 낸다. 출전을 따로
집계해야 '슛 0개 마주한 경기'도 표본 경기수 게이트에 정확히 반영된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import (
    EFFECTIVE_RESULTS,
    GK_POSITION,
    SHOT_TYPE_PENALTY,
    SPGRADE_MAX,
    SPGRADE_MIN,
)


@dataclass(frozen=True)
class Appearance:
    match_id: str
    gk_ouid: str
    gk_sp_id: int
    gk_sp_grade: int


@dataclass(frozen=True)
class ShotRow:
    match_id: str
    gk_ouid: str
    gk_sp_id: int
    gk_sp_grade: int
    shot_type: int
    result: int
    is_pk: bool
    in_penalty: bool
    assist: bool
    hit_post: bool
    x: float | None
    y: float | None


@dataclass
class ParseStats:
    matches: int = 0
    skipped_not_two_teams: int = 0
    skipped_no_single_gk: int = 0
    skipped_grade_out_of_range: int = 0
    appearances: int = 0
    shots: int = 0


def extract_gk(match_info: dict[str, Any]) -> dict[str, Any] | None:
    """spPosition==0 인 선수가 정확히 1명일 때만 그 GK를 반환. 아니면 None."""
    gks = [p for p in match_info.get("player", []) if p.get("spPosition") == GK_POSITION]
    if len(gks) != 1:
        return None
    return gks[0]


def parse_match(
    detail: dict[str, Any], stats: ParseStats | None = None
) -> tuple[list[Appearance], list[ShotRow]]:
    """한 매치를 (GK 출전 목록, 슛 목록)으로 파싱."""
    st = stats if stats is not None else ParseStats()
    appearances: list[Appearance] = []
    shots: list[ShotRow] = []

    infos = detail.get("matchInfo", [])
    match_id = detail.get("matchId", "")
    if len(infos) != 2:
        st.skipped_not_two_teams += 1
        return appearances, shots
    st.matches += 1

    for me, opp in ((infos[0], infos[1]), (infos[1], infos[0])):
        gk = extract_gk(me)
        if gk is None:
            st.skipped_no_single_gk += 1
            continue
        grade = gk.get("spGrade")
        if grade is None or not (SPGRADE_MIN <= grade <= SPGRADE_MAX):
            st.skipped_grade_out_of_range += 1
            continue

        gk_ouid = me.get("ouid", "")
        gk_sp_id = gk.get("spId")
        appearances.append(Appearance(match_id, gk_ouid, gk_sp_id, grade))
        st.appearances += 1

        for s in opp.get("shootDetail", []):
            result = s.get("result")
            if result not in EFFECTIVE_RESULTS:
                continue
            stype = s.get("type")
            st.shots += 1
            shots.append(
                ShotRow(
                    match_id=match_id,
                    gk_ouid=gk_ouid,
                    gk_sp_id=gk_sp_id,
                    gk_sp_grade=grade,
                    shot_type=stype,
                    result=result,
                    is_pk=(stype == SHOT_TYPE_PENALTY),
                    in_penalty=bool(s.get("inPenalty", False)),
                    assist=bool(s.get("assist", False)),
                    hit_post=bool(s.get("hitPost", False)),
                    x=s.get("x"),
                    y=s.get("y"),
                )
            )
    return appearances, shots


def iter_shots(detail: dict[str, Any]) -> list[ShotRow]:
    """테스트 편의: 슛 목록만 반환."""
    return parse_match(detail)[1]
