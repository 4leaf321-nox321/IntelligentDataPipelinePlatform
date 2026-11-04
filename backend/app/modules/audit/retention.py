"""접근 로그 보관 — **오래된 것부터 나눠 지운다.** 감사 기록(변경 이력)은 지우지 않는다.

접근 로그(`access_logs`)는 「누가 어느 통로(화면 · 토큰)로 무엇을 불렀나」 를 찾는 보안
조사의 자리다. 지우는 주기가 없어 쌓이기만 했다(2026-10-10). 사내 감사는 대개 한 해를 보므로
기본 1년(`ACCESS_LOG_RETENTION_DAYS`, 0 이면 지우지 않는다). **감사 기록(`audit_entries`)은
그대로 둔다** — 객체 이력과 되돌리기가 거기에 기댄다.

워커가 한 시간마다 부른다(`app/worker.py` 의 정리). 한 번에 다 지우지 않고 덩어리로 — 큰 표의
긴 DELETE 하나는 그동안 그 표를 쓰는 요청을 붙잡고 복제를 밀리게 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.audit.models import AccessLog
from app.shared import extensions

#: 한 번에 지우는 줄 수와 한 차례의 덩어리 수 — 한 시간에 최대 100만 줄.
PURGE_BATCH = 50_000
PURGE_MAX_BATCHES = 20


def purge_old_access_logs(db: Session) -> int:
    """보관 기간이 지난 접근 로그를 지운다. 지운 줄 수."""
    days = get_settings().access_log_retention_days
    if days <= 0:
        return 0
    cut = datetime.now(UTC) - timedelta(days=days)
    gone = 0
    for _ in range(PURGE_MAX_BATCHES):
        chunk = (
            select(AccessLog.id)
            .where(AccessLog.created_at < cut)
            .limit(PURGE_BATCH)
            .scalar_subquery()
        )
        removed = extensions.rows_changed(
            db.execute(delete(AccessLog).where(AccessLog.id.in_(chunk)))
        )
        db.commit()
        gone += removed
        if removed < PURGE_BATCH:
            break
    return gone
