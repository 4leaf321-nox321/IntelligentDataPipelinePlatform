"""계정 화면에서 **소속을 바꾼다** — 통째로, 그리고 이름으로 보인다.

사람을 옮기는 일은 사람에서 시작한다. 부서 화면에서만 할 수 있으면 옛 부서를 찾아 빼고
새 부서를 찾아 넣어야 하고, 중간에 그만두면 두 부서에 걸친 계정이 남는다.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.modules.workspaces.models import Workspace, WorkspaceMember
from tests.api.conftest import Signed


def _room(db: Session, name: str, slug: str) -> Workspace:
    row = Workspace(slug=slug, name=name)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _account(client: TestClient, admin: Signed, email: str, workspace: str) -> dict[str, Any]:
    made = client.post(
        "/api/accounts",
        json={"email": email, "display_name": "홍길동", "workspace_slug": workspace},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return dict(made.json()["account"])


def _set(client: TestClient, admin: Signed, account_id: str, **body: Any) -> Any:
    return client.put(
        f"/api/accounts/{account_id}/workspaces", json=body, headers=admin.headers
    )


def test_소속은_이름과_경로로_나온다(
    client: TestClient, admin: Signed, db: Session, workspace: Workspace
) -> None:
    """**slug 만 주면 화면은 「hq」 라고 쓴다.** 사람은 자기 부서를 「본사」 로 안다."""
    one = _account(client, admin, "name-shown@test.local", workspace.slug)
    assert one["workspaces"][0]["slug"] == workspace.slug
    assert one["workspaces"][0]["name"] == workspace.name
    assert one["workspaces"][0]["is_home"] is True
    assert one["home_workspace_name"] == workspace.name


def test_소속을_통째로_바꾼다(
    client: TestClient, admin: Signed, db: Session, workspace: Workspace
) -> None:
    hq = _room(db, "본사", f"hq-{workspace.slug}")
    lab = _room(db, "재료시험팀", f"lab-{workspace.slug}")
    one = _account(client, admin, "moved@test.local", workspace.slug)

    moved = _set(client, admin, one["id"], workspace_slugs=[hq.slug, lab.slug])
    assert moved.status_code == 200, moved.text
    body = moved.json()
    # 옛 부서는 빠지고, 새 부서 둘만 남는다.
    assert sorted(body["memberships"]) == sorted([hq.slug, lab.slug])
    # 대표 소속이 빠졌으므로 첫 부서로 간다 — 비워 두면 로그인이 설 자리가 없다.
    assert body["home_workspace_slug"] == hq.slug
    assert [row["name"] for row in body["workspaces"] if row["is_home"]] == ["본사"]

    # 대표 소속만 따로 옮긴다.
    again = _set(
        client,
        admin,
        one["id"],
        workspace_slugs=[hq.slug, lab.slug],
        home_workspace_slug=lab.slug,
    ).json()
    assert again["home_workspace_slug"] == lab.slug


def test_남는_부서의_역할은_그대로다(
    client: TestClient, admin: Signed, db: Session, workspace: Workspace
) -> None:
    """소속 변경이 부서 관리자를 멤버로 떨어뜨리면, 그 사람은 다음 날 자기 부서를 못 고친다."""
    hq = _room(db, "본사", f"hq2-{workspace.slug}")
    one = _account(client, admin, "keeps-role@test.local", workspace.slug)
    db.execute(
        update(WorkspaceMember)
        .where(WorkspaceMember.user_id == one["id"])
        .values(role="manager")
    )
    db.commit()

    kept = _set(client, admin, one["id"], workspace_slugs=[workspace.slug, hq.slug]).json()
    roles = {row["slug"]: row["role"] for row in kept["workspaces"]}
    assert roles[workspace.slug] == "manager"
    assert roles[hq.slug] == "member"


def test_마지막_관리자는_빼지_않고_소속_아닌_부서는_대표가_못_된다(
    client: TestClient, admin: Signed, db: Session, workspace: Workspace
) -> None:
    solo = _room(db, "혼자본부", f"solo-{workspace.slug}")
    hq = _room(db, "본사", f"hq3-{workspace.slug}")
    one = _account(client, admin, "last-manager@test.local", solo.slug)
    db.execute(
        update(WorkspaceMember)
        .where(WorkspaceMember.user_id == one["id"])
        .values(role="manager")
    )
    db.commit()

    # **조용히 빼지 않는다** — 빼면 그 부서는 아무도 못 고치는 상태가 된다.
    refused = _set(client, admin, one["id"], workspace_slugs=[hq.slug])
    assert refused.status_code == 409, refused.text
    assert "마지막 관리자" in refused.json()["error"]["message"]

    wrong = _set(
        client, admin, one["id"], workspace_slugs=[solo.slug], home_workspace_slug=hq.slug
    )
    assert wrong.status_code == 409
    assert "소속이 아닙니다" in wrong.json()["error"]["message"]

    # 비우는 길은 없다 — 소속 없는 계정은 설 자리가 없다.
    empty = _set(client, admin, one["id"], workspace_slugs=[])
    assert empty.status_code == 422


def test_시스템_관리자만_바꾼다(
    client: TestClient, admin: Signed, member: Signed, db: Session, workspace: Workspace
) -> None:
    hq = _room(db, "본사", f"hq4-{workspace.slug}")
    one = _account(client, admin, "guarded@test.local", workspace.slug)
    denied = client.put(
        f"/api/accounts/{one['id']}/workspaces",
        json={"workspace_slugs": [hq.slug]},
        headers=member.headers,
    )
    assert denied.status_code == 403
