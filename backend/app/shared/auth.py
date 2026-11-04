"""인증 의존성 — 모든 모듈이 쓰는 current_user.

여기가 app/modules/auth 가 아니라 shared 에 있는 이유: 모든 모듈이 현재 사용자를
필요로 하는데, 그때마다 auth 모듈을 직접 import 하면 모든 모듈이 auth 에 묶인다.
의존성은 횡단 관심사이므로 shared 가 맞다. 방향은 shared -> auth 한 쪽이다.

**두 가지 자격을 같은 지점에서 받는다** — 사람의 access JWT 와 기계의 PAT.
연계 스크립트가 PAT 로 같은 엔드포인트를 부르므로, 권한 판정이 한 곳이어야
"사람은 되는데 스크립트는 안 되는" 어긋남이 안 생긴다.

경로별 범위 표는 여기 없다. `shared/scopes.py` 의 레지스트리에 도메인이 등록하고
여기서는 그것을 물어보기만 한다 — 공통 틀은 도메인 경로를 몰라야 한다.
"""

from __future__ import annotations

import logging

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.accounts.models import User
from app.modules.auth import delegation, security, services
from app.shared import scopes
from app.shared.access_log import ACTION_SCOPE_KEY, GATEWAY_API, USER_ID_SCOPE_KEY
from app.shared.errors import AppError, Forbidden, code
from app.shared.request_context import set_actor_client, set_actor_token

logger = logging.getLogger(__name__)

_UNAUTHENTICATED = "로그인이 필요합니다."


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header or not header.lower().startswith("bearer "):
        return None
    return header[7:].strip() or None


def scope_path(request: Request) -> str:
    """범위 표와 견줄 경로 — **접두어를 뺀다.**

    ⚠️ 이 한 줄이 없어서, 접두어 아래 설치(`/rootdesign`)에서는 **어떤 범위를 켜도 모든
       쓰기가 막혔다**(`APP-AUTH-0105` 「이 경로를 고칠 수 없습니다」 — 실측 운영 서버).
       `request.url.path` 에는 접두어가 붙어 있다(앞의 nginx 가 떼고 넘겨도 `PrefixMiddleware`
       가 다시 붙인다 — 정적 파일 마운트가 그것을 요구한다). 범위 표는 `/api/…` 로 적혀 있으니
       앞머리가 맞을 수가 없고, 못 찾은 것은 「모르는 경로」 로 막힌다. 범위 문제가 아닌데
       범위를 고치러 다니게 되는 자리다.

    ASGI 의 `root_path` 가 그 접두어다 — 라우팅도 같은 값을 떼고 길을 찾는다.

    ⚠️ **`request.url.path` 가 아니라 `scope["path"]` 다** — 라우팅이 보는 바로 그 값. `url` 은
       디코딩된 경로로 주소를 다시 지어 쪼개므로 `%3F`(?) · `%23`(#) 에서 잘린다: 읽기 토큰이
       `POST /api/metrics/plan%3F/recompute` 를 보내면 범위는 「읽기로 연 `/api/metrics/plan`」
       을 보고 통과시키는데, 라우팅은 `plan?` 을 지표 이름으로 받아 재계산(쓰기)으로 갔다
       (Starlette 1.6, 2026-10-08).
    """
    path = str(request.scope.get("path") or "/")
    root = request.scope.get("root_path") or ""
    if root and path.startswith(root):
        return path[len(root) :] or "/"
    return path


def _enforce_token_scope(request: Request, token_scopes: list[str], path: str) -> None:
    """기계 자격의 쓰기를 범위로 막는다.

    **사람 세션에는 안 건다** — 그 사람의 권한이 이미 한계다. 범위는 기계 자격에만
    있는 개념이고, 둘을 같은 축으로 섞으면 화면에서 되던 일이 이유 없이 막힌다.
    """
    if scopes.is_reading(request.method, path):
        if scopes.may_read(path, token_scopes):
            return
        raise Forbidden(
            code("AUTH", 104),
            f"이 토큰에는 이 경로를 읽을 범위가 없습니다({scopes.READ} 또는 그 자리의 "
            "좁은 범위).",
            details={"path": path, "granted": token_scopes},
        )

    needed = scopes.needed_scope(path)
    if needed is None:
        # **모르는 것은 막는다.** 새 엔드포인트가 생길 때마다 자동으로 열리면
        # 그것을 알아채는 사람이 아무도 없다.
        raise Forbidden(
            code("AUTH", 105),
            "개인 토큰으로는 이 경로를 고칠 수 없습니다. 화면에서 하세요.",
            details={"path": path},
        )
    if needed not in token_scopes:
        raise Forbidden(
            code("AUTH", 106),
            f"이 토큰에는 {needed} 범위가 없습니다.",
            details={"needed": needed, "granted": token_scopes},
        )


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _bearer(request)
    if token is None:
        raise AppError(code("AUTH", 100), _UNAUTHENTICATED, status=401)

    if token.startswith(security.accepted_pat_prefixes()):
        found = services.resolve_pat(db, token)
        if found is None:
            shown = token[: len(security.pat_prefix()) + 6]
            logger.warning("PAT 인증 실패 (prefix=%s)", shown)
            raise AppError(code("AUTH", 101), "토큰이 유효하지 않습니다.", status=401)
        user, pat = found
        # 접근 로그 미들웨어가 "누가" 를 알 수 있게 scope 에 남긴다. 미들웨어는
        # 인증보다 바깥에 있어서 스스로는 사용자를 알 수 없다. **범위 검사보다 먼저** — 범위에
        # 막힌 쓰기야말로 「누가 무엇을 하려 했나」 를 물을 줄이다.
        request.scope[USER_ID_SCOPE_KEY] = user.id
        if pat.delegated_client:
            # **HWAX 게이트웨이가 위임 발급으로 받아 간 토큰**(ADR 0028) — 그 사람 명의로
            # Claude 가 게이트웨이를 거쳐 한 일이다. 범위에 막힌 요청도 그 통로로 남게 먼저
            # 적는다. 통로는 서버가 정한다(머리의 X-Client 는 믿지 않는다).
            set_actor_client(delegation.CLIENT)
            request.scope[ACTION_SCOPE_KEY] = GATEWAY_API
        _enforce_token_scope(request, list(pat.scopes or []), scope_path(request))
        # 한 요청이 **범위 둘**을 요구할 때(정의가 든 묶음) 라우트가 더 물을 수 있게 남긴다.
        # 사람 세션에는 없다 — 범위는 기계 자격에만 있는 개념이다.
        request.state.token_scopes = list(pat.scopes or [])
        # **감사에 토큰 이름을 남긴다.** 소유자만 남기면 사람이 넣은 것과 기계가
        # 넣은 것이 구별되지 않는다.
        set_actor_token(pat.name)
        return user

    payload = security.decode_access_token(token)
    if payload is None:
        # 사유(만료·서명 불일치)는 응답에 싣지 않는다 — 공격자에게 힌트가 된다.
        raise AppError(code("AUTH", 102), "세션이 만료되었습니다.", status=401)

    signed_in = db.get(User, payload["sub"])
    if signed_in is None:
        raise Forbidden(code("AUTH", 2), "삭제된 계정입니다. 관리자에게 문의하세요.")
    services.ensure_can_sign_in(signed_in)
    if payload.get("sep", 0) != signed_in.session_epoch:
        # **끊은 뒤에 쓰인 옛 로그인**(비밀번호 변경 · 초기화 · 정지 전에 받은 토큰)이다 —
        # 만료와 같은 말로 답한다(다시 로그인하면 된다).
        raise AppError(code("AUTH", 102), "세션이 만료되었습니다.", status=401)
    request.scope[USER_ID_SCOPE_KEY] = signed_in.id
    return signed_in


def require_system_admin(user: User = Depends(current_user)) -> User:
    if not user.is_system_admin:
        raise Forbidden(code("AUTH", 103), "권한이 없습니다.")
    return user
