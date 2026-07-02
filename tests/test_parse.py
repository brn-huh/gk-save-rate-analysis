"""파서 CRITICAL 경로 회귀 테스트.

result 분류가 틀리면 모든 순위가 조용히 틀리므로, 실제 구조 픽스처로 손계산과 대조한다.
"""

import copy

from gksave.parse import ParseStats, extract_gk, iter_shots, parse_match


def test_extract_gk_single(sample_detail):
    gk = extract_gk(sample_detail["matchInfo"][0])
    assert gk is not None
    assert gk["spId"] == 300000001
    assert gk["spGrade"] == 10


def test_extract_gk_two_gk_returns_none(sample_detail):
    info = copy.deepcopy(sample_detail["matchInfo"][0])
    info["player"].append({"spId": 300000099, "spPosition": 0, "spGrade": 9})  # 교체 GK
    assert extract_gk(info) is None


def test_shots_result_classification(sample_detail):
    shots = iter_shots(sample_detail)
    # A GK(300000001): B의 유효슛 4개(선방2, 실점1, PK실점1) / offtarget 제외
    a = [s for s in shots if s.gk_sp_id == 300000001]
    assert len(a) == 4
    assert sum(1 for s in a if s.result == 1 and not s.is_pk) == 2
    assert sum(1 for s in a if s.result == 3 and not s.is_pk) == 1
    assert sum(1 for s in a if s.is_pk) == 1  # type==9 PK 실점 → is_pk

    # B GK(280000002): A의 유효슛 4개(선방3, 실점1), PK 없음
    b = [s for s in shots if s.gk_sp_id == 280000002]
    assert len(b) == 4
    assert sum(1 for s in b if s.result == 1) == 3
    assert sum(1 for s in b if s.result == 3) == 1
    assert all(not s.is_pk for s in b)


def test_offtarget_excluded(sample_detail):
    # result==2 는 어느 쪽에도 안 들어간다
    shots = iter_shots(sample_detail)
    assert all(s.result in (1, 3) for s in shots)


def test_gk_grade_from_player_not_shooter(sample_detail):
    # shootDetail 의 spGrade(11, 슈터)가 아니라 player GK 의 spGrade가 붙어야 한다
    shots = iter_shots(sample_detail)
    assert all(s.gk_sp_grade == 10 for s in shots if s.gk_sp_id == 300000001)


def test_grade_out_of_range_skipped(sample_detail):
    d = copy.deepcopy(sample_detail)
    d["matchInfo"][0]["player"][0]["spGrade"] = 7  # 8~13 밖
    stats = ParseStats()
    apps, shots = parse_match(d, stats)
    # A측(grade 7)은 제외 → A GK 출전/슛 없음, B측만 남음
    assert all(a.gk_sp_id != 300000001 for a in apps)
    assert stats.skipped_grade_out_of_range == 1
    assert stats.appearances == 1


def test_two_gk_skip_counted(sample_detail):
    d = copy.deepcopy(sample_detail)
    d["matchInfo"][0]["player"].append({"spId": 300000099, "spPosition": 0, "spGrade": 9})
    stats = ParseStats()
    apps, _ = parse_match(d, stats)
    assert stats.skipped_no_single_gk == 1
    assert all(a.gk_ouid != "A" for a in apps)


def test_not_two_teams_skipped(sample_detail):
    d = copy.deepcopy(sample_detail)
    d["matchInfo"] = d["matchInfo"][:1]  # 상대 결측
    stats = ParseStats()
    apps, shots = parse_match(d, stats)
    assert apps == [] and shots == []
    assert stats.skipped_not_two_teams == 1
