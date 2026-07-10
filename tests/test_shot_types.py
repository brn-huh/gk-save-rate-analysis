"""슛 타입 이름.

넥슨 명세(reference/api-3-match.yaml)는 1~12 만 정의한다. 그런데 실데이터에는
13(36,461슛)·14(129,713슛, 전체의 2.09%)가 있고, 정작 명세에 있는 5(플레어)는
한 건도 없다. 원본 payload 를 열어보면 API 가 `"type": 14` 를 그대로 보낸다 —
파싱 문제가 아니라 게임 업데이트로 타입이 늘었고 명세가 낡은 것이다.

이름을 지어내지 않는다. 모르는 타입은 모른다고 표기한다.
"""

from gksave.config import SHOT_TYPE_NAMES, shot_type_name


def test_documented_types_keep_their_names():
    assert shot_type_name(1) == "노멀"
    assert shot_type_name(9) == "PK"
    assert shot_type_name(12) == "파워샷"


def test_undocumented_types_are_labelled_not_invented():
    # 화면에 맨숫자 '13' 이 뜨면 버그로 보인다. 이름을 지어내는 건 더 나쁘다.
    assert shot_type_name(13) == "기타(#13)"
    assert shot_type_name(14) == "기타(#14)"


def test_unknown_label_survives_future_types():
    assert shot_type_name(99) == "기타(#99)"


def test_none_type_does_not_crash():
    assert shot_type_name(None) == "기타(#?)"


def test_spec_map_still_only_documents_1_to_12():
    """명세가 갱신돼 13·14 이름이 생기면 이 테스트가 실패한다 → 그때 매핑을 채운다."""
    assert set(SHOT_TYPE_NAMES) == set(range(1, 13))
