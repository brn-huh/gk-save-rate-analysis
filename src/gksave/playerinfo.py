"""선수 부가정보(급여·기본OVR·키·몸무게·체형) 수집.

출처는 fc-info.com 의 GK 검색 API. 이 값들은 넥슨 FC 온라인 게임 데이터이고
fc-info 는 집계 사이트다. **우리가 이미 가진 GK spid 만, 카드당 한 번만** 받고
캐시(player_info)에 없는 것만 채운다 — 반복 수집하지 않는다.

per-spid 조회 엔드포인트가 없어(전부 404) GK 목록을 position=20 으로 커서 페이지네이션
하며 한 번 훑고, 우리 spid 에 해당하는 것만 upsert 한다. 페이지 사이 지연으로 예의를 지킨다.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import duckdb
import httpx

from .config import NEW_TRAIT_CODES

FCINFO_BASE = "https://fc-info.com"
_SEARCH_PATH = "/local-api/v2/players/search"
_GK_POSITION = 20

# 국가·클럽은 검색 API 에 없고 상세페이지 HTML 에만 있다. per-spid 상세페이지를 받아 파싱한다.
# 정규식은 fc-info 의 Next.js 마크업에 의존 → 클래스 해시가 아니라 안정적 모듈명 접두사로만 매칭.
# nation_code 는 넥슨 CDN 국기 URL 기반이라 가장 견고(1차 키), 국가명·클럽은 부가.
_NAT_CODE = re.compile(r"countries/smallflags/(\d+)\.png")
_NAT_NAME = re.compile(r'alt="nationality"/><span>([^<]+)</span>')
# 클럽 경력 전체는 __next_f 스트리밍 JSON 에 있다(백슬래시 이스케이프). SSR div
# (PlayerClubHistory_year)엔 '더보기' 이전 최근 3개만 렌더되므로 JSON 을 파싱해 전부 가져온다.
# 항목: {"id":N,"club":{"id":M,"name":"클럽","img":..,"league":{..}},"startYear":YYYY,..}
_CLUB = re.compile(
    r'\\"id\\":\d+,\\"club\\":\{\\"id\\":\d+,\\"name\\":\\"([^"\\]+)\\".*?\\"startYear\\":(\d+)'
)
# 특성(트레잇): 상세페이지에만 있고 카드(spid)별로 다르다. 아이콘 URL 의 코드 + alt 의 이름.
_TRAIT = re.compile(r'traits/trait_icon_(\d+)\.png" alt="([^"]+)"')


def parse_traits(html: str) -> list[tuple[int, str]]:
    """상세페이지 HTML → [(trait_code, trait_name), ...] 표기순."""
    return [(int(code), name) for code, name in _TRAIT.findall(html)]


def parse_bio(html: str) -> tuple[int | None, str | None, list[str]]:
    """상세페이지 HTML → (국가코드, 국가명, 클럽명들). 클럽은 최신순(startYear 내림), 중복 제거.

    클럽은 __next_f JSON 에서 전부 파싱하므로 '더보기'로 감춰진 과거 클럽까지 누락 없이 담긴다.
    """
    m_code = _NAT_CODE.search(html)
    m_name = _NAT_NAME.search(html)
    code = int(m_code.group(1)) if m_code else None
    name = m_name.group(1) if m_name else None
    items = _CLUB.findall(html)                          # [(클럽명, startYear), ...]
    items.sort(key=lambda t: int(t[1]), reverse=True)   # 최신 소속부터
    clubs: list[str] = []
    for club, _year in items:
        if club not in clubs:      # 임대 복귀 등으로 같은 클럽이 두 번 → 최신 등장만
            clubs.append(club)
    return code, name, clubs


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


def season_of(spid: int) -> int:
    """시즌 id = spid 앞 3자리 (meta_season.season_id 와 같은 체계)."""
    return int(str(spid)[:3])


def season_img_url(item: dict[str, Any]) -> str | None:
    """fc-info classImg = 넥슨 CDN 의 시즌 엠블럼 URL. 그대로 쓴다(이미지는 넥슨에서 로드)."""
    ci = item.get("classImg")
    return ci or None


def attach_season_img(con: duckdb.DuckDBPyConnection, cards: list[dict[str, Any]]) -> None:
    """각 카드에 c['season_img'] 를 붙인다. season_id(=spid 앞 3자리)로 매칭."""
    img_by = {r[0]: r[1] for r in con.execute("SELECT season_id, img FROM season_img").fetchall()}
    for c in cards:
        c["season_img"] = img_by.get(season_of(c["gk_sp_id"]))


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


def _search_names(client: httpx.Client, names: list[str]) -> list[dict[str, Any]]:
    """이름 배열(≤10)로 GK 검색. fc-info 는 이 선수들의 모든 시즌 카드를 돌려준다."""
    resp = client.post(_SEARCH_PATH, json={"positionIds": [_GK_POSITION], "names": names})
    resp.raise_for_status()
    return resp.json().get("items", [])


def _record_season_img(con: duckdb.DuckDBPyConnection, item: dict[str, Any]) -> None:
    """카드 하나에서 season_id→엠블럼 URL 을 season_img 에 남긴다(이미 있으면 무시)."""
    spid, url = item.get("id"), season_img_url(item)
    if spid is None or not url:
        return
    con.execute(
        "INSERT INTO season_img (season_id, img) VALUES (?, ?) ON CONFLICT DO NOTHING",
        [season_of(spid), url],
    )


def _new_client() -> httpx.Client:
    return httpx.Client(
        base_url=FCINFO_BASE, timeout=20,
        headers={"User-Agent": "gk-save-rate-analysis", "Content-Type": "application/json",
                 "Referer": f"{FCINFO_BASE}/player/search/result?positionIds={_GK_POSITION}",
                 "Origin": FCINFO_BASE},
    )


def sync_season_img(
    con: duckdb.DuckDBPyConnection,
    *,
    client: httpx.Client | None = None,
    page_delay: float = 1.0,
    max_pages: int = 60,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """우리 리더보드 시즌 중 season_img 에 없는 것을 채운다.

    per-season 조회가 없어 GK 목록을 커서로 훑되, 필요한 시즌이 다 채워지면 조기중단.
    시즌은 목록 곳곳에 등장해 몇 페이지면 대부분 덮인다.
    """
    ours = {season_of(r[0]) for r in con.execute("SELECT DISTINCT gk_sp_id FROM gk_match").fetchall()}
    have = {r[0] for r in con.execute("SELECT season_id FROM season_img").fetchall()}
    need = ours - have
    if not need:
        log(f"season_img: 우리 시즌 {len(ours)} 전부 있음 — 호출 없음")
        return {"seasons": len(ours), "need": 0, "new": 0}

    owns = client is None
    client = client or _new_client()
    before = len(have)
    cursor: str | None = None
    try:
        for page in range(max_pages):
            if page > 0:
                sleep(page_delay)
            body: dict[str, Any] = {"positionIds": [_GK_POSITION]}
            if cursor:
                body["cursor"] = cursor
            resp = client.post(_SEARCH_PATH, json=body)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("items", []):
                _record_season_img(con, item)
                if item.get("id"):
                    need.discard(season_of(item["id"]))
            if not need:
                break
            cursor = data.get("nextCursor")
            if not data.get("hasNext") or not cursor:
                break
    finally:
        if owns:
            client.close()
    now = con.execute("SELECT count(*) FROM season_img").fetchone()[0]
    log(f"season_img: 우리 시즌 {len(ours)} · 신규 {now - before} · 미확보 {len(need)}")
    return {"seasons": len(ours), "need": len(need), "new": now - before}


def sync_player_info(
    con: duckdb.DuckDBPyConnection,
    *,
    client: httpx.Client | None = None,
    batch_delay: float = 1.0,
    name_batch: int = 10,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """우리 gk_match 에 있는 spid 중 player_info 에 없는 것만 fc-info 에서 채운다.

    per-spid 조회가 없어(404) 검색 API 의 names 필터(배열, ≤10)로 **우리 선수 이름만**
    콕 집어 조회한다 — 전체 목록을 훑지 않는다. 이름당 여러 시즌 카드가 오므로 그 중
    우리 need 에 든 spid 만 upsert. 배치 사이 지연으로 예의.

    반환: {'ours','already','need','new'} 카운트.
    """
    ours = {r[0] for r in con.execute("SELECT DISTINCT gk_sp_id FROM gk_match").fetchall()}
    have = {r[0] for r in con.execute("SELECT spid FROM player_info").fetchall()}
    need = ours - have
    if not need:
        log(f"player_info: 우리 GK {len(ours):,} 전부 캐시됨 — fc-info 호출 없음")
        return {"ours": len(ours), "already": len(have & ours), "need": 0, "new": 0}

    # need 에 든 spid 의 이름만 추려 fc-info 에 물어본다 (이름으로만 조회 가능).
    name_by_spid = {r[0]: r[1] for r in con.execute(
        "SELECT sp_id, name FROM meta_spid WHERE name IS NOT NULL").fetchall()}
    need_names = sorted({name_by_spid[s] for s in need if s in name_by_spid})

    owns_client = client is None
    client = client or _new_client()
    new = 0
    try:
        for i in range(0, len(need_names), name_batch):
            if i > 0:
                sleep(batch_delay)
            for item in _search_names(client, need_names[i:i + name_batch]):
                _record_season_img(con, item)   # 본 카드마다 시즌 엠블럼도 채운다(무료 부산물)
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
        f"이름 {len(need_names):,}개 조회 · 신규 {new:,}")
    return {"ours": len(ours), "already": len(have & ours), "need": len(need), "new": new}


def _store_bio(con: duckdb.DuckDBPyConnection, pid: int, html: str) -> None:
    """상세 HTML 에서 국가·클럽을 파싱해 player_bio·player_club 에 저장(pid 단위)."""
    code, name, clubs = parse_bio(html)
    con.execute(
        "INSERT INTO player_bio (pid, nation_code, nation_name) VALUES (?, ?, ?) "
        "ON CONFLICT DO NOTHING",
        [pid, code, name],
    )
    con.execute("DELETE FROM player_club WHERE pid = ?", [pid])   # 재수집 시 갱신
    for ord_, club in enumerate(clubs):
        con.execute("INSERT INTO player_club (pid, ord, club_name) VALUES (?, ?, ?)",
                    [pid, ord_, club])


def _store_traits(con: duckdb.DuckDBPyConnection, spid: int, html: str) -> None:
    """상세 HTML 에서 특성을 파싱해 player_trait 에 저장(spid 단위). is_new 는 코드로 판별."""
    con.execute("DELETE FROM player_trait WHERE spid = ?", [spid])   # 재수집 시 갱신
    for ord_, (code, name) in enumerate(parse_traits(html)):
        con.execute(
            "INSERT INTO player_trait (spid, ord, trait_code, trait_name, is_new) "
            "VALUES (?, ?, ?, ?, ?)",
            [spid, ord_, code, name, code in NEW_TRAIT_CODES],
        )


def sync_player_detail(
    con: duckdb.DuckDBPyConnection,
    *,
    client: httpx.Client | None = None,
    delay: float = 1.0,
    limit: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """우리 GK 카드(spid)의 상세를 fc-info 에서 한 번에 받아 특성 + 국가·클럽을 채운다.

    특성은 시즌(카드)별이라 spid 당 1회 받는다. 국가·클럽은 선수(pid) 불변이라 그 상세페이지에서
    부수적으로 뽑아 pid 가 아직 없을 때만 저장한다 — 두 종류를 한 패스(per-spid)로 수집.
    player_trait 에 없는 spid 만(증분), 요청 사이 delay 로 예의. spid 별 커밋이라 중단해도 안전.
    limit 지정 시 그만큼만(소량 시험용). 반환: {'spids','already','need','new','failed','bio_new'}.
    """
    ours = [r[0] for r in con.execute("SELECT DISTINCT gk_sp_id FROM gk_match").fetchall()]
    have = {r[0] for r in con.execute("SELECT DISTINCT spid FROM player_trait").fetchall()}
    have_bio = {r[0] for r in con.execute("SELECT pid FROM player_bio").fetchall()}
    need = [spid for spid in ours if spid not in have]
    # 특성은 있어(need 에서 빠진) 페이지를 안 받는 pid 중, 국가·클럽이 아직 없는 pid 는
    # 대표 spid 하나를 받아 bio 만 채운다(클럽 파싱 개선 후 재수집에도 이 경로가 쓰인다).
    need_set = set(need)
    seen_pid: set[int] = set()
    for spid in ours:
        pid = _pid_of(spid)
        if pid not in have_bio and spid not in need_set and pid not in seen_pid:
            seen_pid.add(pid)
            need.append(spid)
    if limit is not None:
        need = need[:limit]
    if not need:
        log(f"player_detail: 우리 카드 {len(ours):,} 전부 캐시됨 — fc-info 호출 없음")
        return {"spids": len(ours), "already": len(have & set(ours)),
                "need": 0, "new": 0, "failed": 0, "bio_new": 0}

    owns = client is None
    client = client or _new_client()
    new = failed = bio_new = 0
    try:
        for i, spid in enumerate(need):
            if i > 0:
                sleep(delay)
            try:
                resp = client.get(f"/player/{spid}?grade=1")
                resp.raise_for_status()
                html = resp.text
            except Exception as e:   # 개별 실패는 건너뛰고 다음 실행에서 재시도(증분)
                failed += 1
                log(f"  spid {spid} 실패: {type(e).__name__}: {e}")
                continue
            if spid not in have:              # 특성 미보유 카드만 저장(bio 재방문은 특성 스킵)
                _store_traits(con, spid, html)
                new += 1
            pid = _pid_of(spid)
            if pid not in have_bio:            # 국가·클럽은 pid 당 1회만
                _store_bio(con, pid, html)
                have_bio.add(pid); bio_new += 1
    finally:
        if owns:
            client.close()
    remaining = len(ours) - len(have) - new   # 아직 특성 없는 spid(실패 + limit 밖 포함)
    log(f"player_detail: 우리 카드 {len(ours):,} · 이미 {len(have):,} · 신규 {new:,} · "
        f"국가·클럽 신규 {bio_new:,} · 실패 {failed:,} · 미확보 {remaining:,}")
    return {"spids": len(ours), "already": len(have & set(ours)),
            "need": len(need), "new": new, "failed": failed, "bio_new": bio_new}


def attach_bio(con: duckdb.DuckDBPyConnection, leaderboard: list[dict[str, Any]]) -> None:
    """리더보드 각 카드에 c['bio'] = {nation_code, nation_name, clubs} 를 붙인다(pid 매칭).

    국가·클럽은 pid(실선수) 단위 → 같은 pid 의 모든 시즌·강화 카드가 같은 값을 공유한다.
    bio 없는 pid 는 c['bio'] = None.
    """
    bios = {r[0]: (r[1], r[2]) for r in
            con.execute("SELECT pid, nation_code, nation_name FROM player_bio").fetchall()}
    clubs_by_pid: dict[int, list[str]] = {}
    for pid, club in con.execute(
        "SELECT pid, club_name FROM player_club ORDER BY pid, ord"
    ).fetchall():
        clubs_by_pid.setdefault(pid, []).append(club)
    for c in leaderboard:
        pid = _pid_of(c["gk_sp_id"])
        nat = bios.get(pid)
        if nat is None and pid not in clubs_by_pid:
            c["bio"] = None
            continue
        c["bio"] = {
            "nation_code": nat[0] if nat else None,
            "nation_name": nat[1] if nat else None,
            "clubs": clubs_by_pid.get(pid, []),
        }


def attach_trait(con: duckdb.DuckDBPyConnection, cards: list[dict[str, Any]]) -> None:
    """각 카드에 c['traits'] = [{code, name, is_new}, ...] 를 붙인다(spid 정확 매칭).

    특성은 카드(spid)별 → 시즌·강화 다르면 특성도 다르다. 없으면 c['traits'] = [].
    """
    by_spid: dict[int, list[dict[str, Any]]] = {}
    for spid, code, name, is_new in con.execute(
        "SELECT spid, trait_code, trait_name, is_new FROM player_trait ORDER BY spid, ord"
    ).fetchall():
        by_spid.setdefault(spid, []).append(
            {"code": code, "name": name, "is_new": bool(is_new)}
        )
    for c in cards:
        c["traits"] = by_spid.get(c["gk_sp_id"], [])
