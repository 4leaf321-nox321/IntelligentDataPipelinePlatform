"""첨부 API 형태."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AttachmentLimitsOut(BaseModel):
    max_bytes: int
    """첨부 한 파일의 상한(바이트) — 넘는 업로드는 본문을 받기 전에 413."""


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_table: str
    owner_id: uuid.UUID
    owner_field: str | None
    """그 행의 **어느 자리**에 붙었나. NULL 이면 행 전체의 첨부다.

    온톨로지의 `file` 속성이 이 칸을 쓴다 — 화면이 속성별로 갈라 보여 준다."""
    original_name: str
    """사람이 올린 이름. 저장 이름은 해시라 이것이 없으면 무슨 파일인지 알 수 없다."""
    content_type: str
    size_bytes: int
    sha256: str
    """내용의 해시. **같은 파일인지 눈으로 확인할 수 있는 유일한 값이다.**"""
    created_at: datetime
    is_image: bool = False
    """**서버가 열어 보고** 이미지(PNG · JPEG · GIF · WebP)로 읽은 것 — 화면은 이것만 그림으로
    띄운다(`/thumbnail` 이 있다). 올린 쪽이 붙인 `content_type` 은 판정에 안 쓴다."""
    width: int | None = None
    """세운 뒤(EXIF 회전 반영)의 가로 · 세로. 이미지일 때만."""
    height: int | None = None


class TicketRequest(BaseModel):
    """업로드 표를 낸다 — 어느 자료의 어느 자리에 올릴지 **지금** 정하고 확인한다."""

    owner_table: str = Field(default="objects", max_length=60)
    owner_id: uuid.UUID
    owner_field: str | None = Field(default=None, max_length=48)
    filename: str = Field(default="", max_length=255)
    """올릴 때 `?filename=` 을 안 주면 이 이름."""


class TicketOut(BaseModel):
    ticket: str
    """한 번 쓰는 표. **이것 자체가 자격이다** — `X-Upload-Ticket` 머리로 보낸다(주소에 넣지
    않는다 — 주소는 접근 로그에 남는다)."""
    upload_path: str
    """올릴 경로(접두어 포함) — `PUT` 으로 파일 바이트를 그대로 보낸다(`curl -T <파일>`)."""
    upload_url: str | None = None
    """설정(`MCP_PUBLIC_URL`)으로 바깥 주소를 아는 설치면 완전한 주소. 아니면 부르는 쪽이
    자기가 들어온 주소로 붙인다(MCP 서버)."""
    expires_at: datetime
    expires_in_seconds: int
    max_bytes: int
    accept: str | None = None
    """`image` 면 서버가 이미지로 읽은 것만 붙는다."""
