"""`/api/relay` — 연결 지도 · 파이프라인 상세는 **로그인한 사람 누구나**, 실행 · 적용은 시스템
관리자만(ADR 0001 §6). 실행은 요청 안에서 하지 않고 작업으로 넣는다 — 원천 · 대상이 느리면
요청이 끊긴다."""

from __future__ import annotations

import itertools
import json
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.modules.accounts.models import User
from app.modules.jobs import services as job_services
from app.modules.relay import allow as relay_allow
from app.modules.relay import (
    approval,
    definitions,
    inbound,
    overview,
    recipes,
    services,
    typeplan,
    unresolved,
)
from app.modules.relay import jobs as relay_jobs  # noqa: F401 — import 가 곧 작업 종류 등록
from app.modules.relay import scaffold as scaffolding
from app.modules.relay import secrets as relay_secrets
from app.modules.relay.models import RelayDefinition, RelayRecipe
from app.modules.relay.schemas import (
    AlertWebhookOut,
    AlertWebhookTestOut,
    AllowedHostsIn,
    AllowedHostsOut,
    DefinitionIn,
    DefinitionOut,
    DefinitionSaved,
    DefinitionTestIn,
    DiscoverIn,
    DraftIn,
    InboxOut,
    PipelineDetail,
    RecipeIn,
    RecipeOut,
    RecipeSaved,
    RecipeTestIn,
    RelayMap,
    RelayOverview,
    ScaffoldIn,
    SecretIn,
    SecretOut,
    SecretRequestIn,
    SourceSpecIn,
    SuggestIn,
    SuggestOut,
    TraceOut,
    TrendOut,
    TypeDraftIn,
    TypePlanIn,
)
from app.shared import audit
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import AppError, Conflict, NotFound, code
from relay_core import intake, retract
from relay_core.state import TREND_MAX, Workdir

router = APIRouter(prefix="/relay", tags=["relay"])


class RunIn(BaseModel):
    human_edits: str | None = None
    """대상 SP 에서 사람이 고친 칸 — 비우면 keep(비켜 가고 계획에 적음), overwrite 면
    덮는다."""
    accept: bool = False
    """받기 점검의 멈춤을 확인하고 진행한다(「확인 후 진행」) — **지금 멈춘 건수 · 칸까지만**
    (`relay_core.intake.Accept`). 화면에서 사람만 — 토큰으로는 거절한다."""
    full: bool = False
    """전량 다시 받기 — 워터마크를 두고 처음부터 받는다(어댑터의 칸을 고친 뒤). 안 온 것은
    삭제로 읽히므로 받기 점검이 대량 삭제를 막는다."""
    approval: str = ""
    """토큰(MCP)의 확인 후 진행 — 사람이 대화에서 승인한 말(`approval.py`)."""
    accept_deleted: int | None = None
    """토큰(MCP)의 확인 후 진행 — 사람에게 보여 준 삭제 건수. 지금 중단 사유와 같아야 한다."""


class ApproveIn(BaseModel):
    """화면 대신 대화에서 승인한 실행(`approval.py`) — 화면의 요청은 본문 없이 온다."""

    approval: str = ""
    """사람이 승인한 말 그대로 — 토큰(MCP)의 요청에만 필요하다."""


class ApplyIn(ApproveIn):
    plan_job_id: str = ""
    """토큰(MCP)의 적용 — 사람에게 보여 준 계획의 번호. 지금 적용 대기 계획과 같아야 한다."""


class TypePlanApplyIn(ApproveIn):
    job_id: str
    """이 파이프라인이 올린 타입 계획의 번호(`type_plan` 의 응답)."""


class QueuedOut(BaseModel):
    job_id: str
    kind: str


@router.get("/map", response_model=RelayMap)
def relay_map(
    probe: bool = Query(False, description="대상 상태를 저장값 대신 지금 조회한다."),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RelayMap:
    return services.build_map(db, probe=probe)


@router.get("/overview", response_model=RelayOverview)
def relay_overview(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> RelayOverview:
    """운영 요약 — 상태별 건수 · 확인 필요 목록 · 대상 상태(MCP `relay_overview`)."""
    return overview.build(db)


def _webhook_status() -> AlertWebhookOut:
    from app.modules.relay import webhook

    sent = webhook.last() or {}
    return AlertWebhookOut(
        configured=webhook.configured(),
        host=webhook.host(),
        last_at=sent.get("at"),
        last_ok=sent.get("ok"),
        last_error=str(sent.get("error") or ""),
    )


@router.get("/alert-webhook", response_model=AlertWebhookOut)
def alert_webhook(_: User = Depends(require_system_admin)) -> AlertWebhookOut:
    """알림 웹훅의 상태 — 설정 여부 · 호스트 · 마지막 전송 결과. 주소는 서버 `.env` 에만
    있다."""
    return _webhook_status()


@router.post("/alert-webhook/test", response_model=AlertWebhookTestOut)
def alert_webhook_test(user: User = Depends(require_system_admin)) -> AlertWebhookTestOut:
    """알림 웹훅 시험 전송 — 설정한 주소로 시험 메시지 하나를 보낸다."""
    from app.modules.notifications import services as notifications
    from app.modules.relay import webhook

    if not webhook.configured():
        raise Conflict(
            code("RELAY", 45),
            "알림 웹훅이 설정되지 않았습니다. 서버 .env에 RELAY_ALERT_WEBHOOK_URL을 입력하고 "
            "서비스를 재시작하세요.",
        )
    ok = webhook.send(
        kind=notifications.RELAY_DIGEST,
        title="알림 웹훅 시험 전송",
        body=f"{user.display_name}이(가) 보낸 시험 메시지입니다. 이 메시지가 보이면 설정이 "
        "올바릅니다.",
        link="/relay/pipelines",
    )
    status = _webhook_status()
    if ok:
        return AlertWebhookTestOut(
            ok=True, message=f"{status.host}(으)로 시험 메시지를 보냈습니다."
        )
    return AlertWebhookTestOut(
        ok=False,
        message=f"{status.host}(으)로 보내지 못했습니다({status.last_error}). 주소와 서버의 "
        "네트워크 연결을 확인하세요.",
    )


@router.get("/pipelines/{slug}", response_model=PipelineDetail)
def pipeline_detail(
    slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> PipelineDetail:
    return services.detail(db, slug)


def _queue(db: Session, user: User, kind: str, params: dict[str, Any]) -> QueuedOut:
    found = services.by_slug(params["slug"], db)  # 없는 파이프라인이면 넣기 전에 404
    if found.draft:
        raise Conflict(
            code("RELAY", 15),
            f"{found.slug}의 정의가 아직 초안입니다. 정의 버전을 사용해야 실행됩니다.",
        )
    job = job_services.enqueue(db, kind=kind, params=params, user=user, workspace_id=None)
    db.commit()
    return QueuedOut(job_id=str(job.id), kind=kind)


@router.post("/pipelines/{slug}/run", response_model=QueuedOut, status_code=202)
def pipeline_run(
    slug: str,
    request: Request,
    body: RunIn | None = None,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> QueuedOut:
    stopped = (services.read_state(slug) or {}).get("stopped")
    if stopped:
        raise Conflict(code("RELAY", 41), retract.stopped_message(stopped))
    params: dict[str, Any] = {"slug": slug}
    if body and body.human_edits:
        params["human_edits"] = body.human_edits
    if body and body.accept:
        params["accept"] = _accept(slug, request, user, db, body)
    if body and body.full:
        params["full"] = True
    return _queue(db, user, services.RUN_KIND, params)


def _accept(
    slug: str, request: Request, user: User, db: Session, body: RunIn
) -> dict[str, Any]:
    """[확인 후 진행] — 수신 검증이 중단한 것을 **지금 적힌 그대로** 작업에 싣는다. 실행할 때
    그보다 나빠졌으면(삭제가 늘거나 다른 필드가 비면) 다시 중단한다. 대량 삭제를 통과시키는
    일이라 화면의 사람이나, 대화에서 승인받은 토큰(`approval.py`)만 한다."""
    approved = approval.check(
        request, user, what="수신 검증의 확인 후 진행", refused=23, approval=body.approval
    )
    held = (services.read_state(slug) or {}).get("held")
    accept = intake.Accept.of(held)
    if accept is None:
        raise Conflict(
            code("RELAY", 24),
            f"{slug}은 수신 검증으로 중단된 실행이 없습니다. [즉시 실행]을 클릭하세요.",
        )
    if approved.via_token and body.accept_deleted != accept.deleted:
        raise Conflict(
            code("RELAY", 48),
            f"승인한 삭제 건수({body.accept_deleted})가 지금 중단 사유의 삭제 건수"
            f"({accept.deleted})와 다릅니다. 중단 사유를 다시 보여 주고 승인을 받으세요.",
        )
    approval.record(
        db,
        user,
        approved,
        action="accept",
        target=slug,
        deleted=accept.deleted,
        columns=list(accept.columns),
    )
    return {
        "deleted": accept.deleted,
        "columns": list(accept.columns),
        "by": approved.by,
    }


@router.post("/pipelines/{slug}/apply", response_model=QueuedOut, status_code=202)
def pipeline_apply(
    slug: str,
    request: Request,
    body: ApplyIn | None = None,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> QueuedOut:
    """대기 계획을 대상에 적용한다 — **화면에서 사람만**(ADR 0001 §2). 토큰(MCP · 스크립트
    · 위임 토큰)으로 온 요청은 거절한다 — 판의 사용과 같다. 적용 도구가 MCP 에 없다는
    것만으로는 막히지 않는다(토큰으로 이 경로를 바로 부를 수 있었다, 2026-11-06 보안
    검토)."""
    given = body or ApplyIn()
    approved = approval.check(
        request, user, what="계획 적용", refused=20, approval=given.approval
    )
    params: dict[str, Any] = {"slug": slug, "by": approved.by}
    if approved.via_token:
        pending = str(
            ((services.read_state(slug) or {}).get("pending") or {}).get("job_id") or ""
        )
        wanted = given.plan_job_id.strip()
        if not pending:
            raise Conflict(code("RELAY", 48), f"{slug}에는 적용 대기 계획이 없습니다.")
        if wanted != pending:
            raise Conflict(
                code("RELAY", 48),
                f"승인한 계획({wanted[:8] or '없음'})이 지금 적용 대기 계획({pending[:8]})과 "
                "다릅니다. 계획을 다시 보여 주고 승인을 받으세요.",
            )
        approval.record(db, user, approved, action="apply", target=slug, plan=pending)
        params["approval"] = approved.approval
    return _queue(db, user, services.APPLY_KIND, params)


class ReconcileIn(BaseModel):
    plan: bool = False
    """어긋난 줄을 다시 보내는 **보정 계획**(미리보기)까지 올리나 — 적용은 사람이."""


@router.post("/pipelines/{slug}/reconcile", response_model=QueuedOut, status_code=202)
def pipeline_reconcile(
    slug: str,
    body: ReconcileIn | None = None,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> QueuedOut:
    """대상과 대조 — 대상에 지금 있는 것을 지난번 적용된 출력과 견준다(읽기만). `plan` 이면
    보정 계획까지 올린다(대상의 검토함에 뜬다 — 적용은 사람이)."""
    return _queue(
        db,
        user,
        services.RECONCILE_KIND,
        {"slug": slug, "plan": bool(body and body.plan)},
    )


class RetractIn(BaseModel):
    action: Literal["deprecate", "delete"] = "deprecate"
    """대상에서 어떻게 거두나 — 사용 중지(기본) · 지우기(대상 SP 0.4.101 부터, 기록은 남는다.
    가리키는 것이 있으면 대상이 그 줄을 막는다)."""
    all: bool = False
    """이 파이프라인이 대상에 넣은 것 **전부**."""
    types: list[str] = []
    """이 타입의 것 전부."""
    keys: dict[str, list[str]] = {}
    """타입마다 식별자 — `{"plm_model": ["M-001", …]}`."""
    previous: bool = False
    """앞서 사용 중지로 걷어낸 것 — 대상에 사용 중지로 남은 것을 지운다(`action: delete`
    로만)."""
    note: str = ""
    """왜 걷어내나(감사 기록에)."""


@router.post("/pipelines/{slug}/retract", response_model=QueuedOut, status_code=202)
def pipeline_retract(
    slug: str,
    body: RetractIn,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> QueuedOut:
    """보낸 것 걷어내기(`relay_core.retract`) — 이 파이프라인이 대상에 넣은 것(기준)에서 고른
    객체와 그것에 닿는 선을 **사용 중지 · 끊기**로 거두는 계획(미리 보기)을 올리고 파이프라인을
    멈춘다. 대상 SP 는 원천이 잠근 타입의 객체를 화면에서 못 지운다 — 그래서 원천 쪽에서
    시킨다. AI(토큰)도 계획까지는 올린다 — **적용 · 다시 켜기는 화면에서 사람이.**"""
    try:
        scope = retract.Scope.of(body.model_dump())
        retract.check(scope, body.action)
    except retract.RetractError as bad:
        raise AppError(code("RELAY", 44), str(bad), status=422) from None
    state = services.read_state(slug) or {}
    if not state.get("applied_run") and not state.get("pending"):
        raise Conflict(
            code("RELAY", 44), f"{slug}은 대상에 적용한 데이터가 없어 회수할 수 없습니다."
        )
    if state.get("stopped") and state.get("pending"):
        raise Conflict(
            code("RELAY", 44),
            f"{slug}은 회수 계획이 이미 적용 대기 중입니다. 해당 계획을 적용하거나, [재개]로 "
            "계획을 취소한 후 다시 선택하세요.",
        )
    by_token = getattr(request.state, "token_scopes", None) is not None
    who = f"{user.display_name} (MCP)" if by_token else user.display_name
    audit.record(
        db,
        action="relay.retract.request",
        actor=user,
        target_table="relay_pipelines",
        target_id=None,
        target_label=slug,
        changes={"scope": scope.to_json(), "action": body.action, "note": body.note[:200]},
    )
    return _queue(
        db,
        user,
        services.RETRACT_KIND,
        {"slug": slug, "scope": scope.to_json(), "action": body.action, "who": who},
    )


class ResumeOut(BaseModel):
    message: str


@router.post("/pipelines/{slug}/resume", response_model=ResumeOut)
def pipeline_resume(
    slug: str,
    request: Request,
    body: ApproveIn | None = None,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ResumeOut:
    """걷어내고 멈춘 파이프라인을 다시 켠다 — **화면에서 사람만**(원천에서 지웠는지 · 레시피를
    고쳤는지는 사람이 안다 — 안 그러면 다음 실행이 걷어낸 것을 다시 만든다). 걷어내기 계획이
    아직 대기 중이면, 대상에서 적용됐으면 기준으로 올리고 아니면 그 계획을 거둔다."""
    # 원천에서 회수한 행을 삭제했는지, 레시피를 수정했는지는 사람이 안다 — 화면의 사람이나
    # 대화에서 그 확인을 받은 토큰만 재개한다.
    approved = approval.check(
        request, user, what="재개", refused=42, approval=(body or ApproveIn()).approval
    )
    found = services.by_slug(slug, db)
    if any(
        one.status in ("queued", "running") for one in services.recent_jobs(db, slug, limit=5)
    ):
        raise Conflict(
            code("RELAY", 43),
            f"{slug}의 작업이 대기 중이거나 실행 중입니다. 작업이 끝난 후 재개하세요.",
        )
    token = services.secrets_env().get(found.target_token_env, "")

    def applied(job_id: str) -> bool | None:
        if not token or not found.target_server:
            return None
        status, answer = services.probe_http(found.target_server, token, f"/api/jobs/{job_id}")
        if status == 404:
            return False  # 대상에 그 작업이 없다 — 적용될 수 없다
        if status != 200 or not isinstance(answer, dict):
            return None
        return bool(answer.get("applied_by"))

    work = Workdir(get_settings().relay_work_dir, slug)
    try:
        message = retract.resume(work, applied, by=approved.by)
    except retract.RetractError as refused:
        raise Conflict(code("RELAY", 43), str(refused)) from None
    audit.record(
        db,
        action="relay.resume",
        actor=user,
        target_table="relay_pipelines",
        target_id=None,
        target_label=slug,
        changes={"message": message[:200]},
    )
    approval.record(db, user, approved, action="resume", target=slug)
    db.commit()
    return ResumeOut(message=message)


@router.get("/pipelines/{slug}/reconcile")
def pipeline_reconcile_report(
    slug: str, _: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """마지막 대조의 보고서 **전부**(상세는 본보기만 싣는다). 대조한 적이 없으면 404."""
    services.by_slug(slug, db)
    report = services.read_report(slug)
    if report is None:
        raise NotFound(code("RELAY", 18), f"{slug}은 아직 대조하지 않았습니다.")
    return report


@router.get("/pipelines/{slug}/trend", response_model=TrendOut)
def pipeline_trend(
    slug: str,
    limit: int = Query(90, ge=1, le=TREND_MAX),
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> TrendOut:
    """실행 추이 — 실행마다 받은 행 · 미러 · 계획의 건수 · 걸린 시간(`relay_core.runner`).
    작업 기록은 며칠 뒤 지워지므로 작업 폴더의 것을 읽는다. 읽기만 — 누구나."""
    services.by_slug(slug, db)  # 없는 파이프라인이면 404
    return services.trend(slug, limit)


@router.get("/pipelines/{slug}/trace", response_model=TraceOut)
def pipeline_trace(
    slug: str,
    type_slug: str = Query(min_length=1, max_length=80),
    key: str = Query(min_length=1, max_length=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """**거꾸로 보기** — 대상 객체 하나(`type_slug` · `key`)의 값이 어느 원천 행 · 칸 · 레시피
    규칙에서 왔나, 닿는 관계, 마지막 대조에서의 상태. 읽기만 한다. 못 찾으면 404 와 까닭.
    **원천 행 · 원래 값은 관리자만** — 레시피가 안 보내는 열까지 원천의 날것이다."""
    got = services.trace_value(db, slug, type_slug, key)
    if not user.is_system_admin:
        got["rows"] = []
        got["rows_hidden"] = True
        for one in got["fields"]:
            one["seen"] = []
    return got


# --- 미해결 작업함 — 승인한 대응은 레시피 판 초안으로(relay 사전) ---------------------------


@router.get("/pipelines/{slug}/unresolved", response_model=InboxOut)
def pipeline_unresolved(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> InboxOut:
    """미해결로 멈춘 실행의 미해결 — 참조마다 대상의 후보, 값 갈림. **원천 표기가 그대로**
    나오므로 시스템 관리자만. 대상에는 읽기만 한다."""
    return unresolved.read(db, slug)


@router.post("/pipelines/{slug}/unresolved/suggest", response_model=SuggestOut)
def pipeline_unresolved_suggest(
    slug: str,
    body: SuggestIn,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> SuggestOut:
    """GLM 이 대상의 후보 안에서 하나를 고른다 — 제안일 뿐, 정하는 것은 사람이다."""
    return unresolved.suggest(db, slug, body.target, body.text, body.column)


@router.post("/pipelines/{slug}/unresolved/draft", response_model=RecipeSaved, status_code=201)
def pipeline_unresolved_draft(
    slug: str,
    body: DraftIn,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RecipeSaved:
    """승인한 대응을 지금 레시피의 relay 사전에 더해 **레시피 판 초안**으로 저장한다 — 대상에
    있는 키만. 시험 · 사용은 레시피 판에서(사용은 화면의 사람만)."""
    return unresolved.draft(db, user, request, slug, body.entries, body.note, body.aggregates)


@router.get("/pipelines/{slug}/aliases.csv", include_in_schema=False)
def pipeline_alias_csv(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> Response:
    """대표 플랫폼에 **별칭 등록을 요청할 목록** — 지금 레시피의 relay 사전 그대로. Excel 이
    한글을 깨지 않게 BOM 을 붙인다. 생성 타입에 싣지 않는다(내려받는 자리다)."""
    text = unresolved.alias_csv(db, slug)
    name = f"{slug}-별칭요청.csv"
    return Response(
        content="\ufeff" + text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{slug}-aliases.csv\"; filename*=UTF-8''{quote(name)}"
            )
        },
    )


# --- 레시피의 판 — MCP 가 초안 · 시험, 사람이 화면에서 켠다 ------------------------------


def _recipe_out(row: RelayRecipe) -> RecipeOut:
    return RecipeOut(
        version=row.version,
        status=row.status,
        note=row.note,
        client=row.client,
        created_at=row.created_at.isoformat(),
        activated_at=row.activated_at.isoformat() if row.activated_at else None,
    )


@router.get("/pipelines/{slug}/recipes", response_model=list[RecipeOut])
def recipe_versions(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[RecipeOut]:
    services.by_slug(slug, db)
    return [_recipe_out(one) for one in recipes.versions(db, slug)]


@router.get("/pipelines/{slug}/recipes/{version}")
def recipe_body(
    slug: str,
    version: int,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = recipes.get(db, slug, version)
    return {**_recipe_out(row).model_dump(), "body": row.body}


@router.post("/pipelines/{slug}/recipes/{version}/discard", response_model=RecipeOut)
def recipe_discard(
    slug: str,
    version: int,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RecipeOut:
    """초안을 버린다 — 초안만(켜진 · 지난 판은 그대로). 토큰도 된다."""
    return _recipe_out(recipes.discard(db, user, slug, version))


@router.get("/pipelines/{slug}/recipe-file")
def recipe_file(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """정의 파일이 가리키는 레시피 — 켜진 판이 없을 때 도는 것. 초안을 지을 출발점이다."""
    found = services.by_slug(slug, db)
    if found.recipe is None:
        return {"path": "", "body": None, "problems": found.problems}

    return {
        "path": str(found.recipe),
        "body": json.loads(found.recipe.read_text(encoding="utf-8")),
    }


@router.post("/pipelines/{slug}/recipes", response_model=RecipeSaved, status_code=201)
def recipe_draft(
    slug: str,
    body: RecipeIn,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RecipeSaved:
    found = services.by_slug(slug, db)
    row, problems = recipes.save_draft(db, user, request, found, body.body, body.note)
    return RecipeSaved(version=row.version, problems=problems)


@router.post("/pipelines/{slug}/recipes/{version}/test")
def recipe_test(
    slug: str,
    version: int,
    body: RecipeTestIn | None = None,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """미러에 그 판을 돌려 본다 — **대상에 아무것도 안 보낸다.** 「켜면 무엇이 바뀌나」 를
    보인다. 견본 행(`sample_rows`)을 주면 미러 대신 그것으로."""
    chosen = body or RecipeTestIn()
    return recipes.trial(
        db, services.by_slug(slug, db), version, sample_rows=chosen.sample_rows
    )


@router.post("/pipelines/{slug}/recipes/{version}/activate", response_model=RecipeOut)
def recipe_activate(
    slug: str,
    version: int,
    request: Request,
    body: ApproveIn | None = None,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RecipeOut:
    """사용한다 — 화면의 사람이나, 대화에서 승인받은 토큰(`approval.py`). 다음 실행부터 이
    버전이 돈다."""
    found = services.by_slug(slug, db)
    given = (body or ApproveIn()).approval
    return _recipe_out(recipes.activate(db, user, request, found, version, approval=given))


# --- 레시피를 짓는 데 보는 것 — 원천 표본 · 대상의 정의 ---------------------------------


@router.get("/pipelines/{slug}/sample")
def source_sample(
    slug: str,
    limit: int = Query(default=20, ge=1, le=200),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """미러(마지막으로 받은 원천)의 앞 몇 줄 — 레시피가 읽는 **평평한 열** 그대로. 원천에 다시
    묻지 않는다(실행이 받아 둔 것)."""
    found = services.by_slug(slug, db)
    work = Workdir(get_settings().relay_work_dir, slug)
    # 미러를 다 읽지 않는다 — 원천이 수십만 행이면 표본 한 번에 GB 다.
    rows = list(itertools.islice(work.mirror_rows(), limit))
    return {
        "columns": list(found.columns),
        "column_paths": found.columns,
        "total": work.mirror_size(),
        "rows": [{"key": one.key, **dict(one.values)} for one in rows],
        "note": ""
        if rows
        else "아직 수신 데이터가 없습니다. 먼저 [즉시 실행]으로 원천 데이터를 수신하세요.",
    }


@router.get("/pipelines/{slug}/target-schema")
def target_schema(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """대상 SP 의 정의 — 레시피가 만드는 타입 · 그 칸(자료형 · 고를 값 · 참조 대상), 그리고
    사이드바 묶음(`groups`)과 타입마다 든 묶음(`nav_group_of`)."""
    found = services.by_slug(slug, db)
    model, note = services.target_model(found, services.secrets_env(), db)
    schema = {one: info["properties"] for one, info in model["types"].items()}
    wanted = set(found.types) | {"plm_project", "plm_task", "plm_model", "plm_test_site"}
    return {
        "target": found.target_name,
        "server": found.target_server,
        "note": note,
        "types": {
            slug_: props
            for slug_, props in schema.items()
            if slug_ in wanted or not found.types
        },
        "groups": model["groups"],
        "nav_group_of": {
            slug_: info["nav_group_slug"]
            for slug_, info in model["types"].items()
            if info["nav_group_slug"]
        },
    }


# --- 정의의 판 — 원천 · 대상 · 일정. MCP 가 탐색 · 초안 · 시험, 사람이 화면에서 켠다 ---


@router.get("/adapter-kinds")
def adapter_kinds(_: User = Depends(require_system_admin)) -> dict[str, Any]:
    """어댑터 종류마다 적을 칸 · 기본값 · 설명 — 코드의 설명을 그대로."""
    return definitions.kinds()


@router.post("/inbound/{slug}", status_code=202, include_in_schema=False)
async def inbound_push(
    slug: str, request: Request, db: Session = Depends(get_db)
) -> dict[str, Any]:
    """**보내 주는 원천(push)** 이 부른다 — 로그인 없이, 머리글 `X-Relay-Token` 의 비밀로.
    받을 함에 쌓기만 하고, 미러에 넣는 것은 다음 실행이다(`relay/inbound.py`)."""
    inbound.check_size(request.headers.get("content-length"))
    raw = await request.body()
    trial = request.query_params.get("trial", "").lower() in ("1", "true", "yes")
    return inbound.receive(db, slug, request.headers.get(inbound.HEADER, ""), raw, trial=trial)


@router.post("/type-draft")
def type_draft(body: TypeDraftIn, _: User = Depends(require_system_admin)) -> dict[str, Any]:
    """대상에 받을 타입이 없을 때 — 원천 표본으로 **SP 타입 정의 초안**과 그 타입으로 옮기는
    레시피의 첫 판을 짓는다(`relay_core.typedraft`). 아무 데도 저장 · 전송하지 않는다 — 타입은
    SP 관리자가 대상에 넣는다."""
    from relay_core.typedraft import draft

    if len(body.columns) > 300:
        raise AppError(code("RELAY", 29), "열이 너무 많습니다(최대 300개).", status=422)
    try:
        return draft(
            type_slug=body.type_slug,
            label=body.label,
            columns={k: v[:50] for k, v in body.columns.items()},
            key_column=body.key_column,
            label_column=body.label_column,
            system=body.system,
            nav_group=body.nav_group,
        )
    except ValueError as failure:
        raise AppError(code("RELAY", 29), str(failure), status=422) from failure


@router.post("/source-spec")
def source_spec(
    body: SourceSpecIn,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """원천의 **API 지침서**를 받아 정의를 고를 재료만 추린다 — OpenAPI(JSON · YAML) · OData
    `$metadata` · WSDL · 문서 화면. 저장하지 않는다. 주소는 원천과 같은 울타리 안만."""
    return definitions.source_spec(db, body.url, body.auth)


@router.post("/pipelines/{slug}/scaffold")
def pipeline_scaffold(
    slug: str,
    body: ScaffoldIn,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """**한 번에 초안** — 탐색 → 정의 판 초안 → 대상 타입에 잇는 레시피 판 초안(없으면 SP 타입
    정의 초안) → 시험. 비밀이 비었으면 아무것도 저장하지 않고 사람에게 부탁할 이름을 준다.
    사용하지 않는다 — 사용은 사람이 화면에서."""
    return scaffolding.run(db, user, request, slug, body.model_dump())


@router.post("/pipelines/{slug}/type-plan")
def type_plan(
    slug: str,
    body: TypePlanIn,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """**대상에 타입 계획을 올린다** — SP 타입 정의 초안을 그 파이프라인의 원천 이름
    (`source`)과 함께 대상 SP 의 미리 보기 계획으로. 적용은 사람이 대상 SP 화면 「작업」 에서.
    토큰도 된다
    (데이터 계획처럼 미리 보기일 뿐이다)."""
    return typeplan.submit(db, user, request, slug, body.ontology, body.note)


@router.post("/pipelines/{slug}/type-plan/apply")
def type_plan_apply(
    slug: str,
    body: TypePlanApplyIn,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """**대상 SP 의 타입 계획을 적용한다** — 이 파이프라인이 올린 계획만(`type_plan`). 화면의
    사람이나, 대화에서 승인받은 토큰(`approval.py`). 대상 SP 의 [작업] 화면에서 [적용]한 것과
    같다."""
    approved = approval.check(
        request, user, what="타입 계획 적용", refused=49, approval=body.approval
    )
    return typeplan.apply(db, user, slug, body.job_id, approved)


@router.get("/allowed-hosts", response_model=AllowedHostsOut)
def allowed_hosts(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> dict[str, Any]:
    """AI 의 탐색 · 시험이 닿아도 되는 주소 — 원천 · 대상 SP 마다 서버 설정 · 화면에서 더한
    것 · 사용 중인 정의의 것. 토큰(MCP)으로도 읽는다(어디가 막혔나)."""
    return relay_allow.status(db)


@router.put("/allowed-hosts", response_model=AllowedHostsOut)
def allowed_hosts_put(
    body: AllowedHostsIn,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """화면에서 더한 주소를 통째로 바꾼다 — 화면의 사람이나 대화에서 승인받은 토큰
    (`approval.py`), 재시작 없이."""
    return relay_allow.put(db, user, request, body.source, body.target, approval=body.approval)


@router.get("/secrets", response_model=list[SecretOut])
def secrets_list(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[SecretOut]:
    """정의가 쓰는 비밀마다 상태 — **값은 없다**. 토큰(MCP)으로도 읽는다(무엇이 비었나)."""
    return [SecretOut(**one) for one in relay_secrets.listing(db)]


@router.put("/secrets/{name}")
def secret_put(
    name: str,
    body: SecretIn,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """비밀 입력 — **사람만**(화면). 값은 중계 폴더의 비밀 함에만 두고 다시 보이지 않는다."""
    return relay_secrets.put(db, user, request, name, body.value)


@router.delete("/secrets/{name}")
def secret_delete(
    name: str,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """화면에서 넣은 비밀 삭제 — **사람만**."""
    return relay_secrets.delete(db, user, request, name)


@router.post("/secrets/requests")
def secret_request(
    body: SecretRequestIn,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """정의를 짓기 전에 필요한 비밀 **이름**을 목록에 올린다(값 없음) — AI 가 사람에게 입력을
    부탁할 수 있게."""
    names = relay_secrets.request(db, user, body.pipeline, body.names, body.role)
    return {"requested": names}


@router.post("/discover")
def source_discover(
    body: DiscoverIn,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """어댑터 설정으로 원천에 닿아 칸의 자리 · 보기 값 · 열 제안을 받는다 — 저장하지 않는다.
    부를 수 있는 주소 · 파일 폴더는 울타리 안만(`guard`)."""
    return definitions.discover(db, body.adapter)


def _summary(body: dict[str, Any]) -> str:
    adapter = dict(body.get("adapter") or {})
    what = (
        adapter.get("entity_set")
        or adapter.get("path")
        or adapter.get("table")
        or str(adapter.get("location") or adapter.get("url") or "").rsplit("/", 1)[-1]
    )
    target = dict(body.get("target") or {})
    return (
        f"{adapter.get('kind', '?')} {what} → {target.get('name') or target.get('server', '')}"
        f" · source {body.get('source', '')}"
    )


def _definition_out(row: RelayDefinition) -> DefinitionOut:
    return DefinitionOut(
        version=row.version,
        status=row.status,
        note=row.note,
        client=row.client,
        created_at=row.created_at.isoformat(),
        activated_at=row.activated_at.isoformat() if row.activated_at else None,
        summary=_summary(row.body),
    )


@router.get("/pipelines/{slug}/definitions", response_model=list[DefinitionOut])
def definition_versions(
    slug: str, _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[DefinitionOut]:
    """그 slug 의 정의 판 — 새 파이프라인이면 아직 없을 수 있다(빈 목록)."""
    return [_definition_out(one) for one in definitions.versions(db, slug)]


@router.get("/pipelines/{slug}/definitions/{version}")
def definition_body(
    slug: str,
    version: int,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = definitions.get(db, slug, version)
    return {**_definition_out(row).model_dump(), "body": row.body}


@router.get("/pipelines/{slug}/definition-file")
def definition_file(slug: str, _: User = Depends(require_system_admin)) -> dict[str, Any]:
    """같은 slug 의 정의 파일 — 켜진 판이 없을 때 도는 것. 초안을 지을 출발점이다."""
    for one in services.catalog():
        if one.slug == slug:
            return {"path": str(one.path), "body": one.body, "problems": one.problems}
    return {"path": "", "body": None, "problems": []}


@router.post("/pipelines/{slug}/definitions", response_model=DefinitionSaved, status_code=201)
def definition_draft(
    slug: str,
    body: DefinitionIn,
    request: Request,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DefinitionSaved:
    """초안 — 새 slug 면 새 파이프라인(켜기 전까지 돌지 않는다). 비밀은 거절한다."""
    row, problems = definitions.save_draft(db, user, request, slug, body.body, body.note)
    return DefinitionSaved(version=row.version, problems=problems)


@router.post("/pipelines/{slug}/definitions/{version}/test")
def definition_test(
    slug: str,
    version: int,
    body: DefinitionTestIn | None = None,
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """그 판으로 원천을 받아 보고 · 대상(판 · 토큰 · 타입)을 보고 · 레시피로 바꿔 본다 —
    **대상에 아무것도 안 보낸다.**"""
    chosen = body or DefinitionTestIn()
    return definitions.trial(
        db,
        slug,
        version,
        recipe_version=chosen.recipe_version,
        limit=chosen.limit,
        sample_rows=chosen.sample_rows,
    )


@router.post("/pipelines/{slug}/definitions/{version}/activate", response_model=DefinitionOut)
def definition_activate(
    slug: str,
    version: int,
    request: Request,
    body: ApproveIn | None = None,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DefinitionOut:
    """사용한다 — 화면의 사람이나, 대화에서 승인받은 토큰(`approval.py`). 다음 실행부터 이
    버전이 돈다."""
    given = (body or ApproveIn()).approval
    return _definition_out(
        definitions.activate(db, user, request, slug, version, approval=given)
    )


@router.post("/pipelines/{slug}/definitions/{version}/discard", response_model=DefinitionOut)
def definition_discard(
    slug: str,
    version: int,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> DefinitionOut:
    """초안을 버린다 — 초안만. 새 파이프라인의 초안을 다 버리면 지도에서 빠진다. 토큰도
    된다."""
    return _definition_out(definitions.discard(db, user, slug, version))
