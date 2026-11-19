"""Phase 3 전처리: 출발편 1편 = 1행으로 만들고, 운항 전에 알 수 있는 feature와 타겟을 붙인다.

입력: data/flights.csv, data/passengers.csv, data/weather.csv
출력: data/processed/departures.csv (전처리 결과, 분석·모델 공통)
규칙 문서: docs/preprocessing_rules.md

실행: .venv/bin/python src/preprocess.py
"""
import pandas as pd

import config as C
from common import DATA, now_kst, ymd

OUT = DATA / "processed" / "departures.csv"

# 누수 컬럼: 운항 뒤에야 알 수 있는 값 → 타겟 계산에만 쓰고 feature에서 제외
LEAKAGE = ["estimateddatetime", "rmkKor", "fgenTime", "collected_at_kst", "first_seen_kst", "fid"]


def load_flights():
    f = pd.read_csv(DATA / "flights.csv", dtype=str).fillna("")
    today = ymd(now_kst())
    # 판매 편명 중복 제거: 마스터 편명이 없거나 자기 자신인 행만 실제 운항편
    phys = (f["masterflightid"] == "") | (f["masterflightid"] == f["flightid"])
    # 코드쉐어 판매 편명 수 = 운항 전에 알 수 있는 정보 (수요·제휴 규모의 대리변수)
    share = f[~phys & (f["masterflightid"] != "")].groupby(["io", "masterflightid", "scheduledatetime"]).size()
    f = f[phys & (f["searchday"] >= C.ANALYSIS_START) & (f["searchday"] < today)].copy()  # 확정된 지난 날짜만
    f["n_codeshare"] = [share.get((io, fl, s), 0) for io, fl, s in zip(f["io"], f["flightid"], f["scheduledatetime"])]
    f["sched"] = pd.to_datetime(f["scheduledatetime"], format="%Y%m%d%H%M")
    f["est"] = pd.to_datetime(f["estimateddatetime"], format="%Y%m%d%H%M", errors="coerce")
    return f


def add_traffic(dep, allf):
    """같은 날·같은 시각대에 예정된 출발/도착 편수 (스케줄로 미리 알 수 있음)."""
    key = allf["sched"].dt.floor("h")
    cnt = allf.assign(h=key).groupby(["io", "h"]).size()
    h = dep["sched"].dt.floor("h")
    dep["sched_dep_same_hour"] = [cnt.get(("O", x), 0) for x in h]
    dep["sched_arr_same_hour"] = [cnt.get(("I", x), 0) for x in h]
    return dep


def add_passengers(dep):
    """예상 탑승객 수(공사 사전 예상치, 실측 아님): 같은 날·시간대·국내/국제 출발 승객."""
    p = pd.read_csv(DATA / "passengers.csv", dtype=str)
    p = p[p["AOD"] == "D"].copy()
    p["PCT"] = pd.to_numeric(p["PCT"], errors="coerce")
    line = p["TOF"].map({"D": "국내", "I": "국제"})
    by_line = p.assign(line=line).set_index(["SDT", "HH", "line"])["PCT"]
    total = p.groupby(["SDT", "HH"])["PCT"].sum()
    sdt = dep["sched"].dt.strftime("%Y%m%d")
    hh = dep["sched"].dt.strftime("%H")
    dep["exp_pax_line_hour"] = [by_line.get((s, h, l)) for s, h, l in zip(sdt, hh, dep["line"])]
    dep["exp_pax_total_hour"] = [total.get((s, h)) for s, h in zip(sdt, hh)]
    return dep


def _station(w, stn):
    s = w[w["stnId"] == stn].copy()
    s["tm"] = pd.to_datetime(s["tm"], format="%Y-%m-%d %H:%M")
    # 강수량 규칙 (docs/decisions.md): 빈값+정상 플래그 → 0, 플래그 9(결측)/1(오류) → 결측
    rn = pd.to_numeric(s["rn"], errors="coerce").fillna(0.0)
    rn[s["rnQcflg"].isin(["9", "1"])] = float("nan")
    s["rn_mm"] = rn
    for c in ("ta", "ws", "vs", "hm"):
        s[c] = pd.to_numeric(s[c], errors="coerce")
    return s.set_index("tm").sort_index()


def load_weather():
    w = pd.read_csv(DATA / "weather.csv", dtype=str).fillna("")
    main_, backup = _station(w, C.WEATHER_STATION), _station(w, C.WEATHER_BACKUP_STATION)
    # 김해시(253) 강수 결측(플래그 9, 1년 중 약 13%)은 부산(159) 같은 시각 값으로 보완.
    # 검증: 253 결측 시간대에 159에서 비가 온 비율 2.4% (전체 5.4%) → 결측이 비를 숨기는 패턴 아님
    miss = main_["rn_mm"].isna()
    main_.loc[miss, "rn_mm"] = main_.index[miss].map(backup["rn_mm"])
    main_["rn_filled_from_backup"] = miss.astype(int)
    for c in ("ta", "ws", "vs", "hm"):
        m = main_[c].isna()
        main_.loc[m, c] = main_.index[m].map(backup[c])
    return main_


def add_weather(dep, w):
    """예정 출발 1시간 전 관측값 + 직전 3시간 강수 (운항 전에 알 수 있는 정보만).
    11~3월은 강수가 3시간 누적값으로만 기록되므로 '직전 3시간 창'으로 판단하면 두 기간을 같은 규칙으로 처리할 수 있다."""
    obs_t = dep["sched"].dt.floor("h") - pd.Timedelta(hours=1)
    for c in ("ta", "ws", "vs", "hm"):
        dep[c] = obs_t.map(w[c])
    rain = w["rn_mm"]
    dep["rain_3h_mm"] = [rain.loc[t - pd.Timedelta(hours=2): t].sum(min_count=1) for t in obs_t]
    # 강수 상태 3분류. "기록결측"(플래그 9)은 비 온 날에 몰려 있어(플래그9 15시간+ 인 날의 82%가 비 온 날)
    # 0으로 채우지 않고 별도 범주로 둔다 (docs/preprocessing_rules.md)
    dep["rain_3h_missing"] = dep["rain_3h_mm"].isna().astype(int)
    dep["rain_3h_state"] = ["기록결측" if pd.isna(v) else ("비" if v > 0 else "없음") for v in dep["rain_3h_mm"]]
    return dep


def time_band(h):
    return ("06-08 아침" if 6 <= h <= 8 else "09-11 오전" if h <= 11 else "12-14 점심" if h <= 14
            else "15-17 오후" if h <= 17 else "18-20 저녁" if h <= 20 else "21-23 밤") if h >= 6 else "00-05 심야"


def main():
    allf = load_flights()
    dep = allf[allf["io"] == "O"].copy()
    n_all = len(dep)
    cancelled = dep["rmkKor"].str.contains("결항")
    dep = dep[~cancelled & dep["est"].notna()].copy()

    # 타겟: 출발 지연 여부 (구 기준, 활주로 기준 측정과 일치)
    dep["delay_min"] = (dep["est"] - dep["sched"]).dt.total_seconds() / 60
    dep["delay_threshold"] = dep["line"].map(C.DELAY_THRESHOLD_MIN)
    dep["delayed"] = (dep["delay_min"] > dep["delay_threshold"]).astype(int)
    dep["delayed_gate15_ref"] = (dep["delay_min"] > C.DELAY_THRESHOLD_GATE_MIN).astype(int)  # 참고용

    # 시간 feature
    dep["date"] = dep["sched"].dt.strftime("%Y-%m-%d")
    dep["sched_hour"] = dep["sched"].dt.hour
    dep["time_band"] = dep["sched_hour"].map(time_band)
    dep["dow"] = dep["sched"].dt.dayofweek  # 0=월
    dep["dow_name"] = dep["dow"].map(dict(enumerate("월화수목금토일")))
    dep["is_weekend"] = (dep["dow"] >= 5).astype(int)
    dep["is_holiday"] = dep["sched"].dt.date.isin(C.HOLIDAYS).astype(int)
    dep["dest"] = dep["arrAirportCode"]

    dep = add_traffic(dep, allf)
    dep = add_passengers(dep)
    dep = add_weather(dep, load_weather())

    cols = ["date", "flightid", "airline", "line", "dest", "sched_hour", "time_band", "dow", "dow_name",
            "is_weekend", "is_holiday", "n_codeshare", "sched_dep_same_hour", "sched_arr_same_hour",
            "exp_pax_line_hour", "exp_pax_total_hour", "ta", "ws", "vs", "hm", "rain_3h_mm", "rain_3h_state", "rain_3h_missing",
            "delay_min", "delayed", "delayed_gate15_ref"]
    out = dep.sort_values(["date", "sched_hour", "flightid"])[cols]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False, encoding="utf-8")

    print(f"출발 실제 운항편 {n_all}편 → 결항 {int(cancelled.sum())}편 제외 → 모델 대상 {len(out)}편 "
          f"({out['date'].min()} ~ {out['date'].max()}, {out['date'].nunique()}일)")
    print(f"지연(구 기준) {out['delayed'].sum()}편 = {out['delayed'].mean():.1%} | 참고 15분 초과 {out['delayed_gate15_ref'].mean():.1%}")
    print("결측:", {c: int(v) for c, v in out.isna().sum().items() if v})
    print(f"저장: {OUT.relative_to(DATA.parent)}")


if __name__ == "__main__":
    main()
