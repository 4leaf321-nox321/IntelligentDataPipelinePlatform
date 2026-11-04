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
DATASOURCE_FAILED = "datasource.failed"
"""외부 소스에서 읽어 오는 일이 **처음으로** 실패했다. 타이머가 5분마다 도니까 매번
알리면 하루에 288개가 쌓이고, 그러면 사람은 이 종류를 통째로 안 읽게 된다."""
DATASOURCE_RECOVERED = "datasource.recovered"
"""다시 된다. **복구도 알린다** — 안 알리면 사람은 실패 알림 하나를 들고 「아직도
안 되나」 를 손으로 확인하러 간다."""
OBJECT_CHANGED = "object.changed"
"""지켜보는 객체가 바뀌었다. **내가 한 일은 나에게 안 온다** — 자기 행동을 돌려받으면
그 종은 곧 잡음이 되고, 잡음이 된 종은 진짜 하나가 울려도 안 읽힌다."""
JOB_DONE = "job.done"
"""내가 시킨 작업이 끝났다. **짧게 끝난 것은 안 알린다** — 그때 사람은 아직 그 화면을 보고
있었고, 본 것을 한 번 더 말하면 그 종은 잡음이 된다."""
JOB_FAILED = "job.failed"
"""내가 시킨 작업이 실패했다. 같은 이유로 짧게 끝난 것은 안 알린다 — 그 오류는 화면에 떴다."""
WEBHOOK_FAILED = "webhook.failed"
"""보내기를 세 번 다 실패해 포기했다. 받는 쪽이 조용히 못 받고 있는 상태다."""
METRIC_ALERT = "metric.alert"
"""내 경보에 새 결과가 나왔다(ADR 0016). **처음 보는 결론만** — 순차 검정의 「나쁨」 은 한 번
서면 계속 서 있어서, 그것을 계산마다 알리면 같은 알림이 날마다 온다."""
METRIC_ALERT_FAILED = "metric.alert.failed"
"""내 경보를 돌리지 못했다(지표 정의가 바뀌어 기준이 사라졌다 등). 처음 한 번만 — 다시 되면
조용히 돌아온다."""
BOARD_COMMENT = "board.comment"
"""내 게시글에 남이 댓글을 작성했다(ADR 0033). 내가 단 댓글은 안 알린다."""


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
) -> int:
    """설치 전체가 걸린 일(동기화·웹훅이 멎은 것)을 **고칠 수 있는 사람들에게.**

    부서 관리자에게 보내지 않는 이유: 데이터 소스와 웹훅 화면은 시스템 관리자만
    연다. 못 여는 사람에게 알리면 그 알림은 읽고 나서 할 일이 없는 알림이 되고,
    그런 것이 몇 번 오면 종 자체를 안 보게 된다.
    """
    admins = list(
        db.scalars(
            select(User.id).where(User.is_system_admin.is_(True), User.status == "active")
        )
    )
    for user_id in admins:
        notify(db, user_id=user_id, kind=kind, title=title, body=body, link=link)
    return len(admins)


def unread_count(db: Session, user_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
        or 0
    )
