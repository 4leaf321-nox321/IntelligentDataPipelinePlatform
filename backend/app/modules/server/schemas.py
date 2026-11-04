"""서버 화면의 응답 형태 — **지금 이 설치가 어떤 상태인가.**"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DiskOut(BaseModel):
    path: str
    total_bytes: int
    free_bytes: int
    used_percent: float


class TableCountOut(BaseModel):
    label: str
    count: int


class BackupOut(BaseModel):
    """마지막 백업이 언제였나. **「없다」 와 「설정이 없다」 를 구별한다** —
    같게 말하면 사람은 안 해도 되는 일을 하러 간다."""

    configured: bool
    path: str | None
    last_at: datetime | None
    age_hours: float | None
    stale: bool
    problem: str | None


class ServerStatusOut(BaseModel):
    app_name: str
    app_slug: str
    extensions: list[str]
    """지금 켜져 있는 확장 — `.env` 가 아니라 **화면에서 정한 것**이다."""
    extensions_unknown: list[str]
    """`.env` 에 적혔는데 이 번들에 없는 이름. **오타는 여기서만 드러난다.**"""
    version: str
    app_env: str
    database_url_safe: str
    """비밀번호를 지운 접속 문자열. **어느 DB 를 보고 있는지**가 문제 추적의
    첫 물음인데, 여러 플랫폼이 한 서버에 있으면 그것을 알 방법이 없다."""
    schema_head: str | None
    schema_current: str | None
    schema_behind: bool
    """DB 가 코드보다 뒤처져 있나. **화면이 말해 주지 않으면** 사람은 그 사실을
    엉뚱한 화면의 500 으로 만난다."""
    disk: DiskOut | None
    backup: BackupOut
    counts: list[TableCountOut]
    """`shared/extensions.py` 의 레지스트리가 채운다 — 도메인이 등록한 만큼 는다."""
    started_at: datetime


class ExtensionOut(BaseModel):
    """확장 하나의 켜짐 — **시스템 관리자 화면이 그리는 줄.**"""

    name: str
    description: str = ""
    """하는 일 한 줄 — 확장이 적은 것(`DESCRIPTION`)."""
    enabled: bool
    pinned: bool
    """화면에서 지정했나. 거짓이면 `.env` 의 `EXTENSIONS` 가 답한 것이다 —
    **아무것도 안 켜 본 설치는 예전과 똑같이 돈다.**"""
    updated_at: datetime | None


class ExtensionPatchIn(BaseModel):
    enabled: bool


class ExtensionEndpointOut(BaseModel):
    """확장의 부를 수 있는 자리 하나 — **경로는 확장 뿌리부터.**"""

    method: str
    path: str
    """`dt/pairs` 처럼 확장 이름 뒤부터. 앞에 `/api/ext/<이름>/` 이 붙는다."""
    summary: str
    """그 자리의 한 줄 설명(독스트링 첫 줄)."""
    query: list[str] = Field(default_factory=list)
    """물음표 뒤에 붙는 것들 — 필수는 뒤에 `*`."""
    body: list[str] = Field(default_factory=list)
    """본문 칸 이름들 — 필수는 뒤에 `*`. 한 단만 펼친다."""


class ExtensionApiOut(BaseModel):
    """켠 확장 하나와 그 자리들."""

    name: str
    endpoints: list[ExtensionEndpointOut]


class MaintenanceItemOut(BaseModel):
    """남은 일 하나. **홈이 이것을 보여 준다** — 관리 화면에 들어가야만 보이면
    아무도 안 본다."""

    key: str
    label: str
    count: int
    link: str | None
    severity: str
    """info · warning. 경고는 색이 붙는다."""


class PlatformProfileOut(BaseModel):
    """이 플랫폼의 자기소개 — **로그인 없이 읽힌다**(MCP 안내문의 첫머리 · `whoami`).

    같은 틀로 띄운 플랫폼 여럿이 한 에이전트에 붙으면 도구가 전부 같아서, 어느 플랫폼에
    물을지를 이것으로 고른다. 이름 · 한 줄 설명은 설치(`.env`)의 것, 소개와 사이는 MCP · API 로
    고친 것.
    """

    slug: str
    name: str
    tagline: str
    summary: str
    """무엇을 담나 — 한두 문장. 비어 있으면 아직 아무도 안 적었다."""
    notes: str
    """다른 플랫폼과의 사이 — 정본은 어디인가, 무엇은 여기 없나."""
    updated_at: datetime | None


class PlatformProfileIn(BaseModel):
    summary: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=2000)


class ProfileFactOut(BaseModel):
    """자동 요약 한 갈래 — **읽을 때 센 것**(저장하지 않는다)."""

    key: str
    label: str
    lines: list[str]


class PlatformProfileLiveOut(PlatformProfileOut):
    """자기소개 + 지금 담긴 것 + 낡음 — 로그인한 사람(토큰)에게. MCP 가 접속 때 이것을
    읽는다."""

    facts: list[ProfileFactOut]
    stale: list[str]
    """사람이 쓴 뒤 달라진 것(「생김: 타입 「보고서」」). 비었으면 소개가 지금과 맞다 — 소개가
    아직 없으면 그것도 한 줄로 말한다."""


class PipelineKitOut(BaseModel):
    """정제 도구 키트가 **이 설치에 있나** — 화면이 단추를 세울지 이것으로 정한다."""

    available: bool
    filename: str
    """내려받을 때의 이름 — `sp-pipeline-<이 설치의 판>.zip`. 판이 이름에 있어야 PC 에
    여러 판이 쌓여도 어느 것이 이 서버와 맞는지 안다."""
    version: str
    size_bytes: int


# --- 느린 요청 · 큰 응답(ADR 0026) --------------------------------------------------


class PerformanceThresholdsOut(BaseModel):
    """홈 「남은 일」 의 문턱 — 화면이 그 줄에 표시를 단다(같은 기준으로)."""

    p95_ms: int
    min_calls: int
    """p95 를 따지려면 24시간에 이만큼은 불려야 한다."""
    response_bytes: int


class RequestStatOut(BaseModel):
    """경로 틀 · 메서드 하나의 묶음."""

    method: str
    route: str
    """경로 틀 — `/api/objects/{type_slug}/{object_id}`. 맞는 라우트가 없던 요청은
    「(없는 경로)」 하나로 모인다."""
    calls: int
    p50_ms: int
    """호출의 절반이 이보다 빨랐다."""
    p95_ms: int
    """스무 번 중 한 번만 이보다 느렸다 — 평균은 느린 몇 번을 묻어 버린다."""
    max_ms: int
    avg_bytes: int
    max_bytes: int
    file: bool
    """대부분이 JSON 이 아닌 응답(파일 · 사진)이다 — 크고 받는 쪽 속도를 타는 것이 정상이라
    문턱 판정에서 뺀다."""
    slow: bool
    """p95 가 문턱을 넘었다(호출 수가 `min_calls` 이상일 때만 판정)."""
    large: bool
    """문턱보다 큰 응답을 한 번이라도 냈다."""


class RequestStatsOut(BaseModel):
    period: str
    since: datetime
    """실제로 센 기간의 시작. 기록이 상한보다 많으면 기간의 시작보다 늦다(`capped`)."""
    capped: bool
    """기록이 너무 많아 최근 쪽만 셌다 — 화면이 그렇게 말한다."""
    first_recorded_at: datetime | None
    """가장 오래된 기록. **비었으면 아직 아무것도 안 남긴 것**이다 — 빈 목록이 「그 기간에
    요청이 없었다」 인지 「이 판부터 남기기 시작했다」 인지 가른다."""
    thresholds: PerformanceThresholdsOut
    items: list[RequestStatOut]
    total: int
    limit: int
    offset: int


class RequestCaseOut(BaseModel):
    """최악 사례 한 줄 — 누가 · 언제 · 상태 · 시간 · 크기 · 요청 ID."""

    created_at: datetime
    user_label: str | None
    """로그인 전 요청이면 비어 있다."""
    path: str
    status_code: int
    duration_ms: int
    response_bytes: int
    content_type: str | None
    request_id: str | None
    """파일 로그와 잇는 끈 — 이 값으로 app.log 에서 그 요청의 모든 줄을 찾는다."""


class JobKindStatOut(BaseModel):
    """작업 종류 하나 — 그 기간에 들어온 작업의 소요 · 대기(초)."""

    kind: str
    label: str
    done: int
    failed: int
    cancelled: int
    open: int
    """지금 대기 · 실행 중."""
    run_p50_s: float | None
    run_p95_s: float | None
    run_max_s: float | None
    wait_p50_s: float | None
    wait_p95_s: float | None
    wait_max_s: float | None
    queued_oldest_s: float | None
    """아직 대기인 것 가운데 가장 오래 기다린 것 — 워커가 멎으면 여기만 는다."""


class CutQuestionOut(BaseModel):
    """같은 모양으로 끊긴 질문 묶음 하나(ADR 0031)."""

    kind: str
    """`timeout`(시간 상한) · `too_big`(SPARQL 그래프 · 추론이 큼) · `records`(SPARQL 이 빠진
    기록 타입을 부름)."""
    kind_label: str
    code: str
    method: str
    route: str
    shape: dict[str, Any]
    """물음의 모양 — 목록 · 통계면 `type` · `group_by` · `split_by` · `conditions`(칸과 연산 —
    값은 안 남긴다) …, SPARQL 이면 `types` · `query`(앞부분)."""
    count: int
    users: int
    """물은 사람 수(로그인 전 요청은 안 센다)."""
    last_at: datetime
    last_request_id: str | None
    """마지막 것의 요청 ID — app.log 에서 그 줄을 찾는다."""
    suggestion: dict[str, Any] | None = None
    """`suggest=true` 일 때 — 이 물음을 미리 세어 둘 지표 정의 초안(저장하지 않는다, ADR
    0032)."""


class CutQuestionsOut(BaseModel):
    period: str
    since: datetime
    items: list[CutQuestionOut]
    total: int
    """그 기간의 모양 수(`items` 는 많이 끊긴 것부터 일부)."""


class JobStatsOut(BaseModel):
    period: str
    since: datetime
    items: list[JobKindStatOut]
