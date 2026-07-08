#!/bin/bash
# 수집 끝난 후 build → export 까지 실행
cd "$(dirname "$0")/.."
. .venv/bin/activate

echo "=== build (증분) ===" && gksave build && \
echo "=== export ===" && gksave export --gate 50 --out out && \
echo "✓ build/export 완료 (git commit/push는 수동 진행)"
