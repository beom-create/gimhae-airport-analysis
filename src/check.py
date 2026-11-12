"""수집 상태 점검: 날짜별 건수, 빠진 날짜·시간대, 탑승객 값 변경 현황.

실행: python3 src/check.py            # 최근 14일
     python3 src/check.py --days 60
"""
import argparse
import collections
from datetime import timedelta

from common import DATA, now_kst, read_csv, ymd


def daterange(n):
    today = now_kst().date()
    return [ymd(today - timedelta(days=i)) for i in range(n, -1, -1)]


def main(days):
    dates = daterange(days)
    print(f"=== 점검 {now_kst():%Y-%m-%d %H:%M} KST, 최근 {days}일 ===")

    f = read_csv(DATA / "flights.csv")
    by = collections.Counter((r["searchday"], r["io"]) for r in f)
    hours = collections.defaultdict(set)
    for r in f:
        hours[r["searchday"]].add(r["scheduledatetime"][8:10])
    print(f"\n[운항정보] 총 {len(f)}건 (코드쉐어 제외 {sum(r['codeshare'] != 'Y' for r in f)}건)")
    print("  날짜      출발  도착  비어있는 시간대(06~22시)")
    for d in dates:
        if by[(d, "O")] or by[(d, "I")]:
            empty = [h for h in (f"{i:02d}" for i in range(6, 23)) if h not in hours[d]]
            print(f"  {d}  {by[(d, 'O')]:4d}  {by[(d, 'I')]:4d}  {','.join(empty) or '-'}")
        else:
            print(f"  {d}     -     -  ※ 데이터 없음")
    unsettled = [r for r in f if r["searchday"] < ymd(now_kst()) and r["rmkKor"] in ("", "수속중", "탑승중", "탑승구 변경")]
    print(f"  지난 날짜인데 최종 상태가 아닌 행: {len(unsettled)}건")

    p = read_csv(DATA / "passengers.csv")
    pd = collections.Counter(r["SDT"] for r in p)
    miss = [d for d in dates if not pd[d]]
    print(f"\n[탑승객 수] 총 {len(p)}행, {len(pd)}일 ({min(pd) if pd else '-'} ~ {max(pd) if pd else '-'})")
    print(f"  최근 {days}일 중 빠진 날짜: {', '.join(miss) or '없음'}")
    ch = read_csv(DATA / "passengers_changes.csv")
    if ch:
        lag = collections.Counter(int(r["days_after_SDT"]) for r in ch)
        print(f"  값 변경 기록 {len(ch)}건 — 운항일 기준 며칠 뒤 바뀌었나: {dict(sorted(lag.items()))}")
    else:
        print("  값 변경 기록 0건 (아직 재수집 사이에 바뀐 값 없음)")

    w = read_csv(DATA / "weather.csv")
    wd = collections.Counter((r["stnId"], r["tm"][:10].replace("-", "")) for r in w)
    for stn in ("253", "159"):
        bad = [d for d in dates[:-1] if wd[(stn, d)] != 24]
        print(f"\n[날씨 {stn}] 총 {sum(v for (s, _), v in wd.items() if s == stn)}행, 최근 {days}일 중 24시간이 아닌 날: {', '.join(bad) or '없음'}")

    s = read_csv(DATA / "schedule.csv")
    print(f"\n[스케줄] 유효 정기편 {len(s)}건 (국내 {sum(r['line'] == '국내' for r in s)}, 국제 {sum(r['line'] == '국제' for r in s)})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    main(ap.parse_args().days)
