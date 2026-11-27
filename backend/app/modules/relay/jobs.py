"""파이프라인 실행을 **작업**으로 — 화면의 「지금 실행」 · 「적용」 · 「대조」 · 「보낸 것
걷어내기」 와 타이머가 같은 길을 쓴다.

작업 워커가 `relay_core` 의 같은 함수(`runner.run` · `runner.apply` · `reconcile.compare` ·
`reconcile.resync` · `retract.retract`)를 부른다 — CLI 와 다른 길이 없다. 결과의 `summary` 를
작업 화면이 그대로 보인다.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from app.config import get_settings
from app.modules.jobs import kinds
from app.modules.relay import alerts, recipes, services
from relay_core import intake, isolate, retract, runner
from relay_core.definition import DefinitionError, Pipeline, load_body
from relay_core.describe import Described
from relay_core.state import Workdir

log = logging.getLogger(__name__)


def _load(work: kinds.Work) -> tuple[Described, Pipeline, Workdir]:
    slug = str(work.params.get("slug") or "")
    work.progress("정의 조회", 0, 0)
    found = services.by_slug(slug, work.db)
    if found.draft:
        raise DefinitionError(
            f"{slug}의 정의가 아직 초안입니다. 화면에서 정의 버전을 사용해야 실행됩니다."
        )
    if found.problems:
        raise DefinitionError("정의 오류로 실행할 수 없습니다: " + " / ".join(found.problems))
    # **켜진 레시피 판이 있으면 그것이 돈다** — 없으면 정의 파일의 `recipe`. 정의도 같다 —
    # 켜진 정의 판이 있으면 그것(`found.body`), 없으면 정의 파일.
    pipeline = load_body(
        found.body,
        base=None,
        env=services.secrets_env(),
        recipe=recipes.materialize(work.db, slug) or found.recipe,
        path=found.path,
        file_roots=get_settings().relay_file_root_paths,
        cert_root=get_settings().relay_cert_path,
        inbox=get_settings().relay_work_dir / slug / "inbox",
    )
    pipeline = replace(pipeline, origin=origin_base(pipeline.slug))
    return found, pipeline, Workdir(get_settings().relay_work_dir, pipeline.slug)


def origin_base(slug: str) -> dict[str, str]:
    """묶음에 실을 원천 기록의 바탕 — 이 설치의 이름과 화면 주소(SP ADR 0087). 엔진이
    실행 번호를 더한다(`runner.origin_for`). `trace` 는 파이프라인 상세의 「거꾸로 보기」 를
    그 객체로 연다 — SP 가 `{type}` · `{key}` 를 채운다."""
    settings = get_settings()
    out = {"system": settings.app_slug}
    public = settings.public_url.strip().rstrip("/")
    if public:
        page = f"{public}/relay/pipelines/{quote(slug, safe='')}"
        out["url"] = page
        out["trace"] = f"{page}?trace_type={{type}}&trace_key={{key}}"
    return out


def _engine(
    work: kinds.Work,
    target: str,
    *args: Any,
    label: str,
    progress_kw: str | None = None,
    on_progress: Any = None,
    **kwargs: Any,
) -> tuple[Any, dict[str, Any]]:
    """엔진 함수 하나를 **작업 프로세스에서** — (결과, 단계별 메모리). `RELAY_JOB_PROCESS` 가
    꺼져 있으면(시험) 워커 안에서 같은 함수를 부른다(시험이 바꿔 끼운 함수가 그대로 불린다).

    자식의 단계는 작업 진행에 적는다(심장박동 · 취소 확인 — 취소되면 `work.progress` 가 예외를
    내고, 그때 자식을 끝낸다)."""
    settings = get_settings()

    def stage(name: str) -> None:
        work.progress(name, 0, 0)

    if not settings.relay_job_process:
        fn = isolate._resolve(target)
        if progress_kw:
            kwargs[progress_kw] = on_progress
        return fn(*args, **kwargs), {}
    return isolate.call(
        target,
        *args,
        limit_mb=settings.relay_job_memory_mb,
        label=label,
        on_stage=stage,
        on_tick=stage,
        on_progress=on_progress,
        progress_kw=progress_kw,
        **kwargs,
    )


def _killed(folder: Workdir, refused: isolate.MemoryLimitExceeded) -> None:
    """상한을 넘어 끝낸 실행도 실행 추이에 남긴다 — 자식이 끝나 제 기록을 못 썼다."""
    try:
        folder.add_trend(
            {
                "at": runner._now(),
                "status": "failed",
                "seconds": 0,
                "stage": refused.stage,
                "memory": {refused.stage: refused.peak_mb},
                "peak_mb": refused.peak_mb,
            }
        )
    except (OSError, ValueError):
        log.warning("실행 추이를 쓰지 못했습니다: %s", folder.root, exc_info=True)


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
    work.progress("적용" if apply else "수신 · 변환 · 비교 · 미리 보기", 0, 0)
    reconciled = False
    if apply:
        pending = folder.load_state().pending

        def shown(part: int, total: int) -> None:
            # 나눠 보내는 계획은 조각마다 미리 보기 → 적용 — 어디쯤인지 작업 화면에 보인다.
            if total > 1:
                work.progress(f"적용(분할 번들 {part}/{total})", part, total)

        outcome, record = _engine(
            work,
            "relay_core.runner.apply",
            pipeline,
            folder,
            label="적용",
            progress_kw="progress",
            on_progress=shown,
        )
        outcome.memory = outcome.memory or record
        # **보정 계획을 적용했으면 바로 다시 견준다** — 안 그러면 다음 대조(며칠 뒤)까지 홈이
        # 「어긋남」 을 계속 말한다.
        if outcome.status == "applied" and pending and pending.origin == "reconcile":
            work.progress("재대조", 0, 0)
            again, _record = _engine(
                work, "relay_core.reconcile.compare", pipeline, folder, label="재대조"
            )
            outcome.message += f" · 재대조: {again.summary()}"
            reconciled = True
        if outcome.status == "applied" and pending and pending.origin == retract.ORIGIN:
            outcome.message += f" {retract.after_apply(folder.load_state().stopped)}"
    else:
        human = str(work.params.get("human_edits") or "")
        options = {"human_edits": human} if human in ("keep", "overwrite") else None
        try:
            outcome, _record = _engine(
                work,
                "relay_core.runner.run",
                pipeline,
                folder,
                label="실행",
                options=options,
                accept=_accept(work),
                full=bool(work.params.get("full")),
                incremental_plan=get_settings().relay_incremental,
            )
        except isolate.MemoryLimitExceeded as refused:
            _killed(folder, refused)
            raise
        # **자동 적용은 타이머의 실행에만** — 사람의 「지금 실행」 · MCP 의 실행은 계획만
        # 올린다.
        if work.params.get("by") == "timer" and found.auto_apply is not None:
            work.progress("자동 적용 기준 확인", 0, 0)
            outcome, _record = _engine(
                work,
                "relay_core.autoapply.after_run",
                pipeline,
                folder,
                outcome,
                found.auto_apply,
                label="자동 적용",
            )
    return {
        "ok": outcome.status != "blocked",
        "status": outcome.status,
        "summary": f"[{outcome.status}] {outcome.message}",
        "link": outcome.link,
        "counts": outcome.counts,
        **outcome.memory,
        # 대조 보고서를 새로 썼다 — 알림(`alerts`)이 대조 갈래도 본다.
        "reconciled": reconciled,
        "pruned": _tidy(folder),
    }


def _accept(work: kinds.Work) -> intake.Accept | None:
    """「확인 후 진행」 이 실어 온 받기 점검의 확인(`routes._accept`) — 없으면 None."""
    given = work.params.get("accept")
    if not isinstance(given, dict):
        return None
    return intake.Accept(
        deleted=int(given.get("deleted") or 0),
        columns=tuple(str(one) for one in given.get("columns") or []),
        by=str(given.get("by") or ""),
    )


def run_job(work: kinds.Work) -> dict[str, Any]:
    return _run(work, apply=False)


def _running_besides(work: kinds.Work, slug: str) -> str:
    """같은 파이프라인의 실행 · 적용 · 회수가 **지금 도나**(워커가 여럿이면 겹친다) — 그
    작업의 이름, 없으면 빈 글. 대기 중인 것은 이 대조 뒤에 돈다."""
    for one in services.busy(work.db, slug, but=work.job.id):
        if one.status == "running" and one.kind != services.RECONCILE_KIND:
            spec = kinds.get(one.kind)
            return spec.label if spec else one.kind
    return ""


def reconcile_job(work: kinds.Work) -> dict[str, Any]:
    """대조 — `plan` 이면 어긋난 줄의 보정 계획(미리보기)까지 올린다. 적용은 사람이.

    **적용보다 뒤에** — 같은 파이프라인의 실행 · 적용이 돌고 있거나 대상 SP 가 계획을 적용하는
    중이면 미룬다(`deferred` — 보고서 · 이력은 그대로, 타이머가 다음 틱에 다시 넣는다)."""
    _, pipeline, folder = _load(work)
    other = _running_besides(work, pipeline.slug)
    if other:
        return {
            "ok": True,
            "status": services.DEFERRED,
            "summary": f"[{services.DEFERRED}] 같은 파이프라인의 {other} 작업이 진행 중이라 "
            "대조를 연기했습니다. 작업이 끝난 후 다시 대조합니다.",
        }
    if work.params.get("plan"):
        work.progress("대조 · 보정 계획 생성", 0, 0)
        outcome, record = _engine(
            work, "relay_core.reconcile.resync", pipeline, folder, label="보정 계획 생성"
        )
        return {
            **record,
            "ok": outcome.status != "blocked",
            "status": outcome.status,
            "summary": f"[{outcome.status}] {outcome.message}",
            "link": outcome.link,
            "counts": outcome.counts,
            "pruned": _tidy(folder),
        }
    work.progress("대상 조회 · 비교", 0, 0)
    report, record = _engine(
        work, "relay_core.reconcile.compare", pipeline, folder, label="대조"
    )
    if report.deferred:
        return {
            **record,
            "ok": True,
            "status": services.DEFERRED,
            "summary": f"[{services.DEFERRED}] {report.deferred}",
        }
    status = "problem" if report.problems else ("drift" if report.drift else "same")
    return {
        **record,
        "ok": not report.problems,
        "status": status,
        "summary": f"[{status}] {report.summary()}",
        "drift": report.drift,
    }


def apply_job(work: kinds.Work) -> dict[str, Any]:
    return _run(work, apply=True)


def retract_job(work: kinds.Work) -> dict[str, Any]:
    """보낸 것 걷어내기 — 무덤만 든 계획(미리 보기)을 올리고 파이프라인을 멈춘다. 적용 · 다시
    켜기는 사람이. 선의 도착 쪽 타입은 대상의 관계 종류로 채운다(기준의 정의에 없을 때).
    지우기는 대상 SP 판이 받을 때만 — 옛 판은 칸을 무시하고 사용 중지하므로, 사람이 고른 것과
    다른 일이 조용히 일어나지 않게 여기서 멈춘다."""
    found, pipeline, folder = _load(work)
    work.progress("회수 계획 생성", 0, 0)
    action = str(work.params.get("action") or "deprecate")
    if action == "delete":
        version = services.target_version(found.target_server)
        if not retract.supports_delete(version):
            since = ".".join(map(str, retract.DELETE_SINCE))
            return {
                "ok": False,
                "status": "blocked",
                "summary": f"[blocked] 대상 SP({version or '버전 미확인'})는 삭제를 지원하지 "
                f"않습니다. 삭제는 SP {since} 이상에서 지원합니다. 사용 중지로 회수하거나 "
                "대상 SP를 업그레이드한 후 다시 선택하세요.",
            }
    model, _note = services.target_model(found, services.secrets_env(), work.db)
    ends = {
        str(one["slug"]): [str(x) for x in one["dst_type_slugs"]]
        for one in model["relations"]
        if one.get("slug") and one.get("dst_type_slugs")
    }
    try:
        scope = retract.Scope.of(work.params.get("scope") or {})
        outcome, record = _engine(
            work,
            "relay_core.retract.retract",
            pipeline,
            folder,
            scope,
            label="회수 계획 생성",
            by=str(work.params.get("who") or ""),
            action=action,
            ends=ends,
        )
        outcome.memory = outcome.memory or record
    except retract.RetractError as refused:
        outcome = runner.Outcome("blocked", str(refused))
    return {
        "ok": outcome.status != "blocked",
        "status": outcome.status,
        "summary": f"[{outcome.status}] {outcome.message}",
        "link": outcome.link,
        "counts": outcome.counts,
        "pruned": _tidy(folder),
    }


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
        services.RETRACT_KIND,
        "중계 회수",
        False,
        False,
        retract_job,
        allow_system=False,
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
        after=alerts.after_job,
    )
)
