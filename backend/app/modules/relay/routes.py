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
from app.modules.relay import jobs as relay_jobs  # noqa: F401 — import 가 곧 작업 종류 등록
from app.modules.relay import recipes, services
from app.modules.relay.models import RelayRecipe
from app.modules.relay.schemas import (
    PipelineDetail,
    RecipeIn,
    RecipeOut,
    RecipeSaved,
    RelayMap,
)
from app.shared.auth import current_user, require_system_admin
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


def _queue(db: Session, user: User, kind: str, params: dict[str, str]) -> QueuedOut:
    services.by_slug(params["slug"])  # 없는 파이프라인이면 넣기 전에 404
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
    params = {"slug": slug}
    if body and body.human_edits:
        params["human_edits"] = body.human_edits
    return _queue(db, user, services.RUN_KIND, params)


@router.post("/pipelines/{slug}/apply", response_model=QueuedOut, status_code=202)
def pipeline_apply(
    slug: str, user: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> QueuedOut:
    return _queue(db, user, services.APPLY_KIND, {"slug": slug})


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
    services.by_slug(slug)
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


@router.get("/pipelines/{slug}/recipe-file")
def recipe_file(slug: str, _: User = Depends(require_system_admin)) -> dict[str, Any]:
    """정의 파일이 가리키는 레시피 — 켜진 판이 없을 때 도는 것. 초안을 지을 출발점이다."""
    found = services.by_slug(slug)
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
    found = services.by_slug(slug)
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
    return recipes.trial(db, services.by_slug(slug), version)


@router.post("/pipelines/{slug}/recipes/{version}/activate", response_model=RecipeOut)
def recipe_activate(
    slug: str,
    version: int,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RecipeOut:
    """켠다 — **화면에서 사람만**(토큰이면 거절). 다음 실행부터 이 판이 돈다."""
    return _recipe_out(recipes.activate(db, user, request, services.by_slug(slug), version))


# --- 레시피를 짓는 데 보는 것 — 원천 표본 · 대상의 정의 ---------------------------------


@router.get("/pipelines/{slug}/sample")
def source_sample(
    slug: str,
    limit: int = Query(default=20, ge=1, le=200),
    _: User = Depends(require_system_admin),
) -> dict[str, Any]:
    """미러(마지막으로 받은 원천)의 앞 몇 줄 — 레시피가 읽는 **평평한 열** 그대로. 원천에 다시
    묻지 않는다(실행이 받아 둔 것)."""
    found = services.by_slug(slug)
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
def target_schema(slug: str, _: User = Depends(require_system_admin)) -> dict[str, Any]:
    """대상 SP 의 정의 — 레시피가 만드는 타입 · 그 칸(자료형 · 고를 값 · 참조 대상)."""
    found = services.by_slug(slug)
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
