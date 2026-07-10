"""raw_match.payload 인코딩/디코딩 단일 지점.

매치 상세 JSON 은 평균 20.8KB 인데 zlib-6 으로 2.77KB(13.3%) 가 된다. 수집기가
매치마다 이걸 11GB 넘는 테이블에 쓰는 동안 in-flight 요청이 0이 되므로, 쓰기량
감소가 곧 수집 처리량이다.

파싱 로직(GSAx 캘리브레이션)이 아직 바뀌므로 payload 는 무손실로 보관한다.
`decode_payload` 는 bytes 뿐 아니라 str/dict 도 받는다 — 구형 DB(JSON 컬럼)와
`.bak` 백업을 같은 코드로 계속 읽기 위해서다.
"""

from __future__ import annotations

import json
import zlib
from typing import Any

_LEVEL = 6


def encode_payload(detail: dict[str, Any]) -> bytes:
    """매치 상세 dict → zlib 압축 bytes."""
    return zlib.compress(json.dumps(detail, ensure_ascii=False).encode("utf-8"), _LEVEL)


def decode_payload(value: bytes | bytearray | memoryview | str | dict[str, Any]) -> dict[str, Any]:
    """압축 bytes · 구형 JSON 문자열 · 이미 파싱된 dict 를 모두 dict 로 돌려준다."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return json.loads(zlib.decompress(bytes(value)).decode("utf-8"))
    raise TypeError(f"payload 로 쓸 수 없는 타입: {type(value).__name__}")
