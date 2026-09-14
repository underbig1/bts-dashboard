# BTS 투자철학

Bitcoin · Time · Self를 함께 살펴보는 개인 대시보드입니다.

[BTS 투자철학 열기](https://bts-investment-philosophy.streamlit.app/)

- **B:** 비트코인·테더 가격, 원/달러 환율, 한국·미국 기준금리 — 연결 예정
- **T:** 멤풀 진행 화면과 반감기 현황 — 연결 예정
- **S:** 노션에서 관리하는 노력 기록 카드 — 연결 완료

## S 기록 관리

노션의 `BTS 투자철학 · Self 기록`에서 활동을 관리합니다. `보드에 표시`를 체크한 활동만 `표시 순서` 오름차순으로 나타납니다. 내용을 수정한 뒤 앱의 **기록 새로고침**을 누르세요.

## 로컬 실행

```sh
pip install -r requirements.txt
streamlit run streamlit_app.py
```

노션 연결은 `NOTION_TOKEN`과 `NOTION_DATA_SOURCE_ID` 설정을 사용합니다. 배포 환경에서는 Streamlit Secrets에 저장하며, 인증키를 코드나 저장소에 넣지 않습니다.
