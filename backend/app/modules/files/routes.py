"""첨부 라우터 — 올리기·목록·내려받기·떼기.

**내려받기는 평범한 링크로 안 된다.** access 토큰은 메모리에만 있어서 브라우저가
스스로 여는 주소(a href · img src)에는 안 실린다 — 프론트는 `downloadFile` 로
받는다(`shared/api/client.ts`). 그림도 같다 — 받아서 blob 주소로 띄운다.

## 파일 응답의 머리(ADR 0012)

- `X-Content-Type-Options: nosniff` — 브라우저가 내용을 보고 종류를 짐작하지 않게.
- `Content-Security-Policy: sandbox` — 누가 이 주소를 앱 창에서 직접 열어도 HTML · SVG 의
  스크립트가 앱의 주소로 돌지 않게.
- `Cache-Control: private` — 한 사람의 브라우저에만. 첨부는 id 마다 내용이 안 바뀐다(내용
  주소 · 행은 고치지 않는다) — 같은 화면을 다시 열 때 사진을 또 받지 않는다.
"""

from __future__ import annotations

import tempfile
import uuid
from collections.abc import Callable, Coroutine
from typing import Any, BinaryIO, cast
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.files import services
from app.modules.files.schemas import (
    AttachmentLimitsOut,
    AttachmentOut,
    TicketOut,
    TicketRequest,
)
from app.shared import filestore
from app.shared.auth import current_user

router = APIRouter(prefix="/attachments", tags=["files"])

#: 파일을 내보내는 응답마다 붙이는 머리.
FILE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, max-age=86400",
}


#: multipart 본문에서 파일이 아닌 몫 — 경계 · 칸 머리 · 다른 칸(owner_table 등). 밝힌 크기가
#: 상한 + 이만큼을 넘어야 받기 전에 끊는다(상한에 꼭 맞는 파일은 받아서 잰다).
MULTIPART_SLACK = 64 * 1024


class RefuseDeclaredTooLarge(APIRoute):
    """**본문을 받기 전에** 밝힌 크기(`Content-Length`)로 거절한다.

    FastAPI 는 multipart 를 끝까지 받아 임시 파일로 푼 **다음에야** 엔드포인트(와 그 의존성)를
    부른다 — 거기서 재면 상한을 넘는 파일도 다 받아 디스크에 쓰고 나서 거절한다. 그래서
    엔드포인트를 부르기 전, 머리만 보고 끊는다. 크기를 안 밝힌(chunked) 본문은 저장한 뒤의
    검사(`services.attach`)가 막는다. 여기서 던진 오류도 다른 오류와 같은 처리기를 거친다.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def checked(request: Request) -> Response:
            declared = request.headers.get("content-length", "")
            limit = services.max_bytes()
            if declared.isdigit() and int(declared) > limit + MULTIPART_SLACK:
                raise services.too_large(limit, size=int(declared))
            return await handler(request)

        return checked


@router.get("/limits", response_model=AttachmentLimitsOut)
def limits(_: User = Depends(current_user)) -> AttachmentLimitsOut:
    """첨부 한 파일의 상한 — 화면이 보내기 **전에** 견준다(넘는 파일은 아예 안 보낸다).
    설치마다 다르다(`.env` 의 `ATTACHMENT_MAX_BYTES`)."""
    return AttachmentLimitsOut(max_bytes=services.max_bytes())


def upload(
    owner_table: str = Form(max_length=60),
    owner_id: uuid.UUID = Form(),
    owner_field: str | None = Form(default=None, max_length=48),
    workspace_slug: str | None = Form(default=None),
    upload_file: UploadFile = File(alias="file"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> AttachmentOut:
    """파일 하나를 어떤 자료에 붙인다.

    `owner_table` · `owner_id` 는 **외래키가 아니다** — 공통 틀은 도메인 표를
    모른다. 붙이는 쪽이 자기 표 이름을 준다.
    """
    return services.upload(
        db,
        user=user,
        owner_table=owner_table,
        owner_id=owner_id,
        owner_field=owner_field,
        workspace_slug=workspace_slug,
        filename=upload_file.filename or "이름없음",
        content_type=upload_file.content_type,
        stream=upload_file.file,
    )


# 받기 전에 끊으려면 라우트 클래스가 엔드포인트보다 먼저 돌아야 한다 — 데코레이터는 그 자리를
# 안 열어 주므로 직접 건다.
router.add_api_route(
    "",
    upload,
    methods=["POST"],
    response_model=AttachmentOut,
    status_code=201,
    route_class_override=RefuseDeclaredTooLarge,
)


@router.post("/tickets", response_model=TicketOut, status_code=201)
def issue_ticket(
    payload: TicketRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TicketOut:
    """한 번 쓰는 업로드 표(5분) — MCP 가 파일을 **토큰으로 나르지 않게**(ADR 0012).

    자리(자료 · 칸)를 지금 확인하고 낸다. 받은 쪽은 표를 `X-Upload-Ticket` 머리에 실어
    `PUT /api/attachments/upload` 로 파일 바이트를 그대로 보낸다 — `curl -T <파일>`.
    """
    return services.issue_ticket(
        db,
        user=user,
        owner_table=payload.owner_table,
        owner_id=payload.owner_id,
        owner_field=payload.owner_field,
        filename=payload.filename,
    )


@router.put("/upload", response_model=AttachmentOut, status_code=201)
async def upload_with_ticket(
    request: Request,
    filename: str = Query(default="", max_length=255),
    ticket: str = Header(alias="X-Upload-Ticket", min_length=20, max_length=200),
    db: Session = Depends(get_db),
) -> AttachmentOut:
    """표로 올린다 — **토큰 없이**, 본문이 곧 파일이다(multipart 아님).

    **표부터 본다** — 표가 곧 자격이라, 안 되는 표면 본문을 한 바이트도 받지 않는다. 그다음
    밝힌 크기(`Content-Length`)가 상한을 넘으면 역시 받기 전에 끊고, 안 밝힌 본문은 흘려
    받으며 세다가 상한을 넘는 순간 끊는다(다 받고 나서 재지 않는다).
    """
    await run_in_threadpool(services.check_ticket, db, ticket=ticket)
    limit = services.max_bytes()
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise services.too_large(limit, size=int(declared))
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as spool:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise services.too_large(limit)
            spool.write(chunk)
        spool.seek(0)
        return await run_in_threadpool(
            services.redeem_ticket,
            db,
            ticket=ticket,
            filename=filename,
            stream=cast(BinaryIO, spool),
        )


@router.get("", response_model=list[AttachmentOut])
def list_attachments(
    owner_table: str,
    owner_id: uuid.UUID,
    owner_field: str | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[AttachmentOut]:
    """그 자료의 첨부들. `owner_field` 를 주면 **그 자리의 것만** 나온다."""
    return services.list_for(
        db, user=user, owner_table=owner_table, owner_id=owner_id, owner_field=owner_field
    )


@router.get("/{attachment_id}/content", include_in_schema=False)
def download(
    attachment_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    """파일 내용.

    **스키마에 안 싣는다** — 생성되는 프론트 타입에 파일 응답이 끼면 그 타입은
    `unknown` 이 되고, 그것을 쓰는 화면이 타입 검사를 통과해 버린다.
    """
    attachment = services.get_for_download(db, user=user, attachment_id=attachment_id)
    path = filestore.resolve(attachment.relative_path)
    services.ensure_readable(path is not None)
    assert path is not None  # ensure_readable 이 None 이면 이미 던졌다

    # 파일 이름은 둘로 낸다 — ASCII 는 옛 브라우저용, UTF-8 은 한글 이름용.
    disposition = "attachment; filename=\"download\"; filename*=UTF-8''" + quote(
        attachment.original_name
    )
    return FileResponse(
        path,
        media_type=attachment.content_type,
        headers={**FILE_HEADERS, "Content-Disposition": disposition},
    )


@router.get("/{attachment_id}/thumbnail", include_in_schema=False)
def thumbnail(
    attachment_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    """미리보기(WebP, 긴 변 320px). **서버가 이미지로 읽은 첨부만** — 아니면 404(FILES-8)."""
    path = services.thumbnail_path(db, user=user, attachment_id=attachment_id)
    return FileResponse(path, media_type="image/webp", headers=FILE_HEADERS)


@router.delete("/{attachment_id}", status_code=204)
def remove(
    attachment_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    services.remove(db, user=user, attachment_id=attachment_id)
