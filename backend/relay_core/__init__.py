"""datarelay 의 엔진 — **DB 도 웹도 모른다.**

레거시 시스템(PLM · MES …)에서 받은 행을 Standard Platform 의 묶음(`sp-bundle/1`)으로 만드는
일이 전부 여기 있다(ADR 0001):

    adapters/   원천 → 평평한 행 + 삭제 표시 + 워터마크
    mirror      원천 키마다 최신 행 — 레시피는 늘 **전체**를 다시 변환한다
    convert     미러 → CSV → `sp_table.convert()`(틀의 도구를 고치지 않고 그대로)
    diff        지난번 **적용된** 출력과 견줘 바뀐 줄 · 무덤만 남긴 실행 폴더

웹 앱(`app/modules/relay`)과 CLI 가 같은 함수를 부른다. 그래서 여기는 `app` 을 import 하지
않는다 — 구조 시험이 지킨다. MatNexus 의 `matcore` 와 같은 자리다.
"""
