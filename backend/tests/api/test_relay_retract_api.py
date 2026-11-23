"""보낸 것 걷어내기를 작업으로 — AI(토큰)도 계획까지는 올리고, 적용 · 다시 켜기는 화면에서
사람이. 멈춘 동안 「지금 실행」 은 거절되고, 상세 · 지도가 「걷어내고 멈춤」 을 말한다.

기준은 단위 시험의 가짜 원천으로 실제 한 바퀴를 돌려 만든다(`test_relay_reconcile._baseline`).
대상의 미리 보기 · 적용 · 작업 묻기만 가짜다.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from relay_core import retract, runner
from tests.api.conftest import Signed, work_until
from tests.api.test_relay import _definition, _slug
from tests.api.test_relay_recipes import _pat
from tests.api.test_relay_reconcile import _baseline, _Submitter


def _code(response: Any) -> str:
    return str(response.json()["error"]["code"])


def _done(client: TestClient, db: Session, who: dict[str, str], queued: Any) -> Any:
    assert queued.status_code == 202, queued.text
    work_until(db, uuid.UUID(queued.json()["job_id"]))
    return client.get(f"/api/jobs/{queued.json()['job_id']}", headers=who).json()


def test_걷어내기는_AI도_계획까지_적용과_다시_켜기는_사람이(
    client: TestClient,
    member: Signed,
    admin: Signed,
    registry: Path,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.relay import services

    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    _baseline(tmp_path, slug)
    monkeypatch.setattr(retract, "Submitter", _Submitter)
    monkeypatch.setattr(runner, "Submitter", _Submitter)
    mcp = _pat(client, admin)
    base = f"/api/relay/pipelines/{slug}"

    assert (
        client.post(f"{base}/retract", json={"all": True}, headers=member.headers).status_code
        == 403
    )
    shapeless = client.post(f"{base}/retract", json={}, headers=mcp)
    assert shapeless.status_code == 422 and _code(shapeless).endswith("RELAY-0044")

    job = _done(
        client,
        db,
        admin.headers,
        client.post(
            f"{base}/retract",
            json={"keys": {"plm_model": ["M1"]}, "note": "시험으로 보낸 것"},
            headers=mcp,
        ),
    )
    assert job["kind"] == "relay_retract" and job["status"] == "done", job
    assert job["result"]["status"] == "planned", job["result"]
    assert job["result"]["counts"]["objects"]["plm_model"]["gone"] == 1

    detail = client.get(base, headers=member.headers).json()
    assert detail["status"] == "pending"  # 걷어내기 계획이 사람을 기다린다
    assert detail["state"]["stopped"]["by"].endswith("(MCP)")
    assert detail["state"]["pending"]["origin"] == "retract"
    assert detail["jobs"][0]["kind"] == "relay_retract"
    again = client.post(f"{base}/retract", json={"all": True}, headers=mcp)
    assert again.status_code == 409 and "이미 적용을 기다립니다" in again.text

    run = client.post(f"{base}/run", headers=admin.headers)
    assert run.status_code == 409 and _code(run).endswith("RELAY-0041")
    assert "다시 켜기" in run.json()["error"]["message"]
    by_token = client.post(f"{base}/resume", headers=mcp)
    assert by_token.status_code == 403 and _code(by_token).endswith("RELAY-0042")

    applied = _done(
        client, db, admin.headers, client.post(f"{base}/apply", headers=admin.headers)
    )
    assert applied["result"]["status"] == "applied", applied["result"]
    assert "걷어냈습니다" in applied["result"]["summary"]
    detail = client.get(base, headers=member.headers).json()
    assert (detail["status"], detail["status_label"]) == ("stopped", "걷어내고 멈춤")
    assert services.lateness(db, services.by_slug(slug, db)) == ""

    resumed = client.post(f"{base}/resume", headers=admin.headers)
    assert resumed.status_code == 200, resumed.text
    assert "다시 켰습니다" in resumed.json()["message"]
    detail = client.get(base, headers=member.headers).json()
    assert detail["status"] == "ok" and not detail["state"].get("stopped")
    assert [one["event"] for one in detail["history"][:1]] == ["resumed"]
    not_stopped = client.post(f"{base}/resume", headers=admin.headers)
    assert not_stopped.status_code == 409 and "멈춰 있지 않습니다" in not_stopped.text


def test_대기_중인_걷어내기는_다시_켜면_거두고_대상에_못_닿으면_켜지_않는다(
    client: TestClient,
    admin: Signed,
    registry: Path,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.relay import services

    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    _baseline(tmp_path, slug)
    monkeypatch.setattr(retract, "Submitter", _Submitter)
    base = f"/api/relay/pipelines/{slug}"
    _done(
        client,
        db,
        admin.headers,
        client.post(f"{base}/retract", json={"all": True}, headers=admin.headers),
    )

    answers: list[tuple[int, Any]] = [(0, None), (200, {"applied_by": None})]
    monkeypatch.setattr(services, "probe_http", lambda *_args: answers.pop(0))
    down = client.post(f"{base}/resume", headers=admin.headers)
    assert down.status_code == 409 and "닿지 않아" in down.text
    assert (services.read_state(slug) or {}).get("stopped")
    cancelled = client.post(f"{base}/resume", headers=admin.headers)
    assert cancelled.status_code == 200 and "적용하지 마세요" in cancelled.json()["message"]
    state = services.read_state(slug) or {}
    assert not state.get("stopped") and not state.get("pending")


def test_지우기는_대상_SP_판이_받을_때만(
    client: TestClient,
    admin: Signed,
    registry: Path,
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.relay import services

    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    _baseline(tmp_path, slug)
    made: list[_Submitter] = []

    class Seen(_Submitter):
        def __init__(self, target: Any = None) -> None:
            super().__init__(target)
            made.append(self)

    monkeypatch.setattr(retract, "Submitter", Seen)
    version = {"now": "v0.4.100"}

    def probe(server: str, token: str, path: str) -> Any:
        if path == "/api/health":
            return 200, {"status": "ok", "version": version["now"]}
        return 0, None

    monkeypatch.setattr(services, "probe_http", probe)
    base = f"/api/relay/pipelines/{slug}"
    detail = client.get(base, headers=admin.headers).json()
    assert (detail["target_version"], detail["target_deletes"]) == ("v0.4.100", False)
    wrong = client.post(
        f"{base}/retract",
        json={"previous": True, "action": "deprecate"},
        headers=admin.headers,
    )
    assert wrong.status_code == 422 and "delete" in wrong.text

    old = _done(
        client,
        db,
        admin.headers,
        client.post(
            f"{base}/retract",
            json={"keys": {"plm_model": ["M1"]}, "action": "delete"},
            headers=admin.headers,
        ),
    )
    assert old["result"]["status"] == "blocked" and "0.4.101" in old["result"]["summary"]
    assert not (services.read_state(slug) or {}).get("stopped")  # 아무것도 안 바뀌었다

    version["now"] = "v0.4.101"
    assert client.get(base, headers=admin.headers).json()["target_deletes"] is True
    job = _done(
        client,
        db,
        admin.headers,
        client.post(
            f"{base}/retract",
            json={"keys": {"plm_model": ["M1"]}, "action": "delete"},
            headers=admin.headers,
        ),
    )
    assert job["result"]["status"] == "planned", job["result"]
    sent = made[-1].previews[-1]
    graves = json.loads((sent / "tombstones.json").read_text(encoding="utf-8"))
    assert graves["objects"][0]["action"] == "delete"
    detail = client.get(base, headers=admin.headers).json()
    assert detail["retracted"] == 1 and detail["state"]["stopped"]["action"] == "delete"
