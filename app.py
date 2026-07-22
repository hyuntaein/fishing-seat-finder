import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup


APP_DIR = Path(__file__).parent
SUNSANG_FILE = APP_DIR / "sunsang24_sites.json"
MANUAL_FILE = APP_DIR / "manual_sites.json"
LOG_FILE = APP_DIR / "fishing_logs.json"

ANGLERS = ["인현태", "조정환", "한영탁", "김정국","최귀선","손님"]

HEADERS_JSON = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "application/json,text/html,*/*",
}
HEADERS_HTML = {"User-Agent": HEADERS_JSON["User-Agent"], "Accept": "text/html,*/*"}

REGIONS = ["전체", "인천·경기", "충남", "충북", "전북", "전남", "경북", "경남", "강원", "제주", "기타"]

# 어종과 낚시방식을 분리.
FISH_OPTIONS = ["전체", "문어", "주꾸미", "갑오징어", "광어", "우럭", "참돔", "갈치", "농어", "백조기", "대구", "기타"]
METHOD_OPTIONS = ["전체", "타이라바", "다운샷", "외수질", "생미끼", "지깅", "캐스팅", "텐야", "팁런", "기타"]

METHOD_WORDS = set(METHOD_OPTIONS[1:])
FISH_WORDS = set(FISH_OPTIONS[1:])

# 도시명 -> 대략적인 위도/경도 (날씨·수온 조회용)
CITY_COORDS = {
    "군산": (35.9756, 126.5306),
    "인천": (37.4519, 126.5975),
    "보령": (36.4084, 126.4880),
    "태안": (36.7455, 126.1859),
    "영흥도": (37.2350, 126.4990),
    "홍원": (36.4060, 126.4550),
}

# 바다타임(badatime.com) 지역 ID (국립해양조사원 제공 조석 데이터)
CITY_TIDE_IDS = {
    "군산": 120,
    "인천": 158,
    "보령": 127,
    "태안": 231,
    "영흥도": 151,
    "홍원": 523,
}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_tide_events(city: str, target_iso: str):
    """badatime.com에서 해당 날짜의 만조/간조 시각(HH:MM)과 조위(cm)를 가져온다."""
    city_id = CITY_TIDE_IDS.get(city)
    if not city_id:
        return None
    target_date = datetime.strptime(target_iso, "%Y-%m-%d").date()
    url = f"https://www.badatime.com/{city_id}/daily/{target_date:%Y-%m}"
    try:
        res = requests.get(url, headers=HEADERS_HTML, timeout=15)
        res.raise_for_status()
        res.encoding = res.apparent_encoding or "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")
        text = soup.get_text(" ", strip=True)
    except Exception:
        return None

    WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
    target_wd = WEEKDAY_KR[target_date.weekday()]

    day_anchor_pat = re.compile(r"(\d{1,2})\(([가-힣])\)\s+\d{1,2}\.\d{1,2}")
    anchors = list(day_anchor_pat.finditer(text))

    # 1순위: 날짜 숫자 + 요일이 모두 일치하는 앵커. 못 찾으면 날짜 숫자만이라도 일치하는 첫 앵커.
    target_idx = None
    for i, m in enumerate(anchors):
        if int(m.group(1)) == target_date.day and m.group(2) == target_wd:
            target_idx = i
            break
    if target_idx is None:
        for i, m in enumerate(anchors):
            if int(m.group(1)) == target_date.day:
                target_idx = i
                break
    if target_idx is None:
        return None

    start = anchors[target_idx].end()
    end = anchors[target_idx + 1].start() if target_idx + 1 < len(anchors) else len(text)
    # 안전장치: 하루치 블록은 보통 300자를 넘지 않는다. 경계 탐지가 어긋나 다음 날짜까지
    # 딸려오는 것을 막기 위해 최대 길이를 제한한다.
    end = min(end, start + 400)
    block = text[start:end]

    events = []
    for tm in list(re.finditer(r"(\d{2}:\d{2})\s*\(\s*(\d+)\)\s*▲", block))[:2]:
        events.append({"type": "high", "time": tm.group(1), "height": int(tm.group(2))})
    for tm in list(re.finditer(r"(\d{2}:\d{2})\s*\(\s*(\d+)\)\s*▼", block))[:2]:
        events.append({"type": "low", "time": tm.group(1), "height": int(tm.group(2))})

    if not events:
        return None
    events.sort(key=lambda e: e["time"])
    return events


def build_tide_wave_svg(events):
    """만조/간조 지점을 코사인 보간으로 이어 물결 곡선 SVG를 만든다."""
    if not events or len(events) < 2:
        return None

    W, H = 640, 150
    pad_x, pad_top, pad_bottom = 16, 26, 34

    def to_minutes(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)

    pts = [(to_minutes(e["time"]), e["height"], e["type"]) for e in events]
    heights = [p[1] for p in pts]
    hmin, hmax = min(heights), max(heights)
    if hmax == hmin:
        hmax = hmin + 1

    def x_of(minute):
        return pad_x + (minute / 1440) * (W - 2 * pad_x)

    def y_of(h):
        return pad_top + (1 - (h - hmin) / (hmax - hmin)) * (H - pad_top - pad_bottom)

    path_pts = []
    for i in range(len(pts) - 1):
        t0, h0, _ = pts[i]
        t1, h1, _ = pts[i + 1]
        steps = 20
        last = i == len(pts) - 2
        for s in range(steps + 1 if last else steps):
            frac = s / steps
            t = t0 + (t1 - t0) * frac
            h = h0 + (h1 - h0) / 2 * (1 - math.cos(math.pi * frac))
            path_pts.append((x_of(t), y_of(h)))

    d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in path_pts)
    area_d = d + f" L {path_pts[-1][0]:.1f},{H - pad_bottom + 6:.1f} L {path_pts[0][0]:.1f},{H - pad_bottom + 6:.1f} Z"

    marks = ""
    for t, h, typ in pts:
        x, y = x_of(t), y_of(h)
        hh, mm = divmod(t, 60)
        icon = "▲" if typ == "high" else "▼"
        label_y = y - 12 if typ == "high" else y + 22
        color = "#e0f2fe" if typ == "high" else "#bae6fd"
        marks += (
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="white" stroke="#0369a1" stroke-width="2" />'
            f'<text x="{x:.1f}" y="{label_y:.1f}" font-size="12" fill="{color}" '
            f'text-anchor="middle" font-weight="800">{icon} {hh:02d}:{mm:02d}</text>'
        )

    return f"""
    <svg width="100%" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
      <path d="{area_d}" fill="white" opacity="0.10" />
      <path d="{d}" fill="none" stroke="white" stroke-width="2.5" opacity="0.95" />
      {marks}
    </svg>
    """


def build_tide_wave_svg_compact(events):
    """작은 카드용 미니 물결 곡선 (라벨 없이 곡선+점만)."""
    if not events or len(events) < 2:
        return None

    W, H = 220, 70
    pad_x, pad_top, pad_bottom = 8, 10, 10

    def to_minutes(t):
        h, m = t.split(":")
        return int(h) * 60 + int(m)

    pts = [(to_minutes(e["time"]), e["height"], e["type"]) for e in events]
    heights = [p[1] for p in pts]
    hmin, hmax = min(heights), max(heights)
    if hmax == hmin:
        hmax = hmin + 1

    def x_of(minute):
        return pad_x + (minute / 1440) * (W - 2 * pad_x)

    def y_of(h):
        return pad_top + (1 - (h - hmin) / (hmax - hmin)) * (H - pad_top - pad_bottom)

    path_pts = []
    for i in range(len(pts) - 1):
        t0, h0, _ = pts[i]
        t1, h1, _ = pts[i + 1]
        steps = 14
        last = i == len(pts) - 2
        for s in range(steps + 1 if last else steps):
            frac = s / steps
            t = t0 + (t1 - t0) * frac
            h = h0 + (h1 - h0) / 2 * (1 - math.cos(math.pi * frac))
            path_pts.append((x_of(t), y_of(h)))

    d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in path_pts)
    dots = "".join(
        f'<circle cx="{x_of(t):.1f}" cy="{y_of(h):.1f}" r="3" fill="white" stroke="#0369a1" stroke-width="1.5" />'
        for t, h, _ in pts
    )

    return (
        f'<svg width="100%" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">'
        f'<path d="{d}" fill="none" stroke="white" stroke-width="2" opacity="0.95" />'
        f'{dots}</svg>'
    )


def build_tide_line_text(events):
    """만조/간조를 '▲05:54  ▼00:00  ▲18:06  ▼12:38' 형태의 한 줄로 압축."""
    if not events:
        return ""
    icon = {"high": "▲", "low": "▼"}
    return "   ".join(f"{icon[e['type']]}{e['time']}" for e in events)


MULDDAE_NAMES = ["1물", "2물", "3물", "4물", "5물", "6물", "7물(사리)", "8물(사리)",
                  "9물", "10물", "11물", "12물", "13물", "조금", "무시"]
# 각 물때 단계별 조류 세기(1~5, 5가 가장 강함) — 사리(7~8물) 부근이 가장 세고 조금/무시가 가장 약함
MULDDAE_STRENGTH = [2, 3, 4, 5, 5, 5, 5, 5, 4, 3, 2, 2, 1, 1, 1]


def mulddae_strength_stars(idx: int) -> str:
    n = MULDDAE_STRENGTH[idx]
    return "★" * n + "☆" * (5 - n)


def mulddae_strength_percent(idx: int) -> int:
    return int(MULDDAE_STRENGTH[idx] / 5 * 100)


def estimate_mulddae(target_date: date):
    """음력 계산 라이브러리 없이, 삭(신월) 기준일로부터 경과일을 이용해 물때를 추정한다.
    (실제 물때표와 하루 정도 오차가 있을 수 있는 참고용 수치입니다)
    반환값: (물때이름, 인덱스)"""
    ref_new_moon = date(2000, 1, 6)  # 실제 삭(신월)이었던 기준일
    synodic_month = 29.530588
    days_since = (target_date - ref_new_moon).days
    phase = days_since % synodic_month
    lunar_day = int(phase) + 1  # 1~30
    # 15일 주기로 물때 이름 매핑 (보름/그믐 부근이 사리, 반달 부근이 조금)
    idx = (lunar_day - 1) % 15
    return MULDDAE_NAMES[idx], idx


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_weather(lat: float, lon: float, target_iso: str):
    """Open-Meteo(무료, API키 불필요)로 해당 날짜의 날씨/수온/파고를 가져온다."""
    result = {"weather": None, "sea_temp": None, "wave_height": None, "error": None}
    try:
        w = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,windspeed_10m_max",
                "timezone": "Asia/Seoul",
                "start_date": target_iso, "end_date": target_iso,
            },
            timeout=10,
        ).json()
        daily = w.get("daily", {})
        if daily.get("time"):
            result["weather"] = {
                "최고기온": daily.get("temperature_2m_max", [None])[0],
                "최저기온": daily.get("temperature_2m_min", [None])[0],
                "강수확률": daily.get("precipitation_probability_max", [None])[0],
                "최대풍속": daily.get("windspeed_10m_max", [None])[0],
            }
    except Exception as e:
        result["error"] = str(e)

    try:
        s = requests.get(
            "https://marine-api.open-meteo.com/v1/marine",
            params={
                "latitude": lat, "longitude": lon,
                "daily": "sea_surface_temperature_max,wave_height_max",
                "timezone": "Asia/Seoul",
                "start_date": target_iso, "end_date": target_iso,
            },
            timeout=10,
        ).json()
        daily = s.get("daily", {})
        if daily.get("time") and daily.get("sea_surface_temperature_max"):
            result["sea_temp"] = daily["sea_surface_temperature_max"][0]
        if daily.get("time") and daily.get("wave_height_max"):
            result["wave_height"] = daily["wave_height_max"][0]
    except Exception:
        pass

    return result


def rate_sail_condition(windspeed_kmh, wave_m):
    """풍속(km/h)과 파고(m)로 출조 적합도를 판정한다."""
    if windspeed_kmh is None or wave_m is None:
        return "정보 없음", "#94a3b8", ""
    wind_ms = windspeed_kmh / 3.6
    if wind_ms >= 14 or wave_m >= 3:
        return "출조 취소 가능성", "#C1503F", "풍랑주의보 수준"
    if wind_ms <= 6 and wave_m <= 0.8:
        return "매우 좋음", "#12977A", "바람·파도 모두 양호"
    if wave_m >= 1:
        return "조금 불편", "#D98C2B", "파고 다소 높음"
    return "보통", "#0e7fa6", "출조 가능 범위"


def load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def commit_to_github(filename: str, data) -> tuple[bool, str]:
    """secrets에 GITHUB_TOKEN / GITHUB_REPO가 설정돼 있으면 GitHub에 직접 커밋한다.
    반환값: (성공여부, 메시지)"""
    token = st.secrets.get("GITHUB_TOKEN", "")
    repo = st.secrets.get("GITHUB_REPO", "")
    if not token or not repo:
        return False, "GitHub 자동 저장이 설정되지 않았습니다. (Secrets에 GITHUB_TOKEN/GITHUB_REPO 필요)"

    api_url = f"https://api.github.com/repos/{repo}/contents/{filename}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        get_resp = requests.get(api_url, headers=headers, timeout=10)
        sha = get_resp.json().get("sha") if get_resp.status_code == 200 else None

        content_str = json.dumps(data, ensure_ascii=False, indent=2)
        import base64
        b64_content = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

        payload = {
            "message": f"앱에서 자동 업데이트: {filename}",
            "content": b64_content,
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(api_url, headers=headers, json=payload, timeout=10)
        if put_resp.status_code in (200, 201):
            return True, "GitHub에 영구 저장 완료!"
        else:
            return False, f"GitHub 저장 실패 ({put_resp.status_code}): {put_resp.text[:200]}"
    except Exception as e:
        return False, f"GitHub 저장 중 오류: {e}"


def count_people(value) -> int:
    if not value or value is False:
        return 0
    return sum(int(n) for n in re.findall(r"\((\d+)\)", str(value)))


def build_api_url(base_url: str, target_date: date) -> str:
    start_date = target_date - timedelta(days=4)
    return f"{base_url.rstrip('/')}/ship/schedule_fleet_reservation/{start_date:%Y-%m-%d}/{target_date:%Y-%m-%d}"


def build_detail_url(base_url: str, schedule_no) -> str:
    return f"{base_url.rstrip('/')}/mypage/reservation_ready/{schedule_no}"


def split_species_and_method(raw: str):
    """
    선상24 상세페이지의 '어종 : 참돔 / 타이라바' 같은 값을
    어종=참돔, 낚시방식=타이라바 로 분리한다.
    """
    if not raw:
        return "", ""

    parts = [p.strip() for p in re.split(r"[/,·|]+", raw) if p.strip()]
    species = []
    methods = []

    for part in parts:
        clean = re.sub(r"\s+", " ", part).strip()
        if clean in METHOD_WORDS:
            methods.append(clean)
        elif clean in FISH_WORDS:
            species.append(clean)
        else:
            # 모르는 단어는 일단 어종 쪽에 둔다.
            species.append(clean)

    return " / ".join(species), " / ".join(methods)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_schedule_fleet_page(base_url: str):
    """로그인 없이 볼 수 있는 '출항일정/예약하기' 페이지. 실제 '남은자리' 숫자가 그대로 나온다."""
    url = f"{base_url.rstrip('/')}/ship/schedule_fleet"
    res = requests.get(url, headers=HEADERS_HTML, timeout=15)
    res.raise_for_status()
    res.encoding = res.apparent_encoding or "utf-8"
    soup = BeautifulSoup(res.text, "html.parser")
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{2,}", "\n", text)
    return text, url


def parse_sunsang_site(site: dict, target: date, people: int, fish_filter: str, method_filter: str):
    base = site["base_url"].rstrip("/")
    main_species = site.get("main_species", "")

    try:
        page_text, page_url = fetch_schedule_fleet_page(site["base_url"])
    except Exception as e:
        return [{
            "선사명": site["name"], "주어종": main_species, "권역": site.get("region", ""), "도시": site.get("city", ""), "출항지": site.get("port", ""),
            "어종": "", "낚시방식": "", "가격": "", "출항시간": "", "남은자리": "", "취소인원": "",
            "상태": f"페이지 조회 실패({e})", "예약링크": base, "API": base + "/ship/schedule_fleet",
            "_group": "확인 필요", "_sort": 90
        }]

    # 날짜 블록 찾기: "7월7일(화)" 형태. 다음 날짜가 나오기 전까지를 그 날의 블록으로 본다.
    day_pat = re.compile(rf"{target.month}월\s*{target.day}일\([^)]+\)")
    m = day_pat.search(page_text)
    if not m:
        return [{
            "선사명": site["name"], "주어종": main_species, "권역": site.get("region", ""), "도시": site.get("city", ""), "출항지": site.get("port", ""),
            "어종": "", "낚시방식": "", "가격": "", "출항시간": "", "남은자리": "", "취소인원": "",
            "상태": "해당 날짜 일정 없음/직접 확인", "예약링크": page_url, "API": page_url,
            "_group": "확인 필요", "_sort": 80
        }]

    any_day_pat = re.compile(r"\d+월\s*\d+일\([^)]+\)")
    next_m = any_day_pat.search(page_text, m.end())
    day_block = page_text[m.end(): next_m.start() if next_m else len(page_text)]

    ship_pat = re.compile(r"([가-힣0-9]+호)\s*바로예약(.*?)(?=[가-힣0-9]+호\s*바로예약|\Z)", re.DOTALL)
    matches = list(ship_pat.finditer(day_block))

    if not matches:
        return [{
            "선사명": site["name"], "주어종": main_species, "권역": site.get("region", ""), "도시": site.get("city", ""), "출항지": site.get("port", ""),
            "어종": "", "낚시방식": "", "가격": "", "출항시간": "", "남은자리": "", "취소인원": "",
            "상태": "일정 파싱 실패/직접 확인", "예약링크": page_url, "API": page_url,
            "_group": "확인 필요", "_sort": 80
        }]

    # 어종/방식 필터는 사이트에 등록해둔 '주어종' 기준으로도 확인하되, 페이지에서 직접 읽은 값을 우선한다.
    results = []
    for mm in matches:
        ship_name = mm.group(1)
        detail = mm.group(2)

        raw_fish = ""
        fm = re.search(r"어종\s*[:：]\s*([^\n]+?)(?=운항시간|예약완료|\Z)", detail)
        if fm:
            raw_fish = fm.group(1).strip()
        species, method = split_species_and_method(raw_fish)
        species = species or main_species

        time_text = ""
        tm = re.search(r"운항시간\s*[:：]\s*([\d:]+)\s*~\s*([\d:]+)", detail)
        if tm:
            time_text = f"{tm.group(1)}~{tm.group(2)}"

        remain_m = re.search(r"남은자리\s*(\d+)\s*명", detail)
        if remain_m:
            remain = int(remain_m.group(1))
            if remain <= 0:
                status, group, sort = "마감(남은자리 0)", "마감", 60
            elif remain < people:
                status, group, sort = f"남은자리 {remain}명 (요청인원 {people}명보다 적음)", "확인 필요", 45
            else:
                status, group, sort = f"예약 가능 (남은자리 {remain}명)", "예약 가능", 10
            remain_text = f"{remain}명"
        elif "마감" in detail or "예약마감" in detail:
            status, group, sort = "마감", "마감", 60
            remain_text = "0명"
        elif "선택출항" in detail or "어종을 선택" in detail:
            status, group, sort = "선택출항(어종 지정 필요, 직접 확인)", "확인 필요", 50
            remain_text = ""
        else:
            status, group, sort = "직접 확인 필요", "확인 필요", 80
            remain_text = ""

        # 주어종/어종은 더 이상 검색 제외 기준으로 쓰지 않는다 (참고 정보로만 사용).
        # 대신 어종/낚시방식이 검색 조건과 일치하면 정렬 우선순위를 앞으로 당긴다.
        searchable = f"{species} {method} {main_species}"
        if fish_filter != "전체" and fish_filter in searchable:
            sort -= 3
        if method_filter != "전체" and method_filter in searchable:
            sort -= 2

        results.append({
            "선사명": ship_name, "주어종": main_species, "권역": site.get("region", ""), "도시": site.get("city", ""), "출항지": site.get("port", ""),
            "어종": species, "낚시방식": method,
            "가격": "", "출항시간": time_text,
            "남은자리": remain_text, "취소인원": "",
            "상태": status, "예약링크": page_url,
            "API": page_url, "_group": group, "_sort": sort
        })

    if not results:
        return []

    return results


def check_manual_site(site: dict, target: date, fish_filter: str, method_filter: str):
    try:
        res = requests.get(site["url"], headers=HEADERS_HTML, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        text = soup.get_text(" ", strip=True)

        date_keys = [
            target.strftime("%Y-%m-%d"), target.strftime("%m/%d"), f"{target.month}/{target.day}",
            f"{target.month}월 {target.day}일", f"{target.month}월{target.day}일"
        ]
        has_date = any(k in text for k in date_keys)
        has_booking = any(w in text for w in ["예약", "잔여", "남은자리", "바로예약", "예약가능"])
        has_full = any(w in text for w in ["마감", "만석", "예약완료"])

        if has_full:
            status, group, sort = "마감 키워드 있음", "마감", 60
        elif has_date and has_booking:
            status, group, sort = "예약 가능(키워드 기준, 직접 확인 권장)", "예약 가능", 20
        elif has_booking:
            status, group, sort = "예약 가능 추정(직접 확인 권장)", "예약 가능", 25
        else:
            status, group, sort = "직접 확인 필요", "확인 필요", 80

        # 주어종/어종은 검색 제외 기준이 아니라 참고용. 일치하면 정렬만 앞으로 당긴다.
        main_species = site.get("main_species", "")
        if fish_filter != "전체" and (fish_filter in text or fish_filter in main_species):
            sort -= 3
        if method_filter != "전체" and method_filter in text:
            sort -= 2
    except Exception:
        status, group, sort = "접속 오류/직접 확인 필요", "확인 필요", 90

    return {
        "선사명": site["name"], "주어종": site.get("main_species", ""), "권역": site.get("region", ""), "도시": site.get("city", ""), "출항지": site.get("port", ""),
        "어종": site.get("main_species", ""), "낚시방식": "",
        "가격": "", "출항시간": "", "남은자리": "", "취소인원": "",
        "상태": status, "예약링크": site["url"], "API": "", "_group": group, "_sort": sort
    }


def filter_df(df, available_only, favorites_only, favorites, keyword):
    out = df.copy()
    if available_only:
        out = out[out["_group"] == "예약 가능"]
    if favorites_only:
        out = out[out["선사명"].isin(favorites)]
    if keyword.strip():
        out = out[out["선사명"].str.contains(keyword.strip(), case=False, na=False)]
    return out


SHIP_BANNER_GRADIENTS = [
    "linear-gradient(135deg,#0b3b57,#0e7fa6)",
    "linear-gradient(135deg,#0e7fa6,#14b8a6)",
    "linear-gradient(135deg,#1e3a5f,#2563eb)",
    "linear-gradient(135deg,#155e63,#0ea5a0)",
    "linear-gradient(135deg,#0f3d5c,#4f8ea8)",
]

STATUS_STYLE = {
    "예약 가능": {"badge": "여유", "badge_bg": "#12977A"},
    "확인 필요": {"badge": "마감임박", "badge_bg": "#D98C2B"},
    "마감": {"badge": "마감", "badge_bg": "#C1503F"},
}


def render_grouped_cards(df):
    if df.empty:
        st.info("표시할 결과가 없습니다.")
        return

    for ship_name, group_df in df.groupby("선사명", sort=False):
        with st.expander(f"🚤 {ship_name} ({len(group_df)}개 일정)", expanded=True):
            gradient = SHIP_BANNER_GRADIENTS[hash(ship_name) % len(SHIP_BANNER_GRADIENTS)]
            for _, row in group_df.iterrows():
                st_info = STATUS_STYLE.get(row["_group"], STATUS_STYLE["확인 필요"])
                species = row['어종'] or row.get('주어종') or '어종 확인 필요'
                method_txt = (' · ' + row['낚시방식']) if row['낚시방식'] else ''
                st.markdown(
                    f'<div class="card-v2">'
                    f'<div class="card-banner" style="background:{gradient}">'
                    f'<span class="card-badge" style="background:{st_info["badge_bg"]}">{st_info["badge"]}</span>'
                    f'<span class="card-boat-icon">🚤</span>'
                    f'<span class="card-status-txt">{row["상태"]}</span>'
                    f'</div>'
                    f'<div class="rope-divider"></div>'
                    f'<div class="card-body">'
                    f'<div class="card-name">{row["선사명"]}</div>'
                    f'<div class="card-sub">{species}{method_txt}</div>'
                    f'<div class="card-loc">📍 {row["권역"]} · {row.get("도시","")} · {row["출항지"]}</div>'
                    f'<div class="card-row">'
                    f'<div class="price-tag">{row["가격"] or "가격 문의"}</div>'
                    f'<div class="seat-tag">🪑 {row["남은자리"] or "-"}</div>'
                    f'</div>'
                    f'<div class="card-row" style="color:#7a8794;font-size:12px;margin-top:2px">'
                    f'⏱ {row["출항시간"] or "시간 미정"} {("· 취소 " + row["취소인원"]) if row["취소인원"] else ""}'
                    f'</div>'
                    f'<a class="cta-btn" href="{row["예약링크"]}" target="_blank">예약 페이지 열기 →</a>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )


st.set_page_config(page_title="Fishing Seat Finder", page_icon="🎣", layout="wide")
st.markdown("""
<style>
.main-title{font-size:34px;font-weight:900;margin-bottom:4px}
.sub-title{color:#555;margin-bottom:18px}
.brand-wrap{display:flex;align-items:center;gap:14px;margin-bottom:6px}
.brand-badge{display:inline-flex;align-items:center;justify-content:center;width:52px;height:52px;border-radius:16px;background:linear-gradient(135deg,#0ea5e9,#0369a1);font-size:26px;box-shadow:0 4px 12px rgba(14,165,233,.35)}
.brand-title{font-size:36px;font-weight:900;letter-spacing:-0.5px;
  background:linear-gradient(135deg,#0369a1,#0ea5e9 45%,#10b981);
  -webkit-background-clip:text;background-clip:text;color:transparent;}
.brand-kr{font-size:14px;color:#64748b;font-weight:600;margin-top:-2px}
.result-card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:16px;margin-bottom:12px;box-shadow:0 1px 5px rgba(0,0,0,.06)}
.card-v2{background:#ffffff;border-radius:18px;overflow:hidden;margin-bottom:16px;box-shadow:0 2px 10px rgba(15,45,60,.08);border:1px solid #eef1f0}
.card-banner{position:relative;height:88px;display:flex;align-items:center;justify-content:center}
.card-boat-icon{font-size:38px;opacity:.9;filter:drop-shadow(0 2px 3px rgba(0,0,0,.25))}
.card-badge{position:absolute;top:10px;left:12px;color:white;font-size:11px;font-weight:800;padding:3px 10px;border-radius:999px;letter-spacing:.3px}
.card-status-txt{position:absolute;bottom:8px;right:12px;color:white;font-size:12px;font-weight:700;background:rgba(0,0,0,.22);padding:2px 8px;border-radius:8px}
.rope-divider{height:6px;background:repeating-linear-gradient(-45deg,#dfe7e5 0 6px,#eef3f1 6px 12px)}
.card-body{padding:14px 16px 16px}
.card-name{font-size:18px;font-weight:800;color:#0b3b57}
.card-sub{font-size:13.5px;color:#33474f;margin-top:2px}
.card-loc{font-size:12.5px;color:#7a8794;margin-top:4px}
.card-row{display:flex;justify-content:space-between;align-items:center;margin-top:10px}
.price-tag{font-size:17px;font-weight:800;color:#0b3b57}
.seat-tag{font-size:13px;font-weight:700;color:#12977A;background:#e8f6f1;padding:3px 10px;border-radius:999px}
.cta-btn{display:block;text-align:center;margin-top:12px;padding:9px 0;border-radius:10px;background:#0e7fa6;color:white !important;font-weight:700;font-size:13.5px;text-decoration:none}
.cta-btn:hover{background:#0b3b57}
.card-top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}
.title{font-size:20px;font-weight:800}
.sub{font-size:14px;color:#333;margin-top:2px}
.muted{font-size:13px;color:#777;margin-top:3px}
.status{font-weight:800;color:#0ea5e9;text-align:right}
.meta{display:flex;flex-wrap:wrap;gap:14px;margin:10px 0;color:#333}
.env-wrap{display:flex;gap:14px;flex-wrap:wrap;margin:6px 0 22px}
.env-card.wave{background:linear-gradient(135deg,#0369a1,#0ea5e9)}
.env-card{flex:1;min-width:180px;border-radius:18px;padding:18px 20px;color:white;box-shadow:0 4px 14px rgba(0,0,0,.10)}
.env-card.tide{background:linear-gradient(135deg,#0ea5e9,#0369a1)}
.env-card.strength{background:linear-gradient(135deg,#8b5cf6,#6d28d9)}
.env-card.weather{background:linear-gradient(135deg,#f59e0b,#d97706)}
.env-card.sea{background:linear-gradient(135deg,#10b981,#047857)}
.env-card.sail{background:linear-gradient(135deg,#1e293b,#334155)}
.env-label{font-size:13px;opacity:.9;font-weight:600;margin-bottom:6px}
.env-value{font-size:26px;font-weight:900;line-height:1.15}
.env-sub{font-size:12px;opacity:.85;margin-top:6px}
.tide-row{display:flex;justify-content:space-between;align-items:center;font-size:15px;font-weight:700;margin-top:4px}

@media (max-width: 680px) {
  .brand-title{font-size:26px}
  .brand-badge{width:42px;height:42px;font-size:20px}
  .brand-kr{font-size:11px}
  .env-wrap{gap:8px}
  .env-card{min-width:calc(50% - 4px);padding:9px 10px;flex:0 0 calc(50% - 4px);border-radius:12px}
  .env-value{font-size:16px;line-height:1.1}
  .env-label{font-size:10px;margin-bottom:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .env-sub{font-size:9.5px;margin-top:2px;line-height:1.3}
  .tide-row{font-size:11.5px;margin-top:2px}
  .card-banner{height:72px}
  .card-boat-icon{font-size:30px}
  .card-name{font-size:16px}
  .card-sub{font-size:12.5px}
  .card-loc{font-size:11.5px}
  .price-tag{font-size:15px}
  .seat-tag{font-size:12px;padding:2px 8px}
  .card-badge{font-size:10px;padding:2px 8px}
  .card-status-txt{font-size:11px}
}
</style>
""", unsafe_allow_html=True)

sunsang_sites = load_json(SUNSANG_FILE, [])
manual_sites = load_json(MANUAL_FILE, [])
fishing_logs = load_json(LOG_FILE, [])

st.markdown("""
<div class="brand-wrap">
  <div class="brand-badge">🎣</div>
  <div>
    <div class="brand-title">Fishing Seat Finder</div>
    <div class="brand-kr">MADE BY 인현태 · THANK YOU FOR IDEAS 영탁, 정환형, 귀선형, 정국형</div>
  </div>
</div>
""", unsafe_allow_html=True)
st.markdown(
    '<a href="https://github.com/hyuntaein/fishing-seat-finder" target="_blank" '
    'style="font-size:12px;color:#94a3b8;text-decoration:none">github.com/hyuntaein/fishing-seat-finder ↗</a>',
    unsafe_allow_html=True,
)
st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

top1, top2 = st.columns([1, 1])
with top1:
    target = st.date_input("출조일", value=date.today())
with top2:
    _all_cities = ["전체"] + sorted({s.get("city", "") for s in sunsang_sites + manual_sites if s.get("city")})
    city = st.selectbox("도시 (물때·날씨 기준)", _all_cities)

mulddae, mulddae_idx = estimate_mulddae(target)
stars = mulddae_strength_percent(mulddae_idx)
weather_city = city if city != "전체" else "군산"
coords = CITY_COORDS.get(weather_city)

weather_html = f"<div class='env-value'>예보 범위 밖</div><div class='env-sub'>보통 16일 이내만 제공</div>"
sea_html = f"<div class='env-value'>-</div><div class='env-sub'>조회 실패/범위 밖</div>"
sail_html = "<div class='env-value' style='font-size:16px'>정보 없음</div><div class='env-sub'>예보 범위 밖</div>"
weather_icon = "☀️"
if coords:
    wdata = fetch_weather(*coords, target.strftime("%Y-%m-%d"))
    w = wdata.get("weather")
    sea = wdata.get("sea_temp")
    wave = wdata.get("wave_height")
    if w:
        rain_prob = w["강수확률"] or 0
        if rain_prob >= 50:
            weather_icon = "🌧️"
        elif rain_prob >= 30:
            weather_icon = "⛅"
        else:
            weather_icon = "☀️"
        weather_html = f"<div style='display:flex;align-items:center;gap:10px'><span style='font-size:40px;line-height:1'>{weather_icon}</span><div class='env-value'>{w['최고기온']}° / {w['최저기온']}°</div></div><div class='env-sub'>강수 {rain_prob}% · 풍속 {w['최대풍속']}km/h</div>"

        wind_ms = round(w["최대풍속"] / 3.6, 1) if w["최대풍속"] is not None else None
        label, color, sub = rate_sail_condition(w["최대풍속"], wave)
        wave_txt = f"{wave}m" if wave is not None else "-"
        wind_txt = f"{wind_ms}m/s" if wind_ms is not None else "-"
        sail_html = (
            f"<div class='env-value' style='font-size:17px;color:{color}'>{label}</div>"
            f"<div class='env-sub'>{sub}</div>"
            f"<div class='env-sub' style='margin-top:6px'>💨 {wind_txt} · 🌊 파고 {wave_txt}</div>"
        )
    if sea is not None:
        sea_html = f"<div class='env-value'>{sea}℃</div><div class='env-sub'>{weather_city} 인근 표층수온</div>"

tide_events = fetch_tide_events(weather_city, target.strftime("%Y-%m-%d"))

if tide_events:
    rows_html = ""
    for e in tide_events:
        icon = "▲" if e["type"] == "high" else "▼"
        label = "만조" if e["type"] == "high" else "간조"
        color = "#e0f2fe" if e["type"] == "high" else "#bae6fd"
        rows_html += (
            f"<div class='tide-row' style='color:{color}'>"
            f"<span>{icon} {label}</span><span>{e['time']}</span></div>"
        )
    tide_card_body = f"<div style='margin-top:4px'>{rows_html}</div>"
else:
    tide_card_body = "<div class='env-sub' style='margin-top:8px'>만조·간조 정보 없음</div>"

st.markdown(f"""
<div class="env-wrap">
  <div class="env-card tide">
    <div class="env-label">🌊 {target.strftime('%m/%d')} 물때</div>
    <div class="env-value">{mulddae}</div>
    <div class="env-sub">참고용 추정치</div>
  </div>
  <div class="env-card strength">
    <div class="env-label">🌀 조류세기</div>
    <div class="env-value" style="font-size:26px">{stars}%</div>
    <div class="env-sub">사리 근처일수록 강함</div>
  </div>
  <div class="env-card wave">
    <div class="env-label">⏱️ {weather_city} 만조·간조</div>
    {tide_card_body}
  </div>
  <div class="env-card weather">
    <div class="env-label">{weather_city} 날씨</div>
    {weather_html}
  </div>
  <div class="env-card sea">
    <div class="env-label">🌡️ {weather_city} 수온</div>
    {sea_html}
  </div>
  <div class="env-card sail">
    <div class="env-label">⛵ 출조 적합도</div>
    {sail_html}
  </div>
</div>
""", unsafe_allow_html=True)

left, right = st.columns([1, 3.2], gap="large")

with left:
    st.subheader("검색 조건")

    search_cities = ["전체"] + sorted({s.get("city", "") for s in sunsang_sites + manual_sites if s.get("city")})
    search_city = st.selectbox("도시 (배 검색용)", search_cities)

    people = st.number_input("인원", min_value=1, max_value=30, value=2)
    fish = st.selectbox("어종", FISH_OPTIONS)
    method = st.selectbox("낚시방식", METHOD_OPTIONS)
    st.caption("💡 어종·방식은 결과를 제외하지 않아요. 일치하는 배가 위쪽에 먼저 보여요.")
    region = st.selectbox("권역", REGIONS)

    ports = ["전체"] + sorted({s.get("port", "") for s in sunsang_sites + manual_sites if s.get("port")})
    port = st.selectbox("출항지", ports)


    keyword = st.text_input("선사명 검색", placeholder="예: 참바다, 루키나")

    search = st.button("🔎 실시간 조회", type="primary", use_container_width=True)

    st.divider()
    available_only = st.checkbox("예약 가능만 보기", value=True)
    favorites = st.multiselect("즐겨찾기 선사", [s["name"] for s in sunsang_sites + manual_sites])
    favorites_only = st.checkbox("즐겨찾기만 보기", value=False)
    include_manual = st.checkbox("일반 사이트 포함", value=True)
    group_by_ship = st.checkbox("선사별로 묶어보기", value=True)

    st.divider()
    with st.expander("선상24 사이트 추가"):
        new_name = st.text_input("선사명", key="new_name")
        new_species = st.text_input("주어종 (예: 참돔, 광어)", key="new_species")
        new_url = st.text_input("선상24 주소", placeholder="https://example.sunsang24.com", key="new_url")
        new_region = st.selectbox("권역", REGIONS[1:], key="new_region")
        new_city = st.text_input("도시 (예: 군산)", key="new_city")
        new_port = st.text_input("출항지 (예: 비응항)", key="new_port")
        if st.button("선상24 사이트 저장"):
            if new_name and new_url:
                sunsang_sites.append({"name": new_name, "main_species": new_species, "region": new_region, "city": new_city, "port": new_port, "base_url": new_url.rstrip("/")})
                save_json(SUNSANG_FILE, sunsang_sites)
                ok, msg = commit_to_github("sunsang24_sites.json", sunsang_sites)
                if ok:
                    st.success(f"저장했습니다. {msg} 새로고침(F5) 하면 반영됩니다.")
                else:
                    st.warning(f"임시 저장은 됐지만 GitHub 자동 저장은 실패했어요: {msg}")
            else:
                st.warning("선사명과 주소를 입력하세요.")

    with st.expander("일반 사이트 추가"):
        m_name = st.text_input("선사명", key="m_new_name")
        m_species = st.text_input("주어종 (예: 광어)", key="m_new_species")
        m_url = st.text_input("사이트 주소", placeholder="http://example.co.kr/", key="m_new_url")
        m_region = st.selectbox("권역", REGIONS[1:], key="m_new_region")
        m_city = st.text_input("도시 (예: 보령, 모르면 비워두세요)", key="m_new_city")
        m_port = st.text_input("출항지 (예: 오천항, 모르면 비워두세요)", key="m_new_port")
        if st.button("일반 사이트 저장"):
            if m_name and m_url:
                manual_sites.append({"name": m_name, "main_species": m_species, "region": m_region, "city": m_city, "port": m_port, "url": m_url.strip()})
                save_json(MANUAL_FILE, manual_sites)
                ok, msg = commit_to_github("manual_sites.json", manual_sites)
                if ok:
                    st.success(f"저장했습니다. {msg} 새로고침(F5) 하면 반영됩니다.")
                else:
                    st.warning(f"임시 저장은 됐지만 GitHub 자동 저장은 실패했어요: {msg}")
            else:
                st.warning("선사명과 주소를 입력하세요.")

    with st.expander("사이트 삭제"):
        all_names = [s["name"] for s in sunsang_sites] + [s["name"] for s in manual_sites]
        del_name = st.selectbox("삭제할 선사 선택", ["선택 안함"] + all_names, key="del_name")
        if st.button("삭제", key="del_btn"):
            if del_name != "선택 안함":
                before_s = len(sunsang_sites)
                sunsang_sites[:] = [s for s in sunsang_sites if s["name"] != del_name]
                if len(sunsang_sites) != before_s:
                    save_json(SUNSANG_FILE, sunsang_sites)
                    ok, msg = commit_to_github("sunsang24_sites.json", sunsang_sites)
                else:
                    manual_sites[:] = [s for s in manual_sites if s["name"] != del_name]
                    save_json(MANUAL_FILE, manual_sites)
                    ok, msg = commit_to_github("manual_sites.json", manual_sites)
                if ok:
                    st.success(f"'{del_name}' 삭제했습니다. {msg} 새로고침(F5) 하면 반영됩니다.")
                else:
                    st.warning(f"'{del_name}' 임시 삭제는 됐지만 GitHub 자동 저장은 실패했어요: {msg}")

    st.divider()
    with st.expander("🎣 출조 기록 남기기"):
        log_date = st.date_input("출조일", value=date.today(), key="log_date")
        log_ship = st.text_input("배 이름", placeholder="예: 아쿠아마린호", key="log_ship")
        log_species = st.selectbox("어종", FISH_OPTIONS[1:], key="log_species")
        log_anglers = st.multiselect("출조자", ANGLERS, key="log_anglers")
        log_count = st.text_input("조황 (선택, 예: 참돔4)", key="log_count")
        log_memo = st.text_area("메모", placeholder="자리, 물때, 특이사항 등", key="log_memo", height=100)
        if st.button("기록 저장", key="log_save_btn"):
            if log_ship and log_anglers:
                fishing_logs.append({
                    "date": log_date.strftime("%Y-%m-%d"),
                    "ship": log_ship,
                    "species": log_species,
                    "anglers": log_anglers,
                    "count": log_count,
                    "memo": log_memo,
                })
                save_json(LOG_FILE, fishing_logs)
                ok, msg = commit_to_github("fishing_logs.json", fishing_logs)
                if ok:
                    st.success(f"기록했습니다. {msg} 새로고침(F5) 하면 반영됩니다.")
                else:
                    st.warning(f"임시 저장은 됐지만 GitHub 자동 저장은 실패했어요: {msg}")
            else:
                st.warning("배 이름과 출조자는 최소 1명 입력하세요.")

with right:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("선상24", f"{len(sunsang_sites)}개")
    c2.metric("일반 사이트", f"{len(manual_sites)}개")
    c3.metric("출조일", target.strftime("%m/%d"))
    c4.metric("인원", f"{people}명")

    all_sites_rows = []
    for s in sunsang_sites:
        all_sites_rows.append({
            "구분": "선상24", "선사명": s.get("name", ""), "주어종": s.get("main_species", ""),
            "권역": s.get("region", ""), "도시": s.get("city", ""), "출항지": s.get("port", ""),
            "주소": s.get("base_url", ""),
        })
    for s in manual_sites:
        all_sites_rows.append({
            "구분": "일반", "선사명": s.get("name", ""), "주어종": s.get("main_species", ""),
            "권역": s.get("region", ""), "도시": s.get("city", ""), "출항지": s.get("port", ""),
            "주소": s.get("url", ""),
        })
    with st.expander(f"📋 등록된 사이트 목록 보기 (총 {len(all_sites_rows)}개)"):
        if all_sites_rows:
            st.caption("선상24 = API로 실시간 예약 현황 자동 조회 · 일반 = 홈페이지 텍스트로 대략 판단")
            st.dataframe(
                pd.DataFrame(all_sites_rows),
                use_container_width=True, hide_index=True,
                height=38 * (len(all_sites_rows) + 1) + 3,
                column_config={
                    "주소": st.column_config.LinkColumn("주소", display_text="바로가기 ↗"),
                },
            )
        else:
            st.caption("등록된 사이트가 없습니다.")

    with st.expander(f"🎣 출조 기록 보기 (총 {len(fishing_logs)}건, 최신순)"):
        if fishing_logs:
            log_df = pd.DataFrame(fishing_logs)
            log_df["anglers_txt"] = log_df["anglers"].apply(lambda a: ", ".join(a) if isinstance(a, list) else str(a))
            log_df = log_df.sort_values("date", ascending=False)

            if "editing_log_idx" not in st.session_state:
                st.session_state.editing_log_idx = None

            for orig_idx, r in log_df.iterrows():
                memo_txt = (r["memo"] or "").replace("\n", "<br>")
                card_col, btn_col = st.columns([9, 1])
                with card_col:
                    st.markdown(
                        f"<div style='background:#f8fafc;border:1px solid #e5e7eb;border-radius:12px;"
                        f"padding:10px 14px;margin-bottom:8px'>"
                        f"<div style='font-weight:700;color:#0b3b57'>{r['date']} · {r['ship']} · 🐟{r['species']}"
                        f"{(' · ' + r['count']) if r['count'] else ''}</div>"
                        f"<div style='font-size:12.5px;color:#7a8794;margin-top:2px'>출조자: {r['anglers_txt']}</div>"
                        + (f"<div style='font-size:13.5px;color:#33474f;margin-top:6px;white-space:normal'>{memo_txt}</div>" if memo_txt else "")
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                with btn_col:
                    if st.button("✏️", key=f"edit_log_btn_{orig_idx}", help="이 기록 수정"):
                        st.session_state.editing_log_idx = (
                            None if st.session_state.editing_log_idx == orig_idx else orig_idx
                        )

                if st.session_state.editing_log_idx == orig_idx:
                    target_log = fishing_logs[orig_idx]
                    with st.container(border=True):
                        e_date = st.date_input(
                            "출조일", value=datetime.strptime(target_log["date"], "%Y-%m-%d").date(),
                            key=f"edit_log_date_{orig_idx}",
                        )
                        e_ship = st.text_input("배 이름", value=target_log["ship"], key=f"edit_log_ship_{orig_idx}")
                        e_species = st.selectbox(
                            "어종", FISH_OPTIONS[1:],
                            index=FISH_OPTIONS[1:].index(target_log["species"]) if target_log["species"] in FISH_OPTIONS[1:] else 0,
                            key=f"edit_log_species_{orig_idx}",
                        )
                        e_anglers = st.multiselect(
                            "출조자", ANGLERS, default=target_log.get("anglers", []), key=f"edit_log_anglers_{orig_idx}"
                        )
                        e_count = st.text_input("조황", value=target_log.get("count", ""), key=f"edit_log_count_{orig_idx}")
                        e_memo = st.text_area(
                            "메모", value=target_log.get("memo", ""), key=f"edit_log_memo_{orig_idx}", height=100
                        )

                        c_save, c_del, c_cancel = st.columns(3)
                        if c_save.button("저장", key=f"edit_log_save_{orig_idx}", use_container_width=True):
                            fishing_logs[orig_idx] = {
                                "date": e_date.strftime("%Y-%m-%d"),
                                "ship": e_ship,
                                "species": e_species,
                                "anglers": e_anglers,
                                "count": e_count,
                                "memo": e_memo,
                            }
                            save_json(LOG_FILE, fishing_logs)
                            ok, msg = commit_to_github("fishing_logs.json", fishing_logs)
                            st.session_state.editing_log_idx = None
                            if ok:
                                st.success(f"수정했습니다. {msg} 새로고침(F5) 하면 반영됩니다.")
                            else:
                                st.warning(f"임시 수정은 됐지만 GitHub 자동 저장은 실패했어요: {msg}")
                        if c_del.button("삭제", key=f"edit_log_del_{orig_idx}", use_container_width=True):
                            fishing_logs.pop(orig_idx)
                            save_json(LOG_FILE, fishing_logs)
                            ok, msg = commit_to_github("fishing_logs.json", fishing_logs)
                            st.session_state.editing_log_idx = None
                            if ok:
                                st.success(f"삭제했습니다. {msg} 새로고침(F5) 하면 반영됩니다.")
                            else:
                                st.warning(f"임시 삭제는 됐지만 GitHub 자동 저장은 실패했어요: {msg}")
                        if c_cancel.button("취소", key=f"edit_log_cancel_{orig_idx}", use_container_width=True):
                            st.session_state.editing_log_idx = None
        else:
            st.caption("아직 기록된 출조 기록이 없어요. 왼쪽 '🎣 출조 기록 남기기'에서 추가해보세요.")

    with st.expander("📊 출조 기록 통계"):
        if not fishing_logs:
            st.caption("아직 기록된 출조 기록이 없어요.")
        else:
            log_df = pd.DataFrame(fishing_logs)
            stat_cols = st.columns([2, 1, 1])

            with stat_cols[0]:
                st.markdown("**🧑 출조자별 어종 횟수**")
                exploded = log_df[["species", "anglers"]].explode("anglers").rename(columns={"anglers": "출조자"})
                pivot = exploded.groupby(["출조자", "species"]).size().reset_index(name="횟수")
                summary_rows = []
                for angler, g in pivot.groupby("출조자"):
                    g = g.sort_values("횟수", ascending=False)
                    detail = ", ".join(f"{row['species']}{row['횟수']}" for _, row in g.iterrows())
                    summary_rows.append({"출조자": angler, "총횟수": int(g["횟수"].sum()), "어종별": detail})
                summary_df = pd.DataFrame(summary_rows).sort_values("총횟수", ascending=False)
                st.dataframe(summary_df, use_container_width=True, hide_index=True)

            with stat_cols[1]:
                st.markdown("**🐟 어종별 횟수**")
                species_counts = log_df["species"].value_counts()
                st.dataframe(
                    species_counts.rename_axis("어종").reset_index(name="횟수"),
                    use_container_width=True, hide_index=True,
                )

            with stat_cols[2]:
                st.markdown("**🚤 선사별 횟수**")
                ship_counts = log_df["ship"].value_counts()
                st.dataframe(
                    ship_counts.rename_axis("배").reset_index(name="횟수"),
                    use_container_width=True, hide_index=True,
                )


    if not search:
        st.info("왼쪽에서 조건을 고른 뒤 실시간 조회를 누르세요.")
        st.markdown("""
        **LIVE v2 변경점**
        - 타이라바를 어종이 아닌 낚시방식으로 분리
        - 어종 필터와 낚시방식 필터 분리
        - 선사별 묶어보기 추가
        """)
    else:
        selected_sunsang = [
            s for s in sunsang_sites
            if (region == "전체" or s.get("region") == region)
            and (search_city == "전체" or s.get("city") == search_city)
            and (port == "전체" or s.get("port") == port)
        ]
        selected_manual = [
            s for s in manual_sites
            if include_manual
            and (region == "전체" or s.get("region") == region)
            and (search_city == "전체" or not s.get("city") or s.get("city") == search_city)
            and (port == "전체" or not s.get("port") or s.get("port") == port)
        ]

        st.caption(f"🔍 이번 조건에 해당하는 사이트: 선상24 {len(selected_sunsang)}개 · 일반 {len(selected_manual)}개"
                   + ("" if include_manual else " (일반 사이트 포함 꺼짐)"))

        rows = []
        total = len(selected_sunsang) + len(selected_manual)
        progress = st.progress(0)
        status_box = st.empty()
        idx = 0

        for site in selected_sunsang:
            idx += 1
            status_box.write(f"선상24 조회 중: {site['name']}")
            try:
                rows.extend(parse_sunsang_site(site, target, int(people), fish, method))
            except Exception:
                rows.append({
                    "선사명": site["name"], "주어종": site.get("main_species", ""), "권역": site.get("region", ""), "도시": site.get("city", ""), "출항지": site.get("port", ""),
                    "어종": "", "낚시방식": "", "가격": "", "출항시간": "", "남은자리": "", "취소인원": "",
                    "상태": "조회 오류/직접 확인 필요", "예약링크": site["base_url"],
                    "API": build_api_url(site["base_url"], target), "_group": "확인 필요", "_sort": 95
                })
            progress.progress(idx / max(total, 1))

        for site in selected_manual:
            idx += 1
            status_box.write(f"일반 사이트 확인 중: {site['name']}")
            rows.append(check_manual_site(site, target, fish, method))
            progress.progress(idx / max(total, 1))

        status_box.empty()

        df = pd.DataFrame(rows)
        if df.empty:
            st.warning("조회 결과가 없습니다.")
        else:
            df = df.sort_values(["_sort", "선사명"]).reset_index(drop=True)
            df = filter_df(df, available_only, favorites_only, favorites, keyword)

            available_count = int((df["_group"] == "예약 가능").sum()) if not df.empty else 0
            check_count = int((df["_group"] == "확인 필요").sum()) if not df.empty else 0
            full_count = int((df["_group"] == "마감").sum()) if not df.empty else 0

            st.success(f"조회 완료: 예약 가능 {available_count}건 · 확인 필요 {check_count}건 · 마감 {full_count}건")

            icon_map = {"예약 가능": "🟢", "확인 필요": "🟡", "마감": "🔴"}
            df_view = df.drop(columns=["_group", "_sort"], errors="ignore").copy()
            empty_cols = [c for c in df_view.columns if df_view[c].astype(str).str.strip().replace({"nan": ""}).eq("").all()]
            df_view = df_view.drop(columns=empty_cols)
            df_view.insert(0, "상태표시", df["_group"].map(icon_map).fillna("⚪"))

            st.markdown("**📊 한눈에 보기** — 아래 표에서 정렬·검색이 가능합니다.")
            st.dataframe(
                df_view,
                use_container_width=True,
                hide_index=True,
                height=min(70 + 35 * len(df_view), 560),
                column_config={
                    "상태표시": st.column_config.TextColumn("", width="small"),
                    "예약링크": st.column_config.LinkColumn("예약링크", width="small"),
                    "API": st.column_config.LinkColumn("API", width="small"),
                },
            )

            with st.expander(f"🟢 예약 가능 카드로 보기 ({available_count}건)", expanded=False):
                render_grouped_cards(df[df["_group"] == "예약 가능"])

            with st.expander(f"🟡 확인 필요 카드로 보기 ({check_count}건)", expanded=False):
                render_grouped_cards(df[df["_group"] == "확인 필요"])

            with st.expander(f"🔴 마감 카드로 보기 ({full_count}건)", expanded=False):
                render_grouped_cards(df[df["_group"] == "마감"])
