"""부서 트리 — 끌어 놓기가 부르는 「자리까지 정하는 옮기기」 와 자료 옮기기(통폐합)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.workspaces.models import Workspace, WorkspaceMember
from tests.api.conftest import Signed
from tests.api.test_workspace_reassign_job import reassign


def _make(client: TestClient, admin: Signed, slug: str, parent: str | None = None) -> str:
    made = client.post(
        "/api/workspaces",
        json={"slug": slug, "name": slug.upper(), "parent_slug": parent},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return slug


def _order(client: TestClient, admin: Signed, parent: str | None) -> list[str]:
    rows = client.get("/api/workspaces?all=true", headers=admin.headers).json()
    return [one["slug"] for one in rows if one["parent_slug"] == parent]


def test_자리를_주면_그_자리에_끼운다(client: TestClient, admin: Signed) -> None:
    tag = uuid.uuid4().hex[:6]
    root = _make(client, admin, f"r{tag}")
    for name in ("a", "b", "c"):
        _make(client, admin, f"{name}{tag}", root)
    assert _order(client, admin, root) == [f"a{tag}", f"b{tag}", f"c{tag}"]

    moved = client.post(
        f"/api/workspaces/c{tag}/move",
        json={"parent_slug": root, "position": 0},
        headers=admin.headers,
    )
    assert moved.status_code == 200, moved.text
    assert _order(client, admin, root) == [f"c{tag}", f"a{tag}", f"b{tag}"]

    # 자리를 안 주고 부모만 바꾸면 **끝에 붙는다** — 옛 순서를 들고 오지 않는다.
    _make(client, admin, f"d{tag}")
    client.post(
        f"/api/workspaces/d{tag}/move",
        json={"parent_slug": root},
        headers=admin.headers,
    )
    assert _order(client, admin, root)[-1] == f"d{tag}"


def test_자기_하위로는_못_옮긴다(client: TestClient, admin: Signed) -> None:
    tag = uuid.uuid4().hex[:6]
    top = _make(client, admin, f"t{tag}")
    child = _make(client, admin, f"k{tag}", top)
    denied = client.post(
        f"/api/workspaces/{top}/move",
        json={"parent_slug": child, "position": 0},
        headers=admin.headers,
    )
    assert denied.status_code == 400
    assert "하위 부서" in denied.json()["error"]["message"]


def test_가진_것을_다른_부서로_옮기고_비운다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    tag = uuid.uuid4().hex[:6]
    source = _make(client, admin, f"s{tag}")
    target = _make(client, admin, f"g{tag}")

    listed = client.get(f"/api/workspaces/{source}/contents", headers=admin.headers)
    assert listed.status_code == 200, listed.text
    kinds = {one["kind"]: one for one in listed.json()}
    # **0 건도 나온다** — 빠지면 사람은 그 종류가 안 옮겨지는 줄 안다.
    assert {"members", "attachments"} <= set(kinds)
    assert kinds["members"]["count"] == 1  # 만든 사람이 관리자로 들어간다

    done = reassign(client, admin, source, target, ["members"])
    assert done["moved"] == {"members": 1}

    after = {
        one["kind"]: one["count"]
        for one in client.get(
            f"/api/workspaces/{source}/contents", headers=admin.headers
        ).json()
    }
    assert after["members"] == 0
    rows = client.get("/api/workspaces?all=true", headers=admin.headers).json()
    assert next(one for one in rows if one["slug"] == target)["member_count"] == 1

    # 비었으니 지울 수 있다 — 통폐합의 끝.
    removed = client.delete(f"/api/workspaces/{source}", headers=admin.headers)
    assert removed.status_code == 204, removed.text
    assert db.query(Workspace).filter_by(slug=source).one_or_none() is None


def test_양쪽에_있는_사람은_짝이_안_겹치게(
    client: TestClient, admin: Signed, db: Session, workspace: Workspace
) -> None:
    """옮기다 유일 제약에 걸리면 통폐합 전체가 멈춘다 — 겹치는 사람은 원본만 지운다."""
    tag = uuid.uuid4().hex[:6]
    source = _make(client, admin, f"p{tag}")
    target = _make(client, admin, f"q{tag}")
    both = db.scalars(select(User).where(User.email == admin.email)).one()
    source_row = db.query(Workspace).filter_by(slug=source).one()
    target_row = db.query(Workspace).filter_by(slug=target).one()
    # 원본에서는 멤버, 대상에서는 이미 관리자.
    db.query(WorkspaceMember).filter_by(workspace_id=source_row.id, user_id=both.id).update(
        {"role": "member"}
    )
    db.commit()

    reassign(client, admin, source, target, ["members"])
    pairs = (
        db.query(WorkspaceMember).filter_by(workspace_id=target_row.id, user_id=both.id).all()
    )
    assert len(pairs) == 1
    # **높은 쪽이 남는다** — 통폐합 직후에 그 부서를 고칠 사람이 줄면 안 된다.
    assert pairs[0].role == "manager"
    assert db.query(WorkspaceMember).filter_by(workspace_id=source_row.id).count() == 0


def test_같은_부서로는_못_옮긴다(client: TestClient, admin: Signed) -> None:
    tag = uuid.uuid4().hex[:6]
    one = _make(client, admin, f"z{tag}")
    denied = client.post(
        f"/api/workspaces/{one}/reassign",
        json={"target_slug": one, "kinds": ["members"]},
        headers=admin.headers,
    )
    assert denied.status_code == 400
    other = _make(client, admin, f"y{tag}")
    unknown = client.post(
        f"/api/workspaces/{one}/reassign",
        json={"target_slug": other, "kinds": ["보고서"]},
        headers=admin.headers,
    )
    assert unknown.status_code == 422
    assert "아는 것" in unknown.json()["error"]["message"]


def test_시스템_관리자만_옮긴다(client: TestClient, member: Signed) -> None:
    denied = client.get("/api/workspaces/nope/contents", headers=member.headers)
    assert denied.status_code == 403
