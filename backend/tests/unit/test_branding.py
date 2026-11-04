"""플랫폼 식별자가 **한 자리에서 나오는가.**

포크할 때 이름을 바꾸는 자리가 여럿이면 언젠가 하나가 안 바뀐다. 그리고 안 바뀐
하나는 전부 **조용한 사고**로 나타난다 — 오류가 안 나고 잘못된 데이터가 보인다.
그래서 「따로 안 적혀 있다」 를 시험이 지킨다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from app.branding import DEFAULT_APP_SLUG
from app.config import Settings, get_settings
from app.modules.auth import security

BACKEND = Path(__file__).resolve().parents[2]


def _settings(**env: str) -> Settings:
    """`.env` 를 안 읽는 Settings — 주어진 값만으로. conftest 가 환경에 둔 시험 DB 주소는
    비워서 slug 파생을 본다(직접 준 값이 환경보다 우선한다)."""
    values: dict[str, Any] = {"_env_file": None, "DATABASE_URL": "", **env}
    return Settings(**values)


def test_DB_이름이_slug_에서_나온다() -> None:
    """두 플랫폼이 같은 DB 를 보면 **오류 없이 남의 users 표를 읽는다** — 공통
    틀에서 나온 표는 이름이 같기 때문이다. 그래서 안 적으면 slug 가 DB 이름이다."""
    assert _settings().database_url.rsplit("/", 1)[-1] == DEFAULT_APP_SLUG
    assert _settings(APP_SLUG="plmhub").database_url.rsplit("/", 1)[-1] == "plmhub"
    # 적으면 그대로 — 운영 .env 는 접속 정보를 통째로 적는다.
    explicit = _settings(APP_SLUG="plmhub", DATABASE_URL="postgresql+psycopg://u:p@h:5432/x")
    assert explicit.database_url.endswith("/x")


def test_쿠키_이름이_slug_에서_나온다() -> None:
    """쿠키는 포트를 구분하지 않는다. 같은 이름이면 한쪽 로그인이 다른 쪽 세션을
    덮어써서 번갈아 로그아웃되고, 그 원인은 코드 어디에도 없다."""
    assert _settings(APP_SLUG="plmhub").refresh_cookie_name == "plmhub_refresh"


def test_토큰_표식이_slug_에서_나온다(monkeypatch: pytest.MonkeyPatch) -> None:
    """같으면 옆 플랫폼 토큰을 붙여 넣었을 때 「형식은 맞는데 인증이 안 되는」
    상태가 되는데, 그것은 오타와 구별되지 않는다."""
    slug = get_settings().app_slug
    assert security.pat_prefix() == f"{slug}_pat_"
    assert security.new_pat()[0].startswith(f"{slug}_pat_")
    monkeypatch.setenv("APP_SLUG", "plmhub")
    get_settings.cache_clear()
    try:
        assert security.pat_prefix() == "plmhub_pat_"
        assert security.accepted_pat_prefixes()[0] == "plmhub_pat_"
    finally:
        get_settings.cache_clear()


def test_표시용_앞자리가_컬럼에_들어간다() -> None:
    """**slug 가 길어지면 이 값도 길어진다.** 16자로 잡았다가 slug 를 열여섯 자로
    두는 순간 넘쳤고, 그 오류는 토큰을 발급하는 자리에서 났다 — 거기서는 컬럼
    길이가 원인이라는 것이 안 보인다. slug 의 최대(32자)로 재 본다.
    """
    from sqlalchemy import String

    from app.modules.auth.models import PersonalAccessToken

    column_type = PersonalAccessToken.__table__.c.prefix.type
    assert isinstance(column_type, String)
    assert column_type.length is not None
    longest = "a" * 32
    assert len(f"{longest}_pat_") + 6 <= column_type.length


def test_시험_DB_는_이름에서_파생된다() -> None:
    """접속 정보를 두 곳에 적으면 한쪽만 고쳐지고, 그때 시험이 어느 DB 에서
    도는지 아무도 모른다."""
    from app.database import engine

    name = engine.url.database or ""
    # conftest 가 개발 이름에 _test 를 붙여 만든 것이어야 한다.
    assert name.endswith("_test"), name
    assert not name.endswith("__test"), f"이름이 두 번 붙었습니다: {name}"


def test_slug_는_소문자_한_덩어리다() -> None:
    """`matnexus` · `testscope` · `crossaxtf` — 옆 플랫폼들이 이미 그 규약이고,
    DB 이름을 눈으로 대조하는 자리가 있어서 한 줄이 튀면 그때마다 다시 확인하게
    된다. 그리고 대문자가 섞이면 Postgres 가 따옴표를 요구해 스크립트가 갈린다.

    **32자로 막는다.** 이 값에서 DB 이름(`<slug>_test`)과 토큰 표식이 나오는데,
    둘 다 길이 제한이 있는 자리로 들어간다 — Postgres 식별자는 63바이트고 토큰
    앞자리 컬럼은 64자다. 넘치면 **토큰을 발급하는 자리**에서 터지는데, 거기서는
    이름이 길어서라는 것이 안 보인다. `.env` 의 오타는 **기동에서** 막는다.
    """
    assert re.fullmatch(r"[a-z][a-z0-9]{0,31}", DEFAULT_APP_SLUG), DEFAULT_APP_SLUG
    for bad in ("Plm-Hub", "plm hub", "a" * 33, "1abc", ""):
        with pytest.raises(ValueError):
            _settings(APP_SLUG=bad)


def test_확장_목록은_쉼표로_온다() -> None:
    """`.env` 는 문자열이다 — 빈 것과 중복은 버리고 순서는 지킨다."""
    assert _settings().extension_names == ()
    assert _settings(EXTENSIONS=" hub, bom ,,hub").extension_names == ("hub", "bom")


def test_프론트는_이름을_굽지_않는다() -> None:
    """번들 하나로 여러 플랫폼을 띄우므로, 화면의 이름은 서버가 index.html 에 심는
    `<meta name="app-*">` 에서 온다. 화면 코드가 값을 박아 두면 그 값이 어느 설치에서든
    보인다 — 기본값 하나(개발 서버용)만 branding.ts 에 남긴다."""
    branding_ts = BACKEND.parent / "frontend" / "src" / "shared" / "branding.ts"
    if not branding_ts.exists():  # pragma: no cover - 백엔드만 받은 설치
        return
    text = branding_ts.read_text(encoding="utf-8")
    assert "meta('app-name')" in text and "meta('app-slug')" in text
    found = re.search(r"DEFAULT_APP_SLUG\s*=\s*'([^']+)'", text)
    assert found and found.group(1) == DEFAULT_APP_SLUG


def test_설명_문구는_틀_이름일_때만_기본값이다() -> None:
    """이름을 정한 인스턴스 밑에 「사내 플랫폼 공통 틀」 이 붙으면 틀린 말이다 — 안 주면
    비운다."""
    assert _settings().app_tagline != ""
    assert _settings(APP_NAME="PLM 기준정보").app_tagline == ""
    assert _settings(APP_NAME="PLM 기준정보", APP_TAGLINE="한 줄").app_tagline == "한 줄"
