#!/bin/bash
# 사용법:
#   ./scripts/collect.sh            # 기본 (pending 있을 때, 3만 매치)
#   ./scripts/collect.sh --refresh  # pending 없을 때 (새 경기 보충)
#   ./scripts/collect.sh --max 50000

cd "$(dirname "$0")/.."
. .venv/bin/activate

MAX=30000
EXTRA_ARGS=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --refresh) EXTRA_ARGS="--refresh"; shift ;;
    --max) MAX="$2"; shift 2 ;;
    *) shift ;;
  esac
done

# 수집 창 35일 = 통계 창 30일 + 5일 여유. reached_old 조기중단으로 요청을 아낀다.
echo "=== 수집 시작 (최대 ${MAX}매치, 최근 35일 $EXTRA_ARGS) ==="
if gksave collect --concurrency 12 --days 35 --max-matches "$MAX" $EXTRA_ARGS; then
  echo
  read -r -p "수집 완료. update.sh를 지금 실행할까요? [y/N] " answer
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    ./scripts/update.sh
  else
    echo "update.sh는 건너뜁니다."
  fi
else
  echo "수집 중 오류가 발생해 update.sh는 실행하지 않습니다."
  exit 1
fi
