"""파이프라인 실행을 **작업**으로 — 화면의 「지금 실행」 · 「적용」 과 (나중의) 타이머가 같은
길을 쓴다.

작업 워커가 `relay_core` 의 같은 함수(`runner.run` · `runner.apply`)를 부른다 — CLI 와 다른
길이 없다. 결과의 `summary` 를 작업 화면이 그대로 보인다.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.modules.jobs import kinds
from app.modules.relay import recipes, services
from relay_core import autoapply, runner
from relay_core.definition import DefinitionError, load_body
from relay_core.state import Workdir


def _run(work: kinds.Work, *, apply: bool) -> dict[str, Any]:
    slug = str(work.params.get("slug") or "")
    work.progress("정의 읽기", 0, 0)
    found = services.by_slug(slug, work.db)
    if found.draft:
        raise DefinitionError(f"{slug} 의 정의가 아직 초안입니다 — 화면에서 켜야 돕니다.")
    if found.problems:
        raise DefinitionError(
            "정의에 문제가 있어 돌지 않습니다: " + " / ".join(found.problems)
        )
    # **켜진 레시피 판이 있으면 그것이 돈다** — 없으면 정의 파일의 `recipe`. 정의도 같다 —
    # 켜진 정의 판이 있으면 그것(`found.body`), 없으면 정의 파일.
    pipeline = load_body(
        found.body,
        base=None,
        env=services.secrets_env(),
        recipe=recipes.materialize(work.db, slug) or found.recipe,
        path=found.path,
        file_roots=get_settings().relay_file_root_paths,
    )
    folder = Workdir(get_settings().relay_work_dir, pipeline.slug)
    work.progress("적용" if apply else "받기 · 변환 · 비교 · 미리보기", 0, 0)
    if apply:
        outcome = runner.apply(pipeline, folder)
    else:
        human = str(work.params.get("human_edits") or "")
        options = {"human_edits": human} if human in ("keep", "overwrite") else None
        outcome = runner.run(pipeline, folder, options=options)
        # **자동 적용은 타이머의 실행에만** — 사람의 「지금 실행」 · MCP 의 실행은 계획만
        # 올린다.
        if work.params.get("by") == "timer" and found.auto_apply is not None:
            work.progress("자동 적용 기준 보기", 0, 0)
            outcome = autoapply.after_run(pipeline, folder, outcome, found.auto_apply)
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
