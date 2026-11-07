"""SP 타입 정의 초안 — 대상 SP 에 받을 타입이 **없을 때** 원천 표본에서 타입 정의를 짓는다.

relay 는 대상의 타입을 만들지 않는다(온톨로지는 대상에서 정한다 — 레시피 판은 `ontology` 를 못
가리킨다). 그래서 이것은 **SP 관리자에게 넘길 초안**이다 — SP 의 코어 정의 파일과 같은 모양
(`types[{slug, label, key_policy, properties[{key, label, data_type}]}]`, SP
`pipeline/core/*.json`)이다. 관리자가 읽고 고쳐 대상에 넣은 뒤 파이프라인을 잇는다.

칸의 데이터 타입은 표본 값으로 고른다 — 비지 않은 값이 **모두** 그 모양일 때만(날짜 · 시각 ·
숫자 · 참거짓 · 주소), 아니면 글(긴 것은 긴 글). 고를 값의 목록(`enum`) · 다른 타입 참조
(`object_ref`)는 표본으로는 모른다 — 관리자가 정한다.

같은 칸 대응으로 **레시피의 첫 판**(식별자 · 이름 · 칸마다 열)도 낸다 — 날짜 칸은 `date`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

_DATE = re.compile(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$")
_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$"
)
_NUMBER = re.compile(r"^[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$")
_CODE = re.compile(r"^[+-]?0\d")
_BOOL = {"true", "false", "y", "n", "yes", "no"}
_URL = re.compile(r"^https?://\S+$")
_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
LONG = 200


def data_type(values: Sequence[str]) -> str:
    """표본 값 → SP 의 데이터 타입. 비지 않은 값이 모두 그 모양일 때만 — 아니면 글."""
    seen = [str(one).strip() for one in values if str(one).strip()]
    if not seen:
        return "text"
    checks: tuple[tuple[str, Callable[[str], bool]], ...] = (
        ("date", lambda one: bool(_DATE.match(one))),
        ("datetime", lambda one: bool(_DATETIME.match(one))),
        # 앞에 0 이 붙은 값(`01`)은 코드다 — 숫자로 넣으면 0 이 사라진다(샘플 PLM 의 KindCode).
        ("number", lambda one: bool(_NUMBER.match(one)) and not _CODE.match(one)),
        ("bool", lambda one: one.lower() in _BOOL),
        ("url", lambda one: bool(_URL.match(one))),
    )
    for name, fits in checks:
        if all(fits(one) for one in seen):
            return name
    return "text_long" if any(len(one) > LONG for one in seen) else "text"


def property_key(column: str, taken: set[str]) -> str:
    """열 이름 → 속성 key(소문자 · 숫자 · `_`). 한글 열 이름은 `prop_N` — 관리자가 바꾼다."""
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", column)
    snake = re.sub(r"[^A-Za-z0-9]+", "_", snake).strip("_").lower()
    if not snake or not snake[0].isalpha():
        snake = f"prop_{len(taken) + 1}"
    key, n = snake[:60], 2
    while key in taken or key in ("key", "label"):
        key, n = f"{snake[:56]}_{n}", n + 1
    taken.add(key)
    return key


def draft(
    *,
    type_slug: str,
    label: str,
    columns: Mapping[str, Sequence[str]],
    key_column: str,
    label_column: str = "",
    system: str = "",
) -> dict[str, Any]:
    """`{"ontology": {...}, "recipe": {...}}` — 타입 정의 초안과 그 타입으로 옮기는 레시피의
    첫 판."""
    if not _SLUG.match(type_slug):
        raise ValueError(
            "타입 slug 는 소문자로 시작하는 소문자 · 숫자 · '_' 60자 이내입니다: "
            f"{type_slug!r}"
        )
    if key_column not in columns:
        raise ValueError(f"식별자 열({key_column})이 표본에 없습니다.")
    if label_column and label_column not in columns:
        raise ValueError(f"이름 열({label_column})이 표본에 없습니다.")
    taken: set[str] = set()
    properties: list[dict[str, Any]] = []
    fields: dict[str, Any] = {}
    for column, values in columns.items():
        if column in (key_column, label_column):
            continue
        key = property_key(column, taken)
        kind = data_type(values)
        properties.append({"key": key, "label": column, "data_type": kind})
        fields[key] = (
            {"column": column, "date": True} if kind == "date" else {"column": column}
        )
    where = f"{system} 에서" if system else "원천에서"
    ontology = {
        "types": [
            {
                "slug": type_slug,
                "label": label or type_slug,
                "key_policy": "required",
                "description": f"{where} 데이터 중계(datarelay)가 받는다 — relay 가 지은 "
                "초안: 칸 이름 · 데이터 타입 · 고를 값(enum) · 참조(object_ref)를 확인하세요.",
                "properties": properties,
            }
        ]
    }
    recipe = {
        "format": "sp-table/1",
        "title": f"{label or type_slug}(새 연결)",
        "types": [
            {
                "type_slug": type_slug,
                "key": {"column": key_column},
                "label": {"column": label_column or key_column},
                "fields": fields,
            }
        ],
    }
    return {"ontology": ontology, "recipe": recipe}
