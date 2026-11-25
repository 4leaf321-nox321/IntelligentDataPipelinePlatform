"""실행 한 바퀴 — 받기 → 미러 → 변환 → 비교 → 미리보기. 적용은 따로(`apply`).

1. 대기 중인 계획이 있으면 먼저 「적용됐나」 를 묻는다 — 됐으면 그 출력이 새 기준이다.
2. 원천에서 받는다(워터마크 뒤로). 새 미러를 임시 파일에 쓰고 **받기 점검**(`intake` —
   대량 삭제 · 빈 칸)을 본다. 걸리면 아무것도 반영하지 않고 멈춘다(사람이 「확인 후
   진행」). 지나가면 미러를 바꿔 끼우고, 끝까지 받았으면 워터마크를 저장한다.
3. 미러를 레시피로 변환한다 — **바뀐 행이 닿는 객체만** 다시 모아 지난 출력에 끼운다
   (`relay_core.incremental`). 이어 갈 수 없으면(첫 실행 · 레시피가 바뀜 …) 미러 전체를, 표를
   메모리에 들지 않고 식별자로 나눠서(`relay_core.table`). 결과는 바이트까지 같다. 미해결이
   있으면 멈춘다 — 레시피를 고친다.
4. 지난번 **적용된** 출력과 견준다 — 건드린 것만(`incremental.delta`), 기준이 이어지지 않으면
   출력 전체를(`diff.delta`). 보낼 것이 없으면 끝.
5. 보낼 것이 대기 중인 계획과 같으면 새로 올리지 않는다 — 매일 밤 같은 계획이 쌓이지 않게.
   다르면 미리보기를 올리고 그것이 새 대기 계획이 된다. 크면 **나눠 보낸다**(`parts` — 첫
   조각만 미리 보고, 「적용」 이 조각마다 미리 보기 → 적용을 잇는다).

실행마다 **실행 추이**(`trend.json`)에 한 줄을 남긴다 — 받은 행 · 미러 · 계획의 건수 · 걸린
시간. 실패한 실행도 남긴다(어디까지 갔는지와 함께).
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from relay_core import compute, incremental, intake, memory, parts, records, sp
from relay_core.convert import transform, write_source
from relay_core.definition import Pipeline, Target
from relay_core.diff import Delta, delta
from relay_core.state import MirrorFile, Pending, State, Workdir, stamp
from relay_core.submit import NotApplied, Plan, Submitter, digest_of


@dataclass
class Outcome:
    status: str
    """nothing(보낼 것 없음) · planned(미리보기 올림) · waiting(같은 계획 대기 중) ·
    blocked(미해결 · 계획 오류) · applied(적용함)."""
    message: str
    link: str = ""
    counts: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    """단계별 최고 메모리 — `{"memory": {단계: MB}, "peak_mb": MB}`(`relay_core.memory`)."""


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
            f"대상 화면에서 분할 번들 {number}/{total}의 적용을 확인했습니다. [적용]을 "
            f"클릭하면 나머지 {total - number}개를 차례로 미리 보기 후 적용합니다."
        )
        return f"분할 번들 {number}/{total}의 적용을 확인했습니다. "
    state.applied_run, state.applied_at = pending.run, _now()
    state.history.append({"at": _now(), "event": "applied", "job_id": pending.job_id})
    state.pending = None
    return (
        f"적용 대기 계획({pending.job_id[:8]})이 적용되었습니다. 해당 출력을 새 기준 출력으로 "
        "사용합니다. "
    )


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


def origin_for(pipeline: Pipeline, send: Path | str) -> dict[str, str] | None:
    """묶음에 실을 **원천 기록** — 이 설치 · 파이프라인 · 실행 번호(보내는 폴더의 이름 —
    `sends/<때>`, 보정 계획이면 `sends/<때>-resync`)와 화면 주소(SP ADR 0087).

    대상 SP 가 묶음 판과 그 판이 넣은 객체의 이력에 남기고, 이력이 `trace` 로 이 설치의
    「거꾸로 보기」 에 링크한다 — 예전에는 SP 이력에 「시스템 관리자 · 일괄 가져오기」 만
    남아 어느 원천의 어느 실행에서 왔는지 여기서만 볼 수 있었다. 나눠 보낸 조각도 같은 실행
    번호를 싣는다."""
    base = dict(pipeline.origin or {})
    if not base.get("system"):
        return None
    return {**base, "pipeline": pipeline.slug, "run": Path(str(send)).name}


def propose(
    submit: Submitter,
    send: Path,
    target: Target,
    *,
    origin: dict[str, str] | None = None,
) -> tuple[Plan, parts.Split]:
    """보낼 묶음을 대상에 올린다 — 조각 상한을 넘으면 나눠 **첫 조각만** 미리 본다."""
    cut = parts.split(send, target.part_mb * MB)
    plan = submit.preview(cut.parts[0] if cut.parts else send, origin=origin)
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
            f"분할 번들 {total}개로 전송합니다(전체 {cut.size / 1e6:,.0f}MB, 번들당 "
            f"{part_mb}MB, 관계 {parts.MAX_EDGES:,}건 이하). [적용]을 한 번 클릭하면 분할 "
            f"번들을 차례로 미리 보기 후 적용합니다.\n분할 번들 1/{total}: {plan.summary}"
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


def _enter(sheet: dict[str, Any], meter: memory.Meter, name: str) -> None:
    """다음 단계 — 실패하면 어디까지 갔는지(`stage`)와 그 단계의 최고 메모리를 남긴다."""
    sheet["stage"] = name
    meter.stage(name)


def run(
    pipeline: Pipeline,
    work: Workdir,
    submit: Submitter | None = None,
    *,
    options: dict[str, str] | None = None,
    accept: intake.Accept | None = None,
    full: bool = False,
    on_stage: Callable[[str], None] | None = None,
    incremental_plan: bool = True,
) -> Outcome:
    """한 바퀴. `accept` 는 사람이 「확인 후 진행」 으로 지나가게 한 받기 점검의 멈춤이다 —
    그 건수 · 칸까지만 지나간다(`intake.Accept.covers`). `full` 이면 워터마크를 두고 **전량을
    다시 받는다** — 어댑터의 칸을 고친 뒤 이미 지나간 행까지 다시 읽을 때. 안 온 것은 삭제로
    읽히므로 받기 점검(대량 삭제)이 그대로 지킨다."""
    started = time.monotonic()
    sheet: dict[str, Any] = {}
    meter = memory.Meter(on_stage)
    _enter(sheet, meter, "준비")
    try:
        with contextlib.ExitStack() as cleanup:
            outcome = _run(
                pipeline,
                work,
                submit,
                options=options,
                accept=accept,
                full=full,
                sheet=sheet,
                meter=meter,
                cleanup=cleanup,
                incremental_plan=incremental_plan,
            )
    except Exception:
        _record(work, "failed", {**sheet, **meter.finish()}, time.monotonic() - started)
        raise
    sheet.pop("stage", None)  # 어디까지 갔나는 실패한 실행에만 뜻이 있다
    if outcome.counts:
        sheet["plan"] = _totals(outcome.counts)
    outcome.memory = meter.finish()
    _record(work, outcome.status, {**sheet, **outcome.memory}, time.monotonic() - started)
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
        + "수신 검증으로 실행을 중단했습니다. 미러와 워터마크에 반영하지 않았습니다. "
        + " ".join(reasons)
        + " 원천이나 어댑터를 수정하면 다음 실행에서 통과합니다. 정상적인 변경이면 화면에서 "
        "[확인 후 진행]을 클릭하세요.",
    )


def _compare(
    pipeline: Pipeline,
    work: Workdir,
    state: State,
    run_folder: Path,
    touched: incremental.Touched | None,
    *,
    options: dict[str, str] | None,
    sheet: dict[str, Any],
) -> Delta:
    """지난번 적용된 출력과 견준다 — 증분 변환이 견줄 것을 적어 두었고 기준이 그때 그대로면
    그것만(`incremental.delta`), 아니면 출력 전체를(`diff.delta`). 만드는 폴더는 같다."""
    when = run_folder.name
    out, scratch = work.path(f"sends/{when}"), work.path(f"work/{when}.send.spill")
    if touched is not None and touched.baseline == state.applied_run:
        try:
            found = incremental.delta(
                touched, out, source=pipeline.source, options=options, scratch=scratch
            )
            sheet["compared"] = "incremental"
            return found
        except Exception:
            log.warning("증분 비교 실패 — 출력 전체를 다시 비교합니다", exc_info=True)
            shutil.rmtree(out, ignore_errors=True)
    sheet["compared"] = "full"
    return delta(
        work.path(state.applied_run) if state.applied_run else None,
        run_folder,
        out,
        source=pipeline.source,
        options=options,
        scratch=scratch,
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
    meter: memory.Meter,
    cleanup: contextlib.ExitStack[Any],
    incremental_plan: bool = True,
) -> Outcome:
    submit = submit or Submitter(pipeline.target)
    state = work.load_state()
    note = settle_pending(state, submit)
    if state.stopped:
        # 보낸 것을 걷어내고 멈췄다(`retract`) — 원천을 받지 않는다(받으면 원천에 남은 그
        # 행을 다음 계획이 다시 만든다). 걷어내기 계획이 적용됐으면 위에서 기준으로 올렸다.
        if note:
            work.save_state(state)
        from relay_core.retract import stopped_message

        return Outcome("stopped", note + stopped_message(state.stopped))

    since = None if full else state.watermark
    if full:
        note += "워터마크를 무시하고 원천 전체를 다시 수신합니다(전체 재수신). "
    # **삭제 표시가 없는 원천은 전량 날에 사라진 것을 안다**(부재 대조) — 증분 `full_every`
    # 번마다 한 번.
    source = pipeline.adapter.source
    full_every = int(getattr(source, "full_every", 0) or 0)
    due = full_every and state.incrementals + 1 >= full_every
    if since and due and not getattr(source, "deleted", None):
        since = None
    _enter(sheet, meter, "수신")
    # 받은 행이 많으면 키 차례로 정렬해 디스크로 넘긴다(`records.Records`) — 첫 적재 100만 행에
    # 받은 행 목록만 1.2GB 였다. 미러에 넣은 뒤 지운다.
    with records.spill_to(work.path(f"work/{stamp()}.fetch.spill")):
        fetched = pipeline.adapter.fetch(since)
    full, complete, watermark = fetched.full, fetched.complete, fetched.watermark
    consumed = fetched.done
    received = len(fetched.records)
    # 미러는 파일에서 흘려 이어 쓴다 — 메모리에는 받은 것만 든다(원천이 수십만 행이면 미러
    # 한 벌이 GB 다). 먼저 임시 파일에만 쓰고 받기 점검을 본다.
    _enter(sheet, meter, "미러")
    when = stamp()
    # 증분 계획의 재료 — 미러에 반영되는 변경(이어 쓸 색인이 있을 때만).
    changes = (
        incremental.record_changes(work, pipeline, work.path(f"work/{when}.changes"))
        if incremental_plan
        else None
    )
    if changes is not None:
        cleanup.callback(changes.drop)
    # 증분 변환의 부분 행 · 견줄 줄 — 비교까지 쓰고 지운다.
    cleanup.callback(shutil.rmtree, work.path(f"work/{when}.spill"), ignore_errors=True)
    try:
        staged = work.stage_mirror(fetched, changes)
    finally:
        if isinstance(fetched.records, records.Records):
            fetched.records.close()
    took, size = staged.applied, staged.size
    sheet.update(
        received=received,
        full=full,
        mirror=size,
        added=took.added,
        changed=took.changed,
        removed=took.removed,
    )
    note += (
        f"수신 {received}건(추가 {took.added} · 변경 {took.changed} · 변경 없음 "
        f"{took.unchanged} · 삭제 {took.removed}) · 미러 {size}건. "
    )
    note += "".join(f"{one} " for one in fetched.notes)
    del fetched
    checked = intake.inspect(pipeline.intake, staged.tally, state.blank_watch)
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
        note += "수신 검증의 중단 사유를 확인하고 진행했습니다. "
    state.blank_watch = intake.carry(state.blank_watch, staged.tally)
    if checked.held and accept is not None:
        for column in accept.columns:  # 사람이 맞다고 했다 — 그 칸은 다시 처음부터 센다
            state.blank_watch.pop(column, None)
    state.held = None
    state.incrementals = 0 if full else state.incrementals + 1
    if complete:
        state.watermark = watermark
    work.save_state(state)
    if consumed is not None:
        consumed()  # 미러에 들어갔다 — 보내 준 묶음을 지운다(push)
    if size == 0:
        return Outcome("nothing", note + "미러가 비어 있습니다.")
    _enter(sheet, meter, "변환")
    run_folder = work.path(f"runs/{when}")
    # 계산 칸은 변환할 때 미러 전체에 얹는다 — 규칙을 바꾼 날 옛 행도 같이 바뀐다.
    source = write_source(
        compute.wrap(MirrorFile(work, size), pipeline.computed),
        pipeline.recipe,
        run_folder,
        work=work.path("work"),
    )
    # 참조 대조(`match`)는 대상 SP 의 식별자 · 별칭으로 푼다 — 보낼 곳과 같은 주소 · 토큰. 이어
    # 쓸 색인이 있으면 바뀐 행이 닿는 객체만 다시 모은다(`incremental`) — 못 하면 까닭과 함께
    # 전체로.
    converted = incremental.run_conversion(
        work,
        pipeline,
        source,
        run_folder,
        changes,
        server=pipeline.target.server,
        token=pipeline.target.token,
        enabled=incremental_plan,
        transform=transform,
        baseline=state.applied_run,
    )
    made, touched = converted.made, converted.touched
    sheet["mode"] = converted.mode
    if converted.mode == "full":
        sheet["full_reason"] = converted.reason
        if converted.reason and incremental_plan:
            note += f"전체 변환: {converted.reason}. "
    else:
        note += "증분 변환. "
    if not made.clean:
        state.history.append({"at": _now(), "event": "blocked", "run": f"runs/{when}"})
        work.save_state(state)
        return Outcome(
            "blocked",
            note + f"레시피 변환 결과에 미해결이 있습니다. {made.run}/unresolved.json과 "
            "table-report.txt를 확인하고 레시피를 수정하세요.",
        )

    _enter(sheet, meter, "비교")
    pending = state.pending
    if pending is not None and pending.origin == "run" and pending.part > 0:
        # **나눠 보내는 계획이 일부 들어가 있다.** 원천이 그대로면 그 계획을 잇는다 —
        # 바뀌었으면 들어간 조각을 기준에 접고 거기서 다시 견준다(들어간 것을 모른 채
        # 견주면 무덤이 빠진다).
        if digest_of(made.run) == digest_of(work.path(pending.run)):
            work.save_state(state)
            return Outcome(
                "waiting",
                note + f"분할 번들 {pending.part}/{pending.total}까지 적용되었습니다. "
                "[적용]을 클릭하면 나머지를 전송합니다.",
            )
        fold(work, state)
        superseded = True
    else:
        superseded = False

    found = _compare(pipeline, work, state, made.run, touched, options=options, sheet=sheet)
    incremental.compared(work, made.run, state.applied_run, empty=found.empty)
    counts = counts_of(found)
    if not found.empty:
        # 걷어낸 적이 있는 것을 다시 보내면 되살린다(`retract.revive`) — 대상에 사용 중지로
        # 남아 있다.
        from relay_core.retract import revive

        revive(work, found.run)
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
            note + "마지막 적용 이후 변경 사항이 없습니다.",
            counts=counts,
        )

    # 오류가 난 계획은 기다리게 두지 않는다 — 대상 쪽 원인(정의 · 권한)을 고친 뒤 「다시 실행」
    # 이 새로 미리 본다.
    if state.pending and state.pending.ok and state.pending.digest == digest_of(found.run):
        return Outcome(
            "waiting",
            note + f"같은 계획({state.pending.job_id[:8]})이 적용 대기 중입니다. 새 계획을 "
            "생성하지 않았습니다.",
            link=f"{pipeline.target.server}/jobs?job={state.pending.job_id}",
            counts=counts,
        )

    _enter(sheet, meter, "미리 보기")
    plan, cut = propose(
        submit, found.run, pipeline.target, origin=origin_for(pipeline, found.run)
    )
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
        return Outcome("nothing", "적용 대기 중인 계획이 없습니다.")
    if not pending.ok and pending.part == 0:
        return Outcome(
            "blocked", "계획에 오류가 있어 적용하지 않습니다. 파이프라인을 다시 실행하세요."
        )
    total = pending.total
    note = ""
    while True:
        number = pending.part + 1
        if progress is not None:
            progress(number, total)
        if not pending.job_id or not pending.ok:
            # 이 조각은 아직 미리 보지 않았다(또는 오류였다) — 앞 조각이 들어간 지금 본다.
            plan = submit.preview(
                work.path(pending.parts[pending.part]),
                origin=origin_for(pipeline, pending.send),
            )
            pending.job_id, pending.ok, pending.at = plan.job_id, plan.ok, _now()
            pending.summary = f"분할 번들 {number}/{total}: {plan.summary}"
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
                    f"분할 번들 {number}/{total}의 미리 보기에 오류가 있어 적용을 "
                    f"중단했습니다. 앞의 분할 번들 {number - 1}개는 적용되었습니다. 원인을 "
                    "수정한 후 [적용]을 클릭하면 이 분할 번들부터 다시 전송합니다. "
                    f"{plan.summary}",
                    link=plan.link,
                )
            work.save_state(state)
        if submit.applied(pending.job_id):
            note = "이미 적용된 계획입니다. "
        else:
            try:
                result = submit.apply(pending.job_id)
            except NotApplied as refused:
                # 미리 본 뒤 대상이 바뀌었다 — 기준을 올리지 않고, 다음 실행이 다시 미리 본다.
                pending.ok = False
                state.history.append(
                    {"at": _now(), "event": "blocked", "job_id": pending.job_id}
                )
                work.save_state(state)
                return Outcome(
                    "blocked",
                    f"{refused} 미리 보기 이후 대상이 변경되었습니다. 파이프라인을 다시 "
                    "실행하면 지금 대상으로 계획을 다시 생성합니다.",
                )
            note = f"적용했습니다. {result.get('summary') or summarize(result)} "
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
        note = (
            f"분할 번들 {total}개를 차례로 미리 보기 후 적용했습니다. 마지막 분할 번들: {note}"
        )
    state.history.append(done)
    state.pending = None
    work.save_state(state)
    return Outcome("applied", note.strip())
