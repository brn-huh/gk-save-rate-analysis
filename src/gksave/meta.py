"""메타데이터 (선수명·시즌) enrich.

정적 파일 /static/fconline/meta/{spid,seasonid}.json 을 한 번 받아 DuckDB에
캐시하고, 카드(gk_sp_id)를 선수명·시즌으로 해석한다.

spId 는 시즌 접두 + 선수를 인코딩한다(예: 101190053 → 시즌 101 ICON).
같은 선수의 다른 시즌 카드는 spId 는 다르지만 spid.json 의 name 이 같으므로,
name 으로 묶으면 "동일 선수의 어느 시즌이 잘 막나"(목표 ②의 시즌축)가 나온다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

import duckdb

from . import api
from .http import ResilientClient


def refresh(con: duckdb.DuckDBPyConnection, client: ResilientClient) -> tuple[int, int]:
    """spid/seasonid 정적 메타를 받아 캐시 테이블을 채운다."""
    spid = api.get_metadata(client, "spid")
    season = api.get_metadata(client, "seasonid")
    con.execute("DELETE FROM meta_spid")
    con.executemany(
        "INSERT INTO meta_spid VALUES (?, ?) ON CONFLICT DO NOTHING",
        [(r["id"], r["name"]) for r in spid],
    )
    con.execute("DELETE FROM meta_season")
    con.executemany(
        "INSERT INTO meta_season VALUES (?, ?) ON CONFLICT DO NOTHING",
        [(r["seasonId"], r["className"]) for r in season],
    )
    return len(spid), len(season)


def has_meta(con: duckdb.DuckDBPyConnection) -> bool:
    return con.execute("SELECT count(*) FROM meta_spid").fetchone()[0] > 0


def _season_prefixes(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int, str]]:
    """(접두문자열, seasonId, 시즌명) 을 접두 길이 desc 로. longest-prefix 매칭용."""
    rows = con.execute("SELECT season_id, class_name FROM meta_season").fetchall()
    return sorted(
        ((str(sid), sid, name) for sid, name in rows),
        key=lambda t: len(t[0]),
        reverse=True,
    )


def _season_of(sp_id: int, prefixes: list[tuple[str, int, str]]) -> tuple[int | None, str | None]:
    sid = str(sp_id)
    for pref, season_id, name in prefixes:
        if sid.startswith(pref):
            return season_id, name
    return None, None


def _name_map(con: duckdb.DuckDBPyConnection, sp_ids: Iterable[int]) -> dict[int, str]:
    ids = list({i for i in sp_ids})
    if not ids:
        return {}
    ph = ", ".join(["?"] * len(ids))
    rows = con.execute(f"SELECT sp_id, name FROM meta_spid WHERE sp_id IN ({ph})", ids).fetchall()
    return {r[0]: r[1] for r in rows}


def enrich(con: duckdb.DuckDBPyConnection, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """각 카드에 player_name·season_id·season_name 을 붙인다 (in-place)."""
    names = _name_map(con, (c["gk_sp_id"] for c in cards))
    prefixes = _season_prefixes(con)
    for c in cards:
        c["player_name"] = names.get(c["gk_sp_id"])
        sid, sname = _season_of(c["gk_sp_id"], prefixes)
        c["season_id"] = sid
        c["season_name"] = sname
    return cards


def same_player_view(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """enrich 된 카드에서 같은 선수명의 시즌이 2개 이상인 것만 묶어 비교표."""
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cards:
        if c.get("player_name"):
            by[c["player_name"]].append(c)

    out: list[dict[str, Any]] = []
    for name, group in by.items():
        if len(group) < 2:  # 비교하려면 (시즌×강화) 조합이 2개 이상
            continue
        rows = sorted(
            group,
            key=lambda c: (c["save_pct"] is not None, c["save_pct"] or 0.0),
            reverse=True,
        )
        out.append(
            {
                "player_name": name,
                "cards": [
                    {
                        "season_id": c["season_id"],
                        "season_name": c["season_name"],
                        "grade": c.get("grade"),
                        "gk_sp_id": c["gk_sp_id"],
                        "save_pct": c["save_pct"],
                        "gsax_per_shot": c.get("gsax_per_shot"),
                        "matches": c["matches"],
                    }
                    for c in rows
                ],
            }
        )
    out.sort(key=lambda d: d["player_name"])
    return out
