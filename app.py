
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup


APP_DIR = Path(__file__).parent
SUNSANG_FILE = APP_DIR / "sunsang24_sites.json"
MANUAL_FILE = APP_DIR / "manual_sites.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "application/json,text/html,*/*",
}

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
    """
    '홍길동님(2) / 김철수님(1)' 형식에서 예약 인원 합계 계산.
    """
    if not value or value is False:
        return 0
    nums = re.findall(r"\((\d+)\)", str(value))
    return sum(int(n) for n in nums)


def decode_text(value) -> str:
    if not value or value is False:
        return ""
    return str(value)


def reservation_api_url(base_url: str, target: date) -> str:
    # 선상24가 화면에서 사용하는 방식: 시작일/조회일.
    # 시작일은 조회일 기준 4일 전으로 잡으면 해당 날짜가 포함된 데이터가 내려오는 경우가 많음.
    start = target - timedelta(days=4)
    return f"{base_url.rstrip('/')}/ship/schedule_fleet_reservation/{start:%Y-%m-%d}/{target:%Y-%m-%d}"


def reservation_detail_url(base_url: str, schedule_no) -> str:
    return f"{base_url.rstrip('/')}/mypage/reservation_ready/{schedule_no}"


def get_reservation_rows(site: dict, target: date) -> list[dict]:
    url = reservation_api_url(site["base_url"], target)
    res = requests.get(url, headers=HEADERS, timeout=15)
    res.raise_for_status()
    payload = res.json()

    rows = payload.get("data", [])
    target_str = target.strftime("%Y-%m-%d")
    return [r for r in rows if r.get("sdate") == target_str]


def fetch_detail_info(site: dict, schedule_no) -> dict:
    """
    예약 상세 페이지에서 배 이름, 어종, 가격, 시간 등을 텍스트 기반으로 추출.
    선상24 공통 페이지 구조를 느슨하게 파싱한다.
    """
    url = reservation_detail_url(site["base_url"], schedule_no)
    try:
        res = requests.get(url, headers={**HEADERS, "Accept": "text/html,*/*"}, timeout=15)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        text = soup.get_text("\n", strip=True)

        info = {
            "ship_title": "",
            "fish": "",
            "price": "",
            "time": "",
            "detail_text": text[:500],
        }

        # 가격: 110,000원
        m = re.search(r"(\d{1,3}(?:,\d{3})+원)", text)
        if m:
            info["price"] = m.group(1)

        # 시간: 04:00~17:00 또는 04:00 ~ 17:00
        m = re.search(r"(\d{1,2}:\d{2}\s*~\s*\d{1,2}:\d{2})", text)
        if m:
            info["time"] = re.sub(r"\s+", "", m.group(1))

        # 어종 : 참돔 / 타이라바
        m = re.search(r"어종\s*[:：]\s*([^\n]+)", text)
        if m:
            info["fish"] = m.group(1).strip()

        # 페이지 상단 제목 후보
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        if lines:
            info["ship_title"] = lines[0]

        return info
    except Exception:
        return {"ship_title": "", "fish": "", "price": "", "time": "", "detail_text": ""}


def check_sunsang_site(site: dict, target: date, people: int, fish_filter: str) -> list[dict]:
    rows = get_reservation_rows(site, target)
    results = []

    if not rows:
        return [{
            "사이트명": site["name"],
            "권역": site.get("region", ""),
            "출항지": site.get("port", ""),
            "어종": "",
            "가격": "",
            "출항시간": "",
            "예약인원": "",
            "상태": "일정 없음/확인 필요",
            "예약링크": site["base_url"],
            "_status_group": "확인 필요",
            "_sort": 80
        }]

    for r in rows:
        schedule_no = r.get("ship_schedule_no")
        reserved_people = count_people(r.get("reservation_ready")) + count_people(r.get("reservation_new_ready"))
        cancel_people = count_people(r.get("reservation_cancel")) + count_people(r.get("reservation_cancel_ready"))

        detail = fetch_detail_info(site, schedule_no)
        fish = detail.get("fish") or ""

        if fish_filter != "전체" and fish_filter not in fish and fish_filter not in detail.get("detail_text", ""):
            continue

        if r.get("reservation_end") is True:
            status = "예약 종료"
            group = "마감"
            sort = 60
        elif r.get("reservation_standby"):
            status = "대기 가능"
            group = "확인 필요"
            sort = 40
        else:
            # 정원 데이터는 아직 확보되지 않았으므로 예약 가능 페이지가 열리는 경우를 예약 가능으로 추정.
            status = "예약 가능 추정"
            group = "예약 가능"
            sort = 10

        results.append({
            "사이트명": site["name"],
            "권역": site.get("region", ""),
            "출항지": site.get("port", ""),
            "어종": fish,
            "가격": detail.get("price", ""),
            "출항시간": detail.get("time", ""),
            "예약인원": f"{reserved_people}명" if reserved_people else "0명",
            "상태": status,
            "예약링크": reservation_detail_url(site["base_url"], schedule_no),
            "_status_group": group,
            "_sort": sort
        })

    if not results:
        return [{
            "사이트명": site["name"],
            "권역": site.get("region", ""),
            "출항지": site.get("port", ""),
            "어종": "",
            "가격": "",
            "출항시간": "",
            "예약인원": "",
            "상태": "선택 어종 일정 없음",
            "예약링크": site["base_url"],
            "_status_group": "확인 필요",
            "_sort": 70
        }]

    return results


def check_manual_site(site: dict, target: date, fish_filter: str) -> dict:
    try:
        res = requests.get(site["base_url"], headers={**HEADERS, "Accept": "text/html,*/*"}, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        text = soup.get_text(" ", strip=True)

        date_keys = [
            target.strftime("%Y-%m-%d"),
            target.strftime("%m/%d"),
            f"{target.month}/{target.day}",
            f"{target.month}월 {target.day}일",
            f"{target.month}월{target.day}일",
        ]

        has_date = any(k in text for k in date_keys)
        has_fish = fish_filter == "전체" or fish_filter in text
        has_booking = any(w in text for w in ["예약", "잔여", "남은자리", "바로예약", "예약가능"])
        has_full = any(w in text for w in ["마감", "만석", "예약완료"])

        if has_date and has_fish and has_booking:
            status = "예약 키워드 있음"
            group = "확인 필요"
            sort = 30
        elif has_full:
            status = "마감 키워드 있음"
            group = "마감"
            sort = 60
        else:
            status = "직접 확인 필요"
            group = "확인 필요"
            sort = 80

        return {
            "사이트명": site["name"],
            "권역": site.get("region", ""),
            "출항지": site.get("port", ""),
            "어종": "" if fish_filter == "전체" else fish_filter,
            "가격": "",
            "출항시간": "",
            "예약인원": "",
            "상태": status,
            "예약링크": site["base_url"],
            "_status_group": group,
            "_sort": sort
        }
    except Exception as e:
        return {
            "사이트명": site["name"],
            "권역": site.get("region", ""),
            "출항지": site.get("port", ""),
            "어종": "" if fish_filter == "전체" else fish_filter,
            "가격": "",
            "출항시간": "",
            "예약인원": "",
            "상태": f"접속 오류/직접 확인 필요",
            "예약링크": site["base_url"],
            "_status_group": "확인 필요",
            "_sort": 90
        }


def apply_filters(df: pd.DataFrame, available_only: bool, favorites_only: bool, favorites: list[str], search_name: str):
    result = df.copy()
    if available_only:
        result = result[result["_status_group"] == "예약 가능"]
    if favorites_only:
        result = result[result["사이트명"].isin(favorites)]
    if search_name.strip():
        keyword = search_name.strip()
        result = result[result["사이트명"].str.contains(keyword, case=False, na=False)]
    return result


def render_result_cards(df: pd.DataFrame, page_size: int):
    if df.empty:
        st.warning("조건에 맞는 결과가 없습니다.")
        return

    page_count = max((len(df) - 1) // page_size + 1, 1)
    page = st.number_input("페이지", min_value=1, max_value=page_count, value=1, step=1)
    start = (page - 1) * page_size
    end = start + page_size
    visible = df.iloc[start:end]

    for _, row in visible.iterrows():
        group = row["_status_group"]
        icon = "🟢" if group == "예약 가능" else "🔴" if group == "마감" else "🟡"
        st.markdown(f"""
        <div class="result-card">
            <div class="result-head">
                <div>
                    <div class="result-title">{icon} {row['사이트명']}</div>
                    <div class="muted">{row['권역']} · {row['출항지']} · {row['어종'] or '어종 확인 필요'}</div>
                </div>
                <div class="status">{row['상태']}</div>
            </div>
            <div class="result-meta">
                <span>가격: {row['가격'] or '-'}</span>
                <span>시간: {row['출항시간'] or '-'}</span>
                <span>예약인원: {row['예약인원'] or '-'}</span>
            </div>
            <a href="{row['예약링크']}" target="_blank">예약 페이지 열기</a>
        </div>
        """, unsafe_allow_html=True)


st.set_page_config(page_title="낚시 빈자리 통합검색", page_icon="🎣", layout="wide")

st.markdown("""
<style>
.main-title{font-size:34px;font-weight:900;margin-bottom:2px}
.sub-title{color:#666;margin-bottom:18px}
.result-card{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:16px;margin-bottom:12px;box-shadow:0 1px 5px rgba(0,0,0,.05)}
.result-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.result-title{font-size:20px;font-weight:800}
.status{font-weight:800;color:#2563eb}
.muted{color:#777;font-size:13px}
.result-meta{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0;color:#333}
.small{font-size:13px;color:#777}
</style>
""", unsafe_allow_html=True)

sunsang_sites = load_json(SUNSANG_FILE, [])
manual_sites = load_json(MANUAL_FILE, [])

st.markdown('<div class="main-title">🎣 낚시 빈자리 통합검색</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">선상24 기반 개인용 검색 앱 MVP입니다. 예약 가능 여부는 현재 “예약 가능 추정”으로 표시됩니다.</div>', unsafe_allow_html=True)

left, right = st.columns([1, 2.2], gap="large")

with left:
    st.subheader("검색 조건")
    target = st.date_input("출조일", value=date.today())
    people = st.number_input("인원", min_value=1, max_value=30, value=2)
    fish = st.selectbox("어종", FISH_OPTIONS)
    region = st.selectbox("권역", REGIONS)

    ports = ["전체"] + sorted({s.get("port", "") for s in sunsang_sites + manual_sites if s.get("port")})
    port = st.selectbox("출항지", ports)

    search_name = st.text_input("선사명 검색", placeholder="예: 참바다, 루키나")

    st.divider()
    available_only = st.checkbox("예약 가능만 보기", value=True)
    favorites = st.multiselect(
        "즐겨찾기 선사",
        [s["name"] for s in sunsang_sites + manual_sites],
        default=[]
    )
    favorites_only = st.checkbox("즐겨찾기만 보기", value=False)
    include_manual = st.checkbox("사용자 추가 사이트 포함", value=True)

    page_size = st.selectbox("몇 개씩 보기", [10, 20, 50, 100], index=1)

    st.divider()
    st.subheader("사이트 추가")
    with st.expander("선상24 사이트 추가"):
        new_name = st.text_input("선사명", key="new_sunsang_name")
        new_base_url = st.text_input("선상24 주소", placeholder="https://example.sunsang24.com", key="new_sunsang_url")
        new_region = st.selectbox("권역", REGIONS[1:], key="new_sunsang_region")
        new_port = st.text_input("출항지", key="new_sunsang_port")
        if st.button("선상24 사이트 저장"):
            if new_name and new_base_url:
                sunsang_sites.append({
                    "name": new_name,
                    "platform": "sunsang24",
                    "region": new_region,
                    "port": new_port,
                    "base_url": new_base_url.rstrip("/")
                })
                save_json(SUNSANG_FILE, sunsang_sites)
                st.success("저장했습니다. 새로고침 후 반영됩니다.")

    with st.expander("일반 사이트 추가"):
        m_name = st.text_input("사이트명", key="manual_name")
        m_url = st.text_input("URL", key="manual_url")
        m_region = st.selectbox("권역", REGIONS[1:], key="manual_region")
        m_port = st.text_input("출항지", key="manual_port")
        if st.button("일반 사이트 저장"):
            if m_name and m_url:
                manual_sites.append({
                    "name": m_name,
                    "platform": "manual",
                    "region": m_region,
                    "port": m_port,
                    "base_url": m_url
                })
                save_json(MANUAL_FILE, manual_sites)
                st.success("저장했습니다. 새로고침 후 반영됩니다.")

    search = st.button("🔎 빈자리 검색", type="primary", use_container_width=True)

with right:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("선상24", f"{len(sunsang_sites)}개")
    c2.metric("일반 사이트", f"{len(manual_sites)}개")
    c3.metric("출조일", target.strftime("%m/%d"))
    c4.metric("인원", f"{people}명")

    if search:
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

        for idx, site in enumerate(selected_sunsang, start=1):
            status_box.write(f"선상24 조회 중: {site['name']}")
            try:
                rows.extend(check_sunsang_site(site, target, int(people), fish))
            except Exception as e:
                rows.append({
                    "사이트명": site["name"],
                    "권역": site.get("region", ""),
                    "출항지": site.get("port", ""),
                    "어종": "",
                    "가격": "",
                    "출항시간": "",
                    "예약인원": "",
                    "상태": "조회 오류/직접 확인 필요",
                    "예약링크": site["base_url"],
                    "_status_group": "확인 필요",
                    "_sort": 95
                })
            progress.progress(idx / max(total, 1))

        offset = len(selected_sunsang)
        for j, site in enumerate(selected_manual, start=1):
            status_box.write(f"일반 사이트 확인 중: {site['name']}")
            rows.append(check_manual_site(site, target, fish))
            progress.progress((offset + j) / max(total, 1))

        status_box.empty()

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values(["_sort", "사이트명"]).reset_index(drop=True)
            df = apply_filters(df, available_only, favorites_only, favorites, search_name)

            available_count = int((df["_status_group"] == "예약 가능").sum()) if not df.empty else 0
            check_count = int((df["_status_group"] == "확인 필요").sum()) if not df.empty else 0
            full_count = int((df["_status_group"] == "마감").sum()) if not df.empty else 0

            st.success(f"조회 완료: 예약 가능 {available_count}건 · 확인 필요 {check_count}건 · 마감 {full_count}건")

            with st.expander(f"🟢 예약 가능 ({available_count}건)", expanded=True):
                render_result_cards(df[df["_status_group"] == "예약 가능"], page_size)

            with st.expander(f"🟡 확인 필요 ({check_count}건)", expanded=False):
                render_result_cards(df[df["_status_group"] == "확인 필요"], page_size)

            with st.expander(f"🔴 마감 ({full_count}건)", expanded=False):
                render_result_cards(df[df["_status_group"] == "마감"], page_size)

            st.subheader("표 형태")
            visible = df.drop(columns=["_status_group", "_sort"], errors="ignore")
            st.dataframe(
                visible,
                use_container_width=True,
                hide_index=True,
                column_config={"예약링크": st.column_config.LinkColumn("예약링크")}
            )
        else:
            st.warning("조회 결과가 없습니다.")
    else:
        st.info("왼쪽에서 조건을 고른 뒤 빈자리 검색을 누르세요.")
        st.markdown("""
        **현재 MVP에서 되는 것**
        - 선상24 예약현황 JSON 조회
        - 예약 상세페이지에서 어종/가격/시간 추출
        - 권역/출항지/어종/선사명 필터
        - 예약 가능·확인 필요·마감 그룹 접기/펼치기
        - 10개/20개/50개/100개씩 보기
        - 선상24 사이트 추가
        - 일반 사이트 추가
        """)
