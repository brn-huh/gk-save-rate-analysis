import json
import os
import sys
from pathlib import Path

# src 레이아웃을 설치 없이 import 하기 위한 경로 주입
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# 클라이언트 생성 시 필요한 더미 키 (실제 호출 안 함)
os.environ.setdefault("NEXON_API_KEY", "test-key")

import pytest

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def sample_detail() -> dict:
    return json.loads((_FIXTURES / "match_detail_sample.json").read_text(encoding="utf-8"))
