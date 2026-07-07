#!/bin/bash
# 수집 끝난 후 build → export → git push 한방에
cd "$(dirname "$0")/.."
. .venv/bin/activate

echo "=== build ===" && gksave build && \
echo "=== export ===" && gksave export --gate 50 --out out && \
echo "=== deploy ===" && \
git add out && \
git commit -m "chore: 리더보드 갱신" && \
git push && \
echo "✓ Vercel 재배포 시작됨"
