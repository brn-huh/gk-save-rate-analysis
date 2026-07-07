# 운영 가이드 (수집 → 빌드 → 배포)

---

## 스크립트 한눈에 보기

| 스크립트 | 하는 일 |
|---|---|
| `./scripts/status.sh` | 현재 매치 수 · 대기 유저(pending) 확인 |
| `./scripts/collect.sh` | 수집 (pending 있을 때) |
| `./scripts/collect.sh --refresh` | 수집 (pending 없을 때, 새 경기 보충) |
| `./scripts/collect.sh --max 50000` | 수집량 직접 지정 |
| `./scripts/update.sh` | build → export → git push 한방에 |

---

## status.sh — 현재 상태 확인

수집 전에 먼저 pending이 얼마나 남아있는지 확인.

```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/status.sh
```

출력 예시:
```
저장된 매치: 168,643개
완료 유저:   1,627명
대기 유저:   110,224명  ← 0이면 collect --refresh 사용
```

- **대기 유저 > 0** → `collect.sh` 사용 (상황 A)
- **대기 유저 = 0** → `collect.sh --refresh` 사용 (상황 B)

---

## collect.sh — 수집

### 상황 A — 대기 유저(pending)가 남아있을 때
```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/collect.sh
```

### 상황 B — 대기 유저가 0일 때 (새 경기 보충)
```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/collect.sh --refresh
```

### 수집량 직접 지정 (기본 3만)
```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/collect.sh --max 50000
```

### 옵션 조합 예시
```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/collect.sh --refresh --max 50000
```

### 수집 중단
```
Ctrl + C
```
→ 그 시점까지 저장된 데이터 보존됨. 다시 실행하면 중단된 위치부터 이어서 재개.

---

## build (단독 실행이 필요할 때)

**보통은 `update.sh`로 충분.** 파싱 로직이 바뀌었을 때만 `--full` 사용.

```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
. .venv/bin/activate
gksave build          # 증분 (새로 수집한 것만 파싱 — 빠름)
gksave build --full   # 전체 재파싱 (파싱 로직이 바뀌었을 때만)
```

---

## update.sh — 빌드 + 배포 한방에

수집이 끝난 후 실행. build → export → git push → Vercel 재배포까지 자동.

```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/update.sh
```

출력 예시:
```
=== build ===
raw_match 168643건 → 파싱: 매치 168388, GK출전 307950, 슛 1583198 ...
=== export ===
=== deploy ===
✓ Vercel 재배포 시작됨
```

---

## 전체 흐름

```
1. ./scripts/status.sh
       대기 유저 > 0 ?
       ├── YES → ./scripts/collect.sh
       └── NO  → ./scripts/collect.sh --refresh
           ↓
       (Ctrl+C 로 언제든 중단 가능, 데이터 보존·재개 가능)
           ↓
2. ./scripts/update.sh   ← build + export + push + Vercel 재배포
```
