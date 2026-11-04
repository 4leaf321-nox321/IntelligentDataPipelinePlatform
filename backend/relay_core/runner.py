"""실행 한 바퀴 — 받기 → 미러 → 변환 → 비교 → 미리보기. 적용은 따로(`apply`).

1. 대기 중인 계획이 있으면 먼저 「적용됐나」 를 묻는다 — 됐으면 그 출력이 새 기준이다.
2. 원천에서 받는다(워터마크 뒤로). 끝까지 받은 뒤에만 미러 · 워터마크를 저장한다.
3. 미러 **전체**를 레시피로 변환한다. 미해결이 있으면 멈춘다 — 레시피를 고친다.
4. 지난번 **적용된** 출력과 견준다. 보낼 것이 없으면 끝.
5. 보낼 것이 대기 중인 계획과 같으면 새로 올리지 않는다 — 매일 밤 같은 계획이 쌓이지 않게.
   다르면 미리보기를 올리고 그것이 새 대기 계획이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from relay_core import sp
from relay_core.convert import convert
from relay_core.definition import Pipeline
from relay_core.diff import Delta, delta
from relay_core.state import Pending, State, Workdir, stamp
from relay_core.submit import Submitter, digest_of


@dataclass
class Outcome:
    status: str
    """nothing(보낼 것 없음) · planned(미리보기 올림) · waiting(같은 계획 대기 중) ·
    blocked(미해결 · 계획 오류) · applied(적용함)."""
    message: str
    link: str = ""
    counts: dict[str, Any] = field(default_factory=dict)


def summarize(result: dict[str, Any]) -> str:
    """SP 작업 결과의 한 줄 — 틀의 도구가 쓰는 요약을 그대로."""
    return str(sp.pipeline.summarize(result)).replace("\n", " / ") if result else ""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _counts(found: Delta) -> dict[str, Any]:
    return {
        kind: {
            slug: {
                "created": one.created,
                "changed": one.changed,
                "unchanged": one.unchanged,
                "gone": one.gone,
            }
            for slug, one in group.items()
        }
        for kind, group in (("objects", found.objects), ("relations", found.relations))
    }


def _settle_pending(state: State, submit: Submitter) -> str:
    """대기 계획이 적용됐으면 기준으로 올린다."""
    pending = state.pending
    if pending is None:
        return ""
    if submit.applied(pending.job_id):
        state.applied_run, state.applied_at = pending.run, _now()
        state.history.append({"at": _now(), "event": "applied", "job_id": pending.job_id})
        state.pending = None
        return f"대기하던 계획({pending.job_id[:8]})이 적용됐습니다 — 새 기준입니다. "
    return ""


def run(
    pipeline: Pipeline,
    work: Workdir,
    submit: Submitter | None = None,
    *,
    options: dict[str, str] | None = None,
) -> Outcome:
    submit = submit or Submitter(pipeline.target)
    state = work.load_state()
    note = _settle_pending(state, submit)

    fetched = pipeline.adapter.fetch(state.watermark)
    mirror = work.load_mirror()
    took = mirror.apply(fetched)
    work.save_mirror(mirror)
    if fetched.complete:
        state.watermark = fetched.watermark
    note += (
        f"받음 {len(fetched.records)}건(새 {took.added} · 바뀜 {took.changed} · "
        f"그대로 {took.unchanged} · 빠짐 {took.removed}) · 미러 {len(mirror)}건. "
    )
    work.save_state(state)
    if len(mirror) == 0:
        return Outcome("nothing", note + "미러가 비어 있습니다.")

    when = stamp()
    made = convert(mirror, pipeline.recipe, work.path(f"runs/{when}"), work=work.path("work"))
    if not made.clean:
        state.history.append({"at": _now(), "event": "blocked", "run": f"runs/{when}"})
        work.save_state(state)
        return Outcome(
            "blocked",
            note + f"레시피 변환에 미해결이 있습니다 — {made.run}/unresolved.json · "
            "table-report.txt 를 보고 레시피를 고치세요.",
        )

    baseline = work.path(state.applied_run) if state.applied_run else None
    found = delta(
        baseline, made.run, work.path(f"sends/{when}"), source=pipeline.source, options=options
    )
    counts = _counts(found)
    if found.empty:
        state.history.append({"at": _now(), "event": "nothing", "run": f"runs/{when}"})
        work.save_state(state)
        return Outcome(
            "nothing",
            note + "지난번 적용된 것과 같습니다 — 보낼 것이 없습니다.",
            counts=counts,
        )

    if state.pending and state.pending.digest == digest_of(found.run):
        return Outcome(
            "waiting",
            note + f"같은 계획({state.pending.job_id[:8]})이 적용을 기다립니다 — "
            "새로 올리지 않았습니다.",
            link=f"{pipeline.target.server}/jobs?job={state.pending.job_id}",
            counts=counts,
        )

    plan = submit.preview(found.run)
    state.pending = Pending(
        run=f"runs/{when}",
        send=f"sends/{when}",
        job_id=plan.job_id,
        ok=plan.ok,
        digest=plan.digest,
        summary=plan.summary,
        at=_now(),
    )
    state.history.append(
        {"at": _now(), "event": "planned", "job_id": plan.job_id, "ok": plan.ok}
    )
    work.save_state(state)
    status = "planned" if plan.ok else "blocked"
    return Outcome(status, note + plan.summary, link=plan.link, counts=counts)


def apply(pipeline: Pipeline, work: Workdir, submit: Submitter | None = None) -> Outcome:
    """대기 중인 계획을 적용한다 — 사람이 계획을 본 뒤에 부른다(또는 자동 적용 기준을 넘었을
    때)."""
    submit = submit or Submitter(pipeline.target)
    state = work.load_state()
    pending = state.pending
    if pending is None:
        return Outcome("nothing", "적용을 기다리는 계획이 없습니다.")
    if not pending.ok:
        return Outcome(
            "blocked", "그 계획에는 오류가 있어 적용하지 않습니다 — 다시 실행하세요."
        )
    if submit.applied(pending.job_id):
        note = "이미 적용돼 있었습니다(화면에서 적용한 듯합니다). "
    else:
        result = submit.apply(pending.job_id)
        note = f"적용했습니다 — {result.get('summary') or summarize(result)} "
    state.applied_run, state.applied_at = pending.run, _now()
    state.history.append({"at": _now(), "event": "applied", "job_id": pending.job_id})
    state.pending = None
    work.save_state(state)
    return Outcome("applied", note.strip())
