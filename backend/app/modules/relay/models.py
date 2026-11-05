"""레시피 · 정의의 판 — **초안 · 활성 · 지난 판.** 레시피는 열 이름 · 코드 체계가 사내
정보라 저장소에 두지 않고 여기 둔다(ADR 0001 §5). 판마다 남기므로 되돌릴 수 있고, 누가 ·
어느 길(MCP · 화면)로 지었는지 남는다.

AI(MCP)는 **초안을 저장하고 시험**만 한다. **켜는 것은 사람이 화면에서**(`activate` 는 PAT 를
거절한다). 켜진 판이 없으면 정의 파일의 `recipe` 를 쓴다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RelayRecipe(Base):
    __tablename__ = "relay_recipes"
    __table_args__ = (UniqueConstraint("pipeline_slug", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pipeline_slug: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="draft")
    """draft · active · retired · discarded(버린 초안) — 파이프라인마다 active 는 하나다."""
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """`sp-table/1` 대응 파일 그대로."""
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    client: Mapped[str] = mapped_column(String(40), default="", server_default="")
    """누가 · 어느 길로 지었나 — `X-Client`(mcp · 화면) 또는 토큰 이름."""
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    activated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RelayDefinition(Base):
    """파이프라인 정의(`relay-pipeline/1`)의 판 — 원천(어댑터) · 대상 · `source` · 일정.
    레시피와 같은 길: AI(MCP)가 **초안을 짓고 시험**, **켜는 것은 사람이 화면에서**. 켜진
    판이 같은 slug 의 정의 파일을 이긴다. 비밀은 여기에도 적지 않는다(`token_env` 이름만)."""

    __tablename__ = "relay_definitions"
    __table_args__ = (UniqueConstraint("pipeline_slug", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pipeline_slug: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="draft")
    """draft · active · retired · discarded(버린 초안) — 파이프라인마다 active 는 하나다."""
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """`relay-pipeline/1` 그대로 — 단 `recipe`(파일 자리)는 없다. 레시피는 레시피 판에서."""
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    client: Mapped[str] = mapped_column(String(40), default="", server_default="")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    activated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
