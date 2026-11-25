"""보낸 것 걷어내기 — 기준(적용된 출력)에서 고른 객체와 그것에 닿는 선을 뺀 새 기준을 짓고,
둘을 견준 무덤만 든 계획을 올리고 파이프라인을 멈춘다. 적용되면 새 기준이 기준이 되고, 「다시
켜기」 전에는 실행이 원천을 받지 않는다.

원천 · 제출기는 `test_relay_rest` 의 가짜 그대로 — 기준은 개발모델 M1 · M2 · M3 과 파생 선
M2 → M1(`plm_derived_from`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from relay_core import retract, runner, sp
from relay_core.retract import RetractError, Scope
from relay_core.state import Workdir
from tests.unit.test_relay_rest import ROWS, FakeSubmitter, _pipeline

ENDS = {"plm_derived_from": ["plm_model"]}


def _applied(tmp_path: Path) -> tuple[Any, Workdir, Any]:
    work = Workdir(tmp_path / "work", "t")
    fake: Any = FakeSubmitter()
    pipeline = _pipeline(tmp_path, [dict(one) for one in ROWS])
    assert runner.run(pipeline, work, fake).status == "planned"
    assert runner.apply(pipeline, work, fake).status == "applied"
    return pipeline, work, fake


def _graves(fake: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(
        (fake.previews[-1] / sp.pipeline.TOMBSTONES).read_text(encoding="utf-8")
    )
    return body


def _keys(work: Workdir, run: str, folder: str) -> list[str]:
    out: list[str] = []
    for file in sorted((work.path(run) / folder).glob("*.json")):
        for row in json.loads(file.read_text(encoding="utf-8"))["rows"]:
            out.append(str(row.get("key") or f"{row['src']}>{row['dst']}"))
    return out


def test_고른_객체와_닿는_선을_무덤으로_올리고_멈춘다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    before = work.load_state().applied_run
    outcome = retract.retract(
        pipeline,
        work,
        Scope.of({"keys": {"plm_model": ["M1"]}}),
        by="관리자",
        ends=ENDS,
        submit=fake,
    )
    assert outcome.status == "planned", outcome.message
    assert (
        "객체 1개 사용 중지 · 관계 1건 해제" in outcome.message
        and "중지했습니다" in outcome.message
    )
    graves = _graves(fake)
    # 사용 중지가 기본 — `action` 칸은 지우기일 때만 싣는다(옛 SP 도 같은 뜻으로 읽는다).
    assert graves["objects"] == [{"type_slug": "plm_model", "key": "M1", "merged_into": None}]
    assert [(one["src"], one["dst"], one["dst_type"]) for one in graves["relations"]] == [
        ("M2", "M1", "plm_model")
    ]
    # 무덤만 — 남은 객체 · 선은 다시 보내지 않는다.
    assert not list((fake.previews[-1] / sp.pipeline.OBJECTS_DIR).glob("*.json"))
    assert outcome.counts["objects"]["plm_model"]["gone"] == 1

    state = work.load_state()
    assert state.stopped and state.stopped["scope"] == "식별자 1개(plm_model 1) · 사용 중지"
    assert state.stopped["action"] == "deprecate"
    assert state.stopped["by"] == "관리자"
    assert state.pending and state.pending.origin == "retract"
    assert state.applied_run == before  # 적용 전에는 기준 그대로

    # 멈춘 동안 실행은 원천을 받지 않는다.
    stopped = runner.run(pipeline, work, fake)
    assert stopped.status == "stopped" and "[재개]" in stopped.message

    assert runner.apply(pipeline, work, fake).status == "applied"
    state = work.load_state()
    assert state.applied_run and state.applied_run.endswith("-retract")
    assert _keys(work, state.applied_run, sp.pipeline.OBJECTS_DIR) == ["M2", "M3"]
    assert _keys(work, state.applied_run, sp.pipeline.RELATIONS_DIR) == []
    assert state.stopped  # 적용해도 멈춤은 그대로 — 다시 켜기는 사람이

    # 다시 켜면 돈다 — 원천에 M1 이 그대로라 다시 만든다(그래서 멈췄었다).
    said = retract.resume(work, fake.applied, by="관리자")
    assert "재개했습니다" in said and work.load_state().stopped is None
    again = runner.run(pipeline, work, fake)
    assert again.status == "planned", again.message
    assert again.counts["objects"]["plm_model"]["created"] == 1
    # 대상에는 사용 중지로 남아 있다 — 다시 보내는 줄이 되살린다(안 실으면 「그대로」 로 본다).
    rows = [
        row
        for file in sorted((fake.previews[-1] / sp.pipeline.OBJECTS_DIR).glob("*.json"))
        for row in json.loads(file.read_text(encoding="utf-8"))["rows"]
    ]
    assert [(row["key"], row.get("status")) for row in rows] == [("M1", "active")]
    assert json.loads(work.path(retract.LEDGER).read_text("utf-8")) == {"plm_model": ["M1"]}


def test_도착_쪽_타입을_모르면_출발_쪽만_본다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    outcome = retract.retract(
        pipeline, work, Scope.of({"keys": {"plm_model": ["M1"]}}), submit=fake
    )
    assert outcome.status == "planned"
    assert "plm_derived_from" in outcome.message and "출발 객체 기준으로만" in outcome.message
    assert _graves(fake).get("relations") == []  # M2 → M1 은 M2 가 출발 — 남는다

    other = _applied(tmp_path / "b")
    pipeline, work, fake = other
    retract.retract(pipeline, work, Scope.of({"keys": {"plm_model": ["M2"]}}), submit=fake)
    assert [(one["src"], one["dst"]) for one in _graves(fake)["relations"]] == [("M2", "M1")]


def test_전부를_거두면_새_기준이_빈다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    outcome = retract.retract(pipeline, work, Scope.of({"all": True}), ends=ENDS, submit=fake)
    assert outcome.status == "planned"
    graves = _graves(fake)
    assert sorted(one["key"] for one in graves["objects"]) == ["M1", "M2", "M3"]
    assert len(graves["relations"]) == 1
    assert runner.apply(pipeline, work, fake).status == "applied"
    applied = work.load_state().applied_run or ""
    assert _keys(work, applied, sp.pipeline.OBJECTS_DIR) == []
    manifest = json.loads((work.path(applied) / sp.pipeline.MANIFEST).read_text("utf-8"))
    assert manifest["objects_order"] == [] and manifest["relations_order"] == []


def test_넣지_않은_것은_거절하고_아무것도_바꾸지_않는다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    before = work.load_state()
    with pytest.raises(RetractError, match="plm_model/M9"):
        retract.retract(
            pipeline, work, Scope.of({"keys": {"plm_model": ["M1", "M9"]}}), submit=fake
        )
    with pytest.raises(RetractError, match="적재하지 않은 타입입니다: mes_lot"):
        retract.retract(pipeline, work, Scope.of({"types": ["mes_lot"]}), submit=fake)
    after = work.load_state()
    assert after.stopped is None and after.pending is None
    assert after.applied_run == before.applied_run
    assert not [one for one in work.path("runs").iterdir() if one.name.endswith("-retract")]
    assert len(fake.previews) == 1  # 처음 실행의 것뿐


def test_대기_계획이_있으면_올리지_않는다(tmp_path: Path) -> None:
    work = Workdir(tmp_path / "work", "t")
    fake: Any = FakeSubmitter()
    pipeline = _pipeline(tmp_path, [dict(one) for one in ROWS])
    nothing = retract.retract(pipeline, work, Scope.of({"all": True}), submit=fake)
    assert nothing.status == "nothing" and "적용한 계획이 없어" in nothing.message
    assert runner.run(pipeline, work, fake).status == "planned"
    waiting = retract.retract(pipeline, work, Scope.of({"all": True}), submit=fake)
    assert waiting.status == "waiting" and work.load_state().stopped is None


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ({}, "회수 범위를 지정"),
        ({"all": True, "types": ["plm_model"]}, "단독"),
        ({"types": "plm_model"}, "목록"),
        ({"keys": ["M1"]}, "keys"),
        ({"keys": {"plm_model": []}}, "회수 범위를 지정"),
    ],
)
def test_고르기의_모양(body: dict[str, Any], said: str) -> None:
    with pytest.raises(RetractError, match=said):
        Scope.of(body)


def test_다시_켜기는_대기_중인_걷어내기를_거두거나_기준으로_올린다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    with pytest.raises(RetractError, match="중지 상태가 아닙니다"):
        retract.resume(work, fake.applied)

    retract.retract(pipeline, work, Scope.of({"keys": {"plm_model": ["M3"]}}), submit=fake)
    job = work.load_state().pending.job_id  # type: ignore[union-attr]
    with pytest.raises(RetractError, match="연결할 수 없어"):
        retract.resume(work, lambda _job: None)
    assert work.load_state().stopped  # 그대로

    said = retract.resume(work, lambda _job: False, by="관리자")
    state = work.load_state()
    assert job[:8] in said and "적용하지 마세요" in said
    assert state.pending is None and state.stopped is None
    assert [one["event"] for one in state.history[-2:]] == ["retract_cancelled", "resumed"]
    assert not (state.applied_run or "").endswith("-retract")

    # 대상 화면에서 적용한 뒤 다시 켜면 — 그 계획을 기준으로 올린다.
    retract.retract(pipeline, work, Scope.of({"keys": {"plm_model": ["M3"]}}), submit=fake)
    retract.resume(work, lambda _job: True)
    state = work.load_state()
    assert state.pending is None and (state.applied_run or "").endswith("-retract")
    assert _keys(work, state.applied_run or "", sp.pipeline.OBJECTS_DIR) == ["M1", "M2"]


def test_정리는_걷어내기_폴더를_실행의_것으로_센다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    retract.retract(pipeline, work, Scope.of({"all": True}), submit=fake)
    runner.apply(pipeline, work, fake)
    kept = work.load_state().applied_run
    work.prune(1)
    assert kept and work.path(kept).is_dir()
    from relay_core import incremental

    names = sorted(one.name for one in work.path("runs").iterdir())
    # 걷어내기 기준과, 증분 계획의 색인이 이어 쓸 실행(`incremental.index_run`)만 남는다.
    indexed = incremental.index_run(work)
    assert indexed is not None
    assert names == sorted({Path(kept).name, Path(indexed).name})


class _Target:
    """대상 읽개 대신 — 타입마다 객체의 상태."""

    def __init__(self, status: dict[str, dict[str, str]]) -> None:
        self.status = status

    def objects(self, type_slug: str) -> list[dict[str, Any]] | None:
        return [{"key": k, "status": v} for k, v in self.status.get(type_slug, {}).items()]

    def managed_by(self) -> dict[str, str | None]:
        return {}


def test_지우기는_무덤에_action_을_싣는다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    outcome = retract.retract(
        pipeline,
        work,
        Scope.of({"keys": {"plm_model": ["M2"]}}),
        action="delete",
        ends=ENDS,
        submit=fake,
    )
    assert outcome.status == "planned" and "객체 1개 삭제 · 관계 1건 해제" in outcome.message
    graves = _graves(fake)
    assert graves["objects"] == [
        {"type_slug": "plm_model", "key": "M2", "merged_into": None, "action": "delete"}
    ]
    assert graves["relations"][0]["dst_type"] == "plm_model"  # 관계 종류의 도착 타입이 하나
    assert work.load_state().stopped["action"] == "delete"  # type: ignore[index]
    assert "삭제했습니다" in retract.after_apply(work.load_state().stopped)
    assert "사용 중지했습니다" in retract.after_apply({"action": "deprecate"})


def test_앞서_사용_중지로_걷어낸_것을_지운다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    retract.retract(pipeline, work, Scope.of({"keys": {"plm_model": ["M3"]}}), submit=fake)
    runner.apply(pipeline, work, fake)  # 대상에서 사용 중지
    base = work.load_state().applied_run
    with pytest.raises(RetractError, match="delete"):
        retract.retract(pipeline, work, Scope.of({"previous": True}), submit=fake)

    gone = _Target({"plm_model": {"M3": "active"}})  # 대상에서 누가 되살렸다 — 고를 것 없음
    nothing = retract.retract(
        pipeline, work, Scope.of({"previous": True}), action="delete", submit=fake, reader=gone
    )
    assert (
        nothing.status == "nothing"
        and "사용 중지 상태로 남은 객체가 없습니다" in nothing.message
    )

    target = _Target({"plm_model": {"M1": "active", "M2": "active", "M3": "deprecated"}})
    outcome = retract.retract(
        pipeline,
        work,
        Scope.of({"previous": True}),
        action="delete",
        submit=fake,
        reader=target,
    )
    assert outcome.status == "planned", outcome.message
    assert _graves(fake) == {
        "objects": [
            {"type_slug": "plm_model", "key": "M3", "merged_into": None, "action": "delete"}
        ],
        "relations": [],
    }
    assert outcome.counts["objects"]["plm_model"]["gone"] == 1
    assert runner.apply(pipeline, work, fake).status == "applied"
    assert work.load_state().applied_run == base  # 기준은 그대로 — 이미 빠져 있다


@pytest.mark.parametrize(
    ("version", "deletes"),
    [("v0.4.100", False), ("v0.4.101", True), ("0.5.0", True), ("v0.4", False), ("", False)],
)
def test_지우기를_받는_판(version: str, deletes: bool) -> None:
    assert retract.supports_delete(version) is deletes


def test_previous_는_홀로() -> None:
    with pytest.raises(RetractError, match="단독"):
        Scope.of({"previous": True, "all": True})
    with pytest.raises(RetractError, match="action"):
        retract.check(Scope.of({"all": True}), "purge")
