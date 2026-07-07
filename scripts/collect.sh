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

echo "=== 수집 시작 (최대 ${MAX}매치 $EXTRA_ARGS) ==="
gksave collect --concurrency 12 --max-matches "$MAX" $EXTRA_ARGS
