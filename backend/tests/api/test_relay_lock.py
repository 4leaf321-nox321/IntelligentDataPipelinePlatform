"""파이프라인마다 한 작업만(`relay/jobs.exclusive`) — 이중화의 두 워커가 같은 파이프라인의
실행 · 적용 · 회수를 함께 돌리면 `state.json` 을 서로 덮어썼다(2026-11-27 점검).

다른 서버의 워커 대신 시험이 그 파이프라인의 잠금을 따로 쥔다.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.relay import jobs as relay_jobs
from relay_core import reconcile, runner
from tests.api.conftest import Signed, work_until
from tests.api.test_relay import _definition, _slug
from tests.api.test_relay_reconcile import _baseline, _Submitter
from tests.unit.test_relay_reconcile import SAME, FakeReader


@contextmanager
def _held(slug: str) -> Iterator[None]:
    """다른 서버의 워커가 그 파이프라인의 작업을 돌리고 있다."""
    from app.database import engine

    with engine.connect() as conn:
        key = {"k": f"relay:{slug}"}
        assert conn.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), key)
        conn.commit()
        try:
            yield
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), key)
            conn.commit()


def _job(client: TestClient, admin: Signed, job_id: str) -> dict[str, object]:
    got: dict[str, object] = client.get(f"/api/jobs/{job_id}", headers=admin.headers).json()
    return got


def test_다른_워커가_쥔_파이프라인은_대조가_미루고_실행은_기다린다(
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
    monkeypatch.setattr(runner, "Submitter", _Submitter)
    monkeypatch.setattr(
        reconcile, "best_reader", lambda _target, _slugs: FakeReader({"plm_model": SAME}, {})
    )
    monkeypatch.setattr(reconcile, "Submitter", _Submitter)
    monkeypatch.setattr(relay_jobs, "LOCK_WAIT_SECONDS", 0)
    monkeypatch.setattr(relay_jobs, "LOCK_POLL_SECONDS", 0.01)
    base = f"/api/relay/pipelines/{slug}"

    with _held(slug):
        checked = client.post(f"{base}/reconcile", headers=admin.headers).json()["job_id"]
        work_until(db, uuid.UUID(checked))
        result = _job(client, admin, checked)["result"]
        assert isinstance(result, dict) and result["status"] == "deferred", result

        ran = client.post(f"{base}/run", headers=admin.headers).json()["job_id"]
        work_until(db, uuid.UUID(ran))
        job = _job(client, admin, ran)
        assert job["status"] == "failed", job
        assert "다른 작업이 0분 안에 끝나지 않아" in str(job["error"]), job["error"]
        assert not str(job["error"]).startswith("Busy")

    # 잠금이 풀리면 그대로 돈다.
    again = client.post(f"{base}/reconcile", headers=admin.headers).json()["job_id"]
    work_until(db, uuid.UUID(again))
    result = _job(client, admin, again)["result"]
    assert isinstance(result, dict) and result["status"] != "deferred", result


def test_잠금은_작업이_끝나면_풀린다() -> None:
    from app.database import engine

    slug = _slug()

    def waits(*_args: object) -> None:
        raise AssertionError("기다리지 않는다")

    work = SimpleNamespace(progress=waits)
    with relay_jobs.exclusive(work, slug) as held:  # type: ignore[arg-type]
        assert held
        with relay_jobs.exclusive(work, slug, wait=False) as again:  # type: ignore[arg-type]
            assert not again  # 같은 파이프라인은 하나만
    with engine.connect() as conn:
        free = conn.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"),
            {"k": f"relay:{slug}"},
        )
        conn.execute(text("SELECT pg_advisory_unlock_all()"))
        conn.commit()
    assert free
