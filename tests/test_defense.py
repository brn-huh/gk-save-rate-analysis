"""수비 명단·조합 집계·기존 경기 추가 파싱의 회귀 검증. 모든 식별자는 합성값이다."""

import json
import zlib
from datetime import datetime

import pytest

from gksave import agg, db, defense, export, parse
from gksave.codec import encode_payload
from gksave.db import connect_memory


def _match(mid, user="synthetic-user", *, cb_grade=8, gk_grade=10, saves=2, goals=1):
    return {
        "matchId": mid, "matchDate": "2026-10-01T00:00:00", "matchType": 50,
        "matchInfo": [
            {"ouid": user, "player": [
                {"spId": 300000001, "spGrade": gk_grade, "spPosition": 0},
                {"spId": 300000020, "spGrade": 9, "spPosition": 4},
                {"spId": 300000010, "spGrade": cb_grade, "spPosition": 6},
                {"spId": 300000030, "spGrade": 7, "spPosition": 7},
                {"spId": 300000040, "spGrade": 6, "spPosition": 3},
            ], "shootDetail": []},
            {"ouid": "synthetic-opponent", "player": [
                {"spId": 300000002, "spGrade": 10, "spPosition": 0},
            ], "shootDetail": [
                {"result": r, "type": 1, "x": 0.9, "y": 0.5}
                for r in [1] * saves + [3] * goals
            ]},
        ],
    }


def _insert(con, detail):
    con.execute("INSERT INTO raw_match (match_id, match_date, payload) VALUES (?, ?, ?)",
                [detail["matchId"], datetime.fromisoformat(detail["matchDate"]), encode_payload(detail)])


@pytest.fixture
def con():
    c = connect_memory()
    yield c
    c.close()


def test_back_four_canonical_pair_preserves_fullbacks():
    detail = _match("lineup")
    me = detail["matchInfo"][0]
    result = parse.extract_back_four(me)
    assert result == (300000010, 8, 300000020, 9, 300000030, 7, 300000040, 6)
    me["player"][1]["spPosition"], me["player"][2]["spPosition"] = 6, 4
    assert parse.extract_back_four(me) == result
    apps, _ = parse.parse_match(detail)
    assert apps[0].back_four == result
    assert apps[1].back_four is None


def test_missing_user_is_not_counted_as_a_distinct_user():
    apps, _ = parse.parse_match(_match("missing-user", user=""))
    assert list(defense.rows(apps)) == []


@pytest.mark.parametrize("change", ["three-cbs", "wingback", "duplicate", "missing-grade", "zero-id"])
def test_ambiguous_or_invalid_lineups_excluded_without_losing_gk(change):
    d = _match(change)
    players = d["matchInfo"][0]["player"]
    if change == "three-cbs":
        players.append({"spId": 300000050, "spGrade": 8, "spPosition": 5})
    elif change == "wingback":
        players[3]["spPosition"] = 8
    elif change == "duplicate":
        players[2]["spId"] = players[1]["spId"]
    elif change == "missing-grade":
        del players[2]["spGrade"]
    else:
        players[2]["spId"] = 0
    apps, shots = parse.parse_match(d)
    assert len(apps) == 2 and len(shots) == 3
    assert apps[0].back_four is None


def test_combinations_count_zero_shot_matches_exclude_pk_and_keep_grades(con):
    first = _match("one")
    first["matchInfo"][1]["shootDetail"].append({"result": 3, "type": 9, "x": 0.9, "y": 0.5})
    _insert(con, first)
    _insert(con, _match("zero", "synthetic-second", saves=0, goals=0))
    _insert(con, _match("grade", cb_grade=9, saves=1, goals=0))
    _insert(con, _match("gkgrade", gk_grade=11, saves=1, goals=0))
    changed_lb = _match("fullback", "synthetic-second", saves=1, goals=0)
    changed_lb["matchInfo"][0]["player"][3]["spId"] = 300000031
    _insert(con, changed_lb)
    agg.rebuild(con)
    rows = agg.defense_combinations(con, gate=1, min_users=1)
    assert len(rows) == 3
    row = next(r for r in rows if r["grade"] == 10 and r["cb1_grade"] == 8)
    assert (row["matches"], row["users"], row["shots"], row["saves"], row["goals"]) == (3, 2, 4, 3, 1)
    assert row["save_pct"] == pytest.approx(0.75)
    assert row["shots_per_match"] == pytest.approx(4 / 3)
    assert row["goals_per_match"] == pytest.approx(1 / 3)
    assert len(agg.defense_combinations(con, gate=3, min_users=2)) == 1
    assert agg.defense_combinations(con, gate=4, min_users=1) == []
    assert agg.defense_combinations(con, gate=1, min_users=3) == []
    assert agg.defense_combinations(con, gate=1, min_users=1, since=datetime(2026, 10, 2)) == []


def test_gsax_uses_league_baseline_and_only_coordinate_shots(con):
    _insert(con, _match("saved", saves=10, goals=0))
    # 모델 모집단에는 4백이 아닌 경기의 슛도 포함해야 한다.
    other = _match("conceded", gk_grade=11, saves=0, goals=10)
    other["matchInfo"][0]["player"] = other["matchInfo"][0]["player"][:1]
    _insert(con, other)
    missing = _match("missing", saves=0, goals=1)
    del missing["matchInfo"][1]["shootDetail"][0]["x"]
    _insert(con, missing)
    agg.rebuild(con)
    row = agg.defense_combinations(con, gate=1, min_users=1)[0]
    gk = next(r for r in agg.gsax_leaderboard(con, gate=1) if r["grade"] == 10)
    assert row["gsax_per_shot"] == pytest.approx(gk["gsax_per_shot"])
    assert row["gsax_shots"] == 10 and row["shots"] == 11
    assert row["save_pct"] == pytest.approx(10 / 11)


def test_backfill_is_incremental_windowed_and_preserves_existing_stats(con):
    _insert(con, _match("old"))
    recent = _match("recent")
    recent["matchDate"] = "2026-10-03T00:00:00"
    _insert(con, recent)
    agg.rebuild(con)
    original = agg.grade_leaderboard(con, gate=1)
    con.execute("DELETE FROM defense_match")
    con.execute("UPDATE raw_match SET defense_parsed_at = NULL")
    counts = defense.rebuild(con, since=datetime(2026, 10, 2))
    assert counts == {"matches": 1, "lineups": 1}
    assert defense.rebuild(con, since=datetime(2026, 10, 2))["matches"] == 0
    assert defense.rebuild(con) == {"matches": 1, "lineups": 1}
    assert agg.grade_leaderboard(con, gate=1) == original
    assert con.execute("SELECT count(*) FROM defense_match").fetchone()[0] == 2
    assert agg.rebuild(con, full=True).matches == 2
    assert con.execute("SELECT count(*) FROM defense_match").fetchone()[0] == 2


def test_backfill_rolls_back_failed_batch_and_resumes_after_completed_batch(con, monkeypatch):
    for i in range(1001):
        _insert(con, _match(f"batch-{i}"))
    agg.rebuild(con)
    con.execute("DELETE FROM defense_match")
    con.execute("UPDATE raw_match SET defense_parsed_at = NULL")

    class FailingConnection:
        updates = 0

        def cursor(self):
            return con.cursor()

        def execute(self, sql, params=None):
            if sql.startswith("UPDATE raw_match SET defense_parsed_at"):
                self.updates += 1
                if self.updates == 2:
                    raise RuntimeError("simulated interruption")
            return con.execute(sql, params) if params is not None else con.execute(sql)

    clock = iter([0, 11])
    monkeypatch.setattr(defense.time, "monotonic", lambda: next(clock))
    logs = []
    with pytest.raises(RuntimeError, match="simulated interruption"):
        defense.rebuild(FailingConnection(), log=logs.append)
    assert "1,001경기" in logs[1]
    assert "1,000 / 1,001경기 (99.9%)" in logs[-1]
    assert "100.0%" not in "\n".join(logs)
    assert con.execute("SELECT count(*) FROM defense_match").fetchone()[0] == 1000
    assert con.execute("SELECT count(*) FROM raw_match WHERE defense_parsed_at IS NULL").fetchone()[0] == 1
    clock = iter([20, 21])
    logs.clear()
    assert defense.rebuild(con, log=logs.append) == {"matches": 1, "lineups": 1}
    assert "이번 처리 대상: 1경기" in logs[1]
    assert "1 / 1경기 (100.0%)" in logs[-1]
    assert con.execute("SELECT count(*) FROM defense_match").fetchone()[0] == 1001


def test_backfill_progress_is_throttled_windowed_and_does_not_expose_identifiers(con, monkeypatch):
    for i in range(2001):
        _insert(con, _match(f"synthetic-progress-{i}"))
    _insert(con, _match("synthetic-unparsed"))
    con.execute("UPDATE raw_match SET parsed_at=now() WHERE match_id<>'synthetic-unparsed'")
    _insert(con, {**_match("synthetic-old"), "matchDate": "2026-09-01T00:00:00"})
    con.execute("UPDATE raw_match SET parsed_at=now() WHERE match_id='synthetic-old'")
    clock = iter([0, 5, 15, 16])
    monkeypatch.setattr(defense.time, "monotonic", lambda: next(clock))
    logs = []
    assert defense.rebuild(con, since=datetime(2026, 10, 1), log=logs.append) == {
        "matches": 2001, "lineups": 2001,
    }
    assert len(logs) == 4
    assert "이번 처리 대상: 2,001경기" in logs[1]
    assert "2,000 / 2,001경기 (99.9%)" in logs[2]
    assert "4백 명단 2,000건 · 경과 0분 15초" in logs[2]
    assert "2,001 / 2,001경기 (100.0%)" in logs[3]
    assert "synthetic" not in "\n".join(logs) and "ouid" not in "\n".join(logs)
    clock = iter([20])
    logs.clear()
    assert defense.rebuild(con, since=datetime(2026, 10, 1), log=logs.append)["matches"] == 0
    assert len(logs) == 2 and "이번 처리 대상: 0경기" in logs[-1]


@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_regular_build_rolls_back_all_tables_and_can_retry(con, full, failure):
    _insert(con, _match("existing"))
    agg.rebuild(con)
    _insert(con, _match("new"))
    before = {table: con.execute(f"SELECT * FROM {table}").fetchall()
              for table in ("raw_match", "gk_match", "shot", "defense_match")}

    class InterruptedBuild:
        def execute(self, sql, params=None):
            if sql.startswith("UPDATE raw_match SET parsed_at = now(),"):
                raise failure("simulated interruption")
            return con.execute(sql, params) if params is not None else con.execute(sql)

    with pytest.raises(failure, match="simulated interruption"):
        agg.rebuild(InterruptedBuild(), full=full)
    for table, rows in before.items():
        assert con.execute(f"SELECT * FROM {table}").fetchall() == rows
    assert agg.rebuild(con, full=full).matches == (2 if full else 1)
    assert con.execute("SELECT count(*) FROM gk_match").fetchone()[0] == 4
    assert con.execute("SELECT count(*) FROM shot").fetchone()[0] == 6
    assert con.execute("SELECT count(*) FROM defense_match").fetchone()[0] == 2
    assert agg.rebuild(con).matches == 0


def test_raw_batches_are_bounded_and_do_not_skip_sparse_pending_rows(con):
    for i in range(2101):
        _insert(con, _match(f"bounded-{i}"))
    con.execute("UPDATE raw_match SET parsed_at = now() WHERE rowid BETWEEN 10 AND 1990")
    batches = list(db.raw_match_batches(con, "parsed_at IS NULL"))
    ids = [mid for batch in batches for mid, _ in batch]
    expected = con.execute("SELECT match_id FROM raw_match WHERE parsed_at IS NULL ORDER BY rowid").fetchall()
    assert ids == [r[0] for r in expected]
    assert max(map(len, batches)) <= 1000
    assert list(db.raw_match_batches(con, "match_date >= ?", [datetime(2026, 11, 1)])) == []


def test_full_build_parse_error_preserves_existing_statistics(con):
    _insert(con, _match("existing"))
    agg.rebuild(con)
    before = {table: con.execute(f"SELECT * FROM {table}").fetchall()
              for table in ("gk_match", "shot", "defense_match")}
    con.execute("INSERT INTO raw_match (match_id, payload) VALUES (?, ?)",
                ["synthetic-corrupt", b"invalid-compressed-payload"])
    with pytest.raises(zlib.error):
        agg.rebuild(con, full=True)
    for table, rows in before.items():
        assert con.execute(f"SELECT * FROM {table}").fetchall() == rows


def test_full_build_handles_empty_source(con):
    assert agg.rebuild(con, full=True).matches == 0
    assert con.execute("SELECT count(*) FROM defense_match").fetchone()[0] == 0


def test_completion_marker_preserves_numeric_match_id_with_leading_zeros(con):
    _insert(con, _match("000123456789012345678901"))
    assert agg.rebuild(con).matches == 1
    assert agg.rebuild(con).matches == 0


def test_public_export_has_names_but_no_identifiers_and_is_loaded_separately(con, tmp_path):
    for i in range(5):
        _insert(con, _match(f"export-{i}", f"synthetic-user-{i}"))
    agg.rebuild(con)
    con.execute("INSERT INTO meta_spid VALUES (300000001, '골키퍼'), (300000010, '센터백 A'), (300000020, '센터백 B')")
    con.execute("INSERT INTO meta_season VALUES (300, '테스트 시즌')")
    con.execute("INSERT INTO season_img VALUES (300,'https://example.test/season.png')")
    con.execute("INSERT INTO player_info (spid,salary,ovr,height) VALUES (300000010,25,110,190)")
    export.export(con, tmp_path, gate=1)
    public = (tmp_path / "defense.json").read_text()
    data = json.loads(public)
    assert len(data["combinations"]) == 1
    assert data["defenders"]["300000010"]["player_name"] == "센터백 A"
    assert data["defenders"]["300000010"]["season_img"] == "https://example.test/season.png"
    assert data["defenders"]["300000010"]["info"]["salary"] == 25
    assert data["defenders"]["300000020"]["info"]["salary"] is None
    main = json.loads((tmp_path / "leaderboard.json").read_text())
    assert data["generated_at"] == main["generated_at"]
    assert data["date_range"] == main["date_range"]
    assert "ouid" not in public and "synthetic-user" not in public and "match_id" not in public
    html = (tmp_path / "index.html").read_text()
    assert "fetch('defense.json'" in html
    embedded = html.split('<script id="gk-data" type="application/json">')[1].split('</script>')[0]
    assert '"cb1_sp_id"' not in embedded
