# 운영 가이드 (수집 → 빌드 → 배포)

> 모든 스크립트는 프로젝트 어디서든 실행 가능 (경로 자동 인식).

---

## 스크립트 목록

| 스크립트 | 하는 일 |
|---|---|
| `./scripts/status.sh` | 현재 매치 수 · 대기 유저(pending) 확인 |
| `./scripts/collect.sh` | 수집 (pending 있을 때) |
| `./scripts/collect.sh --refresh` | 수집 (pending 없을 때, 새 경기 보충) |
| `./scripts/collect.sh --max 50000` | 수집량 직접 지정 |
| `./scripts/update.sh` | build → export → git push 한방에 |

---

## 상황별 사용법

### 1. 현재 상태 확인 (pending 남아있나?)
```bash
./scripts/status.sh
```
출력 예시:
```
저장된 매치: 168,643개
완료 유저:   1,627명
대기 유저:   110,224명  ← 0이면 collect --refresh 사용
```

### 2. 수집

**pending > 0 일 때 (일반)**
```bash
./scripts/collect.sh
```

**pending = 0 일 때 (새 경기 보충)**
```bash
./scripts/collect.sh --refresh
```

**수집량 직접 지정**
```bash
./scripts/collect.sh --max 50000
```

**수집 중단**
```
Ctrl + C
```
→ 그 시점까지 저장된 데이터 보존. 다시 실행하면 이어서 재개.

### 3. 수집 끝난 후 — 빌드·배포 한방에
```bash
./scripts/update.sh
```
→ build → export → git push → Vercel 자동 재배포

---

## 전체 흐름

```
./scripts/status.sh         ← pending 확인
    ↓
./scripts/collect.sh        ← pending > 0
./scripts/collect.sh --refresh  ← pending = 0
    ↓  (Ctrl+C 로 언제든 중단 가능, 데이터 보존)
./scripts/update.sh         ← build + export + push + Vercel 재배포
```
