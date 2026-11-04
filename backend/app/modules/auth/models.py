"""토큰 — refresh 폐기 목록과 PAT.

**refresh 토큰을 JWT 로 만들지 않는다.** JWT 는 서버가 상태를 갖지 않으므로 발급한
뒤에는 되돌릴 수 없다. 그러면 탈취된 토큰이 만료까지 유효하고, 끊을 방법이 없다.
그래서 refresh 는 불투명 난수이고 여기 한 행으로 존재한다 — 행을 지우거나
revoked_at 을 채우면 즉시 무효다.

원문은 어디에도 저장하지 않는다. sha256 해시만 둔다. DB 가 새어도 남의 세션을
탈취할 수 없어야 한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
        nullable=True,
    )
    """회전 이력. 폐기된 토큰이 다시 쓰이면 탈취 신호로 볼 수 있다 — 그 판정에
    "방금 회전한 것인가" 를 물으려면 사슬이 필요하다(services.rotate_refresh)."""

    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)


class PersonalAccessToken(Base):
    """스크립트·연계 프로그램이 API 를 부를 때 쓰는 자격 증명.

    자동화가 붙을 때 무슨 자격으로 부를지가 **지금** 정해져 있어야 토큰 체계가
    둘로 갈라지지 않는다. 사람 세션(refresh)과 기계 자격(PAT)은 수명과 폐기
    방식이 다르므로 표를 나눈다.
    """

    __tablename__ = "personal_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    """어디에 쓰는 토큰인지. 폐기할 때 이것만 보고 판단하게 된다."""

    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default='["read"]')
    """이 토큰으로 할 수 있는 일. **기본은 읽기뿐이다.**

    아는 범위의 목록은 `shared/scopes.py` 의 레지스트리가 든다 — 도메인이 자기
    범위를 등록하고, 등록되지 않은 값은 발급에서 거절된다.

    비어 있으면 아무것도 못 쓴다. 「모르는 것은 막는다」 가 맞는 기본값이다:
    새 엔드포인트가 생길 때마다 자동으로 열리면, 그것을 알아채는 사람이 없다."""

    prefix: Mapped[str] = mapped_column(String(64), index=True)
    """평문의 앞자리. 목록 화면에서 어느 토큰인지 알아보게 하는 용도.

    **표식이 `APP_SLUG` 에서 나오므로 이름이 길어지면 이 칸도 길어진다**
    (`<slug>_pat_` + 6자). 16자로 잡았다가 slug 를 `standardplatform` 으로 두는
    순간 넘쳤다 — 오류는 토큰을 **발급하는 자리**에서 나는데, 거기서는 컬럼 길이가
    원인이라는 것이 안 보인다. 그래서 넉넉히 두고, slug 길이는 시험이 32자로
    막는다(tests/unit/test_branding.py)."""
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """마지막 사용 시각. 안 쓰는 토큰을 찾아 지우는 근거가 된다."""

    delegated_client: Mapped[str | None] = mapped_column(String(40), nullable=True)
    """**HWAX 게이트웨이가 위임 발급**(`POST /api/auth/sso`, ADR 0028)으로 받아 간 토큰이면 그
    client(`X-Heax-Client` — 보통 `gateway`). 사람이 화면에서 만든 토큰은 NULL.

    이름(`HWAX 게이트웨이(<client>)`)으로 가리지 않는 까닭: 같은 이름을 사람이 손으로 지으면
    다음 위임 발급이 **그 사람의 토큰을 회수**하고, 그 토큰으로 든 요청이 게이트웨이 통로로
    적힌다. 이 칸이 있는 토큰으로 온 요청은 감사의 통로가 `hwax-gateway`
    다(`shared/auth.py`)."""


class PortalLaunchJti(Base):
    """HWAX 포털 launch 토큰의 **쓴 jti** — 한 토큰은 한 번만 로그인시킨다(ADR 0028).

    launch 토큰은 90초 사는 서명 문서라 그 안에서는 누가 들고 와도 유효하다 — 브라우저 기록 ·
    프록시 로그 · 뒤로 가기의 폼 재전송으로 같은 토큰이 다시 올 수 있다. **메모리가 아니라 DB
    에 둔다**: 이중화면 같은 토큰을 두 서버가 받을 수 있고, 워커(프로세스)도 넷이다. 기본키
    충돌이 곧 「이미 썼다」 다(동시에 둘이 와도 하나만 들어간다).

    원문은 두지 않고 해시만 둔다(길이가 정해지고, 모양이 이상한 jti 가 칸을 넘지 않는다).
    `expires_at`(토큰의 exp + 여유)이 지난 줄은 다음 콜백이 지운다 — 그 뒤에 같은 토큰이 오면
    만료로 이미 거절된다.
    """

    __tablename__ = "portal_launch_jtis"

    jti_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
