#!/bin/bash
# 루트에서 실행하는 update 래퍼
cd "$(dirname "$0")"
./scripts/update.sh "$@"
