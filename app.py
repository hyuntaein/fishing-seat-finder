import json
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

MULDDAE_NAMES = ["1물", "2물", "3물", "4물", "5물", "6물", "7물(사리)", "8물(사리)",
                  "9물", "10물", "11물", "12물", "13물", "조금", "무시"]


def estimate_mulddae(target_date: date) -> str:
    """음력 계산 라이브러리 없이, 삭(신월) 기준일로부터 경과일을 이용해 물때를 추정한다.
    (실제 물때표와 하루 정도 오차가 있을 수 있는 참고용 수치입니다)"""
    ref_new_moon = date(2000, 1, 6)  # 실제 삭(신월)이었던 기준일
    synodic_month = 29.530588
    days_since = (target_date - ref_new_moon).days
    phase = days_since % synodic_month
    lunar_day = int(phase) + 1  # 1~30
    # 15일 주기로 물때 이름 매핑 (보름/그믐 부근이 사리, 반달 부근이 조금)
    idx = (lunar_day - 1) % 15
    return MULDDAE_NAMES[idx]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_weather(lat: float, lon: float, target_iso: str):
    """Open-Meteo(무료, API키 불필요)로 해당 날짜의 날씨/수온을 가져온다."""
    result = {"weather": None, "sea_temp": None, "error": None}
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
                "daily": "sea_surface_temperature_max",
                "timezone": "Asia/Seoul",
                "start_date": target_iso, "end_date": target_iso,
            },
            timeout=10,
        ).json()
        daily = s.get("daily", {})
        if daily.get("time") and daily.get("sea_surface_temperature_max"):
            result["sea_temp"] = daily["sea_surface_temperature_max"][0]
    except Exception:
        pass

    return result


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

        # 필터: 페이지에서 못 읽었을 때만 등록된 주어종으로 대체 판단, 읽었으면 그 값 기준
        searchable = f"{species} {method}"
        if fish_filter != "전체" and searchable.strip() and fish_filter not in searchable:
            continue
        if method_filter != "전체" and method and method_filter not in method:
            continue

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
        has_fish = fish_filter == "전체" or fish_filter in text
        has_method = method_filter == "전체" or method_filter in text
        has_booking = any(w in text for w in ["예약", "잔여", "남은자리", "바로예약", "예약가능"])
        has_full = any(w in text for w in ["마감", "만석", "예약완료"])

        if has_full:
            status, group, sort = "마감 키워드 있음", "마감", 60
        elif has_date and has_fish and has_method and has_booking:
            status, group, sort = "예약 가능(키워드 기준, 직접 확인 권장)", "예약 가능", 20
        elif has_booking and (fish_filter == "전체" or has_fish) and (method_filter == "전체" or has_method):
            status, group, sort = "예약 가능 추정(직접 확인 권장)", "예약 가능", 25
        else:
            status, group, sort = "직접 확인 필요", "확인 필요", 80
    except Exception:
        status, group, sort = "접속 오류/직접 확인 필요", "확인 필요", 90

    return {
        "선사명": site["name"], "주어종": site.get("main_species", ""), "권역": site.get("region", ""), "도시": site.get("city", ""), "출항지": site.get("port", ""),
        "어종": "" if fish_filter == "전체" else fish_filter, "낚시방식": "" if method_filter == "전체" else method_filter,
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


def render_grouped_cards(df):
    if df.empty:
        st.info("표시할 결과가 없습니다.")
        return

    for ship_name, group_df in df.groupby("선사명", sort=False):
        with st.expander(f"🚤 {ship_name} ({len(group_df)}개 일정)", expanded=True):
            for _, row in group_df.iterrows():
                icon = "🟢" if row["_group"] == "예약 가능" else "🔴" if row["_group"] == "마감" else "🟡"
                st.markdown(f"""
                <div class="result-card">
                  <div class="card-top">
                    <div>
                      <div class="title">{icon} {row['선사명']} {('(' + row['주어종'] + ')') if row.get('주어종') else ''}</div>
                      <div class="sub">{row['어종'] or '어종 확인 필요'} {('· ' + row['낚시방식']) if row['낚시방식'] else ''}</div>
                      <div class="muted">{row['권역']} · {row.get('도시','')} · {row['출항지']}</div>
                    </div>
                    <div class="status">{row['상태']}</div>
                  </div>
                  <div class="meta">
                    <span>가격: {row['가격'] or '-'}</span>
                    <span>시간: {row['출항시간'] or '-'}</span>
                    <span>남은자리: {row['남은자리'] or '-'}</span>
                    <span>취소: {row['취소인원'] or '-'}</span>
                  </div>
                  <a href="{row['예약링크']}" target="_blank">예약 페이지 열기</a>
                </div>
                """, unsafe_allow_html=True)


st.set_page_config(page_title="낚시 빈자리 통합검색", page_icon="🎣", layout="wide")
st.markdown("""
<style>
.main-title{font-size:34px;font-weight:900;margin-bottom:4px}
.sub-title{color:#555;margin-bottom:18px}
.result-card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:16px;margin-bottom:12px;box-shadow:0 1px 5px rgba(0,0,0,.06)}
.card-top{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}
.title{font-size:20px;font-weight:800}
.sub{font-size:14px;color:#333;margin-top:2px}
.muted{font-size:13px;color:#777;margin-top:3px}
.status{font-weight:800;color:#0ea5e9;text-align:right}
.meta{display:flex;flex-wrap:wrap;gap:14px;margin:10px 0;color:#333}
</style>
""", unsafe_allow_html=True)

sunsang_sites = load_json(SUNSANG_FILE, [])
manual_sites = load_json(MANUAL_FILE, [])

st.markdown('<div class="main-title">🎣 낚시 빈자리 통합검색 LIVE v2</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">어종과 낚시방식을 분리했습니다. 예: 참돔 = 어종, 타이라바 = 낚시방식.</div>', unsafe_allow_html=True)

left, right = st.columns([1, 3.2], gap="large")

with left:
    st.subheader("검색 조건")

    target = st.date_input("출조일", value=date.today())
    people = st.number_input("인원", min_value=1, max_value=30, value=2)
    fish = st.selectbox("어종", FISH_OPTIONS)
    method = st.selectbox("낚시방식", METHOD_OPTIONS)
    region = st.selectbox("권역", REGIONS)

    cities = ["전체"] + sorted({s.get("city", "") for s in sunsang_sites + manual_sites if s.get("city")})
    city = st.selectbox("도시", cities)
    ports = ["전체"] + sorted({s.get("port", "") for s in sunsang_sites + manual_sites if s.get("port")})
    port = st.selectbox("출항지", ports)

    mulddae = estimate_mulddae(target)
    weather_city = city if city != "전체" else "군산"
    coords = CITY_COORDS.get(weather_city)
    with st.container(border=True):
        st.markdown(f"🌊 **{target.strftime('%m/%d')} 물때** · {mulddae} · <span style='color:#999;font-size:12px'>(참고용 추정치)</span>", unsafe_allow_html=True)
        if coords:
            wdata = fetch_weather(*coords, target.strftime("%Y-%m-%d"))
            w = wdata.get("weather")
            sea = wdata.get("sea_temp")
            if w:
                st.caption(f"☀️ {weather_city} 날씨: 최고 {w['최고기온']}℃ / 최저 {w['최저기온']}℃ · 강수확률 {w['강수확률']}% · 풍속 {w['최대풍속']}km/h")
            else:
                st.caption(f"☀️ {weather_city} 날씨: 예보 범위 밖이거나 조회 실패 (보통 16일 이내 날짜만 지원)")
            if sea is not None:
                st.caption(f"🌡️ {weather_city} 인근 수온: 약 {sea}℃")
            else:
                st.caption("🌡️ 수온: 예보 범위 밖이거나 조회 실패")
        else:
            st.caption("도시를 선택하면 날씨/수온도 같이 보여드려요.")

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
            )
        else:
            st.caption("등록된 사이트가 없습니다.")


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
            and (city == "전체" or s.get("city") == city)
            and (port == "전체" or s.get("port") == port)
        ]
        selected_manual = [
            s for s in manual_sites
            if include_manual
            and (region == "전체" or s.get("region") == region)
            and (city == "전체" or not s.get("city") or s.get("city") == city)
            and (port == "전체" or not s.get("port") or s.get("port") == port)
        ]

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
