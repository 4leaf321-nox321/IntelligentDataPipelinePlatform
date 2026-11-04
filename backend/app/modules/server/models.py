"""확장 모듈의 켜짐 — **설치마다 다르고, 화면에서 바꾼다.**

`.env` 의 `EXTENSIONS` 는 여전히 **기본값**이다. 이 표에 행이 있으면 그것이 이기고,
없으면 `.env` 가 답한다 — 그래서 아무것도 안 켜 본 설치는 예전과 똑같이 돈다.

**왜 표로 옮겼나.** 운영은 uvicorn 워커 넷에 작업 워커 하나다. 켜짐이 프로세스
메모리에 있으면 넷이 서로 다른 답을 하고, A · B 이중화에서는 두 대의 `.env` 가
갈려도 평소엔 안 드러난다 — 넘어간 날 메뉴가 달라진다. 표는 한 벌이다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ExtensionState(Base):
    """확장 하나의 켜짐. **행이 없으면 `.env` 의 기본값**이다."""

    __tablename__ = "extension_states"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    """확장 이름 — `app/extensions/<이름>` 의 그 이름."""
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    """**누가 바꿨나는 감사가 답한다**(`extension.toggle`) — 여기는 언제만 둔다."""


class PlatformProfile(Base):
    """**이 플랫폼의 자기소개** — 무엇을 담고, 다른 플랫폼과 어떤 사이인가. 한 행(`id=1`).

    같은 틀(Standard Platform)로 띄운 플랫폼 여럿이 한 에이전트에 도구로 붙으면 도구
    이름 · 설명이 전부 같다 — 에이전트가 어느 플랫폼에 물을지 가를 단서가 slug 하나뿐이다.
    MCP 서버가 이것을 읽어 안내문 첫머리 · `whoami` 에 싣는다. 이름 · 한 줄 설명은
    `.env`(브랜딩)가 그대로 쥐고, 여기는 **설치가 쌓이며 바뀌는 것**(담는 자료 · 정본이
    어디인가)을 쥔다 — 그래서 MCP · API 로 고친다. **로그인 없이 읽힌다**(에이전트가 토큰을
    싣기 전에 안내문이 서야 한다).
    """

    __tablename__ = "platform_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    summary: Mapped[str] = mapped_column(String(300), default="", server_default="")
    """무엇을 담나 — 한두 문장. 에이전트가 이 플랫폼에 물을지를 이것으로 고른다."""
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    """다른 플랫폼과의 사이 — 정본은 어디인가(「코어 기준정보의 정본은 허브」), 무엇은
    여기 없나."""
    facts_seen: Mapped[dict[str, str]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    """사람이 쓸 때 **무엇이 담겨 있었나**(자동 요약의 표시 → 이름). 지금과 달라지면 소개가
    낡은 것이다 — 「남은 일」 과 MCP 안내문이 그렇게 말한다."""
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
