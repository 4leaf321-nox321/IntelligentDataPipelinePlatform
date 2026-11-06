"""나눠 보내기 — 큰 묶음을 조각으로, 조각마다 차례로 미리 보기 → 적용, 도중의 멈춤 · 접기.

조각 상한을 바이트 몇천으로 줄여(`runner.MB`) 작은 원천을 여러 조각으로 나눠 본다. 대상은
가짜 제출기(`FakeSubmitter`) — 진짜 SP 로 큰 첫 적재를 나눠 보내는 것은
`devtools/bench/sp_bench.sh` 가 본다.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from relay_core import autoapply, parts, runner, sp
from relay_core.definition import DefinitionError, Pipeline, check_body
from relay_core.state import Workdir
from relay_core.submit import Plan, SubmitError, Submitter
from tests.unit.test_relay_rest import FakeSubmitter, _pipeline

#: 조각 상한(바이트) — 조각마다 모델 대여섯 개(줄마다 1KB 넘게)가 들어가게.
LIMIT = parts._HEAD + 8 * 1024


def _rows(n: int = 60) -> list[dict[str, Any]]:
    return [
        {
            "id": f"M{i:03d}",
            "name": f"모델 {i} " + "가" * 400,  # 줄마다 1KB 넘게 — 작은 원천으로 조각을 만든다
            "kind": "AB"[i % 2],
            "base": f"M{i - 1:03d}" if i % 2 else None,
            "updated_at": f"2026-11-01T00:00:{i % 60:02d}Z",
        }
        for i in range(n)
    ]


@pytest.fixture
def small_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "MB", 1)


def _split_pipeline(tmp_path: Path, rows: list[dict[str, Any]]) -> Pipeline:
    pipeline = _pipeline(tmp_path, rows)
    target = dataclasses.replace(pipeline.target, part_mb=LIMIT)
    return dataclasses.replace(pipeline, target=target)


def _keys(folder: Path) -> tuple[list[str], list[tuple[Any, Any]], list[str]]:
    run = sp.pipeline.load(folder)
    objects = [str(row["key"]) for batch in run.objects for row in batch.rows]
    edges = [(row["src"], row["dst"]) for batch in run.relations for row in batch.rows]
    graves = [str(one["key"]) for one in (run.tombstones or {}).get("objects") or []]
    return objects, edges, graves


def test_큰_묶음은_조각으로_나뉘고_적용이_차례로_미리_보고_넣는다(
    tmp_path: Path, small_parts: None
) -> None:
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    pipeline = _split_pipeline(tmp_path, _rows())

    first = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    pending = work.load_state().pending
    assert first.status == "planned" and pending is not None, first.message
    folders = [work.path(one) for one in pending.parts]
    assert len(folders) >= 3 and "나눠 보냅니다" in first.message
    # **첫 조각만** 미리 본다 — 뒤 조각은 앞 조각이 들어간 뒤에.
    assert fake.previews == [folders[0]]

    seen_objects: list[str] = []
    seen_edges: list[Any] = []
    last_object_part = first_edge_part = None
    for index, folder in enumerate(folders):
        loaded = sp.pipeline.load(folder)
        assert not sp.pipeline.validate(loaded).errors
        body = json.dumps(sp.pipeline.payload(loaded), ensure_ascii=False).encode()
        assert len(body) <= LIMIT
        objects, edges, _ = _keys(folder)
        seen_objects += objects
        seen_edges += edges
        if objects:
            last_object_part = index
        if edges and first_edge_part is None:
            first_edge_part = index
    # 하나도 빠지거나 겹치지 않고, 객체가 다 간 뒤에 선이 간다.
    whole_objects, whole_edges, _ = _keys(work.path(pending.send))
    assert sorted(seen_objects) == sorted(whole_objects) and len(seen_objects) == 60
    assert sorted(seen_edges) == sorted(whole_edges) and len(seen_edges) == 30
    assert first_edge_part is not None and last_object_part is not None
    assert first_edge_part >= last_object_part

    # 적용 전에 다시 돌면 같은 계획이다(전체 묶음의 지문으로 견준다).
    assert runner.run(pipeline, work, fake).status == "waiting"  # type: ignore[arg-type]

    shown: list[tuple[int, int]] = []
    done = runner.apply(
        pipeline,
        work,
        fake,  # type: ignore[arg-type]
        progress=lambda part, total: shown.append((part, total)),
    )
    assert done.status == "applied", done.message
    assert fake.previews == folders  # 조각마다 한 번씩, 차례로
    assert len(fake.applied_jobs) == len(folders)
    assert shown == [(k, len(folders)) for k in range(1, len(folders) + 1)]
    state = work.load_state()
    assert state.pending is None and state.applied_run == pending.run
    events = [one["event"] for one in state.history]
    assert events.count("part_applied") == len(folders) - 1 and events[-1] == "applied"
    assert state.history[-1]["parts"] == len(folders)
    # 사람의 적용은 한 번으로 센다(자동 적용의 「사람 적용 N번 뒤」).
    assert autoapply.manual_applies(state) == 1
    assert runner.run(pipeline, work, fake).status == "nothing"  # type: ignore[arg-type]


class _FlakySubmitter(FakeSubmitter):
    """`bad` 번째 미리 보기를 한 번 오류로 — 대상 쪽 원인을 고친 뒤 다시 보면 괜찮다."""

    def __init__(self, bad: int) -> None:
        super().__init__()
        self.bad = bad

    def preview(self, run: Path) -> Plan:
        plan = super().preview(run)
        if len(self.previews) == self.bad:
            return dataclasses.replace(plan, ok=False, summary="오류 1건")
        return plan


def test_뒤_조각의_미리_보기_오류에서_멈추고_다시_적용하면_그_조각부터_잇는다(
    tmp_path: Path, small_parts: None
) -> None:
    work = Workdir(tmp_path / "work", "t")
    fake = _FlakySubmitter(bad=2)
    pipeline = _split_pipeline(tmp_path, _rows())
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]

    stopped = runner.apply(pipeline, work, fake)  # type: ignore[arg-type]
    assert stopped.status == "blocked" and "조각 2/" in stopped.message
    state = work.load_state()
    assert state.pending is not None and state.pending.part == 1
    assert not state.pending.ok and state.applied_run is None
    assert fake.applied_jobs == {"job-1"}  # 첫 조각은 들어가 있다

    # 같은 원천으로 다시 실행해도 계획을 갈아 치우지 않는다 — 이어 보내기를 기다린다.
    again = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert again.status == "waiting" and "조각 1/" in again.message

    resumed = runner.apply(pipeline, work, fake)  # type: ignore[arg-type]
    assert resumed.status == "applied", resumed.message
    folders = [work.path(one) for one in state.pending.parts]
    # 오류였던 조각을 다시 미리 보고 거기서부터 — 첫 조각은 다시 보내지 않는다.
    assert fake.previews == [folders[0], folders[1], folders[1], *folders[2:]]
    assert work.load_state().applied_run == state.pending.run


def test_대상_화면에서_조각을_적용하면_다음_조각으로_넘긴다(
    tmp_path: Path, small_parts: None
) -> None:
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    pipeline = _split_pipeline(tmp_path, _rows())
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    fake.applied_jobs.add("job-1")  # 사람이 대상 SP 화면에서 첫 조각을 적용했다

    waiting = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert waiting.status == "waiting" and "조각 1/" in waiting.message
    pending = work.load_state().pending
    assert pending is not None and pending.part == 1 and pending.job_id == ""
    assert work.load_state().applied_run is None  # 다 들어가야 기준이 된다

    done = runner.apply(pipeline, work, fake)  # type: ignore[arg-type]
    assert done.status == "applied" and len(fake.previews) == pending.total


def test_일부만_들어간_채_원천이_바뀌면_들어간_조각을_기준에_접고_다시_견준다(
    tmp_path: Path, small_parts: None
) -> None:
    rows = _rows()
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    pipeline = _split_pipeline(tmp_path, rows)
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    pending = work.load_state().pending
    assert pending is not None
    first_part, _, _ = _keys(work.path(pending.parts[0]))
    fake.applied_jobs.add("job-1")  # 첫 조각만 들어갔다

    # 원천이 바뀐다 — 첫 조각에 있던 모델 하나(아무도 가리키지 않는 홀수 번)가 지워지고, 뒤
    # 조각의 모델 하나가 바뀐다.
    gone, renamed = first_part[1], "M059"
    assert renamed not in first_part
    for row in rows:
        if row["id"] == gone:
            row.update(deleted=True, updated_at="2026-11-02T00:00:00Z")
        if row["id"] == renamed:
            row.update(name="바뀐 이름", updated_at="2026-11-02T00:00:01Z")
    pipeline = _split_pipeline(tmp_path, rows)
    source = pipeline.adapter.source
    pipeline.adapter.source = dataclasses.replace(source, deleted="deleted")

    later = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert later.status == "planned", later.message
    state = work.load_state()
    assert state.applied_run == f"{pending.run}-partial"
    counts = later.counts["objects"]["plm_model"]
    # 접은 기준에는 첫 조각이 들어 있다 — 그 모델들은 「그대로」, 지워진 것은 무덤으로 간다.
    assert counts["gone"] == 1 and counts["unchanged"] == len(first_part) - 1
    assert counts["created"] == 60 - len(first_part)
    assert state.pending is not None
    folders = [work.path(one) for one in state.pending.parts] or [
        work.path(state.pending.send)
    ]
    graves = [grave for folder in folders for grave in _keys(folder)[2]]
    assert graves == [gone]
    # 정리가 접은 기준과 그 실행의 원천 표를 남긴다.
    work.prune(1)
    assert work.path(state.applied_run).is_dir()
    assert any(work.path("work").glob(f"{pending.run.split('/')[-1]}.*"))


def test_나눠_보내는_계획은_자동으로_적용하지_않는다(
    tmp_path: Path, small_parts: None
) -> None:
    work = Workdir(tmp_path / "work", "t")
    outcome = runner.run(_split_pipeline(tmp_path, _rows()), work, FakeSubmitter())  # type: ignore[arg-type]
    policy = autoapply.Policy(after_manual=0, max_changes=10_000)
    reasons = autoapply.decide(policy, work.load_state(), outcome)
    assert reasons and "나눠 보내는" in reasons[0]


def test_작은_묶음은_나누지_않는다(tmp_path: Path) -> None:
    work = Workdir(tmp_path / "work", "t")
    runner.run(_pipeline(tmp_path, _rows(4)), work, FakeSubmitter())  # type: ignore[arg-type]
    pending = work.load_state().pending
    assert pending is not None and pending.parts == [] and pending.total == 1
    assert not work.path(pending.send).joinpath(parts.PARTS).exists()


def _graves_only(folder: Path) -> Path:
    folder.mkdir(parents=True)
    head = {"format": "sp-bundle/1", "source": "plm", "objects_order": []}
    (folder / "bundle.json").write_text(json.dumps(head), encoding="utf-8")
    graves = {"objects": [{"type_slug": "plm_model", "key": "M1", "merged_into": None}]}
    (folder / "tombstones.json").write_text(json.dumps(graves), encoding="utf-8")
    return folder


def test_무덤만_든_묶음도_미리_본다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """틀의 검증은 「묶음이 비어 있습니다」 로 거절하지만 SP 는 받는다 — 원천에서 지워진 것만
    있는 날이 그렇다."""
    sent: list[dict[str, Any]] = []

    def post(_server: str, _token: str, body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        return {"ok": True, "job_id": "j-1", "objects": [], "relations": []}

    monkeypatch.setattr(sp.pipeline, "_post_bundle", post)
    target = _pipeline(tmp_path, []).target
    plan = Submitter(target).preview(_graves_only(tmp_path / "graves"))
    assert plan.ok and plan.job_id == "j-1"
    assert sent[0]["tombstones"]["objects"][0]["key"] == "M1" and sent[0]["apply"] is False
    seen = json.loads((tmp_path / "graves" / "preview.json").read_text(encoding="utf-8"))
    assert seen["digest"] == plan.digest

    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "bundle.json").write_text(json.dumps({"format": "sp-bundle/1"}), encoding="utf-8")
    with pytest.raises(SubmitError, match="비어"):
        Submitter(target).preview(empty)


@pytest.mark.parametrize("value", [0, 513, "32", True, 1.5])
def test_조각_상한은_범위_안의_정수(value: Any) -> None:
    body = {
        "format": "relay-pipeline/1",
        "slug": "t",
        "source": "plm",
        "adapter": {
            "kind": "rest",
            "base_url": "http://plm.test",
            "path": "/api/models",
            "key": "id",
            "columns": {"MODEL_ID": "id"},
        },
        "target": {"server": "http://sp.test", "token_env": "T", "part_mb": value},
    }
    with pytest.raises(DefinitionError, match="part_mb"):
        check_body(body)
    body["target"]["part_mb"] = 64  # type: ignore[index]
    assert check_body(body) == ("t", "plm")


def test_들어간_조각까지로_원천과_같아지면_남은_조각을_내린다(
    tmp_path: Path, small_parts: None
) -> None:
    rows = [{**row, "base": None} for row in _rows()]
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    pipeline = _split_pipeline(tmp_path, rows)
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    pending = work.load_state().pending
    assert pending is not None and pending.total >= 3
    first_part, _, _ = _keys(work.path(pending.parts[0]))
    fake.applied_jobs.add("job-1")

    # 첫 조각에 없던 모델이 원천에서 모두 지워진다 — 대상에는 이미 원천 그대로가 들어가 있다.
    for row in rows:
        if row["id"] not in first_part:
            row.update(deleted=True, updated_at="2026-11-02T00:00:00Z")
    pipeline = _split_pipeline(tmp_path, rows)
    source = pipeline.adapter.source
    pipeline.adapter.source = dataclasses.replace(source, deleted="deleted")

    later = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert later.status == "nothing", later.message
    state = work.load_state()
    assert state.pending is None and state.applied_run == f"{pending.run}-partial"


def test_대상_SP_의_대기는_넉넉히_기다린다() -> None:
    """큰 조각이 들어간 뒤 대상 SP 의 워커는 그래프 지도를 그린다 — 다음 조각의 미리 보기가 그
    뒤에 줄을 서도 「워커가 꺼졌다」 로 멈추지 않게(틀의 기본은 120초)."""
    assert sp.pipeline.QUEUED_LIMIT_SECONDS >= 1800


def test_선이_많으면_바이트가_작아도_나눈다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SP 가 선을 넣는 시간은 한 묶음 안의 선 수의 제곱으로 는다 — 선은 조각마다 따로 센다."""
    monkeypatch.setattr(parts, "MAX_EDGES", 8)
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    runner.run(_pipeline(tmp_path, _rows()), work, fake)  # type: ignore[arg-type]
    pending = work.load_state().pending
    assert pending is not None and pending.parts  # 8MB 에는 한참 못 미친다
    edges = [len(_keys(work.path(one))[1]) for one in pending.parts]
    assert max(edges) <= 8 and sum(edges) == 30
    assert "선 8줄 이하" in pending.summary
