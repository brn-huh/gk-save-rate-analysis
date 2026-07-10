#!/usr/bin/env python
"""AC3: 마이그레이션이 payload 를 무손실로 옮겼는지 원본과 대조한다.

무작위 표본의 `decode_payload(신규 BLOB)` 가 구 payload 의 `json.loads` 와
정확히 같은 dict 인지 본다. 둘 다 read_only 로 연다.

    python scripts/verify_migration.py [src] [dst] [표본수]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gksave.codec import decode_payload  # noqa: E402


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/gksave.duckdb")
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/gksave.new.duckdb")
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 1000

    old = duckdb.connect(str(src), read_only=True)
    new = duckdb.connect(str(dst), read_only=True)

    ids = [r[0] for r in old.execute(
        f"SELECT match_id FROM raw_match USING SAMPLE {n} ROWS"
    ).fetchall()]
    print(f"표본 {len(ids):,}건 대조 ({src.name} → {dst.name})")

    mismatched: list[str] = []
    missing: list[str] = []
    for mid in ids:
        o = old.execute("SELECT payload FROM raw_match WHERE match_id=?", [mid]).fetchone()
        d = new.execute("SELECT payload FROM raw_match WHERE match_id=?", [mid]).fetchone()
        if d is None:
            missing.append(mid)
            continue
        want = json.loads(o[0]) if isinstance(o[0], str) else o[0]
        if decode_payload(d[0]) != want:
            mismatched.append(mid)

    old.close()
    new.close()

    ok = not mismatched and not missing
    print(f"  {'PASS' if ok else 'FAIL'}  AC3 payload 무손실  "
          f"불일치 {len(mismatched)} / 누락 {len(missing)}")
    if mismatched:
        print("  불일치 예:", mismatched[:5])
    if missing:
        print("  누락 예:", missing[:5])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
