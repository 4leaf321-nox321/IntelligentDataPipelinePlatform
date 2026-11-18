"""한 번에 초안의 레시피 첫 판 — 칸 이름으로 잇기. 이름이 같음 · 표시 이름 · 끝 이름(두 방향,
하나뿐 · 안 쓴 열만), 날짜 칸은 날짜로, 참조 칸은 남긴다. 여러 칸 식별자는 계산 칸."""

from __future__ import annotations

from typing import Any

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


def test_참조_칸은_이름과_식별자_끝이_맞는_열_하나면_match_로_꼭_받는_칸은_알린다() -> None:
    columns = {"MODEL_ID": "model_id", "TASK_CODE": "task.code", "TASK_NAME": "task.name"}
    props: dict[str, dict[str, Any]] = {
        "task": {
            "label": "과제",
            "data_type": "object_ref",
            "ref_type_slug": "plm_task",
            "required": True,
        },
        "project": {
            "label": "프로젝트",
            "data_type": "object_ref",
            "ref_type_slug": "plm_project",
            "required": True,
        },
        "region": {"label": "지역", "data_type": "enum", "enum_options": ["KR", "US"]},
    }
    got = first_recipe("plm_model", columns, props, "MODEL_ID", "")
    fields = got["recipe"]["types"][0]["fields"]
    assert fields["task"] == {
        "column": "TASK_CODE",
        "match": {"keys": "plm_task", "on_missing": "unresolved"},
    }
    (gap,) = got["required_missing"]
    assert gap["property"] == "project" and gap["ref_type_slug"] == "plm_project"
    assert [one["property"] for one in got["unmatched"]] == ["region"]
    assert got["unmatched"][0]["enum_options"] == ["KR", "US"]


def _ref(target: str, label: str, required: bool = False) -> dict[str, Any]:
    return {
        "label": label,
        "data_type": "object_ref",
        "ref_type_slug": target,
        "required": required,
    }


MODEL: dict[str, Any] = {
    "types": {
        "plm_model": {
            "managed_by": "plm",
            "properties": {
                "task": _ref("plm_task", "과제", required=True),
                "site": _ref("t_site", "사이트"),  # 전사 기준정보 — 만들지 않는다
                "region": {"label": "지역", "data_type": "enum", "enum_options": ["KR"]},
                "model_revision": {"label": "리비전", "data_type": "text"},
            },
        },
        "plm_task": {
            "managed_by": "plm",
            "properties": {
                "project": _ref("plm_project", "프로젝트"),
                "test_site": _ref("plm_test_site", "시험 주체"),
            },
        },
        "plm_project": {"managed_by": "plm", "properties": {}},
        "plm_test_site": {"managed_by": "plm", "properties": {}},
        "t_site": {"managed_by": "hub", "properties": {}},
    },
    "relations": [
        {
            "slug": "plm_derived_from",
            "label": "파생",
            "src_type_slugs": ["plm_model"],
            "dst_type_slugs": ["plm_model"],
            "managed_by": "plm",
        }
    ],
}
NESTED = {
    "MODEL_ID": "model_id",
    "MODEL_NAME": "model_name",
    "REGION": "region",
    "REVISION": "revision",
    "BASE_MODEL_ID": "base_model_id",
    "SITE_CODE": "site_code",
    "TASK_CODE": "task.code",
    "TASK_NAME": "task.name",
    "TASK_PROJECT_CODE": "task.project.code",
    "TASK_PROJECT_NAME": "task.project.name",
    "TASK_TEST_SITE_CODE": "task.test_site.code",
    "TASK_TEST_SITE_NAME": "task.test_site.name",
}


def test_한_행을_정의를_따라_여러_타입과_선으로_나눈다() -> None:
    from relay_core.scaffold import split_recipe

    got = split_recipe("plm_model", NESTED, MODEL, "plm", "MODEL_ID", "MODEL_NAME")
    made = {one["type_slug"]: one for one in got["types"]}
    assert [one["type_slug"] for one in got["types"]] == [
        "plm_model",
        "plm_task",
        "plm_project",
        "plm_test_site",
    ]
    assert (made["plm_task"]["via"], made["plm_task"]["key"], made["plm_task"]["label"]) == (
        "plm_model.task",
        "TASK_CODE",
        "TASK_NAME",
    )
    assert made["plm_project"]["key"] == "TASK_PROJECT_CODE"
    assert made["plm_test_site"]["via"] == "plm_task.test_site"
    entries = {one["type_slug"]: one for one in got["recipe"]["types"]}
    model = entries["plm_model"]["fields"]
    assert model["task"] == {"key_of": "plm_task"}
    assert model["site"]["match"]["keys"] == "t_site"  # 주인이 다르면 참조만
    assert model["model_revision"] == {"column": "REVISION"}
    assert "TASK_NAME" not in json_of(model)  # 묶음의 열은 아래 타입의 것
    assert entries["plm_task"]["fields"] == {
        "project": {"key_of": "plm_project"},
        "test_site": {"key_of": "plm_test_site"},
    }
    assert got["recipe"]["relations"] == [
        {
            "relation": "plm_derived_from",
            "type_slug": "plm_model",
            "src": {"key_of": "plm_model"},
            "dst": {"column": "BASE_MODEL_ID"},
            "evidence_note": {"value": "원천 열 BASE_MODEL_ID(한 번에 초안)"},
        }
    ]
    assert got["required_missing"] == [] and "BASE_MODEL_ID" not in got["unused"]


def test_다른_파이프라인이_채우는_타입은_만들지_않고_참조만() -> None:
    from relay_core.scaffold import split_recipe

    got = split_recipe(
        "plm_model",
        NESTED,
        MODEL,
        "plm",
        "MODEL_ID",
        "MODEL_NAME",
        taken={"plm_project": "plm-projects"},
    )
    assert [one["type_slug"] for one in got["types"]] == [
        "plm_model",
        "plm_task",
        "plm_test_site",
    ]
    assert got["skipped"] == [
        {"type_slug": "plm_project", "why": "plm-projects 파이프라인이 채운다 — 참조만"}
    ]
    task = {one["type_slug"]: one for one in got["recipe"]["types"]}["plm_task"]["fields"]
    assert task["project"] == {
        "column": "TASK_PROJECT_CODE",
        "match": {"keys": "plm_project", "on_missing": "blank"},
    }


def test_평평한_열은_이름의_앞부분으로_묶는다() -> None:
    from relay_core.scaffold import split_recipe

    flat = {
        "MODEL_ID": "MODEL_ID",
        "MODEL_NAME": "MODEL_NAME",
        "TASK_CODE": "TASK_CODE",
        "TASK_NAME": "TASK_NAME",
        "PROJECT_CODE": "PROJECT_CODE",
        "PROJECT_NAME": "PROJECT_NAME",
        "SITE_CODE": "SITE_CODE",
        "SITE_NAME": "SITE_NAME",
    }
    model = {**MODEL, "types": {**MODEL["types"]}}
    model["types"]["plm_model"] = {
        "managed_by": "plm",
        "properties": {"task": _ref("plm_task", "과제", required=True)},
    }
    got = split_recipe("plm_model", flat, model, "plm", "MODEL_ID", "MODEL_NAME")
    made = {one["type_slug"]: (one["key"], one["label"]) for one in got["types"]}
    assert made == {
        "plm_model": ("MODEL_ID", "MODEL_NAME"),
        "plm_task": ("TASK_CODE", "TASK_NAME"),
        "plm_project": ("PROJECT_CODE", "PROJECT_NAME"),
        "plm_test_site": ("SITE_CODE", "SITE_NAME"),
    }
    # 원천이 주인이 아니면 나누지 않는다.
    alone = split_recipe("plm_model", flat, model, "mes", "MODEL_ID", "MODEL_NAME")
    assert [one["type_slug"] for one in alone["types"]] == ["plm_model"]
    (entry,) = alone["recipe"]["types"]
    assert entry["fields"]["task"]["match"] == {"keys": "plm_task", "on_missing": "unresolved"}


def json_of(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)
