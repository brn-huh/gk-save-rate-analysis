# openapi

# MatchDetail API 명세서

넥슨 API에서 제공하는 매치 상세 정보 JSON 데이터의 구조를 마크다운 파일로 정리한 문서입니다. 그대로 복사해서 사용하시면 됩니다.

---

## 1. 매치 기본 정보 (최상위 데이터)

경기 자체의 고유 식별자와 일시, 종류를 나타냅니다.

| 필드명 | 타입 | 설명 | 예시 |
| --- | --- | --- | --- |
| `matchId` | String | 매치 고유 식별자 | `64f0000c00007210005f0000` |
| `matchDate` | String | 매치 일자 (UTC+0 기준) | `2023-10-29T12:22:48` |
| `matchType` | Integer | 매치 종류 (`/metadata/matchtype` API 참고) | `52` |
| `matchInfo` | Array | **매치에 참여한 플레이어별 상세 기록 (아래 2번 참고)** | 배열 데이터 |

---

## 2. 유저별 경기 기록 (`matchInfo`)

경기에 참여한 유저 각각의 기록입니다. 닉네임, 등급과 함께 세부 항목(`matchDetail`, `shoot`, `pass`, `defence`, `player`)들로 나뉩니다.

- **`ouid`** (String): 계정 식별자
- **`nickname`** (String): 유저 닉네임
- **`division`** (Integer): 등급 식별자

### ① 경기 결과 및 종합 (`matchDetail`)

| 필드명 | 타입 | 설명 | 예시 |
| --- | --- | --- | --- |
| `seasonId` | Integer | 시즌 ID | `202311` |
| `matchResult` | String | 매치 결과 (`"승"`, `"무"`, `"패"`) | `"승"` |
| `matchEndType` | Integer | 게임 종료 타입 (`0`: 정상종료, `1`: 몰수승, `2`: 몰수패) | `0` |
| `systemPause` | Integer | 게임 일시정지 수 | `0` |
| `foulinteger` | Integer | 파울 수 | `0` |
| `injury` | Integer | 부상 수 | `1` |
| `redCards` | Integer | 받은 레드카드 수 | `0` |
| `yellowCards` | Integer | 받은 옐로카드 수 | `0` |
| `dribble` | Integer | 총 드리블 거리 (야드) | `78` |
| `cornerKick` | Integer | 코너킥 수 | `0` |
| `possession` | Integer | 점유율 | `46` |
| `OffsideCount` | Integer | 오프사이드 수 | `1` |
| `averageRating` | Number | 경기 평균 평점 | `1.18889` |
| `controller` | String | 사용한 컨트롤러 타입 (`keyboard` / `pad` / `etc`) | `"keyboard"` |

### ② 팀 슈팅 종합 기록 (`shoot`)

| 필드명 | 타입 | 설명 | 필드명 | 타입 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `shootTotal` | Integer | 총 슛 수 | `ownGoal` | Integer | 자책골 수 |
| `effectiveShootTotal` | Integer | 총 유효슛 수 | `shootHeading` | Integer | 헤딩 슛 수 |
| `shootOutScore` | Integer | 승부차기 슛 수 | `goalHeading` | Integer | 헤딩 골 수 |
| `goalTotal` | Integer | 실제 총 골 수 | `shootFreekick` | Integer | 프리킥 슛 수 |
| `goalTotalDisplay` | Integer | 인게임 표기용 골 수 | `goalFreekick` | Integer | 프리킥 골 수 |
| `shootInPenalty` | Integer | 페널티박스 안 슛 수 | `shootOutPenalty` | Integer | 페널티박스 밖 슛 수 |
| `goalInPenalty` | Integer | 페널티박스 안 골 수 | `goalOutPenalty` | Integer | 페널티박스 밖 골 수 |
| `shootPenaltyKick` | Integer | 페널티킥 슛 수 | `goalPenaltyKick` | Integer | 페널티킥 골 수 |

### ③ 슈팅별 상세 정보 (`shootDetail` - 리스트)

경기 중 발생한 모든 개별 슈팅에 대한 상세 기록 배열입니다.

> 💡 **슛 시간(`goalTime`) 계산 규칙**
> 
> - **전반전** ($2^{24} \sim 2^{241} - 1$): 값을 그대로 사용
> - **후반전** ($2^{241} \sim 2^{242} - 1$): 값에서 $2^{24}$ 차감 후 $+ (45 \times 60)$초
> - **연장 전반** ($2^{242} \sim 2^{243} - 1$): 값에서 $2^{24} \times 2$ 차감 후 $+ (90 \times 60)$초
> - **연장 후반** ($2^{243} \sim 2^{244} - 1$): 값에서 $2^{24} \times 3$ 차감 후 $+ (105 \times 60)$초
> - **승부차기** ($2^{244} \sim 2^{245} - 1$): 값에서 $2^{24} \times 4$ 차감 후 $+ (120 \times 60)$초
- **위치 및 종류:**
    - `x` / `y` (Number): 슛 위치 좌표 (전체 경기장 기준)
    - `type` (Integer): 슛 종류 (`1`: normal, `2`: finesse, `3`: header, `4`: lob, `5`: flare, `6`: low, `7`: volley, `8`: free-kick, `9`: penalty, `10`: KNUCKLE, `11`: BICYCLE, `12`: super)
    - `result` (Integer): 슛 결과 (`1`: ontarget, `2`: offtarget, `3`: goal)
- **선수 정보:**
    - `spId` (Integer): 슈팅 선수 고유 식별자 (`/metadata/spid` 참고)
    - `spGrade` / `spLevel` (Integer): 슈팅 선수 강화 등급 / 레벨
    - `spIdType` (Boolean): 임대 선수 여부 (`true`: 임대, `false`: 비임대)
- **어시스트 및 기타:**
    - `assist` (Boolean): 어시스트 받은 골 여부
    - `assistSpId` (Integer): 어시스트 선수 고유 식별자
    - `assistX` / `assistY` (Number): 어시스트 위치 좌표
    - `hitPost` (Boolean): 골포스트 맞춤 여부
    - `inPenalty` (Boolean): 페널티박스 안 슛 여부

### ④ 팀 패스 기록 (`pass`)

| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| `passTry` / `passSuccess` | Integer | 총 패스 시도 / 성공 수 |
| `shortPassTry` / `shortPassSuccess` | Integer | 숏 패스 시도 / 성공 수 |
| `longPassTry` / `longPassSuccess` | Integer | 롱 패스 시도 / 성공 수 |
| `bouncingLobPassTry` / `bouncingLobPassSuccess` | Integer | 바운싱 롭 패스 시도 / 성공 수 |
| `drivenGroundPassTry` / `drivenGroundPassSuccess` | Integer | 드리븐 땅볼 패스 시도 / 성공 수 |
| `throughPassTry` / `throughPassSuccess` | Integer | 스루 패스 시도 / 성공 수 |
| `lobbedThroughPassTry` / `lobbedThroughPassSuccess` | Integer | 로빙 스루 패스 시도 / 성공 수 |

### ⑤ 팀 수비 기록 (`defence`)

| 필드명 | 타입 | 설명 |
| --- | --- | --- |
| `blockTry` / `blockSuccess` | Integer | 블락 시도 / 성공 수 |
| `tackleTry` / `tackleSuccess` | Integer | 태클 시도 / 성공 수 |

---

## 3. 경기 출전 선수 정보 (`player` - 리스트)

경기에 사용된 선수 개개인의 식별 정보와 세부 경기 스탯(`status`)이 담긴 리스트입니다.

- **`spId`** (Integer): 선수 고유 식별자 (`/metadata/spid` API 참고)
- **`spPosition`** (Integer): 선수 포지션 (`/metadata/spposition` API 참고)
- **`spGrade`** (Integer): 선수 강화 등급

### 📊 선수 세부 스탯 (`status`)

| 분류 | 필드명 | 타입 | 설명 |
| --- | --- | --- | --- |
| **공격** | `shoot` / `effectiveShoot` <br> `goal` / `assist` <br> `dribbleTry` / `dribbleSuccess` <br> `dribble` | Integer <br> Integer <br> Integer <br> Integer | 슛 / 유효 슛 수 <br> 득점 / 어시스트 수 <br> 드리블 시도 / 성공 수 <br> 드리블 거리 (야드) |
| **수비** | `intercept` <br> `defending` <br> `blockTry` / `block` <br> `tackleTry` / `tackle` | Integer <br> Integer <br> Integer <br> Integer | 인터셉트 수 <br> 디펜딩 수 <br> 블락 시도 / 성공 수 <br> 태클 시도 / 성공 수 |
| **경합/패스** | `passTry` / `passSuccess` <br> `ballPossesionTry` / `ballPossesionSuc` <br> `aerialTry` / `aerialSuccess` | Integer <br> Integer <br> Integer | 패스 시도 / 성공 수 <br> 볼 소유 시도 / 성공 수 <br> 공중볼 경합 시도 / 성공 수 |
| **기타** | `yellowCards` / `redCards` <br> `spRating` | Integer <br> Number | 옐로카드 / 레드카드 수 <br> **선수 개별 평점** |