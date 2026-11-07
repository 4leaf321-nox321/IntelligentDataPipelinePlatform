"""계산 칸 · SP 타입 정의 초안.

- 계산 칸은 변환할 때 미러 **전체**에 얹는다 — 규칙을 바꾸면 옛 행도 같이 바뀐다. 못 바꾸는
  값은 빈 값(짐작하지 않는다). 미러에는 원천 그대로가 남는다.
- 타입 정의 초안은 표본 값이 모두 그 모양일 때만 그 타입 — 아니면 글.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from relay_core import compute, runner
from relay_core.compute import ComputeError, values_with
from relay_core.definition import DefinitionError, check_body
from relay_core.state import Workdir
from relay_core.typedraft import data_type, draft
from tests.unit.test_relay_intake import _Adapter, _model, _pipeline
from tests.unit.test_relay_rest import FakeSubmitter

RULES = compute.check(
    {
        "FULL": {"template": "{LAST} {FIRST}"},
        "KG": {"number": "GRAMS", "multiply": 0.001, "round": 3},
        "AT": {"datetime": "TS", "format": "%Y%m%d%H%M%S", "timezone": "Asia/Seoul"},
        "DAY": {
            "datetime": "TS",
            "format": "%Y%m%d%H%M%S",
            "timezone": "Asia/Seoul",
            "output": "date",
        },
        "PART": {"replace": "RAW", "pattern": "^P-", "with": ""},
        "OWNER": {"first": ["OWNER_ID", "CREATOR_ID"]},
        "LABEL": {"template": "{PART}/{KG}"},  # 앞의 계산 칸을 읽는다
    }
)


def test_계산_칸은_차례대로_계산하고_못_바꾸는_값은_비운다() -> None:
    row = {
        "LAST": "김",
        "FIRST": "철수",
        "GRAMS": "1,250",
        "TS": "20261107093000",
        "RAW": "P-A100",
        "OWNER_ID": "",
        "CREATOR_ID": "u7",
    }
    got = values_with(row, RULES)
    assert got["FULL"] == "김 철수" and got["KG"] == "1.25" and got["PART"] == "A100"
    assert got["AT"] == "2026-11-07T00:30:00Z"  # 서울 09:30 = UTC 00:30
    assert got["DAY"] == "2026-11-07" and got["OWNER"] == "u7" and got["LABEL"] == "A100/1.25"
    assert row["GRAMS"] == "1,250"  # 원래 값은 그대로

    odd = values_with({"GRAMS": "약 3kg", "TS": "어제", "LAST": "", "FIRST": ""}, RULES)
    assert odd["KG"] == "" and odd["AT"] == "" and odd["FULL"] == ""


def test_계산_칸의_모양을_먼저_본다() -> None:
    for wrong, said in (
        ([1], "모양"),
        ({"A": {"template": "{X}", "number": "Y"}}, "하나만"),
        ({"A": {"sum": "X"}}, "하나만"),
        ({"A": {"number": "X", "scale": 2}}, "모르는 칸"),
        ({"A": {"replace": "X", "pattern": "("}}, "정규식"),
        ({"A": {"datetime": "X", "timezone": "Mars/Base"}}, "timezone"),
        ({"A": {"number": "X", "round": 20}}, "round"),
        ({"MODEL_ID": {"template": "{X}"}}, "이미 내는 열"),
    ):
        with pytest.raises(ComputeError, match=said):
            compute.check(wrong, ["MODEL_ID"])
    assert compute.inputs(RULES["FULL"]) == ["LAST", "FIRST"]
    assert "Asia/Seoul" in compute.describe(RULES["AT"])

    body: dict[str, Any] = {
        "format": "relay-pipeline/1",
        "slug": "t",
        "source": "plm",
        "adapter": {
            "kind": "rest",
            "base_url": "http://plm.test",
            "path": "/x",
            "key": "id",
            "columns": {"ID": "id"},
        },
        "target": {"server": "http://sp.test", "token_env": "T"},
        "computed": {"ID": {"template": "{ID}"}},
    }
    with pytest.raises(DefinitionError, match="이미 내는 열"):
        check_body(body)


def test_계산_칸은_변환할_때_미러_전체에_얹어_규칙을_바꾸면_옛_행도_바뀐다(
    tmp_path: Path,
) -> None:
    adapter = _Adapter()
    adapter.records = [_model(n) for n in range(1, 4)]
    recipe = json.loads(
        json.dumps(
            {
                "format": "sp-table/1",
                "types": [
                    {
                        "type_slug": "plm_model",
                        "key": {"column": "MODEL_ID"},
                        "label": {"column": "TITLE"},
                    }
                ],
            }
        )
    )
    pipeline = _pipeline(tmp_path, adapter)
    pipeline.recipe.write_text(json.dumps(recipe), encoding="utf-8")
    first = dataclasses.replace(
        pipeline, computed=compute.check({"TITLE": {"template": "[{KIND}] {MODEL_NAME}"}})
    )
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    assert runner.run(first, work, fake).status == "planned"  # type: ignore[arg-type]
    runner.apply(first, work, fake)  # type: ignore[arg-type]
    assert "TITLE" not in next(work.mirror_rows()).values  # 미러에는 원천 그대로

    adapter.full = False
    adapter.records = []  # 원천은 그대로 — 규칙만 바꾼다
    second = dataclasses.replace(
        pipeline, computed=compute.check({"TITLE": {"template": "{MODEL_NAME} ({KIND})"}})
    )
    later = runner.run(second, work, fake)  # type: ignore[arg-type]
    assert later.status == "planned" and later.counts["objects"]["plm_model"]["changed"] == 3


def test_타입_정의_초안은_표본이_모두_그_모양일_때만_그_타입이다() -> None:
    assert data_type(["2026-11-07", "2026.1.3", ""]) == "date"
    assert data_type(["2026-11-07T09:30:00Z", "2026-11-07 10:00"]) == "datetime"
    assert data_type(["1,250", "-3.5", "0", "0.25"]) == "number"
    assert data_type(["01", "02"]) == "text"  # 앞에 0 이 붙은 코드 — 숫자면 0 이 사라진다
    assert data_type(["true", "N"]) == "bool"
    assert data_type(["https://plm/x/1"]) == "url"
    assert data_type(["12", "약 3"]) == "text"
    assert data_type(["x" * 300]) == "text_long" and data_type([]) == "text"

    got = draft(
        type_slug="mes_lot",
        label="로트",
        columns={
            "LOT_ID": ["L1", "L2"],
            "LOT_NAME": ["로트 1", "로트 2"],
            "MadeOn": ["2026-11-01", "2026-11-02"],
            "QTY": ["10", "20"],
            "작업자": ["김", "이"],
        },
        key_column="LOT_ID",
        label_column="LOT_NAME",
        system="MES",
    )
    (one,) = got["ontology"]["types"]
    assert (
        one["slug"] == "mes_lot"
        and one["key_policy"] == "required"
        and "MES" in one["description"]
    )
    assert [(p["key"], p["data_type"]) for p in one["properties"]] == [
        ("made_on", "date"),
        ("qty", "number"),
        ("prop_3", "text"),  # 한글 열 이름은 관리자가 바꾼다
    ]
    fields = got["recipe"]["types"][0]["fields"]
    assert fields["made_on"] == {"column": "MadeOn", "date": True}
    assert got["recipe"]["types"][0]["key"] == {"column": "LOT_ID"}
    with pytest.raises(ValueError, match="slug"):
        draft(type_slug="Bad-Slug", label="", columns={"A": []}, key_column="A")
    with pytest.raises(ValueError, match="식별자"):
        draft(type_slug="t", label="", columns={"A": []}, key_column="B")
