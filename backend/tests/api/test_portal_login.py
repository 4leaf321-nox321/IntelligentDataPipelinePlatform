"""HWAX 포털 로그인(jwt-handoff, ADR 0028) — 포털이 보증한 신원으로 들어오는 길.

포털은 RS256 launch 토큰(90초)을 만들어 브라우저가 숨은 폼으로 `POST
…/api/auth/portal-callback` 에 보낸다. 여기서는 **가짜 RSA 키로 포털의 JWKS 를 흉내
낸다**(httpx MockTransport — 웹훅 시험과 같은 무늬). 포털이 내는 토큰 모양(클레임 · 머리의
kid)은 포털 저장소의 `backend/app/auth/downstream.py` 의 `JwtDownstreamIssuer.mint` 를 그대로
옮겼다.

포털 update-all 의 진단표와 숫자가 맞아야 한다 — 꺼짐은 404 JSON, 켜짐은 가짜 토큰에도 303.
"""

from __future__ import annotations

import secrets
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.modules.accounts.models import User
from app.modules.audit.models import AccessLog, AuditEntry
from app.modules.auth import portal, security
from app.modules.workspaces.models import Workspace
from app.shared.errors import code
from tests.api.conftest import PASSWORD, Signed, _make_user, maintenance_counts

JWKS_URL = "http://portal.test:8088/.well-known/jwks.json"
ISSUER = "https://portal.test"
LAUNCH_URL = "https://portal.test/launch/hwaxapp"
CALLBACK = "/api/auth/portal-callback"
EXCHANGE = "/api/auth/portal-exchange"

#: 키 만들기는 수십 ms — 시험마다 만들지 않는다.
KEY_A = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_B = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    entry: dict[str, Any] = dict(RSAAlgorithm.to_jwk(key.public_key(), as_dict=True))
    return {**entry, "kid": kid, "use": "sig", "alg": "RS256"}


@dataclass
class FakePortal:
    """포털의 JWKS 와 토큰 발급 — 시험이 키를 바꾸고(회전) 받기를 실패시킨다."""

    keys: list[dict[str, Any]] = field(default_factory=lambda: [_jwk(KEY_A, "k1")])
    fetches: int = 0
    down: bool = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == JWKS_URL
        self.fetches += 1
        if self.down:
            raise httpx.ConnectError("연결 거부", request=request)
        return httpx.Response(200, json={"keys": self.keys})

    def mint(
        self,
        email: str | None,
        *,
        sub: str | None = None,
        name: str = "포털 사람",
        key: rsa.RSAPrivateKey = KEY_A,
        kid: str = "k1",
        audience: str | None = None,
        issuer: str = ISSUER,
        scope: str = "launch",
        shift: int = 0,
        ttl: int = 90,
        **extra: Any,
    ) -> str:
        now = int(time.time()) + shift
        claims: dict[str, Any] = {
            "iss": issuer,
            "sub": sub if sub is not None else (email or ""),
            "aud": audience or get_settings().portal_audience,
            "email": email,
            "name": name,
            "groups": ["CAE"],
            "scope": scope,
            "iat": now,
            "nbf": now,
            "exp": now + ttl,
            "jti": secrets.token_urlsafe(16),
            **extra,
        }
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def fake_portal(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakePortal]:
    """포털 로그인을 켠 설치 — 설정만 다르다(앱은 요청마다 설정을 읽는다)."""
    fake = FakePortal()
    monkeypatch.setenv("PORTAL_JWKS_URL", JWKS_URL)
    monkeypatch.setenv("PORTAL_ISSUER", ISSUER)
    monkeypatch.setenv("PORTAL_LAUNCH_URL", LAUNCH_URL)
    monkeypatch.setenv("MCP_PUBLIC_URL", "")
    get_settings.cache_clear()
    portal.jwks.reset()
    portal.jwks.transport = httpx.MockTransport(fake.handler)
    try:
        yield fake
    finally:
        portal.jwks.transport = None
        portal.jwks.reset()
        monkeypatch.undo()
        get_settings.cache_clear()


@pytest.fixture
def portal_off(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """포털 로그인을 끈 설치 — 개발 `.env` 에 무엇이 적혀 있든."""
    monkeypatch.setenv("PORTAL_JWKS_URL", "")
    get_settings.cache_clear()
    try:
        yield
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


@pytest.fixture
def browser() -> Iterator[TestClient]:
    """그 사람의 브라우저 — 쿠키를 **따로** 든다(공용 `client` 는 관리자 쿠키를 든다)."""
    from app.main import app

    app.state.session_factory = SessionLocal
    with TestClient(app) as one:
        yield one


def _arrive(web: TestClient, token: str, path: str = CALLBACK) -> httpx.Response:
    """포털의 숨은 폼이 하는 일 — form 필드 `token` 으로 POST, 리다이렉트는 따라가지 않는다."""
    got: httpx.Response = web.post(path, data={"token": token}, follow_redirects=False)
    return got


def _reason(response: httpx.Response, base: str = "") -> str | None:
    assert response.status_code == 303, response.text
    location = response.headers["location"]
    marker = f"{base}/login?sso="
    return location[len(marker) :] if location.startswith(marker) else None


def _refresh_cookie(response: httpx.Response) -> str | None:
    name = get_settings().refresh_cookie_name
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header
    return None


def _person(db: Session, workspace: Workspace, label: str = "portal") -> User:
    return _make_user(db, workspace, label=label, is_system_admin=False, role="member")


# --- 꺼짐 · 방법 -------------------------------------------------------------------


def test_꺼져_있으면_404_JSON_으로_답한다(client: TestClient, portal_off: None) -> None:
    """포털의 진단표: **404 + JSON = 앱 SSO 꺼짐**(`PORTAL_JWKS_URL` 없음). 200 HTML 이면
    포털이 「라우트 없음」 으로 읽고 자기 nginx 를 뒤진다."""
    got = client.post(CALLBACK, data={"token": "probe-not-a-token"}, follow_redirects=False)
    assert got.status_code == 404, got.text
    assert got.headers["content-type"].startswith("application/json")
    assert got.json()["error"]["code"] == code("AUTH", 9)


def test_GET_은_405(client: TestClient, fake_portal: FakePortal) -> None:
    """숨은 폼은 POST 로 온다. GET 에 SPA 의 404 를 주면 진단표가 그것을 「꺼짐」 으로
    읽는다."""
    got = client.get(CALLBACK, follow_redirects=False)
    assert got.status_code == 405, got.text
    assert got.json()["error"]["code"] == code("AUTH", 10)


def test_켜져_있으면_가짜_토큰에도_303_으로_로그인_화면에(
    browser: TestClient, fake_portal: FakePortal
) -> None:
    """진단표의 **3xx = 정상** — update-all 이 `token=probe-not-a-token` 을 던진다."""
    got = _arrive(browser, "probe-not-a-token")
    assert _reason(got) == portal.INVALID
    assert _refresh_cookie(got) is None
    # 토큰이 아예 없어도 같은 길이다(422 를 주면 사람은 JSON 을 본다).
    empty = browser.post(CALLBACK, follow_redirects=False)
    assert _reason(empty) == portal.INVALID


# --- 정상 로그인 ------------------------------------------------------------------


def test_포털이_보증한_사람은_로그인되어_SPA_로_간다(
    browser: TestClient, fake_portal: FakePortal, db: Session, workspace: Workspace
) -> None:
    """쿠키를 심고 SPA 루트로 303 — SPA 는 기동 때 그 쿠키로 access 를 받는다."""
    user = _person(db, workspace)
    got = _arrive(browser, fake_portal.mint(f"  {user.email.upper()} "))
    assert got.status_code == 303, got.text
    assert got.headers["location"] == "/"
    cookie = _refresh_cookie(got)
    assert cookie is not None
    assert "Path=/api/auth" in cookie and "HttpOnly" in cookie

    # SPA 가 하는 일 — 그 쿠키로 access 를 받는다.
    session = browser.post("/api/auth/refresh")
    assert session.status_code == 200, session.text
    assert session.json()["user"]["email"] == user.email
    me = browser.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {session.json()['access_token']}"}
    )
    assert me.status_code == 200, me.text


def test_포털_로그인은_감사와_접근_로그에_포털로_남는다(
    browser: TestClient, fake_portal: FakePortal, db: Session, workspace: Workspace
) -> None:
    """비밀번호 로그인과 **통로가 갈려야** 「이 계정에 누가 어디로 들어왔나」 에 답한다."""
    user = _person(db, workspace)
    # 호출자가 스스로 밝히는 통로(X-Client)는 이 길에서 믿지 않는다.
    got = browser.post(
        CALLBACK,
        data={"token": fake_portal.mint(user.email)},
        headers={"X-Client": "someones-script"},
        follow_redirects=False,
    )
    assert got.headers["location"] == "/"

    entry = db.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "auth.portal_login", AuditEntry.target_id == user.id
        )
    )
    assert entry is not None
    assert entry.actor_id == user.id
    assert entry.actor_client == portal.CLIENT
    assert entry.changes["matched_by"] == "email"
    assert entry.changes["sub"] == user.email

    logs = db.scalars(
        select(AccessLog).where(
            AccessLog.user_id == user.id, AccessLog.action == "PORTAL_LOGIN"
        )
    ).all()
    assert [(one.path, one.status_code) for one in logs] == [(CALLBACK, 303)]


def test_접두어_아래에서도_돌아갈_곳과_쿠키_path_가_맞는다(
    monkeypatch: pytest.MonkeyPatch, fake_portal: FakePortal, db: Session, workspace: Workspace
) -> None:
    """포털 nginx 는 `/<id>/` 를 벗겨 넘긴다(routes 줄 끝 `/`). 앱은 벗겨 오든 붙어 오든 받고,
    돌아갈 곳 · 쿠키 path 는 브라우저가 보는 주소(접두어 아래)여야 한다."""
    from app.main import create_app

    prefix = "/hwaxapp"
    monkeypatch.setenv("PUBLIC_PATH", prefix)
    get_settings.cache_clear()
    app = create_app()
    app.state.session_factory = SessionLocal
    user = _person(db, workspace)
    with TestClient(app) as web:
        for path in (CALLBACK, f"{prefix}{CALLBACK}"):
            got = _arrive(web, fake_portal.mint(user.email), path)
            assert got.headers["location"] == f"{prefix}/", path
            cookie = _refresh_cookie(got)
            assert cookie is not None and f"Path={prefix}/api/auth" in cookie
            refreshed = web.post(f"{prefix}/api/auth/refresh")
            assert refreshed.status_code == 200, refreshed.text
        failed = _arrive(web, "probe-not-a-token", f"{prefix}{CALLBACK}")
        assert _reason(failed, prefix) == portal.INVALID


# --- 토큰 확인 --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"key": KEY_B}, portal.INVALID),  # 서명이 다른 키 — kid 는 맞다
        ({"shift": -200}, portal.EXPIRED),  # exp 가 여유(10초)보다 오래 지났다
        ({"shift": 120}, portal.EXPIRED),  # nbf · iat 가 미래 — 시계 차이
        ({"audience": "other-app"}, portal.INVALID),  # 다른 앱에 낸 토큰
        ({"issuer": "https://evil.test"}, portal.INVALID),
        ({"scope": "session"}, portal.INVALID),  # 같은 키로 낸 다른 쓰임의 토큰
    ],
)
def test_확인이_안_되는_토큰은_로그인_화면으로_사유를_달아(
    browser: TestClient,
    fake_portal: FakePortal,
    db: Session,
    workspace: Workspace,
    change: dict[str, Any],
    reason: str,
) -> None:
    user = _person(db, workspace)
    got = _arrive(browser, fake_portal.mint(user.email, **change))
    assert _reason(got) == reason
    assert _refresh_cookie(got) is None
    assert browser.post("/api/auth/refresh").status_code == 401


def test_여유_안의_시계_차이는_받는다(
    browser: TestClient, fake_portal: FakePortal, db: Session, workspace: Workspace
) -> None:
    """포털 시계가 몇 초 앞서도 막 만든 토큰이 「아직 아님」 으로 튕기지 않는다."""
    user = _person(db, workspace)
    assert _arrive(browser, fake_portal.mint(user.email, shift=5)).headers["location"] == "/"


def test_RS256_이_아닌_토큰은_받지_않는다(
    browser: TestClient, fake_portal: FakePortal, db: Session, workspace: Workspace
) -> None:
    """머리의 alg 를 믿으면 `none` 이나 공개 키를 HMAC 비밀로 쓰는 위조가 열린다."""
    user = _person(db, workspace)
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": user.email,
        "aud": get_settings().portal_audience,
        "email": user.email,
        "scope": "launch",
        "iat": now,
        "nbf": now,
        "exp": now + 90,
        "jti": secrets.token_urlsafe(16),
    }
    forged = jwt.encode(claims, "x" * 32, algorithm="HS256", headers={"kid": "k1"})
    assert _reason(_arrive(browser, forged)) == portal.INVALID
    unsigned = jwt.encode(claims, "", algorithm="none", headers={"kid": "k1"})
    assert _reason(_arrive(browser, unsigned)) == portal.INVALID


def test_한_토큰은_한_번만_로그인시킨다(
    browser: TestClient, fake_portal: FakePortal, db: Session, workspace: Workspace
) -> None:
    """뒤로 가기의 폼 재전송 · 새어 나간 토큰 — 90초 안이라도 두 번째는 거절한다. 이중화 두
    서버가 같이 보도록 DB 에 적는다(다른 프로세스가 받아도 같다)."""
    user = _person(db, workspace)
    token = fake_portal.mint(user.email)
    assert _arrive(browser, token).headers["location"] == "/"
    from app.main import app

    with TestClient(app) as other:
        again = _arrive(other, token)
    assert _reason(again) == portal.INVALID
    assert _refresh_cookie(again) is None


def test_모르는_kid_면_한_번_다시_받는다(
    browser: TestClient, fake_portal: FakePortal, db: Session, workspace: Workspace
) -> None:
    """포털이 키를 바꾼 날 — 5분 캐시가 끝나기를 기다리지 않는다. 그래도 모르면 거절하고, 가짜
    kid 를 든 요청마다 포털에 받으러 가지는 않는다."""
    user = _person(db, workspace)
    assert _arrive(browser, fake_portal.mint(user.email)).headers["location"] == "/"
    assert fake_portal.fetches == 1

    # 포털이 키를 돌렸다 — 새 kid 의 토큰이 먼저 온다.
    fake_portal.keys = [_jwk(KEY_A, "k1"), _jwk(KEY_B, "k2")]
    portal.jwks._last_try = 0.0  # 쿨다운(10초)을 시험이 기다리지 않게
    rotated = _arrive(browser, fake_portal.mint(user.email, key=KEY_B, kid="k2"))
    assert rotated.headers["location"] == "/"
    assert fake_portal.fetches == 2

    # 정말 모르는 kid — 방금 받았으니(쿨다운 안) 다시 받지 않고 거절한다.
    unknown = _arrive(browser, fake_portal.mint(user.email, kid="nope"))
    assert _reason(unknown) == portal.INVALID
    assert fake_portal.fetches == 2


def test_공개_키를_못_받으면_분명히_말한다(
    browser: TestClient,
    fake_portal: FakePortal,
    client: TestClient,
    admin: Signed,
    db: Session,
    workspace: Workspace,
) -> None:
    """받기 실패는 「확인 실패」 와 다른 말이다 — 사람이 아니라 관리자가 할 일(주소 ·
    방화벽)."""
    fake_portal.down = True
    user = _person(db, workspace)
    assert _reason(_arrive(browser, fake_portal.mint(user.email))) == portal.UNAVAILABLE

    status = client.get("/api/auth/portal-status", headers=admin.headers).json()
    assert status["enabled"] is True and status["reachable"] is False
    assert "연결 거부" in status["last_error"]


# --- 포털 안에서 바로 들어온 사람 — 교환(`/portal-exchange`) -----------------------------


def _exchange(web: TestClient, token: str) -> httpx.Response:
    """화면이 하는 일 — 포털의 launch 로 받은 토큰을 JSON 으로 바꾼다."""
    got: httpx.Response = web.post(EXCHANGE, json={"token": token})
    return got


def _refused(got: httpx.Response, status: int) -> str:
    assert got.status_code == status, got.text
    error = got.json()["error"]
    assert error["code"] == code("AUTH", 20)
    reason: str = error["details"]["reason"]
    return reason


def test_교환은_꺼져_있으면_404_JSON(client: TestClient, portal_off: None) -> None:
    got = client.post(EXCHANGE, json={"token": "probe-not-a-token"})
    assert got.status_code == 404, got.text
    assert got.json()["error"]["code"] == code("AUTH", 9)


def test_교환은_비밀번호_로그인처럼_access_와_쿠키를_준다(
    browser: TestClient, fake_portal: FakePortal, db: Session, workspace: Workspace
) -> None:
    """콜백과 같은 확인 · 같은 세션 — 응답 모양만 비밀번호 로그인이다(화면이 바로 쓴다)."""
    user = _person(db, workspace)
    token = fake_portal.mint(user.email)
    got = _exchange(browser, token)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["user"]["email"] == user.email and body["expires_in"] > 0
    cookie = _refresh_cookie(got)
    assert cookie is not None and "Path=/api/auth" in cookie and "HttpOnly" in cookie
    me = browser.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200 and me.json()["email"] == user.email
    # 새로 고침하면 그 쿠키로 이어진다.
    assert browser.post("/api/auth/refresh").json()["user"]["email"] == user.email

    # 한 토큰은 한 번 — 콜백과 교환이 같은 jti 장부를 쓴다.
    assert _refused(_exchange(browser, token), 401) == portal.INVALID
    assert _reason(_arrive(browser, token)) == portal.INVALID

    logged = {
        (one.path, one.action, one.user_id)
        for one in db.scalars(select(AccessLog).where(AccessLog.path == EXCHANGE))
    }
    assert (EXCHANGE, "PORTAL_LOGIN", user.id) in logged
    entry = db.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "auth.portal_login", AuditEntry.target_id == user.id
        )
    )
    assert entry is not None and entry.actor_client == portal.CLIENT


def test_교환의_거절은_콜백과_같은_사유를_JSON_으로(
    browser: TestClient, fake_portal: FakePortal, db: Session, workspace: Workspace
) -> None:
    """화면은 숫자가 아니라 사유로 사람 말을 고른다(콜백의 `?sso=` 와 같은 말)."""
    assert _refused(_exchange(browser, "probe-not-a-token"), 401) == portal.INVALID
    user = _person(db, workspace)
    stale = fake_portal.mint(user.email, shift=-600)
    assert _refused(_exchange(browser, stale), 401) == portal.EXPIRED

    newcomer = f"newcomer-{uuid.uuid4().hex[:8]}@corp.test"
    got = _exchange(browser, fake_portal.mint(newcomer))
    assert _refused(got, 403) == portal.PENDING
    assert _refresh_cookie(got) is None
    made = db.scalar(select(User).where(User.email == newcomer))
    assert made is not None and made.status == "pending"  # 콜백처럼 가입 신청이 된다

    fake_portal.down = True
    portal.jwks.reset()
    assert (
        _refused(_exchange(browser, fake_portal.mint(user.email)), 502) == portal.UNAVAILABLE
    )


def test_화면에_포털_타일_id_를_받을_준비가_됐을_때만_심는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """자동 로그인은 `/systems/<id>/launch` 를 부른다 — id 는 타일 id = audience(기본 slug).
    받는 쪽(`PORTAL_JWKS_URL`)이 꺼져 있으면 비워 화면이 시도하지 않게."""
    from app.main import create_app

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>x</title></head><body></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    monkeypatch.setenv("PORTAL_JWKS_URL", "")
    monkeypatch.setenv("PORTAL_AUDIENCE", "")
    try:
        get_settings.cache_clear()
        with TestClient(create_app()) as web:
            assert '<meta name="app-portal-system" content="" />' in web.get("/login").text
        monkeypatch.setenv("PORTAL_JWKS_URL", JWKS_URL)
        get_settings.cache_clear()
        slug = get_settings().app_slug
        with TestClient(create_app()) as web:
            page = web.get("/login").text
            assert f'<meta name="app-portal-system" content="{slug}" />' in page
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# --- 사람 ------------------------------------------------------------------------


def test_처음_보는_사람은_가입_신청이_되고_관리자에게_같은_길로_간다(
    browser: TestClient,
    client: TestClient,
    fake_portal: FakePortal,
    admin: Signed,
    db: Session,
) -> None:
    """이름 · 아이디는 포털이 보증한 값, 비밀번호는 없고, 희망 부서는 비운다(짐작하지 않는다).
    관리자에게는 화면의 가입 신청과 같은 「가입 승인 대기」 로 뜬다."""
    before = maintenance_counts(client, admin).get("accounts_pending", 0)
    email = f"newcomer-{uuid.uuid4().hex[:8]}@corp.test"
    got = _arrive(browser, fake_portal.mint(email.upper(), name="  새 사람  "))
    assert _reason(got) == portal.PENDING
    assert _refresh_cookie(got) is None

    made = db.scalar(select(User).where(User.email == email))
    assert made is not None
    assert (made.status, made.display_name, made.requested_workspace_id) == (
        "pending",
        "새 사람",
        None,
    )
    assert made.password_hash.startswith(security.UNUSABLE_PASSWORD_PREFIX)
    assert maintenance_counts(client, admin)["accounts_pending"] == before + 1

    entry = db.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "account.portal_requested", AuditEntry.target_id == made.id
        )
    )
    assert entry is not None and entry.actor_client == portal.CLIENT

    # 비밀번호가 없다 — 어떤 비밀번호로도 안 들어온다.
    tried = client.post("/api/auth/login", json={"email": email, "password": ""})
    assert tried.status_code == 422  # 빈 비밀번호는 모양에서 막힌다
    tried = client.post("/api/auth/login", json={"email": email, "password": "!" + "0" * 32})
    assert tried.status_code == 401


def test_이미_신청_중이면_같은_말을_하고_둘로_만들지_않는다(
    browser: TestClient, fake_portal: FakePortal, db: Session
) -> None:
    email = f"twice-{uuid.uuid4().hex[:8]}@corp.test"
    assert _reason(_arrive(browser, fake_portal.mint(email))) == portal.PENDING
    assert _reason(_arrive(browser, fake_portal.mint(email))) == portal.PENDING
    assert len(db.scalars(select(User).where(User.email == email)).all()) == 1


def test_승인하면_부서를_골라_주고_그다음부터_포털로_들어온다(
    browser: TestClient,
    client: TestClient,
    fake_portal: FakePortal,
    admin: Signed,
    db: Session,
    workspace: Workspace,
) -> None:
    """신청 부서가 비어 있으니 그대로 승인하면 부서를 고르라고 말한다 — 계정 화면이 그때
    묻는다."""
    email = f"approve-{uuid.uuid4().hex[:8]}@corp.test"
    _arrive(browser, fake_portal.mint(email))
    made = db.scalar(select(User).where(User.email == email))
    assert made is not None

    blind = client.post(f"/api/accounts/{made.id}/approve", json={}, headers=admin.headers)
    assert blind.status_code == 400, blind.text
    approved = client.post(
        f"/api/accounts/{made.id}/approve",
        json={"workspace_slug": workspace.slug},
        headers=admin.headers,
    )
    assert approved.status_code == 200, approved.text

    assert _arrive(browser, fake_portal.mint(email)).headers["location"] == "/"
    session = browser.post("/api/auth/refresh")
    assert session.status_code == 200, session.text
    assert session.json()["user"]["home_workspace_slug"] == workspace.slug


def test_정지_삭제_계정은_사유를_달아_돌려보낸다(
    browser: TestClient,
    client: TestClient,
    fake_portal: FakePortal,
    admin: Signed,
    db: Session,
    workspace: Workspace,
) -> None:
    paused = _person(db, workspace, "paused")
    assert client.post(f"/api/accounts/{paused.id}/suspend", headers=admin.headers).is_success
    got = _arrive(browser, fake_portal.mint(paused.email))
    assert _reason(got) == portal.SUSPENDED
    assert _refresh_cookie(got) is None

    gone = _person(db, workspace, "gone")
    assert client.delete(f"/api/accounts/{gone.id}", headers=admin.headers).is_success
    assert _reason(_arrive(browser, fake_portal.mint(gone.email))) == portal.DELETED
    # 지운 계정의 아이디로 새 신청이 생기지 않는다.
    assert len(db.scalars(select(User).where(User.email == gone.email)).all()) == 1


def test_세션_세대는_비밀번호_로그인과_같다(
    browser: TestClient,
    client: TestClient,
    fake_portal: FakePortal,
    admin: Signed,
    db: Session,
    workspace: Workspace,
) -> None:
    """포털로 연 세션도 정지가 끊는다(이미 받은 access 까지 — `session_epoch`). 다시 켜면 옛
    세션은 안 살아나고, 포털로 다시 들어오면 새 세대의 세션이 열린다."""
    user = _person(db, workspace)
    _arrive(browser, fake_portal.mint(user.email))
    first = browser.post("/api/auth/refresh")
    assert first.status_code == 200, first.text
    old_access = {"Authorization": f"Bearer {first.json()['access_token']}"}

    assert client.post(f"/api/accounts/{user.id}/suspend", headers=admin.headers).is_success
    assert client.post(f"/api/accounts/{user.id}/activate", headers=admin.headers).is_success
    assert browser.get("/api/auth/me", headers=old_access).status_code == 401
    assert browser.post("/api/auth/refresh").status_code == 401

    assert _arrive(browser, fake_portal.mint(user.email)).headers["location"] == "/"
    again = browser.post("/api/auth/refresh")
    assert again.status_code == 200, again.text
    fresh = {"Authorization": f"Bearer {again.json()['access_token']}"}
    assert browser.get("/api/auth/me", headers=fresh).status_code == 200
    db.refresh(user)
    assert user.session_epoch >= 1


def test_메일로_못_찾으면_sub_로_찾는다(
    browser: TestClient, fake_portal: FakePortal, db: Session, workspace: Workspace
) -> None:
    """관리자가 짧은 아이디(사번 · knox id)로 만든 계정 — 포털의 sub 가 그 값일 때 붙는다."""
    short = f"kp{uuid.uuid4().hex[:8]}"
    user = User(
        email=short,
        password_hash=security.hash_password(PASSWORD),
        display_name="짧은 아이디",
        status="active",
        home_workspace_id=workspace.id,
    )
    db.add(user)
    db.commit()
    got = _arrive(browser, fake_portal.mint(f"{short}@corp.test", sub=short.upper()))
    assert got.headers["location"] == "/"
    entry = db.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "auth.portal_login", AuditEntry.target_id == user.id
        )
    )
    assert entry is not None and entry.changes["matched_by"] == "sub"
    # 메일로 새 신청이 생기지 않았다.
    assert db.scalar(select(User).where(User.email == f"{short}@corp.test")) is None


def test_sub_로만_맞는_시스템_관리자_계정에는_붙이지_않는다(
    browser: TestClient, fake_portal: FakePortal, db: Session, workspace: Workspace
) -> None:
    """로컬 `admin` 같은 아이디를 포털이 누군가의 sub 로 내면 그 사람이 관리자가 된다."""
    short = f"adm{uuid.uuid4().hex[:8]}"
    db.add(
        User(
            email=short,
            password_hash=security.hash_password(PASSWORD),
            display_name="로컬 관리자",
            status="active",
            is_system_admin=True,
        )
    )
    db.commit()
    got = _arrive(browser, fake_portal.mint(f"someone-{short}@corp.test", sub=short))
    assert _reason(got) == portal.ADMIN_BY_SUB
    assert _refresh_cookie(got) is None


# --- 진단 · 화면 ------------------------------------------------------------------


def test_진단은_시스템_관리자만_보고_공개_키가_닿는지_말한다(
    client: TestClient, fake_portal: FakePortal, admin: Signed, member: Signed
) -> None:
    assert client.get("/api/auth/portal-status", headers=member.headers).status_code == 403
    got = client.get("/api/auth/portal-status", headers=admin.headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["enabled"] is True and body["reachable"] is True
    assert body["kids"] == ["k1"]
    assert body["audience"] == get_settings().app_slug  # 안 주면 slug
    assert body["callback_path"] == CALLBACK
    # 접두어 없이 포털에 붙이면 로그인 뒤 포털 첫 화면으로 간다 — 그렇다고 말한다.
    assert any("PUBLIC_PATH" in one for one in body["problems"])
    # 포털 MCP 주소는 끝의 / 까지 — 안 맞으면 말한다(개발 .env 에 무엇이 있든 위 fixture 가
    # MCP_PUBLIC_URL 을 비운다).
    assert any("MCP_PUBLIC_URL" in one for one in body["problems"])


def test_진단은_꺼진_것도_말한다(client: TestClient, admin: Signed, portal_off: None) -> None:
    body = client.get("/api/auth/portal-status", headers=admin.headers).json()
    assert (body["enabled"], body["reachable"], body["kids"]) == (False, None, [])


def test_로그인_화면의_단추_주소는_받을_준비가_됐을_때만_심긴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`PORTAL_LAUNCH_URL` 만 있고 받는 쪽(`PORTAL_JWKS_URL`)이 없으면 단추를 내지 않는다 —
    누른 사람이 포털을 돌아 콜백의 404 JSON 을 보게 된다."""
    from app.main import create_app

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>x</title></head><body></body></html>",
        encoding="utf-8",
    )
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    monkeypatch.setenv("PORTAL_LAUNCH_URL", LAUNCH_URL)
    monkeypatch.setenv("PORTAL_JWKS_URL", "")
    try:
        get_settings.cache_clear()
        with TestClient(create_app()) as web:
            assert '<meta name="app-portal-launch" content="" />' in web.get("/login").text
        monkeypatch.setenv("PORTAL_JWKS_URL", JWKS_URL)
        get_settings.cache_clear()
        with TestClient(create_app()) as web:
            page = web.get("/login").text
            assert f'<meta name="app-portal-launch" content="{LAUNCH_URL}" />' in page
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_비워_둔_발급자와_audience_는_기본값으로_읽는다() -> None:
    """`PORTAL_ISSUER=` 한 줄이 「iss 가 빈 토큰만 받는다」 가 되면 모든 포털 로그인이 확인
    실패로 튕긴다 — 빈 값은 안 적은 것과 같다. audience 는 slug 다(포털 id = slug 가 권장)."""
    from app.config import DEFAULT_PORTAL_ISSUER, Settings

    got = Settings(portal_issuer="  ", portal_audience="")
    assert got.portal_issuer == DEFAULT_PORTAL_ISSUER
    assert got.portal_audience == got.app_slug
