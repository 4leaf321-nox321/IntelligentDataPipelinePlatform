"""첨부 — 올리고, 받고, 떼고, **못 볼 것은 못 본다.**"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from app.modules.files import services
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed


def _upload(
    client: TestClient,
    who: Signed,
    *,
    content: bytes = b"hello",
    name: str = "보고서.txt",
    workspace: str | None = None,
) -> Response:
    data = {"owner_table": "parts", "owner_id": "11111111-1111-1111-1111-111111111111"}
    if workspace is not None:
        data["workspace_slug"] = workspace
    response: Response = client.post(
        "/api/attachments",
        data=data,
        files={"file": (name, io.BytesIO(content), "text/plain")},
        headers=who.headers,
    )
    return response


def test_올리고_받으면_같은_내용이다(client: TestClient, admin: Signed) -> None:
    """**우리가 실제로 쓴 바이트를 센다** — 브라우저가 보낸 값을 믿지 않는다."""
    created = _upload(client, admin, content=b"hello world", workspace=admin.workspace)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["size_bytes"] == 11
    assert body["original_name"] == "보고서.txt"

    got = client.get(f"/api/attachments/{body['id']}/content", headers=admin.headers)
    assert got.status_code == 200, got.text
    assert got.content == b"hello world"
    # 한글 이름이 내려받기에 살아 있어야 한다.
    assert "filename*=UTF-8''" in got.headers["content-disposition"]


def test_같은_내용은_한_번만_저장된다(client: TestClient, admin: Signed) -> None:
    """저장 이름이 내용의 해시다. **행은 둘이고 파일은 하나다.**"""
    first = _upload(client, admin, content=b"same bytes", workspace=admin.workspace)
    second = _upload(
        client, admin, content=b"same bytes", name="다른이름.txt", workspace=admin.workspace
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["sha256"] == second.json()["sha256"]
    assert first.json()["id"] != second.json()["id"]


def test_빈_파일은_거절한다(client: TestClient, admin: Signed) -> None:
    response = _upload(client, admin, content=b"", workspace=admin.workspace)
    assert response.status_code == 400


def test_상한을_서버가_강제한다(
    client: TestClient, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**화면의 검사는 우회할 수 있다.** 우회되면 디스크가 차고, 디스크가 차면
    앱만이 아니라 DB 도 함께 멈춘다."""
    monkeypatch.setattr(services, "max_bytes", lambda: 8)
    response = _upload(client, admin, content=b"much too long", workspace=admin.workspace)
    assert response.status_code == 413
    assert response.json()["error"]["details"]["max_bytes"] == 8


def _asgi_post(chunks: int) -> tuple[int, int]:
    """앱을 ASGI 로 직접 불러 (응답 상태, **받아 간 본문 조각 수**) 를 낸다 — TestClient 는
    본문을 한 번에 넘겨서 「받기 전에 끊었나」 가 안 보인다. 조각은 1KB 씩."""
    import anyio
    from starlette.types import Message

    from app.main import app

    pulled = 0
    statuses: list[int] = []

    async def receive() -> Message:
        nonlocal pulled
        if pulled >= chunks:
            return {"type": "http.disconnect"}
        pulled += 1
        return {"type": "http.request", "body": b"x" * 1024, "more_body": pulled < chunks}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            statuses.append(int(message["status"]))

    path = "/api/attachments"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"multipart/form-data; boundary=xyz"),
            (b"content-length", str(chunks * 1024).encode()),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    anyio.run(app, scope, receive, send)
    return statuses[0], pulled


def test_밝힌_크기가_상한을_넘으면_받기_전에_끊는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """FastAPI 는 multipart 를 끝까지 받아 임시 파일로 푼 다음에야 엔드포인트를 부른다 — 거기서
    재면 상한을 넘는 파일도 다 받아 디스크에 쓰고 나서 거절한다. 머리(`Content-Length`)만 보고
    **한 조각도 안 받고** 끊는다. 인증보다 먼저다 — 누구의 것이든 그 본문은 받을 까닭이
    없다."""
    monkeypatch.setattr(services, "max_bytes", lambda: 1024)
    status, pulled = _asgi_post(chunks=200)  # 200KB > 1KB + 여유 64KB
    assert (status, pulled) == (413, 0)
    # 상한 + 여유 안이면 받는다 — 상한에 꼭 맞는 파일은 받아서 잰다(이 본문은 multipart 가
    # 아니라 받고 나서 400).
    status, pulled = _asgi_post(chunks=8)
    assert status != 413 and pulled > 0


def test_상한은_설치가_정하고_화면이_묻는다(
    client: TestClient, member: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = client.get("/api/attachments/limits", headers=member.headers)
    assert got.status_code == 200
    assert got.json() == {"max_bytes": 500 * 1024 * 1024}  # 기본 500MB
    monkeypatch.setattr(services, "max_bytes", lambda: 12345)
    assert client.get("/api/attachments/limits", headers=member.headers).json() == {
        "max_bytes": 12345
    }


def test_전역_첨부는_시스템_관리자만(client: TestClient, member: Signed) -> None:
    """전역은 여러 부서가 함께 본다 — 한 부서가 붙이면 다른 부서의 화면이 바뀐다."""
    response = _upload(client, member, workspace=None)
    assert response.status_code == 403


@pytest.mark.usefixtures("department_scope")
def test_남의_부서_첨부는_없는_것과_같다(
    client: TestClient, db: Session, admin: Signed, member: Signed
) -> None:
    """**403 으로 가르면 그 id 가 존재한다는 사실이 샌다.**

    시스템 관리자가 다른 부서에 붙인 첨부를, 그 부서가 아닌 멤버는 못 본다.
    """
    other = Workspace(slug="other-team", name="다른팀")
    db.add(other)
    db.commit()

    created = _upload(client, admin, workspace=other.slug)
    assert created.status_code == 201, created.text
    attachment_id = created.json()["id"]

    # member 는 other-team 소속이 아니다.
    assert (
        client.get(
            f"/api/attachments/{attachment_id}/content", headers=member.headers
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/attachments/{attachment_id}", headers=member.headers).status_code
        == 404
    )


def test_목록은_붙은_자료별로_나온다(client: TestClient, admin: Signed) -> None:
    _upload(client, admin, name="하나.txt", content=b"one", workspace=admin.workspace)
    rows = client.get(
        "/api/attachments",
        params={"owner_table": "parts", "owner_id": "11111111-1111-1111-1111-111111111111"},
        headers=admin.headers,
    )
    assert rows.status_code == 200, rows.text
    assert len(rows.json()) >= 1

    # 다른 자료에는 안 붙어 있다.
    empty = client.get(
        "/api/attachments",
        params={"owner_table": "parts", "owner_id": "22222222-2222-2222-2222-222222222222"},
        headers=admin.headers,
    )
    assert empty.json() == []


def test_떼면_목록에서_빠진다(client: TestClient, admin: Signed) -> None:
    """**행만 지운다 — 파일은 안 지운다.** 같은 내용을 다른 행이 가리킬 수 있다."""
    created = _upload(client, admin, content=b"to be removed", workspace=admin.workspace)
    attachment_id = created.json()["id"]

    assert (
        client.delete(f"/api/attachments/{attachment_id}", headers=admin.headers).status_code
        == 204
    )
    assert (
        client.get(
            f"/api/attachments/{attachment_id}/content", headers=admin.headers
        ).status_code
        == 404
    )


def test_부서_삭제_확인에_첨부가_뜬다(client: TestClient, admin: Signed) -> None:
    """**등록하지 않으면 안 뜬다.** 안 뜨면 사람은 아무것도 안 걸린 줄 알고
    지우려 하는데, FK 가 RESTRICT 라 서버가 500 을 낸다."""
    _upload(client, admin, content=b"blocks delete", workspace=admin.workspace)

    references = client.get(
        f"/api/workspaces/{admin.workspace}/references", headers=admin.headers
    )
    assert references.status_code == 200, references.text
    found = next(one for one in references.json() if one["table"] == "attachments")
    assert found["count"] >= 1
    assert found["blocks_delete"] is True


def test_서버_화면이_첨부_수를_센다(client: TestClient, admin: Signed) -> None:
    _upload(client, admin, content=b"counted", workspace=admin.workspace)
    counts = client.get("/api/server/status", headers=admin.headers).json()["counts"]
    assert any(one["label"] == "첨부" and one["count"] >= 1 for one in counts)
