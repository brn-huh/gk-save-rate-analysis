#!/bin/bash
# 수동 트리거 전용. 자동 스케줄링(cron/launchd/nohup)은 쓰지 않는다 —
# 모듈을 계속 고치는 중이라 백그라운드 수집이 DuckDB 쓰기 락과 충돌한다.
#
# 사용법:
#   ./scripts/collect.sh              # 기본 (pending 있을 때, 3만 매치)
#   ./scripts/collect.sh --refresh    # pending 없을 때 (새 경기 보충)
#   ./scripts/collect.sh --max 50000
#   ./scripts/collect.sh --yes        # 물어보지 않고 update.sh 까지
#   ./scripts/collect.sh --no-update  # 수집만, update.sh 건너뜀

set -o pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate

MAX=30000
EXTRA_ARGS=""
UPDATE=ask   # ask | yes | no

while [[ $# -gt 0 ]]; do
  case $1 in
    --refresh) EXTRA_ARGS="--refresh"; shift ;;
    --max) MAX="$2"; shift 2 ;;
    --yes|-y) UPDATE=yes; shift ;;
    --no-update) UPDATE=no; shift ;;
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
# 터미널이 아니면(파이프·nohup·CI) read 가 EOF 로 즉시 빠져 조용히 건너뛴다.
# 그 침묵이 "왜 리더보드가 안 바뀌지?" 를 만든다 → 명시적으로 알린다.
if [[ "$UPDATE" == "ask" && ! -t 0 ]]; then
  echo "비대화형 실행이라 update.sh 를 건너뜁니다. 갱신하려면 --yes 를 붙이세요."
  exit 0
fi

if [[ "$UPDATE" == "ask" ]]; then
  read -r -p "수집 완료. update.sh를 지금 실행할까요? [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]] && UPDATE=yes || UPDATE=no
fi

if [[ "$UPDATE" == "yes" ]]; then
  ./scripts/update.sh
else
  echo "update.sh는 건너뜁니다."
fi
