#!/bin/bash
cd "$(dirname "$0")/.."
. .venv/bin/activate
python3 -c "
import duckdb
c = duckdb.connect('data/gksave.duckdb', read_only=True)
matches = c.execute('SELECT count(*) FROM raw_match').fetchone()[0]
done    = c.execute(\"SELECT count(*) FROM frontier WHERE state='done'\").fetchone()[0]
pending = c.execute(\"SELECT count(*) FROM frontier WHERE state='pending'\").fetchone()[0]
print(f'저장된 매치: {matches:,}개')
print(f'완료 유저:   {done:,}명')
print(f'대기 유저:   {pending:,}명  ← 0이면 collect --refresh 사용')
"
