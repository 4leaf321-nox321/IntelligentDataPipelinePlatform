"""접근 로그 보관 — 기간이 지난 것만 지우고, 감사 기록은 안 지운다(2026-10-10).

접근 로그는 지우는 주기가 없어 쌓이기만 했다. 기본 1년(`ACCESS_LOG_RETENTION_DAYS`), 0 이면
지우지 않는다. 감사 기록(변경 이력)은 객체 이력과 되돌리기가 기대므로 그대로 둔다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.audit.models import AccessLog, AuditEntry
from app.modules.audit.retention import purge_old_access_logs


def _log(db: Session, days_ago: int, tag: str) -> uuid.UUID:
    row = AccessLog(
        action="API",
        path=f"/api/retention/{tag}",
        method="POST",
        status_code=200,
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
    )
    db.add(row)
    db.flush()
    return row.id


def test_보관_기간이_지난_접근_로그만_지우고_감사_기록은_둔다(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    tag = uuid.uuid4().hex[:8]
    old = _log(db, 400, tag)
    recent = _log(db, 10, tag)
    audit = AuditEntry(
        action="object.update",
        actor_label="시스템",
        target_table="objects",
        target_label=f"보관 {tag}",
        changes={},
        created_at=datetime.now(UTC) - timedelta(days=400),
    )
    db.add(audit)
    db.commit()

    monkeypatch.setattr(get_settings(), "access_log_retention_days", 365)
    assert purge_old_access_logs(db) >= 1
    db.expire_all()
    left = set(db.scalars(select(AccessLog.id).where(AccessLog.path.like(f"%/{tag}"))))
    assert left == {recent}
    assert old not in left
    assert db.get(AuditEntry, audit.id) is not None  # 감사 기록은 그대로


def test_0_이면_지우지_않는다(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    tag = uuid.uuid4().hex[:8]
    old = _log(db, 4000, tag)
    db.commit()
    monkeypatch.setattr(get_settings(), "access_log_retention_days", 0)
    assert purge_old_access_logs(db) == 0
    assert db.get(AccessLog, old) is not None
