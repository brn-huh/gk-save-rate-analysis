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

### DB 백업과 복구

수집을 이어서 복구하는 데 필요한 원본은 `data/` 폴더다. 현재 폴더에는 보통 아래
두 파일이 있으며, DuckDB의 작업 로그까지 보존하려면 폴더 전체를 백업한다.

```text
data/gksave.duckdb
data/gksave.duckdb.wal
```

백업할 때는 수집·빌드·export 프로세스를 먼저 종료한 뒤 `data/` 폴더를 개인 보관
위치에 복사한다. 실행 중인 DB 파일을 복사하면 마지막 작업이 빠질 수 있다.

복구할 때는 GitHub에서 소스를 다시 받은 뒤 백업한 `data/` 폴더를 프로젝트 루트에
그대로 되돌려 놓고 `./scripts/status.sh`로 매치 수와 pending 유저를 확인한다. 이후
`./scripts/collect.sh`를 실행하면 `frontier`의 pending 상태에서 수집을 이어간다.

`.env.local`의 `NEXON_API_KEY`와 수집 설정은 DB에 들어있지 않으므로 필요하면 별도로
안전하게 백업하거나 다시 작성해야 한다. `out/`은 배포용 결과물이라 복구에 필요하지
않고 `./scripts/update.sh`로 다시 만들 수 있다. DB와 `.env.local`에는 OUID·API 키가
있을 수 있으므로 GitHub나 공개 백업에는 올리지 않는다.

### 수집 속도 설정

넥슨 Open API 한도는 **초당 최대 500건, 일일 최대 2,000만 건**이다.
`GKSAVE_RATE`는 초당 상한만 제어하고 일일 사용량은 추적하지 않는다.

이론상 최대는 `GKSAVE_RATE=500`이지만, 롤링 한도 여유를 둔 운영값은 `450` 정도다.
`RATE=500`이면 일일 한도를 약 11시간 만에 소진한다. 동시성은 응답 지연에 맞춰
올려야 하며, 지연 5.3초 기준 `RATE × 지연시간 ≈ 2,650`이 포화 조건이다.
`GKSAVE_CONCURRENCY`는 앱의 동시 요청 수뿐 아니라 HTTPX의 실제 연결 상한과
keep-alive 연결 상한에도 사용된다. 따라서 값을 크게 올리면 연결 대기 병목은 줄지만
CPU·메모리·발열과 429 가능성이 함께 증가한다.

발열을 우선한 시작값은 아래와 같다. 이후 10분 평균·429 로그를 보며
조금씩 조정한다.

```bash
GKSAVE_RATE=400 GKSAVE_CONCURRENCY=800 GKSAVE_USER_WORKERS=300 \
  gksave collect --refresh --max-matches 50000
```

`GKSAVE_CONCURRENCY=360`은 응답 지연 5.3초에서 약 68 req/s까지만 채울 수 있다.
`GKSAVE_USER_WORKERS`는 동시에 탐색할 유저 수이고, `GKSAVE_MATCH_QUEUE`는 대기 중인
match 작업 큐 크기다. 둘은 연결 수와 별개이며 `.env.local`에서 조정한다.

수집 중 로그는 10분마다 신규 매치 처리량과 CPU·macOS 메모리 여유율 평균을
별도 구분자로 출력한다. macOS 기본 명령만으로는
기기별 CPU·RAM 온도를 안정적으로 읽을 수 없어 온도는 기록하지 않는다.

429가 발생하면 요청은 최대 6회까지 재시도되고, 레이트는 즉시 절반으로 감속된다.
수집이 끝나면 429·5xx 누적 횟수와 감속된 최종 레이트를 확인한다.

> **선수 부가정보(급여·OVR·체격·시즌엠블럼)는 `update.sh` 에 없다.** 새 GK 카드가
> 생겼을 때만 별도로 `gksave playerinfo` 를 한 번 돌려 `player_info`·`season_img` 를 채운다.
> (fc-info 에서 우리 GK 중 캐시에 없는 것만 받고, 이미 받은 건 다시 안 받는다.)
>
> **국적·클럽·특성은 `playerinfo` 에 없다.** `gksave playerdetail` 이 따로 받는다
> (카드당 요청 1회 + 지연 1초라 오래 걸린다).
>
> 두 명령 모두 **조회 시도를 `fc_fetch_log` 에 기록**한다. fc-info 에 없는 카드나 특성이
> 0개인 카드는 저장할 행이 없어서, 기록이 없으면 매 실행마다 다시 받게 된다.
> 기록은 `config.FC_RECHECK_DAYS`(30일) 뒤 만료된다 — fc-info 가 신규 카드를 늦게
> 올리므로 영구 차단은 하지 않는다. 성공한 조회만 기록하므로 일시적 실패는 다음에 재시도된다.

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
| `./scripts/collect.sh --seed-nicknames "닉1,닉2"` | 시드 유저를 추가해 수집 |
| `./scripts/collect.sh --max 50000` | 수집량 직접 지정 |
| `./scripts/collect.sh --day 7` | 수집 창 직접 지정 (기본 1일) |
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

새로운 시작점(활동 중인 유저)을 추가하려면:

```bash
./scripts/collect.sh --seed-nicknames "닉1,닉2"
```

이미 큐에 등록된 닉네임을 다시 넣으면 중복으로 추가하지 않고 `이미 등록됨`으로
기록한다.

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

### 수집 창 직접 지정 (기본 1일)
```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/collect.sh --day 7     # 오늘로부터 7일 전 이후 매치만
```

기본은 **1일** — 매일 한 번 돌리는 전제다. 화면(통계) 창은 그대로 롤링 30일이다
(`update.sh` 의 `export --days 30`). DB 에 쌓인 과거 매치는 지워지지 않으니
수집 창을 좁혀도 리더보드 30일 집계는 그대로 나온다.

**하루 이상 걸렀으면 그만큼 넓혀서 보충해야 한다.** 3일 쉬었으면 `--day 4`,
일주일 쉬었으면 `--day 8` 식으로. 안 그러면 그 기간 매치가 영구히 비고,
나중에 메우려면 이미 done 인 유저를 다시 열어야 해서 `--refresh` 까지 필요해진다.

> **순위 추이가 수집 규칙성에 묶인다.** 페이지의 `변동` 뱃지와 추이 차트는 롤링 30일 창을
> 3일 간격으로 소급 계산한다(`agg.rank_timeseries`). 수집을 거른 날은 그 구간 표본이 얇아져
> **그 시점의 순위가 통째로 튄다.** 매일 돌리면 문제없지만, 며칠 거른 뒤에는 `--day` 를 넓혀
> 구멍을 메우고 `update.sh` 를 돌려야 추이가 정상으로 돌아온다.
> 차트의 마지막 점은 구조적으로 늘 과소표집 상태라 빨간 점 + "수집 중인 구간" 표시가 붙는다.

### 옵션 조합 예시
```bash
cd /Users/jwkim/workspace/gk-save-rate-analysis
./scripts/collect.sh --refresh --max 50000
./scripts/collect.sh --day 8 --max 200000     # 일주일 거른 뒤 보충
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
