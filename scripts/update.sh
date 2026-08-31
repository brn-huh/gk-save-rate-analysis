#!/bin/bash
# 수집 끝난 후 build → 카드정보 갱신 → export 까지 실행.
# git commit/push 는 하지 않는다 — 배포는 수동으로.
cd "$(dirname "$0")/.."
. .venv/bin/activate

# 통계 창은 롤링 30일. 수집 창(35일)보다 5일 좁아 경계에서 데이터가 비지 않는다.
# build/export 중 하나라도 실패하면 중단한다.
echo "=== build (증분) ===" && gksave build || exit 1

# 카드 부가정보(선수명·시즌·급여·특성)를 export 전에 채운다. build 뒤라야 오늘 새로 등장한
# 카드가 gk_match 에 있고, export 앞이라야 그 정보가 당일 산출물에 실린다.
# 순서 고정: meta → playerinfo → playerdetail.
#   playerinfo 는 meta_spid 의 '이름'으로만 fc-info 에 질의한다 — meta 가 먼저 돌지 않으면
#   신규 시즌 카드는 이름을 몰라 조회 자체가 안 된다.
# 셋 다 실패해도 export 는 진행한다. 넥슨 정적파일·fc-info 는 서드파티라 언제든 죽을 수 있고,
# 그때 배포까지 멈추면 손해가 더 크다 — 부가정보는 다음 실행에서 증분으로 다시 채워진다.
# playerdetail --limit: FC_RECHECK_DAYS(30일)마다 '특성 0개 확정' 카드의 재조회가 한꺼번에
# 풀려 수백 장이 대기열에 돌아온다(카드당 1초). 상한을 두어 그날 하루가 통째로 길어지지 않게 한다.
echo "=== 카드정보 갱신 ==="
gksave meta          || echo "! meta 실패 — 이번 회차는 건너뜀" >&2
gksave playerinfo    || echo "! playerinfo 실패 — 이번 회차는 건너뜀" >&2
gksave playerdetail --limit 120 || echo "! playerdetail 실패 — 이번 회차는 건너뜀" >&2

echo "=== export (롤링 30일) ===" && gksave export --gate 200 --days 30 --out out || exit 1

echo "✓ build/export 완료 (git commit/push는 수동 진행)"
