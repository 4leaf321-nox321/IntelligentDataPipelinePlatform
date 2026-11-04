"""부서(workspace)와 멤버십.

**조직 식별자를 내부 키로 감싼다.** URL 과 화면에 보이는 것은 slug/name 이지만,
다른 표는 전부 불변인 id 를 가리킨다. 부서 이름이 바뀌거나 조직이 개편돼도
데이터가 묶이지 않게 하려는 것이다 — 자료는 조직보다 오래 산다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

#: 부서 역할. 시스템 역할은 User.is_system_admin 이 갖는다 — 다른 축이다.
WORKSPACE_ROLES = ("member", "manager")


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    """URL 에 쓰는 이름. 바뀔 수 있으므로 참조 키로 쓰지 않는다.

    **64자다.** 사내 다른 시스템의 부서 슬러그와 주고받을 때 짧게 두면 자를지
    거절할지를 골라야 하는데, 자르면 키가 달라지고 거절하면 그 부서가 안 들어온다
    — 둘 다 나쁘다."""
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(
        String(255), default="", server_default="", nullable=False
    )
    """이 부서가 무엇을 하는 곳인가. 조직도만으로는 「재료2팀」 과 「재료3팀」 이
    무엇이 다른지 알 수 없다."""

    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    """상위 부서. **조직은 평면이 아니다** — 본부 아래 팀이 있고, 같은 이름의 팀이
    본부마다 있을 수 있다(품질팀이 둘). 평면 목록으로 두면 사람이 화면에서 그 둘을
    구분할 방법이 없다.

    RESTRICT 인 이유: 부서는 어차피 지우지 않고 보관한다. 그래도 CASCADE 로 두면
    언젠가 실수로 부모를 지웠을 때 하위 부서가 조용히 함께 사라진다."""

    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    """형제 사이의 순서. 조직도 순서는 이름순도 생성순도 아니다 — 사람이 정한다."""

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    """false 면 보관 상태. 자료는 남기고 새 활동만 막는다."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="member")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
