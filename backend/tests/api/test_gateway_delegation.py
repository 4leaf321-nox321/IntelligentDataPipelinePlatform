"""HWAX 게이트웨이의 사람별 위임(`POST /api/auth/sso`, ADR 0028) — 게이트웨이가 그 사람 명의의
개인 토큰을 받아 간다.

계약은 Report Archive 의 것과 같다(HWAXPortal `docs/sso-delegation/`, 게이트웨이
`gateway.py` 의 `_mint_user_pat`): 본문 없는 POST · 머리 넷 · 200 이면 토큰과 **같은 객체의**
`expires_in`. **200 이 아니면 게이트웨이는 본문 앞 200자를 그 사람에게 그대로 보인다** — 그래서
거절 본문이 사람이 읽을 한국어 한 줄인지까지 본다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.modules.accounts.models import User
from app.modules.audit.models import AccessLog, AuditEntry
from app.modules.auth import delegation, security
from app.modules.auth.models import PersonalAccessToken
from app.modules.server import services as server_services
from app.modules.workspaces.models import Workspace
from app.shared import scopes
from app.shared.errors import code
from tests.api.conftest import PASSWORD, Signed, _make_user, clock_settled

SSO = "/api/auth/sso"
REVOKE = "/api/auth/sso/revoke"
VERIFY = "/api/auth/sso/verify"
SECRET = "5f" * 32
LAUNCH_URL = "https://portal.test/launch/hwaxapp"


@pytest.fixture
def gateway_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """위임을 켠 설치 — 설정만 다르다(앱은 요청마다 설정을 읽는다)."""
    monkeypatch.setenv("HEAX_SSO_SECRET", SECRET)
    monkeypatch.setenv("HEAX_SSO_ALLOWED_IPS", "")
    monkeypatch.setenv("TRUST_PROXY", "false")
    monkeypatch.setenv("PORTAL_JWKS_URL", "")
    monkeypatch.setenv("PORTAL_LAUNCH_URL", "")
    get_settings.cache_clear()
    delegation.refusals.reset()
    try:
        yield
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        delegation.refusals.reset()


def _from(address: str) -> TestClient:
    """그 주소에서 부르는 손 — 게이트웨이는 앱 포트로 직접 온다(TCP 원격 주소)."""
    from app.main import app

    app.state.session_factory = SessionLocal
    return TestClient(app, client=(address, 50000))


def _ask(
    web: TestClient,
    email: str | None,
    *,
    secret: str | None = SECRET,
    client: str | None = "gateway",
    path: str = SSO,
    extra: dict[str, str] | None = None,
) -> httpx.Response:
    headers: dict[str, str] = {}
    if secret is not None:
        headers["X-Heax-Gateway-Secret"] = secret
    if email is not None:
        # 게이트웨이는 둘을 같은 값으로 싣는다(`_mint_user_pat`).
        headers["X-Heax-User-Email"] = email
        headers["X-Hwax-Delegate-Email"] = email
    if client is not None:
        headers["X-Heax-Client"] = client
    got: httpx.Response = web.post(path, headers={**headers, **(extra or {})})
    return got


def _person(db: Session, workspace: Workspace, label: str = "gw") -> User:
    return _make_user(db, workspace, label=label, is_system_admin=False, role="member")


def _bare(db: Session, status: str, *, deleted: bool = False) -> User:
    user = User(
        email=f"bare-{uuid.uuid4().hex[:8]}@example.local",
        password_hash=security.hash_password(PASSWORD),
        display_name="상태 시험",
        status=status,
        deleted_at=datetime.now(UTC) if deleted else None,
    )
    db.add(user)
    db.commit()
    return user


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _one_line(got: httpx.Response, status: int, error: str) -> str:
    """거절은 **사람이 읽을 한 줄**(text/plain) — 끝에 오류 코드."""
    assert got.status_code == status, got.text
    assert got.headers["content-type"].startswith("text/plain")
    text = got.text
    assert "\n" not in text and text.endswith(f"({error})")
    assert not text.lstrip().startswith("{")
    return text


# --- 꺼짐 · 문 ---------------------------------------------------------------------


def test_비밀이_없으면_꺼져_있고_404_JSON(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RA 와 같은 숫자 — 404 + JSON 은 「꺼짐」 이다(포털 콜백의 진단표와 같다)."""
    monkeypatch.setenv("HEAX_SSO_SECRET", "")
    get_settings.cache_clear()
    try:
        got = client.post(
            SSO, headers={"X-Heax-Gateway-Secret": "", "X-Heax-User-Email": "a@b"}
        )
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert got.status_code == 404, got.text
    assert got.json()["error"]["code"] == code("AUTH", 11)


def test_비밀이_틀리거나_없으면_401_한_줄(client: TestClient, gateway_on: None) -> None:
    text = _one_line(_ask(client, "a@example.local", secret="wrong"), 401, code("AUTH", 12))
    assert "비밀이 맞지 않습니다" in text and SECRET not in text
    _one_line(_ask(client, "a@example.local", secret=None), 401, code("AUTH", 12))
    # 비밀을 모르는 쪽에게는 그 뒤의 것(누가 있나)을 말하지 않는다 — 이메일이 없어도 401 이
    # 먼저다.
    _one_line(_ask(client, None, secret="wrong"), 401, code("AUTH", 12))


def test_이메일이_없으면_400_client_모양이_틀려도_400(
    client: TestClient, gateway_on: None
) -> None:
    """비밀이 맞고 이메일이 없으면 400 — 포털이 토큰을 만들지 않고 「켜짐 · 비밀 맞음」 을 보는
    점검."""
    text = _one_line(_ask(client, None), 400, code("AUTH", 13))
    assert "X-Heax-User-Email" in text
    _one_line(_ask(client, "a@example.local", client="gate way!"), 400, code("AUTH", 14))


def test_처음_보는_사람_대기_정지_삭제는_403_한국어_한_줄(
    client: TestClient,
    gateway_on: None,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """계정을 만들지 않는다(JIT 없음) — 본문이 할 일을 말한다. 게이트웨이는 본문 앞 200자를
    그대로 보이므로 그 안에 들어야 한다."""
    stranger = f"new-{uuid.uuid4().hex[:6]}@corp.example"
    text = _one_line(_ask(client, stranger), 403, code("AUTH", 15))
    assert "가입 승인된 계정이 없습니다" in text and "가입 신청" in text and stranger in text
    assert db.scalar(select(User).where(User.email == stranger)) is None

    # 포털 로그인이 켜져 있으면 타일 한 번이 곧 가입 신청이다 — 그 주소를 말한다.
    monkeypatch.setenv("PORTAL_JWKS_URL", "http://portal.test:8088/.well-known/jwks.json")
    monkeypatch.setenv("PORTAL_LAUNCH_URL", LAUNCH_URL)
    get_settings.cache_clear()
    text = _one_line(_ask(client, stranger), 403, code("AUTH", 15))
    assert LAUNCH_URL in text and "한 번 열면" in text
    assert len(text) <= 200, len(text)

    pending = _bare(db, "pending")
    assert "승인을 기다리는" in _one_line(_ask(client, pending.email), 403, code("AUTH", 16))
    paused = _bare(db, "suspended")
    assert "정지된 계정" in _one_line(_ask(client, paused.email), 403, code("AUTH", 17))
    gone = _bare(db, "active", deleted=True)
    assert "삭제된 계정" in _one_line(_ask(client, gone.email), 403, code("AUTH", 18))
    assert not db.scalars(
        select(PersonalAccessToken).where(
            PersonalAccessToken.user_id.in_([pending.id, paused.id, gone.id])
        )
    ).all()


def test_허용_주소_밖이면_403_프록시_머리는_믿지_않는다(
    gateway_on: None, db: Session, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TCP 원격 주소로만 본다. `TRUST_PROXY` 를 켠 설치는 서버가 `X-Forwarded-For` 로 원격
    주소를 바꿔 적으므로, 목록이 있으면 그 머리가 붙은 요청을 받지 않는다."""
    user = _person(db, workspace)
    monkeypatch.setenv("HEAX_SSO_ALLOWED_IPS", "10.1.2.3, 10.1.3.0/24")
    get_settings.cache_clear()
    with _from("10.9.9.9") as outside:
        text = _one_line(_ask(outside, user.email), 403, code("AUTH", 19))
        assert "10.9.9.9" not in text  # 주소는 로그에만
    with _from("10.1.3.77") as inside:
        assert _ask(inside, user.email).status_code == 200
    with _from("::ffff:10.1.2.3") as mapped:  # IPv6 소켓으로 받은 IPv4
        assert _ask(mapped, user.email).status_code == 200

    monkeypatch.setenv("TRUST_PROXY", "true")
    get_settings.cache_clear()
    with _from("10.1.2.3") as proxied:
        forwarded = {"X-Forwarded-For": "10.1.2.3"}
        _one_line(_ask(proxied, user.email, extra=forwarded), 403, code("AUTH", 19))
        assert _ask(proxied, user.email).status_code == 200

    # 못 읽는 값이 하나라도 있으면 **전부** 거절한다 — 오타 하나로 제한이 풀리지 않게.
    monkeypatch.setenv("HEAX_SSO_ALLOWED_IPS", "10.1.2.3, 10.1.2.x")
    get_settings.cache_clear()
    with _from("10.1.2.3") as inside:
        _one_line(_ask(inside, user.email), 403, code("AUTH", 19))


# --- 연결 점검(`/sso/verify`) — 세 플랫폼이 같은 계약 -------------------------------------


def test_점검은_꺼짐_404_JSON_비밀_401_허용_밖_403(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """발급과 같은 순서 · 같은 숫자 — 꺼짐 404 JSON(진단표의 「꺼짐」) → 허용 밖 403 → 비밀
    401. 거절 본문은 발급과 같은 한국어 한 줄이다."""
    monkeypatch.setenv("HEAX_SSO_SECRET", "")
    get_settings.cache_clear()
    try:
        off = client.post(VERIFY, headers={"X-Heax-Gateway-Secret": ""})
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert off.status_code == 404, off.text
    assert off.json()["error"]["code"] == code("AUTH", 11)

    monkeypatch.setenv("HEAX_SSO_SECRET", SECRET)
    monkeypatch.setenv("HEAX_SSO_ALLOWED_IPS", "")
    monkeypatch.setenv("TRUST_PROXY", "false")
    get_settings.cache_clear()
    delegation.refusals.reset()
    try:
        _one_line(_ask(client, None, path=VERIFY, secret="wrong"), 401, code("AUTH", 12))
        _one_line(_ask(client, None, path=VERIFY, secret=None), 401, code("AUTH", 12))
        monkeypatch.setenv("HEAX_SSO_ALLOWED_IPS", "10.1.2.3")
        get_settings.cache_clear()
        with _from("10.9.9.9") as outside:
            # 비밀이 맞아도 허용 밖이면 403 — 주소를 비밀보다 먼저 본다(발급과 같다).
            _one_line(_ask(outside, None, path=VERIFY), 403, code("AUTH", 19))
        with _from("10.1.2.3") as inside:
            assert _ask(inside, None, path=VERIFY).status_code == 204
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        delegation.refusals.reset()


def test_점검은_이메일_없이_204_계정_토큰_감사를_건드리지_않는다(
    client: TestClient, gateway_on: None, db: Session, workspace: Workspace
) -> None:
    """`/sso` 로 점검하면 토큰이 실제로 나가고 직전 토큰이 회수된다 — 점검은 아무것도 안 한다.
    이메일 머리를 실어도(처음 보는 사람이어도) 그것을 보지 않는다."""
    user = _person(db, workspace)

    def counts() -> tuple[int | None, int | None, int | None]:
        db.expire_all()
        return (
            db.scalar(select(func.count()).select_from(User)),
            db.scalar(select(func.count()).select_from(PersonalAccessToken)),
            db.scalar(select(func.count()).select_from(AuditEntry)),
        )

    before = counts()
    got = _ask(client, None, path=VERIFY, client=None)
    assert got.status_code == 204 and got.content == b""
    assert _ask(client, user.email, path=VERIFY).status_code == 204
    stranger = f"new-{uuid.uuid4().hex[:6]}@corp.example"
    assert _ask(client, stranger, path=VERIFY).status_code == 204
    assert counts() == before


def test_점검의_거절도_서버_화면의_마지막_거절에_잡힌다(
    client: TestClient, gateway_on: None, admin: Signed, db: Session
) -> None:
    """접근 로그가 점검도 `GATEWAY_SSO` 로 적는다 — 포털 담당이 점검에서 401 을 받았다고 하면
    서버 화면의 「마지막으로 거절한 때」 에 그것이 있어야 한다."""
    assert _ask(client, None, path=VERIFY).status_code == 204
    clock_settled()  # 이 거절이 앞서 남은 거절들보다 뒤에 찍히게(그래야 「마지막」 이다)
    _ask(client, None, path=VERIFY, secret="wrong")
    rows = db.scalars(select(AccessLog).where(AccessLog.path == VERIFY)).all()
    assert {(one.action, one.status_code) for one in rows} >= {
        ("GATEWAY_SSO", 204),
        ("GATEWAY_SSO", 401),
    }
    refusal = client.get("/api/auth/portal-status", headers=admin.headers).json()[
        "delegation"
    ]["last_refusal"]
    assert refusal is not None and refusal["status"] == 401
    assert "비밀이 다르다" in refusal["reason"]


# --- 정상 발급 ---------------------------------------------------------------------


def test_승인된_사람에게_하루짜리_개인_토큰을_봉투_없이_준다(
    client: TestClient, gateway_on: None, db: Session, workspace: Workspace
) -> None:
    """게이트웨이는 응답을 뒤져 `access_token` 과 **같은 객체의** `expires_in` 을 읽는다. 그
    토큰으로 부르면 그 사람이다."""
    user = _person(db, workspace)
    got = _ask(client, f"  {user.email.upper()} ")
    assert got.status_code == 200, got.text
    body = got.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer" and body["expires_in"] == 86400
    assert body["access_token"].startswith(security.pat_prefix())

    me = client.get("/api/auth/me", headers=_bearer(body["access_token"]))
    assert me.status_code == 200, me.text
    assert me.json()["email"] == user.email

    token = db.scalar(
        select(PersonalAccessToken).where(PersonalAccessToken.user_id == user.id)
    )
    assert token is not None
    assert token.name == "HWAX 게이트웨이(gateway)" and token.delegated_client == "gateway"
    assert token.expires_at is not None
    left = token.expires_at - datetime.now(UTC)
    assert timedelta(hours=23) < left <= timedelta(days=1)
    # 범위 — 읽기 + 쓰기 범위 전부. 관리 범위는 없다.
    assert token.scopes == [
        "read",
        *scopes.write_scopes(server_services.enabled_names(db)),
    ]
    assert not [one for one in token.scopes if one == "admin" or one.endswith(":admin")]


def test_시스템_관리자도_이메일이_맞으면_된다(
    client: TestClient, gateway_on: None, admin: Signed
) -> None:
    got = _ask(client, admin.email)
    assert got.status_code == 200, got.text


def test_같은_client_로_다시_받으면_직전_토큰은_회수되고_줄이_쌓이지_않는다(
    client: TestClient, gateway_on: None, db: Session, workspace: Workspace
) -> None:
    """게이트웨이는 12시간마다 다시 받는다 — 회수하지 않으면 산 토큰이, 지우지 않으면 「내
    정보」 의 목록이 하루 두 줄씩 쌓인다. 방금 회수한 것 하나만 남긴다."""
    user = _person(db, workspace)
    first = _ask(client, user.email).json()["access_token"]
    second = _ask(client, user.email).json()["access_token"]
    assert client.get("/api/auth/me", headers=_bearer(first)).status_code == 401
    assert client.get("/api/auth/me", headers=_bearer(second)).status_code == 200

    third = _ask(client, user.email).json()["access_token"]
    assert client.get("/api/auth/me", headers=_bearer(third)).status_code == 200
    rows = client.get("/api/auth/tokens", headers=_bearer(third)).json()
    named = [one for one in rows if one["name"] == "HWAX 게이트웨이(gateway)"]
    assert len(named) == 2  # 산 것 하나 + 방금 회수한 것 하나
    assert sorted(one["revoked_at"] is None for one in named) == [False, True]

    # 사람이 손으로 같은 이름을 지은 토큰은 위임이 건드리지 않는다(표지는 이름이 아니라
    # 칸이다).
    made = PersonalAccessToken(
        user_id=user.id,
        name="HWAX 게이트웨이(gateway)",
        prefix="hand",
        token_hash=security.hash_token(f"hand-{uuid.uuid4().hex}"),
        scopes=["read"],
    )
    db.add(made)
    db.commit()
    _ask(client, user.email)
    db.refresh(made)
    assert made.revoked_at is None


def test_다른_client_는_따로_든다(
    client: TestClient, gateway_on: None, db: Session, workspace: Workspace
) -> None:
    """포털이 직접 부르는 길(`hwax-portal`)이 게이트웨이의 토큰을 회수하면 그 사람의 Claude 가
    끊긴다 — client 마다 따로다."""
    user = _person(db, workspace)
    gateway = _ask(client, user.email).json()["access_token"]
    portal = _ask(client, user.email, client="hwax-portal").json()["access_token"]
    assert client.get("/api/auth/me", headers=_bearer(gateway)).status_code == 200
    assert client.get("/api/auth/me", headers=_bearer(portal)).status_code == 200
    names = {
        one.name
        for one in db.scalars(
            select(PersonalAccessToken).where(PersonalAccessToken.user_id == user.id)
        )
    }
    assert names == {"HWAX 게이트웨이(gateway)", "HWAX 게이트웨이(hwax-portal)"}
    # client 를 안 주면 게이트웨이의 일반 위임 값(`gateway`)이다.
    again = _ask(client, user.email, client=None).json()["access_token"]
    assert client.get("/api/auth/me", headers=_bearer(gateway)).status_code == 401
    assert client.get("/api/auth/me", headers=_bearer(again)).status_code == 200


def test_발급과_위임_토큰으로_한_일은_게이트웨이_통로로_남는다(
    client: TestClient, gateway_on: None, admin: Signed, db: Session
) -> None:
    """감사의 통로(`actor_client`)는 `hwax-gateway` — 머리의 X-Client 는 믿지 않는다. 접근
    로그는 발급이 `GATEWAY_SSO`, 위임 토큰으로 든 요청이 `GATEWAY_API`(범위에 막혀도)."""
    owner = db.scalar(select(User).where(User.email == admin.email))
    assert owner is not None
    got = _ask(client, admin.email, extra={"X-Client": "someones-script"})
    token = got.json()["access_token"]
    issued = db.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "auth.gateway_delegated", AuditEntry.actor_id == owner.id
        )
    )
    assert issued is not None and issued.actor_client == delegation.CLIENT
    assert issued.changes["client"] == "gateway" and issued.changes["revoked_previous"] == 0

    made = client.put(
        "/api/server/profile",
        json={"summary": f"게이트웨이로 고친 소개 {uuid.uuid4().hex[:8]}"},
        headers={**_bearer(token), "X-Client": "someones-script"},
    )
    assert made.status_code == 200, made.text
    trail = db.scalars(
        select(AuditEntry).where(
            AuditEntry.actor_id == owner.id,
            AuditEntry.actor_token == "HWAX 게이트웨이(gateway)",
        )
    ).all()
    assert trail and {one.actor_client for one in trail} == {delegation.CLIENT}

    # 범위에 막힌 쓰기(내 정보 고치기는 어느 범위로도 안 열린다)도 게이트웨이 줄로 남는다.
    refused = client.patch("/api/auth/me", json={"display_name": "x"}, headers=_bearer(token))
    assert refused.status_code == 403
    actions = {
        (one.path, one.action)
        for one in db.scalars(select(AccessLog).where(AccessLog.user_id == owner.id))
    }
    assert ("/api/auth/sso", "GATEWAY_SSO") in actions
    assert ("/api/server/profile", "GATEWAY_API") in actions
    assert ("/api/auth/me", "GATEWAY_API") in actions


def test_회수는_그_사람의_그_client_토큰만(
    client: TestClient, gateway_on: None, db: Session, workspace: Workspace
) -> None:
    user = _person(db, workspace)
    gateway = _ask(client, user.email).json()["access_token"]
    portal = _ask(client, user.email, client="hwax-portal").json()["access_token"]

    got = _ask(client, user.email, path=REVOKE)
    assert got.status_code == 200, got.text
    assert got.json() == {"revoked": 1}
    assert client.get("/api/auth/me", headers=_bearer(gateway)).status_code == 401
    assert client.get("/api/auth/me", headers=_bearer(portal)).status_code == 200
    assert _ask(client, user.email, path=REVOKE).json() == {"revoked": 0}
    assert _ask(client, "nobody@example.local", path=REVOKE).json() == {"revoked": 0}
    entry = db.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "auth.gateway_delegation_revoked",
            AuditEntry.target_id == user.id,
        )
    )
    assert entry is not None and entry.changes == {"client": "gateway", "revoked": 1}
    # 회수도 같은 문을 지난다 — 비밀이 틀리면 401.
    _one_line(_ask(client, user.email, path=REVOKE, secret="wrong"), 401, code("AUTH", 12))


def test_접두어_설치에서도_게이트웨이는_접두어_없이_부른다(
    gateway_on: None, db: Session, workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """포털에 붙은 설치는 `PUBLIC_PATH=/<id>` 인데 게이트웨이는 포털 nginx 를 안 거치고 앱
    포트로 **직접** 부른다(`<접두>_SSO_URL=http://<A>:<앱 포트>/api/auth/sso`) — 접두어 없이
    와도 받고, 받은 토큰은 어느 길로 와도 그 사람이다."""
    from app.main import create_app

    prefix = "/hwaxapp"
    monkeypatch.setenv("PUBLIC_PATH", prefix)
    get_settings.cache_clear()
    app = create_app()
    app.state.session_factory = SessionLocal
    user = _person(db, workspace)
    with TestClient(app) as web:
        got = _ask(web, user.email)
        assert got.status_code == 200, got.text
        token = got.json()["access_token"]
        for path in ("/api/auth/me", f"{prefix}/api/auth/me"):
            me = web.get(path, headers=_bearer(token))
            assert me.status_code == 200 and me.json()["email"] == user.email, path


# --- 진단 ----------------------------------------------------------------------


def test_진단은_위임이_켜졌나_최근_발급과_거절을_말한다(
    client: TestClient,
    gateway_on: None,
    admin: Signed,
    db: Session,
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트웨이는 위임이 200 이 아니면 그 사람의 도구 호출을 거절한다 — 서버 화면이
    「마지막으로 거절한 때 · 왜」 를 말해야 그 사람의 신고를 받고 찾을 자리가 있다."""
    user = _person(db, workspace)
    assert _ask(client, user.email).status_code == 200
    _ask(client, user.email, secret="wrong")

    body = client.get("/api/auth/portal-status", headers=admin.headers).json()
    one = body["delegation"]
    assert one["enabled"] is True and one["sso_path"] == "/api/auth/sso"
    assert one["scopes"][0] == "read" and one["token_seconds"] == 86400
    assert one["live_tokens"] >= 1 and one["last_issued_at"] is not None
    refusal = one["last_refusal"]
    assert refusal is not None and refusal["status"] == 401
    assert "비밀이 다르다" in refusal["reason"]  # 이 프로세스가 본 거절 — 자세한 사유

    # 다른 프로세스가 한 거절이면 숫자의 뜻을 말한다(접근 로그에서 — 워커 · 서버가 여럿이다).
    delegation.refusals.reset()
    refusal = client.get("/api/auth/portal-status", headers=admin.headers).json()[
        "delegation"
    ]["last_refusal"]
    assert refusal["status"] == 401 and "HEAX_SSO_SECRET" in refusal["reason"]

    # 설정끼리 안 맞는 것 — 짧은 비밀 · 못 읽는 허용 주소.
    monkeypatch.setenv("HEAX_SSO_SECRET", "short")
    monkeypatch.setenv("HEAX_SSO_ALLOWED_IPS", "10.1.2.3,portal-box")
    get_settings.cache_clear()
    body = client.get("/api/auth/portal-status", headers=admin.headers).json()
    assert body["delegation"]["allowed_ips"] == ["10.1.2.3/32"]
    assert body["delegation"]["invalid_ips"] == ["portal-box"]
    assert any("HEAX_SSO_SECRET 이 5자로 짧습니다" in one for one in body["problems"])
    assert any("portal-box" in one for one in body["problems"])


def test_진단은_꺼진_위임도_말한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEAX_SSO_SECRET", "")
    monkeypatch.setenv("PORTAL_JWKS_URL", "")
    get_settings.cache_clear()
    try:
        body = client.get("/api/auth/portal-status", headers=admin.headers).json()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert body["delegation"]["enabled"] is False
    assert not any("HEAX_SSO" in one for one in body["problems"])
