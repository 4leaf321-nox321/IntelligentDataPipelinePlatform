"""계정 라우터.

/signup 만 인증 없이 열려 있고 나머지는 시스템 관리자 전용이다. 부서 단위 멤버
관리는 workspaces 모듈이 담당한다 — 여기는 "계정 자체의 생애" 만 다룬다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts import services
from app.modules.accounts.models import User
from app.modules.accounts.schemas import (
    AccountOut,
    AccountSummaryOut,
    ApproveRequest,
    CreateAccountRequest,
    HomeWorkspaceRequest,
    MembershipsRequest,
    RejectRequest,
    SignupRequest,
    SystemAdminRequest,
    TemporaryPasswordResponse,
    TokenRevokeRequest,
)
from app.modules.auth.schemas import PatOut
from app.shared.auth import require_system_admin
from app.shared.pagination import Page

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("/signup", response_model=AccountOut, status_code=201)
def signup(payload: SignupRequest, db: Session = Depends(get_db)) -> AccountOut:
    """가입 신청. 승인 전까지는 로그인할 수 없다(status=pending)."""
    user = services.signup(
        db,
        email=payload.email,
        password=payload.password,
        display_name=payload.display_name,
        workspace_slug=payload.workspace_slug,
    )
    return services.account_out(db, user)


@router.get("/summary", response_model=AccountSummaryOut)
def account_summary(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> AccountSummaryOut:
    """활성 시스템 관리자가 몇 명인가, 대기가 몇 건인가.

    관리자가 1명이면 화면이 안내 한 줄을 띄운다 — 그 사람이 잠기면 복구 경로가
    서버 콘솔뿐이다.
    """
    return AccountSummaryOut(
        active_system_admins=services.active_system_admin_count(db),
        pending=services.pending_count(db),
    )


@router.get("", response_model=Page[AccountOut])
def list_accounts(
    status: str | None = Query(default=None, pattern="^(pending|active|suspended)$"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> Page[AccountOut]:
    """계정 목록 — **`total` 을 함께 준다**(다른 목록과 같은 `Page` 모양).

    맨 리스트로 주던 때는 화면이 처음 50명만 그리고 끝이었다 — 쪽을 넘길 근거(전체 수)가
    없어서다. 그리고 목록은 잘렸다는 말을 안 하므로 관리자는 그것이 전부라고 읽었다
    (2026-10-08).
    """
    users = services.list_accounts(db, status=status, limit=limit, offset=offset)
    return Page[AccountOut](
        items=[services.account_out(db, user) for user in users],
        total=services.count_accounts(db, status=status),
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=TemporaryPasswordResponse, status_code=201)
def create_account(
    payload: CreateAccountRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> TemporaryPasswordResponse:
    user, temporary = services.create_account(
        db,
        email=payload.email,
        display_name=payload.display_name,
        workspace_slug=payload.workspace_slug,
        role=payload.role,
        is_system_admin=payload.is_system_admin,
        created_by=admin,
    )
    return TemporaryPasswordResponse(
        account=services.account_out(db, user), temporary_password=temporary
    )


@router.post("/{account_id}/approve", response_model=AccountOut)
def approve(
    account_id: uuid.UUID,
    payload: ApproveRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> AccountOut:
    user = services.approve(
        db,
        user_id=account_id,
        decided_by=admin,
        workspace_slug=payload.workspace_slug,
        role=payload.role,
    )
    return services.account_out(db, user)


@router.post("/{account_id}/reject", response_model=AccountOut)
def reject(
    account_id: uuid.UUID,
    payload: RejectRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> AccountOut:
    user = services.reject(db, user_id=account_id, decided_by=admin, note=payload.note)
    return services.account_out(db, user)


@router.post("/{account_id}/suspend", response_model=AccountOut)
def suspend(
    account_id: uuid.UUID,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> AccountOut:
    user = services.set_status(db, user_id=account_id, status="suspended", actor=admin)
    return services.account_out(db, user)


@router.post("/{account_id}/activate", response_model=AccountOut)
def activate(
    account_id: uuid.UUID,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> AccountOut:
    user = services.set_status(db, user_id=account_id, status="active", actor=admin)
    return services.account_out(db, user)


@router.post("/{account_id}/home-workspace", response_model=AccountOut)
def set_home_workspace(
    account_id: uuid.UUID,
    payload: HomeWorkspaceRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> AccountOut:
    """대표 소속 — 이 사람이 로그인해서 처음 서는 부서."""
    user = services.set_home_workspace(
        db, user_id=account_id, workspace_slug=payload.workspace_slug, actor=admin
    )
    return services.account_out(db, user)


@router.put("/{account_id}/workspaces", response_model=AccountOut)
def set_memberships(
    account_id: uuid.UUID,
    payload: MembershipsRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> AccountOut:
    """소속을 **통째로** 정한다 — 적힌 부서가 소속의 전부가 된다.

    사람을 옮기는 일은 사람에서 시작한다. 부서 화면에서만 할 수 있으면 옛 부서를 찾아
    빼고 새 부서를 찾아 넣어야 하고, 중간에 그만두면 두 부서에 걸친 계정이 남는다.

    남는 부서의 역할은 그대로다. 마지막 관리자를 빼려 하면 거절한다.
    """
    user = services.set_memberships(
        db,
        user_id=account_id,
        workspace_slugs=payload.workspace_slugs,
        home_workspace_slug=payload.home_workspace_slug,
        actor=admin,
    )
    return services.account_out(db, user)


@router.post("/{account_id}/system-admin", response_model=AccountOut)
def set_system_admin(
    account_id: uuid.UUID,
    payload: SystemAdminRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> AccountOut:
    user = services.set_system_admin(
        db, user_id=account_id, grant=payload.is_system_admin, actor=admin
    )
    return services.account_out(db, user)


@router.post("/{account_id}/reset-password", response_model=TemporaryPasswordResponse)
def reset_password(
    account_id: uuid.UUID,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> TemporaryPasswordResponse:
    """비밀번호를 임시값으로 되돌린다. 평문은 이 응답에서 한 번만 나온다."""
    temporary = services.reset_password(db, user_id=account_id, actor=admin)
    user = services.get_account(db, account_id)
    return TemporaryPasswordResponse(
        account=services.account_out(db, user), temporary_password=temporary
    )


@router.get("/{account_id}/tokens", response_model=list[PatOut])
def account_tokens(
    account_id: uuid.UUID,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[PatOut]:
    """그 사람의 액세스 토큰 — 이름 · 범위 · 만료 · 최종 사용, **폐기된 것까지.** 평문은 없다
    (발급 응답에서 한 번만 나온다)."""
    return services.account_tokens(db, account_id)


@router.post("/{account_id}/tokens/{token_id}/revoke", response_model=PatOut)
def revoke_account_token(
    account_id: uuid.UUID,
    token_id: uuid.UUID,
    payload: TokenRevokeRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PatOut:
    """남의 토큰 하나를 폐기한다 — 계정은 그대로 두고 그 연동만 끊는다. 감사 기록
    (`account.token_revoked`)과 주인에게 가는 알림에 사유가 실린다. 이미 폐기된 것은 409."""
    return services.revoke_token(
        db, user_id=account_id, token_id=token_id, actor=admin, reason=payload.reason
    )


@router.delete("/{account_id}", response_model=AccountOut)
def delete_account(
    account_id: uuid.UUID,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> AccountOut:
    """계정을 지운다. 행은 남고 접근만 끊긴다 — 자료의 소유자 참조를 잃지 않기 위해서다."""
    user = services.delete_account(db, user_id=account_id, actor=admin)
    return services.account_out(db, user)
