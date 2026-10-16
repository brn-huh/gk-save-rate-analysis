"""공식 데이터센터와 fc-info의 선수 부가정보 수집.

GK와 선택 시 센터백 카드 ID만 조회하며 기존 캐시는 보존한다.
playerinfo는 fc-info만 사용하며 요청 간격은 기본 3초(최소 1초)다.
특성·국가·클럽은 공식 데이터센터에서 조회한다.
"""

from __future__ import annotations

import re
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Callable

import duckdb
import httpx

from .config import FC_RECHECK_DAYS, NEW_TRAIT_CODES
from .progress import log as _log

DATACENTER_BASE = "https://fconline.nexon.com"
FCINFO_BASE = "https://fc-info.com"
_ABILITY_PATH = "/datacenter/PlayerAbility"
_INFO_PATH = "/DataCenter/PlayerInfo"
_TRAIT = re.compile(r'traits/trait_icon_(\d+)\.png" alt="([^"]+)"')


def _element_html(html: str, classes: str) -> str | None:
    checks = "".join(r"(?=[^\"']*(?<![\w-])" + re.escape(c) + r"(?![\w-]))" for c in classes.split())
    pattern = r"<(?P<tag>div|span)\b[^>]*\bclass=[\"']" + checks + r"[^\"']*[\"'][^>]*>(.*?)</(?P=tag)>"
    match = re.search(pattern, html, re.S)
    return match.group(2) if match else None


def _text(html: str | None) -> str | None:
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", html)).split()) if html is not None else None


def parse_traits(html: str) -> list[tuple[int, str]]:
    """공식 데이터센터 특성 아이콘에서 코드와 이름을 읽는다."""
    return list(dict.fromkeys((int(code), unescape(name)) for code, name in _TRAIT.findall(html)))


def parse_bio(html: str) -> tuple[int | None, str | None, list[str]]:
    """공식 능력치의 국적과 상세페이지의 클럽 경력(최신순)을 읽는다."""
    nation = _element_html(html, "etc nation") or ""
    code = re.search(r"countries/smallflags/(\d+)\.png", nation)
    name = _text(_element_html(nation, "txt"))
    clubs = [_text(c) for c in re.findall(r'<div\b[^>]*class="td club"[^>]*>(.*?)</div>', html, re.S)]
    return int(code[1]) if code else None, name, list(dict.fromkeys(c for c in clubs if c))


@dataclass(frozen=True)
class PlayerInfo:
    spid: int
    name: str | None = None
    salary: int | None = None
    ovr: int | None = None
    height: int | None = None
    weight: int | None = None
    body_type: str | None = None


def parse_player(html: str, spid: int) -> PlayerInfo | None:
    """1강·적응도 0 응답의 카드 ID와 기본 필드를 확인한다."""
    try:
        return _parse_player(html, spid)
    except ValueError:
        return None


def _parse_player(html: str, spid: int) -> PlayerInfo:
    identity = re.search(r"var\s+playerData\s*=\s*\{\s*spid:\s*(\d+),.*?strong:\s*(\d+)\s*\}", html, re.S)
    if not identity:
        raise ValueError("playerData.spid/strong 식별 정보 누락 또는 형식 변경")
    if int(identity[1]) != spid:
        raise ValueError(f"카드 ID 불일치: 요청 {spid}, 응답 {identity[1]}")
    if int(identity[2]) != 1:
        raise ValueError(f"강화 단계 불일치: 요청 1강, 응답 {identity[2]}강")
    def number(classes: str) -> int | None:
        text = _text(_element_html(html, classes))
        match = re.fullmatch(r"(\d+)(?:cm|kg)?", text or "")
        return int(match[1]) if match else None
    name = _text(_element_html(html, "name"))
    salary, ovr = number("pay_side"), number("ovr value")
    invalid = [label for label, valid in (("선수명(name)", bool(name)),
               ("급여(pay_side)", salary is not None), ("기본 OVR(ovr value)", ovr is not None)) if not valid]
    if invalid:
        raise ValueError("필수 항목 누락 또는 잘못된 값: " + ", ".join(invalid))
    return PlayerInfo(spid=spid, name=name, salary=salary, ovr=ovr,
                      height=number("etc height"), weight=number("etc weight"),
                      body_type=_text(_element_html(html, "etc physical")))


def _pid_of(spid: int) -> int:
    """실선수 id = spid 뒤 6자리 (선행 0 은 int 변환으로 자동 제거)."""
    return int(str(spid)[-6:])


def season_of(spid: int) -> int:
    """시즌 id = spid 앞 3자리 (meta_season.season_id 와 같은 체계)."""
    return int(str(spid)[:3])


def season_img_url(html: str) -> str | None:
    season = _element_html(html, "season") or ""
    match = re.search(r'<img\b[^>]*src="(https://ssl\.nexon\.com/[^"<>]+)"', season)
    return unescape(match[1]) if match else None


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


def _fetched_recently(con: duckdb.DuckDBPyConnection, kind: str, *, source: str = "nexon") -> set[int]:
    """FC_RECHECK_DAYS 안에 조회해 본 spid. 결과가 비어 있어도 다시 안 받게 하는 게 목적."""
    return {r[0] for r in con.execute(
        "SELECT spid FROM fc_fetch_log WHERE kind = ? "
        "AND fetched_at > now() - INTERVAL (?) DAY", [source + "-" + kind, FC_RECHECK_DAYS]).fetchall()}


def _record_fetch(con: duckdb.DuckDBPyConnection, kind: str, spids: Iterable[int], *, source: str = "nexon") -> None:
    """조회 성공을 기록. 재조회 때 시각이 갱신되도록 upsert."""
    for spid in spids:
        con.execute(
            "INSERT INTO fc_fetch_log (kind, spid, fetched_at) VALUES (?, ?, now()) "
            "ON CONFLICT (kind, spid) DO UPDATE SET fetched_at = now()", [source + "-" + kind, spid])


def _new_client() -> httpx.Client:
    return httpx.Client(base_url=DATACENTER_BASE, timeout=30, follow_redirects=True,
                        headers={"User-Agent": "gk-save-rate-analysis", "Referer": DATACENTER_BASE + "/datacenter"})


def fcinfo_client() -> httpx.Client:
    return httpx.Client(base_url=FCINFO_BASE, timeout=30, follow_redirects=True,
                        headers={"User-Agent": "gk-save-rate-analysis", "Referer": FCINFO_BASE + "/player/search/result"})


def _safe_error_text(message: str) -> str:
    def strip_url(match):
        try:
            return str(httpx.URL(match[0]).copy_with(username="", password="", query=None, fragment=""))
        except (httpx.InvalidURL, ValueError):
            return "<URL 생략>"
    message = re.sub(r'https?://[^\s<>"\']+', strip_url, message)
    message = re.sub(r'''(?i)\b(ouid|api[_-]?key|token|authorization|cookie|password|secret)\b["']?\s*[:=]\s*["']?(?:Bearer\s+|Basic\s+)?[^\s,;"']+''', r'\1=<REDACTED>', message)
    return " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", message).split())[:300]


class ResponseValidationError(ValueError):
    def __init__(self, message: str, response: httpx.Response):
        super().__init__(message)
        self.response = response
        self.request = response.request


def describe_error(error: BaseException) -> str:
    """요청 헤더·쿼리·응답 본문을 노출하지 않고 실제 상태와 예외 원인을 표시한다."""
    parts = []
    try:
        request = error.request if isinstance(error, (httpx.HTTPError, ResponseValidationError)) else None
    except RuntimeError:
        request = None
    if isinstance(error, (httpx.HTTPStatusError, ResponseValidationError)):
        response = error.response
        parts.append(f"HTTP {response.status_code} {_safe_error_text(response.reason_phrase)}")
    elif isinstance(error, httpx.TransportError):
        status = request.extensions.get("datacenter_http_status") if request else None
        parts.append(f"HTTP {status} (헤더 수신 후 통신 오류)" if status is not None else "HTTP 상태: 미확인 (상태 코드를 받지 못함)")
    if request is not None:
        parts.append(f"요청: {request.method} {request.url.path}")
    if isinstance(error, ResponseValidationError):
        response = error.response
        parts.append(f"Content-Type: {_safe_error_text(response.headers.get('Content-Type', '미지정'))}")
        parts.append(f"응답 크기: {len(response.content):,} bytes")
        title = re.search(r"<title\b[^>]*>(.*?)</title>", response.text, re.I | re.S)
        parts.append(f"응답 제목: {_safe_error_text(_text(title[1]) or '') if title else '없음'}")
    cause = error
    seen = set()
    while cause is not None and id(cause) not in seen and len(seen) < 4:
        seen.add(id(cause))
        message = _safe_error_text(str(cause))
        parts.append(f"{type(cause).__name__}: {message or '원본 메시지 없음'}")
        cause = cause.__cause__ or cause.__context__
    return " · ".join(parts)


def _request(client: httpx.Client, method: str, path: str, *, delay: float,
             sleep: Callable[[float], None], log: Callable[[str], None], **kwargs) -> httpx.Response:
    source = "fc-info" if client.base_url.host == "fc-info.com" else "공식 데이터센터"
    for attempt in range(4):
        sleep(max(1.0, delay))
        response = None
        try:
            with client.stream(method, path, **kwargs) as response:
                if response.status_code != 429 or attempt == 3:
                    response.raise_for_status()
                    response.read()
                    return response
        except httpx.TransportError as error:
            if response is not None:
                error.request.extensions["datacenter_http_status"] = response.status_code
            if attempt == 3:
                raise
            cooldown = 5.0 * 2 ** attempt
            log(f"{source} 통신 오류 — {describe_error(error)} — {cooldown:.0f}초 대기 후 재시도 ({attempt + 1}/3)")
            sleep(cooldown)
            continue
        cooldown = 60.0 * 2 ** attempt
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                seconds = float(retry_after)
                if math.isfinite(seconds):
                    cooldown = max(cooldown, seconds)
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    cooldown = max(cooldown, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        log(f"{source} 요청 제한 — HTTP {response.status_code} {_safe_error_text(response.reason_phrase)} · 요청: {method} {path} — {cooldown:.0f}초 대기 후 재시도 ({attempt + 1}/3)")
        sleep(cooldown)
    raise AssertionError("unreachable")


def _ability(client, spid, *, delay, sleep, log) -> str:
    response = _request(client, "POST", _ABILITY_PATH, delay=delay, sleep=sleep, log=log,
                    data={"spid": spid, "n1Strong": 1, "n1Grow": 0,
                          "n4TeamColorId": 0, "n4TeamColorLv": 0,
                          "n4TeamColorId_Enhance": 0, "n4TeamColorLv_Enhance": 0,
                          "n4TeamColorId_Feature": 0, "n1Change": 0})
    try:
        _parse_player(response.text, spid)
    except ValueError as error:
        raise ResponseValidationError(f"공식 데이터센터 선수 정보 검증 실패 (요청 카드 {spid})", response) from error
    return response.text


def _detail(client, spid, *, delay, sleep, log) -> str:
    ability = _ability(client, spid, delay=delay, sleep=sleep, log=log)
    page = _request(client, "GET", _INFO_PATH, delay=delay, sleep=sleep, log=log,
                    params={"spid": spid, "n1Strong": 1})
    if "data_detail_club" not in page.text:
        raise ResponseValidationError("공식 데이터센터 클럽 경력 영역(data_detail_club) 누락", page)
    return ability + page.text


def _our_cards(con: duckdb.DuckDBPyConnection, include_defenders: bool = False) -> set[int]:
    ours = {r[0] for r in con.execute("SELECT DISTINCT gk_sp_id FROM gk_match").fetchall()}
    if include_defenders:
        ours.update(r[0] for r in con.execute(
            "SELECT cb1_sp_id FROM defense_match UNION SELECT cb2_sp_id FROM defense_match"
        ).fetchall() if r[0] is not None)
    return ours


def _fcinfo_cards(client, name, *, delay, sleep, log) -> dict[int, tuple[PlayerInfo, str | None]]:
    """이름 검색 전체 페이지를 재사용하되 카드 ID·1강·기본 능력치를 검증한다."""
    import json

    cards, cursors = {}, set()
    cursor = None
    while True:
        body = {"names": [name]}
        if cursor:
            body["cursor"] = cursor
        response = _request(client, "POST", "/local-api/v2/players/search",
                        delay=delay, sleep=sleep, log=log, json=body)
        try:
            data = json.loads(response.text)
        except ValueError:
            raise ResponseValidationError("fc-info 검색 응답이 JSON이 아닙니다", response) from None
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ResponseValidationError("fc-info 검색 응답의 items 배열 누락 또는 형식 변경", response)
        for item in data["items"]:
            if not isinstance(item, dict) or item.get("grade") != 1:
                continue
            positions = item.get("positions")
            if not isinstance(positions, list):
                continue
            ovr = positions[0].get("ovr") if positions and isinstance(positions[0], dict) else None
            if not all(type(v) is int and v > 0 for v in (item.get("id"), item.get("salary"), ovr)) or not isinstance(item.get("name"), str):
                continue
            p = PlayerInfo(item["id"], item["name"], item["salary"], ovr,
                           item.get("height") if type(item.get("height")) is int else None,
                           item.get("weight") if type(item.get("weight")) is int else None,
                           item.get("bodyType") if isinstance(item.get("bodyType"), str) else None)
            url = item.get("classImg")
            cards[p.spid] = (p, url if isinstance(url, str) and url.startswith("https://ssl.nexon.com/") else None)
        if not data.get("hasNext"):
            return cards
        cursor = data.get("nextCursor")
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ResponseValidationError("fc-info 검색 페이지 커서가 누락되거나 반복됩니다", response)
        cursors.add(cursor)


def sync_season_img(
    con: duckdb.DuckDBPyConnection, *, client: httpx.Client | None = None, page_delay: float = 3.0,
    include_defenders: bool = False, sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = _log,
) -> dict[str, int]:
    names = dict(con.execute("SELECT sp_id,name FROM meta_spid WHERE name IS NOT NULL").fetchall())
    representatives = {}
    for spid in sorted(_our_cards(con, include_defenders)):
        if names.get(spid):
            representatives.setdefault(season_of(spid), spid)
    have = {r[0] for r in con.execute("SELECT season_id FROM season_img").fetchall()}
    need = set(representatives) - have - _fetched_recently(con, "season", source="fc-info")
    if not need:
        log("season_img: 조회할 시즌 없음 — fc-info 호출 없음")
        return {"seasons": len(representatives), "need": 0, "new": 0}
    owns = client is None
    client = client or fcinfo_client()
    new, catalog = 0, {}
    try:
        for sid in sorted(need):
            spid = representatives[sid]
            name = names[spid]
            if name not in catalog:
                catalog[name] = _fcinfo_cards(client, name, delay=page_delay, sleep=sleep, log=log)
            result = catalog[name].get(spid)
            if result and result[1]:
                con.execute("INSERT INTO season_img (season_id,img) VALUES (?,?) ON CONFLICT DO NOTHING", [sid, result[1]])
                new += 1
            _record_fetch(con, "season", [sid], source="fc-info")
    finally:
        if owns:
            client.close()
    log(f"season_img: 우리 시즌 {len(representatives)} · 신규 {new} · 미확보 {len(need) - new}")
    return {"seasons": len(representatives), "need": len(need) - new, "new": new}


def sync_player_info(
    con: duckdb.DuckDBPyConnection, *, client: httpx.Client | None = None, batch_delay: float = 3.0,
    retry_missing: bool = False, include_defenders: bool = False,
    sleep: Callable[[float], None] = time.sleep, log: Callable[[str], None] = _log,
) -> dict[str, int]:
    ours = _our_cards(con, include_defenders)
    have = {r[0] for r in con.execute("SELECT spid FROM player_info").fetchall()}
    recent = set() if retry_missing else _fetched_recently(con, "info", source="fc-info")
    need = sorted(ours - have - recent)
    if not need:
        log(f"player_info: 우리 카드 {len(ours):,} 전부 캐시됨 — 외부 호출 없음")
        return {"ours": len(ours), "already": len(have & ours), "need": 0, "new": 0}
    names = dict(con.execute("SELECT sp_id,name FROM meta_spid WHERE name IS NOT NULL").fetchall())
    owns = client is None
    client = client or fcinfo_client()
    new, catalog = 0, {}
    log(f"player_info: fc-info에서 미수집 카드 {len(need):,}장 조회 시작 (요청 간 최소 {max(1.0, batch_delay):g}초)")
    try:
        for i, spid in enumerate(need):
            name = names.get(spid)
            if not name:
                log(f"  카드 {spid}: 넥슨 메타 캐시에 이름 매핑 없음 — 이름이 필요한 fc-info 검색 건너뜀 (메타 갱신 후에도 공식 목록에 없으면 동일)")
                continue
            if name not in catalog:
                catalog[name] = _fcinfo_cards(client, name, delay=batch_delay, sleep=sleep, log=log)
            result = catalog[name].get(spid)
            if result is None:
                _record_fetch(con, "info", [spid], source="fc-info")
                log(f"  카드 {spid}: fc-info에 없음")
                continue
            p, url = result
            con.execute("INSERT INTO player_info (spid,name,salary,ovr,height,weight,body_type) "
                        "VALUES (?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                        [p.spid,p.name,p.salary,p.ovr,p.height,p.weight,p.body_type])
            if url:
                con.execute("INSERT INTO season_img (season_id,img) VALUES (?,?) ON CONFLICT DO NOTHING", [season_of(spid), url])
            _record_fetch(con, "info", [spid], source="fc-info")
            new += 1
            if (i + 1) % 20 == 0 or i + 1 == len(need):
                log(f"player_info: 조회 {i+1:,}/{len(need):,} · 신규 {new:,} (fc-info)")
    finally:
        if owns:
            client.close()
    log(f"player_info: 우리 카드 {len(ours):,} · 이미 {len(have & ours):,} · 신규 {new:,}")
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
    delay: float = 2.0,
    limit: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = _log,
) -> dict[str, int]:
    """우리 GK 카드(spid)의 상세를 공식 데이터센터에서 능력치와 상세페이지를 받아 특성 + 국가·클럽을 채운다.

    특성은 시즌(카드)별이라 spid 당 1회 받는다. 국가·클럽은 선수(pid) 불변이라 그 상세페이지에서
    부수적으로 뽑아 pid 가 아직 없을 때만 저장한다 — 두 종류를 한 패스(per-spid)로 수집.
    player_trait 에 없는 spid 만(증분), 요청 사이 delay 로 예의. spid 별 커밋이라 중단해도 안전.
    limit 지정 시 그만큼만(소량 시험용). 반환: {'spids','already','need','new','failed','bio_new'}.
    """
    log("player_detail: 조회할 카드·캐시 확인 중")
    ours = [r[0] for r in con.execute("SELECT DISTINCT gk_sp_id FROM gk_match").fetchall()]
    have = {r[0] for r in con.execute("SELECT DISTINCT spid FROM player_trait").fetchall()}
    have_bio = {r[0] for r in con.execute("SELECT pid FROM player_bio").fetchall()}
    # 특성이 0개인 카드는 받아도 player_trait 에 행이 안 남는다 → 조회 기록으로도 걸러야
    # 매 실행마다 같은 상세페이지를 다시 받지 않는다.
    seen = _fetched_recently(con, "detail")
    need = [spid for spid in ours if spid not in have and spid not in seen]
    # 특성은 있어(need 에서 빠진) 페이지를 안 받는 pid 중, 국가·클럽이 아직 없는 pid 는
    # 대표 spid 하나를 받아 bio 만 채운다(클럽 파싱 개선 후 재수집에도 이 경로가 쓰인다).
    need_set = set(need)
    seen_pid: set[int] = set()
    for spid in ours:
        pid = _pid_of(spid)
        if spid in seen:            # 최근에 이 페이지를 봤으면 bio 때문에 다시 받지 않는다
            continue
        if pid not in have_bio and spid not in need_set and pid not in seen_pid:
            seen_pid.add(pid)
            need.append(spid)
    if limit is not None:
        need = need[:limit]
    if not need:
        log(f"player_detail: 우리 카드 {len(ours):,} 전부 캐시됨 — 공식 데이터센터 호출 없음")
        return {"spids": len(ours), "already": len(have & set(ours)),
                "need": 0, "new": 0, "failed": 0, "bio_new": 0}

    owns = client is None
    client = client or _new_client()
    new = failed = bio_new = 0
    log(f"player_detail: 공식 데이터센터 조회 시작 · 대상 {len(need):,}장")
    try:
        for i, spid in enumerate(need):
            log(f"player_detail: {i+1:,}/{len(need):,} 카드 {spid} 조회 중 · 신규 {new:,} · 실패 {failed:,}")
            try:
                html = _detail(client, spid, delay=delay, sleep=sleep, log=log)
            except Exception as e:   # 개별 실패는 건너뛰고 다음 실행에서 재시도(증분)
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                    raise
                failed += 1
                log(f"  spid {spid} 실패: {describe_error(e)}")
                continue
            if spid not in have:              # 특성 미보유 카드만 저장(bio 재방문은 특성 스킵)
                _store_traits(con, spid, html)
                new += 1
            pid = _pid_of(spid)
            if pid not in have_bio:            # 국가·클럽은 pid 당 1회만
                _store_bio(con, pid, html)
                have_bio.add(pid); bio_new += 1
            # 성공한 조회만 기록한다 — 실패는 남기지 않아 다음 실행에서 재시도된다.
            _record_fetch(con, "detail", [spid])
            log(f"player_detail: {i+1:,}/{len(need):,} 완료 · 신규 {new:,} · 국가·클럽 신규 {bio_new:,} · 실패 {failed:,}")
    finally:
        if owns:
            client.close()
    remaining = len(ours) - len(have) - new   # 아직 특성 없는 spid(실패 + limit 밖 포함)
    log(f"player_detail: 우리 카드 {len(ours):,} · 이미 {len(have):,} · 신규 {new:,} · "
        f"국가·클럽 신규 {bio_new:,} · 실패 {failed:,} · 미확보 {remaining:,}")
    return {"spids": len(ours), "already": len(have & set(ours)),
            "need": len(need), "new": new, "failed": failed, "bio_new": bio_new}


def resync_bio(
    con: duckdb.DuckDBPyConnection,
    *,
    client: httpx.Client | None = None,
    delay: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = _log,
) -> dict[str, int]:
    """모든 우리 카드 spid 페이지를 받아 선수(pid)별 국가 다수결·최다 클럽으로 재작성.

    선수별로 여러 시즌 카드의 국적 응답을 모아 기존 캐시를 교정한다.
    pid 당 여러 시즌 카드의 국가를 모아 최빈값을 쓰면 개별 카드 오류에 휘둘리지 않는다.
    클럽은 시즌 간 같아야 하나 카드별 렌더 편차가 있어 가장 많이 담긴 카드를 채택한다.
    전량 조회(증분 아님) 뒤 한 번에 재작성하므로, 중간 중단 시 DB 는 이전 상태를 유지한다.
    """
    from collections import Counter
    ours = [r[0] for r in con.execute("SELECT DISTINCT gk_sp_id FROM gk_match").fetchall()]
    votes: dict[int, Counter] = {}          # pid -> Counter({nation_code: 표수})
    nation_name: dict[int, str | None] = {}  # nation_code -> 국가명(대표)
    best_clubs: dict[int, list[str]] = {}    # pid -> 가장 많이 담긴 클럽 목록
    owns = client is None
    client = client or _new_client()
    got = failed = 0
    try:
        for i, spid in enumerate(ours):
            try:
                html = _detail(client, spid, delay=delay, sleep=sleep, log=log)
            except Exception as e:
                if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 429:
                    raise
                failed += 1
                log(f"  spid {spid} 실패: {describe_error(e)}")
                continue
            got += 1
            code, name, clubs = parse_bio(html)
            pid = _pid_of(spid)
            if code is not None:
                votes.setdefault(pid, Counter())[code] += 1
                nation_name[code] = name
            if len(clubs) > len(best_clubs.get(pid, [])):
                best_clubs[pid] = clubs
    finally:
        if owns:
            client.close()
    if failed:
        raise ValueError("국가·클럽 재수집 실패가 있어 기존 캐시를 보존합니다")
    # 다수결 결과로 player_bio·player_club 을 전면 재작성(부분 조회로 덮어쓰지 않도록 마지막에)
    con.execute("DELETE FROM player_bio")
    con.execute("DELETE FROM player_club")
    for pid, cnt in votes.items():
        code = cnt.most_common(1)[0][0]
        con.execute("INSERT INTO player_bio (pid, nation_code, nation_name) VALUES (?, ?, ?)",
                    [pid, code, nation_name.get(code)])
    for pid, clubs in best_clubs.items():
        for ord_, club in enumerate(clubs):
            con.execute("INSERT INTO player_club (pid, ord, club_name) VALUES (?, ?, ?)",
                        [pid, ord_, club])
    split = sum(1 for cnt in votes.values() if len(cnt) > 1)
    log(f"resync_bio: spid {got:,} 조회 · 실패 {failed:,} · 선수(pid) {len(votes):,} 확정 "
        f"(카드 간 국적 불일치 {split:,}명 다수결)")
    return {"got": got, "failed": failed, "pids": len(votes), "split": split}


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
