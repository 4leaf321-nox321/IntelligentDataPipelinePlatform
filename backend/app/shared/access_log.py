"""접근 로그 미들웨어 — 그리고 **요청마다 걸린 시간 · 응답 크기**(ADR 0026).

**접근 로그는 모든 API 요청을 남기지 않는다.** 알림 배지 폴링까지 적으면 표가 의미 없는 행으로
차서 정작 찾을 것을 못 찾는다. 상태를 바꾸는 요청과 로그인만 남긴다 — 사용자 지원에 필요한
것은 "무엇을 했는가" 이지 "무엇을 봤는가" 가 아니다.

**요청 시간은 조회까지 전부 남긴다**(`request_timings`, 폴링 · 헬스체크만 뺀다). 작업 목록 한
번이 93MB · 4.4초였던 것이 조회였고, 사람이 「화면이 느리다」 고 말하고 나서야 알았다
(2026-10-09). 잰 값은 모아서 넣는다(`shared/request_timing.py`) — 요청마다 커밋하면 그 왕복이
이벤트 루프를 막는다.

기록은 요청 처리와 별개 세션에서 한다. 로그를 남기다 실패해도 사용자의 요청은
성공해야 한다.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from starlette.routing import replace_params
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.database import SessionLocal
from app.modules.audit.models import AccessLog
from app.shared.request_context import get_request_id
from app.shared.request_timing import writer as timing_writer

logger = logging.getLogger(__name__)

#: 인증 의존성이 scope 에 남기는 키. 미들웨어는 인증보다 **바깥**에 있어서
#: 스스로는 누가 불렀는지 알 수 없다.
USER_ID_SCOPE_KEY = "app_user_id"

#: 경로가 아니라 **자격이** 접근 로그의 종류를 정할 때 인증이 scope 에 남기는 키 — HWAX
#: 게이트웨이의 위임 토큰으로 든 요청은 `GATEWAY_API` 다(ADR 0028). 접근 로그에는 통로 칸이
#: 없어 이것이 없으면 「사람이 화면에서 한 것」 과 「사람 명의로 Claude 가 게이트웨이로 한 것」
#: 이 같은 `API` 줄이 된다.
ACTION_SCOPE_KEY = "app_access_action"

#: 게이트웨이 위임 발급 · 회수 · 점검(`/api/auth/sso` · `/revoke` · `/verify`) — 거절도 이
#: 이름으로 남는다(상태 코드와 함께). 서버 화면의 「마지막으로 거절한 때」 가 이것을 읽는다.
GATEWAY_SSO = "GATEWAY_SSO"
#: 게이트웨이의 위임 토큰으로 든 요청(`ACTION_SCOPE_KEY`).
GATEWAY_API = "GATEWAY_API"

#: 남길 메서드. GET 은 기본적으로 안 남긴다(조회는 양이 많고 가치가 낮다).
_RECORDED_METHODS = {"POST", "PATCH", "PUT", "DELETE"}

#: 남기지 않을 경로. 폴링과 헬스체크가 대부분이다 — **요청 시간도 이 경로는 안 남긴다.**
#:
#: **도메인이 폴링 경로를 더하면 여기에도 더한다.** 안 더하면 그 한 줄이 표를
#: 가득 채우고, 그때 접근 로그는 조회할 수 없는 크기가 된다.
_SKIP = ("/api/health", "/api/notifications/unread-count")

#: 경로에 id 가 든 폴링 경로 — **맞은 라우트의 틀**로 견준다
#: (`/api/ext/x/meetings/{meeting_id}/pulse`). `_SKIP` 의 글자 그대로는 회의마다 다른 주소를
#: 못 맞춘다. 도메인 · 확장이 등록한다(`register_polling_route`) — 공통이 확장의 경로를 적으면
#: 방향이 거꾸로 선다.
_SKIP_ROUTES: set[str] = set()


def register_polling_route(template: str) -> None:
    """폴링 경로(틀 — `{…}` 그대로)를 접근 로그 · 요청 시간 기록에서 뺀다. 같은 것을 두 번
    넣어도 된다(`create_app()` 은 시험에서 여러 번 불린다)."""
    _SKIP_ROUTES.add(template)


def _skipped(scope: Scope, path: str) -> bool:
    if path in _SKIP:
        return True
    # 틀은 라우터가 맞춘 뒤에야 안다 — 요청을 처리한 다음에 묻는다.
    return bool(_SKIP_ROUTES) and route_template(scope, path) in _SKIP_ROUTES


#: 맞는 라우트가 없던 요청의 경로 틀. **원 경로를 그대로 묶음 열쇠로 쓰지 않는다** — 아무
#: 주소나 두드리는 요청 하나하나가 묶음을 늘리면 집계 화면이 그것으로 찬다.
UNMATCHED_ROUTE = "(없는 경로)"


def _action(path: str) -> str:
    if path.endswith("/auth/login"):
        return "LOGIN"
    if path.endswith("/auth/logout"):
        return "LOGOUT"
    # **포털 로그인은 따로 적는다**(ADR 0028) — 비밀번호 로그인과 한 이름이면 「이 사람이 어느
    # 길로 들어왔나」 가 안 갈린다. 성공한 줄에만 사람이 붙는다(실패 · 승인 대기는 사람 칸이
    # 빈다 — 둘 다 303 이라 상태 코드로는 안 갈린다).
    if path.endswith(("/auth/portal-callback", "/auth/portal-exchange")):
        return "PORTAL_LOGIN"
    # **게이트웨이 위임 발급 · 회수**(ADR 0028) — 거절도 이 이름으로 남아(상태 코드와 함께)
    # 서버 화면의 「마지막으로 거절한 때」 가 이 줄을 읽는다. 워커 · 서버가 여럿이라 메모리로는
    # 못 본다.
    if path.endswith(("/auth/sso", "/auth/sso/revoke", "/auth/sso/verify")):
        return GATEWAY_SSO
    return "API"


def route_template(scope: Scope, path: str) -> str:
    """이 요청이 맞은 **경로 틀** — `/api/objects/{type_slug}/{object_id}`.

    라우터가 맞춘 라우트를 `scope["route"]` 에 남기는데, 그 `path_format` 은 **자기 라우터
    안의 경로**다(`/objects/{type_slug}/{object_id}` — 바깥 `/api` 접두어가 없다). 그래서 실제
    경로에서 그 라우트가 맞춘 꼬리를 떼어 앞을 붙인다: 꼬리는 틀에 값을 채워 다시 만든다.
    """
    route = scope.get("route")
    fmt = getattr(route, "path_format", None)
    if not isinstance(fmt, str):
        return UNMATCHED_ROUTE
    tail = ""
    try:
        convertors = dict(getattr(route, "param_convertors", {}))
        tail, _ = replace_params(fmt, convertors, dict(scope.get("path_params") or {}))
    except Exception:  # 값을 틀에 다시 못 끼우면 — 접두어 없이라도 묶는다
        tail = ""
    full = path[: len(path) - len(tail)] + fmt if tail and path.endswith(tail) else fmt
    # SPA 의 받아내기(`/{full_path:path}`)가 맞춘 `/api/…` 는 없는 엔드포인트다(main.py).
    if not full.startswith("/api/"):
        return UNMATCHED_ROUTE
    return full[:300]


class _Response:
    """보내는 응답을 지켜본 것 — 상태 · 종류 · 보낸 바이트 · 끝난 때."""

    __slots__ = ("bytes", "content_type", "ended", "length", "status")

    def __init__(self) -> None:
        self.status = 0
        self.content_type: str | None = None
        self.length = 0
        """머리가 말한 길이 — 본문을 이 미들웨어가 안 보는 길(`pathsend`)에서만 쓴다."""
        self.bytes = 0
        self.ended = 0.0


def _content_type(headers: Any) -> str | None:
    for key, value in headers or ():
        if bytes(key).lower() == b"content-type":
            kind = bytes(value).decode("latin-1").split(";", 1)[0].strip().lower()
            return kind[:100] or None
    return None


def _content_length(headers: Any) -> int:
    for key, value in headers or ():
        if bytes(key).lower() == b"content-length":
            try:
                return int(bytes(value))
            except ValueError:
                return 0
    return 0


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path", ""))
        # **접두어를 떼고 본다** — 접두어 설치(`PUBLIC_PATH=/<slug>`)에서는 바깥의
        # `PrefixMiddleware` 가 경로에 접두어를 붙여 넘긴다. `/api/` 로 시작하는지만 보던 때는
        # 그런 설치에서 로그인 · 로그아웃 · 고치기가 한 줄도 안 남았다(2026-10-08).
        base = get_settings().base_path
        if base and (path == base or path.startswith(base + "/")):
            path = path[len(base) :] or "/"
        method = str(scope.get("method", ""))
        timed = path.startswith("/api/") and method != "OPTIONS"
        received_at = datetime.now(UTC)
        started = time.perf_counter()
        seen = _Response()

        async def send_wrapper(message: Message) -> None:
            kind = message["type"]
            if kind == "http.response.start":
                seen.status = int(message["status"])
                seen.content_type = _content_type(message.get("headers"))
                seen.length = _content_length(message.get("headers"))
            elif kind == "http.response.body":
                seen.bytes += len(message.get("body", b""))
            elif kind == "http.response.pathsend":
                # 파일을 서버가 직접 보내는 길(지원하는 서버에서만) — 크기는 머리의 길이다.
                seen.bytes = seen.length
            await send(message)
            # **마지막 바이트를 보낸 뒤에** 잰다 — 첫 바이트까지만 재면 93MB 짜리 응답도 빠른
            # 요청으로 보인다. 뒤따르는 백그라운드 일은 응답 시간이 아니다(여기서 끊는다).
            if kind == "http.response.pathsend" or (
                kind == "http.response.body" and not message.get("more_body", False)
            ):
                seen.ended = time.perf_counter()

        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException:
            # 처리 못 한 예외의 500 은 바깥(ServerErrorMiddleware)이 만든다 — 느려서 터진
            # 요청이 가장 보고 싶은 줄이라 여기서도 남긴다.
            if timed and not _skipped(scope, path):
                self._time(scope, path, method, received_at, started, seen, failed=True)
            raise
        quiet = _skipped(scope, path)
        if timed and not quiet:
            self._time(scope, path, method, received_at, started, seen, failed=False)

        if not path.startswith("/api/") or quiet:
            return
        if method not in _RECORDED_METHODS:
            return

        try:
            headers = dict(scope.get("headers") or {})
            client = scope.get("client")
            # 세션 공장을 app.state 에서 가져온다. SessionLocal 을 직접 부르면 설정과
            # 무관하게 늘 같은 DB 를 보게 되어, 테스트가 자기 DB 를 쓰지 못한다.
            factory = getattr(scope["app"].state, "session_factory", SessionLocal)
            db = factory()
            try:
                db.add(
                    AccessLog(
                        user_id=scope.get(USER_ID_SCOPE_KEY),
                        action=str(scope.get(ACTION_SCOPE_KEY) or _action(path))[:30],
                        path=path[:300],
                        method=method,
                        status_code=seen.status,
                        request_id=get_request_id(),
                        client_ip=client[0] if client else None,
                        user_agent=(headers.get(b"user-agent") or b"").decode()[:300] or None,
                    )
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            # 로그를 남기다 실패해도 사용자의 요청은 이미 끝났다. 삼키되 남긴다.
            logger.exception("접근 로그 기록 실패 (%s %s)", method, path)

    @staticmethod
    def _time(
        scope: Scope,
        path: str,
        method: str,
        received_at: datetime,
        started: float,
        seen: _Response,
        *,
        failed: bool,
    ) -> None:
        """잰 것을 맡긴다 — **DB 는 여기서 안 만진다**(`shared/request_timing.py`)."""
        try:
            ended = seen.ended or time.perf_counter()
            status = seen.status or (500 if failed else 0)
            factory = getattr(scope["app"].state, "session_factory", SessionLocal)
            timing_writer.add(
                factory,
                {
                    "created_at": received_at,
                    "method": method[:10],
                    "route": route_template(scope, path),
                    "path": path[:300],
                    "status_code": status,
                    "duration_ms": max(0, round((ended - started) * 1000)),
                    "response_bytes": seen.bytes,
                    "content_type": seen.content_type,
                    "user_id": scope.get(USER_ID_SCOPE_KEY),
                    "request_id": get_request_id(),
                },
            )
        except Exception:
            logger.exception("요청 시간 기록 실패 (%s %s)", method, path)
