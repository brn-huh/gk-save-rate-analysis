# 운영 가이드 (수집 → 빌드 → 배포)

## 딱 이것만 하면 된다!

```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/collect.sh
```

- 수집이 끝나면 **묻지 않고 자동으로** `update.sh`(증분 빌드 + export)까지 실행된다.
  (수집만 하려면 `--no-update`)
- 완료 후 결과 화면 확인:

```bash
open out/index.html
```

> **선수 부가정보(급여·OVR·체격·시즌엠블럼)는 `update.sh` 에 없다.** 새 GK 카드가
> 생겼을 때만 별도로 `gksave playerinfo` 를 한 번 돌려 `player_info`·`season_img` 를 채운다.
> (fc-info 에서 우리 GK 중 캐시에 없는 것만 받고, 이미 받은 건 다시 안 받는다.)

---

## 컴퓨터 껐다가 다시 시작할 때

터미널 새로 열고 아래만 실행하면 됩니다. 데이터는 그대로 보존돼 있음.

```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/status.sh
```

→ 매치 수·pending 확인 후 이어서 수집하려면:

```bash
./scripts/collect.sh           # pending 있으면
./scripts/collect.sh --refresh # pending 0이면
```

→ 배포만 갱신하려면 (수집 없이 현재 데이터로):

```bash
./scripts/update.sh
```

---

## 스크립트 한눈에 보기

| 스크립트 | 하는 일 |
|---|---|
| `./scripts/status.sh` | 현재 매치 수 · 대기 유저(pending) 확인 |
| `./scripts/collect.sh` | 수집 후 **묻지 않고 자동으로** update.sh(build+export) 실행 |
| `./scripts/collect.sh --refresh` | 수집 (pending 없을 때, 새 경기 보충) + 자동 update |
| `./scripts/collect.sh --max 50000` | 수집량 직접 지정 |
| `./scripts/collect.sh --no-update` | 수집만, update.sh 건너뜀 |
| `./scripts/build.sh` | 증분 빌드 (수집한 것만 파싱 — 빠름) |
| `./scripts/build.sh --full` | 전체 재파싱 (파싱 로직 바뀌었을 때만) |
| `./scripts/update.sh` | 증분 빌드 + export 실행 |

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

> 수동 트리거 전용이다. cron·launchd·nohup 으로 자동화하지 않는다 —
> DuckDB 는 단일 파일이라 쓰기 프로세스가 하나뿐이고, 모듈을 고치는 중에
> 백그라운드 수집이 돌면 `build`·`export` 와 락이 충돌한다.
>
> 락이 겹치면 트레이스백 대신 "다른 프로세스가 DB 를 쓰고 있다" 안내가 뜨고
> 종료코드 1 로 끝난다. `update.sh` 는 자동으로 건너뛴다.

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

## build.sh — 빌드

### 증분 빌드 (평소, 수집 후)
```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/build.sh
```
- 수집한 새 매치만 파싱 → 3만 수집이면 ~30초
- 매치 총량이 늘어나도 빌드 시간은 수집량에만 비례

### 전체 재파싱 (파싱 로직이 바뀌었을 때만)
```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/build.sh --full
```
- 전체를 처음부터 다시 파싱 — 18.9만 기준 약 3분 40초
- 코드 업데이트 후 결과가 이상할 때 사용

> 보통은 `update.sh`로 충분. `build.sh`를 단독으로 쓸 일은 거의 없음.

---

## update.sh — 빌드 + export

수집이 끝난 후 실행. 증분 빌드 → export 까지 자동 실행한다.

```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/update.sh
```

출력 예시:
```
=== build (증분) ===
[증분 빌드] raw_match 189663건 → 파싱: 매치 21020, GK출전 38450, 슛 201124 ...
=== export ===
✓ build/export 완료 (git commit/push는 수동 진행)
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
2. collect 종료 → update.sh 자동 실행 (증분 빌드 + export)

※ 새 GK 카드가 생겼을 때만:  gksave playerinfo  ← 급여·체격·시즌엠블럼 채우고 export 재실행
※ 파싱 로직이 바뀐 경우에만:  ./scripts/build.sh --full  ← 전체 재파싱 후 update.sh
```
