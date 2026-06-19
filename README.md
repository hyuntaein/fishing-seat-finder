# 낚시 빈자리 통합검색 MVP

개인용 Streamlit 웹앱입니다.

## 1. 무료 배포 순서

### 1단계: GitHub 저장소 만들기
1. https://github.com 접속
2. 로그인
3. 오른쪽 위 `+` 클릭
4. `New repository` 클릭
5. Repository name 예시:
   `fishing-seat-finder`
6. Public 선택
7. Create repository 클릭

### 2단계: 파일 업로드
1. 만든 GitHub 저장소로 이동
2. `Add file` 클릭
3. `Upload files` 클릭
4. 이 압축파일을 푼 폴더 안의 파일들을 전부 업로드
5. `Commit changes` 클릭

업로드해야 하는 주요 파일:
- app.py
- requirements.txt
- sunsang24_sites.json
- manual_sites.json
- README.md
- .streamlit/config.toml

### 3단계: Streamlit Cloud 배포
1. https://streamlit.io/cloud 접속
2. GitHub 계정으로 로그인
3. `New app` 클릭
4. Repository에서 `fishing-seat-finder` 선택
5. Branch는 `main`
6. Main file path는 `app.py`
7. Deploy 클릭

배포가 끝나면 이런 주소가 생깁니다.

`https://fishing-seat-finder.streamlit.app`

---

## 2. 로컬 실행

```bash
pip install -r requirements.txt
streamlit run app.py
```

---

## 3. 현재 기능

- 선상24 예약현황 JSON 조회
- 예약 상세페이지에서 어종, 가격, 출항시간 추출
- 권역 필터
- 출항지 필터
- 선사명 검색
- 예약 가능만 보기
- 즐겨찾기만 보기
- 결과 접기/펼치기
- 10개/20개/50개/100개씩 보기
- 선상24 사이트 추가
- 일반 사이트 추가

---

## 4. 현재 제한

선상24 API에서 정원/남은자리 필드는 아직 확인되지 않았습니다.
현재는 예약 상세페이지가 열리는 일정에 대해 `예약 가능 추정`으로 표시합니다.

---

## 5. 초기 등록된 선상24 사이트

- 군산 참바다호
- 군산 후크스타호
- 군산 아리울호
- 군산 자이언트호
- 인천 루키나호
