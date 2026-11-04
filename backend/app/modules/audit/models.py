"""접근 로그와 감사 로그 — **목적이 다르다.**

  접근 로그(AccessLog)         사용자 지원용. 누가 언제 어느 API 를 썼는가
  감사 로그(AuditEntry)        무결성용. 무엇이 바뀌었는가, 누가 승인했는가
  요청 시간(RequestTiming)     성능용. 어느 API 가 얼마나 걸리고 얼마나 큰 응답을 냈나

둘을 한 표에 섞으면 보존 기간이 충돌한다 — 접근 로그는 몇 달이면 지워도 되지만
감사 로그는 남아야 한다. 처음부터 나눈다. 요청 시간도 같은 이유로 따로다(아래).

파일 로그(app.log)와도 역할이 다르다. 파일은 요청 단위 진단용이고 로테이션으로
사라지지만, 이것은 질의할 수 있어야 한다("지난주에 이 사람이 뭘 했나").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AccessLog(Base):
    __tablename__ = "access_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    """로그인 전 요청도 남으므로 nullable. 계정이 지워져도 기록은 남는다."""

    action: Mapped[str] = mapped_column(String(30), index=True)
    """LOGIN / LOGOUT / API. 화면 방문은 SPA 라 서버가 모르므로 API 호출로 갈음한다."""

    path: Mapped[str] = mapped_column(String(300))
    method: Mapped[str] = mapped_column(String(10))
    status_code: Mapped[int] = mapped_column(Integer)

    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    """파일 로그와 잇는 끈. 이 값으로 app.log 에서 그 요청의 모든 줄을 찾는다."""

    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class RequestTiming(Base):
    """요청 하나가 **얼마나 걸렸고 얼마나 컸나** — 「느린 요청 · 큰 응답」(ADR 0026).

    ## 접근 로그와 표를 가른다

    접근 로그는 상태를 바꾸는 요청만 남긴다(「무엇을 했는가」). 그런데 작업 목록 한 번이
    93MB · 4.4초였던 것은 **조회(GET)** 였고, 그것을 사람이 「화면이 느리다」 고 말하고 나서야
    알았다(2026-10-09). 그래서 여기는 조회까지 전부 남긴다. 접근 로그에 섞으면 그 표가 조회로
    가득 차 정작 찾을 것을 못 찾는다 — 보관 기간도 다르다(이것은 `log_retention_days` 뒤 워커가
    지운다).

    ## 가볍게 둔다

    요청마다 한 줄이라 가장 빨리 자라는 표다. 칸을 줄이고, 계정에 **외래키를 걸지 않는다** —
    걸면 계정 하나를 지울 때 이 표 전체를 훑는다(계정은 보통 안 지우지만 시험은 지운다).
    누가 불렀나는 보여 줄 때 이어 붙인다.
    """

    __tablename__ = "request_timings"
    __table_args__ = (
        # 「이 경로 틀의 최악 사례」 — 경로 틀 · 메서드로 좁히고 기간으로 자른다.
        Index("ix_request_timings_route", "route", "method", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    """요청을 **받은** 시각. 기록은 몇 초씩 모아 넣으므로(`shared/request_timing.py`) 넣은
    시각이 아니라 미들웨어가 받을 때 잰 값을 적는다. 기간 집계 · 지우기가 이 색인을 탄다."""
    method: Mapped[str] = mapped_column(String(10))
    route: Mapped[str] = mapped_column(String(300, collation="C"))
    """경로 틀 — `/api/objects/{type_slug}/{object_id}`. 같은 API 를 묶는 단위다(원 경로는
    객체마다 달라서 묶을 수 없다). 맞는 것이 없던 요청은 「(없는 경로)」 하나로 모은다 — 아무
    주소나 두드리는 요청이 묶음을 끝없이 늘리지 않게.

    **글자 정렬 규칙을 C 로 둔다.** 기간 집계는 이 칸으로 묶느라 통째로 정렬하는데, 기본
    규칙(C.UTF-8)이면 70만 줄 묶기가 0.64초, C 면 0.36초였다(2026-10-10 실측). 사람이 읽는
    순서가 필요한 칸이 아니다."""
    path: Mapped[str] = mapped_column(String(300))
    """원 경로 — 최악 사례에서 「어느 객체였나」 를 본다. 물음표 뒤는 남기지 않는다(접근 로그와
    같다)."""
    status_code: Mapped[int] = mapped_column(SmallInteger)
    duration_ms: Mapped[int] = mapped_column(Integer)
    """받고 나서 **응답의 마지막 바이트를 보낼 때까지**(ms). 첫 바이트까지가 아니다 — 큰 JSON ·
    스트리밍 · 파일 내려받기는 첫 바이트 뒤가 길다. 받는 쪽이 느리면 그만큼 든다."""
    response_bytes: Mapped[int] = mapped_column(BigInteger)
    """보낸 본문 바이트."""
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    """응답의 종류(매개변수를 뗀 것). JSON 이 아닌 응답(파일 내려받기 · 사진)은 크고 받는 쪽
    속도를 타는 것이 정상이라 「남은 일」 의 문턱에서 뺀다."""
    user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """누가 불렀나 — 로그인 전 요청은 비어 있다. 외래키가 없다(위)."""
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    """파일 로그와 잇는 끈 — 이 값으로 app.log 에서 그 요청의 모든 줄을 찾는다."""


class CutQuestion(Base):
    """**끊긴 질문** — 답을 못 받고 끊긴 물음과 그 모양(ADR 0031).

    시간 상한(504)에 걸린 목록 · 통계, 너무 커서 세우기 전에 거절한 SPARQL, 빠진 기록 타입을
    부른 SPARQL. 무엇을 지표로 미리 세어 둘지, 트리플 스토어가 정말 필요한지를 **실제
    물음으로** 가르려고 남긴다 — 전에는 짐작뿐이었다.

    **모양만 남긴다** — 타입 · 기준 · 세부 기준 · 조건의 칸과 연산. 조건의 값과 검색어는
    남기지 않는다(요청 시간 기록이 물음표 뒤를 안 남기는 것과 같은 까닭). SPARQL 은 질의문이
    곧 물음이라 앞부분을 남긴다. 드물게 생기는 줄이라 요청 시간 기록처럼 모아 넣지 않고 그때
    한 줄 넣는다.
    """

    __tablename__ = "cut_questions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    """`timeout`(시간 상한) · `too_big`(SPARQL 그래프 · 추론이 큼) · `records`(SPARQL 이 빠진
    기록 타입을 부름)."""
    code: Mapped[str] = mapped_column(String(40))
    """사람이 받은 오류 코드 — `APP-COMMON-0504` · `APP-RDF-0005` …"""
    method: Mapped[str] = mapped_column(String(10))
    route: Mapped[str] = mapped_column(String(300))
    """경로 틀(요청 시간 기록과 같다) — 같은 물음을 묶는 단위의 하나."""
    shape: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """물음의 모양 — 묶는 단위의 나머지. 값이 같으면 같은 물음이다
    (`shared/cut_questions.py`)."""
    user_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True), nullable=True)
    """누가 물었나 — 외래키가 없다(요청 시간 기록과 같은 까닭)."""
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    """app.log 와 잇는 끈."""


class AuditEntry(Base):
    """감사 로그 — **무엇이 바뀌었고 누가 승인했는가.**

    ## 모든 변경을 담지 않는다

    담으면 아무도 안 읽는다. **되돌릴 수 없거나 권한이 실린 것**만 남긴다 —
    계정 상태, 부서 삭제, 기준 이름 변경처럼 "누가 그랬지" 가 실제로 문제가
    되는 일이다. 값 하나 고친 것까지 남기면 그 안에서 이걸 못 찾는다.

    ## 지워져도 남는다

    actor_id 는 SET NULL 이고 **이름을 함께 박아 둔다.** 계정이 지워지면 누가 했는지
    모르게 되는데, 그건 감사 로그가 존재하는 이유와 정면으로 어긋난다.

    ## 고치지 않는다

    이 표에는 updated_at 이 없다. 감사 기록을 고칠 수 있으면 감사가 아니다 —
    쓰는 길만 있고 고치는 길은 API 에도 없다.
    """

    __tablename__ = "audit_entries"
    __table_args__ = (
        # 객체의 변경 이력이 **관계 기록을 양 끝 id 로** 찾는다(`changes.src / dst`).
        # 표현식 인덱스가 없으면 그 조회가 감사 표 전체를 훑고, 표는 매일 자란다 —
        # 느려진 이유는 상세 화면 어디에도 안 적힌다.
        Index("ix_audit_entries_changes_src", text("(changes ->> 'src')")),
        Index("ix_audit_entries_changes_dst", text("(changes ->> 'dst')")),
        # 여럿 골라 고치기의 묶음 번호 — 「같이 바뀐 것 되돌리기」 가 이것으로 찾는다.
        Index("ix_audit_entries_changes_batch", text("((changes -> '_batch') ->> 'id')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    action: Mapped[str] = mapped_column(String(60), index=True)
    """account.suspended 처럼 대상.한일. **과거형으로 적는다** — 일어난 일의
    기록이지 명령이 아니다."""

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    actor_label: Mapped[str] = mapped_column(String(200))
    """그때의 사람 이름. **계정이 지워져도 남아야 한다.**"""

    actor_client: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    """어느 통로로 들어왔나 — 화면인가, 연계 스크립트인가, AI 도구인가.

    소유자 id 는 **사람**이다. 그래서 그것만으로는 사람이 넣은 것과 기계가 넣은
    것이 구별되지 않는다 — 반년 뒤 「이 값 누가 넣었나」 에 "관리자" 라고만 답하면
    쓸모가 없다. 호출자가 `X-Client` 헤더로 스스로 밝힌다(보안 경계가 아니라
    감사 표식이다)."""

    actor_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    """쓴 개인 토큰의 이름. 그 토큰이 만든 것만 골라낼 수 있다."""

    target_table: Mapped[str] = mapped_column(String(60), index=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True, index=True
    )
    """**외래키를 안 건다.** 지워진 대상의 기록이 그 삭제 때문에 사라지면 안 된다."""
    target_label: Mapped[str] = mapped_column(String(300))

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True, index=True
    )
    """어느 부서의 일인가. 가시성 판정에 쓴다 — 여기도 FK 를 안 건다."""

    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """{키: {before, after}}. **바뀐 것만** 담는다 — 통째로 스냅샷하면 표가 커지고
    무엇이 바뀌었는지는 오히려 안 보인다."""

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    """사람이 적은 사유. 없을 수 있다 — **없다고 안 남기지는 않는다.**"""

    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    seq: Mapped[int] = mapped_column(
        BigInteger, Identity(always=False), nullable=False, unique=True
    )
    """넣은 차례. `created_at` 은 트랜잭션 시작 시각이라 **한 트랜잭션의 기록 여럿이 같은
    값**을 갖는다 — 합치기·승격처럼 객체 여러 개를 한 번에 고치는 일이 그렇다. 그 안의
    순서를 이것이 정한다. 이력 재구성이 이 순서를 거꾸로 밟는다."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
