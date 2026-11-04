"""부서 통폐합(자료 이동)은 **작업**이다 — 계획을 보고 적용한다(2026-10-10).

한 부서 객체 20만 개면 90초 남짓이라 요청 안에서 돌면 브라우저 · 이중화의 nginx 가 먼저 끊었다.
이제 `POST /api/workspaces/{slug}/reassign` 은 계획 작업을 넣고(202), 사람이 읽고 적용한다
(`POST /api/jobs/{id}/apply`).

여기서 보는 것: 계획의 수 · 막히는 것(아무것도 안 바뀐다), 적용이 예전 동기 경로와 같은지
(객체 · 객체마다의 기록 · 통폐합 한 줄 · 지운 객체도), 권한(전용 경로 · 일반 작업 API · 워커),
취소 · 빼앗김이면 아무것도 안 옮겨지는지, 지문, 도는 동안 원본을 못 지우는지. 코어 API 의
「안 보이게 됨」 은 `test_core_reassign.py`, 홈의 지표는 `test_metric_home.py` 가 같은 도우미로
본다.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditEntry
from app.modules.workspaces.models import Workspace, WorkspaceMember
from tests.api.conftest import Signed, finish_job


def reassign_plan(
    client: TestClient, admin: Signed, source: str, target: str, kinds: list[str]
) -> dict[str, Any]:
    """통폐합의 **계획**까지 — 넣고(202) 워커가 계획을 세운 작업."""
    started = client.post(
        f"/api/workspaces/{source}/reassign",
        json={"target_slug": target, "kinds": kinds},
        headers=admin.headers,
    )
    assert started.status_code == 202, started.text
    assert started.json()["kind"] == "workspace_reassign"
    planned = finish_job(client, admin, started.json())
    assert planned["status"] == "done", planned
    return planned


def reassign(
    client: TestClient, admin: Signed, source: str, target: str, kinds: list[str]
) -> dict[str, Any]:
    """통폐합을 **끝까지** — 계획을 세우고 적용한다. 돌려주는 것은 적용 작업의 결과 — 옮긴 수는
    `moved`(예전 동기 응답의 `{moved}` 와 같은 모양)."""
    planned = reassign_plan(client, admin, source, target, kinds)
    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    done = finish_job(client, admin, applied.json())
    assert done["status"] == "done", done
    return dict(done["result"])


def _space(client: TestClient, admin: Signed, db: Session, name: str) -> Workspace:
    made = client.post(
        "/api/workspaces",
        json={"slug": f"ws-{uuid.uuid4().hex[:8]}", "name": name},
        headers=admin.headers,
    )
    assert made.status_code == 201, made.text
    return db.scalars(select(Workspace).where(Workspace.slug == made.json()["slug"])).one()


def _object_records(db: Session, ids: list[str]) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(AuditEntry)
            .where(
                AuditEntry.target_table == "objects",
                AuditEntry.target_id.in_([uuid.UUID(one) for one in ids]),
                AuditEntry.changes.has_key("owner_workspace_id"),
            )
        )
        or 0
    )


def _reassigned_records(db: Session, source: Workspace) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(AuditEntry)
            .where(
                AuditEntry.action == "workspace.reassigned",
                AuditEntry.target_id == source.id,
            )
        )
        or 0
    )


def test_자료_이동이_끝나기_전에는_원본도_대상도_지우지_않는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """작업이 되면서 「이동」 뒤 곧장 「삭제」 를 누를 수 있게 됐다 — 대기 중인 작업이 멤버를
    옮기기 전에 원본을 지우면 멤버십이 부서와 함께 사라진다(막는 참조가 아니다)."""
    source = _space(client, admin, db, "원본")
    target = _space(client, admin, db, "대상")
    started = client.post(
        f"/api/workspaces/{source.slug}/reassign",
        json={"target_slug": target.slug, "kinds": ["members"]},
        headers=admin.headers,
    )
    assert started.status_code == 202, started.text
    for slug in (source.slug, target.slug):
        refused = client.delete(f"/api/workspaces/{slug}", headers=admin.headers)
        assert refused.status_code == 409 and "WORKSPACES-0017" in refused.text
        assert refused.json()["error"]["details"]["job_id"] == started.json()["id"]

    planned = finish_job(client, admin, started.json())
    assert planned["status"] == "done"
    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    refused = client.delete(f"/api/workspaces/{source.slug}", headers=admin.headers)
    assert refused.status_code == 409  # 적용이 아직 줄에 있다
    finish_job(client, admin, applied.json())
    removed = client.delete(f"/api/workspaces/{source.slug}", headers=admin.headers)
    assert removed.status_code == 204, removed.text
    db.expire_all()
    assert (
        db.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == target.id)
        )
        == 1
    )


def test_작업_화면의_종류_목록에_뜬다(client: TestClient, admin: Signed) -> None:
    kinds = {
        one["name"]: one for one in client.get("/api/jobs/kinds", headers=admin.headers).json()
    }
    found = kinds["workspace_reassign"]
    assert found["two_step"] is True and found["needs_file"] is False
    assert found["label"] == "부서 자료 이동" and found["max_file_bytes"] is None
