"""한 번에 초안의 레시피 첫 판 — 칸 이름으로 잇기. 이름이 같음 · 표시 이름 · 끝 이름(두 방향,
하나뿐 · 안 쓴 열만), 날짜 칸은 날짜로, 참조 칸은 남긴다. 여러 칸 식별자는 계산 칸."""

from __future__ import annotations

import pytest

from relay_core.scaffold import first_recipe, key_column, label_column

COLUMNS = {
    "MODEL_ID": "model_id",
    "MODEL_NAME": "model_name",
    "REVISION": "revision",
    "TASK_STATUS": "task.status_cd",
    "MADE": "made_on",
    "KIND_CD": "kind_cd",
}
PROPS = {
    "model_revision": {"label": "모델 리비전", "data_type": "text"},
    "status": {"label": "상태", "data_type": "text"},
    "made_on": {"label": "생산일", "data_type": "date"},
    "task": {"label": "과제", "data_type": "object_ref"},
    "model_kind": {"label": "모델 종류", "data_type": "text"},
}


def test_칸_이름으로_잇고_근거를_남긴다() -> None:
    got = first_recipe("plm_model", COLUMNS, PROPS, "MODEL_ID", "MODEL_NAME")
    why = {one["property"]: (one["column"], one["why"]) for one in got["matched"]}
    assert why == {
        "model_revision": ("REVISION", "칸 이름이 열 이름으로 끝남"),
        "status": ("TASK_STATUS", "열 이름이 칸 이름으로 끝남"),
        "made_on": ("MADE", "이름이 같음"),  # 원천 자리 made_on 과 같다
    }
    fields = got["recipe"]["types"][0]["fields"]
    assert fields["made_on"] == {"column": "MADE", "date": True}
    assert [one["property"] for one in got["references"]] == ["task"]
    assert [one["property"] for one in got["unmatched"]] == ["model_kind"]
    assert got["unused"] == ["KIND_CD"]
    assert got["recipe"]["types"][0]["key"] == {"column": "MODEL_ID"}


def test_끝_이름이_여럿이면_고르지_않는다() -> None:
    columns = {"A_STATUS": "a", "B_STATUS": "b", "ID": "id"}
    got = first_recipe("t", columns, {"status": {"data_type": "text"}}, "ID", "")
    assert got["matched"] == [] and got["unmatched"][0]["property"] == "status"


def test_여러_칸_식별자는_계산_칸을_더한다() -> None:
    assert key_column(COLUMNS, "model_id") == {"column": "MODEL_ID"}
    both = key_column(COLUMNS, ["model_id", "revision"])
    assert both == {
        "column": "RELAY_KEY",
        "computed": {"RELAY_KEY": {"template": "{MODEL_ID}|{REVISION}"}},
    }
    taken = key_column({**COLUMNS, "RELAY_KEY": "x"}, ["model_id", "revision"])
    assert taken["column"] == "RELAY_KEY_2"
    with pytest.raises(ValueError, match="열\\(columns\\)에 없습니다"):
        key_column(COLUMNS, ["nope"])
    assert label_column(COLUMNS, ["model_name"]) == "MODEL_NAME"
    assert label_column({"X": "x", "LOT_NM": "nm"}) == "LOT_NM"
