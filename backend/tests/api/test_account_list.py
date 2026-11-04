"""계정 목록은 **전체 수와 함께** 나온다 — 그래야 화면이 쪽을 넘긴다.

맨 리스트로 주던 때는 계정 화면이 처음 50명만 그리고 끝이었다(2026-10-08). 서버는
상한을 강제하는데 전체 수가 없으니 화면은 다음 쪽이 있는지 알 길이 없었고, 목록은
잘렸다는 말을 안 하므로 관리자는 그것이 전부라고 읽었다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed


def _signup(client: TestClient, workspace: Workspace) -> str:
    email = f"pending-{uuid.uuid4().hex[:8]}@example.local"
    made = client.post(
        "/api/accounts/signup",
        json={
            "email": email,
            "password": "pending-password",
            "display_name": "대기",
            "workspace_slug": workspace.slug,
        },
    )
    assert made.status_code == 201, made.text
    return email


def _page(client: TestClient, admin: Signed, query: str) -> dict[str, Any]:
    response = client.get(f"/api/accounts?{query}", headers=admin.headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_계정_목록은_전체_수와_쪽_위치를_준다(
    client: TestClient, admin: Signed, workspace: Workspace
) -> None:
    """**쪽마다 받은 줄을 다 모으면 total 과 같다.** 세는 거르기와 읽는 거르기가 갈리면
    마지막 쪽이 비거나 잘린다 — 시험 DB 는 함께 쓰므로 수를 못 박지 않고 둘을 맞춰 본다."""
    for _ in range(3):
        _signup(client, workspace)

    first = _page(client, admin, "limit=2&offset=0")
    assert set(first) == {"items", "total", "limit", "offset"}
    assert first["limit"] == 2 and first["offset"] == 0
    assert len(first["items"]) == 2
    assert first["total"] >= 4  # 관리자 하나 + 방금 신청한 셋

    seen: list[str] = []
    offset = 0
    while offset < first["total"]:
        page = _page(client, admin, f"limit=100&offset={offset}")
        assert page["total"] == first["total"]
        seen.extend(one["id"] for one in page["items"])
        offset += 100
    assert len(seen) == first["total"]
    assert len(set(seen)) == len(seen)


def test_상태로_거르면_전체_수도_그_상태만_센다(
    client: TestClient, admin: Signed, workspace: Workspace
) -> None:
    """알림의 링크(`/admin/accounts?status=pending`)가 여는 목록 — 수가 거르기 전 것이면
    「대기 120건 중 1-50」 처럼 보이고 다음 쪽이 비어 나온다."""
    mine = {_signup(client, workspace) for _ in range(2)}

    pending = _page(client, admin, "status=pending&limit=100")
    everyone = _page(client, admin, "limit=1")
    assert all(one["status"] == "pending" for one in pending["items"])
    assert mine <= {one["email"] for one in pending["items"]}
    assert 2 <= pending["total"] < everyone["total"]
