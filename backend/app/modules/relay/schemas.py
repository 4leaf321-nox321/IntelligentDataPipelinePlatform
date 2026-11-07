"""연결 지도와 파이프라인 상세의 모양 — 화면(`src/modules/relay`)이 이것을 그린다."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MapNode(BaseModel):
    id: str
    kind: str
    """system · endpoint · pipeline · target · type · downstream."""
    label: str
    sub: str = ""
    """이름 아래 작은 글 — 주소 · 판 · 마지막 실행."""
    status: str = ""
    """파이프라인: ok · pending · blocked · failed · broken · never. 대상: ok · unreachable ·
    mismatch · draft(초안만 보내는 곳 — 묻지 않았다)."""
    status_label: str = ""
    link: str = ""
    """눌렀을 때 갈 곳 — 파이프라인 상세, 대기 계획(대상 SP 의 작업 화면) …"""
    detail: dict[str, Any] = Field(default_factory=dict)


class MapEdge(BaseModel):
    source: str
    target: str
    weight: int = 0
    """선 굵기 — 지난 실행에서 그 길로 간 건수(파이프라인 → 대상). 모르면 0."""
    status: str = ""


class RelayMap(BaseModel):
    generated_at: str
    nodes: list[MapNode]
    edges: list[MapEdge]
    problems: list[str] = Field(default_factory=list)


class LineageStep(BaseModel):
    target_type: str
    target_field: str
    target_label: str = ""
    target_data_type: str = ""
    target_missing: bool = False
    """대상 SP 의 정의에 그 칸이 없다 — 화면이 빨갛게."""
    columns: list[str]
    source_paths: list[str]
    transform: str


class RelayJob(BaseModel):
    id: str
    kind: str
    status: str
    created_at: str
    finished_at: str | None = None
    summary: str = ""
    error: str | None = None


class PipelineDetail(BaseModel):
    slug: str
    system: str
    source: str
    adapter_kind: str
    endpoint: str
    endpoint_label: str
    target_name: str
    target_server: str
    target_token_env: str
    definition_path: str
    recipe_path: str
    types: list[str]
    relations: list[str]
    status: str
    status_label: str
    problems: list[str]
    lineage: list[LineageStep]
    state: dict[str, Any]
    history: list[dict[str, Any]]
    jobs: list[RelayJob]
    target_schema_note: str = ""
    recipe_label: str = "파일"
    """지금 도는 레시피 — 「파일」 또는 「판 N」."""
    schedule_label: str = "수동"
    """일정 — 「매일 01:00」 · 「60분마다」 · 「수동」."""
    auto_apply_label: str = "미사용"
    """자동 적용 — 「미사용」 또는 기준 한 줄(타이머 실행 · 사람 적용 N번 뒤 · 변경 M건
    이하 …)."""
    intake_label: str = ""
    """받기 점검(`relay_core.intake`) — 「미사용」 또는 기준 한 줄(삭제 N건 · 미러의 M% 초과 ·
    빈 칸 K건 이상이면 멈춤)."""
    late: str = ""
    """늦음의 까닭(`services.lateness`) — 일정대로라면 끝났어야 할 실행이 끝나지 않았다. 비면
    늦지 않았다."""
    definition_label: str = "파일"
    """지금 서 있는 정의 — 「파일」 · 「판 N」(사용 중인 판) · 「초안 N」(사용 중인 판이
    없는 새 것)."""
    draft: bool = False
    """사용 중인 정의가 없다 — 실행되지 않는다. 화면이 「사용」 을 권한다."""
    reconcile: dict[str, Any] | None = None
    """마지막 대조(`relay_core.reconcile.Report`) — 타입 · 갈래마다 본보기 20개까지. 전부는
    `GET /pipelines/{slug}/reconcile`. 대조한 적이 없으면 None."""
    reconcile_label: str = "수동"
    """대조의 일정 — 「7일마다 03:00」 · 「수동」(`RELAY_RECONCILE_DAYS` · `_AT`)."""


class TrendPlan(BaseModel):
    created: int = 0
    changed: int = 0
    gone: int = 0


class TrendRow(BaseModel):
    """실행 한 번(`relay_core.runner._record`) — 실패한 실행은 받은 수 · 미러가 비고 어디까지
    갔는지(`stage`)가 있다."""

    at: str
    status: str
    """nothing · planned · waiting · blocked · failed — 실행의 결과(자동 적용은 안 적는다)."""
    seconds: float = 0
    received: int | None = None
    full: bool | None = None
    """전량을 받았나 — 그날은 안 온 것이 삭제로 읽힌다."""
    mirror: int | None = None
    """그 실행 뒤 미러의 행 수(받기 점검이 멈췄으면 반영 전 그대로)."""
    added: int | None = None
    changed: int | None = None
    removed: int | None = None
    plan: TrendPlan | None = None
    """계획의 건수(객체 · 관계 합) — 비교까지 갔을 때만."""
    held: bool = False
    """받기 점검이 멈췄다."""
    stage: str | None = None
    """실패한 실행이 멈춘 단계 — 받기 · 미러 · 변환 · 비교 · 미리 보기."""


class TrendOut(BaseModel):
    rows: list[TrendRow]
    """오래된 것부터."""
    total: int
    """작업 폴더에 남은 줄 — `rows` 는 그 끝의 `limit` 개다."""


class RecipeOut(BaseModel):
    version: int
    status: str
    """draft · active · retired · discarded."""
    note: str
    client: str
    created_at: str
    activated_at: str | None = None


class RecipeIn(BaseModel):
    body: dict[str, Any]
    """`sp-table/1` 대응 파일 그대로."""
    note: str = ""


class RecipeSaved(BaseModel):
    version: int
    problems: list[str]
    """저장은 했지만 알아야 할 것 — 어댑터가 안 내는 열 · 비어 있는 types …"""


class DefinitionOut(BaseModel):
    version: int
    status: str
    """draft · active · retired · discarded."""
    note: str
    client: str
    created_at: str
    activated_at: str | None = None
    summary: str = ""
    """한 줄 — 「odata DevModels → jvhubtest · source plm」."""


class DefinitionIn(BaseModel):
    body: dict[str, Any]
    """`relay-pipeline/1` — `recipe` 칸은 없다(레시피는 레시피 판). 비밀은 환경 변수 이름만."""
    note: str = ""


class DefinitionSaved(BaseModel):
    version: int
    problems: list[str]
    """저장은 했지만 알아야 할 것 — 울타리 밖 주소 · 다른 source · 등록 안 된 대상 …"""


class DiscoverIn(BaseModel):
    adapter: dict[str, Any]
    """어댑터 설정 — 식별자(`key`) · 열(`columns`)은 아직 몰라도 된다."""


class DefinitionTestIn(BaseModel):
    recipe_version: int | None = None
    """이 레시피 판으로 바꿔 본다 — 비우면 켜진 레시피 판, 없으면 같은 slug 의 정의 파일 것."""
    limit: int = Field(default=10, ge=0, le=50)
    """돌려줄 표본 줄 수."""


# --- 거꾸로 보기 — 값의 출처(`relay_core.trace`) -----------------------------------------


class TraceSeen(BaseModel):
    value: str
    """원천 행의 원래 값."""
    rows: list[int]
    """그 값이 나온 원천 표의 행 번호(1행은 머리글)."""


class TraceField(BaseModel):
    name: str
    rule: str
    """사람이 읽는 레시피 규칙 한 줄."""
    columns: list[str]
    value: Any = None
    """기준 출력의 값."""
    seen: list[TraceSeen] = Field(default_factory=list)


class TraceRow(BaseModel):
    row: int
    values: dict[str, str]


class TraceRelation(BaseModel):
    relation: str
    direction: str
    """`out`(이 객체에서 나감) · `in`(이 객체로 들어옴)."""
    other: str


class TraceReconcile(BaseModel):
    state: str
    """`same` · `missing`(빠짐) · `deprecated`(안 씀) · `changed`(다름) · `extra`(대상에만) ·
    `unknown`(그 타입을 견주지 못함)."""
    at: str | None = None
    fields: list[str] = Field(default_factory=list)
    stale: bool = False
    """지금 기준을 적용하기 전의 대조 — 그 상태는 지금 기준의 것이 아니다."""


class TraceOut(BaseModel):
    type_slug: str
    key: str
    label: str = ""
    found: bool
    """기준 출력에 그 객체가 있나."""
    run: str = ""
    applied_at: str = ""
    basis: str
    recipe_basis: str
    note: str = ""
    fields: list[TraceField]
    rows: list[TraceRow]
    rows_total: int
    rows_hidden: bool = False
    """원천 행 · 원래 값(`seen`)을 뺐다 — 원천의 날것은 관리자만 본다(`/sample` 과 같은 선)."""
    relations: list[TraceRelation]
    reconcile: TraceReconcile | None = None
    problems: list[str]
