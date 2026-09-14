"""
9월1일 + 9월·10월 주말 예약 오픈 감시 스크립트.
GitHub Actions에서 15분마다 실행되어, 각 선상24 배의 해당 날짜가
- 언제 처음 스케줄에 나타났는지(day_appeared_at)
- 언제 처음 남은자리가 1개 이상으로 잡혔는지(seats_open_at)
를 기록한다. Streamlit 앱과 완전히 독립적으로 동작한다.
"""
import json
import re
from pathlib import Path
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup

APP_DIR = Path(__file__).parent
SUNSANG_FILE = APP_DIR / "sunsang24_sites.json"
LOG_FILE = APP_DIR / "booking_watch_log.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
}


def load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_target_dates():
    """9월 1일 + 9월·10월 매주 토·일요일 목록. 이미 10월이 지났으면 내년 기준."""
    today = date.today()
    year = today.year
    if today > date(year, 10, 31):
        year += 1

    dates = {date(year, 9, 1)}
    for month in (9, 10):
        d = date(year, month, 1)
        while d.month == month:
            if d.weekday() >= 5:  # 5=토, 6=일
                dates.add(d)
            d += timedelta(days=1)

    return sorted(d for d in dates if d >= today)


def fetch_schedule_text(base_url: str) -> str:
    url = f"{base_url.rstrip('/')}/ship/schedule_fleet"
    res = requests.get(url, headers=HEADERS, timeout=20)
    res.raise_for_status()
    res.encoding = res.apparent_encoding or "utf-8"
    soup = BeautifulSoup(res.text, "html.parser")
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{2,}", "\n", text)


def check_date_block(text: str, target: date):
    """해당 날짜 블록이 스케줄에 존재하는지, 존재하면 남은자리 합계는 몇 명인지."""
    day_pat = re.compile(rf"{target.month}월\s*{target.day}일\([^)]+\)")
    m = day_pat.search(text)
    if not m:
        return False, 0

    any_day_pat = re.compile(r"\d+월\s*\d+일\([^)]+\)")
    next_m = any_day_pat.search(text, m.end())
    block = text[m.end(): next_m.start() if next_m else len(text)]

    remain_matches = re.findall(r"남은자리\s*(\d+)\s*명", block)
    total_remain = sum(int(x) for x in remain_matches)
    return True, total_remain


def main():
    sites = load_json(SUNSANG_FILE, [])
    watch_log = load_json(LOG_FILE, {})
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    targets = get_target_dates()

    for site in sites:
        base_url = site.get("base_url")
        name = site.get("name")
        if not base_url or not name:
            continue
        try:
            text = fetch_schedule_text(base_url)
        except Exception as e:
            print(f"[실패] {name}: {e}")
            continue

        for target in targets:
            key = f"{name}|{target.isoformat()}"
            entry = watch_log.get(key, {})
            appeared, remain = check_date_block(text, target)

            if appeared and "day_appeared_at" not in entry:
                entry["day_appeared_at"] = now
                print(f"[신규 오픈 감지] {name} {target} - 스케줄 최초 등장")
            if remain > 0 and "seats_open_at" not in entry:
                entry["seats_open_at"] = now
                print(f"[예약가능 감지] {name} {target} - 남은자리 {remain}명")

            entry["ship"] = name
            entry["date"] = target.isoformat()
            entry["last_checked"] = now
            entry["last_remain"] = remain
            watch_log[key] = entry

    save_json(LOG_FILE, watch_log)
    print(f"완료: {len(watch_log)}개 항목, {len(sites)}개 사이트, {len(targets)}개 대상일")


if __name__ == "__main__":
    main()
