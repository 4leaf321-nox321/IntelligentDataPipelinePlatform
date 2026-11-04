"""작업 API — 넣고, 보고, 적용하고, 취소하고, 받는다.

넣는 요청은 파일을 표에 저장하고 행 하나를 만들고 **202 로 곧장** 돌려준다. 그 뒤는 화면이
`GET /api/jobs/{id}` 를 2초마다 본다. 워커가 없으면 영영 `queued` 다 — 그래서
`GET /api/jobs/workers` 가 워커가 살아 있는지 말해 준다.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.modules.accounts.models import User
from app.modules.jobs import files as job_files
from app.modules.jobs import kinds, services
from app.modules.jobs.models import Job, JobFile, WorkerHeartbeat
from app.modules.jobs.schemas import (
    JobBulkIn,
    JobBulkResultOut,
    JobListOut,
    JobOut,
    JobProgress,
    KindOut,
    WorkerOut,
)
from app.modules.workspaces.models import Workspace
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import AppError, Forbidden, NotFound, code
from app.shared.pagination import clamp_limit
from app.shared.permissions import resolve_owner_workspace

router = APIRouter(prefix="/jobs", tags=["jobs"])


_FULL = object()


def _out(db: Session, row: Job, result: Any = _FULL) -> JobOut:
    """작업 한 줄. 결과의 줄 목록은 `ROWS_SHOWN` 까지만(`services.trim_rows`) — 목록은 줄
    목록을 비운 요약(`result`)을 따로 넘긴다."""
    spec = kinds.get(row.kind)
    who = db.get(User, row.requested_by_id) if row.requested_by_id else None
    workspace = db.get(Workspace, row.workspace_id) if row.workspace_id else None
    input_name = (
        db.scalar(select(JobFile.name).where(JobFile.id == row.input_file_id))
        if row.input_file_id
        else None
    )
    return JobOut(
        id=row.id,
        kind=row.kind,
        kind_label=spec.label if spec else row.kind,
        status=row.status,
        params={k: v for k, v in (row.params or {}).items() if k != "fingerprint"},
        progress=JobProgress(**(row.progress or {})),
        result=services.trim_rows(row.result) if result is _FULL else result,
        error=row.error,
        parent_id=row.parent_id,
        applied_by=(
            child.id
            if row.status == "done" and (child := services.apply_child(db, row.id)) is not None
            else None
        ),
        input_file_name=input_name,
        has_output=row.output_file_id is not None,
        requested_by_name=who.display_name if who else None,
        requested_by_id=row.requested_by_id,
        workspace_slug=workspace.slug if workspace else None,
        cancel_requested=row.cancel_requested,
        attempts=row.attempts,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def submit(
    db: Session,
    user: User,
    *,
    kind: str,
    params: dict[str, Any],
    upload: UploadFile | None,
    workspace_slug: str | None,
) -> Job:
    """작업 하나를 넣는다 — 객체 라우터의 가져오기 경로도 이것을 부른다.

    부서는 여기서 확정한다(`resolve_owner_workspace`) — 워커가 돌 때 거절되면 사람은 몇 분
    뒤에야 「권한이 없습니다」 를 본다. 넣는 순간에 거절해야 한다.
    """
    spec = kinds.get(kind)
    if spec is None:
        raise AppError(
            code("JOBS", 3),
            f"모르는 작업 종류입니다: {kind}. 되는 것: {', '.join(kinds.names())}",
            status=422,
        )
    workspace_id = (
        resolve_owner_workspace(
            db, user, workspace_slug, what="작업", code_value=code("JOBS", 15)
        )
        if spec.owns_workspace
        else None
    )
    stored: JobFile | None = None
    if upload is not None:
        # **흘려 받는다** — 예전에는 `upload.file.read()` 로 통째로 읽어 100MB 면 요청 하나가
        # 그만큼 메모리를 들었다(사진 zip 은 이제 2GB). 상한은 그 종류의 것
        # (`Kind.max_file_setting`).
        stored = services.store_upload(
            db,
            kind=kind,
            name=upload.filename or "upload.bin",
            content_type=upload.content_type or "application/octet-stream",
            stream=upload.file,
            size=upload.size,
        )
    merged = (
        {**params, "workspace_slug": workspace_slug} if spec.owns_workspace else dict(params)
    )
    job = services.enqueue(
        db,
        kind=kind,
        params=merged,
        user=user,
        workspace_id=workspace_id,
        input_file=stored,
    )
    db.commit()
    db.refresh(job)
    return job


@router.get("/kinds", response_model=list[KindOut])
def list_kinds() -> list[KindOut]:
    return [
        KindOut(
            name=one.name,
            label=one.label,
            needs_file=one.needs_file,
            two_step=one.two_step,
            max_file_bytes=services.upload_cap(one) if one.needs_file else None,
        )
        for one in kinds.all_kinds()
    ]


@router.get("/workers", response_model=list[WorkerOut])
def list_workers(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[WorkerOut]:
    """워커가 살아 있나. **없으면 작업은 영영 대기다** — 화면이 그것을 말해 줘야 한다."""
    cutoff = datetime.now(UTC) - timedelta(seconds=get_settings().worker_stale_seconds)
    rows = db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen.desc()))
    return [
        WorkerOut(
            worker_id=one.worker_id,
            host=one.host,
            pid=one.pid,
            started_at=one.started_at,
            last_seen=one.last_seen,
            current_job_id=one.current_job_id,
            alive=one.last_seen >= cutoff,
        )
        for one in rows
    ]


@router.post("", response_model=JobOut, status_code=202)
def create_job(
    kind: str = Form(),
    params: str = Form(default="{}", description="JSON"),
    workspace_slug: str | None = Form(default=None),
    upload: UploadFile | None = File(default=None, alias="file"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    try:
        parsed = json.loads(params or "{}")
    except ValueError:
        raise AppError(code("JOBS", 16), "params 는 JSON 이어야 합니다.", status=422) from None
    if not isinstance(parsed, dict):
        raise AppError(code("JOBS", 16), "params 는 JSON 객체여야 합니다.", status=422)
    spec = kinds.get(kind)
    if spec is not None and not spec.public:
        # **전용 경로가 권한 · 범위를 본다** — 여기로 들어오면 그것을 건너뛴다(일반 멤버가
        # `bundle_export` 로 모든 부서의 객체를 받고, 쓰기 범위만 있는 토큰이 정의가 든 묶음을
        # 적용할 수 있었다 — 2026-10-08).
        raise Forbidden(
            code("JOBS", 25),
            f"{spec.label}은(는) 이 API 로 넣지 않습니다 — 그 화면(전용 경로)에서 넣으세요.",
        )
    if spec is not None and spec.admin_only and not user.is_system_admin:
        # 워커가 돌릴 때도 다시 보지만(`services.run`), 넣는 순간에 거절해야 사람이 몇 분 뒤가
        # 아니라 지금 안다.
        raise Forbidden(code("JOBS", 24), f"{spec.label}은(는) 시스템 관리자만 돌립니다.")
    reserved = sorted(key for key in ("apply", "needs_scope") if key in parsed)
    if reserved:
        # 계획 없이 곧장 적용하는 길을 막는다 — 적용은 계획을 본 뒤 `/{id}/apply` 로만.
        raise AppError(
            code("JOBS", 26),
            f"{', '.join(reserved)} 는 넣을 수 없습니다 — 계획을 만든 뒤 "
            "`POST /api/jobs/{id}/apply` 로 적용합니다.",
            status=422,
        )
    job = submit(
        db, user, kind=kind, params=parsed, upload=upload, workspace_slug=workspace_slug
    )
    return _out(db, job)


@router.get("", response_model=JobListOut)
def list_jobs(
    status: str | None = Query(default=None),
    kind: str | None = Query(
        default=None, description="작업 종류 — `/api/jobs/kinds` 의 이름"
    ),
    mine: bool = Query(default=False, description="내가 시킨 것만"),
    limit: int | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobListOut:
    capped = clamp_limit(limit)
    rows, total = services.list_visible(
        db, user, limit=capped, offset=offset, status=status, kind=kind, mine=mine
    )
    return JobListOut(items=[_out(db, one, brief) for one, brief in rows], total=total)


@router.get("/{job_id}", response_model=JobOut)
def get_job(
    job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> JobOut:
    return _out(db, services.get_visible(db, user, job_id))


def _require_apply_scope(request: Request, plan_job: Job) -> None:
    """이 계획을 적용할 범위가 토큰에 있나 — **사람 세션에는 범위가 없다**(None 이면 통과).

    넣을 때 적어 둔 `needs_scope` 를 적용 자리에서 다시 묻는다. 계획은 아무것도 안 바꾸니
    읽기 범위로도 세울 수 있고, 바꾸는 것은 여기이기 때문이다.
    """
    needed = (plan_job.params or {}).get("needs_scope")
    granted: list[str] | None = getattr(request.state, "token_scopes", None)
    if needed and granted is not None and needed not in granted:
        raise AppError(
            code("JOBS", 18),
            f"이 토큰에는 {needed} 범위가 없습니다 — 이 작업을 적용하려면 필요합니다.",
            status=403,
            details={"needed": needed, "granted": granted},
        )


@router.post("/apply", response_model=list[JobBulkResultOut])
def apply_many(
    payload: JobBulkIn,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[JobBulkResultOut]:
    """고른 계획들을 **한 번에 적용한다** — 줄마다 결과를 돌려준다.

    스무 건을 스무 번 펼쳐 누르게 하면 아무도 끝까지 안 한다. 검사는 한 건 적용과 **같다**
    — 오류가 있는 계획, 이미 적용한 것, 남의 계획, 파일이 지워진 것은 그 줄만 막힌다.
    작업끼리는 서로 독립이라 하나가 막혀도 나머지는 간다.
    """
    out: list[JobBulkResultOut] = []
    for job_id in payload.ids:
        try:
            plan_job = services.get_visible(db, user, job_id)
            _require_apply_scope(request, plan_job)
            made = services.make_apply(db, user, plan_job)
            db.commit()
            db.refresh(made)
            out.append(
                JobBulkResultOut(
                    id=job_id,
                    status="ok",
                    message=f"{made.kind} 적용을 시작했습니다.",
                    job_id=made.id,
                )
            )
        except AppError as failed:
            db.rollback()
            out.append(JobBulkResultOut(id=job_id, status="error", message=failed.message))
    return out


@router.post("/cancel", response_model=list[JobBulkResultOut])
def cancel_many(
    payload: JobBulkIn,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[JobBulkResultOut]:
    """고른 작업들을 **한 번에 취소한다** — 줄마다 결과를 돌려준다.

    취소는 **부탁**이다(`cancel_requested`). 워커가 다음 묶음에서 그것을 보고 멈춘다 —
    이미 끝난 것은 그 줄만 막힌다.
    """
    out: list[JobBulkResultOut] = []
    for job_id in payload.ids:
        try:
            job = services.request_cancel(db, user, services.get_visible(db, user, job_id))
            db.commit()
            out.append(
                JobBulkResultOut(
                    id=job_id, status="ok", message=f"{job.kind} 취소를 요청했습니다."
                )
            )
        except AppError as failed:
            db.rollback()
            out.append(JobBulkResultOut(id=job_id, status="error", message=failed.message))
    return out


@router.post("/{job_id}/apply", response_model=JobOut, status_code=202)
def apply_job(
    job_id: uuid.UUID,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobOut:
    """계획을 본 뒤 **사람이 누르는 자리.** 같은 파일 · 같은 지문으로 적용 작업을 만든다."""
    plan_job = services.get_visible(db, user, job_id)
    _require_apply_scope(request, plan_job)
    job = services.make_apply(db, user, plan_job)
    db.commit()
    db.refresh(job)
    return _out(db, job)


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(
    job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> JobOut:
    job = services.request_cancel(db, user, services.get_visible(db, user, job_id))
    db.commit()
    db.refresh(job)
    return _out(db, job)


@router.get("/{job_id}/rows.csv")
def download_rows(
    job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    """계획의 **모든 줄** — 화면 · 상세는 앞의 `ROWS_SHOWN` 줄만 싣는다. 엑셀이 한글을 읽게
    BOM 을 붙인다. 묶음이면 줄마다 어느 타입의 것인지 첫 칸에."""
    job = services.get_visible(db, user, job_id)
    found = list(services.all_rows(job.result))
    if not found:
        raise NotFound(code("JOBS", 28), "이 작업의 결과에는 줄 목록이 없습니다.")
    nested = any(where for where, _ in found)
    columns = [key for key in _LEAD if any(key in row for _, row in found)]
    for _, row in found:
        for key in row:
            if key not in columns:
                columns.append(key)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow((["type"] if nested else []) + columns)
    for where, row in found:
        cells = [_cell(row.get(key)) for key in columns]
        writer.writerow(([where] if nested else []) + cells)
    name = f"{job.kind}-{str(job.id)[:8]}-rows.csv"
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


#: CSV 앞에 세우는 열 — 몇째 줄 · 무엇이 되나 · 무엇인가. 나머지는 줄에 나온 차례대로.
_LEAD = ("row", "action", "status", "name", "key", "label", "message")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_cell(one) for one in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


@router.get("/{job_id}/download")
def download_output(
    job_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> Response:
    job = services.get_visible(db, user, job_id)
    if job.output_file_id is None:
        raise NotFound(code("JOBS", 17), "이 작업에는 결과 파일이 없습니다.")
    stored = db.get(JobFile, job.output_file_id)
    if stored is None:
        raise NotFound(code("JOBS", 13), "결과 파일이 이미 지워졌습니다.")
    # 이름은 둘로 — ASCII 는 옛 브라우저용, UTF-8 은 한글 이름용(머리는 latin-1 만 받는다).
    ascii_name = stored.name.encode("ascii", "replace").decode().replace('"', "")
    headers = {
        "Content-Disposition": f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(stored.name)}"
    }
    # 큰 결과는 공용 저장소에 있다 — 파일에서 흘려 보낸다(통째로 메모리에 안 올린다).
    path = job_files.local_path(stored)
    if path is not None:
        return FileResponse(path, media_type=stored.content_type, headers=headers)
    return Response(content=stored.data, media_type=stored.content_type, headers=headers)


@router.post("/maintenance/purge", status_code=200)
def purge_files(
    user: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> dict[str, int]:
    """기한 지난 작업 파일과 **끝난 작업 기록** 지우기 — 워커도 한 시간마다 한다.
    손으로 당길 때."""
    return {
        "files": services.purge_expired_files(db),
        "jobs": services.purge_old_jobs(db),
    }
