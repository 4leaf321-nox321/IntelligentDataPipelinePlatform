"""REST 어댑터와 실행 한 바퀴 — 원천은 `httpx.MockTransport`, 대상은 가짜 제출기.

진짜 원천 · 진짜 SP 로 끝까지 도는 것은 `devtools/`(샘플 PLM + SP 시험 인스턴스)가
본다. 여기서는 그 사이의 규칙을 본다: 쪽 넘김 · 증분 · 펴기 · 삭제 · 재시도, 그리고 「같은
계획을 또 올리지 않는다」 · 「적용된 것이 기준이다」.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from relay_core import runner
from relay_core.adapters.rest import Auth, RestAdapter, RestSource, SourceError
from relay_core.definition import Pipeline, Target
from relay_core.state import Workdir
from relay_core.submit import Plan, digest_of
from tests.unit.test_relay_core import RECIPE

Handler = Callable[[httpx.Request], httpx.Response]


def _adapter(handler: Handler, **over: Any) -> RestAdapter:
    spec: dict[str, Any] = {
        "base_url": "http://plm.test",
        "path": "/api/models",
        "key": "id",
        "columns": {"MODEL_ID": "id", "MODEL_NAME": "name", "KIND": "kind", "BASE": "base"},
        "page_size": 2,
        "since_param": "updated_since",
        "auth": Auth(kind="bearer", token="t0ken"),
    }
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return RestAdapter(RestSource(**{**spec, **over}), client=client, sleep=lambda _s: None)


def _paged(rows: list[dict[str, Any]], seen: list[httpx.Request]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        page = int(request.url.params["page"])
        size = int(request.url.params["page_size"])
        since = request.url.params.get("updated_since")
        picked = [one for one in rows if not since or one["updated_at"] > since]
        return httpx.Response(200, json={"items": picked[(page - 1) * size : page * size]})

    return handler


ROWS: list[dict[str, Any]] = [
    {
        "id": "M1",
        "name": "갤럭시",
        "kind": "A",
        "base": None,
        "updated_at": "2026-11-01T00:00:00Z",
    },
    {
        "id": "M2",
        "name": "엣지",
        "kind": "B",
        "base": "M1",
        "updated_at": "2026-11-02T00:00:00Z",
    },
    {
        "id": "M3",
        "name": "노트",
        "kind": "A",
        "base": None,
        "updated_at": "2026-11-03T00:00:00Z",
    },
]


def test_쪽을_끝까지_넘기고_인증과_증분을_싣는다() -> None:
    seen: list[httpx.Request] = []
    got = _adapter(_paged(ROWS, seen)).fetch(None)
    assert [one.key for one in got.records] == ["M1", "M2", "M3"]
    assert len(seen) == 2  # 2 + 1 — 덜 찬 쪽에서 끝
    assert seen[0].headers["Authorization"] == "Bearer t0ken"
    assert got.full and got.complete and got.watermark == "2026-11-03T00:00:00Z"

    seen.clear()
    later = _adapter(_paged(ROWS, seen)).fetch("2026-11-01T00:00:00Z")
    assert [one.key for one in later.records] == ["M2", "M3"] and not later.full
    assert seen[0].url.params["updated_since"] == "2026-11-01T00:00:00Z"


def test_증분_칸이_없으면_워터마크가_있어도_전량이다() -> None:
    """`since` 를 실어 보낼 자리가 없으면 원천은 늘 전부를 준다 — 「증분」 으로 두면 원천에서
    사라진 행을 미러가 영영 들고 있었다(2026-11-27 점검)."""
    seen: list[httpx.Request] = []
    later = "2026-11-01T00:00:00Z"
    plain = _adapter(_paged(ROWS, seen), since_param=None)
    got = plain.fetch(later)
    assert got.full and [one.key for one in got.records] == ["M1", "M2", "M3"]
    assert "updated_since" not in seen[0].url.params

    # POST 본문에 `{since}` 자리가 있으면 증분이다.
    def searched(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    body = {"filter": {"changed_after": "{since}"}, "page": "{page}"}
    post = _adapter(searched, since_param=None, method="POST", body=body)
    assert not post.fetch(later).full
    bare = _adapter(searched, since_param=None, method="POST", body={"page": "{page}"})
    assert bare.fetch(later).full
    assert not _adapter(_paged(ROWS, [])).fetch(later).full  # 증분 칸이 있으면 증분


def test_증분에서_목록에서_빠진_원소는_지우고_목록이_없는_항목은_그대로(
    tmp_path: Path,
) -> None:
    """펼친 행은 원소가 목록에서 빠져도 다시 오지 않는다 — 목록을 받은 항목의 나머지 원소를
    지운다. 목록 칸이 아예 없는 항목(요약)은 원소를 모르니 건드리지 않는다(2026-11-27 점검)."""
    from relay_core.mirror import Mirror

    items: list[dict[str, Any]] = [
        {"id": "O1", "at": "t1", "lines": [{"no": 1, "q": 5}, {"no": 2, "q": 7}]},
        {"id": "O2", "at": "t1", "lines": [{"no": 1, "q": 3}]},
        {"id": "O3", "at": "t1", "lines": [{"no": 1, "q": 9}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        since = request.url.params.get("updated_since")
        return httpx.Response(
            200, json={"items": [one for one in items if not since or one["at"] > since]}
        )

    adapter = _adapter(
        handler,
        paging="none",
        key=["id", "lines.no"],
        columns={"QTY": "lines.q"},
        explode="lines",
        updated_at="at",
        deleted="gone",
    )
    work = Workdir(tmp_path / "w", "t")
    first = adapter.fetch(None)
    work.merge_mirror(first)
    memory = Mirror(first.records)
    # O1 은 2번 줄이 빠졌고, O2 는 지워져 목록 없이 오고, O3 은 목록 없이 요약만 온다.
    items[:] = [
        {"id": "O1", "at": "t2", "lines": [{"no": 1, "q": 5}]},
        {"id": "O2", "at": "t2", "gone": True},
        {"id": "O3", "at": "t2"},
    ]
    later = adapter.fetch(first.watermark)
    assert not later.full and later.covers is not None
    work.merge_mirror(later)
    assert [one.key for one in work.mirror_rows()] == ["O1|1", "O3|1"]
    assert memory.apply(later).removed == 2  # 메모리의 미러도 같은 규칙
    assert sorted(one.key for one in memory.rows()) == ["O1|1", "O3|1"]


def test_중첩을_펴고_목록은_세미콜론으로_지운_것은_표시로() -> None:
    body = {
        "items": [
            {
                "id": "M1",
                "task": {"code": "T1"},
                "tags": [{"n": "a"}, {"n": "b"}],
                "deleted": False,
            },
            {"id": "M2", "deleted": True},
        ]
    }
    adapter = _adapter(
        lambda _r: httpx.Response(200, json=body),
        paging="none",
        columns={"TASK": "task.code", "TAGS": "tags.n", "MISSING": "nope.deep"},
    )
    first, second = adapter.fetch(None).records
    assert dict(first.values) == {"TASK": "T1", "TAGS": "a;b", "MISSING": ""}
    assert second.deleted and dict(second.values) == {}


def test_4xx_는_다시_묻지_않고_5xx_는_다시_묻는다() -> None:
    calls: list[int] = []

    def refused(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(403, text="권한 없음")

    with pytest.raises(SourceError, match="거부"):
        _adapter(refused).fetch(None)
    assert len(calls) == 1

    calls.clear()

    def flaky(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text="잠시")
        return httpx.Response(200, json={"items": []})

    assert list(_adapter(flaky).fetch(None).records) == []
    assert len(calls) == 3


def test_식별자가_빈_항목은_멈춘다() -> None:
    adapter = _adapter(lambda _r: httpx.Response(200, json={"items": [{"id": ""}]}))
    with pytest.raises(SourceError, match="식별자"):
        adapter.fetch(None)


class FakeSubmitter:
    """대상 SP 대신 — 미리보기는 지문만 남기고, 적용은 표시만 한다."""

    def __init__(self) -> None:
        self.previews: list[Path] = []
        self.origins: list[dict[str, str] | None] = []
        self.applied_jobs: set[str] = set()
        self.applying_jobs: set[str] = set()
        """대상에서 적용하는 중인 계획 — 대조가 그동안 읽지 않는다."""

    def preview(self, run: Path, *, origin: dict[str, str] | None = None) -> Plan:
        self.previews.append(run)
        self.origins.append(origin)
        job = f"job-{len(self.previews)}"
        return Plan(
            job_id=job,
            ok=True,
            summary="계획",
            link=f"sp/jobs?job={job}",
            digest=digest_of(run),
        )

    def applied(self, job_id: str) -> bool | None:
        return job_id in self.applied_jobs or job_id in self.applying_jobs

    def applying(self, job_id: str) -> bool | None:
        return job_id in self.applying_jobs

    def landed(self, job_id: str) -> bool | None:
        """끝나 들어간 적용만 — 적용 중인 것은 아직이다."""
        return job_id in self.applied_jobs and job_id not in self.applying_jobs

    def apply(self, job_id: str) -> dict[str, Any]:
        self.applied_jobs.add(job_id)
        return {"summary": "적용"}


def _pipeline(tmp_path: Path, rows: list[dict[str, Any]]) -> Pipeline:
    recipe = tmp_path / "recipe.table.json"
    recipe.write_text(json.dumps(RECIPE, ensure_ascii=False), encoding="utf-8")
    adapter = _adapter(_paged(rows, []))
    return Pipeline(
        slug="t",
        source="plm",
        adapter=adapter,
        recipe=recipe,
        target=Target(name="sp", server="http://sp.test", token="x"),
        path=tmp_path / "pipeline.json",
    )


def test_한_바퀴_같은_계획은_다시_안_올리고_적용된_것이_기준이_된다(tmp_path: Path) -> None:
    rows = [dict(one) for one in ROWS]
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    pipeline = _pipeline(tmp_path, rows)

    first = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert first.status == "planned", first.message
    assert first.counts["objects"]["plm_model"]["created"] == 3

    # 적용 전에 다시 돌면 — 원천이 그대로라 같은 계획이다. 새로 올리지 않는다.
    again = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert again.status == "waiting" and len(fake.previews) == 1

    assert runner.apply(pipeline, work, fake).status == "applied"  # type: ignore[arg-type]
    state = work.load_state()
    assert (
        state.pending is None
        and state.applied_run
        and state.watermark == ROWS[-1]["updated_at"]
    )

    # 적용된 것이 기준 — 그대로면 보낼 것이 없다.
    assert runner.run(pipeline, work, fake).status == "nothing"  # type: ignore[arg-type]

    # 하나 바뀌고 하나 지워지면 그 둘만 간다.
    rows[0].update(name="갤럭시 S", updated_at="2026-11-04T00:00:00Z")
    rows[2].update(deleted=True, updated_at="2026-11-04T00:00:01Z")
    pipeline = _pipeline(tmp_path, rows)
    pipeline.adapter.source = RestSource(
        **{**pipeline.adapter.source.__dict__, "deleted": "deleted"}
    )
    later = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert later.status == "planned", later.message
    counts = later.counts["objects"]["plm_model"]
    assert (counts["changed"], counts["gone"], counts["unchanged"]) == (1, 1, 1)


def test_적용_상태는_끝나_들어간_것만_적용됨이다() -> None:
    from relay_core.submit import APPLYING, LANDED, NOT_APPLIED, apply_status

    jobs: dict[str, dict[str, Any]] = {
        "c-run": {"status": "running"},
        "c-done": {"status": "done", "result": {"applied": True}},
        "c-refused": {"status": "done", "result": {"applied": False}},
        "c-failed": {"status": "failed"},
    }

    def child(job_id: str) -> dict[str, Any] | None:
        return jobs.get(job_id)

    # SP 0.4.108 이상 — 같은 응답의 `applied_status` 로.
    assert apply_status({"applied_by": "x", "applied_status": "queued"}, child) == APPLYING
    assert apply_status({"applied_by": "x", "applied_status": "done"}, child) == LANDED
    # 옛 SP — 적용 작업을 한 번 더 묻는다.
    assert apply_status({"applied_by": "c-run"}, child) == APPLYING
    assert apply_status({"applied_by": "c-done"}, child) == LANDED
    # 묻는 사이에 끝났지만 들어가지 않았다 — SP 는 다시 적용할 수 있게 둔다.
    assert apply_status({"applied_by": "c-refused"}, child) == NOT_APPLIED
    assert apply_status({"applied_by": "c-failed"}, child) == NOT_APPLIED
    assert apply_status({"applied_by": None}, child) == NOT_APPLIED
    # 모르면 모른다 — 계획에도 적용 작업에도 닿지 않았다.
    assert apply_status(None, child) is None
    assert apply_status({"applied_by": "c-gone"}, child) is None


def test_대상에서_적용_중인_계획은_기준으로_올리지_않고_적용도_다시_넣지_않는다(
    tmp_path: Path,
) -> None:
    rows = [dict(one) for one in ROWS]
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    pipeline = _pipeline(tmp_path, rows)
    assert runner.run(pipeline, work, fake).status == "planned"  # type: ignore[arg-type]
    job = work.load_state().pending.job_id  # type: ignore[union-attr]

    # 사람이 SP 화면에서 [적용]을 눌렀고, 그 적용 작업이 아직 대기 중이다.
    fake.applying_jobs.add(job)
    again = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    state = work.load_state()
    assert state.applied_run is None and state.pending and state.pending.job_id == job
    assert again.status == "waiting" and "실행을 연기했습니다" in again.message
    waited = runner.apply(pipeline, work, fake)  # type: ignore[arg-type]
    assert waited.status == "waiting" and "적용하고 있습니다" in waited.message
    assert job not in fake.applied_jobs  # 다시 넣지 않았다

    # 그 적용이 실패했다 — SP 는 다시 적용할 수 있게 둔다. relay 도 아직 대기 계획이다.
    fake.applying_jobs.discard(job)
    assert runner.apply(pipeline, work, fake).status == "applied"  # type: ignore[arg-type]
    assert work.load_state().applied_run


def test_원천이_기준으로_돌아오면_대기_계획을_거두되_적용_중이면_둔다(tmp_path: Path) -> None:
    """보낼 것이 없는 실행이 실행의 대기 계획을 그대로 두면, 그 계획을 적용했을 때 되돌아간
    원천의 옛 값이 대상에 들어갔다(2026-11-27 점검)."""
    rows = [dict(one) for one in ROWS]
    changed = [dict(one) for one in rows]
    changed[0].update(name="갤럭시 S", updated_at="2026-11-04T00:00:00Z")
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()

    def run(source: list[dict[str, Any]]) -> runner.Outcome:
        return runner.run(_pipeline(tmp_path, source), work, fake, full=True)  # type: ignore[arg-type]

    assert run(rows).status == "planned"
    assert runner.apply(_pipeline(tmp_path, rows), work, fake).status == "applied"  # type: ignore[arg-type]

    # 원천이 바뀌어 계획이 생겼다가, 적용 전에 원천이 원래대로 돌아왔다.
    assert run(changed).status == "planned"
    stale = work.load_state().pending.job_id  # type: ignore[union-attr]
    back = run(rows)
    assert (
        back.status == "nothing" and f"대기 계획({stale[:8]})을 취소했습니다" in back.message
    )
    state = work.load_state()
    assert state.pending is None and state.history[-2]["event"] == "discarded"

    # 대상에서 적용하고 있으면 거두지 않는다 — 실행 자체를 연기한다.
    assert run(changed).status == "planned"
    applying = work.load_state().pending.job_id  # type: ignore[union-attr]
    fake.applying_jobs.add(applying)
    assert run(rows).status == "waiting"
    assert work.load_state().pending.job_id == applying  # type: ignore[union-attr]
