"""자동 적용 — 사람이 켠 기준을 **모두** 넘은 계획만 적용하고, 못 넘으면 까닭을 남기고
기다린다.

원천 · 대상은 `test_relay_rest` 의 것 그대로(가짜 원천 · 가짜 제출기).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from relay_core import autoapply, runner
from relay_core.adapters.rest import RestSource
from relay_core.autoapply import AutoApplyError, Policy, check
from relay_core.state import Workdir
from tests.unit.test_relay_rest import ROWS, FakeSubmitter, _pipeline


def test_기준은_켜야_있고_모르는_칸은_거절한다() -> None:
    assert check(None) is None and check(False) is None
    assert check(True) == Policy(after_manual=3, max_changes=500, allow_gone=False)
    assert check({"max_changes": 10}) == Policy(max_changes=10)
    for wrong in (
        {"after_manual": 0},
        {"max_changes": -1},
        {"allow_gone": "yes"},
        {"x": 1},
        3,
    ):
        with pytest.raises(AutoApplyError):
            check(wrong)
    assert autoapply.label(None) == "미사용"
    assert "3번 뒤" in autoapply.label(Policy())


def _run(pipeline: Any, work: Workdir, fake: FakeSubmitter, policy: Policy) -> runner.Outcome:
    outcome = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    return autoapply.after_run(pipeline, work, outcome, policy, fake)  # type: ignore[arg-type]


def test_사람이_정한_횟수만큼_적용한_뒤부터_자동이다(tmp_path: Path) -> None:
    rows = [dict(one) for one in ROWS]
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    policy = Policy(after_manual=1)

    first = _run(_pipeline(tmp_path, rows), work, fake, policy)
    assert first.status == "planned" and "자동 적용 안 함" in first.message
    skipped = work.load_state().history[-1]
    assert skipped["event"] == "auto_skipped" and "0번" in skipped["reasons"][0]

    assert runner.apply(_pipeline(tmp_path, rows), work, fake).status == "applied"  # type: ignore[arg-type]

    rows[0].update(name="갤럭시 S", updated_at="2026-11-04T00:00:00Z")
    later = _run(_pipeline(tmp_path, rows), work, fake, policy)
    assert later.status == "applied" and "자동 적용" in later.message, later.message
    state = work.load_state()
    assert state.pending is None and state.history[-1] == {
        **state.history[-1],
        "event": "applied",
        "by": "auto",
    }
    # 자동으로 한 것은 「사람이 적용한 것」 이 아니다
    assert autoapply.manual_applies(state) == 1


def test_변경이_많거나_지움이_있으면_사람에게_넘긴다(tmp_path: Path) -> None:
    rows = [dict(one) for one in ROWS]
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    runner.run(_pipeline(tmp_path, rows), work, fake)  # type: ignore[arg-type]
    runner.apply(_pipeline(tmp_path, rows), work, fake)  # type: ignore[arg-type]

    rows[0].update(name="갤럭시 S", updated_at="2026-11-04T00:00:00Z")
    rows[2].update(deleted=True, updated_at="2026-11-04T00:00:01Z")
    pipeline = _pipeline(tmp_path, rows)
    pipeline.adapter.source = RestSource(
        **{**pipeline.adapter.source.__dict__, "deleted": "deleted"}
    )
    strict = _run(pipeline, work, fake, Policy(after_manual=1, max_changes=0))
    assert strict.status == "planned"
    reasons = work.load_state().history[-1]["reasons"]
    assert any("기준 0건" in one for one in reasons) and any("삭제" in one for one in reasons)

    # 같은 계획이 기다리는 중 — 다시 돌아도 「waiting」 이라 자동 적용을 다시 보지 않는다
    again = _run(pipeline, work, fake, Policy(after_manual=1, allow_gone=True))
    assert again.status == "waiting"
