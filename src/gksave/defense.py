"""기존 경기의 수비 명단만 증분 파싱한다. GK·슛 집계와 원본은 보존한다."""

from __future__ import annotations

import csv
import tempfile
import time
from collections.abc import Callable
from datetime import datetime

import duckdb

from .codec import decode_payload
from .db import raw_match_batches
from .parse import Appearance, parse_match

COLUMNS = (
    "match_id", "match_date", "gk_ouid", "gk_sp_id", "gk_sp_grade",
    "cb1_sp_id", "cb1_grade", "cb2_sp_id", "cb2_grade",
    "lb_sp_id", "lb_grade", "rb_sp_id", "rb_grade",
)


def rows(appearances: list[Appearance]):
    for app in appearances:
        if app.back_four is not None and isinstance(app.gk_ouid, str) and app.gk_ouid:
            yield [app.match_id, app.match_date, app.gk_ouid,
                   app.gk_sp_id, app.gk_sp_grade, *app.back_four]


def rebuild(
    con: duckdb.DuckDBPyConnection, *, since: datetime | None = None,
    log: Callable[[str], None] | None = None,
) -> dict:
    """1000경기씩 원자적으로 저장해 중단 후 재개할 수 있다. API 재호출은 없다."""
    predicate = "parsed_at IS NOT NULL AND defense_parsed_at IS NULL"
    if since:
        predicate += " AND match_date >= ?"
    params = [since] if since else []
    started = last_logged = time.monotonic()
    total = 0
    if log is not None:
        log("[수비 명단] 미처리 경기 수 확인 중…")
        total = con.execute(f"SELECT count(*) FROM raw_match WHERE {predicate}", params).fetchone()[0]
        log(f"[수비 명단] 이번 처리 대상: {total:,}경기")
    counts = {"matches": 0, "lineups": 0}
    for batch in raw_match_batches(con, predicate, params):
        with tempfile.TemporaryDirectory() as tmp:
            path = f"{tmp}/defense.csv"
            lineups = 0
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(COLUMNS)
                for _, payload in batch:
                    apps, _ = parse_match(decode_payload(payload))
                    for row in rows(apps):
                        writer.writerow(row)
                        lineups += 1
            con.execute("BEGIN TRANSACTION")
            try:
                con.execute(f"COPY defense_match ({', '.join(COLUMNS)}) FROM ? "
                            "(HEADER true, NULLSTR '')", [path])
                con.execute(
                    "UPDATE raw_match SET defense_parsed_at = now() "
                    "WHERE match_id IN (SELECT unnest(?))", [[mid for mid, _ in batch]],
                )
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
        counts["matches"] += len(batch)
        counts["lineups"] += lineups
        if log is not None:
            now = time.monotonic()
            if now - last_logged >= 10 or counts["matches"] == total:
                elapsed = int(now - started)
                progress = 100.0 if counts["matches"] == total else min(99.9, counts["matches"] / total * 100)
                log(
                    f"[수비 명단] {counts['matches']:,} / {total:,}경기 "
                    f"({progress:.1f}%) · "
                    f"4백 명단 {counts['lineups']:,}건 · 경과 {elapsed // 60:,}분 {elapsed % 60:02d}초"
                )
                last_logged = now
    return counts
