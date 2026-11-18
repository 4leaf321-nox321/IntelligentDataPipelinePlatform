"""한 번에 초안 — 원천 주소 · 인증 · 원천 이름 · 대상 타입만 받아 **정의 판 · 레시피 판 초안과
시험**까지 한 번에(MCP `pipeline_scaffold`). 화면의 「새 연결」 이 사람에게 차례로 고르게 하는
것을 서버가 탐색의 단서로 고른다 — AI 는 결과를 읽고 고칠 것만 고친다.

1. 비밀 — 정의가 쓸 비밀 이름(원천 인증 · 대상 토큰)이 비었으면 **아무것도 저장하지 않고**
   사람에게 부탁할 이름을 돌려준다(이름은 비밀 목록에 「요청됨」 으로 오른다).
2. 탐색 — 칸 · 열 이름 제안과 단서(`hints` · `shape`)로 행 자리 · 쪽 넘김 · 식별자 · 변경
   시각 · 삭제 표시를 채운다. 준 값은 그대로 둔다. 식별자를 고를 수 없으면 후보를 돌려준다.
3. 정의 판 초안 — 식별자가 여러 칸이면 계산 칸(`RELAY_KEY`)을 더한다.
4. 대상 타입 — 있으면 칸 이름으로 잇는 레시피 첫 판(`relay_core.scaffold`), 없으면 SP 타입
   정의 초안(`relay_core.typedraft` — SP 관리자에게 넘긴다)과 그 레시피.
5. 레시피 판 초안 · 시험 — 대상에는 아무것도 보내지 않는다.

**사용하지 않는다** — 판의 사용 · 계획의 적용은 사람이 화면에서 한다(ADR 0001 §2).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.relay import definitions, recipes, secrets, services
from app.shared.errors import AppError, code
from relay_core import scaffold as engine
from relay_core.definition import env_names
from relay_core.typedraft import draft as type_draft

#: 단서로 채우는 어댑터 칸 — 어댑터 종류마다 있는 것만 쓴다.
_SHAPE_KEYS = ("rows_path", "paging", "next_path")


def _fields_of(kind: str) -> set[str]:
    for one in definitions.kinds()["kinds"]:
        if one["kind"] == kind:
            return {f["name"] for f in one["fields"]}
    raise AppError(code("RELAY", 38), f"모르는 어댑터 종류입니다: {kind}", status=422)


def _target(db: Session, given: dict[str, Any] | None, target_from: str) -> dict[str, Any]:
    """대상 — 준 것, 아니면 다른 파이프라인의 것, 아니면 하나뿐인 대상."""
    if given:
        return dict(given)
    known = {
        (one.target_server, one.target_token_env, one.target_name): one
        for one in services.catalog(db)
        if one.target_server
    }
    if target_from:
        found = services.by_slug(target_from, db)
        return dict(found.body.get("target") or {})
    if len(known) == 1:
        (one,) = known.values()
        return dict(one.body.get("target") or {})
    choices = [f"{name}({server})" for server, _env, name in known] or ["없음"]
    raise AppError(
        code("RELAY", 38),
        "대상을 정하지 못했습니다 — target 을 주거나 target_from 에 대상이 같은 파이프라인을 "
        f"적으세요. 알려진 대상: {', '.join(sorted(choices))}",
        status=422,
    )


def _trial_brief(trial: dict[str, Any]) -> dict[str, Any]:
    source = trial.get("source") or {}
    target = trial.get("target") or {}
    recipe = trial.get("recipe") or {}
    return {
        "source": {
            k: source.get(k)
            for k in ("ok", "rows", "complete", "capped", "columns", "always_empty", "error")
            if k in source
        },
        "target": {
            k: target.get(k)
            for k in ("reachable", "token", "types_present", "types_missing", "error")
            if k in target
        },
        "recipe": {
            k: recipe.get(k)
            for k in ("clean", "rows", "unresolved_count", "would_send", "problems", "against")
            if k in recipe
        },
        "intake": trial.get("intake"),
        "problems": trial.get("problems") or [],
    }


def run(
    db: Session, user: User, request: Request, slug: str, spec: dict[str, Any]
) -> dict[str, Any]:
    adapter = dict(spec.get("adapter") or {})
    kind = str(adapter.get("kind") or "rest")
    allowed = _fields_of(kind)
    if kind == "kafka" and not adapter.get("group_id"):
        # 소비 그룹은 파이프라인마다 — 다른 시스템의 그룹을 쓰면 그쪽 메시지를 가져간다. 앞에
        # 이 설치의 이름을 붙여 브로커 쪽에서 누가 읽는지 보이게.
        adapter["group_id"] = f"{get_settings().app_slug}-{slug}"
    target = _target(db, spec.get("target"), str(spec.get("target_from") or ""))
    type_slug = str(spec["type_slug"])

    # 1. 비밀 — 비었으면 저장하지 않는다.
    names = list(dict.fromkeys([*env_names(adapter), *env_names({"target": target})]))
    source_names = env_names(adapter)
    for name in names:
        role = secrets.SOURCE if name in source_names else secrets.TARGET
        secrets.request(db, user, slug, [name], role)
    lacking = secrets.missing(db, names)
    if lacking:
        return {
            "status": "needs_secrets",
            "secrets_needed": lacking,
            "next": [
                "사람에게 화면 › 중계 › 비밀 에서 "
                + " · ".join(lacking)
                + " 의 값 입력을 부탁한다 "
                "(값은 대화로 받지 않는다).",
                "입력되면 pipeline_scaffold 를 같은 인자로 다시 부른다.",
            ],
        }

    # 2. 탐색 — 단서로 모양을 채운다(준 값은 그대로).
    found = definitions.discover(db, adapter)
    if not found.get("ok"):
        return {"status": "discover_failed", "error": found.get("error"), "discover": found}
    seen = found.get("found") or {}
    hints = seen.get("hints") or {}
    shape = seen.get("shape") or {}
    chosen: dict[str, Any] = {}
    rows = (shape.get("rows_path") or {}) if shape else {}
    if (
        "rows_path" in allowed
        and "rows_path" not in adapter
        and rows
        and not rows.get("found")
    ):
        best = (rows.get("candidates") or [{}])[0]
        if "path" in best:
            chosen["rows_path"] = (best["path"], "응답에서 찾은 목록 자리")
    paging = (shape.get("paging") or {}) if shape else {}
    if (
        "paging" in allowed
        and "paging" not in adapter
        and paging.get("guess")
        in (
            "next_url",
            "cursor",
            "link",
            "none",
        )
    ):
        chosen["paging"] = (paging["guess"], paging.get("evidence", ""))
        if paging.get("next_path") and "next_path" not in adapter:
            chosen["next_path"] = (paging["next_path"], "다음 쪽 값의 자리")
    columns = dict(spec.get("columns") or seen.get("columns") or {})
    key = spec.get("key") or adapter.get("key") or (hints.get("key") or [None])[0]
    if not key:
        return {
            "status": "needs_key",
            "candidates": [one["path"] for one in seen.get("paths") or []][:30],
            "next": ["원천의 안 바뀌는 식별자 자리를 골라 key 로 다시 부른다."],
        }
    if not spec.get("key") and not adapter.get("key"):
        chosen["key"] = (key, "값이 모두 다른 칸(식별자 후보의 첫째)")
    for field, hint_key in (("updated_at", "updated_at"), ("deleted", "deleted")):
        target_field = "modified_field" if kind == "odata" and field == "updated_at" else field
        if target_field in allowed and target_field not in adapter and hints.get(hint_key):
            chosen[target_field] = (hints[hint_key][0], "이름 · 값 꼴로 고른 후보")
    for field, (value, _why) in chosen.items():
        adapter[field] = value
    adapter["key"] = key
    adapter["columns"] = columns

    # 3. 정의 판 초안.
    try:
        key_spec = engine.key_column(columns, key)
    except ValueError as failure:
        raise AppError(code("RELAY", 38), str(failure), status=422) from failure
    body: dict[str, Any] = {
        "format": "relay-pipeline/1",
        "slug": slug,
        "source": str(spec["source"]),
        "system": str(spec.get("system") or str(spec["source"]).upper()),
        "adapter": adapter,
        "target": target,
    }
    if key_spec.get("computed"):
        body["computed"] = key_spec["computed"]
    if spec.get("schedule"):
        body["schedule"] = spec["schedule"]
    note = str(spec.get("note") or "한 번에 초안")
    row, def_problems = definitions.save_draft(db, user, request, slug, body, note)

    # 4. 대상 타입 → 레시피 첫 판(없으면 SP 타입 정의 초안).
    described = services.by_slug(slug, db)
    schema, schema_note = services.target_schema(described, services.secrets_env(), db)
    label_col = str(spec.get("label") or "") or engine.label_column(
        columns, hints.get("label") or []
    )
    ontology = None
    if type_slug in schema:
        made = engine.first_recipe(
            type_slug, columns, schema[type_slug], key_spec["column"], label_col
        )
        recipe, mapping = made["recipe"], {k: v for k, v in made.items() if k != "recipe"}
        status = "drafted"
    else:
        samples = {one["path"]: one["sample"] for one in seen.get("paths") or []}
        typed = type_draft(
            type_slug=type_slug,
            label=str(spec.get("type_label") or ""),
            columns={col: [str(samples.get(path, ""))] for col, path in columns.items()},
            key_column=key_spec["column"],
            label_column=label_col,
            system=body["system"],
        )
        recipe, ontology, mapping = typed["recipe"], typed["ontology"], {}
        status = "type_missing"
    rrow, recipe_problems = recipes.save_draft(db, user, request, described, recipe, note)

    # 5. 시험 — 보내지 않는다.
    trial = definitions.trial(db, slug, row.version, recipe_version=rrow.version)
    next_steps = []
    if status == "type_missing":
        next_steps.append(
            f"대상에 {type_slug} 타입이 없습니다 — ontology(SP 타입 정의 초안)를 SP 관리자"
            "에게 넘겨 등록을 부탁한다. 등록 뒤 레시피를 다시 시험한다."
        )
    if mapping.get("required_missing"):
        gaps = ", ".join(
            f"{one['property']}({one['label']})" for one in mapping["required_missing"]
        )
        next_steps.append(
            f"대상이 꼭 받는 칸 {gaps} 이 비었습니다 — 이대로 사용하면 실행이 대상에서 "
            "멈춘다. references 의 candidates(원천 열)로 match 를 적거나, 원천에 그 값이 "
            "없으면 사람에게 알린다."
        )
    if mapping.get("unmatched") or mapping.get("references"):
        next_steps.append(
            "unmatched(이름으로 못 이은 칸) · references(참조 칸 — match 로 상대 타입의 "
            "식별자와 맞춘다)를 원천 열(unused)과 견주어 recipe_draft_save 로 고친다."
        )
    next_steps.append(
        f"시험이 깨끗하면 사람에게: 화면 › 파이프라인 › {slug} 에서 정의 판 {row.version} · "
        f"레시피 판 {rrow.version} 을 「사용」 해 달라고 한다."
    )
    return {
        "status": status,
        "definition": {
            "version": row.version,
            "problems": def_problems,
            "chosen": {k: {"value": v, "why": why} for k, (v, why) in chosen.items()},
            "body": body,
        },
        "recipe": {"version": rrow.version, "problems": recipe_problems, **mapping},
        "ontology": ontology,
        "target_schema_note": schema_note,
        "shape_notes": shape.get("notes") if shape else [],
        "trial": _trial_brief(trial),
        "next": next_steps,
    }
