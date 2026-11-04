"""운영 상태 — **서버가 자기 백업을 볼 수 있어야 한다.**

백업은 작업 스케줄러가 돌린다. 그 말은 **아무도 안 보면 조용히 죽는다**는 뜻이다:
스케줄러가 꺼졌거나, 경로가 사라졌거나, 자격 증명이 만료됐거나. 그리고 그 사실은
**복구가 필요한 날**에야 드러난다 — 그날은 이미 늦다.

그래서 앱이 백업 폴더를 들여다보고, 오래됐으면 홈의 「남은 일」 에 올린다.
관리 화면에 들어가야만 보이는 값은 아무도 안 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.modules.accounts.models import User
from app.shared import extensions

#: 이 시간을 넘으면 오래된 것으로 본다. 하루에 한 번 받는 설정에서 24시간으로 두면
#: 백업이 조금만 늦어도 매일 경고가 뜨고, 매일 뜨는 경고는 곧 안 읽힌다.
STALE_HOURS = 36


@dataclass(frozen=True)
class BackupStatus:
    configured: bool
    path: str | None
    last_at: datetime | None
    age_hours: float | None
    stale: bool
    problem: str | None
    """무엇이 잘못됐는지. **「백업이 없다」 와 「설정이 없다」 는 다른 일이다** —
    같게 말하면 사람은 안 해도 되는 일을 하러 간다."""


def backup_status(*, now: datetime | None = None) -> BackupStatus:
    """`BACKUP_DIR` 에서 가장 최근 `*.dump` 의 시각을 읽는다.

    **이름이 아니라 확장자로 본다.** 백업 스크립트가 `<root>/db/db-<시각>.dump` 로
    남기는데, 배치가 바뀌어도 덤프는 덤프다 — 이름 규칙에 묶어 두면 그 규칙을
    바꾼 날 이 검사가 조용히 아무것도 못 찾게 된다.
    """
    settings = get_settings()
    root = settings.backup_dir
    if root is None:
        return BackupStatus(
            configured=False,
            path=None,
            last_at=None,
            age_hours=None,
            # **설정이 없는 것도 오래된 것으로 본다.** 「모르겠다」 를 「괜찮다」 로
            # 적으면 백업을 한 번도 안 건 설치가 조용히 지나간다.
            stale=True,
            problem=(
                "BACKUP_DIR 설정이 없습니다 — .env 에 백업 폴더를 적으세요. "
                "예: BACKUP_DIR=/home/<계정>/backup/<slug>"
            ),
        )

    base = Path(root)
    if not base.is_dir():
        return BackupStatus(
            configured=True,
            path=str(base),
            last_at=None,
            age_hours=None,
            stale=True,
            problem="백업 폴더가 없습니다. 백업 스크립트가 한 번도 안 돌았습니다.",
        )

    newest: float | None = None
    for item in base.rglob("*.dump"):
        try:
            mtime = item.stat().st_mtime
        except OSError:
            # 권한이 없거나 그 사이에 지워졌다. 하나 때문에 전체를 포기하지 않는다.
            continue
        if newest is None or mtime > newest:
            newest = mtime

    if newest is None:
        return BackupStatus(
            configured=True,
            path=str(base),
            last_at=None,
            age_hours=None,
            stale=True,
            problem="백업 폴더에 덤프가 없습니다. 백업 스크립트가 한 번도 안 돌았습니다.",
        )

    at = datetime.fromtimestamp(newest, tz=UTC)
    age = ((now or datetime.now(UTC)) - at).total_seconds() / 3600
    stale = age > STALE_HOURS
    return BackupStatus(
        configured=True,
        path=str(base),
        last_at=at,
        age_hours=round(age, 1),
        stale=stale,
        problem=(
            f"마지막 백업이 {int(age)}시간 전입니다. 작업 스케줄러가 도는지 확인하세요."
            if stale
            else None
        ),
    )


def maintenance(db: object, viewer: User) -> list[extensions.MaintenanceItem]:
    """홈의 「남은 일」 에 백업을 올린다.

    **시스템 관리자에게만 보인다.** 처리할 수 없는 사람에게 띄우면 그저 못 지우는
    줄이 되고, 사람은 곧 그 자리를 아예 안 읽는다.

    `count` 는 1 이다 — 세는 것이 아니라 **하나 있느냐**가 물음이라서. 0 이면
    레지스트리가 걸러 준다(`extensions.maintenance_items`).
    """
    if not viewer.is_system_admin:
        return []
    status = backup_status()
    if not status.stale:
        return []
    return [
        extensions.MaintenanceItem(
            key="backup_stale",
            label=status.problem or "백업이 오래됐습니다",
            count=1,
            link="/admin/server",
            severity="warning",
        )
    ]
