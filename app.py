
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
FISH_OPTIONS = ["전체", "문어", "주꾸미", "갑오징어", "광어", "우럭", "참돔", "타이라바", "갈치", "농어", "기타"]


def load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def count_people(value) -> int:
    if not value or value is False:
        return 0
    return sum(int(n) for n in re.findall(r"\((\d+)\)", str(value)))


def build_api_url(base_url: str, target_date: date) -> str:
    start_date = target_date - timedelta(days=4)
    return f"{base_url.rstrip('/')}/ship/schedule_fleet_reservation/{start_date:%Y-%m-%d}/{target_date:%Y-%m-%d}"


def build_detail_url(base_url: str, schedule_no) -> str:
    return f"{base_url.rstrip('/')}/mypage/reservation_ready/{schedule_no}"


@st.cache_data(ttl=300, show_spinner=False)
def fetch_reservation_json(base_url: str, target_date_iso: str):
    target_date = datetime.strptime(target_date_iso, "%Y-%m-%d").date()
    api_url = build_api_url(base_url, target_date)
    res = requests.get(api_url, headers=HEADERS_JSON, timeout=15)
    res.raise_for_status()
    return res.json(), api_url


@st.cache_data(ttl=300, show_spinner=False)
def fetch_detail_page(base_url: str, schedule_no):
    url = build_detail_url(base_url, schedule_no)
    res = requests.get(url, headers=HEADERS_HTML, timeout=15)
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    text = soup.get_text("\n", strip=True)

    price = ""
    m = re.search(r"(\d{1,3}(?:,\d{3})+원)", text)
    if m:
        price = m.group(1)

    time_text = ""
    m = re.search(r"(\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2})", text)
    if m:
        time_text = re.sub(r"\s+", "", m.group(1))

    fish = ""
    m = re.search(r"어종\s*[:：]\s*([^\n]+)", text)
    if m:
        fish = m.group(1).strip()

    lines = [x.strip() for x in text.splitlines() if x.strip()]
    ship_title = lines[0] if lines else ""

    return {
        "detail_url": url,
        "ship_title": ship_title,
        "fish": fish,
        "price": price,
        "time": time_text,
        "text": text[:1200],
    }


def parse_sunsang_site(site: dict, target: date, people: int, fish_filter: str):
    target_iso = target.strftime("%Y-%m-%d")
    payload, api_url = fetch_reservation_json(site["base_url"], target_iso)
    rows = payload.get("data", [])
    target_rows = [r for r in rows if r.get("sdate") == target_iso]

    if not target_rows:
        return [{
            "선사명": site["name"], "권역": site.get("region", ""), "출항지": site.get("port", ""),
            "어종": "", "가격": "", "출항시간": "", "예약인원": "", "취소인원": "",
            "상태": "일정 없음/확인 필요", "예약링크": site["base_url"], "API": api_url,
            "_group": "확인 필요", "_sort": 80
        }]

    results = []
    for r in target_rows:
        schedule_no = r.get("ship_schedule_no")
        reserved = (
            count_people(r.get("reservation_ready")) +
            count_people(r.get("reservation_new_ready")) +
            count_people(r.get("reservation_fishing_ready"))
        )
        canceled = (
            count_people(r.get("reservation_cancel")) +
            count_people(r.get("reservation_cancel_ready")) +
            count_people(r.get("reservation_new_cancel")) +
            count_people(r.get("reservation_new_cancel_ready"))
        )

        try:
            detail = fetch_detail_page(site["base_url"], schedule_no)
        except Exception:
            detail = {
                "detail_url": build_detail_url(site["base_url"], schedule_no),
                "fish": "", "price": "", "time": "", "text": ""
            }

        searchable_text = f"{detail.get('fish','')} {detail.get('text','')}"
        if fish_filter != "전체" and fish_filter not in searchable_text:
            continue

        if r.get("reservation_end") is True:
            status, group, sort = "예약 종료", "마감", 60
        elif r.get("reservation_standby"):
            status, group, sort = "대기 가능", "확인 필요", 40
        else:
            status, group, sort = "예약 가능 추정", "예약 가능", 10

        results.append({
            "선사명": site["name"], "권역": site.get("region", ""), "출항지": site.get("port", ""),
            "어종": detail.get("fish", ""), "가격": detail.get("price", ""), "출항시간": detail.get("time", ""),
            "예약인원": f"{reserved}명", "취소인원": f"{canceled}명" if canceled else "",
            "상태": status, "예약링크": detail.get("detail_url") or build_detail_url(site["base_url"], schedule_no),
            "API": api_url, "_group": group, "_sort": sort
        })

    if not results:
        return [{
            "선사명": site["name"], "권역": site.get("region", ""), "출항지": site.get("port", ""),
            "어종": "", "가격": "", "출항시간": "", "예약인원": "", "취소인원": "",
            "상태": "선택 어종 일정 없음", "예약링크": site["base_url"], "API": api_url,
            "_group": "확인 필요", "_sort": 70
        }]

    return results


def check_manual_site(site: dict, target: date, fish_filter: str):
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
        has_booking = any(w in text for w in ["예약", "잔여", "남은자리", "바로예약", "예약가능"])
        has_full = any(w in text for w in ["마감", "만석", "예약완료"])

        if has_date and has_fish and has_booking:
            status, group, sort = "예약 키워드 있음", "확인 필요", 30
        elif has_full:
            status, group, sort = "마감 키워드 있음", "마감", 60
        else:
            status, group, sort = "직접 확인 필요", "확인 필요", 80
    except Exception:
        status, group, sort = "접속 오류/직접 확인 필요", "확인 필요", 90

    return {
        "선사명": site["name"], "권역": site.get("region", ""), "출항지": site.get("port", ""),
        "어종": "" if fish_filter == "전체" else fish_filter, "가격": "", "출항시간": "",
        "예약인원": "", "취소인원": "", "상태": status, "예약링크": site["url"], "API": "",
        "_group": group, "_sort": sort
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


def render_cards(df, page_size, key_prefix):
    if df.empty:
        st.info("표시할 결과가 없습니다.")
        return

    page_count = max((len(df) - 1) // page_size + 1, 1)
    page = st.number_input("페이지", 1, page_count, 1, key=f"page_{key_prefix}") if page_count > 1 else 1
    part = df.iloc[(page - 1) * page_size: page * page_size]

    for _, row in part.iterrows():
        icon = "🟢" if row["_group"] == "예약 가능" else "🔴" if row["_group"] == "마감" else "🟡"
        st.markdown(f"""
        <div class="result-card">
          <div class="card-top">
            <div>
              <div class="title">{icon} {row['선사명']}</div>
              <div class="muted">{row['권역']} · {row['출항지']} · {row['어종'] or '어종 확인 필요'}</div>
            </div>
            <div class="status">{row['상태']}</div>
          </div>
          <div class="meta">
            <span>가격: {row['가격'] or '-'}</span>
            <span>시간: {row['출항시간'] or '-'}</span>
            <span>예약인원: {row['예약인원'] or '-'}</span>
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
.muted{font-size:13px;color:#777;margin-top:3px}
.status{font-weight:800;color:#0ea5e9;text-align:right}
.meta{display:flex;flex-wrap:wrap;gap:14px;margin:10px 0;color:#333}
</style>
""", unsafe_allow_html=True)

sunsang_sites = load_json(SUNSANG_FILE, [])
manual_sites = load_json(MANUAL_FILE, [])

st.markdown('<div class="main-title">🎣 낚시 빈자리 통합검색 LIVE v1</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">선상24 예약현황 JSON과 예약 상세페이지를 실제로 조회합니다.</div>', unsafe_allow_html=True)

left, right = st.columns([1, 2.3], gap="large")

with left:
    st.subheader("검색 조건")
    target = st.date_input("출조일", value=date.today())
    people = st.number_input("인원", min_value=1, max_value=30, value=2)
    fish = st.selectbox("어종", FISH_OPTIONS)
    region = st.selectbox("권역", REGIONS)

    ports = ["전체"] + sorted({s.get("port", "") for s in sunsang_sites + manual_sites if s.get("port")})
    port = st.selectbox("출항지", ports)
    keyword = st.text_input("선사명 검색", placeholder="예: 참바다, 루키나")

    st.divider()
    available_only = st.checkbox("예약 가능만 보기", value=True)
    favorites = st.multiselect("즐겨찾기 선사", [s["name"] for s in sunsang_sites + manual_sites])
    favorites_only = st.checkbox("즐겨찾기만 보기", value=False)
    include_manual = st.checkbox("일반 사이트 포함", value=True)
    page_size = st.selectbox("몇 개씩 보기", [10, 20, 50, 100], index=1)

    st.divider()
    with st.expander("선상24 사이트 추가"):
        new_name = st.text_input("선사명", key="new_name")
        new_url = st.text_input("선상24 주소", placeholder="https://example.sunsang24.com", key="new_url")
        new_region = st.selectbox("권역", REGIONS[1:], key="new_region")
        new_port = st.text_input("출항지", key="new_port")
        if st.button("선상24 사이트 저장"):
            if new_name and new_url:
                sunsang_sites.append({"name": new_name, "region": new_region, "port": new_port, "base_url": new_url.rstrip("/")})
                save_json(SUNSANG_FILE, sunsang_sites)
                st.success("저장했습니다. 새로고침 후 반영됩니다.")

    with st.expander("일반 사이트 추가"):
        m_name = st.text_input("사이트명", key="m_name")
        m_url = st.text_input("URL", key="m_url")
        m_region = st.selectbox("권역", REGIONS[1:], key="m_region")
        m_port = st.text_input("출항지", key="m_port")
        if st.button("일반 사이트 저장"):
            if m_name and m_url:
                manual_sites.append({"name": m_name, "region": m_region, "port": m_port, "url": m_url})
                save_json(MANUAL_FILE, manual_sites)
                st.success("저장했습니다. 새로고침 후 반영됩니다.")

    search = st.button("🔎 실시간 조회", type="primary", use_container_width=True)

with right:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("선상24", f"{len(sunsang_sites)}개")
    c2.metric("일반 사이트", f"{len(manual_sites)}개")
    c3.metric("출조일", target.strftime("%m/%d"))
    c4.metric("인원", f"{people}명")

    if not search:
        st.info("왼쪽에서 조건을 고른 뒤 실시간 조회를 누르세요.")
        st.markdown("""
        **LIVE v1 기능**
        - 선상24 예약현황 JSON 실제 조회
        - 예약 상세페이지에서 어종/가격/출항시간 추출
        - 예약자 목록에서 예약인원 계산
        - 권역/출항지/어종/선사명 필터
        """)
    else:
        selected_sunsang = [
            s for s in sunsang_sites
            if (region == "전체" or s.get("region") == region)
            and (port == "전체" or s.get("port") == port)
        ]
        selected_manual = [
            s for s in manual_sites
            if include_manual
            and (region == "전체" or s.get("region") == region)
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
                rows.extend(parse_sunsang_site(site, target, int(people), fish))
            except Exception:
                rows.append({
                    "선사명": site["name"], "권역": site.get("region", ""), "출항지": site.get("port", ""),
                    "어종": "", "가격": "", "출항시간": "", "예약인원": "", "취소인원": "",
                    "상태": "조회 오류/직접 확인 필요", "예약링크": site["base_url"],
                    "API": build_api_url(site["base_url"], target), "_group": "확인 필요", "_sort": 95
                })
            progress.progress(idx / max(total, 1))

        for site in selected_manual:
            idx += 1
            status_box.write(f"일반 사이트 확인 중: {site['name']}")
            rows.append(check_manual_site(site, target, fish))
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

            with st.expander(f"🟢 예약 가능 ({available_count}건)", expanded=True):
                render_cards(df[df["_group"] == "예약 가능"], page_size, "available")

            with st.expander(f"🟡 확인 필요 ({check_count}건)", expanded=False):
                render_cards(df[df["_group"] == "확인 필요"], page_size, "check")

            with st.expander(f"🔴 마감 ({full_count}건)", expanded=False):
                render_cards(df[df["_group"] == "마감"], page_size, "full")

            st.subheader("표 형태")
            st.dataframe(
                df.drop(columns=["_group", "_sort"], errors="ignore"),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "예약링크": st.column_config.LinkColumn("예약링크"),
                    "API": st.column_config.LinkColumn("API"),
                },
            )
