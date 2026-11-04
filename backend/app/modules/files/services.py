"""첨부 로직 — 올리기·목록·지우기, 그리고 **누가 볼 수 있는가.**

권한은 `workspace_id` 한 칸으로 판정한다. 도메인 표를 조회하지 않는다 — 그러면
이 모듈이 도메인을 알게 되고, 포크한 플랫폼은 없는 표를 가리킨다.
"""

from __future__ import annotations

import hashlib
import io
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.files.models import Attachment, AttachmentTicket
from app.modules.files.schemas import AttachmentOut, TicketOut
from app.shared import extensions, filestore, images
from app.shared.errors import AppError, Conflict, Forbidden, NotFound, code
from app.shared.permissions import (
    require_owner_edit,
    resolve_owner_workspace,
    visible_owner_clause,
)
from app.shared.request_context import get_actor_token, set_actor_token
from app.shared.text import clean


def max_bytes() -> int:
    """한 파일의 상한 — 설치가 정한다(`attachment_max_bytes`, 기본 500MB). **서버가
    강제한다** — 화면의 검사는 우회할 수 있고, 우회되면 디스크가 찬다. 디스크가 차면 앱만이
    아니라 DB 도 함께 멈춘다."""
    return get_settings().attachment_max_bytes


def too_large(limit: int, *, size: int | None = None) -> AppError:
    """상한을 넘은 파일 — 받기 전(밝힌 크기) · 받다가 · 받은 뒤 어디서 끊어도 같은 말."""
    details: dict[str, int] = {"max_bytes": limit}
    if size is not None:
        details["size_bytes"] = size
    return AppError(
        code("FILES", 3),
        f"파일이 너무 큽니다 (최대 {limit // 1024 // 1024}MB).",
        status=413,
        details=details,
    )


#: 이미지만 받는 칸에 넣을 수 있는 것 — 사람에게 보일 말.
IMAGE_KINDS = "PNG · JPEG · GIF · WebP"


def _out(attachment: Attachment) -> AttachmentOut:
    return AttachmentOut(
        id=attachment.id,
        owner_table=attachment.owner_table,
        owner_id=attachment.owner_id,
        owner_field=attachment.owner_field,
        original_name=attachment.original_name,
        content_type=attachment.content_type,
        size_bytes=attachment.size_bytes,
        sha256=attachment.sha256,
        created_at=attachment.created_at,
        is_image=attachment.media == "image",
        width=attachment.width,
        height=attachment.height,
    )


def _inspect(attachment: Attachment, path: Path) -> None:
    """서버가 열어 보고 이미지인지 정한다. 이미지면 종류를 **서버가 읽은 것**으로 고치고
    미리보기를 만든다(`shared/images`)."""
    found = images.inspect(path)
    if found is None:
        attachment.media = "file"
        return
    attachment.media = "image"
    attachment.content_type = found.content_type
    attachment.width = found.width
    attachment.height = found.height
    thumb = images.thumbnail(path)
    if thumb is not None:
        attachment.thumb_path = filestore.save(io.BytesIO(thumb)).relative_path


def inspect_pending(db: Session, rows: list[Attachment]) -> None:
    """이미지 판별 칸이 생기기 전에 올라온 첨부를 **처음 보일 때** 본다.

    마이그레이션이 하지 않는 이유: 파일을 읽어야 하고, 이중화에서는 그 순간 저장소가
    붙어 있는지 모른다. 파일이 없으면 그대로 둔다(다음에 다시 본다).
    """
    pending = [row for row in rows if row.media is None]
    for row in pending:
        path = filestore.resolve(row.relative_path)
        if path is not None:
            _inspect(row, path)
    if any(row.media is not None for row in pending):
        db.commit()


def _visible(db: Session, user: User) -> Select[tuple[Attachment]]:
    """볼 수 있는 첨부. 전역 + 내 부서."""
    return select(Attachment).where(
        Attachment.deleted_at.is_(None),
        visible_owner_clause(user, Attachment.workspace_id),
    )


def upload(
    db: Session,
    *,
    user: User,
    owner_table: str,
    owner_id: uuid.UUID,
    owner_field: str | None,
    workspace_slug: str | None,
    filename: str,
    content_type: str | None,
    stream: BinaryIO,
) -> AttachmentOut:
    """파일 하나를 붙인다 — `attach` 에 커밋을 더한 것(요청 하나가 첨부 하나다)."""
    attachment = attach(
        db,
        user=user,
        owner_table=owner_table,
        owner_id=owner_id,
        owner_field=owner_field,
        workspace_slug=workspace_slug,
        filename=filename,
        content_type=content_type,
        stream=stream,
    )
    db.commit()
    db.refresh(attachment)
    return _out(attachment)


def attach(
    db: Session,
    *,
    user: User,
    owner_table: str,
    owner_id: uuid.UUID,
    owner_field: str | None,
    workspace_slug: str | None,
    filename: str,
    content_type: str | None,
    stream: BinaryIO,
) -> Attachment:
    """파일 하나를 붙인다. **커밋하지 않는다** — 사진 일괄 업로드(작업)는 여러 장을 한
    트랜잭션으로 붙인다(처리 함수는 커밋하지 않는다, `docs/sp/작업-워커-설계.md`).

    **그 자료의 도메인이 자리를 답한다**(`extensions.register_attachment_owner`) — 자료가
    있나, 고칠 수 있나, 그 칸이 무엇을 받나. 첨부의 부서는 자료의 부서를 따른다. 도메인이
    등록하지 않은 표는 예전 동작이다: 올린 쪽이 고른 부서의 관리자면 붙는다(전역은 시스템
    관리자뿐 — 여러 부서가 함께 보는 자리다).

    **이미지인지는 서버가 열어 보고 정한다** — 올린 쪽이 붙인 `content_type` 은 말일 뿐이다.
    파일은 행보다 먼저 저장소에 쓰인다 — 거절되거나 트랜잭션이 무르면 아무도 안 가리키는 파일이
    남고, 고아 정리(`files/gc.py`)가 하루 뒤에 지운다.
    """
    owner = extensions.attachment_owner(db, user, owner_table, owner_id, owner_field)
    if owner is None:
        workspace_id = resolve_owner_workspace(
            db, user, workspace_slug, what="첨부", code_value=code("FILES", 1)
        )
    else:
        workspace_id = owner.workspace_id

    stored = filestore.save(stream)
    if stored.size == 0:
        raise AppError(code("FILES", 2), "빈 파일입니다.", status=400)
    limit = max_bytes()
    if stored.size > limit:
        # **저장하고 나서도 잰다.** 밝힌 크기(Content-Length)로는 받기 전에 끊지만
        # (`routes.RefuseDeclaredTooLarge`), 그 값은 안 밝히거나 틀릴 수 있다. 파일은 남지만
        # 행이 없으므로 아무도 못 보고, 고아 정리가 지운다.
        raise too_large(limit, size=stored.size)

    attachment = Attachment(
        owner_table=owner_table,
        owner_id=owner_id,
        owner_field=owner_field,
        workspace_id=workspace_id,
        original_name=clean(filename)[:255] or "이름없음",
        content_type=(content_type or "application/octet-stream")[:120],
        sha256=stored.sha256,
        size_bytes=stored.size,
        relative_path=stored.relative_path,
        uploaded_by_id=user.id,
    )
    path = filestore.resolve(stored.relative_path)
    if path is not None:
        _inspect(attachment, path)
    if owner is not None and owner.accept == "image" and attachment.media != "image":
        raise AppError(
            code("FILES", 7),
            f"이 칸에는 이미지({IMAGE_KINDS})만 업로드할 수 있습니다 — "
            f"{attachment.original_name} 은(는) 이미지로 확인되지 않았습니다.",
            status=415,
            details={"accept": "image", "content_type": attachment.content_type},
        )
    db.add(attachment)
    db.flush()
    if owner is not None and owner.changed is not None:
        owner.changed(db, user, owner_field, attachment.original_name, True)
    return attachment


def list_for(
    db: Session,
    *,
    user: User,
    owner_table: str,
    owner_id: uuid.UUID,
    owner_field: str | None = None,
) -> list[AttachmentOut]:
    """그 자료의 첨부들.

    `owner_field` 를 주면 **그 자리의 것만** 돌려준다. 안 주면 전부다 — 자리를
    안 나눠 쓰던 기존 화면이 그대로 돈다.
    """
    stmt = _visible(db, user).where(
        Attachment.owner_table == owner_table, Attachment.owner_id == owner_id
    )
    if owner_field is not None:
        stmt = stmt.where(Attachment.owner_field == owner_field)
    rows = list(db.scalars(stmt.order_by(Attachment.created_at)))
    inspect_pending(db, rows)
    return [_out(row) for row in rows]


def get_for_download(db: Session, *, user: User, attachment_id: uuid.UUID) -> Attachment:
    """내려받을 첨부. **없는 것과 못 보는 것을 같게 답한다.**

    403 으로 가르면 그 id 가 존재한다는 사실이 새고, 그것은 알려 줄 이유가 없는
    정보다.
    """
    found = db.scalar(_visible(db, user).where(Attachment.id == attachment_id))
    if found is None:
        raise NotFound(code("FILES", 4), "첨부를 찾을 수 없습니다.")
    return found


def remove(db: Session, *, user: User, attachment_id: uuid.UUID) -> None:
    """첨부를 뗀다. **행만 지운다 — 파일은 안 지운다.**

    같은 내용을 다른 행이 가리킬 수 있어서, 파일까지 지우면 그쪽이 가리킬 곳을
    잃는다. 「아무도 안 가리키는 파일」 을 정리하는 일은 따로다.
    """
    found = db.scalar(_visible(db, user).where(Attachment.id == attachment_id))
    if found is None:
        raise NotFound(code("FILES", 4), "첨부를 찾을 수 없습니다.")
    detach(db, user=user, attachment=found)
    db.commit()


def detach(db: Session, *, user: User, attachment: Attachment) -> None:
    """첨부 하나를 뗀다. **커밋하지 않는다** — 사진 일괄 업로드의 「교체」 가 있던 사진을 떼고
    새것을 붙이는 일을 한 트랜잭션으로 한다."""
    # 뗄 때는 자리를 묻지 않는다(None) — 칸이 지워진 뒤에도 붙은 것은 뗄 수 있어야 한다.
    owner = extensions.attachment_owner(
        db, user, attachment.owner_table, attachment.owner_id, None
    )
    if owner is None:
        require_owner_edit(
            db, user, attachment.workspace_id, what="첨부", code_value=code("FILES", 5)
        )
    attachment.deleted_at = func.now()
    if owner is not None and owner.changed is not None:
        owner.changed(db, user, attachment.owner_field, attachment.original_name, False)


def move_owner(
    db: Session,
    owner_table: str,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
) -> int:
    """한 자료의 첨부를 다른 자료로 — 합치기에서. 부서는 받는 자료의 것을 따른다.
    **커밋하지 않는다** — 합치기와 같은 트랜잭션이다."""
    done = db.execute(
        update(Attachment)
        .where(
            Attachment.owner_table == owner_table,
            Attachment.owner_id == source_id,
            Attachment.deleted_at.is_(None),
        )
        .values(owner_id=target_id, workspace_id=workspace_id)
    )
    return extensions.rows_changed(done)


def thumbnail_path(db: Session, *, user: User, attachment_id: uuid.UUID) -> Path:
    """미리보기 파일. 이미지가 아니면 404 — 화면은 그때 파일 아이콘을 띄운다.

    미리보기 파일만 없으면(파일스토어를 일부만 되돌렸을 때) 원본에서 다시 만든다.
    """
    found = get_for_download(db, user=user, attachment_id=attachment_id)
    if found.media is None:
        inspect_pending(db, [found])
    if found.media != "image":
        raise NotFound(code("FILES", 8), "미리보기가 없는 첨부입니다(이미지가 아닙니다).")
    path = filestore.resolve(found.thumb_path) if found.thumb_path else None
    if path is None:
        original = filestore.resolve(found.relative_path)
        ensure_readable(original is not None)
        assert original is not None
        thumb = images.thumbnail(original)
        if thumb is None:
            raise NotFound(code("FILES", 8), "미리보기를 만들 수 없는 이미지입니다.")
        found.thumb_path = filestore.save(io.BytesIO(thumb)).relative_path
        db.commit()
        path = filestore.resolve(found.thumb_path)
    ensure_readable(path is not None)
    assert path is not None
    return path


def ensure_readable(path_exists: bool) -> None:
    """DB 에는 있는데 파일이 없을 때. **조용히 빈 파일을 주지 않는다.**

    백업을 DB 만 되돌리면 이 상태가 된다 — 그때 0바이트 파일을 내보내면 사람은
    그것을 원본이라고 믿는다.
    """
    if not path_exists:
        raise Forbidden(
            code("FILES", 6),
            "파일이 저장소에 없습니다. 백업에서 파일스토어가 함께 복구됐는지 확인하세요.",
        )


def stats(db: Session) -> list[extensions.StatItem]:
    total = (
        db.scalar(
            select(func.count()).select_from(Attachment).where(Attachment.deleted_at.is_(None))
        )
        or 0
    )
    return [extensions.StatItem(label="첨부", count=total)]


def _move_attachments(db: Session, source: uuid.UUID, target: uuid.UUID) -> int:
    done = db.execute(
        update(Attachment).where(Attachment.workspace_id == source).values(workspace_id=target)
    )
    return extensions.rows_changed(done)


def workspace_content(
    db: Session, workspace_id: uuid.UUID
) -> list[extensions.WorkspaceContent]:
    """부서 통폐합 때 옮길 첨부. **파일 자체는 안 움직인다** — 저장소의 경로는
    그대로고 소유 부서만 바뀐다. 옮기는 데 걸리는 시간이 파일 크기와 무관해야
    사람이 큰 부서도 통폐합할 수 있다."""
    count = (
        db.scalar(
            select(func.count())
            .select_from(Attachment)
            .where(Attachment.workspace_id == workspace_id)
        )
        or 0
    )
    return [
        extensions.WorkspaceContent(
            kind="attachments", label="첨부", count=int(count), move=_move_attachments
        )
    ]


def workspace_reference(
    db: Session, workspace_id: uuid.UUID
) -> list[extensions.WorkspaceReference]:
    """부서 삭제 확인에 뜨는 줄.

    FK 가 RESTRICT 라 **DB 가 거부한다** — blocks_delete 를 False 로 두면 화면은
    지울 수 있다고 말하고 서버가 500 을 낸다.
    """
    count = (
        db.scalar(
            select(func.count())
            .select_from(Attachment)
            .where(Attachment.workspace_id == workspace_id)
        )
        or 0
    )
    return [
        extensions.WorkspaceReference(
            table="attachments", label="첨부", count=count, blocks_delete=True
        )
    ]


# --- 한 번 쓰는 업로드 표(MCP) -----------------------------------------------------
#
# 바이트가 모델을 거치지 않게 — MCP 도구는 표와 `curl` 명령만 주고, AI 의 셸이 파일을 직접
# 올린다(ADR 0012). 표 자체가 자격이라 짧게 · 한 번만 · 자리를 정해서 낸다.

#: 표의 수명. AI 가 명령을 받아 바로 돌리는 데 충분하고, 새어도 곧 쓸모없어지게.
TICKET_TTL = timedelta(minutes=5)


def _ticket_hash(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


def _public_upload_url(path: str) -> str | None:
    """바깥 주소를 설정으로 아는 설치면 완전한 주소 — `MCP_PUBLIC_URL` 이
    `<앱 주소>/mcp` 다(표준이 아닌 포트의 프록시 뒤).

    MCP 포트를 바로 적은 값(`http://<IP>:<앱+2>/mcp` — ReportArchive 가 그렇게 쓴다)이면 그
    주소에서 `/mcp` 를 떼도 앱이 아니라 MCP 포트다. 그때는 만들지 않는다 — MCP 서버가 들어온
    주소로 만든다(앱 포트)."""
    settings = get_settings()
    configured = settings.mcp_public_url.strip().rstrip("/")
    if not configured.endswith("/mcp"):
        return None
    if urlsplit(configured).port == settings.port + 2:
        return None
    base = configured[: -len("/mcp")]
    prefix = settings.base_path
    if prefix and base.endswith(prefix):
        base = base[: -len(prefix)]
    return f"{base}{path}"


def issue_ticket(
    db: Session,
    *,
    user: User,
    owner_table: str,
    owner_id: uuid.UUID,
    owner_field: str | None,
    filename: str,
) -> TicketOut:
    """업로드 표를 낸다. **지금 자리를 확인한다** — 안 되는 자리면 표를 안 낸다(AI 가 curl 을
    돌리고 나서야 거절을 보지 않게)."""
    owner = extensions.attachment_owner(db, user, owner_table, owner_id, owner_field)
    if owner is None:
        raise Conflict(
            code("FILES", 9),
            f"업로드 티켓은 객체의 첨부에만 발급합니다({owner_table} 은(는) 지원하지 "
            "않습니다).",
        )
    ticket = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + TICKET_TTL
    db.add(
        AttachmentTicket(
            token_hash=_ticket_hash(ticket),
            user_id=user.id,
            token_name=get_actor_token(),
            owner_table=owner_table,
            owner_id=owner_id,
            owner_field=owner_field,
            filename=clean(filename)[:255],
            expires_at=expires_at,
        )
    )
    db.commit()
    # 표는 주소가 아니라 머리(`X-Upload-Ticket`)로 — 주소는 접근 로그에 남는다.
    path = f"{get_settings().base_path}/api/attachments/upload"
    return TicketOut(
        ticket=ticket,
        upload_path=path,
        upload_url=_public_upload_url(path),
        expires_at=expires_at,
        expires_in_seconds=int(TICKET_TTL.total_seconds()),
        max_bytes=max_bytes(),
        accept=owner.accept,
    )


def _ticket_holder(db: Session, found: AttachmentTicket | None) -> User:
    """쓸 수 있는 표면 그 표를 낸 사람 — 없거나 · 썼거나 · 만료됐거나 · 낸 사람이 막혔으면
    거절한다. 본문 전의 확인(`check_ticket`)과 쓸 때의 확인(`redeem_ticket`)이 **같은
    규칙**이다."""
    refused = AppError(
        code("FILES", 10),
        "업로드 티켓이 없거나, 이미 사용했거나, 만료됐습니다 — 새로 발급받으세요"
        "(MCP `attachment_upload_prepare`).",
        status=401,
    )
    if found is None or found.used_at is not None or found.expires_at <= datetime.now(UTC):
        raise refused
    user = db.get(User, found.user_id)
    if user is None or not user.can_sign_in:
        raise refused
    return user


def check_ticket(db: Session, *, ticket: str) -> None:
    """본문을 받기 **전에** 표를 본다. 올리는 요청에는 토큰이 없어 표가 곧 자격인데, 본문을
    다 받고 나서 봤다 — 아무나 표 없이 50MB 를 거듭 보내 서버 디스크(임시 파일)를 쓰게 할 수
    있었다(2026-10-08).

    잠그지 않고, 보고 나면 **트랜잭션을 닫는다** — 본문을 받는 동안(50MB 면 수십 초) 연결을
    트랜잭션 안에 세워 두지 않는다. 다 받은 뒤 `redeem_ticket` 이 잠그고 다시 본다(그 사이 다른
    요청이 같은 표를 쓸 수 있다).
    """
    try:
        _ticket_holder(
            db,
            db.scalar(
                select(AttachmentTicket).where(
                    AttachmentTicket.token_hash == _ticket_hash(ticket)
                )
            ),
        )
    finally:
        db.rollback()


def redeem_ticket(
    db: Session, *, ticket: str, filename: str, stream: BinaryIO
) -> AttachmentOut:
    """표로 올린다. **표를 낸 사람의 권한으로 자리를 다시 확인한다** — 그 사이 바뀔 수 있다.

    표는 성공했을 때만 쓴 것이 된다 — 이미지만 받는 칸에 엑셀을 올려 거절되면 같은 표로 고쳐
    다시 올릴 수 있다(5분 안). 같은 표를 동시에 두 번 쓰면 행 잠금이 하나만 통과시킨다.
    """
    found = db.scalar(
        select(AttachmentTicket)
        .where(AttachmentTicket.token_hash == _ticket_hash(ticket))
        .with_for_update()
    )
    user = _ticket_holder(db, found)
    assert found is not None  # _ticket_holder 가 None 이면 이미 던졌다
    # 감사에 「어느 토큰이 올렸나」 — 표를 낸 토큰의 이름을 그대로 잇는다.
    set_actor_token(found.token_name)
    found.used_at = func.now()
    made = upload(
        db,
        user=user,
        owner_table=found.owner_table,
        owner_id=found.owner_id,
        owner_field=found.owner_field,
        workspace_slug=None,
        filename=filename or found.filename or "이름없음",
        content_type=None,
        stream=stream,
    )
    found.attachment_id = made.id
    db.commit()
    return made
