"""파이프라인 실행을 **작업**으로 — 화면의 「지금 실행」 · 「적용」 과 (나중의) 타이머가 같은
길을 쓴다.

작업 워커가 `relay_core` 의 같은 함수(`runner.run` · `runner.apply`)를 부른다 — CLI 와 다른
길이 없다. 결과의 `summary` 를 작업 화면이 그대로 보인다.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.config import get_settings
from app.modules.jobs import kinds
from app.modules.relay import recipes, services
from relay_core import runner
from relay_core.definition import load
from relay_core.state import Workdir


def _run(work: kinds.Work, *, apply: bool) -> dict[str, Any]:
    slug = str(work.params.get("slug") or "")
    found = services.by_slug(slug)
    work.progress("정의 읽기", 0, 0)
    pipeline = load(found.path, env=services.secrets_env())
    # **켜진 레시피 판이 있으면 그것이 돈다** — 없으면 정의 파일의 `recipe`.
    active = recipes.materialize(work.db, pipeline.slug)
    if active is not None:
        pipeline = replace(pipeline, recipe=active)
    folder = Workdir(get_settings().relay_work_dir, pipeline.slug)
    work.progress("적용" if apply else "받기 · 변환 · 비교 · 미리보기", 0, 0)
    if apply:
        outcome = runner.apply(pipeline, folder)
    else:
        human = str(work.params.get("human_edits") or "")
        options = {"human_edits": human} if human in ("keep", "overwrite") else None
        outcome = runner.run(pipeline, folder, options=options)
    return {
        "ok": outcome.status != "blocked",
        "status": outcome.status,
        "summary": f"[{outcome.status}] {outcome.message}",
        "link": outcome.link,
        "counts": outcome.counts,
    }


def run_job(work: kinds.Work) -> dict[str, Any]:
    return _run(work, apply=False)


def apply_job(work: kinds.Work) -> dict[str, Any]:
    return _run(work, apply=True)


kinds.register(
    kinds.Kind(
        services.RUN_KIND,
        "중계 실행",
        False,
        False,
        run_job,
        allow_system=True,
        admin_only=True,
    )
)
kinds.register(
    kinds.Kind(
        services.APPLY_KIND,
        "중계 계획 적용",
        False,
        False,
        apply_job,
        allow_system=True,
        admin_only=True,
    )
)
