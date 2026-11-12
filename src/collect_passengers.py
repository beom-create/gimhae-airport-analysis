"""시간대별 탑승승객 수 조회 GW 수집 → data/passengers.csv + data/passengers_changes.csv

엔드포인트 이름이 daily-expect-passenger(예상 승객)라 실측인지 확인되지 않음 (docs/decisions.md).
그래서 매일 D-7 ~ D+1 을 다시 받아, 이미 저장된 값과 다르면 passengers_changes.csv 에 기록한다.
값이 날짜가 지난 뒤에도 바뀌면 "사후 갱신(실적 반영 가능성)", 전혀 안 바뀌면 "고정값"으로 판단 근거가 된다.

실행 예: python3 src/collect_passengers.py                 # 일일: D-7 ~ D+1
        python3 src/collect_passengers.py --backfill 365  # 1년 백필 (1주 단위로 나눠 요청)
"""
import argparse
from datetime import timedelta

from common import DATA, fetch_all, log, load_keys, now_kst, read_csv, save_raw, upsert, write_csv, ymd

URL = "https://apis.data.go.kr/B551178/daily-expect-passenger/info"
AIRPORT = "PUS"
OUT = DATA / "passengers.csv"
CHANGES = DATA / "passengers_changes.csv"
KEY = ["SDT", "HH", "AOD", "TOF"]
FIELDS = ["SDT", "HH", "AOD", "TOF", "ARP", "PCT", "collected_at_kst", "first_seen_kst"]
CHANGE_FIELDS = ["SDT", "HH", "AOD", "TOF", "old_PCT", "new_PCT", "old_collected_at_kst", "new_collected_at_kst", "days_after_SDT"]
CHUNK_DAYS = 7  # 35일 한 번 요청 시 연결 오류 1회 → 1주 단위


def fetch_range(kac, start, end):
    rows, calls = [], 0
    stamp = now_kst().isoformat(timespec="seconds")
    d = start
    while d <= end:
        e = min(d + timedelta(days=CHUNK_DAYS - 1), end)
        items, c = fetch_all(URL, {"schStDate": ymd(d), "schEdDate": ymd(e), "schAirport": AIRPORT, "type": "json"}, kac)
        calls += c
        save_raw("passengers", f"{ymd(d)}_{ymd(e)}", items)
        for it in items:
            it["collected_at_kst"] = stamp
            rows.append(it)
        log(f"[pax] {ymd(d)}~{ymd(e)}: {len(items)}건 (호출 {c})")
        d = e + timedelta(days=1)
    return rows, calls


def record_changes(new_rows):
    old = {tuple(r[k] for k in KEY): r for r in read_csv(OUT)}
    today = now_kst().date()
    changes = read_csv(CHANGES)
    n = 0
    for r in new_rows:
        k = tuple(str(r.get(f, "")) for f in KEY)
        o = old.get(k)
        if o and str(o.get("PCT")) != str(r.get("PCT")):
            sdt = r["SDT"]
            days_after = (today - today.replace(year=int(sdt[:4]), month=int(sdt[4:6]), day=int(sdt[6:]))).days
            changes.append({**{f: r[f] for f in KEY}, "old_PCT": o.get("PCT"), "new_PCT": r.get("PCT"),
                            "old_collected_at_kst": o.get("collected_at_kst"), "new_collected_at_kst": r["collected_at_kst"],
                            "days_after_SDT": days_after})
            n += 1
    write_csv(CHANGES, changes, CHANGE_FIELDS)
    return n


def run(backfill_days=0):
    kac, _ = load_keys()
    today = now_kst().date()
    start = today - timedelta(days=backfill_days or 7)
    end = today + timedelta(days=1)
    rows, calls = fetch_range(kac, start, end)
    changed = record_changes(rows)
    added, updated = upsert(OUT, rows, KEY, FIELDS, sort_fields=["SDT", "HH", "AOD", "TOF"])
    log(f"[pax] 저장 {OUT.name}: 신규 {added}, 갱신 {updated}, 값 변경 기록 {changed}, 호출 {calls}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", type=int, default=0, help="오늘 기준 며칠 전부터 받을지")
    run(ap.parse_args().backfill)
