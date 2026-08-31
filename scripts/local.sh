#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f out/index.html ]]; then
  echo "out/index.html 이 없습니다. 먼저 ./scripts/update.sh 를 실행하세요." >&2
  exit 1
fi

PORT="${1:-8000}"
echo "로컬 페이지: http://localhost:${PORT}"
echo "종료: Ctrl-C"
exec python3 -m http.server "$PORT" --directory out
