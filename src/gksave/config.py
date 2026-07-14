"""전역 설정과 상수.

값은 설계 문서(jwkim-main-design)에서 잠근 결정을 그대로 옮긴 것이다.
API 키는 코드에 두지 않고 환경변수 NEXON_API_KEY로만 받는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_env_files(names: tuple[str, ...] = (".env.local", ".env")) -> None:
    """.env.local / .env 의 KEY=VALUE 를 os.environ 에 주입(외부 의존성 없음).

    이미 설정된 환경변수는 덮어쓰지 않는다(export 가 파일보다 우선).
    민감정보(API 키 등)는 이 파일에만 두고 커밋하지 않는다(.gitignore).
    """
    for name in names:
        p = Path.cwd() / name
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


# 모듈 로드 시 1회 — 어떤 진입점(gksave CLI 등)에서도 .env.local 이 적용되게
load_env_files()

# ── 넥슨 Open API ────────────────────────────────────────────────
BASE_URL = "https://open.api.nexon.com"
API_KEY_ENV = "NEXON_API_KEY"

# ── 분석 스코프 (설계 결정) ──────────────────────────────────────
MATCHTYPE_OFFICIAL = 50          # 공식경기 (유저 제공 실값으로 확정)
GK_POSITION = 0                  # spposition에서 GK=0
SPGRADE_MIN = 8                  # 측정 강화단계 하한
SPGRADE_MAX = 13                 # 측정 강화단계 상한
MIN_MATCHES_GATE = 50            # 카드(시즌×선수) 리더보드 최소 표본 경기수

# 수집 롤링 창(일). 오늘-N일 이전 매치는 수집에서 제외한다.
# 통계 창(30일)보다 5일 넉넉히 잡아, reached_old 조기중단으로 요청을 아끼면서도
# 창 경계에서 데이터가 비지 않게 한다. collect 시 --since/--days 로 덮어쓸 수 있다.
#
# 이전 값 COLLECT_MIN_DATE="2026-03-26" 은 죽은 설정이었다. 실제 지평선은 날짜가
# 아니라 user_pages=3(유저당 최근 300경기)이 정하고, 수집된 데이터는 6/1 부터다.
COLLECT_WINDOW_DAYS = 35

# 신규특성(금색 배경 아이콘) 트레잇 코드 집합. fc-info 트레잇 아이콘(traits/trait_icon_{code}.png)
# 전체를 배경색(금/회)으로 1회 분류해 확정 — 금색=코드 50~68 구간. 새 게임 특성이 추가돼
# 금색 코드가 늘면 여기를 갱신한다. player_trait.is_new = trait_code in NEW_TRAIT_CODES.
NEW_TRAIT_CODES = frozenset({50, 51, 52, 53, 54, 55, 56, 57, 59, 60, 62, 63, 64, 65, 66, 67, 68})

# ── shootDetail 코드 (nexon open api 명세) ───────────────────────
RESULT_SAVE = 1                  # ontarget = 선방(실점 아님)  ※ T0에서 '수비수 블록 포함 여부' 검증 필요
RESULT_OFFTARGET = 2             # 유효슛 아님 → 분모 제외
RESULT_GOAL = 3                  # 실점
EFFECTIVE_RESULTS = (RESULT_SAVE, RESULT_GOAL)
SHOT_TYPE_PENALTY = 9            # PK → 불가항력, 헤드라인 분모 제외
# 헤더는 3번뿐이다. 명세에 없는 13·14 를 여기 넣지 말 것 — 데이터로 확인했다(2026-07-10):
#   헤더(3)   평균 9.0m · 박스 안 99.8% · 어시 측면성 중앙값 0.382 (터치라인 쪽 크로스)
#   기타(#13) 평균 19.0m · 박스 안 39.5% · 어시 측면성 0.148
#   기타(#14) 평균 14.2m · 박스 안 82.7% · 어시 측면성 0.146  ← 노멀(0.128)과 같은 중앙 패턴
SHOT_TYPE_HEADER = 3             # 헤더

# 넥슨 명세(api-3-match.yaml)가 정의하는 전부. 1~12.
SHOT_TYPE_NAMES = {
    1: "노멀", 2: "감아차기", 3: "헤더", 4: "로빙", 5: "플레어", 6: "낮은슛",
    7: "발리", 8: "프리킥", 9: "PK", 10: "너클", 11: "바이시클", 12: "파워샷",
}


def shot_type_name(t: int | None) -> str:
    """슛 타입 이름. 명세에 없는 값은 이름을 지어내지 않는다.

    실데이터에는 13(0.59%)·14(2.09%)가 있고 정작 5(플레어)는 0건이다. 원본 payload
    에 `"type": 14` 가 그대로 들어오므로 파싱 문제가 아니라 게임 업데이트로 타입이
    늘고 명세가 낡은 것이다. 맨숫자를 그대로 노출하면 화면에 버그처럼 보인다.
    """
    if t in SHOT_TYPE_NAMES:
        return SHOT_TYPE_NAMES[t]
    return f"기타(#{t if t is not None else '?'})"

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
