"""Phase 4: 출발 지연 예측 모델 (로지스틱 회귀 + 랜덤포레스트), feature 조정 전·후 비교.

입력: data/processed/departures.csv (src/preprocess.py 결과)
출력: outputs/model/  — 성능 비교표, feature 영향 비율, 회귀 계수(가중치), 차트 PNG, 보고서 초안
     data/processed/orange/ — Orange3용 .tab (같은 train/test 분할)

원칙
- 타겟: delayed (출발 지연, 구 기준 국내 30분·국제 60분 초과, 활주로 기준 측정과 일치)
- feature: 운항 전에 알 수 있는 값만. 실제·변경 시각, 운항 상태, 지연 분(delay_min)은 누수라 제외
- 분할: 날짜 순서로 앞 75% 학습, 뒤 25% 테스트 (미래 데이터로 과거를 맞히는 누수 방지)
- 불균형: class_weight="balanced". 평가는 지연(1) 클래스 기준 Precision / Recall / F1
- feature 조정은 학습 데이터만 보고 결정 (테스트 정보 누수 방지)

실행: .venv/bin/python src/model.py
"""
import json
import warnings

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from sklearn.compose import ColumnTransformer  # noqa: E402
from sklearn.ensemble import RandomForestClassifier  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import average_precision_score, confusion_matrix, f1_score, precision_score, recall_score  # noqa: E402
from sklearn.model_selection import StratifiedKFold, cross_val_predict  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # noqa: E402

import config as C  # noqa: E402
from common import DATA, ROOT, now_kst  # noqa: E402

warnings.filterwarnings("ignore", category=UserWarning)
from matplotlib import font_manager  # noqa: E402

_fonts = {f.name for f in font_manager.fontManager.ttflist}
plt.rcParams["font.family"] = [f for f in ("AppleGothic", "NanumGothic", "Malgun Gothic") if f in _fonts] or ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

SRC = DATA / "processed" / "departures.csv"
OUT = ROOT / "outputs" / "model"
ORANGE = DATA / "processed" / "orange"
TARGET = "delayed"
LEAKAGE = ["delay_min", "delayed_gate15_ref"]  # 타겟을 만든 값 → 절대 feature로 쓰지 않음
META = ["date", "flightid"]

# 조정 전: 추출한 feature 전부 (원형 그대로)
BEFORE_NUM = ["sched_hour", "dow", "is_weekend", "is_holiday", "n_codeshare", "sched_dep_same_hour",
              "sched_arr_same_hour", "exp_pax_line_hour", "exp_pax_total_hour", "ta", "ws", "vs", "hm",
              "rain_3h_mm", "rain_3h_missing"]
BEFORE_CAT = ["line", "airline", "dest"]

FEATURE_KO = {
    "sched_hour": "예정 출발 시각(시)", "time_band": "시간대 구간", "dow": "요일(숫자)", "dow_name": "요일",
    "is_weekend": "주말 여부", "is_holiday": "공휴일 여부", "n_codeshare": "코드쉐어 판매 편명 수",
    "sched_dep_same_hour": "같은 시각대 예정 출발편 수", "sched_arr_same_hour": "같은 시각대 예정 도착편 수",
    "exp_pax_line_hour": "예상 탑승객 수(같은 시각대·국내/국제)", "exp_pax_total_hour": "예상 탑승객 수(같은 시각대 전체)",
    "ta": "기온(1시간 전)", "ws": "풍속(1시간 전)", "vs": "시정(1시간 전)", "hm": "습도(1시간 전)",
    "rain_3h_mm": "직전 3시간 강수량", "rain_3h_missing": "강수 기록 결측", "rain_3h_state": "직전 3시간 강수 상태",
    "line": "국내/국제", "airline": "항공사", "dest": "목적지",
}


# ── 공통 ────────────────────────────────────────────────────────
def split_by_date(df):
    dates = sorted(df["date"].unique())
    n_test = max(1, int(round(len(dates) * C.TEST_DAYS)))
    test_dates = set(dates[-n_test:])
    return df[~df["date"].isin(test_dates)].copy(), df[df["date"].isin(test_dates)].copy(), dates, sorted(test_dates)


def make_pipe(kind, num, cat):
    num_steps = [("imp", SimpleImputer(strategy="median"))]
    if kind == "lr":
        num_steps.append(("sc", StandardScaler()))
    pre = ColumnTransformer([
        ("num", Pipeline(num_steps), num),
        ("cat", Pipeline([("imp", SimpleImputer(strategy="most_frequent")),
                          ("oh", OneHotEncoder(handle_unknown="ignore"))]), cat),
    ])
    if kind == "lr":
        model = LogisticRegression(class_weight="balanced", max_iter=2000, C=1.0)
    else:
        model = RandomForestClassifier(n_estimators=500, min_samples_leaf=3, class_weight="balanced_subsample",
                                       random_state=C.RANDOM_STATE, n_jobs=-1)
    return Pipeline([("pre", pre), ("model", model)])


def feature_names(pipe, num, cat):
    oh = pipe.named_steps["pre"].named_transformers_["cat"].named_steps["oh"]
    return list(num) + [f"{c}={v}" for c, vals in zip(cat, oh.categories_) for v in vals]


def base_feature(col, num, cat):
    return col if col in num else col.split("=", 1)[0]


def scores(y, pred, proba):
    return {"precision": precision_score(y, pred, zero_division=0), "recall": recall_score(y, pred, zero_division=0),
            "f1": f1_score(y, pred, zero_division=0),
            "pr_auc": average_precision_score(y, proba) if y.sum() else float("nan"),
            "tn_fp_fn_tp": confusion_matrix(y, pred, labels=[0, 1]).ravel().tolist()}


def tune_threshold(y, proba):
    """F1이 가장 높은 판정 기준값. 불균형 데이터에서 0.5 고정은 지연편을 놓치기 쉬움."""
    grid = np.arange(0.05, 0.96, 0.01)
    f1s = [f1_score(y, (proba >= t).astype(int), zero_division=0) for t in grid]
    i = int(np.argmax(f1s))
    return float(round(grid[i], 2)), f1s[i]


def evaluate(kind, train, test, num, cat):
    """불균형 처리 = class_weight(balanced) + 판정 기준값 조정.
    기준값은 학습 데이터 5겹 교차검증 예측확률로만 정하고, 테스트에는 그대로 적용 (테스트 누수 없음)."""
    X, y = train[num + cat], train[TARGET]
    k = int(min(5, y.sum()))
    thr, cv_f1 = 0.5, float("nan")
    if k >= 2:
        oof = cross_val_predict(make_pipe(kind, num, cat), X, y, method="predict_proba",
                                cv=StratifiedKFold(k, shuffle=True, random_state=C.RANDOM_STATE))[:, 1]
        thr, cv_f1 = tune_threshold(y.values, oof)
    pipe = make_pipe(kind, num, cat).fit(X, y)
    proba = pipe.predict_proba(test[num + cat])[:, 1]
    yt = test[TARGET].values
    res = {"threshold": thr, **scores(yt, (proba >= thr).astype(int), proba),
           "f1_at_0.5": f1_score(yt, (proba >= 0.5).astype(int), zero_division=0), "cv_f1_train": cv_f1}
    return pipe, res


def importance_table(pipe, num, cat):
    """RF 불순도 감소 중요도를 원래 feature 단위로 합쳐 100% 기준 비율로."""
    names = feature_names(pipe, num, cat)
    imp = pipe.named_steps["model"].feature_importances_
    t = pd.DataFrame({"column": names, "importance": imp})
    t["feature"] = [base_feature(c, num, cat) for c in names]
    g = t.groupby("feature")["importance"].sum().sort_values(ascending=False)
    out = (g / g.sum() * 100).round(2).rename("importance_share_pct").reset_index()
    out.insert(1, "feature_ko", out["feature"].map(FEATURE_KO))
    return out


def coef_table(pipe, num, cat):
    names = feature_names(pipe, num, cat)
    coef = pipe.named_steps["model"].coef_[0]
    t = pd.DataFrame({"column": names, "coef": coef.round(4), "odds_ratio": np.exp(coef).round(3)})
    t["feature"] = [base_feature(c, num, cat) for c in names]
    t["feature_ko"] = t["feature"].map(FEATURE_KO)
    t["note"] = ["표준화 1단위(1 표준편차) 증가 시" if c in num else "해당 범주일 때" for c in names]
    return t.reindex(t["coef"].abs().sort_values(ascending=False).index)


# ── feature별 타겟 영향 비율 (지연율 표) ─────────────────────────
def target_rates(df):
    rows = []
    base = df[TARGET].mean()
    cats = ["line", "airline", "dest", "time_band", "dow_name", "is_weekend", "is_holiday", "rain_3h_state"]
    nums = ["sched_dep_same_hour", "sched_arr_same_hour", "exp_pax_line_hour", "exp_pax_total_hour",
            "n_codeshare", "ta", "ws", "vs", "hm"]
    for c in cats:
        for v, g in df.groupby(c):
            rows.append((c, str(v), len(g), g[TARGET].sum(), g[TARGET].mean()))
    for c in nums:
        b = pd.qcut(df[c], q=4, duplicates="drop")
        for v, g in df.groupby(b, observed=True):
            rows.append((c, f"{v.left:g}~{v.right:g}", len(g), g[TARGET].sum(), g[TARGET].mean()))
    t = pd.DataFrame(rows, columns=["feature", "value", "n", "delayed_n", "delay_rate"])
    t.insert(1, "feature_ko", t["feature"].map(FEATURE_KO))
    t["lift_vs_overall"] = (t["delay_rate"] / base).round(2)  # 전체 지연율 대비 배수
    t["delay_rate"] = t["delay_rate"].round(4)
    return t, base


# ── feature 조정 (학습 데이터로만 결정) ─────────────────────────
def adjust_features(train, imp_before):
    log, num, cat = [], [], []
    # 1) 비선형 시각 → 구간화, 요일 숫자 → 범주 (로지스틱 회귀가 직선 관계를 가정하므로)
    log.append("예정 출발 시각(sched_hour) → 시간대 구간(time_band) 범주로 변환")
    log.append("요일 숫자(dow) → 요일 범주(dow_name)로 변환")
    cand_num = [c for c in BEFORE_NUM if c not in ("sched_hour", "dow")]
    cand_cat = BEFORE_CAT + ["time_band", "dow_name"]
    # 2) 강수: 결측 지표 + 양 대신 3분류 상태 범주
    cand_num = [c for c in cand_num if c not in ("rain_3h_mm", "rain_3h_missing")]
    cand_cat.append("rain_3h_state")
    log.append("직전 3시간 강수량·결측 지표 → 강수 상태 3분류(비/없음/기록결측)로 통합")
    # 3) 상수 컬럼 제거
    for c in list(cand_num):
        if train[c].nunique(dropna=True) <= 1:
            cand_num.remove(c)
            log.append(f"{c}: 학습 데이터에서 값이 하나뿐(상수) → 제외")
    # 4) 다중공선성: |r| > 0.8 인 쌍은 RF 중요도가 낮은 쪽 제외
    share = dict(zip(imp_before["feature"], imp_before["importance_share_pct"]))
    corr = train[cand_num].corr().abs()
    for i, a in enumerate(cand_num):
        for b in cand_num[i + 1:]:
            if a in cand_num and b in cand_num and corr.loc[a, b] > 0.8:
                drop = a if share.get(a, 0) < share.get(b, 0) else b
                cand_num.remove(drop)
                log.append(f"{a} ↔ {b} 상관 {corr.loc[a, b]:.2f} > 0.8 → 중요도 낮은 {drop} 제외")
    # 5) 조정 전 RF 중요도 2% 미만 수치 feature 제외
    for c in list(cand_num):
        if share.get(c, 0) < 2.0:
            cand_num.remove(c)
            log.append(f"{c}: 조정 전 RF 중요도 {share.get(c, 0):.1f}% < 2% → 제외")
    num, cat = cand_num, cand_cat
    # 6) 드문 범주 묶기 (항공사·목적지)
    rare = {}
    for c in ("airline", "dest"):
        vc = train[c].value_counts()
        rare[c] = set(vc[vc < C.RARE_MIN_COUNT].index)
        if rare[c]:
            log.append(f"{c}: 학습 데이터 {C.RARE_MIN_COUNT}편 미만 {len(rare[c])}개 범주 → '기타'로 묶음")
    return num, cat, rare, log


def apply_rare(df, rare):
    df = df.copy()
    for c, vals in rare.items():
        df[c] = df[c].where(~df[c].isin(vals), "기타")
    return df


# ── Orange3 내보내기 (3줄 헤더 .tab) ────────────────────────────
def to_orange_tab(df, path, num, cat):
    cols = META + num + cat + [TARGET]
    d = df[cols].copy()
    d[TARGET] = d[TARGET].map({1: "지연", 0: "정상"})
    types = ["string"] * len(META) + ["continuous"] * len(num) + ["discrete"] * (len(cat) + 1)
    flags = ["meta"] * len(META) + [""] * (len(num) + len(cat)) + ["class"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\t".join(cols) + "\n" + "\t".join(types) + "\n" + "\t".join(flags) + "\n")
        d.to_csv(f, sep="\t", header=False, index=False, na_rep="?")


# ── 차트 ────────────────────────────────────────────────────────
def bar(t, x, y, title, path, color="#1f4e79"):
    t = t.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(7, 0.35 * len(t) + 1.2))
    ax.barh(t[y], t[x], color=color)
    ax.set_title(title, fontsize=11)
    ax.tick_params(labelsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(SRC)
    df["rain_3h_mm"] = df["rain_3h_mm"]  # 결측은 파이프라인에서 중앙값 대치 + rain_3h_missing 지표
    train, test, dates, test_dates = split_by_date(df)
    info = {"run_at_kst": now_kst().isoformat(timespec="seconds"), "rows": len(df),
            "period": f"{dates[0]} ~ {dates[-1]} ({len(dates)}일)",
            "train": f"{len(train)}편 ({dates[0]} ~ {sorted(train['date'].unique())[-1]}), 지연 {int(train[TARGET].sum())}편",
            "test": f"{len(test)}편 ({test_dates[0]} ~ {test_dates[-1]}), 지연 {int(test[TARGET].sum())}편",
            "delay_rate": round(df[TARGET].mean(), 4), "threshold": C.DELAY_THRESHOLD_MIN}
    print(json.dumps(info, ensure_ascii=False, indent=1))

    # feature별 타겟 영향 비율
    rates, base = target_rates(df)
    rates.to_csv(OUT / "feature_target_rates.csv", index=False, encoding="utf-8-sig")

    results, logs = [], []
    # 조정 전
    pipes = {}
    for kind, name in (("lr", "로지스틱 회귀"), ("rf", "랜덤포레스트")):
        pipe, r = evaluate(kind, train, test, BEFORE_NUM, BEFORE_CAT)
        pipes[("before", kind)] = pipe
        results.append({"단계": "조정 전", "모델": name, "feature 수": len(BEFORE_NUM) + len(BEFORE_CAT), **r})
    imp_before = importance_table(pipes[("before", "rf")], BEFORE_NUM, BEFORE_CAT)

    # 조정
    num, cat, rare, logs = adjust_features(train, imp_before)
    train_a, test_a = apply_rare(train, rare), apply_rare(test, rare)
    for kind, name in (("lr", "로지스틱 회귀"), ("rf", "랜덤포레스트")):
        pipe, r = evaluate(kind, train_a, test_a, num, cat)
        pipes[("after", kind)] = pipe
        results.append({"단계": "조정 후", "모델": name, "feature 수": len(num) + len(cat), **r})
    imp_after = importance_table(pipes[("after", "rf")], num, cat)

    # 저장
    res = pd.DataFrame(results)
    for c in ("precision", "recall", "f1", "pr_auc", "f1_at_0.5", "cv_f1_train"):
        if c in res:
            res[c] = res[c].astype(float).round(3)
    res.to_csv(OUT / "metrics_comparison.csv", index=False, encoding="utf-8-sig")
    imp_before.to_csv(OUT / "rf_importance_before.csv", index=False, encoding="utf-8-sig")
    imp_after.to_csv(OUT / "rf_importance_after.csv", index=False, encoding="utf-8-sig")
    coef_b = coef_table(pipes[("before", "lr")], BEFORE_NUM, BEFORE_CAT)
    coef_a = coef_table(pipes[("after", "lr")], num, cat)
    coef_b.to_csv(OUT / "lr_coefficients_before.csv", index=False, encoding="utf-8-sig")
    coef_a.to_csv(OUT / "lr_coefficients_after.csv", index=False, encoding="utf-8-sig")
    (OUT / "feature_adjustment_log.txt").write_text("\n".join(logs), encoding="utf-8")
    (OUT / "run_info.json").write_text(json.dumps({**info, "features_after": {"num": num, "cat": cat}},
                                                  ensure_ascii=False, indent=1), encoding="utf-8")

    # 차트
    bar(imp_before.assign(label=imp_before["feature_ko"]), "importance_share_pct", "label",
        "랜덤포레스트 feature 중요도 (조정 전, %)", OUT / "rf_importance_before.png")
    bar(imp_after.assign(label=imp_after["feature_ko"]), "importance_share_pct", "label",
        "랜덤포레스트 feature 중요도 (조정 후, %)", OUT / "rf_importance_after.png")
    ca = coef_a.head(15).copy()
    ca["label"] = ca["column"].map(lambda s: FEATURE_KO.get(s, s.replace(s.split("=")[0], FEATURE_KO.get(s.split("=")[0], s.split("=")[0]), 1)))
    fig, ax = plt.subplots(figsize=(7, 0.35 * len(ca) + 1.2))
    ca = ca.iloc[::-1]
    ax.barh(ca["label"], ca["coef"], color=["#c0392b" if v > 0 else "#2e86c1" for v in ca["coef"]])
    ax.axvline(0, color="#555", lw=0.8)
    ax.set_title("로지스틱 회귀 계수 (조정 후, 빨강=지연 가능성↑)", fontsize=11)
    ax.tick_params(labelsize=8)
    fig.tight_layout(); fig.savefig(OUT / "lr_coefficients_after.png", dpi=150); plt.close(fig)
    tb = rates[rates["feature"] == "time_band"]
    fig, ax = plt.subplots(figsize=(7, 3.2))
    ax.bar(tb["value"], tb["delay_rate"] * 100, color="#1f4e79")
    ax.axhline(base * 100, color="#c0392b", ls="--", lw=1, label=f"전체 {base:.1%}")
    ax.set_ylabel("지연율(%)"); ax.set_title("시간대별 출발 지연율 (구 기준)", fontsize=11); ax.legend(fontsize=8)
    ax.tick_params(labelsize=8)
    fig.tight_layout(); fig.savefig(OUT / "delay_rate_by_time_band.png", dpi=150); plt.close(fig)

    # Orange3 파일 (같은 분할, 조정 전 feature 전부 + 조정용 범주 feature 포함)
    o_num = BEFORE_NUM
    o_cat = BEFORE_CAT + ["time_band", "dow_name", "rain_3h_state"]
    to_orange_tab(train, ORANGE / "departures_train.tab", o_num, o_cat)
    to_orange_tab(test, ORANGE / "departures_test.tab", o_num, o_cat)
    to_orange_tab(df, ORANGE / "departures_all.tab", o_num, o_cat)

    write_report(info, res, rates, base, imp_before, imp_after, coef_a, logs, num, cat)
    print(res.to_string(index=False))
    print("\n조정 로그:\n- " + "\n- ".join(logs))
    print(f"\n출력: {OUT.relative_to(ROOT)}/, {ORANGE.relative_to(ROOT)}/")


def md_table(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)]
    return "\n".join(lines)


def write_report(info, res, rates, base, imp_b, imp_a, coef_a, logs, num, cat):
    top_rates = rates[(rates["n"] >= 30)].sort_values("lift_vs_overall", ascending=False).head(8)
    r = res[["단계", "모델", "feature 수", "threshold", "precision", "recall", "f1", "pr_auc", "f1_at_0.5", "cv_f1_train"]]
    text = f"""# 김해공항 출발 지연 예측 — 분석 보고서 (자동 생성 초안)

> ⚠️ **예비 결과**: {info['period']} 데이터, 생성 {info['run_at_kst']}. 12월에 4주치로 다시 실행해 숫자를 확정한다.
> 테스트 지연 표본이 적어 지표가 크게 흔들릴 수 있음.

## 1. 가상 직군
한국공항공사 김해공항 운영기획부 데이터분석 담당자

## 2. 모델링 목적
운항 **전에** 알 수 있는 정보(예정 시각·요일·항공사·목적지·혼잡 예상·직전 날씨)만으로 출발편의 **지연 여부**를 예측하고,
어떤 요인이 지연 가능성을 높이는지 가중치로 설명해 혼잡 시간대 인력 배치와 승객 안내에 활용한다.

- 타겟: 출발 지연 = 실제 출발 − 예정 출발 > 국내 {info['threshold']['국내']}분 / 국제 {info['threshold']['국제']}분 (국토부 구 기준)
  - API 실제 시각이 이륙(활주로) 기준으로 추정되어, 같은 활주로 기준인 구 기준을 사용 (docs/decisions.md)
- 전체 지연율 {base:.1%} → 클래스 불균형 처리: ① `class_weight=balanced`(지연편에 더 큰 가중치) ② 판정 기준값(threshold)을 학습 데이터 교차검증으로 F1 최대가 되게 조정. 평가는 지연 클래스의 Precision / Recall / F1
- 표의 `threshold`=적용한 기준값, `f1_at_0.5`=기준값 0.5 고정 시 F1(비교용), `cv_f1_train`=학습 데이터 교차검증 F1(안정성 확인용)
- 분할: 날짜 순서 — 학습 {info['train']} / 테스트 {info['test']}

## 3. 분석 보고서

### 3-1. Feature 추출
| feature | 설명 | 운항 전에 알 수 있나 |
|---|---|---|
| 예정 출발 시각·시간대 구간 | 항공권 예정 시각 | ○ |
| 요일, 주말, 공휴일 | 예정 날짜 | ○ |
| 국내/국제, 항공사, 목적지 | 운항 계획 | ○ |
| 코드쉐어 판매 편명 수 | 제휴 판매 규모 | ○ |
| 같은 시각대 예정 출발·도착편 수 | 스케줄 기준 혼잡 | ○ |
| 예상 탑승객 수 | 공사 사전 예상치 (실측 아님) | ○ |
| 기온·풍속·시정·습도 (1시간 전), 직전 3시간 강수 | ASOS 관측, 출발 전 시점 값만 | ○ |
| ~~실제·변경 시각, 운항 상태, 지연 분~~ | 운항 후에 정해짐 → **누수라 제외** | × |

### 3-2. Feature별 타겟 영향 비율
전체 지연율 {base:.1%} 대비 지연율이 높은 조건 (30편 이상인 값만):

{md_table(top_rates[['feature_ko', 'value', 'n', 'delayed_n', 'delay_rate', 'lift_vs_overall']])}

랜덤포레스트 중요도 비율 (조정 후, 합계 100%):

{md_table(imp_a.head(10)[['feature_ko', 'importance_share_pct']])}

전체 표: `outputs/model/feature_target_rates.csv`, `rf_importance_before.csv`, `rf_importance_after.csv`

### 3-3. Feature 조정·가중치
조정 내용 (학습 데이터로만 결정):

- {chr(10).join('- ' + l for l in logs)[2:]}

로지스틱 회귀 가중치 (조정 후, |계수| 상위 10, 계수>0이면 지연 가능성↑):

{md_table(coef_a.head(10)[['column', 'coef', 'odds_ratio', 'note']])}

조정 전·후 성능 비교 (테스트 데이터, 지연 클래스 기준):

{md_table(r)}

### 3-4. 결론
(12월 최종 실행 후 작성 — 성능 비교표와 가중치 상위 요인을 근거로 "공사는 무엇을 해야 하나"를 정리)

## 4. 데이터 출처
- 한국공항공사_실시간 항공기 운항정보 조회_GW (공공데이터포털) — 출발 예정·실제 시각
- 한국공항공사_전국공항 시간대별 탑승승객 수 조회_GW (공공데이터포털) — 예상 탑승객 수 (사전 예상치)
- 한국공항공사_항공기 운항 스케줄 정보_GW (공공데이터포털) — 정기편 스케줄
- 기상청_지상(종관, ASOS) 시간자료 조회서비스 (공공데이터포털) — 김해시(253), 결측 시 부산(159) 보완
- 지연 기준: 국토교통부 지연 기준 (구 기준 활주로 국내 30분·국제 60분, 현행 게이트 15분) — 한경비즈니스 2025-02 보도 인용
"""
    (OUT / "model_report.md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
