# 운영 가이드 (수집 → 빌드 → 배포)

## 사전 준비 (최초 1회)

```bash
. .venv/bin/activate          # 가상환경 활성화
cp .env.local.example .env.local
# .env.local 열어서 NEXON_API_KEY=발급키 채우기
```

---

## Step 0. 현재 상태 확인

```bash
python3 -c "
import duckdb
c = duckdb.connect('data/gksave.duckdb', read_only=True)
matches = c.execute('SELECT count(*) FROM raw_match').fetchone()[0]
done    = c.execute(\"SELECT count(*) FROM frontier WHERE state='done'\").fetchone()[0]
pending = c.execute(\"SELECT count(*) FROM frontier WHERE state='pending'\").fetchone()[0]
print(f'저장된 매치: {matches:,}개')
print(f'완료 유저:   {done:,}명')
print(f'대기 유저:   {pending:,}명  ← 아직 안 긁은 유저')
"
```

**`pending`(대기 유저)이란?**
수집 중 상대 유저 ID를 발견하면 큐에 쌓음. "나중에 이 유저 매치도 긁어야 함" 대기 목록.
`gksave collect`를 실행하면 pending에서 꺼내 긁고, 그 매치에서 또 새 유저를 발견해 큐에 추가 → 스노우볼처럼 불어남.

---

## Step 1. 수집

### 상황 A — pending이 남아있을 때 (일반 추가 수집)
```bash
gksave collect --concurrency 12 --max-matches 30000
```
- `--max-matches 30000` : 이번에 새로 받을 매치 수 (3만 ≈ 1시간)
- `--concurrency 12` : 동시 요청 수 (레이트 한도 안에서 병렬 → 약 3배 빠름)
- 중단해도 이어서 재개 가능

### 상황 B — pending이 0일 때 (새 경기 보충)
```bash
gksave collect --refresh --concurrency 12 --max-matches 30000
```
- `--refresh` : 이미 완료된 유저를 다시 열어 **새로 생긴 경기만** 추가 수집
- 중복은 자동 차단되므로 안전하게 실행 가능

### 상황 C — 백그라운드로 돌리기 (터미널 닫아도 계속)
```bash
nohup gksave collect --concurrency 12 --max-matches 50000 > collect.log 2>&1 &
echo "PID: $!"              # PID 메모해두기

tail -f collect.log         # 진행 확인 (Ctrl+C 로 tail만 빠져나와도 수집은 계속)
```

진행 출력 예시:
```
[snowball] ouid 완료. 신규매치 누적 5000 | pending 94000
[snowball] ouid 완료. 신규매치 누적 5100 | pending 93980
```
→ `신규매치 누적`이 `--max-matches`에 도달하면 자동 종료됨.

---

## Step 2. 수집 중단

### 포그라운드 (터미널에서 직접 돌리는 중)
```
Ctrl + C
```

### 백그라운드 (nohup 으로 돌린 경우)
```bash
pkill -f "gksave collect"
```

→ 두 경우 모두 **그 시점까지 저장된 데이터는 모두 보존**됨.
→ 다시 `gksave collect`를 실행하면 **중단된 위치부터 자동으로 이어서** 재개.

---

## Step 3. 빌드

수집한 raw JSON을 분석 테이블(gk_match, shot)로 파싱. **수집 후 반드시 실행.**

```bash
gksave build
```
- 168,000 매치 기준 약 2분

---

## Step 4. Export (페이지·JSON 갱신)

```bash
gksave export --gate 50 --out out
```
- `out/index.html`, `out/leaderboard.json`, `out/leaderboard.csv` 갱신
- `--gate 50` : 최소 50경기 이상인 카드만 포함

---

## Step 5. 배포 (Vercel 반영)

```bash
git add out
git commit -m "chore: 리더보드 갱신"
git push
```
→ Vercel 자동 재배포 (1~2분 내 반영)

---

## 한 번에 실행 (빌드→export→배포 한방에)

```bash
gksave build && \
gksave export --gate 50 --out out && \
git add out && \
git commit -m "chore: 리더보드 갱신" && \
git push
```

---

## 자주 쓰는 조회 명령어

```bash
gksave leaderboard --gate 50 --top 20        # 선방률 순위
gksave gsax --gate 50 --top 20               # GSAx(난이도 보정) 순위
gksave card <spId> --grade 10                # 카드 상세 (거리존·타입별)
open out/index.html                          # 공개 페이지 로컬에서 열기
```

---

## 전체 흐름 요약

```
[ Step 0 ] 현재 상태 확인 (매치 수, pending 수)
    ↓
[ Step 1 ] 수집
    - pending 있으면 → gksave collect
    - pending 없으면 → gksave collect --refresh
    - 백그라운드 원하면 → nohup ... &
    ↓  Ctrl+C 또는 pkill 로 언제든 중단 가능 (데이터 보존, 재개 가능)
[ Step 2 ] 중단 (선택)
    ↓
[ Step 3 ] gksave build         ← raw → gk_match/shot 파싱
    ↓
[ Step 4 ] gksave export        ← 리더보드 HTML/JSON/CSV 생성
    ↓
[ Step 5 ] git add out && git commit && git push
    ↓
Vercel 자동 재배포
```
