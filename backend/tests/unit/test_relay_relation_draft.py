"""관계 종류 초안(`relay_core.typedraft.relation_draft`) — 레시피의 관계에서 출발 · 도착
타입을, 표본에서 관계 속성의 자료형과 모양을. **개수 제약은 늘 `many_to_many`** 이고 좁힐 수
있다는 것은 알리기만 한다(표본이 원천 전체가 아니다)."""

from __future__ import annotations

from typing import Any

import pytest

from relay_core.typedraft import check_relation_type, end_types, relation_draft, shape

RECIPE: dict[str, Any] = {
    "types": [{"type_slug": "plm_task"}, {"type_slug": "plm_model"}],
    "relations": [
        {
            "relation": "plm_task_model",
            "type_slug": "plm_task",
            "src": {"key_of": "plm_task"},
            "dst": {"key_of": "plm_model"},
            "properties": {"evidence_count": {"column": "N"}, "role": {"column": "ROLE"}},
        },
        {
            "relation": "plm_task_site",
            "type_slug": "plm_task",
            "src": {"key_of": "plm_task"},
            "dst": {"column": "SITE", "match": {"keys": "t_site"}},
        },
        {
            "relation": "plm_task_owner",
            "type_slug": "plm_task",
            "src": {"column": "TASK_ID"},
            "dst": {"column": "OWNER", "match": {"keys": "@owners.keys"}},
        },
    ],
}


def test_끝점에서_출발과_도착_타입을_고른다() -> None:
    task_model, task_site, task_owner = RECIPE["relations"]
    assert end_types(RECIPE, task_model, "src") == ["plm_task"]
    assert end_types(RECIPE, task_model, "dst") == ["plm_model"]
    assert end_types(RECIPE, task_site, "dst") == ["t_site"]  # 대상과의 참조 대조
    assert end_types(RECIPE, task_owner, "src") == ["plm_task"]  # 열 그대로면 출발 타입
    assert end_types(RECIPE, task_owner, "dst") == []  # @파일 — 모른다


def test_초안은_늘_many_to_many_이고_표본의_모양은_알리기만_한다() -> None:
    pairs = [("T1", "M1"), ("T2", "M1"), ("T3", "M2")]  # 과제마다 모델 하나
    made = relation_draft(
        RECIPE,
        RECIPE["relations"][0],
        pairs=pairs,
        properties={"evidence_count": [3, 12], "role": ["주", "부"]},
        system="PLM",
    )
    kind = made["relation_type"]
    assert kind["cardinality"] == "many_to_many"
    assert (kind["src_type_slugs"], kind["dst_type_slugs"]) == (["plm_task"], ["plm_model"])
    assert kind["properties"] == [
        {"key": "evidence_count", "label": "evidence_count", "data_type": "number"},
        {"key": "role", "label": "role", "data_type": "text"},
    ]
    assert "PLM에서" in kind["description"]
    assert made["observed"] == {
        "pairs": 3,
        "max_dst_per_src": 1,
        "max_src_per_dst": 2,
        "fits": "many_to_one",
    }
    assert any("(후보: many_to_one)" in one for one in made["check"])
    check_relation_type(kind)  # type_plan 이 그대로 받는다


def test_모양을_모르면_말하지_않고_도착을_모르면_그렇다고_한다() -> None:
    made = relation_draft(RECIPE, RECIPE["relations"][2])
    assert made["observed"] is None
    assert made["relation_type"]["dst_type_slugs"] is None
    assert any("도착 타입을 레시피에서 알 수 없습니다" in one for one in made["check"])
    self_loop = {
        "relation": "plm_derived_from",
        "type_slug": "plm_model",
        "src": {"key_of": "plm_model"},
        "dst": {"column": "BASE", "match": {"keys": "plm_model"}},
    }
    assert any("acyclic" in one for one in relation_draft(RECIPE, self_loop)["check"])


@pytest.mark.parametrize(
    ("pairs", "fits"),
    [
        ([], None),
        ([("a", "x"), ("b", "y")], "one_to_one"),
        ([("a", "x"), ("a", "y")], "one_to_many"),
        ([("a", "x"), ("a", "y"), ("b", "x")], "many_to_many"),
    ],
)
def test_표본의_모양(pairs: list[tuple[str, str]], fits: str | None) -> None:
    assert shape(pairs)["fits"] == fits


def test_관계_종류의_모양은_보내기_전에_본다() -> None:
    with pytest.raises(ValueError, match="관계 slug"):
        check_relation_type({"slug": "Bad-Slug"})
    with pytest.raises(ValueError, match="cardinality"):
        check_relation_type({"slug": "plm_x", "cardinality": "many"})
    with pytest.raises(ValueError, match="src_type_slugs"):
        check_relation_type({"slug": "plm_x", "src_type_slugs": "plm_task"})
    with pytest.raises(ValueError, match="관계 slug"):
        relation_draft(RECIPE, {"relation": "", "type_slug": "plm_task"})


def test_일괄_초안은_대상에_없는_관계_종류를_레시피에_넣지_않고_제안한다() -> None:
    from relay_core.scaffold import split_recipe
    from tests.unit.test_relay_scaffold import MODEL, NESTED

    model = {**MODEL, "relations": []}  # 대상에 관계 종류가 없다
    columns = {**NESTED, "PARENT_TASK_CODE": "parent_task_code"}
    got = split_recipe("plm_model", columns, model, "plm", "MODEL_ID", "MODEL_NAME")
    assert "relations" not in got["recipe"]
    proposed = got["relation_drafts"]
    lines = {one["relation"]: one for one in proposed["recipe_relations"]}
    assert lines["plm_model_base_model"]["dst"] == {"column": "BASE_MODEL_ID"}
    assert lines["plm_model_parent_task"]["dst"] == {"column": "PARENT_TASK_CODE"}
    kinds = {one["slug"]: one for one in proposed["relation_types"]}
    assert kinds["plm_model_base_model"]["dst_type_slugs"] == ["plm_model"]
    assert kinds["plm_model_parent_task"]["dst_type_slugs"] == ["plm_task"]
    # 대상에 맞는 종류가 있으면 지금처럼 레시피에 넣고 제안하지 않는다.
    known = split_recipe("plm_model", NESTED, MODEL, "plm", "MODEL_ID", "MODEL_NAME")
    assert known["recipe"]["relations"][0]["relation"] == "plm_derived_from"
    assert known["relation_drafts"] is None
