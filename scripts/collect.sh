#!/bin/bash
# 수동 트리거 전용. 자동 스케줄링(cron/launchd/nohup)은 쓰지 않는다 —
# 모듈을 계속 고치는 중이라 백그라운드 수집이 DuckDB 쓰기 락과 충돌한다.
#
# 수집이 끝나면 묻지 않고 바로 update.sh(build + export)를 실행한다.
#
# 사용법:
#   ./scripts/collect.sh              # 기본 (3만 매치 수집 후 자동 update)
#   ./scripts/collect.sh --refresh    # pending 없을 때 (새 경기 보충)
#   ./scripts/collect.sh --max 50000
#   ./scripts/collect.sh --no-update  # 수집만, update.sh 건너뜀

set -o pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate

MAX=30000
EXTRA_ARGS=""
UPDATE=yes   # yes | no  (기본: 수집 후 자동 update)

while [[ $# -gt 0 ]]; do
  case $1 in
    --refresh) EXTRA_ARGS="--refresh"; shift ;;
    --max) MAX="$2"; shift 2 ;;
    --no-update) UPDATE=no; shift ;;
    --yes|-y) shift ;;   # 하위호환: 이제 기본이라 no-op
    *) shift ;;
  esac
done

# 수집 창 35일 = 통계 창 30일 + 5일 여유. reached_old 조기중단으로 요청을 아낀다.
echo "=== 수집 시작 (최대 ${MAX}매치, 최근 35일 $EXTRA_ARGS) ==="
# 동시성은 CLI 기본(18) 또는 GKSAVE_CONCURRENCY 를 따른다 — 여기서 하드코딩하지 않는다.
if ! gksave collect --days 35 --max-matches "$MAX" $EXTRA_ARGS; then
  echo "수집 중 오류가 발생해 update.sh는 실행하지 않습니다." >&2
  exit 1
fi

echo
if [[ "$UPDATE" == "yes" ]]; then
  echo "=== 수집 완료 → update.sh 자동 실행 ==="
  ./scripts/update.sh
else
  echo "update.sh는 건너뜁니다 (--no-update)."
fi
