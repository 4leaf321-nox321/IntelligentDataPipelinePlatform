"""계정 로직 — 계정 자체의 생애(생성·승인·정지·삭제).

부서 단위 멤버 관리는 workspaces 모듈이 한다. 두 책임을 섞으면 화면도 섞인다.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import USER_STATUSES, User
from app.modules.accounts.schemas import AccountOut, AccountWorkspaceOut
from app.modules.auth import security
from app.modules.auth import services as auth_services
from app.modules.auth.models import PersonalAccessToken
from app.modules.auth.schemas import PatOut
from app.modules.notifications import services as notifications
from app.modules.workspaces import services as workspaces_services
from app.modules.workspaces.models import WORKSPACE_ROLES, Workspace, WorkspaceMember
from app.shared import audit, extensions, system_sources
from app.shared.errors import AppError, Conflict, NotFound, code
from app.shared.permissions import workspace_by_slug
from app.shared.text import clean


def _now() -> datetime:
    return datetime.now(UTC)


def account_out(db: Session, user: User) -> AccountOut:
    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.name)
    ).all()
    paths = workspaces_services.paths(db)
    home = db.get(Workspace, user.home_workspace_id) if user.home_workspace_id else None
    requested = (
        db.get(Workspace, user.requested_workspace_id) if user.requested_workspace_id else None
    )
    return AccountOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        is_system_admin=user.is_system_admin,
        must_change_password=user.must_change_password,
        home_workspace_slug=home.slug if home else None,
        home_workspace_name=home.name if home else None,
        requested_workspace_slug=requested.slug if requested else None,
        requested_workspace_name=requested.name if requested else None,
        memberships=[workspace.slug for workspace, _ in rows],
        workspaces=[
            AccountWorkspaceOut(
                slug=workspace.slug,
                name=workspace.name,
                path=paths.get(workspace.id, workspace.name),
                role=role,
                is_home=workspace.id == user.home_workspace_id,
            )
            for workspace, role in rows
        ],
        created_at=user.created_at,
        decided_at=user.decided_at,
        decision_note=user.decision_note,
    )


def active_system_admin_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.is_system_admin.is_(True),
                User.status == "active",
                User.deleted_at.is_(None),
            )
        )
        or 0
    )


def pending_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.status == "pending", User.deleted_at.is_(None))
        )
        or 0
    )


def get_account(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound(code("ACCOUNTS", 1), "계정을 찾을 수 없습니다.")
    return user


def _check_role(role: str) -> None:
    """부서 역할은 정해진 것만. **무엇이든 받으면** `owner` 같은 글자가 그대로 저장된다 —
    권한 판정은 `manager` 만 보므로 그 사람은 조용히 멤버로 돌고, 관리자로 올렸다고 믿은
    사람은 부서 화면 어디에서도 그 까닭을 못 본다(2026-10-08). 무엇을 만들기 전에 본다."""
    if role not in WORKSPACE_ROLES:
        raise AppError(
            code("ACCOUNTS", 10),
            f"모르는 역할입니다: {role} ({' · '.join(WORKSPACE_ROLES)} 중 하나)",
            status=400,
            details={"allowed": list(WORKSPACE_ROLES)},
        )


def _listed(status: str | None) -> list[Any]:
    """목록과 그 수가 **같은 거르기**를 쓴다 — 갈리면 「120명 중 1-50」 이 실제 쪽과 안 맞는다.
    지운 계정은 빼고, 상태를 주면 그것만."""
    where: list[Any] = [User.deleted_at.is_(None)]
    if status is not None:
        where.append(User.status == status)
    return where


def count_accounts(db: Session, *, status: str | None) -> int:
    return int(db.scalar(select(func.count()).select_from(User).where(*_listed(status))) or 0)


def list_accounts(db: Session, *, status: str | None, limit: int, offset: int) -> list[User]:
    query = select(User).where(*_listed(status))
    # 승인 대기가 먼저 온다. **관리자가 할 일이 목록 맨 위에 있어야 한다** —
    # 이름순으로 두면 대기 하나를 찾으려고 세 쪽을 넘겨야 한다.
    return list(
        db.scalars(
            query.order_by((User.status != "pending"), User.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    )


def signup(
    db: Session, *, email: str, password: str, display_name: str, workspace_slug: str
) -> User:
    """가입 신청. 승인 전까지는 로그인할 수 없다(status=pending)."""
    normalized = auth_services.normalize_login_id(email)
    if db.scalar(select(User).where(User.email == normalized)) is not None:
        raise Conflict(code("ACCOUNTS", 2), "이미 있는 아이디입니다.")

    workspace = workspace_by_slug(db, workspace_slug)
    user = User(
        email=normalized,
        password_hash=security.hash_password(password),
        display_name=clean(display_name),
        status="pending",
        requested_workspace_id=workspace.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def request_from_portal(db: Session, *, login_id: str, display_name: str) -> User:
    """**포털로 처음 들어온 사람**의 가입 신청 — 승인 대기로 만든다(ADR 0028). 커밋하지 않는다
    (부르는 쪽이 감사 기록과 한 트랜잭션으로).

    화면의 가입 신청(`signup`)과 같은 줄이 된다 — 상태 `pending` 이라 관리자에게는 홈 「남은
    일」 의 「가입 승인 대기」 로 같은 길로 가고, 승인 · 거절도 같은 화면에서 한다. 다른 것은
    셋:

    - **희망 부서가 없다.** 포털은 우리 조직도를 모르고(토큰의 `groups` 는 포털 쪽 묶음이다),
      짐작으로 고른 부서는 사람 눈에 맞는 값처럼 보여 아무도 안 고친다 — 승인할 때 관리자가
      고른다(계정 화면이 그때 부서를 묻는다).
    - **비밀번호가 없다**(`security.unusable_password`) — 그 사람은 포털로 들어온다. 아이디 ·
      비밀번호로도 들어와야 하면 승인 뒤 「비밀번호 초기화」.
    - 아이디 · 이름은 포털이 보증한 값(토큰의 `email` · `name`)이다.
    """
    user = User(
        email=auth_services.normalize_login_id(login_id),
        password_hash=security.unusable_password(),
        display_name=clean(display_name)[:100] or login_id[:100],
        status="pending",
        requested_workspace_id=None,
    )
    db.add(user)
    db.flush()
    return user


def create_account(
    db: Session,
    *,
    email: str,
    display_name: str,
    workspace_slug: str,
    role: str,
    is_system_admin: bool,
    created_by: User,
) -> tuple[User, str]:
    """관리자가 계정을 만든다. 임시 비밀번호를 함께 돌려준다."""
    _check_role(role)
    normalized = auth_services.normalize_login_id(email)
    if db.scalar(select(User).where(User.email == normalized)) is not None:
        raise Conflict(code("ACCOUNTS", 2), "이미 있는 아이디입니다.")

    workspace = workspace_by_slug(db, workspace_slug)
    temporary = secrets.token_urlsafe(9)
    user = User(
        email=normalized,
        password_hash=security.hash_password(temporary),
        display_name=clean(display_name),
        status="active",
        is_system_admin=is_system_admin,
        home_workspace_id=workspace.id,
        # **첫 로그인 때 바꾸게 한다.** 임시 비밀번호가 그대로 남는 것이 가장
        # 흔한 사고이고, 그 사고는 계정을 만든 사람이 아니라 몇 달 뒤에 드러난다.
        must_change_password=True,
        decided_at=_now(),
        decided_by_id=created_by.id,
    )
    db.add(user)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role))
    # **만든 것도 기록한다.** 승인 · 정지 · 권한 변경은 남는데 직접 만든 계정만 안 남았다 —
    # 만들면서 시스템 관리자를 켜면 그 권한이 흔적 없이 생겼다(2026-10-08). 권한은 계정
    # 화면에서 켠 것과 같은 항목으로.
    audit.record(
        db,
        action=audit.ACCOUNT_CREATED,
        actor=created_by,
        target_table="users",
        target_id=user.id,
        target_label=user.email,
        workspace_id=workspace.id,
        changes={"status": {"before": None, "after": "active"}, "role": role},
    )
    if is_system_admin:
        audit.record(
            db,
            action=audit.ACCOUNT_ADMIN_CHANGED,
            actor=created_by,
            target_table="users",
            target_id=user.id,
            target_label=user.email,
            changes={"is_system_admin": {"before": False, "after": True}},
        )
    db.commit()
    db.refresh(user)
    return user, temporary


def approve(
    db: Session,
    *,
    user_id: uuid.UUID,
    decided_by: User,
    workspace_slug: str | None,
    role: str,
) -> User:
    _check_role(role)
    user = get_account(db, user_id)
    if user.status != "pending":
        raise Conflict(code("ACCOUNTS", 3), "승인 대기 중인 계정이 아닙니다.")

    slug = workspace_slug
    if slug is None:
        requested = (
            db.get(Workspace, user.requested_workspace_id)
            if user.requested_workspace_id
            else None
        )
        if requested is None:
            raise AppError(
                code("ACCOUNTS", 4),
                "신청한 부서가 없어졌습니다. 배정할 부서를 골라 주세요.",
                status=400,
            )
        slug = requested.slug

    workspace = workspace_by_slug(db, slug)
    user.status = "active"
    user.decided_at = _now()
    user.decided_by_id = decided_by.id
    # **대표 소속을 여기서 정한다.** 승인은 "어느 부서 사람인가" 를 정하는 자리이고,
    # 비워 두면 로그인이 이름순 첫 부서로 떨어진다 — 사람이 정한 값이 아니다.
    user.home_workspace_id = workspace.id

    if (
        db.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == user.id,
            )
        )
        is None
    ):
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role))

    # **본인에게 알린다.** 메일이 없으므로 승인됐다는 사실을 알 방법이 이것뿐이다
    # — 안 보내면 신청한 사람은 매일 로그인을 시도해 본다.
    notifications.notify(
        db,
        user_id=user.id,
        kind=notifications.ACCOUNT_APPROVED,
        title="가입이 승인되었습니다",
        body=f"{workspace.name} 소속으로 등록되었습니다.",
        link=f"/w/{workspace.slug}",
    )

    audit.record(
        db,
        action=audit.ACCOUNT_DECIDED,
        actor=decided_by,
        target_table="users",
        target_id=user.id,
        target_label=user.email,
        workspace_id=workspace.id,
        changes={"status": {"before": "pending", "after": "active"}},
    )
    db.commit()
    db.refresh(user)
    return user


def reject(db: Session, *, user_id: uuid.UUID, decided_by: User, note: str) -> User:
    user = get_account(db, user_id)
    if user.status != "pending":
        raise Conflict(code("ACCOUNTS", 3), "승인 대기 중인 계정이 아닙니다.")
    user.status = "suspended"
    user.decided_at = _now()
    user.decided_by_id = decided_by.id
    # **사유를 반드시 남긴다.** 메일이 없어 통보가 앱 안에서만 되므로, 여기 안
    # 적히면 신청한 사람은 왜 거절됐는지 영영 모른다.
    user.decision_note = note
    audit.record(
        db,
        action=audit.ACCOUNT_DECIDED,
        actor=decided_by,
        target_table="users",
        target_id=user.id,
        target_label=user.email,
        changes={"status": {"before": "pending", "after": "suspended"}},
        reason=note,
    )
    db.commit()
    db.refresh(user)
    return user


def _guard_last_admin(db: Session, user: User, *, what: str) -> None:
    """마지막 활성 시스템 관리자를 잃는 길을 막는다.

    잃으면 복구 경로가 **서버 콘솔뿐**이다. 그 상태는 실제로 일어나고, 일어난
    뒤에는 화면에서 할 수 있는 것이 하나도 없다.
    """
    if user.is_system_admin and user.status == "active" and active_system_admin_count(db) <= 1:
        raise Conflict(
            code("ACCOUNTS", 5),
            f"마지막 활성 시스템 관리자입니다. 다른 사람을 지정한 뒤에 {what}세요.",
        )


def set_status(db: Session, *, user_id: uuid.UUID, status: str, actor: User) -> User:
    if status not in USER_STATUSES:
        raise AppError(code("ACCOUNTS", 6), f"모르는 상태입니다: {status}", status=400)
    user = get_account(db, user_id)
    if status != "active":
        _guard_last_admin(db, user, what="정지하")

    before = user.status
    user.status = status
    changes: dict[str, Any] = {"status": {"before": before, "after": status}}
    if status != "active":
        # **정지하면 열린 로그인(refresh)을 폐기한다.** 막기만 하면 정지 동안은 거절되지만,
        # 다시 켜는 순간 정지 전의 로그인(30일)이 그대로 이어졌다 — 정지한 까닭이 「그 기기가
        # 샜다」 면 다시 켠 날 그 기기가 돌아온다(2026-10-08). 다시 켠 사람은 새로 로그인한다.
        #
        # 개인 토큰(PAT)은 **폐기하지 않는다** — 정지 동안은 `resolve_pat` 이 막고
        # (can_sign_in), 다시 켜면 산다. 정지는 잠시 막는 것이고, 연동은 사람처럼 그
        # 자리에서 다시 로그인하지 못한다 — 폐기하면 다시 켠 뒤에도 연동이 말없이 끊긴
        # 채로 남는다(초기화가 토큰을 따로 두는 것과 같은 까닭). 연동을 끊을 일이면 그
        # 토큰을 골라 폐기한다(`revoke_token` — 계정 화면의 「토큰」). 계정을 지우면 토큰도
        # 전부 폐기된다(아래 `delete_account`).
        changes["sessions_revoked"] = auth_services.revoke_sessions(db, user.id)
    audit.record(
        db,
        action=audit.ACCOUNT_ACTIVATED if status == "active" else audit.ACCOUNT_SUSPENDED,
        actor=actor,
        target_table="users",
        target_id=user.id,
        target_label=user.email,
        changes=changes,
    )
    db.commit()
    db.refresh(user)
    return user


def set_system_admin(db: Session, *, user_id: uuid.UUID, grant: bool, actor: User) -> User:
    user = get_account(db, user_id)
    if not grant:
        _guard_last_admin(db, user, what="해제하")

    before = user.is_system_admin
    user.is_system_admin = grant
    audit.record(
        db,
        action=audit.ACCOUNT_ADMIN_CHANGED,
        actor=actor,
        target_table="users",
        target_id=user.id,
        target_label=user.email,
        changes={"is_system_admin": {"before": before, "after": grant}},
    )
    db.commit()
    db.refresh(user)
    return user


def set_home_workspace(
    db: Session, *, user_id: uuid.UUID, workspace_slug: str, actor: User
) -> User:
    """대표 소속 — 이 사람이 로그인해서 처음 서는 부서."""
    user = get_account(db, user_id)
    workspace = workspace_by_slug(db, workspace_slug)

    # **소속이 아닌 부서를 대표로 둘 수 없다.** 두면 로그인하자마자 403 이 나고,
    # 사람은 그것을 "시스템이 고장났다" 로 읽는다.
    if (
        db.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == user.id,
            )
        )
        is None
    ):
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="member"))

    before = user.home_workspace_id
    user.home_workspace_id = workspace.id
    audit.record(
        db,
        action=audit.ACCOUNT_HOME_CHANGED,
        actor=actor,
        target_table="users",
        target_id=user.id,
        target_label=user.email,
        workspace_id=workspace.id,
        changes={"home_workspace": {"before": str(before), "after": workspace.slug}},
    )
    db.commit()
    db.refresh(user)
    return user


def set_memberships(
    db: Session,
    *,
    user_id: uuid.UUID,
    workspace_slugs: list[str],
    home_workspace_slug: str | None,
    actor: User,
) -> User:
    """소속을 **통째로** 정한다 — 적힌 부서가 전부가 된다.

    부서 화면에도 멤버 관리가 있는데 여기에도 두는 이유: 사람을 옮기는 일은 **사람에서
    시작한다.** 「이 사람 소속을 바꿔라」 를 부서 화면에서 하려면 옛 부서를 먼저 찾아
    빼고 새 부서를 찾아 넣어야 하고, 중간에 그만두면 두 부서에 걸쳐 있거나 어디에도
    없는 계정이 남는다. 여기서는 한 번에 끝난다.

    **역할은 그대로 둔다** — 남는 부서의 관리자를 소속 변경이 멤버로 떨어뜨리면 안 된다.
    새로 들어가는 부서는 member 로 시작한다.
    """
    user = get_account(db, user_id)

    wanted: list[Workspace] = []
    seen: set[uuid.UUID] = set()
    for slug in workspace_slugs:
        workspace = workspace_by_slug(db, slug)
        if workspace.id not in seen:
            seen.add(workspace.id)
            wanted.append(workspace)

    current = {
        row.workspace_id: row
        for row in db.scalars(
            select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
        )
    }

    # **마지막 관리자를 조용히 빼지 않는다** — 그 부서는 아무도 못 고치는 상태가 된다.
    for workspace_id, member in current.items():
        if workspace_id in seen or member.role != "manager":
            continue
        if workspaces_services.manager_count(db, workspace_id) <= 1:
            leaving = db.get(Workspace, workspace_id)
            raise Conflict(
                code("ACCOUNTS", 8),
                f"{leaving.name if leaving else '그 부서'}의 마지막 관리자입니다. "
                "다른 사람을 관리자로 올린 뒤에 빼세요.",
            )

    for workspace_id, member in current.items():
        if workspace_id not in seen:
            db.delete(member)
    for workspace in wanted:
        if workspace.id not in current:
            db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="member"))

    home = wanted[0]
    if home_workspace_slug:
        chosen = workspace_by_slug(db, home_workspace_slug)
        if chosen.id not in seen:
            raise Conflict(
                code("ACCOUNTS", 9),
                f"{chosen.name}은(는) 이 계정의 소속이 아닙니다 — 대표 소속은 소속 중에서 "
                "고릅니다.",
            )
        home = chosen
    elif user.home_workspace_id in seen:
        found = db.get(Workspace, user.home_workspace_id)
        if found is not None:
            home = found

    before = [
        row.slug for row in db.scalars(select(Workspace).where(Workspace.id.in_(current)))
    ]
    before_home = user.home_workspace_id
    user.home_workspace_id = home.id
    audit.record(
        db,
        action=audit.ACCOUNT_WORKSPACES_CHANGED,
        actor=actor,
        target_table="users",
        target_id=user.id,
        target_label=user.email,
        workspace_id=home.id,
        changes={
            "memberships": {
                "before": ", ".join(sorted(before)),
                "after": ", ".join(sorted(row.slug for row in wanted)),
            },
            "home_workspace": {"before": str(before_home), "after": home.slug},
        },
    )
    db.commit()
    db.refresh(user)
    return user


def reset_password(db: Session, *, user_id: uuid.UUID, actor: User) -> str:
    """임시 비밀번호로 되돌린다. 평문은 **이 응답에서 한 번만** 나온다.

    관리자 복구 경로가 서버 콘솔뿐이면 사람은 결국 DB 를 직접 만진다 — 그 경로가
    강제 변경을 건너뛴다. 화면에 두되 must_change_password 를 함께 켠다.
    """
    user = get_account(db, user_id)
    temporary = secrets.token_urlsafe(9)
    user.password_hash = security.hash_password(temporary)
    user.must_change_password = True
    user.failed_logins = 0
    user.last_failed_login_at = None
    # **열려 있던 로그인을 끊는다** — 초기화는 대개 「비밀번호가 샜다」 이고, 그때 옛 세션
    # (리프레시 사슬)이 살아 있으면 초기화한 뜻이 없다(2026-10-08). 본인이 바꿀 때와 같다.
    # 기계 자격(PAT)은 따로 폐기한다 — 연동이 말없이 끊기지 않게.
    revoked = auth_services.revoke_sessions(db, user.id)
    audit.record(
        db,
        action=audit.ACCOUNT_PASSWORD_RESET,
        actor=actor,
        target_table="users",
        target_id=user.id,
        target_label=user.display_name or user.email,
        changes={"sessions_revoked": revoked},
    )
    db.commit()
    return temporary


def delete_account(db: Session, *, user_id: uuid.UUID, actor: User) -> User:
    """계정을 지운다. **행은 남고 접근만 끊긴다.**

    감사 기록과 자료의 담당자가 이 계정을 가리키고 있다. 행을 실제로 지우면
    그것들이 가리킬 곳을 잃고, 몇 년 전 자료를 누가 등록했는지 답할 수 없게 된다.
    """
    user = get_account(db, user_id)
    if user.id == actor.id:
        raise Conflict(code("ACCOUNTS", 7), "자기 계정은 지울 수 없습니다.")
    _guard_last_admin(db, user, what="지우")

    user.deleted_at = _now()
    user.status = "suspended"
    # **자격을 폐기한다 — 로그인(refresh)도 개인 토큰도.** 지운 계정은 되살리는 길이 없으니
    # 정지와 달리 토큰을 남길 까닭이 없다. 막혀 있기만 한 자격을 살아 있는 채로 두면 「이
    # 창구를 읽을 수 있는 자격」 목록(코어 › 액세스 토큰)이 그것을 센다(2026-10-08).
    audit.record(
        db,
        action=audit.ACCOUNT_DELETED,
        actor=actor,
        target_table="users",
        target_id=user.id,
        target_label=user.email,
        changes={
            "sessions_revoked": auth_services.revoke_sessions(db, user.id),
            "tokens_revoked": auth_services.revoke_pats(db, user.id),
        },
    )
    db.commit()
    db.refresh(user)
    return user


# --- 그 사람의 액세스 토큰 -----------------------------------------------------


def account_tokens(db: Session, user_id: uuid.UUID) -> list[PatOut]:
    """그 사람의 개인 토큰 — **폐기된 것까지.** 「언제 끊겼나」 도 이 목록이 답할 자리다."""
    return auth_services.list_pats(db, get_account(db, user_id))


def revoke_token(
    db: Session,
    *,
    user_id: uuid.UUID,
    token_id: uuid.UUID,
    actor: User,
    reason: str | None,
) -> PatOut:
    """남의 토큰 **하나**를 폐기한다 — 시스템 관리자.

    예전에는 본인만 폐기할 수 있었다. 퇴사자 · 정지된 계정의 연동 토큰을 끊으려면 계정을
    지워야 했고(지우면 그 사람의 토큰이 전부 끊긴다), 지울 수 없는 계정(자료의 담당자로 남은
    사람)의 토큰은 끊을 길이 없었다(2026-10-08). 정지는 토큰을 그대로 두므로(`set_status`)
    「계정은 살리고 이 연동만 끊는다」 는 이 길뿐이다.

    **이미 폐기된 것은 다시 폐기하지 않는다**(409) — 처음 폐기한 시각과 사람(감사 기록)을
    덮지 않으려고. 다른 사람의 토큰 id 를 섞어 보내면 없는 것과 같이 답한다(404).
    """
    owner = get_account(db, user_id)
    pat = db.get(PersonalAccessToken, token_id)
    if pat is None or pat.user_id != owner.id:
        raise NotFound(code("ACCOUNTS", 11), f"{owner.email} 의 토큰 중 해당 토큰이 없습니다.")
    if pat.revoked_at is not None:
        raise Conflict(
            code("ACCOUNTS", 12),
            f"이미 폐기된 토큰입니다 — {pat.revoked_at:%Y-%m-%d %H:%M}(UTC)에 폐기되었습니다.",
            details={"revoked_at": pat.revoked_at.isoformat()},
        )
    pat.revoked_at = _now()
    note = clean(reason or "") or None
    audit.record(
        db,
        action=audit.ACCOUNT_TOKEN_REVOKED,
        actor=actor,
        target_table="personal_access_tokens",
        target_id=pat.id,
        target_label=f"{owner.email} · {pat.name}",
        changes={
            "owner": owner.email,
            "owner_id": str(owner.id),
            "name": pat.name,
            "prefix": pat.prefix,
            "scopes": list(pat.scopes or []),
            "last_used_at": pat.last_used_at.isoformat() if pat.last_used_at else None,
        },
        reason=note,
    )
    if owner.id != actor.id:
        notifications.notify(
            db,
            user_id=owner.id,
            kind=notifications.ACCOUNT_TOKEN_REVOKED,
            title=f"액세스 토큰 「{pat.name}」 이 폐기되었습니다"[:200],
            body=(
                f"시스템 관리자({actor.display_name or actor.email})가 폐기했습니다. "
                "이 토큰을 사용하던 연동은 이제 인증에 실패합니다."
                + (f" 사유: {note}" if note else "")
            ),
            link="/me",
        )
    db.commit()
    db.refresh(pat)
    return PatOut.model_validate(pat)


# --- 홈의 「남은 일」에 끼는 것 -----------------------------------------------


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """가입 승인 대기.

    **시스템 관리자에게만 보인다.** 처리할 수 없는 사람에게 띄우면 그 자리는
    그저 못 지우는 숫자가 되고, 사람은 곧 홈의 그 칸을 아예 안 읽게 된다.
    """
    if not viewer.is_system_admin:
        return []
    return [
        extensions.MaintenanceItem(
            key="accounts_pending",
            label="가입 승인 대기",
            count=pending_count(db),
            link="/admin/accounts?status=pending",
            severity="warning",
        )
    ]


def stats(db: Session) -> list[extensions.StatItem]:
    total = (
        db.scalar(select(func.count()).select_from(User).where(User.deleted_at.is_(None))) or 0
    )
    return [extensions.StatItem(label="계정", count=total)]


# --- 온톨로지가 계정을 비추는 길 --------------------------------------------


def _system_ref(row: User) -> system_sources.SystemRef:
    return system_sources.SystemRef(
        id=row.id,
        key=row.email,
        label=row.display_name or row.email,
        hint=row.email,
        active=row.deleted_at is None and row.status == "active",
    )


def _system_base() -> Any:
    # 지워진 계정은 없는 것이다. 정지·대기는 **있되 못 고르는** 것 — 이미 걸린
    # 「담당자」 칸이 빈 칸으로 변하면 안 되기 때문이다.
    return select(User).where(User.deleted_at.is_(None))


def _system_search(
    db: Session, _viewer: User, q: str | None, limit: int, offset: int
) -> tuple[list[system_sources.SystemRef], int]:
    stmt = _system_base()
    if q:
        needle = f"%{clean(q)}%"
        stmt = stmt.where(User.display_name.ilike(needle) | User.email.ilike(needle))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(User.display_name, User.email).limit(limit).offset(offset))
    return [_system_ref(row) for row in rows], int(total)


def _system_lookup(
    db: Session, ids: list[uuid.UUID]
) -> dict[uuid.UUID, system_sources.SystemRef]:
    if not ids:
        return {}
    found = db.scalars(_system_base().where(User.id.in_(ids)))
    return {row.id: _system_ref(row) for row in found}


def _system_list_all(db: Session) -> list[system_sources.SystemRef]:
    return [
        _system_ref(row)
        for row in db.scalars(_system_base().order_by(User.display_name, User.email))
    ]


#: `kind_class='system'` 타입이 `system_source='user'` 로 가리키는 원 표.
SYSTEM_SOURCE = system_sources.SystemSource(
    key="user",
    label="계정",
    search=_system_search,
    lookup=_system_lookup,
    list_all=_system_list_all,
)
