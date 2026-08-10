"""신규 시즌·신규 카드의 부가정보 결손 점검.

update.sh 는 build+export 만 한다 — meta/playerinfo/playerdetail 은 수동이라
새 시즌이 나오면 급여·특성·엠블럼이 빈 채로 배포된다. 그걸 찾아내는 진단용.

    python scripts/check_card_gaps.py [--days 30]

--days: 최근 N일 안에 gk_match 에 처음 등장한 카드를 '신규'로 본다(기본 30).

'미조회'(아직 안 받음) 와 '없음 확정'(받아봤는데 fc-info 에 없거나 특성 0개) 을
fc_fetch_log 로 구분한다. 미조회만 실제 할 일이다.
읽기 전용 — 수집 중이면 DuckDB 락 때문에 실패한다(수집 끝나고 실행할 것).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

DB = Path(__file__).resolve().parent.parent / "data" / "gksave.duckdb"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="'신규 카드' 판정 창(일)")
    ap.add_argument("--db", default=str(DB), help="DB 경로(기본 data/gksave.duckdb)")
    args = ap.parse_args()

    try:
        con = duckdb.connect(args.db, read_only=True)
    except duckdb.IOException as e:
        print(f"DB 열기 실패(수집이 돌고 있으면 락 충돌): {e}", file=sys.stderr)
        return 1

    q = lambda sql, *p: con.execute(sql, list(p)).fetchall()  # noqa: E731

    # 신규 카드 = gk_match 첫 등장이 최근 N일 안
    con.execute(
        """
        CREATE OR REPLACE TEMP VIEW card_first AS
        SELECT gk_sp_id AS spid,
               CAST(substr(CAST(gk_sp_id AS VARCHAR), 1, 3) AS INTEGER) AS season_id,
               CAST(substr(CAST(gk_sp_id AS VARCHAR), -6) AS BIGINT)    AS pid,
               min(match_date) AS first_seen
        FROM gk_match GROUP BY 1
        """
    )
    (n_cards,) = q("SELECT count(*) FROM card_first")[0]
    (n_new,) = q(
        "SELECT count(*) FROM card_first WHERE first_seen > now() - INTERVAL (?) DAY", args.days
    )[0]
    print(f"# 우리 GK 카드 {n_cards:,}장 (최근 {args.days}일 신규 {n_new:,}장)\n")

    # 1) 시즌 메타 (gksave meta 로 해결)
    rows = q(
        """
        SELECT c.season_id, count(*) AS cards, max(c.first_seen) AS latest
        FROM card_first c LEFT JOIN meta_season m ON m.season_id = c.season_id
        WHERE m.season_id IS NULL GROUP BY 1 ORDER BY 1 DESC
        """
    )
    print(f"[1] meta_season 에 없는 시즌: {len(rows)}개  → `gksave meta`")
    for sid, cards, latest in rows:
        print(f"    시즌 {sid}  카드 {cards}장  최근등장 {latest:%Y-%m-%d}")

    # 2) 선수명 메타 (gksave meta 로 해결)
    rows = q(
        """
        SELECT c.season_id, count(*) FROM card_first c
        LEFT JOIN meta_spid m ON m.sp_id = c.spid
        WHERE m.sp_id IS NULL GROUP BY 1 ORDER BY 2 DESC
        """
    )
    tot = sum(r[1] for r in rows)
    print(f"\n[2] meta_spid 에 이름 없는 카드: {tot}장  → `gksave meta`")
    for sid, cnt in rows[:15]:
        print(f"    시즌 {sid}  {cnt}장")

    # 3) 급여·OVR (gksave playerinfo 로 해결). 미조회 / 없음확정 구분
    rows = q(
        """
        SELECT c.season_id,
               count(*) FILTER (WHERE l.spid IS NULL) AS not_tried,
               count(*) FILTER (WHERE l.spid IS NOT NULL) AS confirmed_absent
        FROM card_first c
        LEFT JOIN player_info p ON p.spid = c.spid
        LEFT JOIN fc_fetch_log l ON l.spid = c.spid AND l.kind = 'info'
        WHERE p.spid IS NULL GROUP BY 1 HAVING not_tried > 0 ORDER BY not_tried DESC
        """
    )
    (absent,) = q(
        """
        SELECT count(*) FROM card_first c
        LEFT JOIN player_info p ON p.spid = c.spid
        JOIN fc_fetch_log l ON l.spid = c.spid AND l.kind = 'info'
        WHERE p.spid IS NULL
        """
    )[0]
    tot = sum(r[1] for r in rows)
    print(f"\n[3] player_info 미조회 카드: {tot}장  → `gksave playerinfo`")
    print(f"    (별개로 fc-info '미등재 확정' {absent}장 — 받아봤지만 없는 카드, 할 일 아님)")
    for sid, nt, ca in rows[:15]:
        print(f"    시즌 {sid}  미조회 {nt}장")

    # 4) 시즌 엠블럼 (gksave playerinfo 안의 sync_season_img 로 해결)
    # sync_season_img 는 훑어본 시즌을 fc_fetch_log 의 spid 칸에 season_id 로 남긴다.
    rows = q(
        """
        SELECT DISTINCT c.season_id,
               (l.spid IS NOT NULL) AS searched
        FROM card_first c
        LEFT JOIN season_img s ON s.season_id = c.season_id
        LEFT JOIN fc_fetch_log l ON l.spid = c.season_id AND l.kind = 'season'
        WHERE s.season_id IS NULL ORDER BY 1 DESC
        """
    )
    todo = [r[0] for r in rows if not r[1]]
    done = [r[0] for r in rows if r[1]]
    print(f"\n[4] season_img 미조회 시즌: {len(todo)}개  → `gksave playerinfo`")
    if todo:
        print("    " + ", ".join(str(s) for s in todo))
    if done:
        print(f"    (별개로 '못 찾음 확정' {len(done)}개: {', '.join(str(s) for s in done)}"
              " — fc-info 목록에 없는 옛 시즌)")

    # 5) 특성 (gksave playerdetail 로 해결)
    rows = q(
        """
        SELECT c.season_id,
               count(*) FILTER (WHERE l.spid IS NULL) AS not_tried,
               count(*) FILTER (WHERE l.spid IS NOT NULL) AS confirmed_none
        FROM card_first c
        LEFT JOIN (SELECT DISTINCT spid FROM player_trait) t ON t.spid = c.spid
        LEFT JOIN fc_fetch_log l ON l.spid = c.spid AND l.kind = 'detail'
        WHERE t.spid IS NULL GROUP BY 1 HAVING not_tried > 0 ORDER BY not_tried DESC
        """
    )
    (none_confirmed,) = q(
        """
        SELECT count(*) FROM card_first c
        LEFT JOIN (SELECT DISTINCT spid FROM player_trait) t ON t.spid = c.spid
        JOIN fc_fetch_log l ON l.spid = c.spid AND l.kind = 'detail'
        WHERE t.spid IS NULL
        """
    )[0]
    tot = sum(r[1] for r in rows)
    print(f"\n[5] player_trait 미조회 카드: {tot}장  → `gksave playerdetail`")
    print(f"    (별개로 '특성 0개 확정' {none_confirmed}장 — 받아봤는데 특성이 없는 카드)")
    for sid, nt, cn in rows[:15]:
        print(f"    시즌 {sid}  미조회 {nt}장")

    # 6) 국가·클럽 (pid 단위, playerdetail 로 해결)
    (n_bio,) = q(
        """
        SELECT count(DISTINCT c.pid) FROM card_first c
        LEFT JOIN player_bio b ON b.pid = c.pid WHERE b.pid IS NULL
        """
    )[0]
    print(f"\n[6] player_bio 없는 선수(pid): {n_bio}명  → `gksave playerdetail`")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
