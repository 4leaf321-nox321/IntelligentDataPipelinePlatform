"""첨부 — **무엇에 붙었는지는 문자열 두 칸으로 든다.**

도메인마다 첨부 표를 따로 만들면 업로드·내려받기·권한 판정이 그 수만큼 갈라진다.
그렇다고 공통 틀이 도메인 표에 외래키를 걸 수도 없다 — 그러면 이 모듈이 도메인을
알게 되고, 포크한 플랫폼은 없는 표를 가리키게 된다.

그래서 **느슨하게 가리킨다**: `owner_table` + `owner_id`. 외래키가 아니므로 DB 가
정합성을 지켜 주지 않는 대신, 도메인이 무엇이든 이 표 하나로 끝난다.

`workspace_id` 를 함께 두는 이유: 권한 판정이 도메인 표를 몰라도 되게 하려는
것이다. 첨부를 만들 때 그 자료의 소유 부서를 함께 적어 두면, 내려받기는 이 칸만
보고 판정할 수 있다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    owner_table: Mapped[str] = mapped_column(String(60), index=True)
    """무슨 표에 붙었나. **외래키가 아니다** — 공통 틀은 도메인 표를 모른다."""
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), index=True)

    owner_field: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    """그 행의 **어느 자리**에 붙었나. NULL 이면 행 전체의 첨부다(기존 동작).

    온톨로지의 `file` 속성이 이 칸을 쓴다 — 속성 키가 여기 들어간다. 대신
    `properties` JSONB 에 첨부 id 를 넣는 길도 있었지만, 그러면 첨부를 지웠을 때
    **JSONB 에 유령 id 가 남고 그것은 화면의 깨진 링크로만** 드러난다. 이 칸이면
    목록을 거르기만 하면 되고 삭제가 저절로 반영된다."""

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    """어느 부서의 것인가. NULL 은 전역이다.

    **이 칸이 없으면 내려받기가 도메인 표를 조회해야 한다** — 그러면 이 모듈이
    도메인을 알게 된다. RESTRICT 인 이유는 부서를 지울 때 첨부가 조용히 사라지지
    않게 하려는 것이고, 부서 삭제 확인 화면이 그 수를 보여 준다."""

    original_name: Mapped[str] = mapped_column(String(255))
    """사람이 올린 이름. 저장 이름은 해시라 이것이 없으면 무슨 파일인지 알 수 없다."""
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    """이미지면 **서버가 열어 보고 정한 종류**, 아니면 올린 쪽이 붙인 말(내려받기에만 쓴다)."""

    media: Mapped[str | None] = mapped_column(String(8), nullable=True)
    """서버가 열어 보고 정한 것 — `image`(래스터 넷 중 하나로 읽혔다) · `file`. NULL 은 이
    칸이 생기기 전에 올라와 아직 안 본 것이다(`files.services.inspect_pending`).

    **화면이 그림으로 띄우는 것은 `image` 뿐이다.** 올린 쪽이 `image/png` 라고 적어도
    서버가 못 열었으면 파일이다 — 그 말을 믿으면 HTML 이 앱의 주소에서 그림인 척 돈다."""
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """세운 뒤(EXIF 회전 반영)의 가로 · 세로. 이미지일 때만."""
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thumb_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    """미리보기(WebP)의 filestore 상대 경로. 내용 주소라 같은 사진은 미리보기도 하나다."""

    sha256: Mapped[str] = mapped_column(String(64), index=True)
    """내용의 해시이자 저장 이름. **같은 내용은 한 번만 저장된다** — 여러 행이 같은
    해시를 가리킬 수 있어서 unique 가 아니다."""
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    """**우리가 실제로 쓴 바이트.** 브라우저가 보낸 값을 믿지 않는다."""

    relative_path: Mapped[str] = mapped_column(String(300))
    """filestore 아래의 상대 경로. **절대경로를 넣지 않는다** — 서버를 옮기면 그
    값 전부가 틀린 것이 된다."""

    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    """**행만 지운다. 파일은 안 지운다** — 같은 내용을 다른 행이 가리킬 수 있다."""

    @property
    def is_image(self) -> bool:
        """서버가 이미지로 읽었나 — 응답 스키마가 이 이름으로 읽는다."""
        return self.media == "image"


class AttachmentTicket(Base):
    """한 번 쓰는 업로드 표 — **바이트가 모델(AI)을 거치지 않게** 하는 길(ADR 0012).

    MCP 서버는 서버에서 돌아 사용자 PC 의 파일을 못 읽는다. 그렇다고 AI 가 파일을 base64 로
    도구 인자에 실으면 1MB 가 수십만 토큰이다. 그래서 MCP 도구는 이 표를 받아 `curl` 명령을
    돌려주고, AI 의 셸이 그 명령으로 **파일을 직접** 올린다.

    - 표 자체가 자격이다(올리는 요청에는 토큰이 없다) — 그래서 짧게(5분), 한 번만, 자리를
      정해서 낸다. DB 에는 해시만 둔다(표가 새도 이 행으로는 못 올린다).
    - 낼 때 자리를 확인하고, 쓸 때 **다시** 확인한다 — 그 사이 권한이 바뀔 수 있다.
    """

    __tablename__ = "attachment_tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    """표의 sha256. 표 그 자체는 낸 응답에만 있다."""
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    """누구의 권한으로 올리나. 쓸 때 이 사람으로 다시 판정한다."""
    token_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    """표를 낸 개인 토큰의 이름 — 감사에 「어느 토큰이 올렸나」 를 남긴다."""
    owner_table: Mapped[str] = mapped_column(String(60))
    owner_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True))
    owner_field: Mapped[str | None] = mapped_column(String(48), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), default="", server_default="")
    """올릴 때 이름을 안 주면 이것."""
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("attachments.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
