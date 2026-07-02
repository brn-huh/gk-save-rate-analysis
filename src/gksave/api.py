"""넥슨 Open API 엔드포인트 얇은 래퍼.

경로/파라미터는 reference/api-*.yaml 스펙에서 확인한 값이다.
  /fconline/v1/match          matchtype로 전역 최근순 matchId 목록 (시드)
  /fconline/v1/match-detail   matchid로 매치 상세
  /fconline/v1/id             nickname → ouid
  /fconline/v1/user/match     ouid+matchtype로 유저별 matchId 목록 (스노우볼)
  /fconline/v1/metadata/*     코드 매핑 (matchtype/spid/seasonid/spposition)
"""

from __future__ import annotations

from typing import Any

from .config import MATCHTYPE_OFFICIAL
from .http import ResilientClient


def list_matches(
    client: ResilientClient,
    *,
    matchtype: int = MATCHTYPE_OFFICIAL,
    offset: int = 0,
    limit: int = 100,
    orderby: str = "desc",
) -> list[str]:
    """전역 최근순 matchId 목록 (시드). 최대 limit 100."""
    return client.get(
        "/fconline/v1/match",
        {"matchtype": matchtype, "offset": offset, "limit": limit, "orderby": orderby},
    )


def get_match_detail(client: ResilientClient, matchid: str) -> dict[str, Any]:
    return client.get("/fconline/v1/match-detail", {"matchid": matchid})


def get_ouid(client: ResilientClient, nickname: str) -> str:
    """닉네임 → ouid. 랭커 닉네임을 씨앗 ouid로 바꿀 때 사용."""
    return client.get("/fconline/v1/id", {"nickname": nickname})["ouid"]


def list_user_matches(
    client: ResilientClient,
    ouid: str,
    *,
    matchtype: int = MATCHTYPE_OFFICIAL,
    offset: int = 0,
    limit: int = 100,
) -> list[str]:
    """유저별 matchId 목록 (스노우볼 확장). 최대 limit 100."""
    return client.get(
        "/fconline/v1/user/match",
        {"ouid": ouid, "matchtype": matchtype, "offset": offset, "limit": limit},
    )


def get_metadata(client: ResilientClient, kind: str) -> list[dict[str, Any]]:
    """kind ∈ {matchtype, spid, seasonid, spposition, division}."""
    return client.get(f"/fconline/v1/metadata/{kind}")
