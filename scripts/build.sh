#!/bin/bash
# 사용법:
#   ./scripts/build.sh          # 증분 (수집한 것만 파싱 — 빠름)
#   ./scripts/build.sh --full   # 전체 재파싱 (파싱 로직이 바뀌었을 때만)

cd "$(dirname "$0")/.."
. .venv/bin/activate

if [[ "$1" == "--full" ]]; then
  echo "=== 전체 재파싱 (--full) ==="
  gksave build --full
else
  echo "=== 증분 빌드 (새로 수집한 것만) ==="
  gksave build
fi
