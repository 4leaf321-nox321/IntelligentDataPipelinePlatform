"""부서 라우터.

/options 만 인증 없이 열려 있다 — 가입 신청 화면에서 희망 부서를 골라야 하는데,
그 화면은 로그인 전이다. 이름과 주소만 나가고 멤버 수나 내부 식별자는 안 담는다.
"""

from __future__ import annotations

import csv
import io
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.jobs import routes as jobs_routes
from app.modules.jobs import services as job_services
from app.modules.jobs.schemas import JobOut
from app.modules.workspaces import services
from app.modules.workspaces.schemas import (
    MemberAddRequest,
    MemberOut,
    MemberRoleRequest,
    WorkspaceContentOut,
    WorkspaceCreateRequest,
    WorkspaceImportPlanOut,
    WorkspaceImportRequest,
    WorkspaceImportRowOut,
    WorkspaceMoveRequest,
    WorkspaceOption,
    WorkspaceOut,
    WorkspaceReassignRequest,
    WorkspaceReferenceOut,
    WorkspaceReorderRequest,
    WorkspaceUpdateRequest,
)
from app.shared import tabular
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import Conflict, Forbidden, code
from app.shared.permissions import require_manager, require_member, workspace_by_slug

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("/options", response_model=list[WorkspaceOption])
def options(db: Session = Depends(get_db)) -> list[WorkspaceOption]:
    """가입 신청 화면용. 로그인 전에 부르므로 인증이 없다."""
    return services.options(db)


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(
    all_workspaces: bool = Query(default=False, alias="all"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[WorkspaceOut]:
    if all_workspaces and not user.is_system_admin:
        raise Forbidden(
            code("WORKSPACES", 13), "전체 부서 목록은 시스템 관리자만 볼 수 있습니다."
        )
    return services.list_for(db, user, all_workspaces=all_workspaces)


@router.post("/import", response_model=WorkspaceImportPlanOut)
def import_workspaces(
    payload: WorkspaceImportRequest,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> WorkspaceImportPlanOut:
    """붙여 넣은 부서 정보를 넣는다 — ReportArchive 같은 다른 플랫폼의 내보내기를 그대로.

    `apply=false` 면 **계획만**: 행마다 새로/고침/그대로/건너뜀/오류. 오류가 하나라도 있으면
    아무것도 안 넣는다. 같은 slug 면 고치고, 상위는 같은 표 안의 것이어도 된다. 개인 공간
    (`kind=personal`)은 건너뛴다. 멤버·관리자 열은 안 읽는다 — 사람은 이쪽 계정으로 따로
    넣는다.
    """
    rows = payload.rows
    if rows is None:
        try:
            rows = tabular.parse_rows("pasted.csv", (payload.text or "").encode("utf-8"))
        except tabular.TabularError as caught:
            raise Conflict(code("WORKSPACES", 30), str(caught)) from None
    if not rows:
        raise Conflict(code("WORKSPACES", 30), "행이 없습니다.")
    if len(rows) > 2000:
        raise Conflict(code("WORKSPACES", 30), "한 번에 2,000행까지입니다.")
    plan = (
        services.apply_import(db, rows, actor=user)
        if payload.apply
        else services.plan_import(db, rows)
    )
    return WorkspaceImportPlanOut(
        applied=payload.apply and plan.ok,
        rows=[
            WorkspaceImportRowOut(
                row=one.row,
                slug=one.slug,
                action=one.action,
                label=one.label,
                changes=one.changes,
                message=one.message,
            )
            for one in plan.rows
        ],
        errors=plan.errors,
        counts=plan.counts,
    )


@router.get("/export.csv", include_in_schema=False)
def export_csv(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> Response:
    """부서 정보를 CSV 로.

    ## BOM 을 붙인다

    안 붙이면 Excel 이 한글을 깬다. 사내에서 이 파일을 여는 것은 대부분 Excel 이다.

    ## 스키마에 안 싣는다

    `include_in_schema=False` — 생성되는 프론트 타입에 CSV 응답이 끼면 그 타입은
    `unknown` 이 되고, 그것을 쓰는 화면이 타입 검사를 통과해 버린다. 이 경로는
    파일을 내려받는 자리지 데이터를 읽는 자리가 아니다.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(services.EXPORT_HEADER)
    writer.writerows(services.export_rows(db))

    # 파일 이름은 둘로 낸다 — ASCII 는 옛 브라우저용, UTF-8 은 한글 이름용.
    # f-string 을 안 쓴다: 이 값에는 따옴표가 섞여 있어 읽기 어려워진다.
    disposition = "attachment; filename=\"workspaces.csv\"; filename*=UTF-8''" + quote(
        "부서정보.csv"
    )
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@router.post("", response_model=WorkspaceOut, status_code=201)
def create_workspace(
    payload: WorkspaceCreateRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    workspace = services.create(
        db,
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        creator=admin,
        parent_slug=payload.parent_slug,
    )
    return services.workspace_out(db, workspace, admin)


@router.patch("/{slug}", response_model=WorkspaceOut)
def update_workspace(
    slug: str,
    payload: WorkspaceUpdateRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    workspace = services.update(
        db,
        slug=slug,
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
    )
    return services.workspace_out(db, workspace, admin)


@router.post("/{slug}/move", response_model=WorkspaceOut)
def move_workspace(
    slug: str,
    payload: WorkspaceMoveRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    workspace = services.move(
        db,
        slug=slug,
        parent_slug=payload.parent_slug,
        position=payload.position,
        actor=admin,
    )
    return services.workspace_out(db, workspace, admin)


@router.post("/{slug}/reorder", response_model=WorkspaceOut)
def reorder_workspace(
    slug: str,
    payload: WorkspaceReorderRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> WorkspaceOut:
    workspace = services.reorder(db, slug=slug, direction=payload.direction)
    return services.workspace_out(db, workspace, admin)


@router.get("/{slug}/references", response_model=list[WorkspaceReferenceOut])
def workspace_references(
    slug: str,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[WorkspaceReferenceOut]:
    """무엇이 이 부서를 가리키는가. 삭제 확인 화면이 부른다."""
    return services.references(db, slug=slug)


@router.get("/{slug}/contents", response_model=list[WorkspaceContentOut])
def workspace_contents(
    slug: str,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[WorkspaceContentOut]:
    """이 부서가 가진 것 — 자료 옮기기 화면이 부른다. 0 건도 나간다."""
    return services.contents(db, slug=slug)


@router.post("/{slug}/reassign", response_model=JobOut, status_code=202)
def reassign_workspace(
    slug: str,
    payload: WorkspaceReassignRequest,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> JobOut:
    """자료를 다른 부서로 통째 옮긴다 — 부서 통폐합의 앞 단계. **작업으로**(202).

    워커가 **계획**을 세운다 — 무엇이 몇 건 옮겨지나 · 겹쳐서 막히는 것(`result.items` ·
    `result.errors`). 아무것도 안 바뀐다. 사람이 읽고 `POST /api/jobs/{id}/apply` 로 적용하면
    같은 지문일 때만 한 트랜잭션으로 옮긴다 — 고른 종류만, 하나라도 실패하면 아무것도 안
    옮긴다. 옮기고 나서 원본을 보관하거나 지운다(도는 동안은 지우기를 막는다).

    예전에는 이 요청 안에서 옮기고 `{moved}` 를 돌려줬다 — 한 부서 객체 20만 개면 90초 남짓이라
    브라우저 · 이중화의 nginx 가 먼저 끊었다(2026-10-10). 옮긴 수는 이제 적용 작업의
    `result.moved` 다.

    없는 부서 · 같은 부서 · 모르는 종류는 **넣는 순간에** 거절한다(몇 분 뒤가 아니라).
    """
    services.check_reassign(
        db, slug=slug, target_slug=payload.target_slug, kinds=payload.kinds
    )
    job = job_services.enqueue(
        db,
        kind=services.REASSIGN_KIND,
        params={"slug": slug, "target_slug": payload.target_slug, "kinds": payload.kinds},
        user=admin,
        workspace_id=None,
    )
    db.commit()
    db.refresh(job)
    return jobs_routes._out(db, job)


@router.delete("/{slug}", status_code=204)
def delete_workspace(
    slug: str,
    admin: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> Response:
    services.delete(db, slug=slug, actor=admin)
    return Response(status_code=204)


# --- 멤버 --------------------------------------------------------------------


@router.get("/{slug}/members", response_model=list[MemberOut])
def list_members(
    slug: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[MemberOut]:
    workspace = workspace_by_slug(db, slug)
    # 조회는 멤버면 된다 — 같은 부서 사람이 누군지는 알아야 일이 된다.
    require_member(db, workspace=workspace, user=user)
    return services.members(db, workspace=workspace)


@router.post("/{slug}/members", response_model=MemberOut, status_code=201)
def add_member(
    slug: str,
    payload: MemberAddRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MemberOut:
    workspace = workspace_by_slug(db, slug)
    require_manager(db, workspace=workspace, user=user)
    return services.add_member(db, workspace=workspace, email=payload.email, role=payload.role)


@router.patch("/{slug}/members/{user_id}", response_model=MemberOut)
def set_member_role(
    slug: str,
    user_id: uuid.UUID,
    payload: MemberRoleRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> MemberOut:
    workspace = workspace_by_slug(db, slug)
    require_manager(db, workspace=workspace, user=user)
    return services.set_role(db, workspace=workspace, user_id=user_id, role=payload.role)


@router.delete("/{slug}/members/{user_id}", status_code=204)
def remove_member(
    slug: str,
    user_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    workspace = workspace_by_slug(db, slug)
    require_manager(db, workspace=workspace, user=user)
    services.remove_member(db, workspace=workspace, user_id=user_id)
