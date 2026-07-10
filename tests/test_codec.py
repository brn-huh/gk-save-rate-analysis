"""payload 압축 코덱: 왕복 무손실, 한글 보존, 구형 입력(str/dict) 수용.

decode_payload 가 관대해야 마이그레이션 전 구형 DB(JSON 컬럼)도 계속 읽힌다.
그 덕에 코드 전환(T1~T5)을 실제 DB 마이그레이션(T6~T7)보다 먼저 커밋할 수 있다.
"""

import json
import zlib

import pytest

from gksave.codec import decode_payload, encode_payload

_DETAIL = {
    "matchId": "6650a1b2c3",
    "matchInfo": [
        {"nickname": "골키퍼왕", "shootDetail": [{"result": 1, "x": 0.5, "y": 0.9}]},
        {"nickname": "상대", "shootDetail": []},
    ],
    "설명": "한글·기호 ✓ 보존되어야 함",
}


def test_encode_returns_compressed_bytes():
    blob = encode_payload(_DETAIL)
    assert isinstance(blob, bytes)
    # 압축이 실제로 일어났는지 — 원본 JSON 보다 작아야 한다
    assert len(blob) < len(json.dumps(_DETAIL, ensure_ascii=False).encode())


def test_roundtrip_is_lossless():
    assert decode_payload(encode_payload(_DETAIL)) == _DETAIL


def test_korean_survives_roundtrip():
    out = decode_payload(encode_payload(_DETAIL))
    assert out["설명"] == "한글·기호 ✓ 보존되어야 함"
    assert out["matchInfo"][0]["nickname"] == "골키퍼왕"


def test_decode_accepts_legacy_json_string():
    # 구형 DB 의 JSON 컬럼은 str 로 나온다 → 그대로 파싱해야 한다
    assert decode_payload(json.dumps(_DETAIL, ensure_ascii=False)) == _DETAIL


def test_decode_accepts_dict_passthrough():
    # duckdb 가 JSON 을 dict 로 돌려주는 경로도 있다
    assert decode_payload(_DETAIL) == _DETAIL


def test_decode_accepts_bytearray():
    assert decode_payload(bytearray(encode_payload(_DETAIL))) == _DETAIL


def test_encode_uses_level_6():
    # 계획서가 zlib-6 을 못박았다(2.77KB/13.3% 실측). 레벨이 바뀌면 크기 가정이 깨진다.
    raw = json.dumps(_DETAIL, ensure_ascii=False).encode()
    assert encode_payload(_DETAIL) == zlib.compress(raw, 6)


def test_decode_rejects_unknown_type():
    with pytest.raises(TypeError):
        decode_payload(12345)
