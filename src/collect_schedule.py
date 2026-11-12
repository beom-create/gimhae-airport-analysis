"""항공기 운항 스케줄 정보 GW 수집 → data/schedule.csv (김해 출발편 정기 스케줄 스냅샷)

필터 규칙 (docs/decisions.md):
- lastdate(정기운항 종료일) < 수집일 인 편은 이미 끝난 스케줄 → 저장하지 않음
- fgenType == "D"(삭제) 인 편도 제외
- 국제선 응답의 airlinecode(소문자)를 airlineCode로 통일

실행: python3 src/collect_schedule.py
"""
from common import DATA, fetch_all, log, load_keys, now_kst, save_raw, upsert, ymd

BASE = "https://apis.data.go.kr/B551178/flight-schedule/"
AIRPORT = "PUS"
OUT = DATA / "schedule.csv"
KEY = ["line", "fid", "flightid"]
FIELDS = ["line", "flightid", "airline", "airlineCode", "st", "firstdate", "lastdate",
          "ynmon", "yntue", "ynwed", "ynthu", "ynfri", "ynsat", "ynsun",
          "depcitycode", "depcity", "arrvcitycode", "arrvcity", "fid", "fgenType", "fgentime",
          "collected_at_kst", "first_seen_kst"]


def is_active(it, today_ymd):
    last = str(it.get("lastdate") or "")
    if it.get("fgenType") == "D":
        return False
    return not last or last >= today_ymd


def run():
    kac, _ = load_keys()
    today = ymd(now_kst())
    stamp = now_kst().isoformat(timespec="seconds")
    rows, calls = [], 0
    for line, op in (("국내", "taxfree-dom"), ("국제", "taxfree-int")):
        items, c = fetch_all(BASE + op, {"depCityCode": AIRPORT, "type": "json"}, kac)
        calls += c
        save_raw("schedule", op, items)
        active = [it for it in items if is_active(it, today)]
        for it in active:
            it["line"] = line
            it["airlineCode"] = it.get("airlineCode") or it.get("airlinecode", "")
            it["collected_at_kst"] = stamp
            rows.append(it)
        log(f"[schedule] {op}: 받음 {len(items)}, 종료·삭제 제외 {len(items) - len(active)}, 유지 {len(active)} (호출 {c})")
    # 매번 '현재 유효한 스케줄'만 남기도록 덮어쓰기 (과거 스냅샷은 data/raw/schedule/ 에 보관)
    if OUT.exists():
        OUT.unlink()
    added, _ = upsert(OUT, rows, KEY, FIELDS, sort_fields=["line", "st", "flightid"])
    log(f"[schedule] 저장 {OUT.name}: {added}건, 호출 {calls}")


if __name__ == "__main__":
    run()
