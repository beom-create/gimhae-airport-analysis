# API 명세 · 테스트 결과 (Phase 0)

- 확인일: 2026-11-12 (KST), 기준공항 김해 = `PUS`
- 출처: 공공데이터포털 상세 페이지 + `docs/` 활용가이드 (.docx, 텍스트 추출본 .txt), 탑승객 수는 포털 Swagger 명세(`docs/탑승승객수_GW_swagger.json`)
- 테스트 응답: `data/sample/*.json` (`_meta`에 엔드포인트·파라미터·호출시각, 키는 저장 안 함)
- 재실행: `python3 src/api_probe.py`

## 요약

| # | API (포털 정식 이름) | 엔드포인트 | 개발계정 트래픽 | 과거 조회 | 테스트 결과 |
|---|---|---|---|---|---|
| 1 | 한국공항공사_실시간 항공기 운항정보 조회_GW | `B551178/flight-status/depart`, `/arrival` | 5,000/일 | **D-3 ~ D+6** (`searchday`) | D-3 출발 254건·도착 244건, **D-4는 0건**, D+6 158건 (상태 null) |
| 2 | 한국공항공사_전국공항 시간대별 탑승승객 수 조회_GW | `B551178/daily-expect-passenger/info` | 5,000/일 | **당일만 아님**: D-365 조회됨, D+1까지 있음, D+7은 0건 | 하루 약 62행 (06~22시 × 출/도착 × 국내/국제) |
| 3 | 한국공항공사_항공기 운항 스케줄 정보_GW | `B551178/flight-schedule/taxfree-dom`, `/taxfree-int` (그 외 `/dom`, `/int`) | 확인 필요 | 해당 없음 (정기편 기간·요일 정보) | 국내 1,157건, 국제 2,847건. 국제에 2024년 종료 편 섞여 있음 |
| 4 | 기상청_지상(종관, ASOS) 시간자료 조회서비스 | `1360000/AsosHourlyInfoService/getWthrDataList` | 10,000/일 | 1904년~, **종료일은 D-1까지** | 253 김해시·159 부산 모두 24시간 정상 |

## 1. 운항정보 조회 GW — depart / arrival (보험사용 지연결항 결과조회)

요청: `serviceKey`, `pageNo`, `numOfRows`, `searchday`(YYYYMMDD), `from_time`/`to_time`(HHMM, 계획시간 기준), `airport_code`(PUS), `line`(D/I), `flight_id`, `type`(json)

| 필드 | 뜻 | 예 (샘플) |
|---|---|---|
| fid | 편명별 유니크 코드 | 0000000000000013502667 |
| searchday | 조회일 | 20261109 |
| scheduledatetime | 예정시간 (YYYYMMDDHHMM) | |
| estimateddatetime | 실제 또는 변경시간 | 202611090641 |
| flightid / airline | 편명 / 항공사 국문명 | KE1505 / 대한항공 |
| codeshare / masterflightid | 코드쉐어 여부 / 마스터 편명 | N |
| line | 국내/국제 (국문) | 국내 |
| io | O 출발 / I 도착 | O |
| depAirportCode, arrAirportCode(출발편은 `arrvAirportCode`) | 출·도착 공항 IATA | PUS, CJU |
| rmkKor | 상태: 출발·지연·도착·결항 등 | 출발 (D+6은 null) |
| fgenTime | 데이터 생성시간 | |

- `estimateddatetime`의 측정 시점(게이트 출발인지 이륙인지)은 명세에 정의 없음 → 확인 중 (docs/decisions.md).
- 지연 = `estimateddatetime − scheduledatetime`. "실제"인지 "변경 예정"인지 같은 필드에 섞이므로, 지난 날짜(D-1~D-3)를 받아야 확정값.
- 중복 제거: `masterflightid`가 비어 있거나 `flightid`와 같은 행만 실제 운항편. `codeshare=Y`에도 운항사 본편이 포함되므로 codeshare 값만으로 거르면 안 됨 (docs/decisions.md).
- 그 외 오퍼레이션: `/info`(실시간, 날짜 지정 없음), `/detail`, `/taxfree` — 과거 조회가 안 되므로 쓰지 않음.

## 2. 시간대별 탑승승객 수 GW — info

요청: `serviceKey`, `pageNo`, `numOfRows`, `schStDate`/`schEdDate`(YYYYMMDD, 기간 조회 가능), `schAirport`(PUS), `schTof`(D/I), `schAOD`(D 출발/A 도착), `type`

| 필드 | 뜻 | 예 |
|---|---|---|
| ARP | 공항 | PUS |
| SDT | 운항일자 | 20261112 |
| HH | 시간대 (06~22) | 22 |
| AOD | D 출발 / A 도착 | D |
| TOF | D 국내 / I 국제 | I |
| PCT | 시간대별 탑승 승객 수 | 987 |

- 7일 기간 조회 → 440행. 35일 기간 요청은 1회 연결 오류(원인 불명, 일시적일 수 있음) → 안전하게 1주 단위로 나눠 받기.
- **확인 필요**: 엔드포인트 이름이 `daily-expect-passenger`(예상 승객)이고 D+1 값이 있음 → 과거 날짜 값이 **실적인지 예상치인지** 명세에 없음. 같은 날짜를 당일·다음날에 다시 받아 값이 바뀌는지 비교해서 판단.

## 3. 운항 스케줄 GW — taxfree-dom / taxfree-int

요청: `depCityCode`/`arrvCityCode`(PUS), `flightId`, `schAirLine`, `fgenTime` 등
필드: `fid, flightid, airline, airlineCode, st(출발시간 HHMM), firstdate~lastdate(정기운항 기간), ynmon~ynsun(요일별 취항), depcitycode, arrvcitycode, fgenType(I/U/D)`

- 보조용: 요일·시간대별 **예정 출발편 수**(밀집도) 계산. `lastdate`가 지난 편은 제외해야 함.
- 국제선 응답 필드명이 `airlinecode`(소문자)로 국내선 `airlineCode`와 다름.

## 4. 기상청 ASOS 시간자료

요청: `ServiceKey`, `dataType`(JSON), `dataCd=ASOS`, `dateCd=HR`, `startDt`/`startHh`, `endDt`/`endHh`(D-1까지), `stnIds`
필드(사용 예정): `tm`(일시), `ta`(기온), `rn`(강수량), `ws`(풍속), `wd`(풍향), `vs`(시정), `hm`(습도), `dsnw`(적설) + 각 `*QcFlg`

- 관측소 후보: **253 김해시**, **159 부산** (가이드 첨부 지점표). 김해공항(부산 강서구)과의 거리로 하나를 고르거나 둘 다 받기 — 확인 필요.
- `rn`: 253은 비 안 온 시간이 **빈값 ""**, 159는 **"0.0"** → 빈값을 0으로 볼지 전처리 규칙 필요. 겨울철 3시간 간격 표시 방식도 실제 강수일 데이터로 다시 확인.

## 5. 공항 소요시간 정보 GW (2026-11-19 추가) — `B551178/airport-process-time/v2`

요청: `serviceKey`, `pageNo`, `numOfRows`, `type`, `cond[IATA_APCD::EQ]`(PUS — 필터가 무시되고 5개 공항이 다 옴). **날짜 파라미터 없음 → 실시간만**
필드: `IATA_APCD`, `PRC_HR`(처리시간 HH:MM), `OPR_STS_CD`(1 운영중/0 비운영), `STY_TCT_AVG_A~D`(1~4구간 평균 소요, 초), `STY_TCT_AVG_ALL`(전체, 초)
- 구간: 1 체크인→신분확인, 2 신분확인→보안검색, 3 보안검색→탑승, 4 탑승→출발. 국내선만 (김포·제주는 `/v1`)
- 샘플: `data/sample/proctime_PUS.json` (23:00 비운영, 3구간 120초)

## 6. 공항 혼잡도 정보 GW (2026-11-19 추가) — `B551178/airport-congestion/v2`

요청: `serviceKey`, `pageNo`(필수), `numOfRows`(필수), `type`. **날짜 파라미터 없음 → 실시간만**
필드: `IATA_APCD`, `PRC_HR`, `CGDR_A~C_LVL`(1~3구간), `CGDR_ALL_LVL`(전체) — 1 원활, 2 보통, 3 혼잡, 4 매우혼잡 (0은 비운영 추정, 확인 필요)
- 샘플: `data/sample/congestion_v2.json`
