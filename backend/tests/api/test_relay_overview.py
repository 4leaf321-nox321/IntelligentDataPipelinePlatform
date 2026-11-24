"""운영 요약 · 대상 상태 저장 · 묶음 알림 · 일일 요약 — 연결이 많아져도 화면과 알림이
읽히게(2026-11-24).

- 지도 · 목록은 대상 상태를 요청마다 조회하지 않고 저장값을 읽는다(`targets.py`).
- 요약(`/relay/overview`, MCP `relay_overview`)은 확인이 필요한 것만 싣는다.
- 같은 원천 시스템의 실행 실패가 이어지면 알림 1건으로 묶는다(`alerts.GROUP_AT`).
- 일일 요약은 확인 필요가 있을 때만 하루 한 번 간다(`digest.py`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.relay import alerts, digest, services, webhook
from relay_core import runner
from relay_core.runner import Outcome
from tests.api.conftest import Signed
from tests.api.test_relay import _definition, _slug
from tests.api.test_relay_alerts import _timer


def _titles(db: Session, email: str, needle: str) -> list[tuple[str, str]]:
    from app.modules.accounts.models import User
    from app.modules.notifications.models import Notification

    db.expire_all()
    user_id = db.scalar(select(User.id).where(User.email == email))
    rows = db.scalars(
        select(Notification)
        .where(Notification.user_id == user_id, Notification.title.contains(needle))
        .order_by(Notification.created_at)
    )
    return [(one.kind, one.title) for one in rows]


def test_지도는_대상_상태를_저장값으로_읽고_요약은_확인_필요만_싣는다(
    client: TestClient,
    registry: Path,
    admin: Signed,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    calls: list[str] = []

    def probe(server: str, token: str, path: str) -> tuple[int, Any]:
        calls.append(path)
        if path == "/api/health":
            return 200, {"slug": "hub", "version": "v0.4.101", "app": "Hub"}
        return 0, None

    monkeypatch.setattr(services, "probe_http", probe)
    first = client.get("/api/relay/map", headers=admin.headers).json()
    target = next(one for one in first["nodes"] if one["kind"] == "target")
    assert target["status"] == "ok" and target["detail"]["checked_at"]
    assert "/api/health" in calls

    calls.clear()
    client.get("/api/relay/map", headers=admin.headers)
    assert calls == []  # 저장값을 읽는다
    client.get("/api/relay/map?probe=true", headers=admin.headers)
    assert "/api/health" in calls  # [대상 다시 조회]

    pipeline = next(one for one in first["nodes"] if one["id"] == f"pipeline:{slug}")
    assert "last_job_at" in pipeline["detail"] and "last_error" in pipeline["detail"]

    summary = client.get("/api/relay/overview", headers=admin.headers).json()
    assert summary["total"] >= 1 and sum(summary["counts"].values()) == summary["total"]
    assert all(one["status"] != "ok" or one["reason"] for one in summary["attention"])
    hub = next(
        one for one in summary["targets"] if one["server"] == target["detail"]["server"]
    )
    assert (hub["status"], hub["version"]) == ("ok", "v0.4.101")


def _scripted_many(monkeypatch: pytest.MonkeyPatch, plans: dict[str, list[Any]]) -> None:
    """파이프라인마다 실행 결과를 차례로 — 예외면 작업이 실패한다."""

    def run(pipeline: Any, *_args: Any, **_kwargs: Any) -> Outcome:
        plan = plans.get(pipeline.slug)
        if not plan:
            return Outcome("nothing", "다른 시험의 작업")
        one = plan.pop(0)
        if isinstance(one, Exception):
            raise one
        assert isinstance(one, Outcome)
        return one

    monkeypatch.setattr(runner, "run", run)


def test_같은_원천_시스템의_실행_실패는_묶어서_한_번_알리고_모두_복구되면_한_번(
    registry: Path, db: Session, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    system = f"SYS{uuid.uuid4().hex[:6]}"
    slugs = [_slug() for _ in range(4)]
    for slug in slugs:
        _definition(registry, slug, system=system)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    down = RuntimeError("원천에 연결할 수 없습니다")
    ok = Outcome("nothing", "변경 사항이 없습니다")
    _scripted_many(monkeypatch, {slug: [down, ok] for slug in slugs})

    for slug in slugs:
        _timer(db, services.RUN_KIND, slug)
    # 처음 둘은 개별로, 셋째에 묶음 1건 — 넷째는 묶음이 맡는다.
    individual = [_titles(db, admin.email, f"{slug} 실행") for slug in slugs]
    assert individual == [
        [("relay.run.failed", f"{slugs[0]} 실행 실패")],
        [("relay.run.failed", f"{slugs[1]} 실행 실패")],
        [],
        [],
    ]
    assert _titles(db, admin.email, f"{system} 실행") == [
        ("relay.run.failed", f"{system} 실행 실패 — 파이프라인 3개")
    ]

    for slug in slugs[:3]:
        _timer(db, services.RUN_KIND, slug)
    assert _titles(db, admin.email, f"{system} 실행 복구") == []  # 아직 하나가 실패 중
    _timer(db, services.RUN_KIND, slugs[3])
    assert _titles(db, admin.email, f"{system} 실행 복구") == [
        ("relay.run.recovered", f"{system} 실행 복구 — 파이프라인 4개")
    ]
    # 앞의 둘(묶음 전에 개별로 알린 것)도 복구는 묶음이 대신 알렸다.
    assert [one for one in _titles(db, admin.email, "실행 복구") if system not in one[1]] == []
    assert alerts.GROUP_AT == 3


def test_일일_요약은_확인_필요가_있을_때만_하루_한_번(
    registry: Path, db: Session, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    monkeypatch.setattr(services, "probe_http", lambda *_args: (0, None))
    monkeypatch.setattr(get_settings(), "relay_digest_at", "08:30")
    sent: list[str] = []

    def send(**kwargs: Any) -> bool:
        sent.append(kwargs["title"])
        return True

    monkeypatch.setattr(webhook, "send", send)
    _scripted_many(monkeypatch, {slug: [RuntimeError("원천에 연결할 수 없습니다")]})
    _timer(db, services.RUN_KIND, slug)

    morning = datetime.now().astimezone().replace(hour=8, minute=0, second=0, microsecond=0)
    after = morning.replace(minute=40)
    count = digest.send_if_due(db, after)
    db.commit()
    assert count and count >= 1
    title = f"중계 일일 요약 — 확인 필요 {count}건"
    assert _titles(db, admin.email, "중계 일일 요약")[-1] == ("relay.digest", title)
    # 실행 실패 알림도, 일일 요약도 웹훅으로 함께 간다.
    assert sent == [f"{slug} 실행 실패", title]

    assert digest.send_if_due(db, after + timedelta(minutes=5)) is None  # 그날은 한 번
    tomorrow = morning + timedelta(days=1)
    assert digest.send_if_due(db, tomorrow) is None  # 08:30 전
    assert digest.send_if_due(db, tomorrow.replace(minute=35)) is not None
    db.commit()
