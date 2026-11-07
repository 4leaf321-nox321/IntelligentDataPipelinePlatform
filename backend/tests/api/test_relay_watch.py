"""무인 운영의 지킴 — 받기 점검의 「확인 후 진행」(화면에서 사람만), 늦음(일정 타이머가
알린다), 실행 추이(누구나 읽는다).

엔진의 규칙(무엇이 멈추나 · 늦음의 판정)은 `tests/unit/test_relay_intake.py` ·
`test_relay_schedule.py` 가 본다. 여기서는 그 결과가 경로 · 상태 · 알림으로 어떻게 나가나를
본다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.jobs.models import Job
from app.modules.relay import alerts, services
from relay_core.state import State, Workdir
from tests.api.conftest import Signed
from tests.api.test_relay import _definition, _slug
from tests.api.test_relay_alerts import _bell
from tests.api.test_relay_recipes import _pat

HELD: dict[str, Any] = {
    "at": "2026-11-07T01:00:00Z",
    "findings": {
        "before": 100,
        "deleted": 80,
        "received": 20,
        "blanks": [{"column": "KIND", "lost": 5, "filled": 100}],
    },
    "reasons": ["대량 삭제 — 미러 100건 중 80건(80.0%)이 이번 받기로 삭제됩니다."],
}


def _work(slug: str) -> Workdir:
    return Workdir(get_settings().relay_work_dir, slug)


def test_확인_후_진행은_멈춘_것이_있을_때_화면의_사람만(
    client: TestClient, admin: Signed, member: Signed, registry: Path, db: Session
) -> None:
    slug = _slug()
    _definition(registry, slug)
    run = f"/api/relay/pipelines/{slug}/run"

    nothing = client.post(run, json={"accept": True}, headers=admin.headers)
    assert nothing.status_code == 409, nothing.text
    assert nothing.json()["error"]["code"].endswith("RELAY-0024")

    _work(slug).save_state(State(held=HELD))
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=member.headers).json()
    assert (
        detail["status"] == "blocked" and detail["state"]["held"]["reasons"] == HELD["reasons"]
    )
    assert "빈 칸 3건 이상이면 멈춤" in detail["intake_label"]
    nodes = client.get("/api/relay/map", headers=member.headers).json()["nodes"]
    (pipeline,) = [one for one in nodes if one["kind"] == "pipeline"]
    assert pipeline["status"] == "blocked" and pipeline["detail"]["held"] == HELD["reasons"]

    # 대량 삭제를 지나가게 하는 일이다 — 토큰(MCP)으로는 거절한다(적용과 같다).
    token = client.post(run, json={"accept": True}, headers=_pat(client, admin))
    assert token.status_code == 403, token.text
    assert token.json()["error"]["code"].endswith("RELAY-0023")
    assert client.post(run, json={"accept": True}, headers=member.headers).status_code == 403

    queued = client.post(run, json={"accept": True}, headers=admin.headers)
    assert queued.status_code == 202, queued.text
    job = db.get(Job, uuid.UUID(queued.json()["job_id"]))
    assert job is not None
    # 지금 멈춘 그대로 싣는다 — 실행할 때 원천이 더 나빠졌으면 다시 멈춘다.
    assert job.params["accept"]["deleted"] == 80
    assert job.params["accept"]["columns"] == ["KIND"]
    assert job.params["accept"]["by"]
    # 일반 실행은 확인을 싣지 않는다.
    plain = client.post(run, headers=admin.headers)
    plain_job = db.get(Job, uuid.UUID(plain.json()["job_id"]))
    assert plain_job is not None and "accept" not in plain_job.params
    db.execute(
        update(Job).where(Job.id.in_([job.id, plain_job.id])).values(status="cancelled")
    )
    db.commit()


def test_실행_추이는_누구나_끝의_limit_줄을_읽는다(
    client: TestClient, member: Signed, registry: Path
) -> None:
    slug = _slug()
    _definition(registry, slug)
    trend = f"/api/relay/pipelines/{slug}/trend"
    empty = client.get(trend, headers=member.headers)
    assert empty.status_code == 200 and empty.json() == {"rows": [], "total": 0}
    assert not (get_settings().relay_work_dir / slug).exists()  # 읽기만 — 폴더를 안 만든다

    work = _work(slug)
    for n in range(5):
        work.add_trend(
            {
                "at": f"2026-11-0{n + 1}T01:00:00Z",
                "status": "planned",
                "seconds": n,
                "mirror": n,
            }
        )
    work.add_trend(
        {"at": "2026-11-06T01:00:00Z", "status": "failed", "seconds": 1, "stage": "받기"}
    )
    got = client.get(f"{trend}?limit=3", headers=member.headers).json()
    assert got["total"] == 6 and [one["status"] for one in got["rows"]] == [
        "planned",
        "planned",
        "failed",
    ]
    assert got["rows"][-1]["stage"] == "받기" and got["rows"][0]["mirror"] == 3
    assert client.get(f"{trend}?limit=0", headers=member.headers).status_code == 422
    assert (
        client.get("/api/relay/pipelines/none-such/trend", headers=member.headers).status_code
        == 404
    )


def test_늦음은_일정_타이머가_한_번_알리고_다시_끝나면_재개를_알린다(
    client: TestClient, admin: Signed, registry: Path, db: Session
) -> None:
    from app.modules.jobs import services as job_services

    slug = _slug()
    _definition(registry, slug, schedule={"every_minutes": 60})
    job = job_services.enqueue(
        db,
        kind=services.RUN_KIND,
        params={"slug": slug, "by": "timer"},
        user=None,
        workspace_id=None,
    )
    # 다섯 시간째 대기 — 작업 워커가 멈췄다.
    db.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(created_at=datetime.now(UTC) - timedelta(hours=5))
    )
    db.commit()

    assert slug in alerts.watch_late(db)
    db.commit()
    assert slug not in alerts.watch_late(db)  # 같은 늦음은 다시 안 알린다
    db.commit()
    assert _bell(db, admin.email, slug) == [("relay.run.late", f"{slug} 실행 늦음")]
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["status"] == "late" and "5시간째 끝나지 않았습니다" in detail["late"]
    home = client.get("/api/server/maintenance", headers=admin.headers).json()
    assert any(one["key"] == "relay_trouble" and one["count"] >= 1 for one in home)

    # 실행이 끝났다 — 재개를 알린다.
    db.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(status="done", created_at=datetime.now(UTC), finished_at=datetime.now(UTC))
    )
    db.commit()
    assert slug in alerts.watch_late(db)
    db.commit()
    assert _bell(db, admin.email, slug)[-1] == ("relay.run.recovered", f"{slug} 실행 재개")
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["status"] != "late" and detail["late"] == ""
