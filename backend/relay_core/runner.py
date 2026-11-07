"""실행 한 바퀴 — 받기 → 미러 → 변환 → 비교 → 미리보기. 적용은 따로(`apply`).

1. 대기 중인 계획이 있으면 먼저 「적용됐나」 를 묻는다 — 됐으면 그 출력이 새 기준이다.
2. 원천에서 받는다(워터마크 뒤로). 새 미러를 임시 파일에 쓰고 **받기 점검**(`intake` —
   대량 삭제 · 빈 칸)을 본다. 걸리면 아무것도 반영하지 않고 멈춘다(사람이 「확인 후
   진행」). 지나가면 미러를 바꿔 끼우고, 끝까지 받았으면 워터마크를 저장한다.
3. 미러 **전체**를 레시피로 변환한다 — 표를 메모리에 들지 않고 식별자로 나눠서
   (`relay_core.table`). 미해결이 있으면 멈춘다 — 레시피를 고친다.
4. 지난번 **적용된** 출력과 견준다(이것도 나눠서 — `diff.delta`). 보낼 것이 없으면 끝.
5. 보낼 것이 대기 중인 계획과 같으면 새로 올리지 않는다 — 매일 밤 같은 계획이 쌓이지 않게.
   다르면 미리보기를 올리고 그것이 새 대기 계획이 된다. 크면 **나눠 보낸다**(`parts` — 첫
   조각만 미리 보고, 「적용」 이 조각마다 미리 보기 → 적용을 잇는다).

실행마다 **실행 추이**(`trend.json`)에 한 줄을 남긴다 — 받은 행 · 미러 · 계획의 건수 · 걸린
시간. 실패한 실행도 남긴다(어디까지 갔는지와 함께).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from relay_core import compute, intake, parts, sp
from relay_core.convert import transform, write_source
from relay_core.definition import Pipeline, Target
from relay_core.diff import Delta, delta
from relay_core.state import MirrorFile, Pending, State, Workdir, stamp
from relay_core.submit import Plan, Submitter, digest_of


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


def counts_of(found: Delta) -> dict[str, Any]:
    """비교 결과의 수 — 타입마다 생성 · 고침 · 그대로 · 무덤."""
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


def settle_pending(state: State, submit: Submitter) -> str:
    """대기 계획이 적용됐으면 기준으로 올린다 — 실행 · 대조가 먼저 부른다(SP 화면에서 적용한
    것을 모른 채 견주지 않게). 나눠 보내는 계획이면 **그 조각만** 들어간 것이다 — 다음 조각으로
    넘기고(아직 미리 보지 않음), 마지막 조각이면 기준으로 올린다."""
    pending = state.pending
    if pending is None or not pending.job_id:
        return ""
    if not submit.applied(pending.job_id):
        return ""
    if pending.part + 1 < pending.total:
        number, total = pending.part + 1, pending.total
        state.history.append(
            {
                "at": _now(),
                "event": "part_applied",
                "job_id": pending.job_id,
                "part": number,
                "parts": total,
            }
        )
        pending.part, pending.job_id, pending.ok = number, "", True
        pending.summary = (
            f"조각 {number}/{total} 이 적용됐습니다(대상 화면에서) — 「적용」 이 나머지 "
            f"{total - number}개를 차례로 미리 보고 적용합니다."
        )
        return f"나눠 보내는 계획의 조각 {number}/{total} 이 적용됐습니다. "
    state.applied_run, state.applied_at = pending.run, _now()
    state.history.append({"at": _now(), "event": "applied", "job_id": pending.job_id})
    state.pending = None
    return f"대기하던 계획({pending.job_id[:8]})이 적용됐습니다 — 새 기준입니다. "


def fold(work: Workdir, state: State) -> None:
    """나눠 보내는 계획이 **일부만** 들어갔으면 그 조각을 기준에 접는다(`parts.fold`) — 새
    실행 · 대조가 기준을 보기 전에. 보정 계획은 기준 그대로를 다시 보내는 것이라 접을 것이
    없다(기준이 곧 목표다)."""
    pending = state.pending
    if pending is None or pending.part <= pending.folded:
        return
    if pending.origin == "run":
        out = f"{pending.run}-partial"
        parts.fold(
            work.path(state.applied_run) if state.applied_run else None,
            work.path(pending.run),
            [work.path(one) for one in pending.parts[pending.folded : pending.part]],
            work.path(out),
        )
        state.applied_run = out
    pending.folded = pending.part


#: 조각 상한의 단위(바이트) — 시험은 작게 바꿔 작은 묶음을 나눠 본다.
MB = 1024 * 1024


def propose(submit: Submitter, send: Path, target: Target) -> tuple[Plan, parts.Split]:
    """보낼 묶음을 대상에 올린다 — 조각 상한을 넘으면 나눠 **첫 조각만** 미리 본다."""
    cut = parts.split(send, target.part_mb * MB)
    plan = submit.preview(cut.parts[0] if cut.parts else send)
    return plan, cut


def pending_of(
    plan: Plan,
    cut: parts.Split,
    work: Workdir,
    *,
    run: str,
    send: str,
    origin: str = "run",
    part_mb: int = parts.DEFAULT_MB,
) -> Pending:
    summary = plan.summary
    total = len(cut.parts)
    if total:
        summary = (
            f"나눠 보냅니다 — 조각 {total}개(본문 {cut.size / 1e6:,.0f}MB · 조각마다 "
            f"{part_mb}MB · 선 {parts.MAX_EDGES:,}줄 이하). 「적용」 한 번이 조각을 차례로 "
            "미리 보고 적용합니다.\n"
            f"조각 1/{total}: {plan.summary}"
        )
    return Pending(
        run=run,
        send=send,
        job_id=plan.job_id,
        ok=plan.ok,
        digest=cut.digest or plan.digest,
        summary=summary,
        at=_now(),
        origin=origin,
        parts=[one.relative_to(work.root).as_posix() for one in cut.parts],
    )


log = logging.getLogger(__name__)


def _totals(counts: dict[str, Any]) -> dict[str, int]:
    """계획의 건수 — 객체 · 관계를 합쳐 생성 · 변경 · 삭제."""
    out = {"created": 0, "changed": 0, "gone": 0}
    for group in counts.values():
        for one in (group or {}).values():
            for name in out:
                out[name] += int(one.get(name, 0))
    return out


def _record(work: Workdir, status: str, sheet: dict[str, Any], seconds: float) -> None:
    """실행 추이에 한 줄 — **실패해도 실행은 실패가 아니다**(할 일은 했다)."""
    entry = {"at": _now(), "status": status, "seconds": round(seconds, 1), **sheet}
    try:
        work.add_trend(entry)
    except (OSError, ValueError):
        log.warning("실행 추이를 쓰지 못했습니다: %s", work.root, exc_info=True)


def run(
    pipeline: Pipeline,
    work: Workdir,
    submit: Submitter | None = None,
    *,
    options: dict[str, str] | None = None,
    accept: intake.Accept | None = None,
    full: bool = False,
) -> Outcome:
    """한 바퀴. `accept` 는 사람이 「확인 후 진행」 으로 지나가게 한 받기 점검의 멈춤이다 —
    그 건수 · 칸까지만 지나간다(`intake.Accept.covers`). `full` 이면 워터마크를 두고 **전량을
    다시 받는다** — 어댑터의 칸을 고친 뒤 이미 지나간 행까지 다시 읽을 때. 안 온 것은 삭제로
    읽히므로 받기 점검(대량 삭제)이 그대로 지킨다."""
    started = time.monotonic()
    sheet: dict[str, Any] = {}
    try:
        outcome = _run(
            pipeline, work, submit, options=options, accept=accept, full=full, sheet=sheet
        )
    except Exception:
        _record(work, "failed", sheet, time.monotonic() - started)
        raise
    sheet.pop("stage", None)  # 어디까지 갔나는 실패한 실행에만 뜻이 있다
    if outcome.counts:
        sheet["plan"] = _totals(outcome.counts)
    _record(work, outcome.status, sheet, time.monotonic() - started)
    return outcome


def _held(
    work: Workdir, state: State, found: intake.Findings, note: str, sheet: dict[str, Any]
) -> Outcome:
    """받기 점검이 멈춘다 — 미러 · 워터마크 · 증분 횟수는 그대로(다음 실행이 같은 것을 다시
    받는다). 까닭을 상태에 적어 화면이 「확인 후 진행」 을 낸다."""
    reasons = found.reasons()
    state.held = {"at": _now(), "findings": found.to_json(), "reasons": reasons}
    state.history.append({"at": _now(), "event": "held", "reasons": reasons})
    work.save_state(state)
    sheet["held"] = True
    return Outcome(
        "blocked",
        note
        + "받기 점검에서 멈췄습니다 — 미러 · 워터마크에 반영하지 않았습니다. "
        + " ".join(reasons)
        + " 원천 · 어댑터를 수정하면 다음 실행이 지나가고, 맞는 변경이면 화면에서 「확인 후 "
        "진행」 을 클릭하세요.",
    )


def _run(
    pipeline: Pipeline,
    work: Workdir,
    submit: Submitter | None,
    *,
    options: dict[str, str] | None,
    accept: intake.Accept | None,
    full: bool,
    sheet: dict[str, Any],
) -> Outcome:
    submit = submit or Submitter(pipeline.target)
    state = work.load_state()
    note = settle_pending(state, submit)

    since = None if full else state.watermark
    if full:
        note += "전량을 다시 받습니다(워터마크를 두고). "
    # **삭제 표시가 없는 원천은 전량 날에 사라진 것을 안다**(부재 대조) — 증분 `full_every`
    # 번마다 한 번.
    source = pipeline.adapter.source
    full_every = int(getattr(source, "full_every", 0) or 0)
    due = full_every and state.incrementals + 1 >= full_every
    if since and due and not getattr(source, "deleted", None):
        since = None
    sheet["stage"] = "받기"
    fetched = pipeline.adapter.fetch(since)
    full, complete, watermark = fetched.full, fetched.complete, fetched.watermark
    # 미러는 파일에서 흘려 이어 쓴다 — 메모리에는 받은 것만 든다(원천이 수십만 행이면 미러
    # 한 벌이 GB 다). 먼저 임시 파일에만 쓰고 받기 점검을 본다.
    sheet["stage"] = "미러"
    staged = work.stage_mirror(fetched)
    took, size = staged.applied, staged.size
    sheet.update(
        received=len(fetched.records),
        full=full,
        mirror=size,
        added=took.added,
        changed=took.changed,
        removed=took.removed,
    )
    note += (
        f"받음 {len(fetched.records)}건(추가 {took.added} · 변경 {took.changed} · "
        f"그대로 {took.unchanged} · 삭제 {took.removed}) · 미러 {size}건. "
    )
    del fetched
    checked = intake.inspect(pipeline.intake, staged.tally)
    if checked.held and not (accept is not None and accept.covers(checked)):
        staged.discard()
        sheet["mirror"] = staged.tally.before  # 반영하지 않았다 — 미러는 그대로다
        return _held(work, state, checked, note, sheet)
    staged.commit()
    if checked.held and accept is not None:
        state.history.append(
            {
                "at": _now(),
                "event": "held_accepted",
                "by": accept.by,
                "reasons": checked.reasons(),
            }
        )
        note += "받기 점검의 멈춤을 확인하고 진행했습니다. "
    state.held = None
    state.incrementals = 0 if full else state.incrementals + 1
    if complete:
        state.watermark = watermark
    work.save_state(state)
    if size == 0:
        return Outcome("nothing", note + "미러가 비어 있습니다.")
    sheet["stage"] = "변환"

    when = stamp()
    run_folder = work.path(f"runs/{when}")
    # 계산 칸은 변환할 때 미러 전체에 얹는다 — 규칙을 바꾼 날 옛 행도 같이 바뀐다.
    source = write_source(
        compute.wrap(MirrorFile(work, size), pipeline.computed),
        pipeline.recipe,
        run_folder,
        work=work.path("work"),
    )
    # 참조 대조(`match`)는 대상 SP 의 식별자 · 별칭으로 푼다 — 보낼 곳과 같은 주소 · 토큰.
    made = transform(
        source,
        pipeline.recipe,
        run_folder,
        server=pipeline.target.server,
        token=pipeline.target.token,
    )
    if not made.clean:
        state.history.append({"at": _now(), "event": "blocked", "run": f"runs/{when}"})
        work.save_state(state)
        return Outcome(
            "blocked",
            note + f"레시피 변환에 미해결이 있습니다 — {made.run}/unresolved.json · "
            "table-report.txt 를 보고 레시피를 수정하세요.",
        )

    sheet["stage"] = "비교"
    pending = state.pending
    if pending is not None and pending.origin == "run" and pending.part > 0:
        # **나눠 보내는 계획이 일부 들어가 있다.** 원천이 그대로면 그 계획을 잇는다 —
        # 바뀌었으면 들어간 조각을 기준에 접고 거기서 다시 견준다(들어간 것을 모른 채
        # 견주면 무덤이 빠진다).
        if digest_of(made.run) == digest_of(work.path(pending.run)):
            work.save_state(state)
            return Outcome(
                "waiting",
                note + f"나눠 보내는 계획의 조각 {pending.part}/{pending.total} 까지 "
                "적용됐습니다 — 「적용」 으로 나머지를 보냅니다.",
            )
        fold(work, state)
        superseded = True
    else:
        superseded = False

    baseline = work.path(state.applied_run) if state.applied_run else None
    found = delta(
        baseline,
        made.run,
        work.path(f"sends/{when}"),
        source=pipeline.source,
        options=options,
        scratch=work.path(f"work/{when}.send.spill"),
    )
    counts = counts_of(found)
    if found.empty:
        if superseded:
            # 들어간 조각까지로 이미 원천과 같다 — 남은 조각은 보낼 것이 아니다.
            state.pending = None
        state.history.append(
            {"at": _now(), "event": "nothing", "run": f"runs/{when}", "counts": counts}
        )
        work.save_state(state)
        return Outcome(
            "nothing",
            note + "지난번 적용된 것과 같습니다 — 보낼 것이 없습니다.",
            counts=counts,
        )

    # 오류가 난 계획은 기다리게 두지 않는다 — 대상 쪽 원인(정의 · 권한)을 고친 뒤 「다시 실행」
    # 이 새로 미리 본다.
    if state.pending and state.pending.ok and state.pending.digest == digest_of(found.run):
        return Outcome(
            "waiting",
            note + f"같은 계획({state.pending.job_id[:8]})이 적용을 기다립니다 — "
            "새로 생성하지 않았습니다.",
            link=f"{pipeline.target.server}/jobs?job={state.pending.job_id}",
            counts=counts,
        )

    sheet["stage"] = "미리 보기"
    plan, cut = propose(submit, found.run, pipeline.target)
    state.pending = pending_of(
        plan,
        cut,
        work,
        run=f"runs/{when}",
        send=f"sends/{when}",
        part_mb=pipeline.target.part_mb,
    )
    event: dict[str, Any] = {
        "at": _now(),
        "event": "planned",
        "job_id": plan.job_id,
        "ok": plan.ok,
        "counts": counts,
    }
    if cut.parts:
        event["parts"] = len(cut.parts)
    state.history.append(event)
    work.save_state(state)
    status = "planned" if plan.ok else "blocked"
    return Outcome(status, note + state.pending.summary, link=plan.link, counts=counts)


def apply(
    pipeline: Pipeline,
    work: Workdir,
    submit: Submitter | None = None,
    *,
    by: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Outcome:
    """대기 중인 계획을 적용한다 — 사람이 계획을 본 뒤에 부른다(또는 자동 적용 기준을 넘었을
    때 — `by="auto"`, 이력에 남는다. `autoapply`).

    나눠 보내는 계획이면 **조각마다 미리 보기 → 적용**을 차례로 잇는다(`progress(조각, 전체)`).
    조각이 들어갈 때마다 상태를 적어 두어, 도중에 멈춰도 다음 「적용」 이 거기서 잇는다. 뒤
    조각의 미리 보기에 오류가 나면 멈추고 그 조각의 계획을 보인다 — 앞 조각들은 들어가 있다."""
    submit = submit or Submitter(pipeline.target)
    state = work.load_state()
    pending = state.pending
    if pending is None:
        return Outcome("nothing", "적용을 기다리는 계획이 없습니다.")
    if not pending.ok and pending.part == 0:
        return Outcome(
            "blocked", "그 계획에는 오류가 있어 적용하지 않습니다 — 다시 실행하세요."
        )
    total = pending.total
    note = ""
    while True:
        number = pending.part + 1
        if progress is not None:
            progress(number, total)
        if not pending.job_id or not pending.ok:
            # 이 조각은 아직 미리 보지 않았다(또는 오류였다) — 앞 조각이 들어간 지금 본다.
            plan = submit.preview(work.path(pending.parts[pending.part]))
            pending.job_id, pending.ok, pending.at = plan.job_id, plan.ok, _now()
            pending.summary = f"조각 {number}/{total}: {plan.summary}"
            if not plan.ok:
                state.history.append(
                    {
                        "at": _now(),
                        "event": "blocked",
                        "job_id": plan.job_id,
                        "part": number,
                        "parts": total,
                    }
                )
                work.save_state(state)
                return Outcome(
                    "blocked",
                    f"조각 {number}/{total} 의 미리 보기에 오류가 있어 멈췄습니다 — 앞 조각 "
                    f"{number - 1}개는 적용됐습니다. 원인을 고친 뒤 「적용」 이 이 조각부터 "
                    f"다시 보냅니다. {plan.summary}",
                    link=plan.link,
                )
            work.save_state(state)
        if submit.applied(pending.job_id):
            note = "이미 적용돼 있었습니다(화면에서 적용한 듯합니다). "
        else:
            result = submit.apply(pending.job_id)
            note = f"적용했습니다 — {result.get('summary') or summarize(result)} "
        if number >= total:
            break
        event: dict[str, Any] = {
            "at": _now(),
            "event": "part_applied",
            "job_id": pending.job_id,
            "part": number,
            "parts": total,
        }
        if by:
            event["by"] = by
        state.history.append(event)
        pending.part, pending.job_id = number, ""
        work.save_state(state)
    state.applied_run, state.applied_at = pending.run, _now()
    done: dict[str, Any] = {"at": _now(), "event": "applied", "job_id": pending.job_id}
    if by:
        done["by"] = by
    if total > 1:
        done["parts"] = total
        note = f"조각 {total}개를 차례로 미리 보고 적용했습니다 — 마지막 조각: {note}"
    state.history.append(done)
    state.pending = None
    work.save_state(state)
    return Outcome("applied", note.strip())
