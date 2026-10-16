"""사용자 편중 카드는 모든 최종 산출물에서 제외한다. 식별자는 합성값이다."""

import csv
import json
import re
from datetime import datetime, timedelta, timezone

from gksave import agg, export
from gksave.db import connect_memory


def test_export_omits_concentrated_cards_from_all_outputs(tmp_path, monkeypatch):
    model_calls = []
    original = agg._expected_shots_cte

    def counted(*args, **kwargs):
        model_calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(agg, "_expected_shots_cte", counted)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    since = now - timedelta(days=10)
    cases = [
        (300000001, 10, [7, 4, 3, 3, 3]),  # 정확히 35% → 제외
        (300000001, 11, [6, 4, 4, 3, 3]),  # 같은 카드의 다른 강화 30% → 유지
        (400000001, 10, [6, 4, 4, 3, 3]),
        (500000002, 10, [8, 3, 3, 3, 3]),  # 40% → 제외
    ]
    expected = {(300000001, 11), (400000001, 10)}
    with connect_memory() as con:
        for sp_id, grade, counts in cases:
            for user, n in enumerate(counts):
                for i in range(n):
                    mid = f"synthetic-{sp_id}-{grade}-{user}-{i}"
                    identity = [mid, now - timedelta(days=1), f"synthetic-user-{user}", sp_id, grade]
                    con.execute("INSERT INTO gk_match (match_id,match_date,gk_ouid,gk_sp_id,gk_sp_grade) "
                                "VALUES (?,?,?,?,?)", identity)
                    con.execute("INSERT INTO shot (match_id,match_date,gk_ouid,gk_sp_id,gk_sp_grade,"
                                "result,shot_type,is_pk,x,y) VALUES (?,?,?,?,?,1,1,false,0.9,0.5)", identity)
                    con.execute("INSERT INTO defense_match (match_id,match_date,gk_ouid,gk_sp_id,"
                                "gk_sp_grade,cb1_sp_id,cb1_grade,cb2_sp_id,cb2_grade) "
                                "VALUES (?,?,?,?,?,300000010,8,300000020,8)", identity)
        # 과거 추이를 만들되 현재 창의 사용자 비중에는 영향을 주지 않는다.
        con.execute("INSERT INTO gk_match (match_id,match_date,gk_ouid,gk_sp_id,gk_sp_grade) "
                    "VALUES ('synthetic-old',?,'synthetic-old-user',900000001,10)",
                    [since - timedelta(days=5)])
        con.execute("INSERT INTO meta_spid VALUES (300000001,'합성 GK A'), (400000001,'합성 GK A'), "
                    "(500000002,'합성 GK B'), (300000010,'합성 CB A'), (300000020,'합성 CB B')")
        con.execute("INSERT INTO meta_season VALUES (300,'TEST A'), (400,'TEST B'), (500,'TEST C')")
        logs = []
        export.export(con, tmp_path, gate=1, since=since, log=logs.append)
        assert len(model_calls) == 1
        assert con.execute("SELECT count(*) FROM gk_match").fetchone()[0] == 81

    def keys(rows):
        return {(int(r["gk_sp_id"]), int(r["grade"])) for r in rows}

    main = json.loads((tmp_path / "leaderboard.json").read_text())
    assert main["leaderboard_count"] == 2
    assert keys(main["leaderboard"]) == keys(main["gsax"]) == expected
    assert sorted(r["rank"] for r in main["leaderboard"]) == [1, 2]
    assert keys([c for g in main["same_player"] for c in g["cards"]]) == expected
    expected_details = {f"{sp_id}_{grade}" for sp_id, grade in expected}
    assert set(json.loads((tmp_path / "details.json").read_text())) == expected_details
    assert main["trend"]["points"]
    assert set(main["trend"]["cards"]) == expected_details
    defense = json.loads((tmp_path / "defense.json").read_text())
    assert keys(defense["combinations"]) == expected
    assert keys(main["defense"]["combinations"]) == expected
    with (tmp_path / "leaderboard.csv").open() as f:
        assert keys(list(csv.DictReader(f))) == expected
    html = (tmp_path / "index.html").read_text()
    embedded = json.loads(re.search(r'id="gk-data"[^>]*>(.*?)</script>', html, re.S)[1])
    assert keys(embedded["leaderboard"]) == expected
    assert "합성 GK B" not in html
    phases = ["사용자 비중", "강화효과", "순위 추이", "센터백 듀오 대상 선정", "공통 GSAx 모델",
              "GSAx 카드 순위", "센터백 듀오 입력 준비",
              "거리 구간별", "슛 타입별", "추가 통계", "선수·시즌", "JSON·CSV", "HTML", "저장 완료"]
    positions = [next(i for i, line in enumerate(logs) if phase in line) for phase in phases]
    assert positions == sorted(positions)
    assert all("synthetic-user" not in line and "ouid" not in line for line in logs)
