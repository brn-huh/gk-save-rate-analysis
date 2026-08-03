#!/bin/bash
# 수동 트리거 전용. 자동 스케줄링(cron/launchd/nohup)은 쓰지 않는다 —
# 모듈을 계속 고치는 중이라 백그라운드 수집이 DuckDB 쓰기 락과 충돌한다.
#
# 수집이 끝나면 묻지 않고 바로 update.sh(build + export)를 실행한다.
#
# 사용법:
#   ./scripts/collect.sh              # 기본 (100만 매치, 최근 1일 수집 후 자동 update)
#   ./scripts/collect.sh --refresh    # pending 없을 때 (새 경기 보충)
#   ./scripts/collect.sh --max 50000
#   ./scripts/collect.sh --day 7      # 수집 창을 최근 7일로 (며칠 걸렀을 때 보충)
#   ./scripts/collect.sh --no-update  # 수집만, update.sh 건너뜀

set -o pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate

# 기본 상한 100만 — 사실상 "하루치 다 긁기". 실제 종료는 --day 창이 결정한다.
MAX=1000000
# 수집 창 기본 1일 — 매일 1회 돌리는 전제. 화면(통계) 창은 그대로 롤링 30일이다
# (update.sh 의 export --days 30). DB 에 쌓인 과거 매치는 남아 있으니 영향 없다.
# 하루 이상 거르면 그 기간만큼 구멍이 나니, 걸렀을 땐 --day 로 넓혀 보충한다.
DAYS=1
EXTRA_ARGS=""
UPDATE=yes   # yes | no  (기본: 수집 후 자동 update)

while [[ $# -gt 0 ]]; do
  case $1 in
    --refresh) EXTRA_ARGS="--refresh"; shift ;;
    --max) MAX="$2"; shift 2 ;;
    --day|--days) DAYS="$2"; shift 2 ;;   # 수집 하한 = 오늘로부터 N일 전
    --no-update) UPDATE=no; shift ;;
    --yes|-y) shift ;;   # 하위호환: 이제 기본이라 no-op
    *) shift ;;
  esac
done

if ! [[ "$DAYS" =~ ^[0-9]+$ ]] || [[ "$DAYS" -lt 1 ]]; then
  echo "--day 는 1 이상의 정수여야 합니다 (받은 값: '$DAYS')" >&2
  exit 1
fi

echo "=== 수집 시작 (최대 ${MAX}매치, 최근 ${DAYS}일 $EXTRA_ARGS) ==="
# 동시성은 CLI 기본(18) 또는 GKSAVE_CONCURRENCY 를 따른다 — 여기서 하드코딩하지 않는다.
if ! gksave collect --days "$DAYS" --max-matches "$MAX" $EXTRA_ARGS; then
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
