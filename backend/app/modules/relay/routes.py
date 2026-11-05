"""`/api/relay` — 연결 지도 · 파이프라인 상세는 **로그인한 사람 누구나**, 실행 · 적용은 시스템
관리자만(ADR 0001 §6). 실행은 요청 안에서 하지 않고 작업으로 넣는다 — 원천 · 대상이 느리면
요청이 끊긴다."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.modules.accounts.models import User
from app.modules.jobs import services as job_services
from app.modules.relay import definitions, recipes, services
from app.modules.relay import jobs as relay_jobs  # noqa: F401 — import 가 곧 작업 종류 등록
from app.modules.relay.models import RelayDefinition, RelayRecipe
from app.modules.relay.schemas import (
    DefinitionIn,
    DefinitionOut,
    DefinitionSaved,
    DefinitionTestIn,
    DiscoverIn,
    PipelineDetail,
    RecipeIn,
    RecipeOut,
    RecipeSaved,
    RelayMap,
)
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import Conflict, NotFound, code
from relay_core.state import Workdir

router = APIRouter(prefix="/relay", tags=["relay"])


class RunIn(BaseModel):
    human_edits: str | None = None
    """대상 SP 에서 사람이 고친 칸 — 비우면 keep(비켜 가고 계획에 적음), overwrite 면
    덮는다."""


class QueuedOut(BaseModel):
    job_id: str
    kind: str


@router.get("/map", response_model=RelayMap)
def relay_map(_: User = Depends(current_user), db: Session = Depends(get_db)) -> RelayMap:
    return services.build_map(db)


@router.get("/pipelines/{slug}", response_model=PipelineDetail)
def pipeline_detail(
    slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> PipelineDetail:
    return services.detail(db, slug)


def _queue(db: Session, user: User, kind: str, params: dict[str, Any]) -> QueuedOut:
    found = services.by_slug(params["slug"], db)  # 없는 파이프라인이면 넣기 전에 404
    if found.draft:
        raise Conflict(
            code("RELAY", 15),
            f"{found.slug} 의 정의가 아직 초안입니다 — 정의 판을 사용해야 실행됩니다.",
        )
    job = job_services.enqueue(db, kind=kind, params=params, user=user, workspace_id=None)
    db.commit()
    return QueuedOut(job_id=str(job.id), kind=kind)


@router.post("/pipelines/{slug}/run", response_model=QueuedOut, status_code=202)
def pipeline_run(
    slug: str,
    body: RunIn | None = None,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> QueuedOut:
    params: dict[str, Any] = {"slug": slug}
    if body and body.human_edits:
        params["human_edits"] = body.human_edits
    return _queue(db, user, services.RUN_KIND, params)


@router.post("/pipelines/{slug}/apply", response_model=QueuedOut, status_code=202)
def pipeline_apply(
    slug: str, user: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> QueuedOut:
    return _queue(db, user, services.APPLY_KIND, {"slug": slug})


class ReconcileIn(BaseModel):
    plan: bool = False
    """어긋난 줄을 다시 보내는 **보정 계획**(미리보기)까지 올리나 — 적용은 사람이."""


@router.post("/pipelines/{slug}/reconcile", response_model=QueuedOut, status_code=202)
def pipeline_reconcile(
    slug: str,
    body: ReconcileIn | None = None,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> QueuedOut:
    """대상과 대조 — 대상에 지금 있는 것을 지난번 적용된 출력과 견준다(읽기만). `plan` 이면
    보정 계획까지 올린다(대상의 검토함에 뜬다 — 적용은 사람이)."""
    return _queue(
        db,
        user,
        services.RECONCILE_KIND,
        {"slug": slug, "plan": bool(body and body.plan)},
    )


@router.get("/pipelines/{slug}/reconcile")
def pipeline_reconcile_report(
    slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """마지막 대조의 보고서 **전부**(상세는 본보기만 싣는다). 대조한 적이 없으면 404."""
    services.by_slug(slug, db)
    report = services.read_report(slug)
    if report is None:
        raise NotFound(code("RELAY", 18), f"{slug} 은 아직 대조한 적이 없습니다.")
    return report


# --- 레시피의 판 — MCP 가 초안 · 시험, 사람이 화면에서 켠다 ------------------------------


def _recipe_out(row: RelayRecipe) -> RecipeOut:
    return RecipeOut(
        version=row.version,
        status=row.status,
        note=row.note,
        client=row.client,
        created_at=row.created_at.isoformat(),
        activated_at=row.activated_at.isoformat() if row.activated_at else None,
    )


@router.get("/pipelines/{slug}/recipes", response_model=list[RecipeOut])
def recipe_versions(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[RecipeOut]:
    services.by_slug(slug, db)
    return [_recipe_out(one) for one in recipes.versions(db, slug)]


@router.get("/pipelines/{slug}/recipes/{version}")
def recipe_body(
    slug: str,
    version: int,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = recipes.get(db, slug, version)
    return {**_recipe_out(row).model_dump(), "body": row.body}


@router.post("/pipelines/{slug}/recipes/{version}/discard", response_model=RecipeOut)
def recipe_discard(
    slug: str,
    version: int,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RecipeOut:
    """초안을 버린다 — 초안만(켜진 · 지난 판은 그대로). 토큰도 된다."""
    return _recipe_out(recipes.discard(db, user, slug, version))


@router.get("/pipelines/{slug}/recipe-file")
def recipe_file(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """정의 파일이 가리키는 레시피 — 켜진 판이 없을 때 도는 것. 초안을 지을 출발점이다."""
    found = services.by_slug(slug, db)
    if found.recipe is None:
        return {"path": "", "body": None, "problems": found.problems}

    return {
        "path": str(found.recipe),
        "body": json.loads(found.recipe.read_text(encoding="utf-8")),
    }


@router.post("/pipelines/{slug}/recipes", response_model=RecipeSaved, status_code=201)
def recipe_draft(
    slug: str,
    body: RecipeIn,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RecipeSaved:
    found = services.by_slug(slug, db)
    row, problems = recipes.save_draft(db, user, request, found, body.body, body.note)
    return RecipeSaved(version=row.version, problems=problems)


@router.post("/pipelines/{slug}/recipes/{version}/test")
def recipe_test(
    slug: str,
    version: int,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """미러에 그 판을 돌려 본다 — **대상에 아무것도 안 보낸다.** 「켜면 무엇이 바뀌나」 를
    보인다."""
    return recipes.trial(db, services.by_slug(slug, db), version)


@router.post("/pipelines/{slug}/recipes/{version}/activate", response_model=RecipeOut)
def recipe_activate(
    slug: str,
    version: int,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RecipeOut:
    """켠다 — **화면에서 사람만**(토큰이면 거절). 다음 실행부터 이 판이 돈다."""
    found = services.by_slug(slug, db)
    return _recipe_out(recipes.activate(db, user, request, found, version))


# --- 레시피를 짓는 데 보는 것 — 원천 표본 · 대상의 정의 ---------------------------------


@router.get("/pipelines/{slug}/sample")
def source_sample(
    slug: str,
    limit: int = Query(default=20, ge=1, le=200),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """미러(마지막으로 받은 원천)의 앞 몇 줄 — 레시피가 읽는 **평평한 열** 그대로. 원천에 다시
    묻지 않는다(실행이 받아 둔 것)."""
    found = services.by_slug(slug, db)
    mirror = Workdir(get_settings().relay_work_dir, slug).load_mirror()
    rows = mirror.records()
    return {
        "columns": list(found.columns),
        "column_paths": found.columns,
        "total": len(rows),
        "rows": [{"key": one.key, **dict(one.values)} for one in rows[:limit]],
        "note": ""
        if rows
        else "아직 받은 것이 없습니다 — 먼저 「지금 실행」 으로 원천을 받으세요.",
    }


@router.get("/pipelines/{slug}/target-schema")
def target_schema(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """대상 SP 의 정의 — 레시피가 만드는 타입 · 그 칸(자료형 · 고를 값 · 참조 대상)."""
    found = services.by_slug(slug, db)
    schema, note = services.target_schema(found, services.secrets_env())
    wanted = set(found.types) | {"plm_project", "plm_task", "plm_model", "plm_test_site"}
    return {
        "target": found.target_name,
        "server": found.target_server,
        "note": note,
        "types": {
            slug_: props
            for slug_, props in schema.items()
            if slug_ in wanted or not found.types
        },
    }


# --- 정의의 판 — 원천 · 대상 · 일정. MCP 가 탐색 · 초안 · 시험, 사람이 화면에서 켠다 ---


@router.get("/adapter-kinds")
def adapter_kinds(_: User = Depends(require_system_admin)) -> dict[str, Any]:
    """어댑터 종류마다 적을 칸 · 기본값 · 설명 — 코드의 설명을 그대로."""
    return definitions.kinds()


@router.post("/discover")
def source_discover(
    body: DiscoverIn,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """어댑터 설정으로 원천에 닿아 칸의 자리 · 보기 값 · 열 제안을 받는다 — 저장하지 않는다.
    부를 수 있는 주소 · 파일 폴더는 울타리 안만(`guard`)."""
    return definitions.discover(db, body.adapter)


def _summary(body: dict[str, Any]) -> str:
    adapter = dict(body.get("adapter") or {})
    what = (
        adapter.get("entity_set")
        or adapter.get("path")
        or adapter.get("table")
        or str(adapter.get("location") or adapter.get("url") or "").rsplit("/", 1)[-1]
    )
    target = dict(body.get("target") or {})
    return (
        f"{adapter.get('kind', '?')} {what} → {target.get('name') or target.get('server', '')}"
        f" · source {body.get('source', '')}"
    )


def _definition_out(row: RelayDefinition) -> DefinitionOut:
    return DefinitionOut(
        version=row.version,
        status=row.status,
        note=row.note,
        client=row.client,
        created_at=row.created_at.isoformat(),
        activated_at=row.activated_at.isoformat() if row.activated_at else None,
        summary=_summary(row.body),
    )


@router.get("/pipelines/{slug}/definitions", response_model=list[DefinitionOut])
def definition_versions(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[DefinitionOut]:
    """그 slug 의 정의 판 — 새 파이프라인이면 아직 없을 수 있다(빈 목록)."""
    return [_definition_out(one) for one in definitions.versions(db, slug)]


@router.get("/pipelines/{slug}/definitions/{version}")
def definition_body(
    slug: str,
    version: int,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = definitions.get(db, slug, version)
    return {**_definition_out(row).model_dump(), "body": row.body}


@router.get("/pipelines/{slug}/definition-file")
def definition_file(slug: str, _: User = Depends(require_system_admin)) -> dict[str, Any]:
    """같은 slug 의 정의 파일 — 켜진 판이 없을 때 도는 것. 초안을 지을 출발점이다."""
    for one in services.catalog():
        if one.slug == slug:
            return {"path": str(one.path), "body": one.body, "problems": one.problems}
    return {"path": "", "body": None, "problems": []}


@router.post("/pipelines/{slug}/definitions", response_model=DefinitionSaved, status_code=201)
def definition_draft(
    slug: str,
    body: DefinitionIn,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DefinitionSaved:
    """초안 — 새 slug 면 새 파이프라인(켜기 전까지 돌지 않는다). 비밀은 거절한다."""
    row, problems = definitions.save_draft(db, user, request, slug, body.body, body.note)
    return DefinitionSaved(version=row.version, problems=problems)


@router.post("/pipelines/{slug}/definitions/{version}/test")
def definition_test(
    slug: str,
    version: int,
    body: DefinitionTestIn | None = None,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """그 판으로 원천을 받아 보고 · 대상(판 · 토큰 · 타입)을 보고 · 레시피로 바꿔 본다 —
    **대상에 아무것도 안 보낸다.**"""
    chosen = body or DefinitionTestIn()
    return definitions.trial(
        db, slug, version, recipe_version=chosen.recipe_version, limit=chosen.limit
    )


@router.post("/pipelines/{slug}/definitions/{version}/activate", response_model=DefinitionOut)
def definition_activate(
    slug: str,
    version: int,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DefinitionOut:
    """켠다 — **화면에서 사람만**(토큰이면 거절). 다음 실행부터 이 판이 돈다."""
    return _definition_out(definitions.activate(db, user, request, slug, version))


@router.post("/pipelines/{slug}/definitions/{version}/discard", response_model=DefinitionOut)
def definition_discard(
    slug: str,
    version: int,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DefinitionOut:
    """초안을 버린다 — 초안만. 새 파이프라인의 초안을 다 버리면 지도에서 빠진다. 토큰도
    된다."""
    return _definition_out(definitions.discard(db, user, slug, version))
