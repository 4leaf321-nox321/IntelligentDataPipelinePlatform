"""작업 API 형태."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobProgress(BaseModel):
    stage: str = ""
    done: int = 0
    total: int = 0


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    kind_label: str
    status: str
    """`queued` · `running` · `done` · `failed` · `cancelled`."""
    params: dict[str, Any]
    progress: JobProgress
    result: dict[str, Any] | None
    """끝난 뒤의 결과 — 가져오기면 계획 표(`ImportPlanOut` 모양 + `fingerprint`)."""
    error: str | None
    parent_id: uuid.UUID | None
    applied_by: uuid.UUID | None = None
    """이 **계획**을 적용한(또는 적용 중인) 작업 — 있으면 화면이 「적용」 을 안 세우고, 정제
    도구가 「적용함」 을 안다. 계획의 `result.applied` 는 영영 거짓이라(적용은 새 작업이다)
    이것이 「적용됐나」 의 답이다."""
    input_file_name: str | None
    has_output: bool
    requested_by_name: str | None
    requested_by_id: uuid.UUID | None = None
    """시킨 사람 — 화면이 「취소」 · 「적용」 을 그 사람(과 시스템 관리자)에게만 세운다(서버도
    그렇게 거절한다)."""
    workspace_slug: str | None
    cancel_requested: bool
    attempts: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int


class JobBulkIn(BaseModel):
    """목록에서 고른 작업들 — **한 번에 적용하거나 한 번에 취소한다.**

    스무 건을 스무 번 펼쳐 누르게 하면 아무도 끝까지 안 한다. 상한을 두는 이유는 한
    요청이 워커 큐를 통째로 채우지 않게 하기 위해서다 — 나눠 보내면 된다.
    """

    ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


class JobBulkResultOut(BaseModel):
    """줄마다 결과 — **하나가 막혀도 나머지는 간다.**

    통째로 되돌리면 스무 건 중 하나의 문제가 열아홉 건의 일을 없앤다. 대신 어느 것이
    왜 막혔는지 돌려준다(작업끼리는 서로 독립이다)."""

    id: uuid.UUID
    status: str
    """`ok` · `error`."""
    message: str
    job_id: uuid.UUID | None = None
    """적용이 만든 **새 작업**의 id. 취소는 비어 있다."""


class KindOut(BaseModel):
    name: str
    label: str
    needs_file: bool
    two_step: bool
    max_file_bytes: int | None = None
    """파일을 받는 종류면 그 상한(바이트) — 종류마다 다르다(표 파일 100MB · 사진 zip 2GB,
    설치가 `.env` 로 정한다). 화면이 보내기 전에 견준다 — 2GB 를 다 올린 뒤에야 413 을 보지
    않게."""


class WorkerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    worker_id: str
    host: str
    pid: int
    started_at: datetime
    last_seen: datetime
    current_job_id: uuid.UUID | None
    alive: bool = Field(description="심장박동이 최근인가")
