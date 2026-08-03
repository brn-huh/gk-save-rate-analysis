#!/bin/bash
# 수집 끝난 후 build → export → git commit/push 까지 실행
cd "$(dirname "$0")/.."
. .venv/bin/activate

# 통계 창은 롤링 30일. 수집 창(35일)보다 5일 좁아 경계에서 데이터가 비지 않는다.
# build/export 중 하나라도 실패하면 커밋하지 않고 중단한다.
echo "=== build (증분) ===" && gksave build && \
echo "=== export (롤링 30일) ===" && gksave export --gate 200 --days 30 --out out || exit 1

echo "=== git commit/push ==="
# 산출물 경로만 스테이징한다. 작업 중이던 다른 파일이 딸려 올라가지 않도록.
git add out || exit 1

# 수집분이 통계에 반영되지 않은 날은 out/이 그대로다. 커밋할 게 없으니 정상 종료.
if git diff --quiet --cached -- out; then
  echo "✓ out/ 변경 없음 — 커밋 생략"
  exit 0
fi

# 커밋 메시지의 매치 수·리더보드 장수는 산출물에 박힌 gk-data JSON에서 뽑는다.
STATS=$(python3 - <<'PY'
import json, re
html = open('out/index.html', encoding='utf-8').read()
m = re.search(r'<script id="gk-data" type="application/json">(.*?)</script>', html, re.S)
d = json.loads(m.group(1))
print(d['total_collected_matches'] // 10000, len(d['leaderboard']))
PY
)

if [ -n "$STATS" ]; then
  read -r MATCHES CARDS <<< "$STATS"
  MSG="chore: out/ 재생성 — 최신 수집 반영 (매치 ${MATCHES}만건, 리더보드 ${CARDS}장)"
else
  # 숫자 추출이 실패해도 푸시 자체는 막지 않는다.
  echo "! gk-data 파싱 실패 — 숫자 없는 메시지로 커밋" >&2
  MSG="chore: out/ 재생성 — 최신 수집 반영"
fi

git commit -q -m "$MSG" && git push && echo "✓ 완료 — $MSG"
