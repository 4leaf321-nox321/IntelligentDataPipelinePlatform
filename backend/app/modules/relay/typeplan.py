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
from app.modules.relay import approval, guard, services
from app.shared import audit
from app.shared.errors import AppError, code
from relay_core import sp
from relay_core.state import Workdir
from relay_core.submit import errors_of
from relay_core.typedraft import check_group

#: 한 번에 올리는 타입의 상한 — 정의 초안은 원천 하나의 몫이다.
TYPES_MAX = 50
#: 정의 본문의 상한(바이트).
BODY_MAX = 512 * 1024


def _check(ontology: Any) -> dict[str, Any]:
    if not isinstance(ontology, dict):
        raise AppError(code("RELAY", 40), "ontology는 JSON 객체여야 합니다.", status=422)
    types = ontology.get("types")
    if not isinstance(types, list) or not types:
        raise AppError(
            code("RELAY", 40),
            "ontology.types에 타입이 하나 이상 있어야 합니다. type_draft 또는 일괄 초안 "
            "생성 결과의 ontology를 그대로 입력하세요.",
            status=422,
        )
    if len(types) > TYPES_MAX:
        raise AppError(
            code("RELAY", 40), f"타입은 한 번에 최대 {TYPES_MAX}개입니다.", status=422
        )
    if any(not isinstance(one, dict) or not one.get("slug") for one in types):
        raise AppError(
            code("RELAY", 40), "ontology.types의 각 항목에 slug가 있어야 합니다.", status=422
        )
    if len(json.dumps(ontology, ensure_ascii=False).encode()) > BODY_MAX:
        raise AppError(code("RELAY", 40), "정의가 너무 큽니다(최대 512KB).", status=413)
    groups = ontology.get("groups")
    if groups is not None:
        if not isinstance(groups, list) or not all(isinstance(one, dict) for one in groups):
            raise AppError(
                code("RELAY", 40),
                "ontology.groups는 사이드바 그룹(객체)의 목록이어야 합니다.",
                status=422,
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
            f"대상에 없는 사이드바 그룹을 참조합니다: {', '.join(missing)}. 기존 그룹 중에서 "
            "선택하거나 ontology.groups에 해당 그룹({slug, label, icon})을 함께 입력하세요. "
            f"기존 그룹: {shown}.",
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
            "타입 계획을 생성하려면 정의에 대상(target.server)과 원천 이름(source)이 있어야 "
            "합니다.",
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
            f"대상 토큰({found.target_token_env})이 미입력 상태입니다. 사람에게 [중계 › 인증 "
            "정보] 화면에서 입력을 요청하세요.",
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
        text = str(stop).replace(token, "***").rstrip(". ")
        hint = (
            " 대상 토큰에 ontology:write 범위가 없으면 SP 관리자에게 범위 추가를 요청하거나, "
            "ontology(SP 타입 정의 초안)를 SP 관리자에게 전달하세요."
            if "ontology:write" in text
            else ""
        )
        raise AppError(
            code("RELAY", 40), f"대상이 타입 계획을 거부했습니다: {text}.{hint}", status=502
        ) from None
    job_id = str(result.get("job_id") or "")
    errors = errors_of(result)
    link = sp.pipeline.jobs_link(found.target_server, job_id) if job_id else ""
    names = [str(one["slug"]) for one in body["types"]]
    if job_id and not errors:
        _remember(slug, job_id, names)  # 오류가 있는 계획은 적용할 것이 아니다
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
        "errors": errors,
        "next": [
            "계획에 오류가 있어 적용할 수 없습니다. 정의를 수정해 다시 호출하세요. "
            f"링크: {link}"
        ]
        if errors
        else [
            f"사람에게 이 계획({job_id[:8]})의 타입 · 속성을 보여 주고 적용 승인을 받으세요. "
            "승인하면 type_plan_apply(slug, job_id, approval)로 적용합니다(토큰에 "
            "relay:approve 범위 필요). 사람이 직접 하려면 대상 SP의 [작업] 화면에서 "
            f"[적용]합니다. 링크: {link}",
            "적용 후 pipeline_test(recipe_version=…)를 다시 호출해 target.types_missing이 "
            "비어 있는지 확인하세요.",
        ],
    }


#: 이 파이프라인이 올린 타입 계획 — 작업 폴더의 파일. 타입 계획 적용은 여기 있는 번호만 받는다
#: (대화에서 승인받은 토큰이 남의 계획 · 데이터 계획을 이 길로 적용하지 않게).
PLANS = "type-plans.json"


def _plans(slug: str) -> Workdir:
    return Workdir(get_settings().relay_work_dir, slug)


def _remember(slug: str, job_id: str, types: list[str]) -> None:
    work = _plans(slug)
    found = work.read_json(PLANS)
    rows = found if isinstance(found, list) else []
    rows.append({"job_id": job_id, "types": types, "at": datetime.now(UTC).isoformat()})
    work.write_json(PLANS, rows[-50:])


def apply(
    db: Session, user: User, slug: str, job_id: str, approved: approval.Approved
) -> dict[str, Any]:
    """이 파이프라인이 올린 타입 계획을 대상 SP 에서 적용한다 — 끝날 때까지 기다린다(정의만
    바꾸므로 짧다). 대상 토큰에 그 적용 권한이 없으면 대상의 거부를 그대로 전한다."""
    from relay_core.definition import Target
    from relay_core.submit import SubmitError, Submitter

    found = services.by_slug(slug, db)
    rows = _plans(slug).read_json(PLANS)
    known = [
        one for one in (rows if isinstance(rows, list) else []) if one.get("job_id") == job_id
    ]
    if not known:
        raise AppError(
            code("RELAY", 50),
            f"{slug}이 올린 타입 계획 중에 {job_id[:8]}이 없습니다. type_plan의 응답에 있는 "
            "job_id를 입력하세요.",
            status=404,
        )
    token = services.secrets_env().get(found.target_token_env, "")
    if not token:
        raise AppError(
            code("RELAY", 40),
            f"대상 토큰({found.target_token_env})이 미입력 상태입니다. 사람에게 [중계 › 인증 "
            "정보] 화면에서 입력을 요청하세요.",
            status=409,
        )
    target = Target(name=found.target_name, server=found.target_server, token=token)
    try:
        result = Submitter(target).apply(job_id)
    except SubmitError as refused:
        text = str(refused).replace(token, "***")
        raise AppError(
            code("RELAY", 40), f"대상이 타입 계획 적용을 거부했습니다: {text}", status=502
        ) from None
    audit.record(
        db,
        action="relay.type_plan.apply",
        actor=user,
        target_table="relay_pipelines",
        target_id=None,
        target_label=slug,
        changes={"job_id": job_id, "types": known[-1].get("types"), "by": approved.by},
    )
    approval.record(db, user, approved, action="type_plan", target=slug, job_id=job_id)
    db.commit()
    return {
        "job_id": job_id,
        "types": known[-1].get("types"),
        "result": result,
        "message": (
            "타입 계획을 적용했습니다. pipeline_test로 대상에 타입이 생겼는지 확인하세요."
        ),
    }
