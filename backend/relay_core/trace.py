"""거꾸로 보기 — 대상 객체 하나의 값이 **어느 원천 행 · 칸 · 레시피 규칙**에서 왔나.

기준 출력(지난번 적용된 실행)의 그 객체 → 레시피의 칸 규칙 → 그 실행에 넣은 원천 표의 행들
→ 그 객체에 닿는 관계 → 마지막 대조에서의 상태. 대조가 「다름」 을 말할 때 레시피의 값 모양
탓인지, 원천이 그렇게 준 것인지, 대상에서 사람이 바꾼 것인지를 가린다.

실행 폴더의 행마다 `_source`(원천 표의 첫 행)가 있고, 그 실행에 넣은 원천 표와 레시피는
작업 폴더의 `work/<때>.source.csv` · `work/<때>.recipe.table.json` 에 남는다(정리가 기준의
것은 남긴다). 없으면(오래된 실행 · 백업에서 되살린 폴더) 지금 미러 · 지금 레시피로 찾고
**그렇다고 적는다**.

**읽기만 한다.** 레시피를 다시 돌리지 않는다 — 원천 행을 찾는 데만 식별자의 열 규칙(대문자 ·
빈 값 · 대응)을 쓴다. 참조 대조(`match`) · 해석기(`parser`) · 날짜로 정한 식별자는 여기서 풀
수 없어 `_source` 의 한 행만 보인다.
"""

from __future__ import annotations

import csv
import json
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from relay_core.state import Workdir

ROWS_MAX = 50
SEEN_MAX = 10
AGGREGATES = ("collect", "join")


class TraceError(Exception):
    """찾을 수 없다 — 모르는 타입 · 그 식별자가 어디에도 없음 · 레시피 없음."""


class _Cannot(Exception):
    """이 식별자 규칙은 여기서 풀 수 없다(참조 대조 · 해석기 · 날짜)."""


@dataclass
class FieldTrace:
    name: str
    """`key` · `label` · 속성 이름."""
    rule: str
    """사람이 읽는 규칙 한 줄 — 「열 KIND_CD · 대응 01→양산 · 02→선행」."""
    columns: list[str]
    value: Any = None
    """기준 출력의 값(없으면 None)."""
    seen: list[dict[str, Any]] = field(default_factory=list)
    """원천 행들의 원래 값 — `{"value": 원문, "rows": [행 번호 …]}`, 다른 값끼리."""


@dataclass
class Trace:
    type_slug: str
    key: str
    label: str = ""
    found: bool = False
    """기준 출력에 그 객체가 있나."""
    run: str = ""
    applied_at: str = ""
    basis: str = ""
    """원천 행을 어디서 찾았나 — 「기준 실행의 원천 표」 또는 「지금 미러」."""
    recipe_basis: str = ""
    note: str = ""
    """변환기의 메모 — 「3행에서 모음」."""
    fields: list[FieldTrace] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    """원천 행 — `{"row": 행 번호, "values": {열: 값}}`(레시피가 쓰는 열만)."""
    rows_total: int = 0
    relations: list[dict[str, str]] = field(default_factory=list)
    reconcile: dict[str, Any] | None = None
    problems: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# --- 레시피 읽기 -----------------------------------------------------------------------


def _types(recipe: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(one["type_slug"]): one for one in recipe.get("types") or []}


def _short_map(mapping: dict[str, Any]) -> str:
    pairs = [f"{raw}→{out}" for raw, out in mapping.items()]
    more = f" 외 {len(pairs) - 6}개" if len(pairs) > 6 else ""
    return " · ".join(pairs[:6]) + more


def _rule(spec: dict[str, Any], types: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    """(규칙 한 줄, 원천 열) — `sp_table` 의 값 뽑기 모양 그대로 읽는다."""
    for mode in AGGREGATES:
        if mode in spec:
            inner, columns = _rule(spec[mode], types)
            if mode == "join":
                glue = spec.get("separator") or "; "
                return f"여러 행을 이어 붙임({glue}) — {inner}", columns
            return f"여러 행에서 모음 — {inner}", columns
    if "value" in spec:
        return f"고정값 「{spec['value']}」", []
    if "key_of" in spec:
        target = types.get(str(spec["key_of"]))
        columns = _rule(target["key"], types)[1] if target else []
        return f"{spec['key_of']} 의 식별자", columns
    if "parser" in spec:
        return f"해석기 {spec['parser']} 의 {spec.get('slot')}", []
    column = str(spec.get("column") or "")
    parts = [f"열 {column}"]
    if spec.get("upper"):
        parts.append("대문자로")
    if spec.get("blank"):
        parts.append("빈 값으로 볼 것 " + " · ".join(str(one) for one in spec["blank"]))
    if spec.get("map"):
        parts.append("대응 " + _short_map(spec["map"]))
    if spec.get("date"):
        parts.append("날짜로")
    if spec.get("match"):
        parts.append(f"대상 {spec['match'].get('keys')} 의 식별자와 대조")
    return " · ".join(parts), [column] if column else []


def _key_of_row(
    spec: dict[str, Any], row: dict[str, str], types: dict[str, Any]
) -> str | None:
    """원천 행 하나의 식별자 — 열 규칙만 푼다(`sp_table.Mapping.value` 의 앞부분과 같다)."""
    for mode in AGGREGATES:
        if mode in spec:
            return _key_of_row(spec[mode], row, types)
    if "value" in spec:
        return str(spec["value"])
    if "key_of" in spec:
        return _key_of_row(types[str(spec["key_of"])]["key"], row, types)
    if "parser" in spec or "match" in spec or spec.get("date"):
        raise _Cannot
    text = str(row.get(str(spec.get("column") or "")) or "").strip()
    if spec.get("upper"):
        text = text.upper()
    if not text or text in {str(one) for one in spec.get("blank") or []}:
        return None
    if "map" in spec:
        got = spec["map"].get(text)
        return None if got is None else str(got)
    return text


# --- 기준 출력 · 원천 표 ----------------------------------------------------------------


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _baseline_row(run: Path, type_slug: str, key: str) -> dict[str, Any] | None:
    file = run / "objects" / f"{type_slug}.json"
    if not file.is_file():
        return None
    body = json.loads(file.read_text(encoding="utf-8"))
    for row in body.get("rows") or []:
        if isinstance(row, dict) and str(row.get("key")) == key:
            return row
    return None


def _relations(run: Path, type_slug: str, key: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    folder = run / "relations"
    for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        body = json.loads(file.read_text(encoding="utf-8"))
        starts_here = str(body.get("type_slug") or "") == type_slug
        for row in body.get("rows") or []:
            if not isinstance(row, dict):
                continue
            relation = str(row.get("relation") or file.stem)
            if starts_here and str(row.get("src")) == key:
                found.append(
                    {"relation": relation, "direction": "out", "other": str(row["dst"])}
                )
            elif str(row.get("dst")) == key:
                found.append(
                    {"relation": relation, "direction": "in", "other": str(row["src"])}
                )
    return found[:ROWS_MAX]


def _reconcile(
    work: Workdir, type_slug: str, key: str, applied_at: str
) -> dict[str, Any] | None:
    """마지막 대조에서 이 객체 — 보고서에는 표본이 아니라 전부가 있다. 지금 기준을 적용하기
    전의 대조면 `stale` — 그 「같음」 은 지금 기준의 것이 아니다."""
    report = work.read_json("reconcile.json")
    if not report:
        return None
    one = (report.get("types") or {}).get(type_slug)
    fields: list[str] = []
    if not isinstance(one, dict):
        state = "unknown"
    elif key in (one.get("missing") or []):
        state = "missing"
    elif key in (one.get("deprecated") or []):
        state = "deprecated"
    elif key in (one.get("changed") or {}):
        state, fields = "changed", list(one["changed"][key])
    elif key in (one.get("extra") or []):
        state = "extra"
    else:
        state = "same"
    at = str(report.get("at") or "")
    stale = bool(at and applied_at and at < applied_at)
    return {"state": state, "at": at or None, "fields": fields, "stale": stale}


def _mirror_table(work: Workdir) -> list[dict[str, str]]:
    mirror = work.load_mirror()
    if len(mirror) == 0:
        return []
    with tempfile.TemporaryDirectory() as scratch:
        return _read_rows(mirror.write_csv(Path(scratch) / "mirror.csv"))


# --- 본체 ------------------------------------------------------------------------------


Recipe = Path | dict[str, Any] | None
"""지금 쓰는 레시피 — 파일(정의 파일의 것) 또는 본문(사용 중인 레시피 판)."""


def _recipe(work: Workdir, stamp: str, recipe_now: Recipe, out: Trace) -> dict[str, Any]:
    saved = work.path(f"work/{stamp}.recipe.table.json") if stamp else None
    now_given = isinstance(recipe_now, dict) or (
        isinstance(recipe_now, Path) and recipe_now.is_file()
    )
    file: Recipe
    if saved and saved.is_file():
        file, out.recipe_basis = saved, "기준 실행의 레시피"
    elif now_given:
        file, out.recipe_basis = recipe_now, "지금 레시피"
        if stamp:
            out.problems.append(
                "기준 실행의 레시피 사본이 없어 지금 레시피로 읽었습니다 — 그 뒤 레시피 판이 "
                "바뀌었으면 규칙이 기준과 다를 수 있습니다."
            )
    else:
        raise TraceError("레시피가 없습니다 — 레시피 판을 작성하고 사용하세요.")
    if isinstance(file, dict):
        return file
    assert isinstance(file, Path)
    body: dict[str, Any] = json.loads(file.read_text(encoding="utf-8"))
    return body


def _source_table(
    work: Workdir, stamp: str, baseline: dict[str, Any] | None, out: Trace
) -> list[dict[str, str]]:
    named = str((baseline or {}).get("_source", {}).get("file") or "")
    source = work.path(f"work/{named or stamp + '.source.csv'}") if stamp else None
    if source and source.is_file():
        out.basis = "기준 실행의 원천 표"
        return _read_rows(source)
    out.basis = "지금 미러"
    if stamp:
        out.problems.append(
            "기준 실행의 원천 표가 없어 지금 미러에서 찾았습니다 — 그 뒤 원천이 바뀐 값일 수 "
            "있습니다."
        )
    return _mirror_table(work)


def trace(work: Workdir, recipe_now: Recipe, type_slug: str, key: str) -> Trace:
    """`type_slug` 의 객체 `key` 를 거꾸로 본다. `recipe_now` 는 지금 쓰는 레시피 — 기준 실행의
    사본이 없을 때만 쓴다."""
    key = key.strip()
    out = Trace(type_slug=type_slug, key=key)
    state = work.load_state()
    run = work.path(state.applied_run) if state.applied_run else None
    out.run, out.applied_at = state.applied_run or "", state.applied_at or ""
    stamp = run.name if run else ""

    types = _types(_recipe(work, stamp, recipe_now, out))
    spec = types.get(type_slug)
    if spec is None:
        made = " · ".join(types) or "없음"
        raise TraceError(
            f"레시피에 타입 {type_slug} 이 없습니다 — 레시피가 만드는 타입: {made}"
        )

    baseline = _baseline_row(run, type_slug, key) if run else None
    out.found = baseline is not None
    if baseline:
        out.label = str(baseline.get("label") or "")
        out.note = str(baseline.get("_note") or "")
    elif run:
        out.problems.append(
            "기준 출력에 이 식별자가 없습니다 — 아직 적용되지 않았거나, 레시피가 미해결로 "
            "제외했거나, 대상에만 있는 객체입니다."
        )
    else:
        out.problems.append(
            "아직 적용한 것이 없습니다 — 기준 출력 없이 지금 미러에서 찾습니다."
        )

    # 1행은 머리글 — 변환기의 `_source.row` 와 같은 셈이다.
    numbered = list(enumerate(_source_table(work, stamp, baseline, out), start=2))
    try:
        matched = [
            (n, row) for n, row in numbered if _key_of_row(spec["key"], row, types) == key
        ]
    except _Cannot:
        first = int((baseline or {}).get("_source", {}).get("row") or 0)
        matched = [(n, row) for n, row in numbered if n == first]
        out.problems.append(
            "이 타입의 식별자는 참조 대조 · 해석기 · 날짜로 정해져 원천 행을 다 찾을 수 "
            "없습니다 — 변환기가 적어 둔 첫 행만 보입니다."
        )
    out.rows_total = len(matched)
    if not matched and not out.found:
        raise TraceError(
            f"{type_slug} 의 {key} 를 기준 출력에서도 원천 표에서도 찾지 못했습니다."
        )

    rules = [("key", spec["key"]), ("label", spec.get("label"))]
    rules += list((spec.get("fields") or {}).items())
    used: list[str] = []
    for name, one in rules:
        if not isinstance(one, dict):
            continue
        text, columns = _rule(one, types)
        item = FieldTrace(name=str(name), rule=text, columns=columns)
        item.value = (baseline or {}).get(str(name))
        if columns:
            seen: dict[str, list[int]] = {}
            for number, row in matched:
                seen.setdefault(str(row.get(columns[0]) or ""), []).append(number)
            item.seen = [
                {"value": raw, "rows": numbers[:SEEN_MAX]}
                for raw, numbers in list(seen.items())[:SEEN_MAX]
            ]
        used += [one for one in columns if one not in used]
        out.fields.append(item)
    out.rows = [
        {"row": number, "values": {column: row.get(column, "") for column in used}}
        for number, row in matched[:ROWS_MAX]
    ]
    if run:
        out.relations = _relations(run, type_slug, key)
    out.reconcile = _reconcile(work, type_slug, key, out.applied_at)
    return out
