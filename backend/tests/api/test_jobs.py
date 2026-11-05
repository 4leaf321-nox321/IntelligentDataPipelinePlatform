"""작업 — **오래 걸리는 일은 요청이 아니라 표에 산다.**

여기서 보는 것은 작업 장치 자체다: 넣기 · 집기 · 계획 → 적용 · 두 번 적용 막기 · 취소 · 실패 ·
누가 보나. 틀(StandardPlatform)의 같은 시험은 객체 일괄 입력을 예로 들었다 — 그 종류는
온톨로지와 함께 걷어냈으므로(ADR 0001) **시험만의 종류**를 등록해 같은 장치를 본다. 파이프라인
실행의 종류는 `relay` 모듈의 시험이 따로 본다.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.jobs import kinds
from app.modules.jobs.models import Job
from app.modules.workspaces.models import Workspace
from tests.api.conftest import Signed, _login, _make_user, finish_job, work_until


def _echo(work: kinds.Work) -> dict[str, Any]:
    """계획이면 무엇을 할지 세기만, 적용이면 「했다」 — 두 단계 종류의 가장 작은 모양."""
    n = int(work.params.get("n") or 0)
    work.progress("세기", n, n)
    if not work.params.get("apply"):
        return {"applied": False, "ok": True, "summary": f"할 일 {n}건"}
    return {"applied": True, "ok": True, "summary": f"한 일 {n}건"}


def _boom(work: kinds.Work) -> dict[str, Any]:
    raise RuntimeError("시험이 일부러 낸 실패")


kinds.register(kinds.Kind("test_echo", "시험 — 세기", False, True, _echo, public=True))
kinds.register(kinds.Kind("test_boom", "시험 — 실패", False, False, _boom, public=True))
kinds.register(kinds.Kind("test_private", "시험 — 전용 경로", False, False, _echo))

_AFTER: list[tuple[str, str]] = []


def _after(db: Session, job: Job) -> None:
    _AFTER.append((job.kind, job.status))
    if (job.params or {}).get("break_after"):
        raise RuntimeError("뒤처리가 일부러 낸 실패")


kinds.register(
    kinds.Kind("test_after", "시험 — 뒤처리", False, False, _echo, public=True, after=_after)
)
kinds.register(
    kinds.Kind(
        "test_after_boom", "시험 — 실패 뒤", False, False, _boom, after=_after, public=True
    )
)


def _submit(
    client: TestClient, who: Signed, kind: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    got = client.post(
        "/api/jobs",
        data={"kind": kind, "params": json.dumps(params or {})},
        headers=who.headers,
    )
    assert got.status_code == 202, got.text
    return dict(got.json())


def test_계획을_세우고_사람이_적용한다(client: TestClient, admin: Signed) -> None:
    queued = _submit(client, admin, "test_echo", {"n": 3})
    assert queued["status"] == "queued"
    planned = finish_job(client, admin, queued)
    assert planned["status"] == "done", planned
    assert planned["result"]["applied"] is False
    assert planned["result"]["summary"] == "할 일 3건"

    applied = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    assert applied.status_code == 202, applied.text
    assert applied.json()["parent_id"] == planned["id"]
    done = finish_job(client, admin, applied.json())
    assert done["status"] == "done", done
    assert done["result"]["applied"] is True

    again = client.get(f"/api/jobs/{planned['id']}", headers=admin.headers)
    assert again.json()["applied_by"] == done["id"]


def test_같은_계획은_두_번_적용하지_않는다(client: TestClient, admin: Signed) -> None:
    planned = finish_job(client, admin, _submit(client, admin, "test_echo", {"n": 1}))
    first = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    assert first.status_code == 202, first.text
    second = client.post(f"/api/jobs/{planned['id']}/apply", headers=admin.headers)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"].endswith("JOBS-0022")


def test_넣을_때_적용을_바로_청하지_못한다(client: TestClient, admin: Signed) -> None:
    got = client.post(
        "/api/jobs",
        data={"kind": "test_echo", "params": json.dumps({"apply": True})},
        headers=admin.headers,
    )
    assert got.status_code == 422, got.text
    assert got.json()["error"]["code"].endswith("JOBS-0026")


def test_전용_경로의_종류는_일반_API_로_못_넣는다(client: TestClient, admin: Signed) -> None:
    got = client.post(
        "/api/jobs", data={"kind": "test_private", "params": "{}"}, headers=admin.headers
    )
    assert got.status_code == 403, got.text
    assert got.json()["error"]["code"].endswith("JOBS-0025")


def test_모르는_종류는_거절한다(client: TestClient, admin: Signed) -> None:
    got = client.post(
        "/api/jobs", data={"kind": "nope", "params": "{}"}, headers=admin.headers
    )
    assert got.status_code == 422, got.text


def test_실패하면_이유를_남긴다(client: TestClient, admin: Signed) -> None:
    failed = finish_job(client, admin, _submit(client, admin, "test_boom"))
    assert failed["status"] == "failed", failed
    assert failed["error"]


def test_끝난_뒤처리는_실패한_작업에도_돌고_그_오류는_결과를_바꾸지_않는다(
    client: TestClient, admin: Signed
) -> None:
    """`Kind.after` — 실패를 알리는 일(중계의 알림)이 가장 필요한 때가 실패한 작업이다. 본
    트랜잭션의 롤백에 쓸려 가지 않게 끝난 상태를 적은 뒤에 돈다."""
    _AFTER.clear()
    failed = finish_job(client, admin, _submit(client, admin, "test_after_boom"))
    assert failed["status"] == "failed", failed
    params = {"n": 1, "break_after": True}
    done = finish_job(client, admin, _submit(client, admin, "test_after", params))
    assert done["status"] == "done" and done["result"]["summary"] == "할 일 1건", done
    assert _AFTER == [("test_after_boom", "failed"), ("test_after", "done")]


def test_대기_중인_작업은_취소하면_바로_끝난다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    queued = _submit(client, admin, "test_echo", {"n": 1})
    got = client.post(f"/api/jobs/{queued['id']}/cancel", headers=admin.headers)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "cancelled"
    work_until(db, uuid.UUID(queued["id"]))
    status = db.scalar(select(Job.status).where(Job.id == uuid.UUID(queued["id"])))
    assert status == "cancelled"


def test_다른_부서의_작업은_안_보이고_취소도_못_한다(
    client: TestClient, db: Session, member: Signed, admin: Signed
) -> None:
    mine = _submit(client, member, "test_echo", {"n": 1})
    elsewhere = Workspace(slug=f"other-{uuid.uuid4().hex[:8]}", name="다른팀")
    db.add(elsewhere)
    db.commit()
    stranger_row = _make_user(
        db, elsewhere, label="stranger", is_system_admin=False, role="member"
    )
    stranger = Signed(
        email=stranger_row.email,
        token=_login(client, stranger_row.email),
        workspace=elsewhere.slug,
    )
    assert client.get(f"/api/jobs/{mine['id']}", headers=stranger.headers).status_code == 404
    refused = client.post(f"/api/jobs/{mine['id']}/cancel", headers=stranger.headers)
    assert refused.status_code == 404, refused.text
    # 시스템 관리자는 전부 본다 — 멎은 남의 작업을 치우는 사람이 있어야 한다.
    assert client.get(f"/api/jobs/{mine['id']}", headers=admin.headers).status_code == 200


def test_종류_목록에_등록한_것이_뜬다(client: TestClient, admin: Signed) -> None:
    got = client.get("/api/jobs/kinds", headers=admin.headers)
    assert got.status_code == 200, got.text
    names = {one["name"] for one in got.json()}
    assert {"filestore_gc", "workspace_reassign", "test_echo"} <= names
