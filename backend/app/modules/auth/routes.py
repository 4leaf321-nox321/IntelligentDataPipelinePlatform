"""인증 라우터.

**refresh 토큰은 httpOnly 쿠키로만 오간다.** 자바스크립트가 읽을 수 없으므로 XSS 로
새지 않고, 배포가 동일 출처(백엔드 한 프로세스가 SPA 까지 서빙)라 별도 설정도
필요 없다. access 토큰은 응답 본문으로만 주고 프론트는 메모리에 둔다 —
localStorage 에 두면 XSS 한 번에 탈취된다.

쿠키 path 를 /api/auth 로 제한해 일반 API 호출에는 실려 나가지 않는다.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.modules.accounts.models import User
from app.modules.auth import delegation, portal, services
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    DelegatedTokenOut,
    DelegationRevokeOut,
    LoginRequest,
    LoginResponse,
    PatCreateRequest,
    PatCreateResponse,
    PatOut,
    PortalExchangeRequest,
    PortalStatusOut,
    ProfileUpdateRequest,
    TokenScopesOut,
    UserOut,
)
from app.modules.server import services as server_services
from app.shared import scopes
from app.shared.access_log import USER_ID_SCOPE_KEY
from app.shared.auth import current_user, require_system_admin
from app.shared.errors import AppError, NotFound, code

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _cookie_path() -> str:
    """리프레시 쿠키의 path — 브라우저가 보는 주소 기준이라 접두어를 붙인다. 접두어 없이
    `/api/auth` 로 두면 `/plm/api/auth/refresh` 요청에 쿠키가 안 실려 로그인이 안 이어진다."""
    return f"{get_settings().base_path}/api/auth"


def _set_refresh_cookie(request: Request, response: Response, raw: str) -> None:
    settings = get_settings()
    # **Secure 는 요청이 https 로 왔을 때 붙는다.** 설정으로 늘 켜 두면 메인 서버 없이 IP 로
    # 직접(http) 확인할 때 브라우저가 쿠키를 버려 새로고침마다 로그인 화면이다(실측).
    # 프록시 뒤에서는 TRUST_PROXY 가 X-Forwarded-Proto 를 읽어 https 로 판정한다. 설정은
    # 「강제로 켬」.
    secure = settings.refresh_cookie_secure or request.url.scheme == "https"
    response.set_cookie(
        settings.refresh_cookie_name,
        raw,
        max_age=settings.refresh_token_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path=_cookie_path(),
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(get_settings().refresh_cookie_name, path=_cookie_path())


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    user = services.authenticate(db, str(payload.email), payload.password)
    access, expires_in, refresh_raw = services.issue_session(
        db, user, request.headers.get("user-agent")
    )
    _set_refresh_cookie(request, response, refresh_raw)
    return LoginResponse(
        access_token=access, expires_in=expires_in, user=services.user_out(db, user)
    )


@router.post("/refresh", response_model=LoginResponse)
def refresh(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> LoginResponse:
    raw = request.cookies.get(get_settings().refresh_cookie_name)
    if not raw:
        raise AppError(code("AUTH", 3), "세션이 없습니다. 로그인해 주세요.", status=401)

    user, access, expires_in, new_raw = services.rotate_refresh(
        db, raw, request.headers.get("user-agent")
    )
    _set_refresh_cookie(request, response, new_raw)
    return LoginResponse(
        access_token=access, expires_in=expires_in, user=services.user_out(db, user)
    )


# --- HWAX 포털 로그인(jwt-handoff, ADR 0028) -------------------------------------


@router.post(
    "/portal-callback",
    status_code=303,
    response_class=RedirectResponse,
    responses={303: {"description": "SPA 루트(성공) 또는 로그인 화면(`?sso=<사유>`)으로"}},
)
def portal_callback(
    request: Request,
    token: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """포털이 보낸 launch 토큰(폼 필드 `token`)으로 로그인한다 — **브라우저가 오는 길이다.**

    성공하면 refresh 쿠키를 심고 SPA 루트(`<접두어>/`)로 303 — SPA 는 기동 때 그 쿠키로 access
    를 받는다(비밀번호 로그인 뒤 새로 고침한 것과 같은 길). 실패 · 승인 대기 · 정지는 로그인
    화면(`<접두어>/login?sso=<사유>`)으로 303. **꺼져 있으면 404 JSON** — 포털 update-all 의
    진단표가 가짜 토큰을 던져 그 숫자로 「앱 SSO 꺼짐」 을 가른다(켜져 있으면 가짜에도 303 =
    정상).
    """
    settings = get_settings()
    if not portal.enabled(settings):
        raise NotFound(
            code("AUTH", 9),
            "포털 SSO 가 꺼져 있습니다 — 이 설치의 .env 에 PORTAL_JWKS_URL 이 없습니다.",
        )
    base = settings.base_path
    try:
        user, _access, _expires_in, refresh_raw = portal.sign_in(
            db, token or "", request.headers.get("user-agent")
        )
    except portal.PortalLoginError as failed:
        _log_portal_refusal(failed)
        return RedirectResponse(f"{base}/login?sso={failed.reason}", status_code=303)
    # 접근 로그가 「누가」 를 알게 — 미들웨어는 인증보다 바깥이라 스스로 모른다.
    request.scope[USER_ID_SCOPE_KEY] = user.id
    response = RedirectResponse(f"{base}/", status_code=303)
    _set_refresh_cookie(request, response, refresh_raw)
    return response


def _log_portal_refusal(failed: portal.PortalLoginError) -> None:
    # 승인 대기 · 정지는 사람이 할 일이 있는 정상적인 거절이고, 나머지는 살펴볼 일이다.
    level = (
        logging.INFO
        if failed.reason in (portal.PENDING, portal.SUSPENDED, portal.DELETED)
        else logging.WARNING
    )
    logger.log(level, "포털 로그인 거절(%s): %s", failed.reason, failed.detail)


#: 교환의 거절 숫자 — 토큰 문제는 401, 계정 문제는 403, 포털 공개 키를 못 받으면 502. 화면은
#: 숫자가 아니라 `details.reason` 으로 사람 말을 고른다(콜백의 `?sso=` 와 같은 사유).
_EXCHANGE_STATUS = {
    portal.INVALID: 401,
    portal.EXPIRED: 401,
    portal.UNAVAILABLE: 502,
}


@router.post(
    "/portal-exchange",
    response_model=LoginResponse,
    responses={
        401: {"description": "토큰을 확인하지 못함 · 만료 — `details.reason`"},
        403: {
            "description": "승인 대기 · 정지 · 삭제 · sub 로만 맞은 관리자 — `details.reason`"
        },
        404: {"description": "포털 로그인 꺼짐(`PORTAL_JWKS_URL` 없음)"},
        502: {"description": "포털의 공개 키를 받지 못함"},
    },
)
def portal_exchange(
    payload: PortalExchangeRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    """**포털 안에서 앱으로 바로 들어온 사람**(북마크 · 링크)의 자동 로그인 — Report Archive 와
    같은 길.

    화면이 포털 로그인 쿠키(`hwax_csrf`)를 보면, 포털의 표준 launch(`POST
    /systems/{id}/launch`, 타일이 쓰는 그것)를 포털 세션으로 불러 토큰을 받고 여기서 바꾼다.
    확인 · 계정 판정은 콜백과 **같고**(`portal.sign_in`), 응답은 비밀번호 로그인과 같다 —
    refresh 쿠키 + access. 거절은 사유(`details.reason`)를 달아 JSON 으로 — 화면이 콜백의
    `?sso=` 와 같은 말을 보인다.
    """
    settings = get_settings()
    if not portal.enabled(settings):
        raise NotFound(
            code("AUTH", 9),
            "포털 SSO 가 꺼져 있습니다 — 이 설치의 .env 에 PORTAL_JWKS_URL 이 없습니다.",
        )
    try:
        user, access, expires_in, refresh_raw = portal.sign_in(
            db, payload.token, request.headers.get("user-agent")
        )
    except portal.PortalLoginError as failed:
        _log_portal_refusal(failed)
        raise AppError(
            code("AUTH", 20),
            "HWAX 포털 로그인을 받지 못했습니다.",
            status=_EXCHANGE_STATUS.get(failed.reason, 403),
            details={"reason": failed.reason},
        ) from None
    request.scope[USER_ID_SCOPE_KEY] = user.id
    _set_refresh_cookie(request, response, refresh_raw)
    return LoginResponse(
        access_token=access, expires_in=expires_in, user=services.user_out(db, user)
    )


@router.get("/portal-callback", include_in_schema=False)
def portal_callback_get() -> None:
    """**GET 은 405.** 이 길은 포털의 숨은 폼이 POST 로 온다 — 주소창에 붙여 넣은 사람에게
    SPA 의 「없는 엔드포인트」(404)를 주면 포털의 진단표가 그것을 「SSO 꺼짐」 으로 읽는다."""
    raise AppError(
        code("AUTH", 10),
        "포털 로그인 콜백은 POST 로만 받습니다 — HWAX 포털의 타일이나 로그인 화면의 "
        "단추로 여세요.",
        status=405,
    )


@router.get("/portal-status", response_model=PortalStatusOut)
def portal_status(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> PortalStatusOut:
    """포털 로그인이 켜졌나 · 이 서버에서 포털의 공개 키가 닿나 · 게이트웨이 위임은 어떤가 —
    시스템 관리자만."""
    return portal.status(db, get_settings())


# --- HWAX 게이트웨이의 사람별 위임(ADR 0028) --------------------------------------

#: 거절은 사람이 읽을 한 줄(text/plain)이다 — 게이트웨이가 본문 앞 200자를 그 사람에게 그대로
#: 보인다. 꺼짐만 404 JSON.
_REFUSED: dict[int | str, dict[str, Any]] = {
    400: {"description": "누구의 토큰인지 모른다(이메일 머리 없음) · client 모양 — 한 줄"},
    401: {"description": "게이트웨이 비밀이 다르다 — 한 줄"},
    403: {"description": "승인된 계정 없음 · 대기 · 정지 · 삭제 · 허용 주소 밖 — 한 줄"},
    404: {"description": "위임 꺼짐(`HEAX_SSO_SECRET` 없음) — JSON"},
}


@router.post("/sso", response_model=DelegatedTokenOut, responses=_REFUSED)
def gateway_sso(request: Request, db: Session = Depends(get_db)) -> DelegatedTokenOut:
    """**HWAX 게이트웨이의 사람별 위임 발급** — 그 사람 명의의 개인 토큰(하루)을 내준다.

    게이트웨이가 앱 포트로 직접 부른다(본문 없음). 머리: `X-Heax-Gateway-Secret` ·
    `X-Hwax-Delegate-Email`(또는 `X-Heax-User-Email`) · `X-Heax-Client`. 같은 client 의 직전
    토큰은 회수한다. 처음 보는 사람을 만들지 않는다 — 계약과 까닭은 `delegation.py`.
    """
    return delegation.issue(db, request, get_settings())


@router.post("/sso/revoke", response_model=DelegationRevokeOut, responses=_REFUSED)
def gateway_sso_revoke(request: Request, db: Session = Depends(get_db)) -> DelegationRevokeOut:
    """그 사람의 그 client 위임 토큰을 끊는다(같은 머리). 모르는 사람이면 0."""
    return delegation.revoke(db, request, get_settings())


#: 점검은 이메일을 안 보고 계정을 안 찾는다 — 거절은 꺼짐 · 허용 밖 · 비밀뿐이다.
_VERIFY_REFUSED: dict[int | str, dict[str, Any]] = {
    401: _REFUSED[401],
    403: {"description": "허용 주소 밖 — 한 줄"},
    404: _REFUSED[404],
}


@router.post("/sso/verify", status_code=204, responses=_VERIFY_REFUSED)
def gateway_sso_verify(request: Request) -> None:
    """**연결 점검** — 게이트웨이가 위임 설정(주소 · 비밀)이 맞는지만 본다. 맞으면 본문 없는
    204.

    `X-Heax-Gateway-Secret` 만 본다(이메일 머리는 요구하지 않는다). 계정 · 토큰 · 감사를
    건드리지 않는다 — `/sso` 로 점검하면 토큰이 실제로 나가므로 따로 둔다."""
    delegation.verify(request, get_settings())


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    raw = request.cookies.get(get_settings().refresh_cookie_name)
    if raw:
        services.revoke_refresh(db, raw)
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserOut:
    return services.user_out(db, user)


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: ProfileUpdateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> UserOut:
    """자기 표시 이름을 바꾼다.

    아이디는 여기서 못 바꾼다 — 로그인 식별자라 본인이 바꾸면 기록이 가리키는
    대상이 흔들린다. 그것은 관리자의 일이다.
    """
    user.display_name = payload.display_name.strip()
    db.commit()
    db.refresh(user)
    return services.user_out(db, user)


@router.post("/change-password", status_code=204)
def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    services.change_password(db, user, payload.current_password, payload.new_password)
    # 모든 세션을 끊었으므로 이 브라우저의 쿠키도 함께 버린다.
    _clear_refresh_cookie(response)


# --- PAT — 연계 스크립트용 자격 증명 ------------------------------------------


@router.get("/token-scopes", response_model=TokenScopesOut)
def token_scopes(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> TokenScopesOut:
    """지금 발급할 수 있는 범위 — 꺼진 확장 모듈의 범위는 빠진다.

    **화면이 목록을 손으로 들지 않는다.** 도메인이 범위를 더하면 발급 화면은
    아무것도 안 고쳐도 새 범위를 보여 준다 — 손 목록은 반드시 서버와 어긋나고,
    어긋난 날 사람은 있는 범위를 못 고른다.
    """
    return TokenScopesOut(scopes=list(scopes.known_scopes(server_services.enabled_names(db))))


@router.post("/tokens", response_model=PatCreateResponse, status_code=201)
def create_token(
    payload: PatCreateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> PatCreateResponse:
    raw, pat = services.create_pat(
        db, user, payload.name, payload.expires_in_days, payload.scopes
    )
    return PatCreateResponse(token=raw, pat=pat)


@router.get("/tokens", response_model=list[PatOut])
def list_tokens(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[PatOut]:
    return services.list_pats(db, user)


@router.delete("/tokens/{pat_id}", status_code=204)
def revoke_token(
    pat_id: uuid.UUID,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    services.revoke_pat(db, user, pat_id)
