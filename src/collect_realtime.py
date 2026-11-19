"""공항 소요시간 정보 GW + 공항 혼잡도 정보 GW 수집 (실시간 전용, 과거 조회 불가) → 15분 간격 실행.

- 명세에 날짜 파라미터가 없고, 응답은 처리시간 PRC_HR(HH:MM)만 준다 → 호출 시점의 스냅샷.
- 국내선만 제공 (명세 설명: 김포·김해·제주·대구·청주 국내선).
- PRC_HR에 날짜가 없으므로 수집 시각(KST)으로 날짜를 붙인다. PRC_HR이 수집 시각보다 늦으면(예: 00:05 수집에 23:50) 전날로 본다.
- 같은 공항·날짜·PRC_HR은 한 번만 저장 (15분 간격이라도 API 갱신이 더 느리면 중복).

출력: data/realtime/process_time.csv, data/realtime/congestion.csv
실행: python3 src/collect_realtime.py
"""
from datetime import datetime, timedelta

from common import DATA, call, items_of, load_keys, log, now_kst, upsert

OUT_DIR = DATA / "realtime"
SOURCES = {
    "process_time": {
        "url": "https://apis.data.go.kr/B551178/airport-process-time/v2",  # 김해·대구·청주
        "params": {"cond[IATA_APCD::EQ]": "PUS"},  # 필터가 무시되고 전체 공항이 올 수 있음 → 받은 그대로 저장
        "fields": ["OPR_STS_CD", "STY_TCT_AVG_A", "STY_TCT_AVG_B", "STY_TCT_AVG_C", "STY_TCT_AVG_D", "STY_TCT_AVG_ALL"],
    },
    "congestion": {
        "url": "https://apis.data.go.kr/B551178/airport-congestion/v2",
        "params": {},
        "fields": ["CGDR_A_LVL", "CGDR_B_LVL", "CGDR_C_LVL", "CGDR_ALL_LVL"],
    },
}
KEY = ["IATA_APCD", "date", "PRC_HR"]


def prc_date(prc_hr, now):
    try:
        t = datetime.strptime(prc_hr, "%H:%M").replace(year=now.year, month=now.month, day=now.day, tzinfo=now.tzinfo)
    except (TypeError, ValueError):
        return now.strftime("%Y-%m-%d")
    if t - now > timedelta(minutes=30):  # 처리시간이 수집 시각보다 늦으면 전날 값
        t -= timedelta(days=1)
    return t.strftime("%Y-%m-%d")


def run():
    kac, _ = load_keys()
    now = now_kst()
    stamp = now.isoformat(timespec="seconds")
    failed = []
    for name, s in SOURCES.items():
        try:
            items, _ = items_of(call(s["url"], {**s["params"], "type": "json", "pageNo": 1, "numOfRows": 50}, kac))
        except Exception as e:
            failed.append(name)
            log(f"[realtime] 실패 {name}: {e}")
            continue
        rows = [{**it, "date": prc_date(it.get("PRC_HR"), now), "collected_at_kst": stamp} for it in items]
        fields = KEY + s["fields"] + ["collected_at_kst", "first_seen_kst"]
        added, updated = upsert(OUT_DIR / f"{name}.csv", rows, KEY, fields, sort_fields=["date", "PRC_HR", "IATA_APCD"])
        pus = next((r for r in rows if r.get("IATA_APCD") == "PUS"), {})
        log(f"[realtime] {name}: {len(items)}건, 신규 {added}, 갱신 {updated} | PUS {pus.get('PRC_HR')} "
            f"{ {k: pus.get(k) for k in s['fields']} }")
    return failed


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
