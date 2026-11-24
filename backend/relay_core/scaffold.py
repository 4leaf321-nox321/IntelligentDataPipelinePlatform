"""한 번에 초안 — 원천의 열과 대상 타입의 칸을 **이름으로** 잇는 레시피의 첫 판(MCP
`pipeline_scaffold`). 화면의 「새 연결」 이 사람에게 고르게 하는 것을 AI 가 한 번에 하게 하는
재료다.

- 칸 이름 · 표시 이름이 열 이름 · 원천 자리와 같으면 잇는다(대소문자 · `_` · `.` 무시). 열
  이름이 칸 이름으로 끝나는 것(`TASK_STATUS` ↔ `status`), 칸 이름이 열 이름으로 끝나는 것
  (`model_revision` ↔ `REVISION`)은 4자 이상 · 하나뿐 · 아직 안 쓴 열일 때만.
- 날짜 칸(`date`)은 날짜로 바꾼다(`"date": true`).
- **참조 칸(`object_ref`)** 은 칸 이름에 식별자 꼴의 끝이 붙은 열(`task` ↔ `TASK_CODE` ·
  `TASK_ID` · `task.code`)이 **하나뿐일 때만** 대상의 그 타입(`ref_type_slug`)의 식별자와
  맞추는 규칙(`match`)으로 잇는다 — 꼭 받는 칸이면 못 맞춘 것을 미해결로(`on_missing:
  unresolved` — 대상이 거절하기 전에 멈춘다). 아니면 남기고 후보 열을 함께 돌려준다.
- 대상이 **꼭 받는 칸**(`required`)이 끝내 비면 `required_missing` 으로 — 그대로 사용하면
  실행이 대상에서 멈춘다(2026-11-18 Kafka 점검에서 「과제: 값이 필요합니다」).
- 남은 칸 · 참조 칸 · 안 쓴 열을 함께 돌려준다 — AI 가 읽고 레시피를 고친다.
- 식별자가 여러 칸이면 그 값을 잇는 계산 칸(`RELAY_KEY = {A}|{B}`)을 정의에 더하고 그 열을
  레시피의 식별자로 쓴다(미러의 식별자와 같은 모양 — 화면의 「새 연결」 과 같다).

**한 행을 여러 타입으로 나누기**(`split_recipe`) — 대상의 정의를 따라간다:

- 주 타입의 참조 칸이 가리키는 타입(`ref_type_slug`)이 **같은 원천이 주인**(`managed_by`)이고
  다른 파이프라인이 채우지 않으면, 그 칸 이름의 **열 묶음**(원천 자리 `task.*`, 또는 열 이름
  `TASK_*`)에서 식별자(`…_CODE` · `…_ID`) · 이름(`…_NAME`)을 찾아 그 타입의 객체도 만들고 참조
  칸을 `{"key_of": …}` 로 잇는다. 묶음 안에서 다시 따라간다(과제 → 프로젝트). 묶음의 열은 그
  타입의 것이라 위 타입의 칸 맞추기에서 뺀다.
- 주인이 다르면(전사 기준정보 · 다른 시스템) 만들지 않는다 — 위의 `match` 참조.
- 대상의 관계 종류 중 출발이 묶음의 타입이고 도착이 그 타입 자신이거나 묶음 안인 것은, 도착
  타입 이름이 든 식별자 꼴의 열이 하나뿐이면(`plm_derived_from` ↔ `BASE_MODEL_ID`) 선으로
  넣는다. 아니면 후보로만 돌려준다.

이것은 제안이다 — 저장 전에 시험(`pipeline_test`)이 「사용하면 보낼 것」 으로 확인한다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: 여러 칸 식별자의 계산 칸 이름 — 어댑터의 열과 겹치면 뒤에 수를 붙인다.
KEY_COLUMN = "RELAY_KEY"
_REF = {"object_ref", "ref", "reference", "relation"}
_LABEL_WORDS = ("NAME", "TITLE", "LABEL", "NM")
#: 식별자 꼴의 끝 — 참조 칸의 열을 고를 때(`task` + `code`).
_ID_ENDS = ("code", "id", "key", "no", "cd")


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(text).lower())


def key_column(columns: Mapping[str, str], key: str | Sequence[str]) -> dict[str, Any]:
    """식별자 자리 → 레시피가 읽을 열. 여러 칸이면 계산 칸까지 — `{"column", "computed"?}`."""
    by_path = {path: column for column, path in columns.items()}
    paths = [key] if isinstance(key, str) else list(key)
    missing = [path for path in paths if path not in by_path]
    if missing:
        raise ValueError(f"식별자 위치가 열(columns)에 없습니다: {', '.join(missing)}")
    if len(paths) == 1:
        return {"column": by_path[paths[0]]}
    name, n = KEY_COLUMN, 2
    while name in columns:
        name, n = f"{KEY_COLUMN}_{n}", n + 1
    template = "|".join("{" + by_path[path] + "}" for path in paths)
    return {"column": name, "computed": {name: {"template": template}}}


def label_column(columns: Mapping[str, str], hints: Sequence[str] = ()) -> str:
    """이름 칸 — 탐색의 단서(`hints.label`)가 먼저, 아니면 이름 · 제목 같은 열."""
    by_path = {path: column for column, path in columns.items()}
    for path in hints:
        if path in by_path:
            return by_path[path]
    for column in columns:
        if any(word in column.upper() for word in _LABEL_WORDS):
            return column
    return ""


def _brief(key: str, prop: Mapping[str, Any]) -> dict[str, Any]:
    brief: dict[str, Any] = {
        "property": key,
        "label": prop.get("label") or "",
        "data_type": str(prop.get("data_type") or ""),
    }
    if prop.get("required"):
        brief["required"] = True
    if prop.get("enum_options"):
        brief["enum_options"] = list(prop["enum_options"])
    return brief


def _fields(
    columns: Mapping[str, str],
    props: Mapping[str, Mapping[str, Any]],
    used: set[str],
    preset: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(칸 규칙, 이은 것, 못 이은 것, 참조 칸) — `preset` 은 이미 정한 칸(`key_of`)."""
    names: dict[str, set[str]] = {}
    for column, path in columns.items():
        names[column] = {_norm(column), _norm(path), _norm(path.rsplit(".", 1)[-1])}
    fields: dict[str, Any] = dict(preset or {})
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for key, prop in props.items():
        if key in fields:
            continue
        kind = str(prop.get("data_type") or "")
        brief = _brief(key, prop)
        if kind in _REF:
            ref = str(prop.get("ref_type_slug") or "")
            want = _norm(key)
            exact = [
                c
                for c in columns
                if c not in used
                and want
                and (
                    {
                        _norm(c),
                        _norm(columns[c]),
                        _norm(".".join(columns[c].split(".")[-2:])),  # task.project.code
                    }
                    & {want + end for end in _ID_ENDS}
                )
            ]
            if ref and len(exact) == 1:
                fields[key] = {
                    "column": exact[0],
                    "match": {
                        "keys": ref,
                        "on_missing": "unresolved" if prop.get("required") else "blank",
                    },
                }
                matched.append(
                    {
                        "property": key,
                        "column": exact[0],
                        "why": f"참조: {ref}의 식별자와 대조(match)",
                    }
                )
                used.add(exact[0])
            else:
                near = [c for c in columns if want and want in _norm(c)]
                references.append({**brief, "ref_type_slug": ref, "candidates": near[:8]})
            continue
        want = _norm(key)
        label = _norm(str(prop.get("label") or ""))
        column, why = "", ""
        exact = [c for c, n in names.items() if want and want in n]
        if exact:
            column, why = exact[0], "이름 일치"
        elif label and (same := [c for c, n in names.items() if label in n]):
            column, why = same[0], "표시 이름 일치"
        else:
            free = [c for c in columns if c not in used]
            tail = [c for c in free if len(want) >= 4 and _norm(c).endswith(want)]
            head = [c for c in free if len(_norm(c)) >= 4 and want.endswith(_norm(c))]
            if len(tail) == 1:
                column, why = tail[0], "열 이름이 속성 이름으로 끝남"
            elif not tail and len(head) == 1:
                column, why = head[0], "속성 이름이 열 이름으로 끝남"
        if not column:
            unmatched.append(brief)
            continue
        fields[key] = (
            {"column": column, "date": True} if kind == "date" else {"column": column}
        )
        hit: dict[str, Any] = {"property": key, "column": column, "why": why}
        if brief.get("enum_options"):
            # 고를 값 칸 — 원천 값이 대상의 값 목록에 없으면 `map` 으로 옮긴다(시험이 말한다).
            hit["enum_options"] = brief["enum_options"]
        matched.append(hit)
        used.add(column)
    return fields, matched, unmatched, references


def first_recipe(
    type_slug: str,
    columns: Mapping[str, str],
    props: Mapping[str, Mapping[str, Any]],
    key_col: str,
    label_col: str,
) -> dict[str, Any]:
    """`{"recipe", "matched", "unmatched", "references", "required_missing", "unused"}` —
    타입 하나의 레시피 첫 판과 그 근거."""
    used: set[str] = {key_col, label_col}
    fields, matched, unmatched, references = _fields(columns, props, used)
    recipe = {
        "format": "sp-table/1",
        "title": f"{type_slug}(일괄 초안 생성)",
        "types": [
            {
                "type_slug": type_slug,
                "key": {"column": key_col},
                "label": {"column": label_col or key_col},
                "fields": fields,
            }
        ],
    }
    return {
        "recipe": recipe,
        "matched": matched,
        "unmatched": unmatched,
        "references": references,
        "required_missing": [one for one in (*unmatched, *references) if one.get("required")],
        "unused": [column for column in columns if column not in used],
    }


# --- 한 행을 여러 타입으로 -----------------------------------------------------------------

_NAME_ENDS = ("name", "title", "label", "nm")
#: 묶음을 따라가는 깊이 — 모델 → 과제 → 프로젝트 → … 끝없이 가지 않게.
_DEPTH = 4


@dataclass
class _Group:
    """한 타입의 열 묶음 — 원천 자리 앞부분(`task.`) 또는 열 이름 앞부분(`task`)."""

    type_slug: str
    via: str
    columns: dict[str, str]
    key: str
    label: str
    lead: str = ""
    """원천 자리의 앞부분(`task.`) — 그 안에서 다시 묶음을 찾는다."""
    head: str = ""
    """열 이름의 앞부분(정규화 — `task`)."""
    children: list[str] = field(default_factory=list)


def _pick(rest: Mapping[str, str], ends: Sequence[str]) -> str:
    """`{열: 묶음 뒤의 이름}` 에서 끝 이름이 `ends` 의 차례로 먼저 맞는 열."""
    for end in ends:
        for column, name in rest.items():
            if name == end:
                return column
    return ""


def _group(
    columns: Mapping[str, str], lead: str, head: str, prop_key: str, ref: str
) -> tuple[dict[str, str], str, str, str, str] | None:
    """(열들, 식별자 열, 이름 열, 새 앞부분(자리), 새 앞부분(이름)) — 없으면 None."""
    names = [prop_key]
    if "_" in ref:
        names += [ref.split("_", 1)[1], ref.rsplit("_", 1)[1]]
    else:
        names.append(ref)
    names = [one for one in dict.fromkeys(names) if one]
    for name in names:  # 원천이 중첩이면 자리로(`task.code`)
        start = f"{lead}{name}.".lower()
        nested = {c: p for c, p in columns.items() if p.lower().startswith(start)}
        direct = {
            c: _norm(p[len(start) :]) for c, p in nested.items() if "." not in p[len(start) :]
        }
        key = _pick(direct, _ID_ENDS)
        if key:
            return nested, key, _pick(direct, _NAME_ENDS), f"{lead}{name}.", ""
    for name in names:  # 평평하면 열 이름으로(`TASK_CODE`)
        for prefix in dict.fromkeys(one for one in (head + _norm(name), _norm(name)) if one):
            flat = {c: p for c, p in columns.items() if _norm(c).startswith(prefix)}
            rest = {c: _norm(c)[len(prefix) :] for c in flat}
            key = _pick(rest, _ID_ENDS)
            if key:
                return flat, key, _pick(rest, _NAME_ENDS), "", prefix
    return None


def split_recipe(
    type_slug: str,
    columns: Mapping[str, str],
    model: Mapping[str, Any],
    source: str,
    key_col: str,
    label_col: str,
    taken: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """한 행을 대상의 정의를 따라 여러 타입 · 선으로 — `first_recipe` 와 같은 근거에 더해
    `types`(타입마다 묶음 · 식별자 · 이름 · 이은 것) · `relations`(넣은 선) ·
    `relation_candidates`(고르지 못한 선). `model` 은 `{"types": {slug: {"managed_by",
    "properties"}}, "relations": [관계 종류]}`, `taken` 은 다른 파이프라인이 채우는 타입 → 그
    파이프라인."""
    types: Mapping[str, Any] = model.get("types") or {}
    taken = taken or {}
    groups: dict[str, _Group] = {
        type_slug: _Group(type_slug, "주 타입", dict(columns), key_col, label_col)
    }
    order = [type_slug]
    #: 열 → 그 열을 가져간 묶음(가장 아래). 묶음은 자기 열 안에서 아래 묶음을 다시 찾는다.
    claimed: dict[str, str] = {}
    skipped: list[dict[str, str]] = []
    queue = [type_slug]
    while queue:
        current = groups[queue.pop(0)]
        if len(order) > _DEPTH * 4:
            break
        props = (types.get(current.type_slug) or {}).get("properties") or {}
        for prop_key, prop in props.items():
            ref = str(prop.get("ref_type_slug") or "")
            if str(prop.get("data_type") or "") not in _REF or not ref or ref in groups:
                continue
            owner = str((types.get(ref) or {}).get("managed_by") or "")
            if ref not in types or owner != source:
                continue  # 주인이 다르다 — 만들지 않고 참조(`match`)
            if ref in taken:
                skipped.append(
                    {"type_slug": ref, "why": f"{taken[ref]} 파이프라인 적재 타입(참조만)"}
                )
                continue
            free = {
                c: p
                for c, p in current.columns.items()
                if claimed.get(c, current.type_slug) == current.type_slug
            }
            found = _group(free, current.lead, current.head, prop_key, ref)
            if found is None:  # 평평한 원천 — 아래 타입의 열이 맨 위에 있다(PROJECT_CODE)
                rest = {c: p for c, p in columns.items() if c not in claimed}
                found = _group(rest, "", "", prop_key, ref)
            if found is None:
                continue
            cols, key, label, lead, head = found
            child = _Group(
                ref, f"{current.type_slug}.{prop_key}", cols, key, label, lead, head
            )
            groups[ref] = child
            current.children.append(prop_key)
            claimed.update(dict.fromkeys(cols, ref))
            order.append(ref)
            queue.append(ref)
    # 묶음마다 자기 열 — 아래 묶음이 가져간 열은 뺀다.
    owned: dict[str, dict[str, str]] = {}
    for slug in order:
        below = {c for other in order if other != slug for c in _descends(groups, slug, other)}
        owned[slug] = {c: p for c, p in groups[slug].columns.items() if c not in below}
    main = groups[type_slug]
    if main.label and main.label not in owned[type_slug]:
        # 이름 칸을 아래 타입이 가져갔다(ORDER_NAME 은 작업지시의 이름) — 제 열에서 다시
        # 고른다.
        main.label = label_column(owned[type_slug])
    used_all: set[str] = set()
    entries: list[dict[str, Any]] = []
    per_type: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    for slug in reversed(order):
        group = groups[slug]
        props = (types.get(slug) or {}).get("properties") or {}
        preset = {
            prop_key: {"key_of": groups[ref].type_slug}
            for prop_key, prop in props.items()
            if (ref := str(prop.get("ref_type_slug") or "")) in groups
            and groups[ref].via == f"{slug}.{prop_key}"
        }
        used = {group.key, group.label}
        fields, got, missing, refs = _fields(owned[slug], props, used, preset)
        used_all |= used
        entries.append(
            {
                "type_slug": slug,
                "key": {"column": group.key},
                "label": {"column": group.label or group.key},
                "fields": fields,
            }
        )
        linked = [
            {
                "property": k,
                "column": groups[v["key_of"]].key,
                "why": f"참조: 같은 행의 {v['key_of']}(key_of)",
            }
            for k, v in preset.items()
        ]
        per_type.append(
            {
                "type_slug": slug,
                "via": group.via,
                "key": group.key,
                "label": group.label or group.key,
                "columns": sorted(owned[slug]),
                "matched": [*linked, *got],
                "unmatched": missing,
                "references": refs,
            }
        )
        matched += [{**one, "type_slug": slug} for one in (*linked, *got)]
        unmatched += [{**one, "type_slug": slug} for one in missing]
        references += [{**one, "type_slug": slug} for one in refs]
    relations, candidates = _relations(model, source, order, groups, owned, used_all)
    used_all |= {one["dst"]["column"] for one in relations}
    recipe: dict[str, Any] = {
        "format": "sp-table/1",
        "title": f"{type_slug}(일괄 초안 생성)",
        "types": entries,
    }
    if relations:
        recipe["relations"] = relations
    return {
        "recipe": recipe,
        "types": list(reversed(per_type)),
        "matched": matched,
        "unmatched": unmatched,
        "references": references,
        "required_missing": [one for one in (*unmatched, *references) if one.get("required")],
        "relations": [
            {
                "relation": one["relation"],
                "type_slug": one["type_slug"],
                "dst_column": one["dst"]["column"],
                "why": one["evidence_note"]["value"],
            }
            for one in relations
        ],
        "relation_candidates": candidates,
        "skipped": skipped,
        "unused": [column for column in columns if column not in used_all],
    }


def _descends(groups: Mapping[str, _Group], top: str, other: str) -> set[str]:
    """`other` 가 `top` 아래 묶음이면 그 열들."""
    via = groups[other].via
    while via != "주 타입":
        parent = via.split(".", 1)[0]
        if parent == top:
            return set(groups[other].columns)
        via = groups[parent].via
    return set()


def _relations(
    model: Mapping[str, Any],
    source: str,
    order: Sequence[str],
    groups: Mapping[str, _Group],
    owned: Mapping[str, Mapping[str, str]],
    used: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """관계 종류마다 — 출발이 묶음의 타입이고 도착이 그 자신이거나 묶음 안. 도착 타입의 이름이
    든 식별자 꼴의 열이 하나뿐이면 선으로, 아니면 후보로."""
    lines: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for kind in model.get("relations") or []:
        slug = str(kind.get("slug") or "")
        owner = str(kind.get("managed_by") or "")
        if not slug or (owner and owner != source):
            continue
        srcs = list(kind.get("src_type_slugs") or [])
        dsts = list(kind.get("dst_type_slugs") or [])
        for start in order:
            if srcs and start not in srcs:
                continue
            for end in dsts:
                if end != start and end not in groups:
                    continue
                tail = _norm(end.rsplit("_", 1)[-1])
                found = [
                    c
                    for c in owned[start]
                    if c not in used
                    and c != groups[start].key
                    and tail
                    and tail in _norm(c)
                    and _norm(c).endswith(_ID_ENDS)
                ]
                if len(found) == 1:
                    lines.append(
                        {
                            "relation": slug,
                            "type_slug": start,
                            "src": {"key_of": start},
                            "dst": {"column": found[0]},
                            "evidence_note": {
                                "value": f"원천 열 {found[0]}(일괄 초안 생성)",
                            },
                        }
                    )
                    used.add(found[0])
                elif found or end != start:
                    candidates.append(
                        {
                            "relation": slug,
                            "label": kind.get("label") or "",
                            "type_slug": start,
                            "dst_type": end,
                            "candidates": found,
                        }
                    )
    return lines, candidates
