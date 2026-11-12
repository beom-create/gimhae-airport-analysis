"""운항정보 조회 GW (depart/arrival) 수집 → data/flights.csv

명세: D-3 ~ D+6 조회 가능 (docs/api_fields.md). 기본은 D-3 ~ D0(오늘)을 받아
지난 날짜는 확정값으로 덮어쓰고, 오늘 값은 다음 실행에서 다시 갱신된다.

실행 예: python3 src/collect_flights.py            # D-3 ~ 오늘
        python3 src/collect_flights.py --from -3 --to 0
"""
import argparse
from datetime import timedelta

from common import DATA, fetch_all, log, load_keys, now_kst, save_raw, upsert, ymd

URL = "https://apis.data.go.kr/B551178/flight-status/{io}"
AIRPORT = "PUS"  # 김해공항 IATA
OUT = DATA / "flights.csv"
KEY = ["io", "flightid", "scheduledatetime"]  # 출도착 + 편명 + 예정시각
FIELDS = [
    "searchday", "io", "line", "flightid", "airline", "codeshare", "masterflightid",
    "scheduledatetime", "estimateddatetime", "rmkKor",
    "depAirportCode", "depAirport", "arrAirportCode", "arrAirport",
    "fid", "fgenTime", "collected_at_kst", "first_seen_kst",
]


def run(day_from=-3, day_to=0):
    kac, _ = load_keys()
    today = now_kst().date()
    stamp = now_kst().isoformat(timespec="seconds")
    rows, calls, failed = [], 0, []
    for n in range(day_from, day_to + 1):
        day = ymd(today + timedelta(days=n))
        for io in ("depart", "arrival"):
            try:
                items, c = fetch_all(URL.format(io=io), {"searchday": day, "airport_code": AIRPORT, "type": "json"}, kac)
            except Exception as e:
                failed.append(f"{io} {day}: {e}")
                log(f"[flights] 실패 {io} {day} {e}")
                continue
            calls += c
            save_raw("flights", f"{io}_{day}", items)
            for it in items:
                # 출발편은 도착공항 코드가 arrvAirportCode 로 옴 → 이름 통일
                it["arrAirportCode"] = it.get("arrAirportCode") or it.get("arrvAirportCode", "")
                it["io"] = it.get("io") or ("O" if io == "depart" else "I")
                it["searchday"] = it.get("searchday") or day
                it["collected_at_kst"] = stamp
                rows.append(it)
            log(f"[flights] {io} {day}: {len(items)}건 (호출 {c})")
    added, updated = upsert(OUT, rows, KEY, FIELDS, sort_fields=["scheduledatetime", "io", "flightid"])
    log(f"[flights] 저장 {OUT.name}: 신규 {added}, 갱신 {updated}, 호출 {calls}, 실패 {len(failed)}")
    return failed


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="f", type=int, default=-3)
    ap.add_argument("--to", dest="t", type=int, default=0)
    a = ap.parse_args()
    raise SystemExit(1 if run(a.f, a.t) else 0)
