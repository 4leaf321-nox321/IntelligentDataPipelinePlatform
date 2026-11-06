"""자동 적용 — **사람이 켠 기준**을 넘은 계획만 사람 없이 적용한다(개발계획 ③).

정의의 `auto_apply` — 없거나 `false` 면 꺼짐(기본):

    "auto_apply": true
    "auto_apply": {"after_manual": 3, "max_changes": 500, "allow_gone": false}

- **타이머가 돌린 실행에만** 쓴다. 사람의 「지금 실행」 · MCP 의 실행은 계획만 올린다 — 적용은
  사람이라는 약속(ADR 0001 §2)은 그대로이고, 이것은 사람이 정의 판을 켜며 정한 예외다.
- 기준(모두 넘어야 한다): 대상의 미리보기에 오류가 없다 · 사람이 적용한 것이 `after_manual` 번
  이상이다(처음 몇 번은 사람이 계획을 읽는다) · 생성 + 고침이 `max_changes` 이하다 · 지움(사용
  중지 · 선 끊기)이 없다(`allow_gone` 이면 있어도 된다).
- 못 넘으면 계획은 그대로 기다린다 — 이력에 까닭(`auto_skipped`)이 남고 홈 「남은 일」 이
  사람에게 말한다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from relay_core.definition import Pipeline
    from relay_core.runner import Outcome
    from relay_core.state import State, Workdir
    from relay_core.submit import Submitter


class AutoApplyError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    after_manual: int = 3
    """사람이 적용한 것이 이만큼 된 뒤부터 — 처음 몇 번은 사람이 계획을 읽는다."""
    max_changes: int = 500
    """한 번에 생성 + 고침이 이것보다 많으면 사람에게 넘긴다(원천이 갑자기 크게 바뀐 날)."""
    allow_gone: bool = False
    """지움(사용 중지 · 선 끊기)이 있어도 적용하나 — 기본은 사람에게 넘긴다."""


_KEYS = {"after_manual", "max_changes", "allow_gone"}


def check(spec: Any) -> Policy | None:
    """정의의 `auto_apply` → 기준. 꺼짐이면 None. 틀리면 `AutoApplyError`."""
    if spec is None or spec is False:
        return None
    if spec is True:
        return Policy()
    if not isinstance(spec, dict):
        raise AutoApplyError("auto_apply 는 true · false 또는 기준 객체입니다.")
    unknown = sorted(set(spec) - _KEYS)
    if unknown:
        raise AutoApplyError(f"auto_apply: 모르는 칸입니다: {', '.join(unknown)}")
    after = spec.get("after_manual", Policy.after_manual)
    most = spec.get("max_changes", Policy.max_changes)
    gone = spec.get("allow_gone", Policy.allow_gone)
    if not isinstance(after, int) or isinstance(after, bool) or after < 1:
        raise AutoApplyError(
            "auto_apply.after_manual 은 1 이상의 정수입니다(첫 적재는 사람이)."
        )
    if not isinstance(most, int) or isinstance(most, bool) or most < 0:
        raise AutoApplyError("auto_apply.max_changes 는 0 이상의 정수입니다.")
    if not isinstance(gone, bool):
        raise AutoApplyError("auto_apply.allow_gone 은 true · false 입니다.")
    return Policy(after_manual=after, max_changes=most, allow_gone=gone)


def label(policy: Policy | None) -> str:
    if policy is None:
        return "미사용"
    gone = "삭제 포함" if policy.allow_gone else "삭제 없을 때만"
    return (
        f"타이머 실행 · 사람 적용 {policy.after_manual}번 뒤 · "
        f"변경 {policy.max_changes}건 이하 · {gone}"
    )


def manual_applies(state: State) -> int:
    return sum(
        1 for one in state.history if one.get("event") == "applied" and one.get("by") != "auto"
    )


def totals(counts: dict[str, Any]) -> tuple[int, int]:
    """(생성 + 고침, 지움) — 객체 · 선 모두."""
    changes = gone = 0
    for group in counts.values():
        for one in (group or {}).values():
            changes += int(one.get("created", 0)) + int(one.get("changed", 0))
            gone += int(one.get("gone", 0))
    return changes, gone


def decide(policy: Policy, state: State, outcome: Outcome) -> list[str]:
    """적용하지 **않을** 까닭 — 비면 적용한다."""
    reasons: list[str] = []
    if outcome.status != "planned" or state.pending is None or not state.pending.ok:
        reasons.append("대상의 미리 보기에 오류가 있습니다")
        return reasons
    if state.pending.parts:
        reasons.append(f"나눠 보내는 계획(조각 {state.pending.total}개)은 사람이 적용합니다")
        return reasons
    done = manual_applies(state)
    if done < policy.after_manual:
        reasons.append(
            f"사람이 적용한 것이 {done}번 — {policy.after_manual}번 뒤부터 자동입니다"
        )
    changes, gone = totals(outcome.counts)
    if changes > policy.max_changes:
        reasons.append(f"생성 + 변경 {changes}건이 기준 {policy.max_changes}건을 넘습니다")
    if gone and not policy.allow_gone:
        reasons.append(f"삭제 {gone}건이 있습니다")
    return reasons


def after_run(
    pipeline: Pipeline,
    work: Workdir,
    outcome: Outcome,
    policy: Policy | None,
    submit: Submitter | None = None,
) -> Outcome:
    """타이머의 실행 뒤 — 기준을 넘으면 적용하고, 아니면 까닭을 이력에 남기고 그대로 둔다."""
    from relay_core import runner

    if policy is None or outcome.status != "planned":
        return outcome
    state = work.load_state()
    reasons = decide(policy, state, outcome)
    if reasons:
        state.history.append(
            {
                "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "event": "auto_skipped",
                "job_id": state.pending.job_id if state.pending else None,
                "reasons": reasons,
            }
        )
        work.save_state(state)
        return replace(
            outcome, message=f"{outcome.message} · 자동 적용 안 함: {'; '.join(reasons)}"
        )
    applied = runner.apply(pipeline, work, submit, by="auto")
    return replace(
        outcome,
        status=applied.status,
        message=f"{outcome.message} · 자동 적용: {applied.message}",
    )
