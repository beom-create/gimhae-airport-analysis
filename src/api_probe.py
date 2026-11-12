"""Phase 0: API 4개 테스트 호출 → data/sample/ 에 응답 저장.

엔드포인트·파라미터는 docs/ 의 활용가이드(.docx)와 탑승승객수 Swagger 명세에서 그대로 가져옴.
API 키는 .env 에서 읽고, 저장 파일·출력에는 절대 남기지 않음.

실행: python3 src/api_probe.py
"""
import json
import os
import pathlib
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample"
KST = timezone(timedelta(hours=9))


def load_env():
    env = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


ENV = load_env()
KAC_KEY = ENV["DATA_GO_KR_SERVICE_KEY"]
KMA_KEY = ENV.get("KMA_ASOS_SERVICE_KEY") or KAC_KEY

today = datetime.now(KST).date()
d = lambda n: (today + timedelta(days=n)).strftime("%Y%m%d")  # noqa: E731

KAC = "https://apis.data.go.kr/B551178"
KMA = "https://apis.data.go.kr/1360000/AsosHourlyInfoService/getWthrDataList"

# (저장 이름, URL, 파라미터, 키)
CALLS = [
    # 1) 실시간 항공기 운항정보 조회 GW — 보험사용 출발편: searchday로 D-3 ~ D+6 조회 가능하다고 명세에 적힘
    ("flight_depart_D-3", f"{KAC}/flight-status/depart", {"searchday": d(-3), "airport_code": "PUS", "numOfRows": 20}, KAC_KEY),
    ("flight_depart_D-4", f"{KAC}/flight-status/depart", {"searchday": d(-4), "airport_code": "PUS", "numOfRows": 20}, KAC_KEY),
    ("flight_arrival_D-3", f"{KAC}/flight-status/arrival", {"searchday": d(-3), "airport_code": "PUS", "numOfRows": 20}, KAC_KEY),
    # 2) 전국공항 시간대별 탑승승객 수 조회 GW
    ("pax_today", f"{KAC}/daily-expect-passenger/info", {"schStDate": d(0), "schEdDate": d(0), "schAirport": "PUS", "numOfRows": 100}, KAC_KEY),
    ("pax_D-3", f"{KAC}/daily-expect-passenger/info", {"schStDate": d(-3), "schEdDate": d(-3), "schAirport": "PUS", "numOfRows": 100}, KAC_KEY),
    ("pax_D-30", f"{KAC}/daily-expect-passenger/info", {"schStDate": d(-30), "schEdDate": d(-30), "schAirport": "PUS", "numOfRows": 100}, KAC_KEY),
    ("pax_D+1", f"{KAC}/daily-expect-passenger/info", {"schStDate": d(1), "schEdDate": d(1), "schAirport": "PUS", "numOfRows": 100}, KAC_KEY),
    # 3) 항공기 운항 스케줄 정보 GW
    ("schedule_dom", f"{KAC}/flight-schedule/taxfree-dom", {"depCityCode": "PUS", "numOfRows": 20}, KAC_KEY),
    ("schedule_int", f"{KAC}/flight-schedule/taxfree-int", {"depCityCode": "PUS", "numOfRows": 20}, KAC_KEY),
    # 4) 기상청 ASOS 시간자료 — 종료일은 전일(D-1)까지. 지점: 253 김해시, 159 부산 (가이드 첨부 지점표)
    ("asos_253_D-3", KMA, {"dataCd": "ASOS", "dateCd": "HR", "startDt": d(-3), "startHh": "00", "endDt": d(-3), "endHh": "23", "stnIds": "253", "numOfRows": 24}, KMA_KEY),
    ("asos_159_D-3", KMA, {"dataCd": "ASOS", "dateCd": "HR", "startDt": d(-3), "startHh": "00", "endDt": d(-3), "endHh": "23", "stnIds": "159", "numOfRows": 24}, KMA_KEY),
]


def call(name, url, params, key):
    q = {"pageNo": 1, "type": "json", "dataType": "JSON", **params}
    full = url + "?serviceKey=" + urllib.parse.quote(key, safe="") + "&" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(full, timeout=30) as r:
            status, body = r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # 네트워크 오류 메시지에 키가 섞이지 않게 타입만 기록
        status, body = None, f"{type(e).__name__}"
    body = body.replace(key, "***")
    try:
        data = json.loads(body)
        ext = "json"
    except ValueError:
        data, ext = body, "txt"
    out = SAMPLE / f"{name}.{ext}"
    meta = {"name": name, "endpoint": url, "params": q, "http_status": status, "called_at_kst": datetime.now(KST).isoformat(timespec="seconds")}
    if ext == "json":
        out.write_text(json.dumps({"_meta": meta, "response": data}, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        out.write_text(json.dumps(meta, ensure_ascii=False) + "\n\n" + data, encoding="utf-8")
    return status, data


def summarize(data):
    if not isinstance(data, dict):
        return "비JSON 응답: " + str(data)[:150].replace("\n", " ")
    resp = data.get("response", data)
    h, b = resp.get("header", {}), resp.get("body", {})
    items = (b.get("items") or {})
    items = items.get("item", items) if isinstance(items, dict) else items
    if isinstance(items, dict):
        items = [items]
    first = items[0] if items else {}
    return f"{h.get('resultCode')} {h.get('resultMsg')} | totalCount={b.get('totalCount')} | 첫 행={json.dumps(first, ensure_ascii=False)[:300]}"


if __name__ == "__main__":
    SAMPLE.mkdir(parents=True, exist_ok=True)
    print("오늘(KST):", today)
    for name, url, params, key in CALLS:
        status, data = call(name, url, params, key)
        print(f"\n[{name}] HTTP {status}\n  {summarize(data)}")
