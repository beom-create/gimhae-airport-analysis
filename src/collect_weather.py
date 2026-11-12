"""기상청 ASOS 시간자료 수집 → data/weather.csv

- 분석 기본 관측소: 253 김해시 (김해공항에서 더 가까움, docs/decisions.md). 159 부산은 비교·보완용으로 함께 저장.
- 종료일은 명세상 D-1 까지.
- rn(강수량) 빈값 처리 규칙은 docs/decisions.md 참고. 여기서는 원본값을 그대로 저장하고 전처리(Phase 3)에서 변환.

실행 예: python3 src/collect_weather.py               # D-7 ~ D-1
        python3 src/collect_weather.py --from 20261109
"""
import argparse
from datetime import datetime, timedelta

from common import DATA, fetch_all, log, load_keys, now_kst, save_raw, upsert, ymd

URL = "https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"
STATIONS = {"253": "김해시", "159": "부산"}
PRIMARY_STATION = "253"
OUT = DATA / "weather.csv"
KEY = ["stnId", "tm"]
FIELDS = ["stnId", "stnNm", "tm", "ta", "rn", "ws", "wd", "vs", "hm", "dsnw", "hr3Fhsc",
          "taQcflg", "rnQcflg", "wsQcflg", "collected_at_kst", "first_seen_kst"]


def run(start=None):
    _, kma = load_keys()
    today = now_kst().date()
    s = start or today - timedelta(days=7)
    e = today - timedelta(days=1)
    stamp = now_kst().isoformat(timespec="seconds")
    rows, calls = [], 0
    for stn in STATIONS:
        params = {"dataType": "JSON", "dataCd": "ASOS", "dateCd": "HR", "startDt": ymd(s), "startHh": "00",
                  "endDt": ymd(e), "endHh": "23", "stnIds": stn}
        items, c = fetch_all(URL, params, kma, rows=999)
        calls += c
        save_raw("weather", f"{stn}_{ymd(s)}_{ymd(e)}", items)
        for it in items:
            it["collected_at_kst"] = stamp
            rows.append(it)
        log(f"[weather] {stn} {STATIONS[stn]} {ymd(s)}~{ymd(e)}: {len(items)}건 (호출 {c})")
    added, updated = upsert(OUT, rows, KEY, FIELDS, sort_fields=["stnId", "tm"])
    log(f"[weather] 저장 {OUT.name}: 신규 {added}, 갱신 {updated}, 호출 {calls}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="f", help="YYYYMMDD")
    a = ap.parse_args()
    run(datetime.strptime(a.f, "%Y%m%d").date() if a.f else None)
