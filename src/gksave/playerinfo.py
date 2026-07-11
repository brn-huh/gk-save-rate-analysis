"""선수 부가정보(급여·기본OVR·키·몸무게·체형) 수집.

출처는 fc-info.com 의 GK 검색 API. 이 값들은 넥슨 FC 온라인 게임 데이터이고
fc-info 는 집계 사이트다. **우리가 이미 가진 GK spid 만, 카드당 한 번만** 받고
캐시(player_info)에 없는 것만 채운다 — 반복 수집하지 않는다.

per-spid 조회 엔드포인트가 없어(전부 404) GK 목록을 position=20 으로 커서 페이지네이션
하며 한 번 훑고, 우리 spid 에 해당하는 것만 upsert 한다. 페이지 사이 지연으로 예의를 지킨다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import duckdb
import httpx

FCINFO_BASE = "https://fc-info.com"
_SEARCH_PATH = "/local-api/v2/players/search"
_GK_POSITION = 20


@dataclass(frozen=True)
class PlayerInfo:
    spid: int
    name: str | None = None
    salary: int | None = None
    ovr: int | None = None
    height: int | None = None
    weight: int | None = None
    body_type: str | None = None


def parse_player(item: dict[str, Any]) -> PlayerInfo | None:
    """fc-info 검색 응답의 선수 1건 → PlayerInfo. id 없으면 None."""
    spid = item.get("id")
    if spid is None:
        return None
    positions = item.get("positions") or []
    ovr = positions[0].get("ovr") if positions else None
    return PlayerInfo(
        spid=int(spid),
        name=item.get("name"),
        salary=item.get("salary"),
        ovr=ovr,
        height=item.get("height"),
        weight=item.get("weight"),
        body_type=item.get("bodyType"),
    )


def _pid_of(spid: int) -> int:
    """실선수 id = spid 뒤 6자리 (선행 0 은 int 변환으로 자동 제거)."""
    return int(str(spid)[-6:])


def attach_info(con: duckdb.DuckDBPyConnection, leaderboard: list[dict[str, Any]]) -> None:
    """리더보드 각 카드에 c['info'] 를 붙인다.

    급여·기본OVR: 카드(spid) 정확 매칭만.
    키·몸무게·체형: 실선수 속성 → spid 없으면 같은 pid 의 아무 카드 값으로 역채움.
    """
    rows = con.execute(
        "SELECT spid, salary, ovr, height, weight, body_type FROM player_info"
    ).fetchall()
    by_spid = {r[0]: r for r in rows}
    by_pid: dict[int, tuple] = {}
    for r in rows:
        by_pid.setdefault(_pid_of(r[0]), r)   # pid 당 아무거나 하나 (신체는 시즌 무관 동일)

    for c in leaderboard:
        spid = c["gk_sp_id"]
        exact = by_spid.get(spid)
        phys = exact or by_pid.get(_pid_of(spid))
        c["info"] = {
            "salary": exact[1] if exact else None,     # 카드별 → 정확 매칭만
            "ovr": exact[2] if exact else None,
            "height": phys[3] if phys else None,        # 신체 → pid 역채움 허용
            "weight": phys[4] if phys else None,
            "body_type": phys[5] if phys else None,
        }


def _iter_gk_players(
    client: httpx.Client,
    *,
    page_delay: float,
    max_pages: int,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterable[dict[str, Any]]:
    """fc-info GK 목록을 커서 페이지네이션으로 훑는다. 페이지 사이 지연으로 예의."""
    cursor: str | None = None
    for page in range(max_pages):
        if page > 0:
            sleep(page_delay)
        body: dict[str, Any] = {"positionIds": [_GK_POSITION]}
        if cursor:
            body["cursor"] = cursor
        resp = client.post(_SEARCH_PATH, json=body)
        resp.raise_for_status()
        data = resp.json()
        yield from data.get("items", [])
        cursor = data.get("nextCursor")
        if not data.get("hasNext") or not cursor:
            return


def sync_player_info(
    con: duckdb.DuckDBPyConnection,
    *,
    client: httpx.Client | None = None,
    page_delay: float = 1.0,
    max_pages: int = 200,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """우리 gk_match 에 있는 spid 중 player_info 에 없는 것만 fc-info 에서 채운다.

    반환: {'ours','already','fetched','new'} 카운트.
    """
    ours = {r[0] for r in con.execute("SELECT DISTINCT gk_sp_id FROM gk_match").fetchall()}
    have = {r[0] for r in con.execute("SELECT spid FROM player_info").fetchall()}
    need = ours - have
    if not need:
        log(f"player_info: 우리 GK {len(ours):,} 전부 캐시됨 — fc-info 호출 없음")
        return {"ours": len(ours), "already": len(have & ours), "fetched": 0, "new": 0}

    owns_client = client is None
    client = client or httpx.Client(
        base_url=FCINFO_BASE, timeout=20,
        headers={"User-Agent": "gk-save-rate-analysis", "Content-Type": "application/json",
                 "Referer": f"{FCINFO_BASE}/player/search/result?positionIds={_GK_POSITION}",
                 "Origin": FCINFO_BASE},
    )
    new = 0
    seen = 0
    try:
        for item in _iter_gk_players(client, page_delay=page_delay, max_pages=max_pages, sleep=sleep):
            seen += 1
            p = parse_player(item)
            if p is None or p.spid not in need:
                continue
            con.execute(
                "INSERT INTO player_info (spid, name, salary, ovr, height, weight, body_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                [p.spid, p.name, p.salary, p.ovr, p.height, p.weight, p.body_type],
            )
            new += 1
    finally:
        if owns_client:
            client.close()
    log(f"player_info: 우리 GK {len(ours):,} · 이미 {len(have & ours):,} · "
        f"fc-info GK {seen:,} 훑음 · 신규 {new:,}")
    return {"ours": len(ours), "already": len(have & ours), "fetched": seen, "new": new}
