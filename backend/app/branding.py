"""이 코드가 무슨 제품군인가 — **설치가 무슨 플랫폼인가는 여기가 아니라 `.env` 가 정한다.**

이 저장소 하나로 여러 플랫폼(인스턴스)을 띄운다 — 허브 · 그룹 쌍둥이들. 번들은 하나고,
설치마다 `.env` 의 `APP_SLUG` · `APP_NAME` · `APP_TAGLINE` · `EXTENSIONS` 가
다르다(`app/config.py`).
그래서 여기 있는 이름은 **아무것도 안 준 설치의 기본값**(개발 PC · 시험)일 뿐이다. 코드가
제품 이름을 쓰려면 `get_settings().app_name` 을 읽는다 — 여기서 import 하지 않는다.

## 오류 코드 접두사

`DRL-<MODULE>-<NNNN>`(틀 StandardPlatform 은 `APP`).
이것만은 **빌드에 박힌다** — 코드 곳곳의 `errors.code()` 가 조립하는
문자열이라 설치마다 다를 이유가 없고, 로그 검색이 한 값으로 걸려야 한다. 포크(다른 제품군)할 때
`ERROR_PREFIX` 를 바꾸고 `app/` 아래를 찾아 바꾼다 —
시험(`tests/architecture/test_boundaries.py`)이
**섞였는지 검사한다.**
"""

from __future__ import annotations

#: `.env` 에 `APP_NAME` 이 없을 때 — 화면 제목 · API 문서 제목 · 기동 로그.
DEFAULT_APP_NAME = "데이터 중계"

#: `.env` 에 `APP_SLUG` 가 없을 때 — 기계가 읽는 이름. **DB·쿠키·토큰 표식이 전부 여기서
#: 나온다.**
#:
#: 소문자와 숫자만, 한 덩어리로 적는다(`matnexus` · `testscope` · `crossaxtf`).
#: 옆 플랫폼들이 이미 그 규약이고, DB 이름을 눈으로 대조하는 자리가 있어서
#: 한 줄이 튀면 그때마다 「이게 맞나」 를 다시 확인하게 된다.
#:
#: **이 값을 안 바꾸면 옆 플랫폼과 부딪힌다.** 그리고 그 셋은 부딪히는 방식이
#: 전부 조용하다:
#:
#:   DB       공통 틀에서 나온 표는 이름이 같다(users·workspaces·notifications).
#:            같은 DB 를 보면 **오류 없이 남의 계정 표를 읽는다.**
#:   쿠키     쿠키는 포트를 구분하지 않는다. 한쪽 로그인이 다른 쪽 세션을 덮어
#:            **번갈아 로그아웃**되고, 그 원인은 코드 어디에도 없다.
#:   토큰     옆 플랫폼 토큰을 붙여 넣으면 「형식은 맞는데 인증이 안 되는」 상태가
#:            되는데, 그것은 오타와 구별되지 않는다.
#:
#: 그래서 셋을 따로 적지 않고 `Settings` 가 slug 하나에서 만든다 — 따로 적으면 언젠가 하나가
#: 안 바뀌고, 안 바뀐 하나는 위 셋 중 하나로 나타난다.
DEFAULT_APP_SLUG = "datarelay"

#: `.env` 에 `APP_TAGLINE` 이 없을 때 — 한 줄 설명. 로그인 화면과 사이드바가 같은 말을 한다.
DEFAULT_APP_TAGLINE = "레거시 시스템 → Standard Platform 데이터 중계"

#: 오류 코드 접두사. **한 저장소에 하나뿐이어야 한다.**
ERROR_PREFIX = "DRL"
