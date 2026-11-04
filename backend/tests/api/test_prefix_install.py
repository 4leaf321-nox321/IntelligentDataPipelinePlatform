"""**접두어 아래 설치**(`https://…/<slug>/`) — 그 설치에서만 드러나는 것.

시험은 늘 루트(`/`)로 도는데 운영은 접두어 아래 산다. 그 차이 때문에 **기계 자격(PAT)의
쓰기가 범위와 무관하게 전부 막혔다** — 「이 토큰에는 … 범위가 없습니다」 가 아니라 「개인
토큰으로는 이 경로를 고칠 수 없습니다」(AUTH 0105)가 떠서, 받는 사람은 범위를 고치러 다녔다
(실측: 운영 서버의 접두어 설치).

까닭: 범위 표는 `/api/…` 로 적혀 있는데 판정에 넘긴 경로에는 접두어가 붙어 있었다
(앞의 nginx 가 떼고 넘겨도 `PrefixMiddleware` 가 다시 붙인다 — 정적 파일 마운트가 그것을
요구한다). 앞머리가 맞을 수 없으니 「모르는 경로」 가 되고, 모르는 것은 막는다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import SessionLocal
from app.main import create_app
from app.shared.errors import code
from tests.api.conftest import Signed

PREFIX = "/rootdesign"

#: 토큰 쓰기의 본보기 — 고아 첨부 정리의 계획(`relay:write`, 시스템 관리자).
#: 아무것도 안 지운다.
_GC = {"kind": "filestore_gc", "params": "{}"}


@pytest.fixture
def prefixed(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """접두어 아래 사는 같은 앱 — 설정만 다르다."""
    monkeypatch.setenv("PUBLIC_PATH", PREFIX)
    get_settings.cache_clear()
    app = create_app()
    app.state.session_factory = SessionLocal
    try:
        with TestClient(app) as one:
            yield one
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def _token(client: TestClient, admin: Signed, scopes: list[str]) -> dict[str, str]:
    made = client.post(
        "/api/auth/tokens",
        json={"name": f"prefix_{uuid.uuid4().hex[:6]}", "scopes": scopes},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}"}


def _error(response: Any) -> str:
    return str((response.json().get("error") or {}).get("code") or "")


def test_접두어_아래에서도_토큰_쓰기가_범위대로_된다(
    client: TestClient, admin: Signed, prefixed: TestClient
) -> None:
    head = _token(client, admin, ["read", "relay:write"])
    # 접두어를 붙여 부르든(프록시가 그대로 넘김) 빼고 부르든(프록시가 뗌) 같아야 한다.
    for path in (f"{PREFIX}/api/jobs", "/api/jobs"):
        got = prefixed.post(path, data=_GC, headers=head)
        assert got.status_code == 202, (path, got.status_code, got.text)


def test_범위가_없으면_그렇다고_말한다(
    client: TestClient, admin: Signed, prefixed: TestClient
) -> None:
    """**말이 달라지면 사람이 다른 것을 고친다.** 0105 는 「어떤 범위로도 안 되는 경로」 고,
    0106 은 「이 범위만 켜면 되는 경로」 다."""
    head = _token(client, admin, ["read"])
    denied = prefixed.post(f"{PREFIX}/api/jobs", data=_GC, headers=head)
    assert denied.status_code == 403, denied.text
    assert _error(denied) == code("AUTH", 106), denied.text
    assert "relay:write" in denied.json()["error"]["message"]


def test_범위가_없는_경로는_접두어_아래에서도_막는다(
    client: TestClient, admin: Signed, prefixed: TestClient
) -> None:
    """토큰으로 토큰을 만들 수는 없다 — 그 경로에는 쓰기 범위가 없다(0105 가 맞는 자리)."""
    head = _token(client, admin, ["read", "relay:write"])
    denied = prefixed.post(
        f"{PREFIX}/api/auth/tokens", json={"name": "더", "scopes": ["read"]}, headers=head
    )
    assert denied.status_code == 403, denied.text
    assert _error(denied) == code("AUTH", 105), denied.text


def test_접두어_아래에서도_접근_로그가_남는다(
    client: TestClient, admin: Signed, prefixed: TestClient
) -> None:
    """접두어 설치에서는 경로가 `/<slug>/api/…` 로 보인다 — `/api/` 로 시작하는지만 보던 접근
    로그가 로그인 · 고치기를 한 줄도 안 남겼다(2026-10-08). 남길 때는 접두어를 뗀 경로로."""
    from sqlalchemy import func, select

    from app.modules.audit.models import AccessLog

    head = _token(client, admin, ["read", "relay:write"])
    marker = "/api/jobs"
    with SessionLocal() as db:
        before = db.scalar(
            select(func.count()).select_from(AccessLog).where(AccessLog.path == marker)
        )
    got = prefixed.post(f"{PREFIX}{marker}", data=_GC, headers=head)
    assert got.status_code == 202, got.text
    with SessionLocal() as db:
        after = db.scalar(
            select(func.count()).select_from(AccessLog).where(AccessLog.path == marker)
        )
    assert (after or 0) == (before or 0) + 1
