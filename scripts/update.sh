#!/bin/bash
# 수집 끝난 후 build → 카드정보 갱신 → export 까지 실행.
# git commit/push 는 하지 않는다 — 배포는 수동으로.
cd "$(dirname "$0")/.."
. .venv/bin/activate

# 통계 창은 롤링 60일. 수집 창(65일)보다 5일 좁아 경계에서 데이터가 비지 않는다.
# build/export 중 하나라도 실패하면 중단한다.
echo "=== build (증분) ===" && gksave build || exit 1

# 카드 부가정보(선수명·시즌·급여·특성)를 export 전에 채운다. build 뒤라야 오늘 새로 등장한
# 카드가 gk_match/defense_match 에 있고, export 앞이라야 그 정보가 당일 산출물에 실린다.
# 순서 고정: meta → playerinfo → playerdetail.
# playerinfo는 fc-info만 사용(기본 3초), playerdetail은 공식 데이터센터를 사용한다.
# 셋 다 실패해도 export 는 진행한다. 외부 서비스는 언제든 일시적으로 응답하지 않을 수 있고,
# 그때 배포까지 멈추면 손해가 더 크다 — 부가정보는 다음 실행에서 증분으로 다시 채워진다.
# playerdetail --limit: FC_RECHECK_DAYS(30일)마다 '특성 0개 확정' 카드의 재조회가 한꺼번에
# 풀려 수백 장이 대기열에 돌아온다. 상한을 두어 한 회차의 상세 조회량을 제한한다.
echo "=== 카드정보 갱신 ==="
echo "=== 선수명·시즌 메타 갱신 ==="
gksave meta          || echo "! meta 실패 — 이번 회차는 건너뜀" >&2
echo "=== fc-info 카드정보·시즌 이미지 갱신 ==="
if gksave playerinfo --defenders; then
  echo "=== 공식 데이터센터 선수 상세 갱신 (최대 120장) ==="
  gksave playerdetail --limit 120 || echo "! playerdetail 실패 — 이번 회차는 건너뜀" >&2
else
  echo "! playerinfo 실패 — 추가 부가정보 요청을 멈추고 기존 캐시로 export합니다" >&2
fi

# 사용자 편중 제외는 export 공통 로직에 적용되어 collect.sh 경유 실행에도 동일하다.
echo "=== export (롤링 60일 · 한 유저 비중 35% 이상 제외) ===" && gksave export --gate 200 --days 60 --out out || exit 1

echo "✓ build/export 완료 (git commit/push는 수동 진행)"
