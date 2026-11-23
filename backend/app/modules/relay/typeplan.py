"""대상에 타입 계획 올리기 — **SP 타입 정의 초안을 대상 SP 의 미리 보기 계획으로**(적용은
사람이 대상 SP 화면 「작업」 에서). 예전에는 datarelay 가 초안(`type_draft`)까지만 짓고,
넣으려면 대상 SP 의 MCP 를 따로 붙여야 했다 — datarelay 는 이미 대상의 주소와 토큰을 들고
있다.

- 묶음(`/api/bundles/import`)에 정의(`ontology`)와 **원천 이름(`source`)** 을 싣는다 — 그래야
  그 타입이 그 원천의 것으로 잠긴다(SP 화면에서 사람이 못 고친다 — 정본은 원천). `apply:
  false` — 미리 보기만. 데이터 계획과 같은 길이다.
- 대상 토큰에 `ontology:write` 범위가 있어야 한다(SP 가 거절하면 그 말을 그대로 돌려준다).
- 사용 중인 정의가 아니어도 된다(초안) — 그때 대상은 울타리(`guard.check_target`)를 먼저
  지난다.
- 사이드바 묶음 — 타입의 `nav_group_slug` 가 대상에 있는 묶음이거나 `groups` 로 함께 만드는
  묶음이어야 한다(보내기 전에 본다). 이미 있는 타입을 다른 묶음으로 옮기는 것도 같은 길이다 —
  `{"slug", "nav_group_slug"}` 만 보내면 SP 는 보낸 칸만 고친다. 묶음은 원천으로 잠기지 않는다.
- 원천 기록(`origin` — SP ADR 0087)을 함께 싣는다. 옛 SP 는 모르는 칸이라 무시한다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.relay import guard, services
from app.shared import audit
from app.shared.errors import AppError, code
from relay_core import sp
from relay_core.typedraft import check_group

#: 한 번에 올리는 타입의 상한 — 정의 초안은 원천 하나의 몫이다.
TYPES_MAX = 50
#: 정의 본문의 상한(바이트).
BODY_MAX = 512 * 1024


def _check(ontology: Any) -> dict[str, Any]:
    if not isinstance(ontology, dict):
        raise AppError(code("RELAY", 40), "ontology 는 JSON 객체여야 합니다.", status=422)
    types = ontology.get("types")
    if not isinstance(types, list) or not types:
        raise AppError(
            code("RELAY", 40),
            "ontology.types 에 타입이 하나는 있어야 합니다 — type_draft · 한 번에 초안의 "
            "ontology 를 그대로 주세요.",
            status=422,
        )
    if len(types) > TYPES_MAX:
        raise AppError(
            code("RELAY", 40), f"타입은 한 번에 {TYPES_MAX}개까지입니다.", status=422
        )
    if any(not isinstance(one, dict) or not one.get("slug") for one in types):
        raise AppError(
            code("RELAY", 40), "ontology.types 마다 slug 가 있어야 합니다.", status=422
        )
    if len(json.dumps(ontology, ensure_ascii=False).encode()) > BODY_MAX:
        raise AppError(code("RELAY", 40), "정의가 너무 큽니다(512KB).", status=413)
    groups = ontology.get("groups")
    if groups is not None:
        if not isinstance(groups, list) or not all(isinstance(one, dict) for one in groups):
            raise AppError(
                code("RELAY", 40), "ontology.groups 는 묶음(객체)의 목록입니다.", status=422
            )
        try:
            ontology = {**ontology, "groups": [check_group(one) for one in groups]}
        except ValueError as failure:
            raise AppError(code("RELAY", 40), str(failure), status=422) from failure
    out: dict[str, Any] = ontology
    return out


def _check_groups(body: dict[str, Any], known: list[dict[str, Any]]) -> list[str]:
    """타입이 가리키는 사이드바 묶음이 대상에 있거나 이 계획이 함께 만드나 — 새로 만드는 묶음의
    slug 들을 돌려준다(SP 가 「없는 묶음을 가리킵니다」 로 거절하기 전에)."""
    existing = {str(one.get("slug")) for one in known}
    made = [str(one["slug"]) for one in body.get("groups") or []]
    wanted = {
        str(one.get("nav_group_slug")) for one in body["types"] if one.get("nav_group_slug")
    }
    missing = sorted(wanted - existing - set(made))
    if missing:
        shown = ", ".join(sorted(existing)) or "없음"
        raise AppError(
            code("RELAY", 40),
            f"대상에 없는 사이드바 묶음을 가리킵니다: {', '.join(missing)} — 있는 묶음"
            f"({shown}) 중에서 고르거나, ontology.groups 에 그 묶음({{slug, label, icon}})을 "
            "함께 적으세요.",
            status=422,
        )
    return [one for one in made if one not in existing]


def submit(
    db: Session, user: User, request: Request, slug: str, ontology: Any, note: str = ""
) -> dict[str, Any]:
    body = _check(ontology)
    found = services.by_slug(slug, db)
    if not found.target_server or not found.source:
        raise AppError(
            code("RELAY", 40),
            "정의에 대상(target.server)과 원천 이름(source)이 있어야 타입 계획을 올립니다.",
            status=422,
        )
    if found.draft:
        guard.check_target(
            {"server": found.target_server, "token_env": found.target_token_env},
            guard.fence(services.catalog(db)),
        )
    env = services.secrets_env()
    token = env.get(found.target_token_env, "")
    if not token:
        raise AppError(
            code("RELAY", 40),
            f"대상 토큰({found.target_token_env})이 비어 있습니다 — 사람에게 화면 › 중계 › "
            "비밀 에서 넣어 달라고 하세요.",
            status=409,
        )
    model, _note = services.target_model(found, env, db)
    created = _check_groups(body, model["groups"]) if not _note else []
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    sent: dict[str, Any] = {
        "ontology": body,
        "source": found.source,
        "apply": False,
        "origin": {
            "system": get_settings().app_slug,
            "pipeline": slug,
            "run": f"type-plan-{stamp}",
        },
    }
    try:
        result = sp.pipeline._post_bundle(found.target_server, token, sent)
    except sp.pipeline.Stop as stop:
        text = str(stop).replace(token, "***")
        hint = (
            " — 대상 토큰에 ontology:write 범위가 없으면 SP 관리자에게 그 범위를 더해 달라고 "
            "하거나, ontology(SP 타입 정의 초안)를 SP 관리자에게 넘깁니다."
            if "ontology:write" in text
            else ""
        )
        raise AppError(
            code("RELAY", 40), f"대상이 타입 계획을 받지 않았습니다: {text}{hint}", status=502
        ) from None
    job_id = str(result.get("job_id") or "")
    link = sp.pipeline.jobs_link(found.target_server, job_id) if job_id else ""
    names = [str(one["slug"]) for one in body["types"]]
    audit.record(
        db,
        action="relay.type_plan.submit",
        actor=user,
        target_table="relay_pipelines",
        target_id=None,
        target_label=slug,
        changes={"types": names, "job_id": job_id, "note": note[:200]},
    )
    db.commit()
    return {
        "job_id": job_id,
        "link": link,
        "source": found.source,
        "types": names,
        "groups_created": created,
        "nav_group_of": {
            str(one["slug"]): one["nav_group_slug"]
            for one in body["types"]
            if one.get("nav_group_slug")
        },
        "plan": result.get("ontology"),
        "next": [
            f"사람에게: 대상 SP 화면 › 작업 에서 이 계획({job_id[:8]})을 열어 확인하고 "
            f"「적용」 해 달라고 한다 — {link}",
            "적용되면 pipeline_test(recipe_version=…)를 다시 불러 target.types_missing 이 "
            "비었는지 본다.",
        ],
    }
