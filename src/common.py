"""수집 공통 함수: 키 로드, API 호출(재시도 1회), 페이지 순회, CSV 업서트. 표준 라이브러리만 사용."""
import csv
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
LOGS = DATA / "logs"
KST = timezone(timedelta(hours=9))


def now_kst():
    return datetime.now(KST)


def ymd(date):
    return date.strftime("%Y%m%d")


def load_keys():
    """환경변수(GitHub Secrets) 우선, 없으면 로컬 .env."""
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    kac = os.environ.get("DATA_GO_KR_SERVICE_KEY") or env.get("DATA_GO_KR_SERVICE_KEY")
    kma = os.environ.get("KMA_ASOS_SERVICE_KEY") or env.get("KMA_ASOS_SERVICE_KEY") or kac
    if not kac:
        raise SystemExit("DATA_GO_KR_SERVICE_KEY 가 없습니다 (.env 또는 Secrets 확인)")
    return kac, kma


def log(msg):
    LOGS.mkdir(parents=True, exist_ok=True)
    line = f"{now_kst().isoformat(timespec='seconds')} {msg}"
    print(line)
    with open(LOGS / f"collect_{ymd(now_kst())}.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


class ApiError(Exception):
    pass


def call(url, params, key, timeout=60):
    """한 번 호출. 실패하면 3초 뒤 1회 재시도. 키는 예외 메시지·로그에 남기지 않음."""
    full = url + "?serviceKey=" + urllib.parse.quote(key, safe="") + "&" + urllib.parse.urlencode(params)
    last = None
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(full, timeout=timeout) as r:
                body = r.read().decode("utf-8", "replace")
            data = json.loads(body)
            if "OpenAPI_ServiceResponse" in data:  # 게이트웨이 오류 형식
                h = data["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
                raise ApiError(f"gateway {h.get('returnReasonCode')} {h.get('errMsg')}")
            resp = data.get("response", data)
            code = str(resp.get("header", {}).get("resultCode"))
            if code not in ("00", "0"):
                raise ApiError(f"resultCode={code} {resp.get('header', {}).get('resultMsg')}")
            return resp
        except (urllib.error.URLError, TimeoutError, ValueError, ApiError) as e:
            last = f"{type(e).__name__}: {str(e).replace(key, '***')[:200]}"
            if attempt == 1:
                time.sleep(3)
    raise ApiError(last)


def items_of(resp):
    body = resp.get("body", {})
    items = body.get("items") or {}
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return items or [], int(body.get("totalCount") or 0)


def fetch_all(url, params, key, rows=100, max_pages=100):
    # 주의: 한국공항공사 GW는 numOfRows=1000 이면 HTTP_ERROR(04) → 기본 100
    """페이지를 끝까지 돌며 모든 item을 모음. 반환: (items, 호출 수)."""
    out, page, calls = [], 1, 0
    while page <= max_pages:
        resp = call(url, {**params, "pageNo": page, "numOfRows": rows}, key)
        calls += 1
        items, total = items_of(resp)
        out.extend(items)
        if not items or len(out) >= total:
            break
        page += 1
    return out, calls


def save_raw(api, name, items):
    d = RAW / api / ymd(now_kst())
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}_{now_kst().strftime('%H%M%S')}.json"
    p.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return p


def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


IGNORE_ON_COMPARE = ("collected_at_kst", "first_seen_kst", "fgenTime", "fgentime")


def _s(v):
    return "" if v is None else str(v)


def upsert(path, new_rows, key_fields, fields, sort_fields=None):
    """key 기준 중복 제거. 같은 key면 최신값으로 덮되 first_seen_kst는 유지. 반환: (추가, 갱신)."""
    rows = {tuple(r[k] for k in key_fields): r for r in read_csv(path)}
    added = updated = 0
    for r in new_rows:
        k = tuple(str(r.get(f, "")) for f in key_fields)
        if k in rows:
            old = rows[k]
            r["first_seen_kst"] = old.get("first_seen_kst") or r.get("collected_at_kst")
            # None(API의 null)과 ""는 같은 값으로 보고, 수집 시각·데이터 생성시간만 바뀐 건 '갱신'으로 세지 않음
            if any(_s(old.get(f)) != _s(r.get(f)) for f in fields if f not in IGNORE_ON_COMPARE):
                updated += 1
            rows[k] = {f: r.get(f, "") for f in fields}
        else:
            r["first_seen_kst"] = r.get("collected_at_kst")
            rows[k] = {f: r.get(f, "") for f in fields}
            added += 1
    out = list(rows.values())
    out.sort(key=lambda r: tuple(r.get(f, "") for f in (sort_fields or key_fields)))
    write_csv(path, out, fields)
    return added, updated
