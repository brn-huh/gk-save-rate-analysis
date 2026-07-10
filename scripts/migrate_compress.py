#!/usr/bin/env python
"""raw_match.payload 를 JSON → zlib BLOB 으로 옮긴 새 DB 파일을 만든다.

원본은 read_only 로만 연다. 새 파일(`data/gksave.new.duckdb`)만 쓰므로 중단해도
안전하다 — 신규 파일을 지우고 다시 돌리면 된다. 스왑(mv)은 이 스크립트가 하지 않는다.

DuckDB 1.5.4 는 `DROP COLUMN`·`DELETE` 후 `CHECKPOINT` 해도 공간 회수율이 0% 라
in-place ALTER 대신 새 파일을 만든다.

주의:
- `card_stats`·`shot_readable` 은 뷰다. SCHEMA 가 다시 만드니 복사하지 않는다.
- `raw_match` 컬럼 순서가 원본(match_id, payload, fetched_at, match_date, parsed_at)과
  새 스키마(match_id, match_date, payload, ...)에서 다르다. `SELECT *` 금지, 컬럼 명시.
- 대량 삽입은 base64 + COPY. `executemany` 대비 8배 빠르다.
"""

from __future__ import annotations

import base64
import csv
import dataclasses
import os
import sys
import tempfile
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gksave import db  # noqa: E402
from gksave.codec import decode_payload, encode_payload  # noqa: E402

# 기본은 실 DB. 스모크 테스트를 위해 인자로 덮을 수 있다.
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/gksave.duckdb")
DST = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/gksave.new.duckdb")
MAX_SIZE_GB = float(os.environ.get("MIGRATE_MAX_GB", "3.5"))  # AC1

# 복사할 베이스 테이블 → 컬럼 (뷰 제외, 순서 명시)
COPY_TABLES = {
    "frontier": ["ouid", "state", "added_at"],
    "meta_spid": ["sp_id", "name"],
    "meta_season": ["season_id", "class_name"],
    "gk_match": ["match_id", "gk_ouid", "gk_sp_id", "gk_sp_grade", "match_date",
                 "sp_rating", "pass_try", "pass_success", "aerial_try", "aerial_success"],
    "shot": ["match_id", "gk_ouid", "gk_sp_id", "gk_sp_grade", "shot_type", "result",
             "is_pk", "in_penalty", "assist", "hit_post", "x", "y", "match_date"],
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _baseline(con: duckdb.DuckDBPyConnection, prefix: str = "") -> dict:
    p = f"{prefix}." if prefix else ""
    one = lambda q: con.execute(q).fetchone()  # noqa: E731
    return {
        "raw_match": one(f"SELECT count(*) FROM {p}raw_match")[0],
        "frontier": one(f"SELECT count(*) FROM {p}frontier")[0],
        "gk_match": one(f"SELECT count(*) FROM {p}gk_match")[0],
        "shot": one(f"SELECT count(*) FROM {p}shot")[0],
        "parsed_null": one(f"SELECT count(*) FROM {p}raw_match WHERE parsed_at IS NULL")[0],
        "parsed_set": one(f"SELECT count(*) FROM {p}raw_match WHERE parsed_at IS NOT NULL")[0],
        "states": dict(con.execute(f"SELECT state, count(*) FROM {p}frontier GROUP BY 1").fetchall()),
    }


def main() -> int:
    if not SRC.exists():
        _log(f"원본이 없다: {SRC}")
        return 1
    if DST.exists():
        _log(f"이미 있다: {DST} — 지우고 다시 돌려라")
        return 1

    t0 = time.time()
    ro = duckdb.connect(str(SRC), read_only=True)
    before = _baseline(ro)
    _log(f"[기준값] {before}")
    ro.close()

    settings = dataclasses.replace(db.DEFAULT, data_dir=DST.parent, db_name=DST.name)
    con = db.connect(settings)  # SCHEMA + _migrate 적용 (payload BLOB, parsed_at)
    con.execute(f"ATTACH '{SRC}' AS old (READ_ONLY)")

    for table, cols in COPY_TABLES.items():
        c = ", ".join(cols)
        t = time.time()
        con.execute(f"INSERT INTO {table} ({c}) SELECT {c} FROM old.{table}")
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        _log(f"[복사] {table:12} {n:>9,}행  {time.time()-t:.1f}s")

    # raw_match: 스트리밍 압축 → base64 CSV → COPY
    t = time.time()
    tmp = tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8",
                                      suffix=".csv", delete=False)
    written = 0
    try:
        w = csv.writer(tmp)
        w.writerow(["match_id", "match_date", "payload_b64", "fetched_at", "parsed_at"])
        cur = con.execute(
            "SELECT match_id, match_date, payload, fetched_at, parsed_at FROM old.raw_match"
        )
        while True:
            batch = cur.fetchmany(1000)
            if not batch:
                break
            for mid, mdate, payload, fetched, parsed in batch:
                # 구형 JSON 컬럼은 str 로 나온다 → decode_payload 가 흡수
                b64 = base64.b64encode(encode_payload(decode_payload(payload))).decode("ascii")
                w.writerow([mid, mdate or "", b64, fetched or "", parsed or ""])
                written += 1
            if written % 100_000 == 0:
                _log(f"  ... 압축 {written:,}건 ({time.time()-t:.0f}s)")
        tmp.close()
        _log(f"[압축] raw_match {written:,}건 → CSV {os.path.getsize(tmp.name)/2**30:.2f}GB "
             f"({time.time()-t:.0f}s)")

        t = time.time()
        con.execute("""
            CREATE TEMP TABLE stage (
                match_id VARCHAR, match_date TIMESTAMP,
                payload_b64 VARCHAR, fetched_at TIMESTAMP, parsed_at TIMESTAMP
            )
        """)
        con.execute(f"COPY stage FROM '{tmp.name}' (HEADER true, NULLSTR '')")
        con.execute("""
            INSERT INTO raw_match (match_id, match_date, payload, fetched_at, parsed_at)
            SELECT match_id, match_date, from_base64(payload_b64), fetched_at, parsed_at
            FROM stage
        """)
        con.execute("DROP TABLE stage")
        _log(f"[적재] COPY + from_base64 {time.time()-t:.0f}s")
    finally:
        if not tmp.closed:
            tmp.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

    con.execute("DETACH old")
    con.execute("CHECKPOINT")
    after = _baseline(con)
    con.close()

    size_gb = DST.stat().st_size / 2**30
    _log(f"\n[신규 파일] {DST} {size_gb:.2f} GB  (총 {time.time()-t0:.0f}s)")

    checks = [
        (f"AC1 새 DB ≤ {MAX_SIZE_GB}GB", size_gb <= MAX_SIZE_GB, f"{size_gb:.2f}GB"),
        ("AC2 raw_match 행수", after["raw_match"] == before["raw_match"], f"{after['raw_match']:,}"),
        ("AC4 parsed_at 분포", (after["parsed_null"], after["parsed_set"])
                               == (before["parsed_null"], before["parsed_set"]),
         f"NULL {after['parsed_null']:,} / SET {after['parsed_set']:,}"),
        ("AC5 frontier 행수·state", after["frontier"] == before["frontier"]
                                     and after["states"] == before["states"], f"{after['frontier']:,}"),
        ("AC6 gk_match·shot 행수", after["gk_match"] == before["gk_match"]
                                    and after["shot"] == before["shot"],
         f"{after['gk_match']:,} / {after['shot']:,}"),
    ]
    failed = [name for name, ok, _ in checks if not ok]
    for name, ok, detail in checks:
        _log(f"  {'PASS' if ok else 'FAIL'}  {name:26} {detail}")

    if failed:
        _log(f"\n실패: {failed} — 신규 파일을 남긴다. 원본은 무손상.")
        return 1
    _log("\n자체 검증 통과. AC3 는 verify_migration.py 로 따로 확인한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
