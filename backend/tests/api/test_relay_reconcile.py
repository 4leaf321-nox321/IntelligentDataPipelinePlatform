"""대조를 작업으로 — 시스템 관리자가 넣고(타이머도), 보고서가 상세 · 지도 · 홈 「남은 일」 에
나온다. 보정 계획을 적용하면 같은 작업이 다시 견줘 「어긋남」 이 바로 풀린다.

기준은 단위 시험의 가짜 원천으로 실제 한 바퀴를 돌려 만든다. 대상의 목록 · 미리보기 · 적용만
가짜다(`FakeReader` · `FakeSubmitter`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from relay_core import reconcile, runner
from relay_core.state import Workdir
from tests.api.conftest import Signed, work_until
from tests.api.test_relay import _definition, _slug
from tests.unit.test_relay_reconcile import SAME, FakeReader, _item
from tests.unit.test_relay_rest import ROWS, FakeSubmitter, _pipeline


class _Submitter(FakeSubmitter):
    def __init__(self, _target: Any = None) -> None:
        super().__init__()


def _baseline(tmp_path: Path, slug: str) -> Workdir:
    from app.config import get_settings

    work = Workdir(get_settings().relay_work_dir, slug)
    fake: Any = FakeSubmitter()
    pipeline = _pipeline(tmp_path, [dict(one) for one in ROWS])
    assert runner.run(pipeline, work, fake).status == "planned"
    assert runner.apply(pipeline, work, fake).status == "applied"
    return work


def _blocked_then_reconciled(slug: str) -> str:
    from app.modules.relay import services

    found = services.by_slug(slug)
    state = services.read_state(slug) or {}
    history = [*state.get("history", []), {"event": "blocked"}, {"event": "reconciled"}]
    return services.status_of(found, {**state, "history": history}, None)


def _job(client: TestClient, db: Session, admin: Signed, path: str, **body: Any) -> Any:
    queued = client.post(path, json=body or None, headers=admin.headers)
    assert queued.status_code == 202, queued.text
    work_until(db, uuid.UUID(queued.json()["job_id"]))
    return client.get(f"/api/jobs/{queued.json()['job_id']}", headers=admin.headers).json()


def test_대조는_관리자가_넣고_보고서가_상세_지도_홈에_나온다(
    client: TestClient,
    member: Signed,
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
    target = FakeReader({"plm_model": [*SAME[1:], _item("M9", "누가", "양산")]}, {})
    monkeypatch.setattr(reconcile, "TargetReader", lambda _target: target)
    monkeypatch.setattr(reconcile, "Submitter", _Submitter)

    path = f"/api/relay/pipelines/{slug}/reconcile"
    assert client.post(path, headers=member.headers).status_code == 403
    assert client.get(path, headers=member.headers).status_code == 404  # 아직 안 함

    job = _job(client, db, admin, path)
    assert job["kind"] == "relay_reconcile" and job["status"] == "done", job
    assert job["result"]["status"] == "drift" and job["result"]["drift"] == 2

    detail = client.get(f"/api/relay/pipelines/{slug}", headers=member.headers).json()
    report = detail["reconcile"]
    assert report["types"]["plm_model"]["missing"] == ["M1"]
    assert report["types"]["plm_model"]["extra"] == ["M9"]
    assert detail["reconcile_label"] == "7일마다 03:00"
    assert detail["jobs"][0]["kind"] == "relay_reconcile"
    assert detail["status"] == "ok"  # 대조 작업은 실행 상태를 바꾸지 않는다
    services_state = _blocked_then_reconciled(slug)
    assert services_state == "blocked"  # 멈춘 실행 뒤의 대조가 「멈춤」 을 가리지 않는다
    assert detail["history"][0]["event"] == "reconciled"
    full = client.get(path, headers=member.headers).json()
    assert full["drift"] == 2 and full["types"]["plm_model"]["counts"]["missing"] == 1

    nodes = client.get("/api/relay/map", headers=member.headers).json()["nodes"]
    (node,) = [one for one in nodes if one["id"] == f"pipeline:{slug}"]
    assert node["detail"]["drift"] == 2 and node["detail"]["reconciled_at"]
    assert node["detail"]["reconcile_status"] == "drift"
    home = client.get("/api/server/maintenance", headers=admin.headers).json()
    assert any(one["key"] == "relay_drift" and one["count"] >= 1 for one in home)


def test_보정_계획을_적용하면_다시_견줘_어긋남이_풀린다(
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
    work = _baseline(tmp_path, slug)
    target = FakeReader({"plm_model": SAME[1:]}, {"plm_model": "plm"})
    monkeypatch.setattr(reconcile, "TargetReader", lambda _target: target)
    monkeypatch.setattr(reconcile, "Submitter", _Submitter)
    monkeypatch.setattr(runner, "Submitter", _Submitter)

    planned = _job(client, db, admin, f"/api/relay/pipelines/{slug}/reconcile", plan=True)
    assert planned["result"]["status"] == "planned", planned
    pending = work.load_state().pending
    assert pending is not None and pending.origin == "reconcile"
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["status"] == "pending"

    # 적용은 화면에서 사람만 — 토큰(MCP)이 이 경로를 바로 불러도 거절한다(RELAY-0020)
    from tests.api.test_relay_recipes import _pat

    path = f"/api/relay/pipelines/{slug}/apply"
    by_token = client.post(path, headers=_pat(client, admin))
    assert by_token.status_code == 403, by_token.text
    assert by_token.json()["error"]["code"].endswith("RELAY-0020")
    assert work.load_state().pending is not None  # 그대로 기다린다

    target.items["plm_model"] = SAME  # 사람이 대상에서 적용했다
    applied = _job(client, db, admin, path)
    assert applied["result"]["status"] == "applied", applied
    assert "다시 대조: 대상과 같습니다" in applied["result"]["summary"]
    home = client.get("/api/server/maintenance", headers=admin.headers).json()
    assert not any(one["key"] == "relay_drift" for one in home)


def test_타이머는_적용_뒤_정한_시각에_대조를_한_번_넣는다(
    registry: Path, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings
    from app.modules.relay import services

    slug, fresh = _slug(), _slug()
    _definition(registry, slug)
    _definition(registry, fresh)
    work = _baseline(tmp_path, slug)
    state = work.load_state()
    state.applied_at = "2026-11-01T00:00:00Z"
    work.save_state(state)
    monkeypatch.setattr(get_settings(), "relay_reconcile_at", "03:00")

    before = datetime.fromisoformat("2026-11-01T02:00:00+00:00")
    assert f"{slug}(대조)" not in services.enqueue_due(db, before)  # 그날 03:00 전
    after = datetime.fromisoformat("2026-11-01T03:05:00+00:00")
    queued = services.enqueue_due(db, after)
    assert f"{slug}(대조)" in queued and f"{fresh}(대조)" not in queued  # 적용한 적 없음
    assert f"{slug}(대조)" not in services.enqueue_due(db, after)  # 이미 대기 중

    monkeypatch.setattr(get_settings(), "relay_reconcile_days", 0)
    assert not services.reconcile_due(db, slug, after)


def test_이중화의_두_타이머가_겹치면_한_대만_넣는다(registry: Path, db: Session) -> None:
    from sqlalchemy import text

    from app.database import engine
    from app.modules.relay import services

    slug = _slug()
    _definition(registry, slug, schedule={"every_minutes": 60})
    with engine.connect() as other:  # 다른 서버의 타이머가 잠금을 쥐고 있다
        other.execute(text("SELECT pg_advisory_lock(hashtext(:k))"), {"k": services.DUE_LOCK})
        assert services.enqueue_due(db) == []
        other.execute(
            text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": services.DUE_LOCK}
        )
    assert slug in services.enqueue_due(db)
