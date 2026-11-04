"""이 틀이 지키기로 한 규칙들 — **어기면 여기서 걸린다.**

지침 문서에만 적힌 규칙은 반드시 어긋난다. 급할 때 사람은 문서를 안 읽는다.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.branding import ERROR_PREFIX
from app.config import get_settings
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed

# --- 계정 --------------------------------------------------------------------


def _all_accounts(client: TestClient, admin: Signed) -> list[dict[str, Any]]:
    """계정을 **끝까지** 읽는다.

    목록은 서버가 상한을 강제한다(기본 50). 첫 쪽만 읽고 「이게 전부」 로 여기면,
    시험이 늘어 계정이 그 상한을 넘는 날 **조용히 틀린 것을 보게 된다** — 그리고
    그 실패는 계정과 아무 상관 없어 보이는 시험에서 튀어나온다. 실측으로 그렇게
    나왔다(온톨로지 시험을 더한 날).
    """
    out: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = client.get(
            f"/api/accounts?limit=100&offset={offset}", headers=admin.headers
        ).json()
        out.extend(page["items"])
        if offset + 100 >= page["total"]:
            return out
        offset += 100


def test_마지막_관리자는_정지할_수_없다(client: TestClient, admin: Signed) -> None:
    """잃으면 복구 경로가 **서버 콘솔뿐**이다. 그 상태는 실제로 일어나고, 일어난
    뒤에는 화면에서 할 수 있는 것이 하나도 없다.

    **다른 관리자를 먼저 치운다.** 시험 DB 는 스위트 하나를 통째로 함께 쓰므로
    앞선 시험들이 만든 관리자가 남아 있다 — 그것을 안 치우고 「마지막이니 막힐
    것」 을 기대하면, 이 시험은 관리자가 하나뿐일 때만 우연히 통과한다.
    """
    accounts = _all_accounts(client, admin)
    me = next(one for one in accounts if one["email"] == admin.email)
    others = [
        one
        for one in accounts
        if one["is_system_admin"] and one["status"] == "active" and one["id"] != me["id"]
    ]
    for one in others:
        cleared = client.post(f"/api/accounts/{one['id']}/suspend", headers=admin.headers)
        assert cleared.status_code == 200, cleared.text

    response = client.post(f"/api/accounts/{me['id']}/suspend", headers=admin.headers)
    assert response.status_code == 409
    assert "마지막" in response.json()["error"]["message"]

    # 권한 해제도 같은 문턱에 걸린다 — 정지만 막고 해제를 열어 두면 같은 자리로
    # 가는 길이 하나 남는다.
    demote = client.post(
        f"/api/accounts/{me['id']}/system-admin",
        json={"is_system_admin": False},
        headers=admin.headers,
    )
    assert demote.status_code == 409


def test_자기_계정은_못_지운다(client: TestClient, admin: Signed) -> None:
    accounts = _all_accounts(client, admin)
    me = next(one for one in accounts if one["email"] == admin.email)
    assert client.delete(f"/api/accounts/{me['id']}", headers=admin.headers).status_code == 409


def test_승인하면_알림이_간다(
    client: TestClient, db: Session, admin: Signed, workspace: Workspace
) -> None:
    """**메일이 없으므로 이것이 유일한 통보 경로다.** 안 보내면 신청한 사람은
    승인됐는지 알 방법이 없어 매일 로그인을 시도해 본다."""
    signup = client.post(
        "/api/accounts/signup",
        json={
            "email": "newbie@example.local",
            "password": "signup-password",
            "display_name": "새 사람",
            "workspace_slug": workspace.slug,
        },
    )
    assert signup.status_code == 201, signup.text
    account_id = signup.json()["id"]

    approved = client.post(
        f"/api/accounts/{account_id}/approve", json={}, headers=admin.headers
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["home_workspace_slug"] == workspace.slug

    # 본인으로 로그인해 알림을 확인한다 — 승인으로 로그인이 열렸어야 한다.
    login = client.post(
        "/api/auth/login",
        json={"email": "newbie@example.local", "password": "signup-password"},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    rows = client.get("/api/notifications", headers=headers).json()
    assert [one["kind"] for one in rows] == ["account.approved"]
    # **링크가 있어야 알림이 읽고 끝나는 글이 안 된다.**
    assert rows[0]["link"] == f"/w/{workspace.slug}"


def test_거절은_사유_없이는_안_된다(
    client: TestClient, admin: Signed, workspace: Workspace
) -> None:
    """메일이 없어 통보가 앱 안에서만 되므로, 안 적히면 신청한 사람은 이유를
    영영 모른다."""
    signup = client.post(
        "/api/accounts/signup",
        json={
            "email": "rejected@example.local",
            "password": "signup-password",
            "display_name": "거절될 사람",
            "workspace_slug": workspace.slug,
        },
    )
    account_id = signup.json()["id"]

    without = client.post(f"/api/accounts/{account_id}/reject", json={}, headers=admin.headers)
    assert without.status_code == 422

    with_note = client.post(
        f"/api/accounts/{account_id}/reject",
        json={"note": "타 부서 소속입니다."},
        headers=admin.headers,
    )
    assert with_note.status_code == 200
    assert with_note.json()["decision_note"] == "타 부서 소속입니다."


# --- 부서 --------------------------------------------------------------------


def test_부서를_자기_하위로_옮길_수_없다(client: TestClient, admin: Signed) -> None:
    """막지 않으면 트리에서 통째로 사라지고, 화면에 안 나오니 되돌릴 수도 없다."""
    parent = client.post(
        "/api/workspaces", json={"slug": "div-a", "name": "A본부"}, headers=admin.headers
    )
    assert parent.status_code == 201, parent.text
    child = client.post(
        "/api/workspaces",
        json={"slug": "team-a1", "name": "A1팀", "parent_slug": "div-a"},
        headers=admin.headers,
    )
    assert child.status_code == 201, child.text

    response = client.post(
        "/api/workspaces/div-a/move",
        json={"parent_slug": "team-a1"},
        headers=admin.headers,
    )
    assert response.status_code == 400


def test_하위_부서가_있으면_삭제를_막고_이유를_말한다(
    client: TestClient, admin: Signed
) -> None:
    """**누르기 전에 안다.** 지우고 나서 알게 되면 되돌릴 방법이 없다."""
    client.post(
        "/api/workspaces", json={"slug": "div-b", "name": "B본부"}, headers=admin.headers
    )
    client.post(
        "/api/workspaces",
        json={"slug": "team-b1", "name": "B1팀", "parent_slug": "div-b"},
        headers=admin.headers,
    )

    references = client.get("/api/workspaces/div-b/references", headers=admin.headers)
    assert references.status_code == 200, references.text
    blocking = [one for one in references.json() if one["blocks_delete"]]
    assert any(one["table"] == "workspaces" for one in blocking)

    deleted = client.delete("/api/workspaces/div-b", headers=admin.headers)
    assert deleted.status_code == 409
    assert "하위 부서" in deleted.json()["error"]["message"]


def test_부서_목록의_순서는_서버가_정한다(client: TestClient, admin: Signed) -> None:
    """화면이 평면 목록을 받아 스스로 트리를 세우면 화면마다 순서가 갈린다."""
    client.post(
        "/api/workspaces", json={"slug": "div-c", "name": "C본부"}, headers=admin.headers
    )
    client.post(
        "/api/workspaces",
        json={"slug": "team-c1", "name": "C1팀", "parent_slug": "div-c"},
        headers=admin.headers,
    )

    rows = client.get("/api/workspaces?all=true", headers=admin.headers).json()
    by_slug = {one["slug"]: one for one in rows}
    assert by_slug["team-c1"]["depth"] == by_slug["div-c"]["depth"] + 1
    assert by_slug["team-c1"]["path"].endswith("C본부 / C1팀")
    # 부모가 자식보다 먼저 온다 — 화면은 이 순서 그대로 그린다.
    order = [one["slug"] for one in rows]
    assert order.index("div-c") < order.index("team-c1")


def test_마지막_부서_관리자는_뺄_수_없다(
    client: TestClient, admin: Signed, member: Signed
) -> None:
    """그러면 그 부서는 아무도 못 고치는 상태가 된다."""
    accounts = _all_accounts(client, admin)
    me = next(one for one in accounts if one["email"] == admin.email)

    response = client.delete(
        f"/api/workspaces/{admin.workspace}/members/{me['id']}", headers=admin.headers
    )
    assert response.status_code == 409
    assert "마지막 관리자" in response.json()["error"]["message"]


def test_안_보낸_칸은_안_바뀐다(client: TestClient, admin: Signed) -> None:
    """**부분 수정에서 "안 보낸 것" 과 "비운 것" 을 구별한다.** 안 구별하면 이름
    하나 바꿀 때마다 보관 상태 · 설명이 함께 초기화되고, 그 손실은 저장한 사람 눈에
    안 보인다."""
    slug = f"div-{uuid.uuid4().hex[:6]}"
    client.post(
        "/api/workspaces",
        json={"slug": slug, "name": "D본부", "description": "설명"},
        headers=admin.headers,
    )
    client.patch(f"/api/workspaces/{slug}", json={"is_active": False}, headers=admin.headers)

    renamed = client.patch(
        f"/api/workspaces/{slug}", json={"name": "D사업본부"}, headers=admin.headers
    )
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["name"] == "D사업본부"
    assert body["is_active"] is False  # 함께 초기화되지 않았다
    assert body["description"] == "설명"


# --- 권한 --------------------------------------------------------------------


def test_관리자가_아니면_전사_목록을_못_본다(client: TestClient, member: Signed) -> None:
    assert client.get("/api/workspaces?all=true", headers=member.headers).status_code == 403


def test_멤버는_계정_관리에_못_들어간다(client: TestClient, member: Signed) -> None:
    """**눌러 보고 403 을 알게 하지 않는다** — 사이드바가 먼저 가리지만, 판정은
    언제나 서버가 한다."""
    assert client.get("/api/accounts", headers=member.headers).status_code == 403
    assert client.get("/api/server/status", headers=member.headers).status_code == 403
    assert client.get("/api/audit/access", headers=member.headers).status_code == 403


def test_멤버는_변경_이력을_못_본다(client: TestClient, member: Signed) -> None:
    """부서 관리자부터 본다 — 자기 부서의 일을 물을 사람이 그쪽이다."""
    assert client.get("/api/audit/entries", headers=member.headers).status_code == 403


def test_부서_관리자는_변경_이력을_본다(client: TestClient, admin: Signed) -> None:
    response = client.get("/api/audit/entries", headers=admin.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {"items", "total", "limit", "offset"} <= body.keys()


# --- 공통 화면 ---------------------------------------------------------------


def test_없는_엔드포인트는_봉투로_404_를_준다(client: TestClient, admin: Signed) -> None:
    """index.html 을 돌려주면 프론트가 200 HTML 을 JSON 으로 파싱하려다 실패해
    원인이 흐려진다."""
    response = client.get("/api/nope", headers=admin.headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"].startswith(f"{ERROR_PREFIX}-")


def test_남은_일은_0건을_안_내보낸다(client: TestClient, admin: Signed) -> None:
    """다 0 인 목록을 매일 보면 사람은 그 자리를 아예 안 읽게 되고, 그때 진짜
    하나가 떠도 눈에 안 들어온다."""
    response = client.get("/api/server/maintenance", headers=admin.headers)
    assert response.status_code == 200, response.text
    assert all(one["count"] > 0 for one in response.json())


def test_남은_일은_처리할_수_있는_사람에게만_뜬다(
    client: TestClient, member: Signed, admin: Signed, workspace: Workspace
) -> None:
    """가입 승인은 시스템 관리자의 일이다. 멤버에게 띄우면 그저 못 지우는 숫자가
    되고, 사람은 곧 홈의 그 칸을 아예 안 읽게 된다."""
    client.post(
        "/api/accounts/signup",
        json={
            "email": "pending-for-badge@example.local",
            "password": "signup-password",
            "display_name": "대기자",
            "workspace_slug": workspace.slug,
        },
    )

    admin_keys = {
        one["key"]
        for one in client.get("/api/server/maintenance", headers=admin.headers).json()
    }
    member_keys = {
        one["key"]
        for one in client.get("/api/server/maintenance", headers=member.headers).json()
    }
    assert "accounts_pending" in admin_keys
    assert "accounts_pending" not in member_keys


def test_서버_상태는_어느_DB_인지_말하되_비밀번호는_지운다(
    client: TestClient, admin: Signed
) -> None:
    """**화면에 그대로 뜨는 값이다.**"""
    response = client.get("/api/server/status", headers=admin.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["database_url_safe"].endswith("_test")
    assert "postgres:postgres@" not in body["database_url_safe"]
    # 레지스트리가 채운다 — 계정 하나는 공통 틀이 등록했다.
    assert any(one["label"] == "계정" for one in body["counts"])


def test_상태를_바꾼_요청만_접근_로그에_남는다(client: TestClient, admin: Signed) -> None:
    """조회까지 남기면 표가 의미 없는 행으로 차서 정작 찾을 것을 못 찾는다."""
    client.get("/api/auth/me", headers=admin.headers)
    client.get("/api/notifications/unread-count", headers=admin.headers)

    rows = client.get("/api/audit/access", headers=admin.headers).json()["items"]
    assert all(row["method"] != "GET" for row in rows)
    assert all(row["path"] != "/api/notifications/unread-count" for row in rows)
    # 로그인은 남는다 — 사용자 지원에 필요한 것이 그것이다.
    assert any(row["action"] == "LOGIN" for row in rows)


def test_주소_접두어는_빌드가_아니라_배포_설정이다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """여러 플랫폼이 한 호스트명에 경로로 붙는다(`/plm/`). 같은 이미지가 어느 접두어에서도
    떠야 하므로 접두어는 `.env` 에서 오고, 화면에는 `<base href>` · `<meta name="app-base">` 로
    심긴다. 리프레시 쿠키의 path 도 그 아래여야 로그인이 이어진다."""
    from app.main import create_app

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>x</title></head><body></body></html>",
        encoding="utf-8",
    )
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setenv("PUBLIC_PATH", "plm/")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.base_path == "/plm"
        prefixed = create_app()
        with TestClient(prefixed) as web:
            page = web.get("/o/anything").text
            assert '<base href="/plm/" />' in page
            assert '<meta name="app-base" content="/plm" />' in page
            # 문서 주소도 접두어 아래(root_path).
            assert web.get("/api/openapi.json").json()["servers"][0]["url"] == "/plm"
            # **접두어를 떼고 넘기든(nginx) 붙인 채 오든(직접 접속) 같다.** 정적 파일은
            # Starlette 가 붙어 있어야만 찾으므로, 떼고 온 쪽이 404 였다(실측).
            for prefix in ("", "/plm"):
                assert web.get(f"{prefix}/api/health").status_code == 200
                assert web.get(f"{prefix}/assets/app.js").text == "console.log(1)"
                assert '<base href="/plm/" />' in web.get(f"{prefix}/o/anything").text
        from app.modules.auth.routes import _cookie_path

        assert _cookie_path() == "/plm/api/auth"
        monkeypatch.setenv("PUBLIC_PATH", "")
        get_settings.cache_clear()
        assert get_settings().base_path == "" and _cookie_path() == "/api/auth"
    finally:
        get_settings.cache_clear()


def test_쿠키의_Secure_는_요청이_https_일_때만(client: TestClient, admin: Signed) -> None:
    """설정으로 늘 켜 두면 메인 서버 없이 IP(http)로 확인하는 동안 브라우저가 쿠키를 버려
    새로고침마다 로그인 화면이다(실측). 요청의 scheme 을 보고 붙인다."""
    from app.main import app as fastapi_app
    from tests.api.conftest import PASSWORD

    body = {"email": admin.email, "password": PASSWORD}
    plain = client.post("/api/auth/login", json=body).headers.get("set-cookie", "")
    assert "refresh" in plain and "Secure" not in plain
    with TestClient(fastapi_app, base_url="https://testserver") as https:
        secure = https.post("/api/auth/login", json=body).headers.get("set-cookie", "")
    assert "refresh" in secure and "Secure" in secure
