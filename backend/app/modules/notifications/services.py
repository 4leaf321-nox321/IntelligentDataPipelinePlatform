"""알림을 만드는 **한 곳.**

도메인 코드가 Notification 을 직접 add 하지 않는다. 라우트마다 손으로 만들면
어떤 알림은 링크를 빼먹고, 그러면 사람은 그것을 보고 나서 무엇을 해야 할지
스스로 찾아야 한다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.notifications.models import Notification

#: 알림의 종류. 화면이 아이콘을 고르는 근거이자, **나중에 사람이 끄고 켤 단위**다.
#: 도메인이 자기 종류를 여기 옆에 더한다 — 자유 문자열로 두면 같은 뜻의 값이
#: 두 이름으로 갈리고, 그때 "이 알림 끄기" 를 만들 수가 없다.
ACCOUNT_APPROVED = "account.approved"
ACCOUNT_REJECTED = "account.rejected"
ACCOUNT_TOKEN_REVOKED = "account.token_revoked"
"""시스템 관리자가 내 액세스 토큰을 폐기했다. **연동 담당자는 그 토큰의 주인이다** — 알리지
않으면 연동이 401 을 받기 시작한 까닭을 토큰 · 서버 · 네트워크에서 차례로 찾는다."""
JOB_DONE = "job.done"
"""내가 시킨 작업이 끝났다. **짧게 끝난 것은 안 알린다** — 그때 사람은 아직 그 화면을 보고
있었고, 본 것을 한 번 더 말하면 그 종은 잡음이 된다."""
JOB_FAILED = "job.failed"
"""내가 시킨 작업이 실패했다. 같은 이유로 짧게 끝난 것은 안 알린다 — 그 오류는 화면에 떴다."""
RELAY_RUN_FAILED = "relay.run.failed"
"""파이프라인의 실행 · 적용이 실패하거나 멈췄다(`app.modules.relay.alerts`). **나빠질 때
한 번만** — 타이머가 밤마다 돌므로 매번 알리면 같은 알림이 날마다 쌓인다."""
RELAY_RUN_RECOVERED = "relay.run.recovered"
"""실패 · 멈춤 · 늦음 뒤 실행이 다시 된다."""
RELAY_RUN_LATE = "relay.run.late"
"""일정이 있는 파이프라인의 실행이 차례가 지나도록 끝나지 않는다 — 작업 워커가 멈췄거나 실행이
걸렸다(`relay.alerts.watch_late`, 일정 타이머가 본다). 끝날 때 알리는 길로는 아무 말도 없는
자리다."""
RELAY_DRIFT = "relay.drift"
"""대조에서 대상 SP 가 지난번 적용한 것과 어긋났거나, 대조를 하지 못했다. 어긋난 건수만 바뀐
것은 새 알림이 아니다."""
RELAY_DRIFT_RESOLVED = "relay.drift.resolved"
"""어긋남 · 대조 실패 뒤 대상이 다시 같다."""
RELAY_PLAN_WAITING = "relay.plan.waiting"
"""적용할 수 있는 계획이 새로 올라와 사람의 적용을 기다린다(자동 적용이 꺼졌거나 기준을 못
넘었다). 생길 때 한 번 — 같은 계획을 다음 실행이 다시 올려도 새 알림이 아니다."""


def notify(
    db: Session,
    *,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
) -> Notification:
    """알림 하나. **부르는 쪽이 커밋한다** — 그 변경과 같은 트랜잭션에 있어야
    "알림은 갔는데 변경은 롤백된" 상태가 안 생긴다."""
    row = Notification(user_id=user_id, kind=kind, title=title, body=body, link=link)
    db.add(row)
    return row


def notify_system_admins(
    db: Session,
    *,
    kind: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    skip: uuid.UUID | None = None,
) -> int:
    """설치 전체가 걸린 일(파이프라인이 멎거나 대상과 어긋난 것)을 **고칠 수 있는 사람들에게.**

    `skip` — 그 일을 한 사람. 화면에서 결과를 이미 봤다(내가 한 일은 나에게 안 온다).

    부서 관리자에게 보내지 않는 이유: 중계를 실행 · 적용 · 대조하는 것은 시스템 관리자만
    한다. 못 여는 사람에게 알리면 그 알림은 읽고 나서 할 일이 없는 알림이 되고,
    그런 것이 몇 번 오면 종 자체를 안 보게 된다.
    """
    admins = list(
        db.scalars(
            select(User.id).where(User.is_system_admin.is_(True), User.status == "active")
        )
    )
    sent = [one for one in admins if one != skip]
    for user_id in sent:
        notify(db, user_id=user_id, kind=kind, title=title, body=body, link=link)
    return len(sent)


def unread_count(db: Session, user_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
        or 0
    )
