"""파이프라인 실행을 **작업**으로 — 화면의 「지금 실행」 · 「적용」 · 「대조」 와 타이머가 같은
길을 쓴다.

작업 워커가 `relay_core` 의 같은 함수(`runner.run` · `runner.apply` · `reconcile.compare` ·
`reconcile.resync`)를 부른다 — CLI 와 다른 길이 없다. 결과의 `summary` 를 작업 화면이 그대로
보인다.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.modules.jobs import kinds
from app.modules.relay import alerts, recipes, services
from relay_core import autoapply, reconcile, runner
from relay_core.definition import DefinitionError, Pipeline, load_body
from relay_core.describe import Described
from relay_core.state import Workdir

log = logging.getLogger(__name__)


def _load(work: kinds.Work) -> tuple[Described, Pipeline, Workdir]:
    slug = str(work.params.get("slug") or "")
    work.progress("정의 읽기", 0, 0)
    found = services.by_slug(slug, work.db)
    if found.draft:
        raise DefinitionError(
            f"{slug} 의 정의가 아직 초안입니다 — 화면에서 정의 판을 사용해야 실행됩니다."
        )
    if found.problems:
        raise DefinitionError(
            "정의에 문제가 있어 실행되지 않습니다: " + " / ".join(found.problems)
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
    return found, pipeline, Workdir(get_settings().relay_work_dir, pipeline.slug)


def _tidy(folder: Workdir) -> int:
    """지난 실행을 정리한다(`RELAY_KEEP_RUNS`) — 실행마다 `runs/` · `sends/` · `work/` 가
    하나씩 늘어 안 지우면 디스크가 찬다. **정리가 실패해도 작업은 실패가 아니다** — 할 일은
    했다."""
    try:
        return len(folder.prune(get_settings().relay_keep_runs))
    except OSError:
        log.warning("작업 폴더 정리 실패: %s", folder.root, exc_info=True)
        return 0


def _run(work: kinds.Work, *, apply: bool) -> dict[str, Any]:
    found, pipeline, folder = _load(work)
    work.progress("적용" if apply else "받기 · 변환 · 비교 · 미리 보기", 0, 0)
    reconciled = False
    if apply:
        pending = folder.load_state().pending

        def shown(part: int, total: int) -> None:
            # 나눠 보내는 계획은 조각마다 미리 보기 → 적용 — 어디쯤인지 작업 화면에 보인다.
            if total > 1:
                work.progress(f"적용 — 조각 {part}/{total}", part, total)

        outcome = runner.apply(pipeline, folder, progress=shown)
        # **보정 계획을 적용했으면 바로 다시 견준다** — 안 그러면 다음 대조(며칠 뒤)까지 홈이
        # 「어긋남」 을 계속 말한다.
        if outcome.status == "applied" and pending and pending.origin == "reconcile":
            work.progress("다시 대조", 0, 0)
            again = reconcile.compare(pipeline, folder)
            outcome.message += f" · 다시 대조: {again.summary()}"
            reconciled = True
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
        # 대조 보고서를 새로 썼다 — 알림(`alerts`)이 대조 갈래도 본다.
        "reconciled": reconciled,
        "pruned": _tidy(folder),
    }


def run_job(work: kinds.Work) -> dict[str, Any]:
    return _run(work, apply=False)


def reconcile_job(work: kinds.Work) -> dict[str, Any]:
    """대조 — `plan` 이면 어긋난 줄의 보정 계획(미리보기)까지 올린다. 적용은 사람이."""
    _, pipeline, folder = _load(work)
    if work.params.get("plan"):
        work.progress("대조 · 보정 계획", 0, 0)
        outcome = reconcile.resync(pipeline, folder)
        return {
            "ok": outcome.status != "blocked",
            "status": outcome.status,
            "summary": f"[{outcome.status}] {outcome.message}",
            "link": outcome.link,
            "counts": outcome.counts,
            "pruned": _tidy(folder),
        }
    work.progress("대상 읽기 · 견주기", 0, 0)
    report = reconcile.compare(pipeline, folder)
    status = "problem" if report.problems else ("drift" if report.drift else "same")
    return {
        "ok": not report.problems,
        "status": status,
        "summary": f"[{status}] {report.summary()}",
        "drift": report.drift,
    }


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
        after=alerts.after_job,
    )
)
kinds.register(
    kinds.Kind(
        services.RECONCILE_KIND,
        "중계 대조",
        False,
        False,
        reconcile_job,
        allow_system=True,
        admin_only=True,
        after=alerts.after_job,
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
        after=alerts.after_job,
    )
)
