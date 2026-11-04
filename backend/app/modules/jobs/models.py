"""작업 — **오래 걸리는 일은 요청이 아니라 표에 산다.**

파일을 올려 바꾸는 것이 이 플랫폼의 일반적인 쓰임이다. 그 일을 요청 안에서 하면
1만 행에서 분 단위가 되고, 브라우저·MCP 는 1-2분에서 끊고, 앱을 재시작하면 어디까지
됐는지 아무 데도 안 남는다. 그래서 일은 여기 한 행이 되고, 워커(`app/worker.py`)가
집어 돌리고, 요청은 그 행을 보기만 한다. 설계는 `docs/sp/작업-워커-설계.md`.

## 파일도 표에 든다 — 큰 것은 공용 저장소에

서버가 A · B 두 대다. 올린 파일이 A 의 로컬 디스크에 놓이면 B 의 워커가 못 읽는다. 작업 파일은
7일 뒤 지우는 임시물이라 `job_files.data` 에 넣으면 복제로 따라가고 어느 대가 집어도 된다. 첨부
(`attachments`)는 영구물이라 그대로 filestore 다.

**큰 것(`job_file_inline_max_bytes`, 기본 32MB 초과)은 DB 에 안 넣는다**(2026-10-10) —
행에는 공용 저장소(`FILESTORE_DIR`, 두 대가 같은 경로로 마운트)의 작업 폴더 아래 경로
(`path`, `_jobs/…`)만 둔다. 사진 zip 은 2GB 까지인데, bytea 한 값은 1GB 까지이고 그만한 값이
WAL · 복제로 흐르며 읽으려면 통째로 메모리에 올려야 한다. 둘 중 하나만 있다
(`ck_job_files_one_place`). 저장 계층은 `jobs/files.py` 하나다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

JOB_STATUSES = ("queued", "running", "done", "failed", "cancelled")

BRIEF_SQL = """CREATE OR REPLACE FUNCTION sp_job_brief(r jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE AS $$
DECLARE
  out jsonb;
  k text;
  v jsonb;
  n jsonb;
BEGIN
  IF r IS NULL THEN
    RETURN NULL;
  END IF;
  IF jsonb_typeof(r) = 'object' THEN
    -- 줄 수는 jsonpath 로 센다 — `r -> 'rows'` 로 꺼내면 수십 MB 배열을 통째로 베낀다
    -- (50건에 0.24초 → 0.04초, 2026-10-09 실측). 줄 목록을 뺀 나머지만 훑는다.
    -- strict 라야 배열을 낱개로 풀지 않는다(lax 는 `rows` 의 줄마다 걸러 못 알아본다).
    n := jsonb_path_query_first(r, 'strict $.rows ? (@.type() == "array").size()', '{}', true);
    out := '{}'::jsonb;
    FOR k, v IN SELECT * FROM jsonb_each(CASE WHEN n IS NULL THEN r ELSE r - 'rows' END) LOOP
      out := out || jsonb_build_object(k, sp_job_brief(v));
    END LOOP;
    IF n IS NOT NULL THEN
      out := out || jsonb_build_object('rows', '[]'::jsonb, 'rows_omitted', n);
    END IF;
    RETURN out;
  END IF;
  -- 배열은 안에 객체가 있을 때만 들어간다(묶음의 `objects[].plan.rows`) — 글자 목록
  -- (`errors` 등)을 한 칸씩 부르면 그것만으로 느려진다.
  IF jsonb_typeof(r) = 'array' AND jsonb_typeof(r -> 0) IN ('object', 'array') THEN
    SELECT coalesce(jsonb_agg(sp_job_brief(e) ORDER BY i), '[]'::jsonb)
      INTO out FROM jsonb_array_elements(r) WITH ORDINALITY AS t(e, i);
    RETURN out;
  END IF;
  RETURN r;
END
$$;
"""
"""**작업 결과의 요약** — 결과 안의 줄 목록(`rows`, 묶음이면 `objects[].plan.rows` 처럼
안쪽에도)을 비우고 그 수만 `rows_omitted` 로 남긴다. 작업 목록이 이것을 싣는다
(`services.list_visible`).

결과에는 파일의 **모든 줄의 계획**이 든다 — 5만 줄 파일이면 17 ~ 19MB 다. 목록이 그것을
작업마다 통째로 싣던 때는 목록 한 번이 93MB · 서버에서만 4초였고, 작업 화면은 도는 작업이
있으면 그것을 3초마다 다시 받아 「적용」 을 누르면 화면이 멈춘 듯했다(2026-10-09 실측).
**DB 안에서** 줄이는 까닭: 파이썬으로 가져와 줄이면 93MB 를 읽고 푸는 4초가 그대로다. 운영은
마이그레이션 0072 가 건다."""


def _need_brief(_target: Any, connection: Any, **_kw: Any) -> None:
    """시험은 스키마를 모델로 세운다 — 함수도 함께(운영은 0072)."""
    connection.exec_driver_sql(BRIEF_SQL)


event.listen(Base.metadata, "before_create", _need_brief)


class JobFile(Base):
    __tablename__ = "job_files"
    __table_args__ = (
        # 내용은 **한 곳에만** — 둘 다 비면 읽을 것이 없고, 둘 다 있으면 어느 쪽이 정본인지
        # 모른다.
        CheckConstraint("(data IS NULL) <> (path IS NULL)", name="one_place"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255))
    """사람이 올린 이름 — 확장자로 CSV 인지 JSON 인지 가른다."""
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    """작은 파일의 내용. 큰 파일이면 비어 있고 `path` 가 있다."""
    path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """큰 파일이 놓인 곳 — 공용 저장소(`FILESTORE_DIR`) 아래 상대 경로(`_jobs/<id>`).
    절대경로를 안 둔다 — 서버마다 마운트 자리가 다를 수 있다. 행이 지워지면(만료) 파일도
    지운다(`files.purge_expired`) — 첨부의 고아 정리(`files/gc.py`)는 이 폴더를 안 본다."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    """이 뒤로는 지운다 — 임시물이 영구물이 되면 표가 DB 의 대부분이 된다."""


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    kind: Mapped[str] = mapped_column(String(40), index=True)
    """`objects_import` · `relations_import` · … — `kinds.py` 의 등록 이름."""
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """종류가 정하는 인자 — 타입 · 부서 · apply · 지문. 값은 작아야 한다(파일은 따로)."""

    input_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("job_files.id", ondelete="SET NULL"), nullable=True
    )
    output_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("job_files.id", ondelete="SET NULL"), nullable=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    """적용 작업 → 그 계획 작업. 같은 파일 · 같은 지문을 여기서 잇는다."""

    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    """`{"stage": "계획", "done": 12400, "total": 50000}` — 워커가 다른 연결로 쓴다."""
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    """끝난 뒤의 결과. 가져오기면 계획 표 그대로 — 화면이 같은 표를 그린다."""
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    """왜 실패했나 — 서버의 말 그대로. 스택은 로그에."""

    requested_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    """어느 부서의 일인가 — 내 것과 내 부서 것만 보인다. NULL 은 전역."""

    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    """워커가 살아 있다는 표시. `running` 인데 오래 멎어 있으면 다른 워커가 되돌린다."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    host: Mapped[str] = mapped_column(String(120))
    pid: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    current_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), nullable=True
    )
