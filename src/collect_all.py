"""일일 수집 진입점 (GitHub Actions 하루 2회 실행). API 하나가 실패해도 나머지는 계속 진행.

하루 예상 호출 수 (1회 실행 기준, 2회면 ×2):
  운항정보 D-3~D0 × 출발/도착 × 약 3페이지 = 약 24  (한도 5,000)
  탑승객 수 D-7~D+1 = 약 7                           (한도 5,000)
  스케줄 국내+국제 = 약 41                            (한도: 확인 필요)
  ASOS 2개 관측소 = 2                                  (한도 10,000)
"""
import sys
import traceback

import collect_flights
import collect_passengers
import collect_schedule
import collect_weather
from common import log

failed = []
for name, fn in (("flights", collect_flights.run), ("passengers", collect_passengers.run),
                 ("weather", collect_weather.run), ("schedule", collect_schedule.run)):
    try:
        r = fn()
        if r:  # collect_flights는 실패 목록을 돌려줌
            failed.append(name)
    except Exception as e:
        failed.append(name)
        log(f"[{name}] 실패: {type(e).__name__}: {e}")
        traceback.print_exc()

log(f"[all] 완료, 실패: {', '.join(failed) or '없음'}")
sys.exit(1 if failed else 0)
