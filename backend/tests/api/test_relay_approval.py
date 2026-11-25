"""승인 위임(`relay/approval.py`, ADR 0002) — 화면에서 사람이 하던 결정을 사람이 대화에서
승인한 뒤 MCP 가 실행한다.

- `relay:approve` 범위가 없는 토큰은 지금처럼 거부한다(사람이 발급할 때 직접 선택한다).
- 범위가 있어도 승인 문구가 없으면 거부한다.
- 본 것과 실행하는 것이 다르면(계획 번호 · 중단 건수 · 남의 타입 계획) 거부한다.
- 승인한 실행은 감사 기록에 `relay.approve.*` 로, 작업 · 이력에 '(MCP 승인)' 으로 남는다.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from relay_core import retract, runner
from relay_core.state import Workdir
from tests.api.conftest import Signed, work_until
from tests.api.test_relay import _definition, _slug
from tests.api.test_relay_recipes import _draft, _mirror, _pat
from tests.api.test_relay_reconcile import _baseline, _Submitter
from tests.unit.test_relay_core import RECIPE


def _approver(client: TestClient, who: Signed) -> dict[str, str]:
    """사람이 화면에서 `relay:approve` 를 선택해 발급한 MCP 토큰."""
    made = client.post(
        "/api/auth/tokens",
        json={
            "name": f"mcp-approve-{uuid.uuid4().hex[:6]}",
            "scopes": ["read", "relay:write", "relay:approve"],
        },
        headers=who.headers,
    )
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}", "X-Client": "mcp"}


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _approvals(db: Session, action: str) -> list[dict[str, Any]]:
    from app.modules.audit.models import AuditEntry

    db.expire_all()
    rows = db.scalars(select(AuditEntry).where(AuditEntry.action == f"relay.approve.{action}"))
    return [dict(one.changes or {}) for one in rows]


def test_승인_범위가_있는_토큰만_사람의_승인으로_계획을_적용하고_재개한다(
    client: TestClient,
    admin: Signed,
    registry: Path,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    _baseline(tmp_path, slug)
    monkeypatch.setattr(retract, "Submitter", _Submitter)
    monkeypatch.setattr(runner, "Submitter", _Submitter)
    plain, approver = _pat(client, admin), _approver(client, admin)
    base = f"/api/relay/pipelines/{slug}"

    planned = client.post(
        f"{base}/retract", json={"keys": {"plm_model": ["M1"]}}, headers=plain
    )
    assert planned.status_code == 202, planned.text
    work_until(db, uuid.UUID(planned.json()["job_id"]))
    pending = client.get(base, headers=admin.headers).json()["state"]["pending"]["job_id"]

    refused = client.post(f"{base}/apply", json={"approval": "네"}, headers=plain)
    assert refused.status_code == 403 and _code(refused).endswith("RELAY-0020")
    assert "relay:approve" in refused.json()["error"]["message"]
    silent = client.post(f"{base}/apply", json={"plan_job_id": pending}, headers=approver)
    assert silent.status_code == 422 and _code(silent).endswith("RELAY-0047")
    other = client.post(
        f"{base}/apply", json={"plan_job_id": "nope", "approval": "네"}, headers=approver
    )
    assert other.status_code == 409 and _code(other).endswith("RELAY-0048")

    queued = client.post(
        f"{base}/apply",
        json={"plan_job_id": pending, "approval": "네, M1 회수 계획을 적용하세요"},
        headers=approver,
    )
    assert queued.status_code == 202, queued.text
    work_until(db, uuid.UUID(queued.json()["job_id"]))
    job = client.get(f"/api/jobs/{queued.json()['job_id']}", headers=admin.headers).json()
    assert job["result"]["status"] == "applied", job["result"]
    assert job["params"]["by"].endswith("(MCP 승인)")
    assert job["params"]["approval"] == "네, M1 회수 계획을 적용하세요"
    assert _approvals(db, "apply")[-1]["plan"] == pending

    assert client.post(f"{base}/resume", headers=plain).status_code == 403
    resumed = client.post(f"{base}/resume", json={"approval": "재개하세요"}, headers=approver)
    assert resumed.status_code == 200, resumed.text
    history = client.get(base, headers=admin.headers).json()["history"]
    assert history[0]["event"] == "resumed" and history[0]["by"].endswith("(MCP 승인)")
    assert _approvals(db, "resume")[-1]["approval"] == "재개하세요"

    # 사람의 화면 요청은 본문 없이도 그대로 된다.
    assert client.post(f"{base}/apply", headers=admin.headers).status_code == 202


def test_버전의_사용과_허용_주소도_승인으로(
    client: TestClient, admin: Signed, registry: Path, db: Session
) -> None:
    slug = _slug()
    _definition(registry, slug)
    approver = _approver(client, admin)
    assert _draft(client, approver, slug, RECIPE).status_code == 201
    _mirror(slug)
    path = f"/api/relay/pipelines/{slug}/recipes/1/activate"

    assert client.post(path, headers=approver).status_code == 422  # 승인 문구 없음
    used = client.post(path, json={"approval": "버전 1 사용하세요"}, headers=approver)
    assert used.status_code == 200, used.text
    assert used.json()["status"] == "active"
    assert _approvals(db, "recipe.activate")[-1]["version"] == 1

    hosts = client.put(
        "/api/relay/allowed-hosts",
        json={"target": ["hub.corp.example:8040"], "approval": "추가하세요"},
        headers=approver,
    )
    assert hosts.status_code == 200, hosts.text
    assert hosts.json()["target"]["screen"] == ["hub.corp.example:8040"]
    assert _approvals(db, "allowed_hosts")[-1]["hosts"]["target"]["added"] == [
        "hub.corp.example:8040"
    ]


def test_확인_후_진행은_보여_준_삭제_건수가_같을_때만(
    client: TestClient, admin: Signed, registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _slug()
    _definition(registry, slug)
    approver = _approver(client, admin)
    work = Workdir(get_settings().relay_work_dir, slug)
    state = work.load_state()
    state.held = {"findings": {"deleted": 40, "blanks": []}, "reasons": ["대량 삭제"]}
    work.save_state(state)
    path = f"/api/relay/pipelines/{slug}/run"

    differs = client.post(
        path, json={"accept": True, "accept_deleted": 12, "approval": "진행"}, headers=approver
    )
    assert differs.status_code == 409 and _code(differs).endswith("RELAY-0048")
    queued = client.post(
        path,
        json={"accept": True, "accept_deleted": 40, "approval": "40건 삭제 확인"},
        headers=approver,
    )
    assert queued.status_code == 202, queued.text


def test_타입_계획_적용은_이_파이프라인이_올린_계획만(
    client: TestClient,
    admin: Signed,
    registry: Path,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.relay import typeplan
    from relay_core import submit

    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    applied: list[str] = []

    def apply(self: Any, job_id: str) -> dict[str, Any]:
        applied.append(job_id)
        return {"ok": True}

    monkeypatch.setattr(submit.Submitter, "apply", apply)
    typeplan._remember(slug, "job-type-1", ["plm_demo_lot"])
    approver = _approver(client, admin)
    path = f"/api/relay/pipelines/{slug}/type-plan/apply"

    plain = client.post(path, json={"job_id": "job-type-1"}, headers=_pat(client, admin))
    assert plain.status_code == 403 and _code(plain).endswith("RELAY-0049")
    unknown = client.post(path, json={"job_id": "job-x", "approval": "네"}, headers=approver)
    assert unknown.status_code == 404 and _code(unknown).endswith("RELAY-0050")
    done = client.post(
        path,
        json={"job_id": "job-type-1", "approval": "타입 계획 적용하세요"},
        headers=approver,
    )
    assert done.status_code == 200, done.text
    assert applied == ["job-type-1"] and done.json()["types"] == ["plm_demo_lot"]
    assert _approvals(db, "type_plan")[-1]["job_id"] == "job-type-1"
