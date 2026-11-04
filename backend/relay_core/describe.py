"""정의를 **비밀 없이** 읽는다 — 연결 지도 · 파이프라인 상세 · MCP 가 쓴다.

달리지 않으므로 토큰이 필요 없다(`definition.load` 는 달리려고 비밀까지 푼다). 그리고 **필드
계보**를 정의에서 계산한다 — 대상 SP 의 `타입.속성` 하나가 레시피의 어느 칸 · 어느 변환을 거쳐
원천 API 의 어느 자리에서 오나. 손으로 적은 표가 아니라 정의 자신에서 나오므로 정의와 화면이
갈리지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from relay_core.definition import FORMAT, DefinitionError, check_adapter
from relay_core.schedule import ScheduleError, check, label


@dataclass
class Step:
    """대상 속성 하나의 계보."""

    target_type: str
    target_field: str
    """`key` · `label` · 속성 key · 관계(`→ relation`)."""
    columns: list[str]
    """레시피가 읽는 원천 표의 열."""
    source_paths: list[str]
    """그 열이 원천 응답의 어디서 오나(`task.code`)."""
    transform: str
    """사람이 읽는 변환 한 줄 — 「값 대응 3개」 · 「날짜」 · 「타입 plm_task 의 식별자」 …"""


@dataclass
class Described:
    path: Path
    slug: str = ""
    source: str = ""
    adapter_kind: str = ""
    system: str = ""
    """원천 시스템의 이름 — 정의의 `system`(없으면 `source` 를 대문자로)."""
    endpoint: str = ""
    endpoint_label: str = ""
    columns: dict[str, str] = field(default_factory=dict)
    recipe: Path | None = None
    target_name: str = ""
    target_server: str = ""
    target_token_env: str = ""
    types: list[str] = field(default_factory=list)
    relations: list[str] = field(default_factory=list)
    lineage: list[Step] = field(default_factory=list)
    schedule: dict[str, Any] | None = None
    schedule_label: str = "수동"
    recipe_label: str = "파일"
    """지금 도는 레시피 — 「파일」(정의의 `recipe`) 또는 「판 N」(켜진 판,
    `app/modules/relay`)."""
    problems: list[str] = field(default_factory=list)
    """정의를 읽다가 난 문제 — 지도는 그 파이프라인을 「깨짐」 으로 그린다(안 그리면 없는 줄
    안다)."""


def _transform(spec: dict[str, Any]) -> str:
    parts: list[str] = []
    if "key_of" in spec:
        return f"타입 {spec['key_of']} 의 식별자"
    if "value" in spec:
        return f"고정값 {spec['value']!r}"
    if "parser" in spec:
        part = "의 종류" if spec.get("part") == "kind" else ""
        return f"해석기 {spec['parser']} › {spec.get('slot')}{part}"
    if spec.get("upper"):
        parts.append("대문자")
    if spec.get("date"):
        parts.append("날짜")
    if spec.get("map"):
        parts.append(f"값 대응 {len(spec['map'])}개")
    if spec.get("blank"):
        parts.append(f"빈 값으로 볼 것 {len(spec['blank'])}개")
    if spec.get("match"):
        parts.append(f"대상의 {spec['match'].get('keys')} 와 대조")
    if "collect" in spec:
        parts.append("여러 행을 목록으로")
    if "join" in spec:
        parts.append("여러 행을 한 글로")
    return " · ".join(parts) or "그대로"


def _columns_of(
    spec: Any, recipe: dict[str, Any], types: dict[str, dict[str, Any]]
) -> list[str]:
    if not isinstance(spec, dict):
        return []
    if "column" in spec:
        return [str(spec["column"])]
    if "key_of" in spec:
        return _columns_of((types.get(spec["key_of"]) or {}).get("key"), recipe, types)
    if "parser" in spec:
        parser = (recipe.get("parsers") or {}).get(spec["parser"]) or {}
        return [str(parser["column"])] if parser.get("column") else []
    for inner in ("collect", "join"):
        if isinstance(spec.get(inner), dict):
            return _columns_of(spec[inner], recipe, types)
    return []


def lineage(recipe: dict[str, Any], columns: dict[str, str]) -> list[Step]:
    """레시피(`sp-table/1`)의 칸마다 — 열 · 원천 자리 · 변환."""
    types = {
        str(one["type_slug"]): one for one in recipe.get("types") or [] if "type_slug" in one
    }
    steps: list[Step] = []

    def add(type_slug: str, name: str, spec: Any) -> None:
        cols = _columns_of(spec, recipe, types)
        steps.append(
            Step(
                target_type=type_slug,
                target_field=name,
                columns=cols,
                source_paths=[columns.get(one, "") for one in cols],
                transform=_transform(spec) if isinstance(spec, dict) else "그대로",
            )
        )

    for slug, one in types.items():
        add(slug, "key", one.get("key"))
        add(slug, "label", one.get("label") or one.get("key"))
        for name, spec in (one.get("fields") or {}).items():
            add(slug, str(name), spec)
    for one in recipe.get("relations") or []:
        add(str(one.get("type_slug")), f"→ {one.get('relation')}", one.get("dst"))
    return steps


def describe(path: Path) -> Described:
    out = Described(path=path)
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as failure:
        out.problems.append(f"정의를 못 읽었습니다: {failure}")
        return out
    if body.get("format") != FORMAT:
        out.problems.append(f"format 이 {FORMAT} 가 아닙니다")
    out.slug = str(body.get("slug") or path.stem)
    out.source = str(body.get("source") or "")
    out.system = str(body.get("system") or out.source.upper())
    try:
        out.schedule = check(body.get("schedule"))
        out.schedule_label = label(out.schedule)
    except ScheduleError as failure:
        out.problems.append(str(failure))
    adapter = dict(body.get("adapter") or {})
    try:
        out.adapter_kind, checked = check_adapter(adapter)
    except DefinitionError as failure:
        out.problems.append(str(failure))
        out.adapter_kind, checked = str(adapter.get("kind") or "?"), adapter
    out.columns = {str(k): str(v) for k, v in dict(checked.get("columns") or {}).items()}
    if out.adapter_kind == "odata":
        base = str(checked.get("service_url") or "")
        out.endpoint = f"{base.rstrip('/')}/{checked.get('entity_set', '')}"
        out.endpoint_label = f"OData {checked.get('entity_set', '')}"
    else:
        base = str(checked.get("base_url") or "")
        out.endpoint = f"{base.rstrip('/')}{checked.get('path', '')}"
        out.endpoint_label = f"GET {checked.get('path', '')}"
    host = urlsplit(base).netloc
    if host:
        out.endpoint_label += f" · {host}"
    target = dict(body.get("target") or {})
    out.target_name = str(target.get("name") or "")
    out.target_server = str(target.get("server") or "").rstrip("/")
    out.target_token_env = str(target.get("token_env") or "")
    recipe_path = (path.parent / str(body.get("recipe") or "")).resolve()
    if not recipe_path.is_file():
        out.problems.append(f"레시피가 없습니다: {recipe_path}")
        return out
    out.recipe = recipe_path
    try:
        recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    except ValueError as failure:
        out.problems.append(f"레시피를 못 읽었습니다: {failure}")
        return out
    out.types = [
        str(one["type_slug"]) for one in recipe.get("types") or [] if "type_slug" in one
    ]
    out.relations = sorted(
        {
            str(one.get("relation"))
            for one in recipe.get("relations") or []
            if one.get("relation")
        }
    )
    out.lineage = lineage(recipe, out.columns)
    unknown = sorted({c for step in out.lineage for c in step.columns} - set(out.columns))
    if unknown:
        out.problems.append(f"레시피가 읽는 열을 어댑터가 안 냅니다: {', '.join(unknown)}")
    return out


def find(root: Path) -> list[Path]:
    """`root` 아래의 정의 파일 전부 — `format` 이 `relay-pipeline/1` 인 JSON.
    `*.example.json` 은 본보기라 뺀다(같은 타입을 두 길로 채우는 정의가 지도에 서지 않게)."""
    found: list[Path] = []
    for path in sorted(root.rglob("*.json")):
        if path.name.endswith(".example.json"):
            continue
        try:
            head = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(head, dict) and head.get("format") == FORMAT:
            found.append(path)
    return found
