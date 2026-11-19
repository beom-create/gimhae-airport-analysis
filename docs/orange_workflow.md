# Orange3 재현 가이드 (Phase 4 지연 예측)

Python(`src/model.py`)과 **같은 데이터·같은 train/test 분할**로 Orange3에서 같은 흐름을 재현한다.
보고서에 위젯 화면 캡처를 넣을 때 사용.

## 파일 (`data/processed/orange/`, `src/model.py` 실행 시 자동 생성)
| 파일 | 내용 |
|---|---|
| `departures_train.tab` | 학습용 (앞 75% 날짜) |
| `departures_test.tab` | 테스트용 (뒤 25% 날짜) |
| `departures_all.tab` | 전체 (Rank·분포 확인용) |

- Orange 3줄 헤더 형식이라 **타입·역할이 자동 지정**됨: `delayed`(지연/정상) = 타겟(class), `date`·`flightid` = meta, 나머지 = feature
- 누수 컬럼(`delay_min`, 실제 시각, 운항 상태)은 **파일에 아예 없음**
- 결측은 `?` (Orange 기본 결측 표기)

## 위젯 흐름

```
File(all) ──► Feature Statistics / Distributions      ← feature별 지연 분포 확인
File(all) ──► Rank                                    ← feature별 타겟 영향 (Information Gain, Gini, χ²)
File(train) ─► Select Columns ─┬─► Logistic Regression ─► Nomogram    ← 가중치(계수) 설명
                               ├─► Random Forest
                               └─► Test and Score ◄── File(test)       ← "Test on test data"
                                         └─► Confusion Matrix
```

### 1. feature별 타겟 영향 비율 — Rank
- 입력: `departures_all.tab`
- Scoring: **Information Gain**, **Gini**, **χ²** 체크 → 점수 순 정렬 화면 캡처
- Python 대응: `outputs/model/feature_target_rates.csv`(값별 지연율), `rf_importance_*.csv`(RF 중요도 %)

### 2. Feature 조정 전·후 — Select Columns
- **조정 전**: 모든 feature 사용. 단, 같은 정보를 두 형태로 넣지 않도록 `time_band`, `dow_name`, `rain_3h_state`는 **Ignored**
- **조정 후** (`outputs/model/feature_adjustment_log.txt`와 같게):
  - Ignored로 옮김: `sched_hour`, `dow`, `rain_3h_mm`, `rain_3h_missing`, 그리고 로그에서 "제외"된 feature (현재: `is_holiday`, `sched_dep_same_hour`, `is_weekend`)
  - Features에 둠: `time_band`, `dow_name`, `rain_3h_state` 포함 나머지
  - 드문 항공사·목적지 "기타" 묶기는 Orange에서 생략 가능 (보고서에 "Python에서만 적용" 표기). 하려면 **Edit Domain** 위젯에서 값 병합
- 12월 최종 실행 후 로그의 제외 목록이 바뀔 수 있으니 로그 파일 기준으로 맞출 것

### 3. 모델 — 불균형 처리
- **Logistic Regression**: Regularization L2, C=1, **Balance class distribution 체크**
- **Random Forest**: Trees 500, Min. leaf 3, **Balance class distribution 체크**
- Python의 `class_weight="balanced"`와 같은 의미

### 4. 평가 — Test and Score
- 왼쪽 Sampling: **Test on test data** (File(test) 연결) → Python과 같은 분할
- Target class: **지연** 으로 선택 (평균이 아니라 지연 클래스 기준)
- 표에서 **Prec, Recall, F1** 열 캡처 → 조정 전/후 각각
- 참고: Orange는 판정 기준값 0.5 고정 → Python 표의 `f1_at_0.5` 열과 비교. Python은 교차검증으로 기준값을 추가 조정한 값(`f1`)도 제공

### 5. 가중치 설명 — Nomogram
- Logistic Regression 출력 → Nomogram: 각 feature가 "지연" 확률을 얼마나 올리고 내리는지 점수 막대로 표시
- Python 대응: `outputs/model/lr_coefficients_after.csv`, `lr_coefficients_after.png`

## 주의
- Orange의 One-hot·정규화 방식이 scikit-learn과 조금 달라 숫자가 완전히 같지는 않다. 보고서에는 "같은 흐름, 수치는 Python 기준"으로 적는다.
