# 운영 가이드 (수집 → 빌드 → 배포)

## 사전 준비 (최초 1회)

```bash
# 가상환경 활성화
. .venv/bin/activate

# .env.local 에 API 키 설정 (없으면)
cp .env.local.example .env.local
# .env.local 열어서 NEXON_API_KEY=발급키 채우기
```

---

## 1. 수집

### 기본 (추천)
```bash
gksave collect --concurrency 12 --max-matches 30000
```
- `--max-matches` : 이번에 새로 받을 매치 수 (시간 기준: 3만 ≈ 1시간)
- `--concurrency 12` : 동시 요청 수 (레이트리밋 안에서 병렬 → 약 3배 빠름)
- 중단해도 이어서 재개 가능 (frontier 큐 영속)

### 백그라운드로 돌리기 (터미널 닫아도 됨)
```bash
nohup gksave collect --concurrency 12 --max-matches 30000 > collect.log 2>&1 &
echo "PID: $!"          # PID 메모해두기
tail -f collect.log     # 진행 확인 (Ctrl+C 로 tail 빠져나와도 수집은 계속)
```

### 진행 확인
```bash
tail -20 collect.log                        # 백그라운드 로그
# 또는
python3 -c "
import duckdb
c = duckdb.connect('data/gksave.duckdb', read_only=True)
print('매치:', c.execute('SELECT count(*) FROM raw_match').fetchone()[0])
print('pending:', c.execute(\"SELECT count(*) FROM frontier WHERE state='pending'\").fetchone()[0])
"
```

---

## 2. 수집 중단

### 포그라운드 (터미널에서 직접 돌리는 중)
```
Ctrl + C
```
→ 즉시 중단. 그 시점까지 저장된 데이터는 그대로 보존됨.

### 백그라운드 (nohup 으로 돌린 경우)
```bash
pkill -f "gksave collect"
```
→ 마찬가지로 저장된 데이터 보존. 중단된 위치에서 다시 이어서 재개 가능.

---

## 3. 빌드 (수집 후 반드시 실행)

수집한 raw JSON을 분석 테이블(gk_match, shot)로 파싱.

```bash
gksave build
```
- 168,000 매치 기준 약 2분
- 수집 후 한 번, export 전에 항상 실행

---

## 4. Export (페이지·JSON 갱신)

```bash
gksave export --gate 50 --out out
```
- `out/index.html` (공개 페이지), `out/leaderboard.json`, `out/leaderboard.csv` 갱신
- `--gate 50` : 최소 50경기 이상인 카드만 리더보드에 포함

---

## 5. 배포 (Vercel 반영)

```bash
git add out
git commit -m "chore: 리더보드 갱신"
git push
```
→ Vercel 자동 재배포 (1~2분 내 URL 반영)

---

## 한 번에 실행 (수집 끝난 후 갱신 전체)

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
# 선방률 순위 (콘솔)
gksave leaderboard --gate 50 --top 20

# GSAx(난이도 보정) 순위
gksave gsax --gate 50 --top 20

# 특정 카드 상세 (거리존·타입별)
gksave card <spId> --grade 10

# 공개 페이지 로컬에서 열기
open out/index.html
```

---

## 흐름 요약

```
수집 (collect)
  ↓  Ctrl+C 또는 pkill 로 중단 가능 — 데이터 보존
빌드 (build)     ← raw_match → gk_match/shot 파싱
  ↓
Export           ← 리더보드 JSON/CSV/HTML 생성
  ↓
git add out && git commit && git push
  ↓
Vercel 자동 재배포
```
