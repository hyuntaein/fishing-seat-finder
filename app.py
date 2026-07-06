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

    raw_fish = ""
    m = re.search(r"어종\s*[:：]\s*([^\n]+)", text)
    if m:
        raw_fish = m.group(1).strip()

    species, method = split_species_and_method(raw_fish)

    return {
        "detail_url": url,
        "raw_fish": raw_fish,
        "species": species,
        "method": method,
        "price": price,
        "time": time_text,
        "text": text[:1200],
    }


def parse_sunsang_site(site: dict, target: date, people: int, fish_filter: str, method_filter: str):
    target_iso = target.strftime("%Y-%m-%d")
    payload, api_url = fetch_reservation_json(site["base_url"], target_iso)
    rows = payload.get("data", [])
    target_rows = [r for r in rows if r.get("sdate") == target_iso]

    if not target_rows:
        return [{
            "선사명": site["name"], "주어종": site.get("main_species", ""), "권역": site.get("region", ""), "도시": site.get("city", ""), "출항지": site.get("port", ""),
            "어종": "", "낚시방식": "", "가격": "", "출항시간": "", "예약인원": "", "취소인원": "",
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
                "species": "", "method": "", "price": "", "time": "", "text": ""
            }

        searchable = f"{detail.get('species','')} {detail.get('method','')} {detail.get('text','')}"
        parse_failed = not detail.get("species") and not detail.get("method")
        if not parse_failed:
            if fish_filter != "전체" and fish_filter not in searchable:
                continue
            if method_filter != "전체" and method_filter not in searchable:
                continue

        if r.get("reservation_end") is True:
            status, group, sort = "예약 종료", "마감", 60
        elif r.get("reservation_standby"):
            status, group, sort = "대기 가능", "확인 필요", 40
        else:
            status, group, sort = "예약 가능 추정", "예약 가능", 10

        results.append({
            "선사명": site["name"], "주어종": site.get("main_species", ""), "권역": site.get("region", ""), "도시": site.get("city", ""), "출항지": site.get("port", ""),
            "어종": detail.get("species", "") or ("(확인필요)" if parse_failed else ""), "낚시방식": detail.get("method", "") or ("(확인필요)" if parse_failed else ""),
            "가격": detail.get("price", ""), "출항시간": detail.get("time", ""),
            "예약인원": f"{reserved}명", "취소인원": f"{canceled}명" if canceled else "",
            "상태": status, "예약링크": detail.get("detail_url") or build_detail_url(site["base_url"], schedule_no),
            "API": api_url, "_group": group, "_sort": sort
        })

    if not results:
        return [{
            "선사명": site["name"], "주어종": site.get("main_species", ""), "권역": site.get("region", ""), "도시": site.get("city", ""), "출항지": site.get("port", ""),
            "어종": "", "낚시방식": "", "가격": "", "출항시간": "", "예약인원": "", "취소인원": "",
            "상태": "선택 조건 일정 없음", "예약링크": site["base_url"], "API": api_url,
            "_group": "확인 필요", "_sort": 70
        }]

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
        "가격": "", "출항시간": "", "예약인원": "", "취소인원": "",
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
    keyword = st.text_input("선사명 검색", placeholder="예: 참바다, 루키나")

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
                st.success("저장했습니다. 새로고침(F5) 하면 목록에 반영됩니다.")
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
                st.success("저장했습니다. 새로고침(F5) 하면 목록에 반영됩니다.")
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
                else:
                    manual_sites[:] = [s for s in manual_sites if s["name"] != del_name]
                    save_json(MANUAL_FILE, manual_sites)
                st.success(f"'{del_name}' 삭제했습니다. 새로고침(F5) 하면 반영됩니다.")

    search = st.button("🔎 실시간 조회", type="primary", use_container_width=True)

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
                    "어종": "", "낚시방식": "", "가격": "", "출항시간": "", "예약인원": "", "취소인원": "",
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
