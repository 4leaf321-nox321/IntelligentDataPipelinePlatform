"""파이프라인 정의의 판 — 원천(어댑터) · 대상 · `source` · 일정을 **AI 가 MCP 로 짓는다.**

    종류   어댑터마다 적을 칸과 그 설명(`kinds`) — 코드의 설명을 그대로 읽어 준다.
    탐색   어댑터 설정으로 원천에 닿아(식별자 · 열을 아직 몰라도) 칸의 자리 · 보기 값 · 열
           제안을 받는다. 저장하지 않는다.
    초안   판 번호가 하나 는다. 켜진 판은 그대로다. 비밀을 적으면 거절한다.
    시험   그 판으로 원천을 **받아 보고**(전량 · 저장 안 함), 대상의 판 · 토큰 · 타입을 보고,
           레시피(고른 판 › 켜진 판 › 같은 slug 의 정의 파일)로 바꿔 본다 — **대상에 아무것도
           안 보낸다.**
    켜기   **사람이 화면에서만**(PAT 거절). 다음 실행부터 이 판이 돈다 — 같은 slug 의 정의
           파일을 이긴다.

탐색 · 시험은 서버가 바깥에 닿는 일이라 울타리(`guard`)를 먼저 지난다.
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from dataclasses import MISSING, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.relay import guard
from app.modules.relay.models import RelayDefinition
from app.shared import audit
from app.shared.errors import AppError, Conflict, Forbidden, NotFound, code
from relay_core.adapters.rest import Auth
from relay_core.definition import (
    ADAPTERS,
    SECRET_FIELDS,
    DefinitionError,
    build_adapter,
    check_adapter,
    check_body,
)
from relay_core.describe import Described, describe, describe_body, find
from relay_core.mirror import Mirror
from relay_core.schedule import ScheduleError, check
from relay_core.state import Workdir

#: 정의의 맨 위 칸 — 모르는 칸은 거절한다.
TOP_KEYS = {
    "format",
    "slug",
    "source",
    "system",
    "schedule",
    "auto_apply",
    "adapter",
    "target",
    "note",
}

#: 탐색 · 시험이 돌려줄 표본의 줄 수 · 글 길이 상한 — MCP 응답이 끝없이 커지지 않게.
SAMPLE_MAX = 50
TEXT_MAX = 300


def _invalid(message: str) -> AppError:
    return AppError(code("RELAY", 9), message, status=422)


# --- 판 ---------------------------------------------------------------------------------


def versions(db: Session, slug: str) -> list[RelayDefinition]:
    return list(
        db.scalars(
            select(RelayDefinition)
            .where(RelayDefinition.pipeline_slug == slug)
            .order_by(RelayDefinition.version.desc())
        )
    )


def get(db: Session, slug: str, version: int) -> RelayDefinition:
    row = db.scalar(
        select(RelayDefinition).where(
            RelayDefinition.pipeline_slug == slug, RelayDefinition.version == version
        )
    )
    if row is None:
        raise NotFound(code("RELAY", 16), f"{slug} 의 정의 판 {version} 이 없습니다.")
    return row


def active(db: Session, slug: str) -> RelayDefinition | None:
    return db.scalar(
        select(RelayDefinition).where(
            RelayDefinition.pipeline_slug == slug, RelayDefinition.status == "active"
        )
    )


def current(db: Session) -> dict[str, RelayDefinition]:
    """slug 마다 지도에 설 판 — 켜진 판, 없으면 가장 새 초안(켜진 적 없는 새 파이프라인)."""
    out: dict[str, RelayDefinition] = {}
    rows = db.scalars(
        select(RelayDefinition)
        .where(RelayDefinition.status.in_(("active", "draft")))
        .order_by(RelayDefinition.version.desc())
    )
    for row in rows:
        held = out.get(row.pipeline_slug)
        if held is None or (row.status == "active" and held.status != "active"):
            out[row.pipeline_slug] = row
    return out


def describe_row(row: RelayDefinition, file: Described | None) -> Described:
    """판 하나를 비밀 없이 — 레시피는 같은 slug 의 정의 파일 것을 빌린다(있으면). 켜진 레시피
    판은 `services.catalog` 가 그 위에 얹는다."""
    label = f"판 {row.version}" if row.status == "active" else f"초안 {row.version}"
    found = describe_body(
        dict(row.body), path=Path(f"<정의 판:{row.pipeline_slug}>"), base=None, origin=label
    )
    found.slug = row.pipeline_slug
    found.draft = row.status != "active"
    if file is not None and file.recipe is not None:
        from app.modules.relay import recipes

        found.recipe = file.recipe
        try:
            body = json.loads(file.recipe.read_text(encoding="utf-8"))
        except (OSError, ValueError) as failure:
            found.problems.append(f"레시피를 못 읽었습니다: {failure}")
        else:
            recipes.with_recipe(found, body, "파일")
    return found


# --- 종류 · 탐색 -----------------------------------------------------------------------------


def _field_docs(shape: type[Any]) -> dict[str, str]:
    """데이터클래스 칸 아래의 설명 글(`\"\"\"…\"\"\"`) — 코드의 설명이 곧 안내다."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(shape)))
    except (OSError, TypeError, SyntaxError):
        return {}
    out: dict[str, str] = {}
    body = tree.body[0].body if tree.body and isinstance(tree.body[0], ast.ClassDef) else []
    for one, after in zip(body, [*body[1:], None], strict=True):
        if (
            isinstance(one, ast.AnnAssign)
            and isinstance(one.target, ast.Name)
            and isinstance(after, ast.Expr)
            and isinstance(after.value, ast.Constant)
            and isinstance(after.value.value, str)
        ):
            out[one.target.id] = " ".join(after.value.value.split())
    return out


def _fields_of(shape: type[Any], need: tuple[str, ...]) -> list[dict[str, Any]]:
    docs = _field_docs(shape)
    out: list[dict[str, Any]] = []
    for one in fields(shape):
        if one.name in SECRET_FIELDS:
            continue
        default: Any = None
        if one.default is not MISSING:
            default = one.default
        elif one.default_factory is not MISSING:
            made = one.default_factory()
            default = {} if isinstance(made, Auth) else made
        out.append(
            {
                "name": one.name,
                "type": str(one.type),
                "required": one.name in need,
                "default": default
                if isinstance(default, (str, int, float, bool, dict, list)) or default is None
                else str(default),
                "doc": docs.get(one.name, ""),
            }
        )
    return out


def kinds() -> dict[str, Any]:
    """어댑터 종류마다 — 설명 · 꼭 적을 칸 · 모든 칸(기본값 · 설명). 비밀 칸은 없다."""
    out: list[dict[str, Any]] = []
    for kind, (shape, _adapter, need) in ADAPTERS.items():
        module = inspect.getmodule(shape)
        doc = inspect.cleandoc((module.__doc__ if module else "") or "")
        out.append(
            {
                "kind": kind,
                "doc": doc,
                "required": list(need),
                "fields": _fields_of(shape, need),
            }
        )
    auth = [one for one in _fields_of(Auth, ()) if one["name"] != "token"]
    auth.append(
        {
            "name": "token_env",
            "type": "str",
            "required": False,
            "default": "",
            "doc": "비밀(토큰 · 비밀번호)이 든 환경 변수의 이름 — 값은 정의에 적지 않는다.",
        }
    )
    return {"kinds": out, "auth": auth, "secret_fields": sorted(SECRET_FIELDS)}


def _trim(value: Any, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value if len(value) <= TEXT_MAX else value[:TEXT_MAX] + "…"
    if isinstance(value, list):
        return [_trim(one, depth + 1) for one in value[:200]]
    if isinstance(value, dict):
        return {str(k): _trim(v, depth + 1) for k, v in list(value.items())[:500]}
    return value


def _allowed(db: Session) -> set[str]:
    from app.modules.relay import services

    return guard.allowed_hosts(services.catalog(db))


def discover(db: Session, spec: dict[str, Any]) -> dict[str, Any]:
    """어댑터 설정으로 원천에 닿아 본다 — 저장하지 않는다. 식별자 · 열은 비워도 된다."""
    from app.modules.relay import services

    if not isinstance(spec, dict):
        raise _invalid("adapter 는 JSON 객체여야 합니다.")
    guard.check_no_secrets(spec, "adapter")
    try:
        kind, checked = check_adapter(spec, partial=True)
    except DefinitionError as failure:
        raise _invalid(str(failure)) from failure
    guard.check_env(spec)
    guard.check_source(kind, checked, _allowed(db))
    try:
        adapter = build_adapter(
            spec,
            services.secrets_env(),
            partial=True,
            file_roots=get_settings().relay_file_root_paths,
        )
    except DefinitionError as failure:
        raise _invalid(str(failure)) from failure
    out: dict[str, Any] = {"kind": kind, "ok": True, "probe": ""}
    if checked.get("key"):
        try:
            out["probe"] = adapter.probe()
        except Exception as failure:  # 원천이 무엇을 던지든 AI 에게 까닭을 보인다
            out["probe_error"] = str(failure)
    try:
        out["found"] = _trim(adapter.discover())
    except Exception as failure:
        out.update(ok=False, error=str(failure))
    return out


# --- 초안 · 켜기 -----------------------------------------------------------------------------


def _check(db: Session, slug: str, body: Any) -> tuple[dict[str, Any], list[str]]:
    """저장 전에 — 거절할 것은 예외로, 알아 둘 것은 `problems` 로."""
    from app.modules.relay import services

    if not isinstance(body, dict):
        raise _invalid("정의는 JSON 객체여야 합니다.")
    body = dict(body)
    problems: list[str] = []
    if "recipe" in body:
        body.pop("recipe")
        problems.append(
            "recipe 칸은 뺐습니다 — DB 의 정의는 레시피 판을 씁니다(켜진 판이 없으면 "
            "같은 slug 의 정의 파일의 레시피)."
        )
    unknown = sorted(set(body) - TOP_KEYS)
    if unknown:
        raise _invalid(
            f"모르는 칸입니다: {', '.join(unknown)} (되는 것: {', '.join(sorted(TOP_KEYS))})"
        )
    guard.check_no_secrets(body)
    try:
        got_slug, source = check_body(body)
        check(body.get("schedule"))
    except (DefinitionError, ScheduleError) as failure:
        raise _invalid(str(failure)) from failure
    if got_slug != slug:
        raise _invalid(f"정의의 slug({got_slug})가 주소의 slug({slug})와 다릅니다.")
    guard.check_env(body)

    kind, spec = check_adapter(dict(body["adapter"]))
    allowed = _allowed(db)
    try:
        guard.check_source(kind, spec, allowed)
    except AppError as failure:
        problems.append(f"원천: {failure.message} — 시험 · 실행 전에 풀어야 합니다.")
    try:
        guard.check_target(dict(body["target"]), allowed)
    except AppError as failure:
        problems.append(f"대상: {failure.message} — 시험 전에 풀어야 합니다.")

    running = [one for one in services.catalog(db) if not one.draft]
    for one in (other for other in running if other.slug == slug):
        if one.source and one.source != source:
            problems.append(
                f"source 가 지금 도는 정의({one.origin})의 {one.source!r} 와 다릅니다 — 대상 "
                "타입의 잠금(managed_by)과 어긋나 적재가 거절됩니다."
            )
    targets = {one.target_server for one in running}
    server = str(dict(body["target"]).get("server") or "").rstrip("/")
    if targets and server not in targets:
        problems.append(
            f"대상 {server} 는 지금 도는 어느 정의도 쓰지 않는 곳입니다 — 등록된 SP "
            "인스턴스가 맞는지 보세요."
        )
    return body, problems


def save_draft(
    db: Session, user: User, request: Request, slug: str, body: Any, note: str
) -> tuple[RelayDefinition, list[str]]:
    cleaned, problems = _check(db, slug, body)
    latest = db.scalar(
        select(func.max(RelayDefinition.version)).where(RelayDefinition.pipeline_slug == slug)
    )
    via_token = getattr(request.state, "token_scopes", None) is not None
    row = RelayDefinition(
        pipeline_slug=slug,
        version=int(latest or 0) + 1,
        status="draft",
        body=cleaned,
        note=note.strip(),
        client=request.headers.get("X-Client") or ("토큰" if via_token else "화면"),
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="relay.definition.draft",
        actor=user,
        target_table="relay_definitions",
        target_id=row.id,
        target_label=f"{slug} 정의 판 {row.version}",
        changes={"note": row.note, "client": row.client},
    )
    db.commit()
    db.refresh(row)
    found = describe_row(row, None)
    return row, problems + [one for one in found.problems if one not in problems]


def activate(
    db: Session, user: User, request: Request, slug: str, version: int
) -> RelayDefinition:
    """**사람이 화면에서만** — 토큰(MCP · 스크립트)이면 거절한다."""
    if getattr(request.state, "token_scopes", None) is not None:
        raise Forbidden(
            code("RELAY", 14),
            "정의를 켜는 일은 화면에서 사람이 합니다 — 토큰(MCP)으로는 켜지 않습니다. 시험 "
            "결과를 화면에서 보고 「켜기」 를 누르세요.",
        )
    row = get(db, slug, version)
    if row.status == "active":
        return row
    if row.status == "discarded":
        raise Conflict(code("RELAY", 17), f"버린 초안은 켜지 않습니다 — 판 {version}.")
    try:
        check_body(row.body)
    except DefinitionError as failure:
        raise _invalid(str(failure)) from failure
    guard.check_env(row.body)
    kind, spec = check_adapter(dict(row.body["adapter"]))
    if kind == "file" and not guard.source_url(kind, spec):
        guard.check_source(kind, spec, set())  # 파일 폴더의 울타리는 켜는 사람도 넘지 못한다
    before = active(db, slug)
    db.execute(
        update(RelayDefinition)
        .where(RelayDefinition.pipeline_slug == slug, RelayDefinition.status == "active")
        .values(status="retired")
    )
    row.status = "active"
    row.activated_by_id = user.id
    row.activated_at = datetime.now(UTC)
    audit.record(
        db,
        action="relay.definition.activate",
        actor=user,
        target_table="relay_definitions",
        target_id=row.id,
        target_label=f"{slug} 정의 판 {row.version}",
        changes={"was": before.version if before else None},
    )
    db.commit()
    db.refresh(row)
    return row


# --- 시험 ----------------------------------------------------------------------------------


def _target_check(target: dict[str, Any], env: Any, wanted: list[str]) -> dict[str, Any]:
    from app.modules.relay import services

    server = str(target.get("server") or "").rstrip("/")
    out: dict[str, Any] = {"server": server, "reachable": False}
    status_code, health = services.probe_http(server, "", "/api/health")
    if status_code != 200 or not isinstance(health, dict):
        out["error"] = f"대상에 닿지 않습니다({status_code or '응답 없음'})."
        return out
    out.update(reachable=True, slug=health.get("slug"), version=health.get("version"))
    name = str(target.get("token_env") or "")
    token = env.get(name, "")
    if not token:
        out["token"] = (
            f"환경 변수 {name} 가 비어 있습니다 — 이 서버에 대상 토큰을 넣어야 합니다."
        )
        return out
    status_code, schema = services.probe_http(server, token, "/api/ontology/schema")
    if status_code != 200 or not isinstance(schema, dict):
        out["token"] = f"토큰으로 대상의 정의를 못 읽었습니다({status_code or '응답 없음'})."
        return out
    have = {str(one.get("slug")): one for one in schema.get("types") or []}
    out["token"] = "정상"
    out["types_present"] = [one for one in wanted if one in have]
    out["types_missing"] = [one for one in wanted if one not in have]
    out["managed_by"] = {one: have[one].get("managed_by") for one in out["types_present"]}
    return out


def _recipe_for(
    db: Session, slug: str, recipe_version: int | None
) -> tuple[dict[str, Any] | None, str]:
    from app.modules.relay import recipes

    if recipe_version is not None:
        return recipes.get(db, slug, recipe_version).body, f"레시피 판 {recipe_version}"
    row = recipes.active(db, slug)
    if row is not None:
        return row.body, f"켜진 레시피 판 {row.version}"
    root = get_settings().relay_pipelines_dir
    for path in find(root) if root.is_dir() else []:
        one = describe(path)
        if one.slug == slug and one.recipe is not None:
            return json.loads(one.recipe.read_text(encoding="utf-8")), "정의 파일의 레시피"
    return None, ""


def trial(
    db: Session, slug: str, version: int, *, recipe_version: int | None = None, limit: int = 10
) -> dict[str, Any]:
    """그 판으로 원천을 받아 보고 · 대상을 보고 · 레시피로 바꿔 본다 — **아무것도 안
    보낸다.**"""
    from app.modules.relay import recipes, services

    row = get(db, slug, version)
    body = dict(row.body)
    try:
        check_body(body)
    except DefinitionError as failure:
        raise _invalid(str(failure)) from failure
    guard.check_env(body)
    kind, spec = check_adapter(dict(body["adapter"]))
    allowed = _allowed(db)
    guard.check_source(kind, spec, allowed)
    guard.check_target(dict(body["target"]), allowed)
    env = services.secrets_env()
    described = describe_row(row, None)
    out: dict[str, Any] = {"version": version, "problems": list(described.problems)}

    recipe_body, recipe_label = _recipe_for(db, slug, recipe_version)
    wanted = [
        str(one["type_slug"])
        for one in (recipe_body or {}).get("types") or []
        if "type_slug" in one
    ]
    out["target"] = _target_check(dict(body["target"]), env, wanted)
    server = str(dict(body["target"]).get("server") or "").rstrip("/")
    for other in services.catalog(db):
        if other.draft or other.slug == slug or other.target_server != server:
            continue
        shared = sorted(set(wanted) & set(other.types))
        if shared:
            out["problems"].append(
                f"{', '.join(shared)} 을(를) 이미 {other.slug} 가 같은 대상에 넣습니다 — "
                "한 타입을 두 길로 채우면 서로가 안 보낸 것을 지움(무덤)으로 냅니다. 한 "
                "파이프라인만 켜세요."
            )

    try:
        adapter = build_adapter(
            dict(body["adapter"]), env, file_roots=get_settings().relay_file_root_paths
        )
        probe = adapter.probe()
        cap = get_settings().relay_trial_rows
        fetched = adapter.fetch(None, limit=cap or None)
    except Exception as failure:  # 원천이 무엇을 던지든 까닭을 보인다
        out["source"] = {"ok": False, "error": str(failure)}
        return out
    seen: dict[str, None] = {}
    filled: set[str] = set()
    for one in fetched.records:
        for column, value in one.values.items():
            seen.setdefault(column, None)
            if str(value).strip():
                filled.add(column)
    declared = list(described.columns) if not described.columns_all else list(seen)
    out["source"] = {
        "ok": True,
        "probe": probe,
        "rows": len(fetched.records),
        "deleted": sum(1 for one in fetched.records if one.deleted),
        "complete": fetched.complete,
        # 상한에 닿아 멈춘 것만 — 어댑터는 상한일 때만 덜 받고 돌아온다(그 밖은 예외)
        "capped": cap
        if cap and not fetched.complete and len(fetched.records) >= cap
        else None,
        "watermark": fetched.watermark,
        "columns": list(seen),
        "always_empty": [one for one in declared if one not in filled],
        "sample": [
            _trim({"key": one.key, **dict(one.values)})
            for one in fetched.records[: max(0, min(limit, SAMPLE_MAX))]
        ],
    }
    if recipe_body is None:
        out["recipe"] = {
            "note": "레시피가 아직 없습니다 — recipe_draft_save 로 지은 뒤 recipe_version 을 "
            "주고 다시 시험하세요."
        }
        return out
    mirror = Mirror()
    mirror.apply(fetched)
    work = Workdir(get_settings().relay_work_dir, slug)
    out["recipe"] = {
        "used": recipe_label,
        "problems": recipes.problems_of(described, recipe_body),
        **recipes.dry_run(
            mirror, recipe_body, work, described.source, compare=fetched.complete
        ),
    }
    return out


def discard(db: Session, user: User, slug: str, version: int) -> RelayDefinition:
    """초안을 버린다 — 초안만. 켜진 판이 없는 새 파이프라인은 초안을 다 버리면 지도에서
    빠진다. 토큰도 된다(켜는 일이 아니다 — AI 가 자기 시험 초안을 치운다)."""
    row = get(db, slug, version)
    if row.status != "draft":
        raise Conflict(
            code("RELAY", 17), f"초안만 버립니다 — 정의 판 {version} 은 {row.status} 입니다."
        )
    row.status = "discarded"
    audit.record(
        db,
        action="relay.definition.discard",
        actor=user,
        target_table="relay_definitions",
        target_id=row.id,
        target_label=f"{slug} 정의 판 {row.version}",
    )
    db.commit()
    db.refresh(row)
    return row
