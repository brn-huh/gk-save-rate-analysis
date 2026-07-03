"""전역 설정과 상수.

값은 설계 문서(jwkim-main-design)에서 잠근 결정을 그대로 옮긴 것이다.
API 키는 코드에 두지 않고 환경변수 NEXON_API_KEY로만 받는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ── 넥슨 Open API ────────────────────────────────────────────────
BASE_URL = "https://open.api.nexon.com"
API_KEY_ENV = "NEXON_API_KEY"

# ── 분석 스코프 (설계 결정) ──────────────────────────────────────
MATCHTYPE_OFFICIAL = 50          # 공식경기 (유저 제공 실값으로 확정)
GK_POSITION = 0                  # spposition에서 GK=0
SPGRADE_MIN = 8                  # 측정 강화단계 하한
SPGRADE_MAX = 13                 # 측정 강화단계 상한
MIN_MATCHES_GATE = 50            # 카드(시즌×선수) 리더보드 최소 표본 경기수

# 수집 하한 날짜(기본): 이 날짜 이전 매치는 수집에서 제외한다.
# (게임 시즌/업데이트 경계 — 그 이전 데이터는 메타가 달라 분석에서 뺀다)
# collect 시 --since 로 덮어쓸 수 있다.
COLLECT_MIN_DATE = "2026-03-26"

# ── shootDetail 코드 (nexon open api 명세) ───────────────────────
RESULT_SAVE = 1                  # ontarget = 선방(실점 아님)  ※ T0에서 '수비수 블록 포함 여부' 검증 필요
RESULT_OFFTARGET = 2             # 유효슛 아님 → 분모 제외
RESULT_GOAL = 3                  # 실점
EFFECTIVE_RESULTS = (RESULT_SAVE, RESULT_GOAL)
SHOT_TYPE_PENALTY = 9            # PK → 불가항력, 헤드라인 분모 제외
SHOT_TYPE_HEADER = 3             # 헤더

SHOT_TYPE_NAMES = {
    1: "노멀", 2: "감아차기", 3: "헤더", 4: "로빙", 5: "플레어", 6: "낮은슛",
    7: "발리", 8: "프리킥", 9: "PK", 10: "너클", 11: "바이시클", 12: "파워샷",
}

# 거리(미터) 환산 — 정규화 유클리드거리 sqrt((1-x)^2+(0.5-y)^2) × 스케일.
# 스케일은 실데이터로 캘리브레이션: inPenalty(박스 경계)가 정규화거리 ~0.18에서
# 전환 → 그걸 페널티박스 깊이 16.5m 로 잡아 91.7 m/단위. (근사)
PITCH_SCALE_M = 91.7
# 존 컷(m): 초근<5 / 근<11 / 중<16.5 / 원≥16.5  (명세서 §2②)
ZONE_CUTS_M = (5.0, 11.0, 16.5)
ZONE_NAMES = ("초근거리(0-5m)", "근거리(5-11m)", "중거리(11-16.5m)", "원거리(16.5m+)")


def api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise RuntimeError(
            f"환경변수 {API_KEY_ENV} 가 비어 있습니다. "
            f"발급 키를 export {API_KEY_ENV}=... 로 설정하세요."
        )
    return key


@dataclass(frozen=True)
class Settings:
    """실행 튜닝값. T0 스파이크로 실측한 뒤 조정한다."""

    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("GKSAVE_DATA_DIR", "data")))
    db_name: str = "gksave.duckdb"

    # 레이트리밋 (기본 5, 환경변수 GKSAVE_RATE 로 조정. 429는 백오프가 흡수)
    # 넥슨 한도: 초당 50 / 분당 1,000(병목=평균 16.7/s) / 일일 2천만.
    # 지속 안전 최대 ≈ 15/s(900/min). 그 이상은 분당 한도로 429 유발.
    max_requests_per_sec: float = field(
        default_factory=lambda: float(os.environ.get("GKSAVE_RATE", "5"))
    )
    # 429/5xx 재시도
    max_retries: int = 6
    backoff_base_sec: float = 1.0
    backoff_max_sec: float = 60.0
    request_timeout_sec: float = 20.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_name


DEFAULT = Settings()
