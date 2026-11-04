"""권한 판정 — 부서 스코프. **판정 지점이 하나여야 한다.**

가시성과 수정 권한을 각 쿼리에 흩뿌리면 "목록에는 보이는데 상세는 403" 같은
어긋남이 생기고, 그때 어느 쪽이 맞는지 알 방법이 없다.

시스템 역할과 부서 역할은 **다른 축**이다.
  - is_system_admin : 전사. 계정·부서 자체를 만들고 지운다
  - 부서 manager    : 그 부서 안에서만. 멤버와 그 부서 소유 자료를 관리한다

## 도메인 자료가 이것을 쓰는 방법

부서가 소유하는 표에 `owner_workspace_id`(nullable) 한 칸을 두면 아래 셋이 그대로
붙는다: `visible_owner_clause` · `resolve_owner_workspace` · `require_owner_edit`.
NULL 은 **전역**이다 — 여러 부서가 함께 쓰고, 고치는 것은 시스템 관리자뿐이다.
**보는 것은 기본이 부서와 상관없이 누구나**다(ADR 0034, `VIEW_SCOPE`) — 부서는 고치는
사람을 가른다.

각 모듈이 자기 판정을 따로 적으면 "A 는 되는데 B 는 안 되는" 식으로 어긋나고,
그 어긋남이 부서 관리자를 막다른 길로 보낸다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, or_, select, true
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.workspaces.models import Workspace, WorkspaceMember
from app.shared.errors import Forbidden, NotFound, code


def workspace_by_slug(db: Session, slug: str) -> Workspace:
    workspace = db.scalar(select(Workspace).where(Workspace.slug == slug))
    if workspace is None:
        raise NotFound(code("WORKSPACES", 1), f"부서를 찾을 수 없습니다: {slug}")
    return workspace


def membership_of(
    db: Session, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> WorkspaceMember | None:
    return db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )


def require_member(db: Session, *, workspace: Workspace, user: User) -> None:
    """그 부서를 볼 수 있는가. 시스템 관리자는 모든 부서를 본다."""
    if user.is_system_admin:
        return
    if membership_of(db, workspace_id=workspace.id, user_id=user.id) is None:
        raise Forbidden(code("WORKSPACES", 2), "이 부서에 접근할 권한이 없습니다.")


def require_manager(db: Session, *, workspace: Workspace, user: User) -> None:
    """그 부서의 멤버·자료를 바꿀 수 있는가."""
    if user.is_system_admin:
        return
    membership = membership_of(db, workspace_id=workspace.id, user_id=user.id)
    if membership is None or membership.role != "manager":
        raise Forbidden(code("WORKSPACES", 3), "부서 관리자만 할 수 있습니다.")


def my_workspace_ids(db: Session, user: User) -> list[uuid.UUID]:
    return list(
        db.scalars(
            select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
        )
    )


def is_any_manager(db: Session, user: User) -> bool:
    """어느 부서든 관리자인가. 화면이 만들기 버튼을 보일지 판단하는 근거다."""
    if user.is_system_admin:
        return True
    return (
        db.scalar(
            select(WorkspaceMember.id).where(
                WorkspaceMember.user_id == user.id, WorkspaceMember.role == "manager"
            )
        )
        is not None
    )


# --- 부서 소유 자산 ---------------------------------------------------------


def sees_every_department(user: User) -> bool:
    """이 사람이 **모든 부서의 자료를** 보나 — 시스템 관리자, 또는 조회가 전원에게 열린
    설치(`VIEW_SCOPE=all`, 기본 — ADR 0034)."""
    return user.is_system_admin or get_settings().view_scope == "all"


def visible_owner_clause(
    user: User, column: InstrumentedAttribute[uuid.UUID | None]
) -> ColumnElement[bool]:
    """볼 수 있는 것 — 기본은 **부서와 상관없이 전부**(ADR 0034).
    `VIEW_SCOPE=department` 면 전역 + 내 부서. 목록 · 조회 · 검색 · 그래프 · 지표 ·
    코어 API 가 모두 이것 하나를 부른다.

    같은 규칙을 두 번 적으면 언젠가 한쪽만 고쳐지고, 그때 검색 결과와 목록이 서로 다른 것을
    보여 준다. 고치는 것은 이것과 다른 축이다 — 늘 소유 부서의 관리자(`require_owner_edit`).

    예전에는 부서마다 「멤버만」 을 켜는 칸(`restricted`)이 있었지만 그 칸을 부르는 조회가
    하나도 없어 화면의 설정이 아무 데도 안 걸렸다(2026-10-22 발견, 칸은 지웠다).
    """
    if sees_every_department(user):
        return true()
    mine = select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
    return or_(column.is_(None), column.in_(mine))


def editable_owner_clause(
    user: User, column: InstrumentedAttribute[uuid.UUID | None]
) -> ColumnElement[bool]:
    """고칠 수 있는 것만 — `require_owner_edit` 를 질의로. 전역은 시스템 관리자만, 부서 것은
    그 부서의 관리자만. **보이는 것(`visible_owner_clause`)과 다른 축이다** — 여럿을 한 번에
    고치는 자리가 보이는 것으로 고르면, 고칠 수 없는 것까지 고친다."""
    if user.is_system_admin:
        return true()
    managed = select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == user.id, WorkspaceMember.role == "manager"
    )
    return column.in_(managed)


def resolve_owner_workspace(
    db: Session, user: User, slug: str | None, *, what: str, code_value: str
) -> uuid.UUID | None:
    """만들 때 누구 것으로 할지. None 이면 전역 — 시스템 관리자만."""
    if slug is None:
        if not user.is_system_admin:
            raise Forbidden(
                code_value,
                f"전역 {what}은 시스템 관리자만 만들 수 있습니다. 부서를 고르세요.",
            )
        return None
    workspace = workspace_by_slug(db, slug)
    require_manager(db, workspace=workspace, user=user)
    return workspace.id


def require_owner_edit(
    db: Session,
    user: User,
    owner_workspace_id: uuid.UUID | None,
    *,
    what: str,
    code_value: str,
) -> None:
    """고칠 수 있는가.

    전역은 **여러 부서가 함께 쓴다.** 한 부서가 고치면 다른 부서의 데이터가 다르게
    읽힌다 — 그래서 전역은 시스템 관리자만 손댄다.
    """
    if user.is_system_admin:
        return
    if owner_workspace_id is None:
        raise Forbidden(
            code_value,
            f"전역 {what}은 시스템 관리자만 고칠 수 있습니다. "
            f"여러 부서가 함께 쓰기 때문입니다.",
        )
    workspace = db.get(Workspace, owner_workspace_id)
    if workspace is None:
        raise NotFound(code_value, f"{what}의 소속 부서를 찾을 수 없습니다.")
    require_manager(db, workspace=workspace, user=user)


def can_edit_authored(user: User, author_id: uuid.UUID | None) -> bool:
    """**누구나 쓰는 자리**(게시판, ADR 0033)의 글 · 댓글을 수정 · 삭제할 수 있나 — 작성자와
    시스템 관리자. 부서 관리자는 아니다 — 게시판은 부서의 것이 아니라 설치 전체의 것이다."""
    return user.is_system_admin or (author_id is not None and author_id == user.id)


def require_authored_edit(
    user: User, author_id: uuid.UUID | None, *, what: str, code_value: str
) -> None:
    """`can_edit_authored` 가 아니면 거절한다 — 화면의 단추와 서버가 같은 판정을 쓴다."""
    if not can_edit_authored(user, author_id):
        raise Forbidden(
            code_value, f"{what}은(는) 작성자와 시스템 관리자만 수정 · 삭제할 수 있습니다."
        )
