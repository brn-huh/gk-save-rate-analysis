"""일회성 백필: 이미 조회했던 카드를 fc_fetch_log 에 기록한다.

negative cache(fc_fetch_log) 도입 전에 돌린 playerinfo/playerdetail 은 조회 기록을
남기지 않았다. 그대로 두면 "결과 행이 없다"는 이유로 같은 카드를 한 번 더 받는다
(2026-07-22 기준 info 246장 · detail 778장 = 요청 800여 회).

근거 — 직전 실행에서 실패 0으로 끝났으므로:
  detail: need = ours - player_trait 였고 limit 없이 전부 받았다. 지금도 player_trait 에
          없는 spid 는 "받았지만 특성이 0개" 인 것뿐이다.
  info:   need = ours - player_info 중 meta_spid 에 이름이 있는 것만 검색했다. 지금도
          player_info 에 없다면 fc-info 가 그 이름으로 안 돌려준 것이다.
          이름이 없어 검색 자체를 못 한 spid 는 제외한다(조회한 적 없으므로).

**절대 반복 실행용이 아니다.** 자동화(_migrate 등)에 넣지 말 것 — 새로 수집된 카드까지
"조회함"으로 찍혀 영영 안 받게 된다. 한 번 돌리고 끝.

사용: .venv/bin/python scripts/backfill_fetch_log.py [--apply]
"""

from __future__ import annotations

import sys

from gksave import db

DETAIL = """
INSERT INTO fc_fetch_log (kind, spid, fetched_at)
SELECT 'detail', gk_sp_id, now() FROM (
    SELECT DISTINCT gk_sp_id FROM gk_match
    EXCEPT SELECT DISTINCT spid FROM player_trait
) ON CONFLICT DO NOTHING
"""

INFO = """
INSERT INTO fc_fetch_log (kind, spid, fetched_at)
SELECT 'info', s.sp_id, now() FROM meta_spid s
WHERE s.name IS NOT NULL AND s.sp_id IN (
    SELECT gk_sp_id FROM gk_match EXCEPT SELECT spid FROM player_info
) ON CONFLICT DO NOTHING
"""

COUNTS = {
    "detail": "SELECT count(*) FROM (SELECT DISTINCT gk_sp_id FROM gk_match "
              "EXCEPT SELECT DISTINCT spid FROM player_trait)",
    "info": "SELECT count(*) FROM meta_spid s WHERE s.name IS NOT NULL AND s.sp_id IN "
            "(SELECT gk_sp_id FROM gk_match EXCEPT SELECT spid FROM player_info)",
}


def main() -> int:
    apply = "--apply" in sys.argv
    con = db.connect()
    for kind, q in COUNTS.items():
        print(f"{kind}: 기록할 카드 {con.execute(q).fetchone()[0]:,}장")
    if not apply:
        print("\n미적용(dry-run). 실제로 쓰려면 --apply 를 붙여라.")
        return 0
    con.execute(DETAIL)
    con.execute(INFO)
    rows = con.execute(
        "SELECT kind, count(*) FROM fc_fetch_log GROUP BY kind ORDER BY kind").fetchall()
    print("\nfc_fetch_log:", ", ".join(f"{k} {n:,}" for k, n in rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
