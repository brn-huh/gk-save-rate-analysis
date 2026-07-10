#!/bin/bash
# 수집 끝난 후 build → export 까지 실행
cd "$(dirname "$0")/.."
. .venv/bin/activate

# 통계 창은 롤링 30일. 수집 창(35일)보다 5일 좁아 경계에서 데이터가 비지 않는다.
echo "=== build (증분) ===" && gksave build && \
echo "=== export (롤링 30일) ===" && gksave export --gate 50 --days 30 --out out && \
echo "✓ build/export 완료 (git commit/push는 수동 진행)"
