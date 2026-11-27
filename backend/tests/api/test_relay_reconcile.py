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
    monkeypatch.setattr(reconcile, "best_reader", lambda _target, _slugs: target)
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
    monkeypatch.setattr(reconcile, "best_reader", lambda _target, _slugs: target)
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
    assert "재대조: 대상과 일치합니다" in applied["result"]["summary"]
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
    assert not services.reconcile_due(db, services.by_slug(slug, db), after)


def _finish(db: Session, slug: str, at: str, **result: Any) -> None:
    """그 파이프라인의 대기 중인 작업을 끝난 것으로 — 넣은 때를 `at` 으로(타이머가 보는 때)."""
    from sqlalchemy import select

    from app.modules.jobs.models import Job

    for job in db.scalars(
        select(Job).where(Job.params["slug"].as_string() == slug, Job.status == "queued")
    ):
        job.status, job.result = "done", dict(result) or None
        job.created_at = datetime.fromisoformat(at)
    db.commit()


def test_대조는_정의의_요일에_실행_적용이_끝난_뒤에_넣고_미룬_것은_다시(
    registry: Path, db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.modules.jobs import services as job_services
    from app.modules.relay import services

    slug, off = _slug(), _slug()
    # 매일 04:00 에 실행하고, 대조는 일요일 04:00 — 같은 시각이면 실행이 먼저다.
    _definition(
        registry,
        slug,
        schedule={"daily_at": "04:00"},
        reconcile={"weekdays": ["sun"], "at": "04:00"},
    )
    _definition(registry, off, reconcile=False)
    for one in (slug, off):
        work = _baseline(tmp_path, one)
        state = work.load_state()
        state.applied_at = "2026-11-01T00:00:00+00:00"  # 일요일 0시에 첫 적용
        work.save_state(state)
    found = services.by_slug(slug, db)
    assert services.reconcile_label(found) == "매주 일요일 04:00"
    assert services.reconcile_label(services.by_slug(off, db)) == "수동"

    def tick(at: str) -> list[str]:
        return services.enqueue_due(db, datetime.fromisoformat(at))

    first = tick("2026-11-01T03:55:00+00:00")  # 첫 실행은 바로, 대조는 04:00 전이라 아직
    assert slug in first and f"{slug}(대조)" not in first
    _finish(db, slug, "2026-11-01T03:55:00+00:00", status="planned")
    queued = tick("2026-11-01T04:05:00+00:00")
    # 그 틱에 실행을 넣었다 — 대조는 실행(과 자동 적용)이 끝난 뒤로.
    assert slug in queued and f"{slug}(대조)" not in queued
    assert f"{off}(대조)" not in queued  # 정의가 대조를 껐다
    assert f"{slug}(대조)" not in tick("2026-11-01T04:10:00+00:00")  # 실행이 아직 대기 중
    _finish(db, slug, "2026-11-01T04:05:00+00:00", status="planned")
    assert f"{slug}(대조)" in tick("2026-11-01T04:15:00+00:00")  # 실행이 끝난 뒤의 첫 틱

    # 대상이 적용 중이라 미룬 대조는 세지 않는다 — 다음 틱에 다시 넣는다.
    _finish(db, slug, "2026-11-01T04:15:00+00:00", status=services.DEFERRED)
    assert services.last_reconciled(db, slug) is None
    assert f"{slug}(대조)" in tick("2026-11-01T04:20:00+00:00")
    _finish(db, slug, "2026-11-01T04:20:00+00:00", status="same")
    assert f"{slug}(대조)" not in tick("2026-11-02T04:25:00+00:00")  # 월요일 — 차례가 아니다
    _finish(db, slug, "2026-11-02T04:05:00+00:00", status="planned")
    assert f"{slug}(대조)" not in tick("2026-11-08T03:00:00+00:00")  # 다음 일요일 04:00 전
    _finish(db, slug, "2026-11-08T03:00:00+00:00", status="planned")
    queued = tick("2026-11-08T04:01:00+00:00")
    assert slug in queued and f"{slug}(대조)" not in queued  # 그날의 실행이 먼저
    _finish(db, slug, "2026-11-08T04:01:00+00:00", status="planned")
    queued = tick("2026-11-08T04:06:00+00:00")
    assert f"{slug}(대조)" in queued and slug not in queued
    # 사람이 넣은 실행이 대기 중이어도 넣지 않는다.
    _finish(db, slug, "2026-11-08T04:06:00+00:00", status="same")
    job_services.enqueue(
        db, kind=services.APPLY_KIND, params={"slug": slug}, user=None, workspace_id=None
    )
    db.commit()
    sunday = datetime.fromisoformat("2026-11-15T04:05:00+00:00")
    assert not services.reconcile_due(db, found, sunday)
    _finish(db, slug, "2026-11-15T04:00:00+00:00", status="applied")
    assert services.reconcile_due(db, found, sunday)


class _Applying(_Submitter):
    """대상 SP 가 기다리는 계획을 적용하고 있다."""

    def applying(self, job_id: str) -> bool | None:
        return True


def test_대상에서_적용하는_중이면_대조_작업이_미루고_알림_보고서를_건드리지_않는다(
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
    work = _baseline(tmp_path, slug)
    rows = [dict(one) for one in ROWS]
    rows[0] = {**rows[0], "name": "갤럭시 2", "updated_at": "2026-11-10T00:00:00Z"}
    fake: Any = FakeSubmitter()
    assert runner.run(_pipeline(tmp_path, rows), work, fake).status == "planned"
    target = FakeReader({"plm_model": SAME}, {})
    monkeypatch.setattr(reconcile, "best_reader", lambda _target, _slugs: target)
    monkeypatch.setattr(reconcile, "Submitter", _Applying)

    job = _job(client, db, admin, f"/api/relay/pipelines/{slug}/reconcile")
    assert job["status"] == "done" and job["result"]["status"] == "deferred", job
    assert "적용하고 있어 대조를 연기했습니다" in job["result"]["summary"]
    assert services.read_report(slug) is None  # 보고서 · 이력은 그대로
    assert services.last_reconciled(db, slug) is None  # 타이머가 다음 틱에 다시 넣는다
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["reconcile"] is None and detail["reconcile_label"] == "7일마다 03:00"

    planned = _job(client, db, admin, f"/api/relay/pipelines/{slug}/reconcile", plan=True)
    assert planned["result"]["status"] == "waiting", planned
    assert "적용하고 있습니다" in planned["result"]["summary"]


def test_같은_파이프라인의_실행이_돌면_대조가_미룬다(registry: Path, db: Session) -> None:
    from types import SimpleNamespace

    from app.modules.jobs import services as job_services
    from app.modules.relay import jobs, services

    slug = _slug()
    _definition(registry, slug)
    run = job_services.enqueue(
        db, kind=services.RUN_KIND, params={"slug": slug}, user=None, workspace_id=None
    )
    mine = job_services.enqueue(
        db, kind=services.RECONCILE_KIND, params={"slug": slug}, user=None, workspace_id=None
    )
    db.flush()
    work: Any = SimpleNamespace(db=db, job=mine)
    assert jobs._running_besides(work, slug) == ""  # 대기 중인 실행은 이 대조 뒤에 돈다
    run.status = "running"
    db.flush()
    assert jobs._running_besides(work, slug) == "중계 실행"
    run.status, mine.status = "done", "done"
    db.commit()


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
