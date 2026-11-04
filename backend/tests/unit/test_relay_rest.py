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

    with pytest.raises(SourceError, match="거절"):
        _adapter(refused).fetch(None)
    assert len(calls) == 1

    calls.clear()

    def flaky(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(503, text="잠시")
        return httpx.Response(200, json={"items": []})

    assert _adapter(flaky).fetch(None).records == []
    assert len(calls) == 3


def test_식별자가_빈_항목은_멈춘다() -> None:
    adapter = _adapter(lambda _r: httpx.Response(200, json={"items": [{"id": ""}]}))
    with pytest.raises(SourceError, match="식별자"):
        adapter.fetch(None)


class FakeSubmitter:
    """대상 SP 대신 — 미리보기는 지문만 남기고, 적용은 표시만 한다."""

    def __init__(self) -> None:
        self.previews: list[Path] = []
        self.applied_jobs: set[str] = set()

    def preview(self, run: Path) -> Plan:
        self.previews.append(run)
        job = f"job-{len(self.previews)}"
        return Plan(
            job_id=job,
            ok=True,
            summary="계획",
            link=f"sp/jobs?job={job}",
            digest=digest_of(run),
        )

    def applied(self, job_id: str) -> bool | None:
        return job_id in self.applied_jobs

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
