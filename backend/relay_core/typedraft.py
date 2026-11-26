"""SP 타입 정의 초안 — 대상 SP 에 받을 타입이 **없을 때** 원천 표본에서 타입 정의를 짓는다.

relay 는 대상의 타입을 만들지 않는다(온톨로지는 대상에서 정한다 — 레시피 판은 `ontology` 를 못
가리킨다). 그래서 이것은 **SP 관리자에게 넘길 초안**이다 — SP 의 코어 정의 파일과 같은 모양
(`types[{slug, label, key_policy, properties[{key, label, data_type}]}]`, SP
`pipeline/core/*.json`)이다. 관리자가 읽고 고쳐 대상에 넣은 뒤 파이프라인을 잇는다.

칸의 데이터 타입은 표본 값으로 고른다 — 비지 않은 값이 **모두** 그 모양일 때만(날짜 · 시각 ·
숫자 · 참거짓 · 주소), 아니면 글(긴 것은 긴 글). 고를 값의 목록(`enum`) · 다른 타입 참조
(`object_ref`)는 표본으로는 모른다 — 관리자가 정한다.

같은 칸 대응으로 **레시피의 첫 판**(식별자 · 이름 · 칸마다 열)도 낸다 — 날짜 칸은 `date`.

**관계 종류 초안**(`relation_draft`) — 레시피의 관계(`relations`)가 쓰는 관계 종류가 대상에
없을 때. 출발 · 도착 타입은 레시피의 끝점에서(`key_of` · 참조 대조의 대상), 관계 속성의 데이터
타입은 표본 값으로. **개수 제약은 늘 `many_to_many`** 로 둔다 — 표본으로 좁히면 나중 데이터가
어길 때 대상이 계획 전체를 막는다. 표본의 모양(출발 하나에 도착이 몇 개인가)은 알리기만 하고
좁힐지는 사람이 정한다(`shape`).

**사이드바 묶음**(`nav_group`) — 그 타입을 SP 사이드바의 어느 묶음에 둘지. 이미 있는 묶음의
slug, 또는 새 묶음(`{slug, label, icon, …}` — 정의의 `groups` 로 함께 만든다). 안 주면
`suggest_group` 이 대상의 묶음에서 고른다: 같은 원천의 타입이 든 묶음 › 이름이 원천과 같은
묶음 › 없으면 새 묶음. 묶음은 원천으로 잠기지 않는다(SP 의 화면 구성이다 — 적용 뒤에도 SP
관리자가 고친다).
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
#: 사이드바 묶음의 칸 — SP 의 정의 가져오기가 받는 것(`ontology/importer.GROUP_FIELDS`).
GROUP_KEYS = {"slug", "label", "icon", "audience", "color", "parent_slug", "sort_order"}
_GROUP_SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,59}$")
_AUDIENCES = {"everyone", "manager", "system_admin"}
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def check_group(group: Mapping[str, Any]) -> dict[str, Any]:
    """새 사이드바 묶음의 모양 — 잘못이면 `ValueError`(SP 가 거절하기 전에)."""
    unknown = sorted(set(group) - GROUP_KEYS)
    if unknown:
        raise ValueError(f"사이드바 그룹: 지원하지 않는 항목입니다: {', '.join(unknown)}")
    slug = str(group.get("slug") or "")
    if not _GROUP_SLUG.match(slug):
        raise ValueError(
            "사이드바 그룹 slug는 소문자로 시작하는 소문자, 숫자, '_', '-' 60자 이내여야 "
            f"합니다: {slug!r}"
        )
    label = str(group.get("label") or "").strip()
    if not label or len(label) > 100:
        raise ValueError(f"사이드바 그룹 {slug}: 이름(label)이 필요합니다(100자 이내).")
    if "audience" in group and group["audience"] not in _AUDIENCES:
        raise ValueError(
            f"사이드바 그룹 {slug}: audience는 {' · '.join(sorted(_AUDIENCES))} 중 하나여야 "
            "합니다."
        )
    if "color" in group and not _COLOR.match(str(group["color"])):
        raise ValueError(f"사이드바 그룹 {slug}: color는 #rrggbb 형식이어야 합니다.")
    return {**dict(group), "slug": slug, "label": label}


def suggest_group(
    groups: Sequence[Mapping[str, Any]],
    types: Mapping[str, Mapping[str, Any]],
    source: str,
    system: str = "",
) -> dict[str, Any]:
    """그 원천의 새 타입을 둘 사이드바 묶음 — `{"slug", "label", "new", "why"}`.

    1. 같은 원천(`managed_by`)의 타입이 가장 많이 든 묶음 — 사람이 그 원천을 이미 거기서 본다.
    2. slug · 이름이 원천 이름 · 시스템 이름과 같은 묶음.
    3. 없으면 새 묶음(slug 는 원천 이름, 이름은 시스템 이름).
    """
    known = {str(one.get("slug")): one for one in groups if one.get("slug")}
    counts: dict[str, int] = {}
    for one in types.values():
        slug = str(one.get("nav_group_slug") or "")
        if slug in known and source and one.get("managed_by") == source:
            counts[slug] = counts.get(slug, 0) + 1
    if counts:
        best = max(counts, key=lambda slug: (counts[slug], slug))
        return {
            "slug": best,
            "label": str(known[best].get("label") or best),
            "new": False,
            "why": f"원천 {source}의 타입 {counts[best]}개가 속한 그룹",
        }
    names = {one.lower() for one in (source, system) if one}
    for slug, one in known.items():
        if slug.lower() in names or str(one.get("label") or "").lower() in names:
            return {
                "slug": slug,
                "label": str(one.get("label") or slug),
                "new": False,
                "why": "이름이 원천과 같은 그룹",
            }
    slug = re.sub(r"[^a-z0-9_-]", "_", (source or "relay").lower())
    if not slug[:1].isalpha():
        slug = f"g_{slug}"
    return {
        "slug": slug[:60],
        "label": system or (source or "relay").upper(),
        "new": True,
        "why": "해당 그룹이 없어 원천 이름으로 새 그룹 생성",
    }


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
    nav_group: str | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """`{"ontology": {...}, "recipe": {...}}` — 타입 정의 초안과 그 타입으로 옮기는 레시피의
    첫 판. `nav_group` 은 사이드바 묶음 — 있는 묶음의 slug 또는 새 묶음(정의의 `groups` 로)."""
    if not _SLUG.match(type_slug):
        raise ValueError(
            "타입 slug는 소문자로 시작하는 소문자, 숫자, '_' 60자 이내여야 합니다: "
            f"{type_slug!r}"
        )
    if key_column not in columns:
        raise ValueError(f"식별자 열({key_column})이 샘플 데이터에 없습니다.")
    if label_column and label_column not in columns:
        raise ValueError(f"이름 열({label_column})이 샘플 데이터에 없습니다.")
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
    where = f"{system}에서" if system else "원천에서"
    ontology = {
        "types": [
            {
                "slug": type_slug,
                "label": label or type_slug,
                "key_policy": "required",
                "description": f"{where} 데이터 중계(datarelay)로 수신하는 타입입니다. "
                "relay가 작성한 초안이므로 속성 이름, 데이터 타입, 선택 값(enum), "
                "참조(object_ref)를 확인하세요.",
                "properties": properties,
            }
        ]
    }
    if isinstance(nav_group, Mapping):
        group = check_group(nav_group)
        ontology["groups"] = [group]
        ontology["types"][0]["nav_group_slug"] = group["slug"]
    elif nav_group:
        if not _GROUP_SLUG.match(str(nav_group)):
            raise ValueError(f"사이드바 그룹 slug 형식이 아닙니다: {nav_group!r}")
        ontology["types"][0]["nav_group_slug"] = str(nav_group)
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


#: SP 의 개수 제약(`ontology.models.CARDINALITIES`) — 출발 · 도착 쪽에서 하나인가.
CARDINALITIES = ("one_to_one", "one_to_many", "many_to_one", "many_to_many")
#: 관계 속성 하나에 보는 표본 값의 수.
PROPERTY_SAMPLES = 50


def end_types(recipe: Mapping[str, Any], relation: Mapping[str, Any], end: str) -> list[str]:
    """레시피 관계의 한 끝(`src` · `dst`)이 가리키는 대상 타입 — 다른 타입의
    식별자(`key_of`)거나 대상과의 참조 대조(`match.keys`)일 때. 알 수 없으면(열 그대로 ·
    `@파일`) 빈 목록 — 출발은 관계의 `type_slug` 로 본다."""
    spec = relation.get(end)
    if isinstance(spec, Mapping):
        if spec.get("key_of"):
            return [str(spec["key_of"])]
        rule = spec.get("match")
        target = str(rule.get("keys") or "").strip() if isinstance(rule, Mapping) else ""
        if target and not target.startswith("@"):
            return [target]
    if end == "src" and relation.get("type_slug"):
        return [str(relation["type_slug"])]
    return []


def shape(pairs: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """표본의 관계 (출발, 도착)에서 본 모양 — 출발 하나에 도착이 몇 개까지, 도착 하나에 출발이
    몇 개까지. `fits` 는 표본이 어기지 않는 가장 좁은 개수 제약(**제안일 뿐** — 초안은
    `many_to_many`)."""
    by_src: dict[str, set[str]] = {}
    by_dst: dict[str, set[str]] = {}
    for src, dst in pairs:
        by_src.setdefault(src, set()).add(dst)
        by_dst.setdefault(dst, set()).add(src)
    most_dst = max((len(one) for one in by_src.values()), default=0)
    most_src = max((len(one) for one in by_dst.values()), default=0)
    if not pairs:
        fits = None
    elif most_dst <= 1 and most_src <= 1:
        fits = "one_to_one"
    elif most_dst <= 1:
        fits = "many_to_one"
    elif most_src <= 1:
        fits = "one_to_many"
    else:
        fits = "many_to_many"
    return {
        "pairs": len(pairs),
        "max_dst_per_src": most_dst,
        "max_src_per_dst": most_src,
        "fits": fits,
    }


def relation_draft(
    recipe: Mapping[str, Any],
    relation: Mapping[str, Any],
    *,
    pairs: Sequence[tuple[str, str]] | None = None,
    properties: Mapping[str, Sequence[Any]] | None = None,
    system: str = "",
) -> dict[str, Any]:
    """레시피의 관계 하나 → `{"relation_type": SP 관계 종류 정의 초안, "observed": 표본의 모양,
    "check": 사람이 볼 것}`. `pairs` · `properties` 는 레시피 시험이 만든 관계(표본)에서 —
    없으면 모양 없이."""
    slug = str(relation.get("relation") or "")
    if not _SLUG.match(slug):
        raise ValueError(
            f"관계 slug는 소문자로 시작하는 소문자, 숫자, '_' 60자 이내여야 합니다: {slug!r}"
        )
    src = end_types(recipe, relation, "src")
    dst = end_types(recipe, relation, "dst")
    seen = properties or {}
    props: list[dict[str, Any]] = []
    for key in relation.get("properties") or {}:
        values = [str(one) for one in seen.get(str(key), [])][:PROPERTY_SAMPLES]
        props.append({"key": str(key), "label": str(key), "data_type": data_type(values)})
    where = f"{system}에서" if system else "원천에서"
    body: dict[str, Any] = {
        "slug": slug,
        "label": slug[:64],
        "inverse_label": "",
        "description": f"{where} 데이터 중계(datarelay)로 수신하는 관계입니다. relay가 작성한 "
        "초안이므로 이름, 반대 방향 이름, 개수 제약, 순환 금지(acyclic)를 확인하세요.",
        "directed": True,
        "cardinality": "many_to_many",
        "src_type_slugs": src or None,
        "dst_type_slugs": dst or None,
    }
    if props:
        body["properties"] = props
    observed = shape(list(pairs)) if pairs is not None else None
    check = ["label · inverse_label(화면에 보이는 이름)을 업무 용어로 바꾸세요."]
    if not dst:
        check.append(
            "도착 타입을 레시피에서 알 수 없습니다(열 그대로 또는 @파일). dst_type_slugs를 "
            "지정하지 않으면 어느 타입이든 도착할 수 있습니다."
        )
    if observed and observed["fits"] and observed["fits"] != "many_to_many":
        check.append(
            f"표본 {observed['pairs']}건에서는 {_CARDINALITY_TEXT[observed['fits']]}. "
            f"cardinality를 좁힐 수 있습니다(후보: {observed['fits']}). 표본이 원천 전체가 "
            "아니므로 업무 규칙으로 정하세요. 좁히면 어기는 관계가 오는 날 대상이 계획 "
            "전체를 막습니다."
        )
    if src and dst and src == dst:
        check.append("같은 타입끼리의 관계입니다. 순환을 막아야 하면 acyclic: true로 하세요.")
    return {"relation_type": body, "observed": observed, "check": check}


_CARDINALITY_TEXT = {
    "one_to_one": "출발 하나에 도착 하나, 도착 하나에 출발 하나입니다",
    "many_to_one": "출발 하나에 도착이 하나입니다",
    "one_to_many": "도착 하나에 출발이 하나입니다",
}


def check_relation_type(one: Any) -> dict[str, Any]:
    """`type_plan` 에 실을 관계 종류 하나의 모양 — 잘못이면 `ValueError`(SP 가 거절하기 전에,
    빠진 칸만. 나머지는 SP 의 계획이 본다)."""
    if not isinstance(one, Mapping):
        raise ValueError("relation_types의 각 항목은 JSON 객체여야 합니다.")
    slug = str(one.get("slug") or "")
    if not _SLUG.match(slug):
        raise ValueError(
            f"관계 slug는 소문자로 시작하는 소문자, 숫자, '_' 60자 이내여야 합니다: {slug!r}"
        )
    if "cardinality" in one and one["cardinality"] not in CARDINALITIES:
        raise ValueError(
            f"관계 {slug}: cardinality는 {' · '.join(CARDINALITIES)} 중 하나여야 합니다."
        )
    for name in ("src_type_slugs", "dst_type_slugs"):
        found = one.get(name)
        if found is not None and (
            not isinstance(found, list) or not all(isinstance(x, str) for x in found)
        ):
            raise ValueError(f"관계 {slug}: {name}는 타입 slug의 목록이어야 합니다.")
    return dict(one)
