"""모든 ORM 모델을 한 곳에서 import 한다.

Alembic autogenerate 는 Base.metadata 에 등록된 것만 본다. 모듈이 늘어날 때 여기에
한 줄을 더하지 않으면, 새 표가 안 보이는 정도가 아니라 **기존 표를 지우는
마이그레이션**이 생성된다. 그래서 모으는 지점을 하나로 고정한다.

DB 를 만지는 스크립트도 이 모듈을 import 한다 — 앱에서는 안 드러나고 배포 뒤
그 스크립트를 돌릴 때만 터진다.

`tests/architecture/test_boundaries.py` 가 빠진 것이 있는지 검사한다.
"""

from __future__ import annotations

from app.database import Base

# **확장의 표도 여기 적는다.** 빠뜨리면 autogenerate 가 기존 표를 지우는 마이그레이션을
# 만들고, 그 사실은 배포 뒤 마이그레이션에서만 터진다. 마이그레이션은 확장을 켰는지와
# 무관하게 늘 전부 돈다 — 안 켠 설치에서는 그 표가 비어 있을 뿐이다.
from app.modules.accounts.models import User
from app.modules.audit.models import AccessLog, AuditEntry, CutQuestion, RequestTiming
from app.modules.auth.models import PersonalAccessToken, PortalLaunchJti, RefreshToken
from app.modules.files.models import Attachment, AttachmentTicket
from app.modules.jobs.models import Job, JobFile, WorkerHeartbeat
from app.modules.notices.models import Notice, NoticeRead
from app.modules.notifications.models import Notification
from app.modules.server.models import ExtensionState, PlatformProfile
from app.modules.workspaces.models import Workspace, WorkspaceMember

__all__ = [
    "AccessLog",
    "Attachment",
    "AttachmentTicket",
    "AuditEntry",
    "Base",
    "CutQuestion",
    "ExtensionState",
    "Job",
    "JobFile",
    "Notice",
    "NoticeRead",
    "Notification",
    "PersonalAccessToken",
    "PlatformProfile",
    "PortalLaunchJti",
    "RefreshToken",
    "RequestTiming",
    "User",
    "WorkerHeartbeat",
    "Workspace",
    "WorkspaceMember",
]
