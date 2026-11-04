"""인증 — 로그인, 세션 회전, 비밀번호 변경, 개인 토큰.

이 틀에서 가장 먼저 깨지면 안 되는 것이다. 여기가 무너지면 나머지 화면은
아무도 못 연다.
"""

from __future__ import annotations

import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.branding import ERROR_PREFIX
from app.modules.accounts.models import User
from app.modules.audit.models import AuditEntry
from app.modules.auth import security
from app.modules.auth.models import PersonalAccessToken, RefreshToken
from app.modules.workspaces.models import Workspace
from app.shared.errors import code
from app.shared.request_context import get_request_id
from tests.api.conftest import PASSWORD, Signed, _make_user


def test_로그인하면_소속과_역할이_함께_온다(client: TestClient, admin: Signed) -> None:
    """**화면이 사이드바를 그리려면 이것이 필요하다.**

    소속을 따로 한 번 더 물어야 하면 로그인 직후 화면이 한 번 비었다 채워지고,
    사람은 그 깜빡임을 고장으로 읽는다.
    """
    response = client.get("/api/auth/me", headers=admin.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_system_admin"] is True
    assert body["home_workspace_slug"] == admin.workspace
    assert [one["slug"] for one in body["memberships"]] == [admin.workspace]
    # 경로가 있어야 같은 이름의 팀이 둘일 때 구별된다.
    assert body["memberships"][0]["path"]


def test_틀린_비밀번호는_사유를_안_알려_준다(client: TestClient, admin: Signed) -> None:
    """계정이 있는지 없는지가 응답에서 새면 안 된다 — 둘 다 같은 말을 한다."""
    wrong = client.post(
        "/api/auth/login", json={"email": admin.email, "password": "not-the-password"}
    )
    missing = client.post(
        "/api/auth/login", json={"email": "nobody@example.local", "password": "x"}
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["error"]["message"] == missing.json()["error"]["message"]


def test_승인_대기_계정은_사유를_말한다(
    client: TestClient, db: Session, workspace: Workspace
) -> None:
    """**"왜 안 되는지" 를 구분해 준다.** 승인 대기와 정지는 사람이 할 일이 다르고,
    "비활성 계정" 이라고만 하면 관리자에게 무엇을 요청해야 할지 알 수 없다."""
    user = User(
        email="waiting@example.local",
        password_hash=security.hash_password(PASSWORD),
        display_name="대기",
        status="pending",
        requested_workspace_id=workspace.id,
    )
    db.add(user)
    db.commit()

    response = client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert response.status_code == 403
    assert "승인" in response.json()["error"]["message"]


def test_오류는_봉투와_요청id_를_싣는다(client: TestClient) -> None:
    """**사용자가 전달할 수 있는 문자열이 있어야 로그에서 그 요청을 찾는다.**

    화면마다 다르게 만들면 어떤 화면에서는 코드가 안 보여서 재현이 막힌다.
    """
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    error = response.json()["error"]
    # **접두사를 손으로 박지 않는다.** 포크해서 ERROR_PREFIX 를 바꾸면 올바른
    # 코드인데도 이 시험이 깨지고, 사람은 자기가 뭘 잘못했나 찾게 된다.
    assert error["code"].startswith(f"{ERROR_PREFIX}-")
    assert error["request_id"]
    # 헤더로도 나간다 — 응답 본문을 못 읽는 상황에서도 끈이 남아야 한다.
    assert response.headers["X-Request-ID"] == error["request_id"]


def test_처리_못한_오류에도_요청id_가_실린다() -> None:
    """**500 이야말로 요청 ID 가 필요한 자리다.** 500 은 요청 ID 미들웨어 바깥의 처리기가
    만드는데, 미들웨어가 먼저 값을 되돌려 본문 · 헤더 · 로그가 모두 「-」 였다 — 화면은
    「요청 ID 를 알려 주세요」 라고 하면서 ID 를 안 줬다(v0.4.34 까지)."""
    from app.main import app
    from app.shared.auth import current_user

    def boom() -> User:
        raise RuntimeError("일부러 낸 오류")

    logged: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if "500 unhandled" in record.getMessage():
                logged.append(get_request_id())

    grab = Grab()
    errors_log = logging.getLogger("app.shared.errors")
    errors_log.addHandler(grab)
    app.dependency_overrides[current_user] = boom
    try:
        response = TestClient(app, raise_server_exceptions=False).get("/api/auth/me")
    finally:
        app.dependency_overrides.pop(current_user, None)
        errors_log.removeHandler(grab)
    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"].endswith("COMMON-0500")
    assert error["request_id"] not in ("", "-")
    assert response.headers["X-Request-ID"] == error["request_id"]
    # 로그의 그 줄에도 같은 ID — 신고받은 ID 로 스택 트레이스를 찾는다.
    assert logged == [error["request_id"]]


def test_세션은_한_번_쓰면_회전한다(client: TestClient, admin: Signed) -> None:
    """회전하지 않으면 탈취된 토큰이 만료까지 유효하다."""
    login = client.post("/api/auth/login", json={"email": admin.email, "password": PASSWORD})
    assert login.status_code == 200, login.text

    first = client.post("/api/auth/refresh")
    assert first.status_code == 200, first.text
    # 쿠키가 바뀌었다 = 새 refresh 가 발급됐다.
    assert first.json()["access_token"]

    second = client.post("/api/auth/refresh")
    assert second.status_code == 200, second.text


def test_비밀번호를_바꾸면_모든_세션이_끊긴다(client: TestClient, member: Signed) -> None:
    """바꾼 이유가 유출일 수 있다. **다른 기기의 로그인이 남아 있으면** 바꾼 의미가
    없다."""
    changed = client.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "brand-new-password"},
        headers=member.headers,
    )
    assert changed.status_code == 204, changed.text

    # 쿠키가 지워졌으므로 갱신이 안 된다.
    assert client.post("/api/auth/refresh").status_code == 401


def test_같은_비밀번호로는_못_바꾼다(client: TestClient, member: Signed) -> None:
    """길이 하한 대신 이 검사를 둔다 — 지키려던 것은 길이가 아니다."""
    response = client.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": PASSWORD},
        headers=member.headers,
    )
    assert response.status_code == 400


def test_개인_토큰은_읽기만_기본이다(client: TestClient, admin: Signed) -> None:
    """**기본값이 전권이면 「일단 만들고 나중에 좁히자」 가 되고, 나중은 오지 않는다.**"""
    created = client.post(
        "/api/auth/tokens", json={"name": "연계 스크립트"}, headers=admin.headers
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["pat"]["scopes"] == ["read"]
    assert body["token"].startswith(security.pat_prefix())

    # 발급된 토큰으로 읽기는 된다.
    with_pat = {"Authorization": f"Bearer {body['token']}"}
    assert client.get("/api/auth/me", headers=with_pat).status_code == 200


def test_개인_토큰으로는_아무_경로나_못_고친다(client: TestClient, admin: Signed) -> None:
    """**모르는 것은 막는다.** 등록되지 않은 쓰기 경로는 어떤 범위로도 안 열린다 —
    새 엔드포인트가 생길 때마다 자동으로 열리면 그것을 알아채는 사람이 없다."""
    created = client.post(
        "/api/auth/tokens", json={"name": "쓰기 시도"}, headers=admin.headers
    )
    token = created.json()["token"]

    response = client.post(
        "/api/workspaces",
        json={"slug": "sneaky", "name": "몰래"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "개인 토큰" in response.json()["error"]["message"]


def test_모르는_범위는_발급을_거절한다(client: TestClient, admin: Signed) -> None:
    response = client.post(
        "/api/auth/tokens",
        json={"name": "이상한 범위", "scopes": ["read", "everything:write"]},
        headers=admin.headers,
    )
    assert response.status_code == 400
    assert "everything:write" in response.json()["error"]["message"]


def test_아는_범위_목록은_서버가_준다(client: TestClient, admin: Signed) -> None:
    """화면이 손 목록을 들면 반드시 서버와 어긋난다."""
    response = client.get("/api/auth/token-scopes", headers=admin.headers)
    assert response.status_code == 200
    assert "read" in response.json()["scopes"]


def test_관리자가_비밀번호를_초기화하면_그_사람의_세션이_끊기고_기록이_남는다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """초기화는 대개 「비밀번호가 샜다」 다 — 옛 세션(리프레시 사슬)이 살아 있으면 초기화한
    뜻이 없다. 본인이 바꿀 때는 끊었는데 관리자 초기화는 안 끊고 기록도 안 남겼다
    (2026-10-08)."""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.modules.accounts.models import User
    from app.modules.audit.models import AuditEntry
    from app.modules.auth.models import RefreshToken

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == member.email))
        assert user is not None
        user_id = user.id
        alive = list(
            db.scalars(
                select(RefreshToken.id).where(
                    RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
                )
            )
        )
    assert alive, "로그인한 세션이 있어야 시험이 뜻이 있다"
    reset = client.post(f"/api/accounts/{user_id}/reset-password", headers=admin.headers)
    assert reset.status_code == 200, reset.text
    with SessionLocal() as db:
        left = db.scalar(
            select(RefreshToken.id).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
            )
        )
        assert left is None
        logged = db.scalar(
            select(AuditEntry).where(
                AuditEntry.action == "account.password_reset", AuditEntry.target_id == user_id
            )
        )
        assert logged is not None and logged.changes["sessions_revoked"] >= 1


def test_검증_오류는_비밀번호를_남기지_않는다(client: TestClient) -> None:
    """틀린 모양의 본문이 오면 그 값이 로그와 응답에 그대로 실렸다 — 비밀번호까지
    (2026-10-08)."""
    got = client.post(
        "/api/auth/login", json={"username": "a@b.c", "password": "hunter2-secret"}
    )
    assert got.status_code == 422
    assert "hunter2-secret" not in got.text


# --- 로그인의 입구 ---------------------------------------------------------------


def test_없는_계정도_해시_검증을_한_번만_한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**응답 시간으로 계정이 있는지가 새면 안 된다.** 없는 계정 쪽이 해시를 새로 만들고
    (bcrypt 한 번) 그것으로 검증해(또 한 번) 있는 계정보다 두 배 느렸다 — 막으려던 것과
    반대로 「느린 쪽이 없는 계정」 이었다(2026-10-08). 시간은 흔들리므로 bcrypt 를 부른
    횟수로 본다."""
    import bcrypt

    called: list[str] = []
    hashpw, checkpw = bcrypt.hashpw, bcrypt.checkpw

    def counted_hash(password: bytes, salt: bytes) -> bytes:
        called.append("hash")
        return hashpw(password, salt)

    def counted_check(password: bytes, hashed: bytes) -> bool:
        called.append("check")
        return checkpw(password, hashed)

    monkeypatch.setattr(bcrypt, "hashpw", counted_hash)
    monkeypatch.setattr(bcrypt, "checkpw", counted_check)

    def work(email: str) -> list[str]:
        body = {"email": email, "password": "not-the-password"}
        client.post("/api/auth/login", json=body)  # 처음 한 번은 준비(미리 만드는 해시)
        called.clear()
        assert client.post("/api/auth/login", json=body).status_code == 401
        return list(called)

    assert work(f"nobody-{uuid.uuid4().hex[:8]}@example.local") == ["check"]
    assert work(admin.email) == ["check"]


def test_로그인은_아이디를_가입과_같게_다듬는다(client: TestClient, admin: Signed) -> None:
    """가입 · 관리자 생성은 앞뒤 공백을 떼고 소문자로 저장하는데 로그인은 소문자만 했다 —
    붙여 넣다 딸려 온 공백 하나로 「이메일 또는 비밀번호가 올바르지 않습니다」 가 났다
    (2026-10-08)."""
    got = client.post(
        "/api/auth/login", json={"email": f"  {admin.email.upper()}\t", "password": PASSWORD}
    )
    assert got.status_code == 200, got.text


def test_인코딩된_물음표로_범위_판정을_비껴가지_못한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """범위는 **라우팅과 같은 경로**로 판정한다. `request.url.path` 는 디코딩된 경로로 주소를
    다시 지어 쪼개므로 `%3F`(?) · `%23`(#) 에서 잘린다 — 틀에서는
    `/api/metrics/plan%3F/recompute` 가 「읽기로 연 `/api/metrics/plan`」 으로 보여 읽기
    토큰이 재계산(쓰기) 경로를 지나갔다(2026-10-08). 이 플랫폼에는 아직 읽기로 연 POST 가
    없어 **시험 동안만** 하나 연다."""
    from app.shared import scopes

    monkeypatch.setattr(
        scopes, "_read_only_posts", [*scopes._read_only_posts, "/api/jobs/plan"]
    )
    made = client.post(
        "/api/auth/tokens", json={"name": "읽기만", "scopes": ["read"]}, headers=admin.headers
    )
    head = {"Authorization": f"Bearer {made.json()['token']}"}
    for path in ("/api/jobs/plan%3F/apply", "/api/jobs/plan%23/apply"):
        got = client.post(path, headers=head)
        assert got.status_code == 403, (path, got.status_code, got.text)
        assert got.json()["error"]["code"] == code("AUTH", 106), got.text


# --- 계정의 생애와 세션 ------------------------------------------------------------


def test_계정을_만들거나_승인할_때_모르는_역할은_거절한다(
    client: TestClient, admin: Signed, workspace: Workspace
) -> None:
    """역할 칸을 안 봐서 `owner` 같은 아무 글자나 부서 역할로 들어갔다 — 그 사람은 관리자도
    멤버도 아닌 채로 남고, 부서 화면 어디에서도 설명되지 않는다(2026-10-08)."""
    email = f"role-{uuid.uuid4().hex[:8]}@example.local"
    body = {"email": email, "display_name": "역할", "workspace_slug": workspace.slug}
    refused = client.post(
        "/api/accounts", json={**body, "role": "owner"}, headers=admin.headers
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == code("ACCOUNTS", 10)
    # 반쯤 만든 계정을 남기지 않는다 — 남기면 같은 아이디로 다시 못 만든다.
    made = client.post(
        "/api/accounts", json={**body, "role": "manager"}, headers=admin.headers
    )
    assert made.status_code == 201, made.text
    assert [one["role"] for one in made.json()["account"]["workspaces"]] == ["manager"]

    signup = client.post(
        "/api/accounts/signup",
        json={
            "email": f"wait-{uuid.uuid4().hex[:8]}@example.local",
            "password": "signup-password",
            "display_name": "대기",
            "workspace_slug": workspace.slug,
        },
    )
    assert signup.status_code == 201, signup.text
    approve = f"/api/accounts/{signup.json()['id']}/approve"
    wrong = client.post(approve, json={"role": "superuser"}, headers=admin.headers)
    assert wrong.status_code == 400, wrong.text
    assert wrong.json()["error"]["code"] == code("ACCOUNTS", 10)
    # 거절이 대기 상태를 안 바꿨다 — 맞는 역할로 다시 승인된다.
    approved = client.post(approve, json={"role": "member"}, headers=admin.headers)
    assert approved.status_code == 200, approved.text


def test_관리자가_만든_계정과_그때_준_시스템_관리자_권한이_기록에_남는다(
    client: TestClient, db: Session, admin: Signed, workspace: Workspace
) -> None:
    """계정 화면에서 시스템 관리자를 **켜면** 기록이 남는데, 만들면서 켜면 아무것도 안 남았다
    — 같은 권한이 길에 따라 흔적이 있고 없었다(2026-10-08)."""
    made = client.post(
        "/api/accounts",
        json={
            "email": f"made-{uuid.uuid4().hex[:8]}@example.local",
            "display_name": "새 관리자",
            "workspace_slug": workspace.slug,
            "is_system_admin": True,
        },
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    account_id = uuid.UUID(made.json()["account"]["id"])
    actions = set(
        db.scalars(select(AuditEntry.action).where(AuditEntry.target_id == account_id))
    )
    assert actions == {"account.created", "account.admin_changed"}


def _own_session(email: str) -> tuple[TestClient, dict[str, str]]:
    """그 사람의 브라우저 — refresh 쿠키를 **따로** 든다(공용 `client` 의 쿠키는 마지막에
    로그인한 사람 것이다). 그 사람의 개인 토큰도 하나 만들어 머리로 돌려준다."""
    from app.main import app

    own = TestClient(app)
    signed = own.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert signed.status_code == 200, signed.text
    made = own.post(
        "/api/auth/tokens",
        json={"name": "연동"},
        headers={"Authorization": f"Bearer {signed.json()['access_token']}"},
    )
    assert made.status_code == 201, made.text
    return own, {"Authorization": f"Bearer {made.json()['token']}"}


def test_정지했다_다시_켜도_옛_세션은_살아나지_않는다(
    client: TestClient, db: Session, admin: Signed, workspace: Workspace
) -> None:
    """정지는 세션을 끊지 않고 **막기만** 했다 — 정지 동안 refresh 는 거절되지만 다시 켜는
    순간 정지 전의 로그인(30일)이 그대로 이어졌다. 정지한 까닭이 「그 사람의 기기가 샜다」
    면 다시 켠 날 그 기기가 돌아온다(2026-10-08).

    개인 토큰은 다르다 — 정지 동안 막히고 다시 켜면 산다. 연동은 사람이 그 자리에서 다시
    로그인하지 못한다(초기화도 토큰은 따로 폐기한다)."""
    user = _make_user(db, workspace, label="paused", is_system_admin=False, role="member")
    own, machine = _own_session(user.email)

    paused = client.post(f"/api/accounts/{user.id}/suspend", headers=admin.headers)
    assert paused.status_code == 200, paused.text
    assert own.get("/api/auth/me", headers=machine).status_code == 401
    resumed = client.post(f"/api/accounts/{user.id}/activate", headers=admin.headers)
    assert resumed.status_code == 200, resumed.text

    assert own.post("/api/auth/refresh").status_code == 401
    assert own.get("/api/auth/me", headers=machine).status_code == 200
    # 막은 것은 옛 세션이지 계정이 아니다 — 다시 로그인하면 이어진다.
    login = own.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    assert own.post("/api/auth/refresh").status_code == 200

    logged = [
        entry.changes
        for entry in db.scalars(
            select(AuditEntry).where(
                AuditEntry.action == "account.suspended", AuditEntry.target_id == user.id
            )
        )
    ]
    assert {"status": {"before": "active", "after": "suspended"}, "sessions_revoked": 1} in (
        logged
    )


def test_계정을_지우면_세션과_개인_토큰을_폐기한다(
    client: TestClient, db: Session, admin: Signed, workspace: Workspace
) -> None:
    """지운 계정은 되살리는 길이 없다. 막혀 있기만 한 자격을 살아 있는 채로 두면 「이 창구를
    읽을 수 있는 자격」 목록(코어 › 액세스 토큰)이 그것을 센다 — 폐기해 두면 목록과 실제가
    같다(2026-10-08)."""
    user = _make_user(db, workspace, label="leaving", is_system_admin=False, role="member")
    own, machine = _own_session(user.email)

    gone = client.delete(f"/api/accounts/{user.id}", headers=admin.headers)
    assert gone.status_code == 200, gone.text
    assert own.get("/api/auth/me", headers=machine).status_code == 401
    assert own.post("/api/auth/refresh").status_code in (401, 403)

    alive_refresh = db.scalar(
        select(RefreshToken.id).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    )
    alive_pat = db.scalar(
        select(PersonalAccessToken.id).where(
            PersonalAccessToken.user_id == user.id, PersonalAccessToken.revoked_at.is_(None)
        )
    )
    assert alive_refresh is None and alive_pat is None
    entry = db.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "account.deleted", AuditEntry.target_id == user.id
        )
    )
    assert entry is not None
    assert entry.changes == {"sessions_revoked": 1, "tokens_revoked": 1}


def _browser(email: str, password: str = PASSWORD) -> tuple[TestClient, dict[str, str]]:
    """그 사람의 브라우저 하나 — refresh 쿠키를 따로 들고, 받은 access 를 머리로 돌려준다."""
    from app.main import app

    own = TestClient(app)
    signed = own.post("/api/auth/login", json={"email": email, "password": password})
    assert signed.status_code == 200, signed.text
    return own, {"Authorization": f"Bearer {signed.json()['access_token']}"}


def test_세션을_끊으면_이미_받은_access_도_못_쓴다(
    client: TestClient, db: Session, admin: Signed, workspace: Workspace
) -> None:
    """끊는 일은 refresh 만 폐기했다 — 이미 받은 access(12시간)는 만료까지 그대로 통해서,
    「샜다」 고 초기화 · 정지한 뒤에도 공격자가 쥔 토큰이 반나절 살았다(2026-10-08)."""
    user = _make_user(db, workspace, label="leaked", is_system_admin=False, role="member")

    own, access = _browser(user.email)
    assert own.get("/api/auth/me", headers=access).status_code == 200
    reset = client.post(f"/api/accounts/{user.id}/reset-password", headers=admin.headers)
    assert reset.status_code == 200, reset.text
    assert own.get("/api/auth/me", headers=access).status_code == 401

    # 정지했다 다시 켜도 정지 전의 access 는 돌아오지 않는다.
    db.refresh(user)
    user.password_hash = security.hash_password(PASSWORD)
    db.commit()
    own, access = _browser(user.email)
    assert client.post(f"/api/accounts/{user.id}/suspend", headers=admin.headers).is_success
    assert client.post(f"/api/accounts/{user.id}/activate", headers=admin.headers).is_success
    assert own.get("/api/auth/me", headers=access).status_code == 401

    # 막은 것은 그때까지의 로그인이다 — 새로 받은 것은 통한다.
    _, fresh = _browser(user.email)
    assert own.get("/api/auth/me", headers=fresh).status_code == 200


def test_비밀번호를_바꾸면_옛_기기의_갱신이_새_로그인을_끊지_않는다(
    db: Session, workspace: Workspace
) -> None:
    """비밀번호 변경으로 폐기된 옛 기기의 refresh 가 늦게 오면 「탈취」 로 보고 그 사람의
    로그인을 **전부** 끊었다 — 바꾼 뒤 새로 한 로그인까지(2026-10-08). 회전이 아니라 끊어서
    폐기된 것은 그 기기만 거절한다."""
    user = _make_user(db, workspace, label="mover", is_system_admin=False, role="member")
    old_device, old_access = _browser(user.email)
    laptop, laptop_access = _browser(user.email)

    changed = laptop.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "brand-new-password"},
        headers=laptop_access,
    )
    assert changed.status_code == 204, changed.text
    # 바꾼 그 자리의 access 도, 다른 기기의 access 도 끊겼다.
    assert laptop.get("/api/auth/me", headers=laptop_access).status_code == 401
    assert old_device.get("/api/auth/me", headers=old_access).status_code == 401

    laptop, fresh = _browser(user.email, "brand-new-password")
    late = old_device.post("/api/auth/refresh")
    assert late.status_code == 401
    assert late.json()["error"]["code"] == code("AUTH", 3)
    # 새 로그인은 살아 있다.
    assert laptop.get("/api/auth/me", headers=fresh).status_code == 200
    assert laptop.post("/api/auth/refresh").status_code == 200


def test_회전으로_넘겨진_값이_다시_오면_전부_끊는다(db: Session, workspace: Workspace) -> None:
    """탈취 탐지는 그대로다 — 회전으로 이미 넘겨진 값이 유예를 지나 다시 오면 사슬이 둘로
    갈린 것이다. 그 사람의 refresh 도, 이미 받은 access 도 모두 끊는다."""
    from app.config import get_settings
    from app.main import app
    from app.modules.auth import services as auth_services

    cookie = get_settings().refresh_cookie_name
    user = _make_user(db, workspace, label="stolen", is_system_admin=False, role="member")
    own, _ = _browser(user.email)
    stolen = own.cookies.get(cookie)
    assert stolen
    rotated = own.post("/api/auth/refresh")
    assert rotated.status_code == 200, rotated.text
    access = {"Authorization": f"Bearer {rotated.json()['access_token']}"}

    token = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == security.hash_token(stolen))
    )
    assert token is not None and token.revoked_at is not None
    token.revoked_at = token.revoked_at - auth_services.REFRESH_GRACE * 2
    db.commit()

    thief = TestClient(app)
    thief.cookies.set(cookie, stolen)
    replay = thief.post("/api/auth/refresh")
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == code("AUTH", 5)
    assert own.get("/api/auth/me", headers=access).status_code == 401
    assert own.post("/api/auth/refresh").status_code == 401


# --- 관리자가 남의 토큰을 폐기한다 ---------------------------------------------------


def _issued(
    email: str, *names: str
) -> tuple[TestClient, dict[str, dict[str, str]], list[str]]:
    """그 사람의 브라우저로 토큰을 이름마다 하나씩 — (브라우저, {이름: 머리}, [id])."""
    from app.main import app

    own = TestClient(app)
    signed = own.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert signed.status_code == 200, signed.text
    bearer = {"Authorization": f"Bearer {signed.json()['access_token']}"}
    heads: dict[str, dict[str, str]] = {}
    ids: list[str] = []
    for name in names:
        made = own.post("/api/auth/tokens", json={"name": name}, headers=bearer)
        assert made.status_code == 201, made.text
        heads[name] = {"Authorization": f"Bearer {made.json()['token']}"}
        ids.append(made.json()["pat"]["id"])
    return own, heads, ids


def test_관리자가_남의_토큰_하나를_폐기하면_그_연동만_끊기고_기록과_알림이_남는다(
    client: TestClient, db: Session, admin: Signed, workspace: Workspace
) -> None:
    """예전에는 본인만 폐기했다 — 퇴사자 · 정지된 계정의 연동을 끊으려면 계정을 지워야 했고,
    지우면 그 사람의 토큰이 전부 끊겼다(2026-10-08). 이제 관리자가 하나만 고른다."""
    from app.modules.notifications.models import Notification

    user = _make_user(db, workspace, label="leaver", is_system_admin=False, role="member")
    own, heads, (nightly, script) = _issued(user.email, "야간 동기화", "정제 스크립트")

    listed = client.get(f"/api/accounts/{user.id}/tokens", headers=admin.headers)
    assert listed.status_code == 200, listed.text
    assert {one["name"] for one in listed.json()} == {"야간 동기화", "정제 스크립트"}
    assert all(
        one["revoked_at"] is None and one["scopes"] == ["read"] for one in listed.json()
    )

    gone = client.post(
        f"/api/accounts/{user.id}/tokens/{nightly}/revoke",
        json={"reason": "  퇴사  "},
        headers=admin.headers,
    )
    assert gone.status_code == 200, gone.text
    assert gone.json()["id"] == nightly and gone.json()["revoked_at"] is not None

    # 그 토큰만 끊긴다 — 다른 토큰과 계정은 그대로다.
    assert own.get("/api/auth/me", headers=heads["야간 동기화"]).status_code == 401
    assert own.get("/api/auth/me", headers=heads["정제 스크립트"]).status_code == 200
    db.expire_all()
    assert db.get(User, user.id).status == "active"  # type: ignore[union-attr]
    again = client.get(f"/api/accounts/{user.id}/tokens", headers=admin.headers).json()
    assert {one["id"]: bool(one["revoked_at"]) for one in again} == {
        nightly: True,
        script: False,
    }

    entry = db.scalar(
        select(AuditEntry).where(
            AuditEntry.action == "account.token_revoked",
            AuditEntry.target_id == uuid.UUID(nightly),
        )
    )
    assert entry is not None
    assert entry.actor_label and entry.reason == "퇴사"
    assert entry.target_table == "personal_access_tokens"
    assert entry.target_label == f"{user.email} · 야간 동기화"
    assert entry.changes["owner"] == user.email
    assert entry.changes["owner_id"] == str(user.id)
    assert entry.changes["name"] == "야간 동기화" and entry.changes["scopes"] == ["read"]
    admin_row = db.scalar(select(User).where(User.email == admin.email))
    assert admin_row is not None and entry.actor_id == admin_row.id

    # 토큰 주인이 안다 — 연동이 401 을 받기 시작한 까닭을 찾아 헤매지 않게.
    told = db.scalar(
        select(Notification).where(
            Notification.user_id == user.id, Notification.kind == "account.token_revoked"
        )
    )
    assert told is not None and "야간 동기화" in told.title
    assert told.body is not None and "퇴사" in told.body and told.link == "/me"


def test_이미_폐기된_토큰과_없는_토큰과_남의_토큰은_폐기하지_않는다(
    client: TestClient, db: Session, admin: Signed, workspace: Workspace
) -> None:
    """**다시 폐기하지 않는다**(409) — 처음 폐기한 시각과 기록을 덮지 않으려고. 다른 사람의
    토큰 id 를 섞어 보내면 없는 것과 같다(404) — 경로의 계정과 토큰이 짝이 맞아야 한다."""
    one = _make_user(db, workspace, label="one", is_system_admin=False, role="member")
    other = _make_user(db, workspace, label="other", is_system_admin=False, role="member")
    own, _, (mine,) = _issued(one.email, "연동")
    _, _, (theirs,) = _issued(other.email, "남의 연동")

    # 본인이 먼저 폐기했다.
    signed = own.post("/api/auth/login", json={"email": one.email, "password": PASSWORD})
    bearer = {"Authorization": f"Bearer {signed.json()['access_token']}"}
    assert own.delete(f"/api/auth/tokens/{mine}", headers=bearer).status_code == 204
    twice = client.post(
        f"/api/accounts/{one.id}/tokens/{mine}/revoke", json={}, headers=admin.headers
    )
    assert twice.status_code == 409, twice.text
    assert twice.json()["error"]["code"] == code("ACCOUNTS", 12)
    assert "revoked_at" in twice.json()["error"]["details"]

    crossed = client.post(
        f"/api/accounts/{one.id}/tokens/{theirs}/revoke", json={}, headers=admin.headers
    )
    assert crossed.status_code == 404, crossed.text
    assert crossed.json()["error"]["code"] == code("ACCOUNTS", 11)
    nowhere = client.post(
        f"/api/accounts/{one.id}/tokens/{uuid.uuid4()}/revoke", json={}, headers=admin.headers
    )
    assert nowhere.status_code == 404
    nobody = client.get(f"/api/accounts/{uuid.uuid4()}/tokens", headers=admin.headers)
    assert nobody.status_code == 404
    assert nobody.json()["error"]["code"] == code("ACCOUNTS", 1)

    # 남의 토큰은 그대로 살아 있고, 거절한 것은 기록에 안 남는다.
    alive = db.scalar(
        select(PersonalAccessToken).where(PersonalAccessToken.id == uuid.UUID(theirs))
    )
    assert alive is not None and alive.revoked_at is None
    logged = db.scalars(
        select(AuditEntry.target_id).where(AuditEntry.action == "account.token_revoked")
    ).all()
    assert uuid.UUID(mine) not in logged and uuid.UUID(theirs) not in logged


def test_관리자가_아니면_남의_토큰을_보지도_폐기하지도_못한다(
    client: TestClient, db: Session, member: Signed, workspace: Workspace
) -> None:
    target = _make_user(db, workspace, label="target", is_system_admin=False, role="member")
    own, heads, (pat,) = _issued(target.email, "연동")

    assert (
        client.get(f"/api/accounts/{target.id}/tokens", headers=member.headers).status_code
        == 403
    )
    refused = client.post(
        f"/api/accounts/{target.id}/tokens/{pat}/revoke",
        json={"reason": "몰래"},
        headers=member.headers,
    )
    assert refused.status_code == 403, refused.text
    assert own.get("/api/auth/me", headers=heads["연동"]).status_code == 200
