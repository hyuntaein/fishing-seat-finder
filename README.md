# 낚시 빈자리 통합검색 LIVE v1

GitHub에 이 파일들을 덮어 올리면 Streamlit 앱이 자동 업데이트됩니다.

## 필수 파일
- app.py
- requirements.txt
- sunsang24_sites.json
- manual_sites.json
- .streamlit/config.toml

## 기능
- 선상24 예약현황 JSON 실제 조회
- 예약 상세페이지에서 어종/가격/출항시간 추출
- 예약자 목록 괄호 숫자로 예약인원 계산
- 권역/출항지/어종/선사명 필터

## 제한
선상24 정원/남은자리 필드는 아직 확인되지 않아 `예약 가능 추정`으로 표시합니다.
