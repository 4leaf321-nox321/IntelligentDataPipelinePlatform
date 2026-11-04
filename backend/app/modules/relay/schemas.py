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
    mismatch."""
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


class RecipeOut(BaseModel):
    version: int
    status: str
    """draft · active · retired."""
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
