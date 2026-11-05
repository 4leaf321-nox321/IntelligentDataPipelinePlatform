"""OData 어댑터 · 정의 읽기(비밀 없이) · 필드 계보 · 전량 날.

PLM 의 응답이 OData 라는 것까지만 안다(2026-11-05). 그래서 v4 · v2 둘 다 — 봉투 · 다음 쪽 ·
증분 조건 · v2 의 `/Date()/` — 를 여기서 지킨다. 진짜 서비스의 엔티티 셋 · 칸 이름 · 인증은
붙는 날 정의에 적는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from relay_core import runner
from relay_core.adapters.base import Fetched
from relay_core.adapters.odata import ODataAdapter, ODataSource
from relay_core.definition import Pipeline, Target
from relay_core.describe import describe
from relay_core.state import Workdir
from tests.unit.test_relay_core import RECIPE

COLUMNS = {"MODEL_ID": "ModelId", "TASK_CODE": "Task.Code", "PROJECT": "Task.Project.Name"}


def _adapter(handler: Any, **over: Any) -> ODataAdapter:
    spec: dict[str, Any] = {
        "service_url": "http://plm.test/odata/v4/Plm",
        "entity_set": "DevModels",
        "key": "ModelId",
        "columns": COLUMNS,
        "modified_field": "ModifiedOn",
        "expand": "Task($expand=Project)",
        "page_size": 2,
    }
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return ODataAdapter(ODataSource(**{**spec, **over}), client=client, sleep=lambda _s: None)


def _row(key: str, at: str) -> dict[str, Any]:
    return {
        "ModelId": key,
        "ModifiedOn": at,
        "Task": {"Code": "T1", "Project": {"Name": "알파"}},
    }


def test_v4_는_nextLink_를_따르고_증분은_filter_로() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "page2" in str(request.url):
            return httpx.Response(200, json={"value": [_row("M3", "2026-11-03T00:00:00Z")]})
        return httpx.Response(
            200,
            json={
                "value": [
                    _row("M1", "2026-11-01T00:00:00Z"),
                    _row("M2", "2026-11-02T00:00:00Z"),
                ],
                "@odata.nextLink": "http://plm.test/odata/v4/Plm/DevModels?page2=1",
            },
        )

    got = _adapter(handler).fetch("2026-10-31T00:00:00Z")
    assert [one.key for one in got.records] == ["M1", "M2", "M3"]
    assert dict(got.records[0].values) == {
        "MODEL_ID": "M1",
        "TASK_CODE": "T1",
        "PROJECT": "알파",
    }
    first = seen[0].url.params
    assert first["$filter"] == "ModifiedOn gt 2026-10-31T00:00:00Z"
    assert first["$orderby"] == "ModelId" and first["$expand"] == "Task($expand=Project)"
    assert "page2" in str(seen[1].url)  # 서버가 준 다음 주소를 그대로
    assert got.watermark == "2026-11-03T00:00:00Z" and not got.full


def test_다음_쪽_주소가_다른_곳이면_따라가지_않는다() -> None:
    """응답이 준 주소로 인증 머리글을 실어 보내면 비밀이 그리로 나간다(2026-11-06 보안 검토).
    같은 곳의 상대 주소는 따라간다."""
    from relay_core.adapters.rest import Auth, SourceError

    seen: list[httpx.Request] = []

    def handler(next_link: str) -> Any:
        def answer(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if "page2" in str(request.url):
                return httpx.Response(200, json={"value": []})
            rows = [_row("M1", "2026-11-01T00:00:00Z"), _row("M2", "2026-11-02T00:00:00Z")]
            return httpx.Response(200, json={"value": rows, "@odata.nextLink": next_link})

        return answer

    auth = Auth("bearer", "sekret-token-123")
    for far in (
        "http://evil.test/odata/v4/Plm/DevModels?page2=1",
        "https://plm.test/odata/v4/Plm/DevModels?page2=1",
        "http://plm.test:8443/odata/v4/Plm/DevModels?page2=1",
        "//evil.test/x?page2=1",
    ):
        seen.clear()
        with pytest.raises(SourceError, match="다른 곳"):
            _adapter(handler(far), auth=auth).fetch(None)
        assert [one.url.host for one in seen] == ["plm.test"]
    seen.clear()
    got = _adapter(handler("DevModels?page2=1"), auth=auth).fetch(None)
    assert len(got.records) == 2 and str(seen[1].url).startswith("http://plm.test/odata/v4/")


def test_v2_는_d_results_와_Date_를_풀고_껍질을_벗긴다() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "d": {
                    "results": [
                        {
                            "__metadata": {"uri": "x"},
                            "ModelId": "M1",
                            "ModifiedOn": "/Date(1730764800000)/",
                            "Task": {"Code": "T1", "Project": {"__deferred": {"uri": "y"}}},
                            "Tags": {"results": [{"Name": "a"}, {"Name": "b"}]},
                        }
                    ]
                }
            },
        )

    adapter = _adapter(
        handler, version="2", columns={**COLUMNS, "TAGS": "Tags.Name", "AT": "ModifiedOn"}
    )
    (one,) = adapter.fetch("2026-11-01T00:00:00Z").records
    assert one.values["AT"] == "2024-11-05T00:00:00.000Z"
    assert one.values["TAGS"] == "a;b" and one.values["PROJECT"] == ""
    params = seen[0].url.params
    assert params["$filter"] == "ModifiedOn gt datetime'2026-11-01T00:00:00'"
    assert params["$format"] == "json"


def test_다음_주소가_없으면_skip_으로_넘긴다() -> None:
    rows = [_row(f"M{i}", f"2026-11-0{i}T00:00:00Z") for i in range(1, 6)]

    def handler(request: httpx.Request) -> httpx.Response:
        skip = int(request.url.params.get("$skip", "0"))
        top = int(request.url.params["$top"])
        return httpx.Response(200, json={"value": rows[skip : skip + top]})

    got = _adapter(handler).fetch(None)
    assert [one.key for one in got.records] == ["M1", "M2", "M3", "M4", "M5"] and got.full


def test_정의를_비밀_없이_읽고_필드_계보를_낸다(tmp_path: Path) -> None:
    (tmp_path / "recipe.table.json").write_text(json.dumps(RECIPE), encoding="utf-8")
    definition = {
        "format": "relay-pipeline/1",
        "slug": "plm-model",
        "source": "plm",
        "system": "PLM",
        "adapter": {
            "kind": "odata",
            "service_url": "http://plm.test/odata/v4/Plm",
            "entity_set": "DevModels",
            "key": "ModelId",
            "columns": {
                "MODEL_ID": "ModelId",
                "MODEL_NAME": "Name",
                "KIND": "KindCode",
                "BASE": "BaseModelId",
            },
            "auth": {"kind": "bearer", "token_env": "NOT_SET_ANYWHERE"},
        },
        "recipe": "recipe.table.json",
        "target": {"name": "jvhub", "server": "http://sp.test/", "token_env": "ALSO_NOT_SET"},
    }
    path = tmp_path / "pipeline.json"
    path.write_text(json.dumps(definition), encoding="utf-8")

    found = describe(path)
    assert found.problems == []
    assert found.endpoint == "http://plm.test/odata/v4/Plm/DevModels"
    assert found.endpoint_label == "OData DevModels · plm.test"
    assert found.target_server == "http://sp.test" and found.types == ["plm_model"]
    kind = next(one for one in found.lineage if one.target_field == "model_kind")
    assert kind.columns == ["KIND"] and kind.source_paths == ["KindCode"]
    assert kind.transform == "값 대응 2개"
    edge = next(one for one in found.lineage if one.target_field == "→ plm_derived_from")
    assert edge.source_paths == ["BaseModelId"]


class _Counting:
    """전량 날을 정하는지만 본다 — `since` 를 적어 두는 가짜 어댑터."""

    def __init__(self) -> None:
        self.source = ODataSource(
            service_url="x", entity_set="y", key="k", columns={}, full_every=3
        )
        self.asked: list[str | None] = []

    def fetch(self, since: str | None) -> Fetched:
        self.asked.append(since)
        return Fetched(records=[], watermark="2026-11-05T00:00:00Z", full=since is None)


def test_삭제_칸이_없는_원천은_증분_몇_번마다_전량을_받는다(tmp_path: Path) -> None:
    fake = _Counting()
    pipeline = Pipeline(
        slug="t",
        source="plm",
        adapter=fake,  # type: ignore[arg-type]
        recipe=tmp_path / "r.json",
        target=Target(name="sp", server="http://sp.test", token="x"),
        path=tmp_path / "p.json",
    )
    work = Workdir(tmp_path / "work", "t")
    for _ in range(5):
        runner.run(pipeline, work, submit=object())  # type: ignore[arg-type]
    # 처음은 전량, 그 뒤 증분 둘, 셋째가 전량 … (full_every=3)
    assert fake.asked == [
        None,
        "2026-11-05T00:00:00Z",
        "2026-11-05T00:00:00Z",
        None,
        "2026-11-05T00:00:00Z",
    ]
