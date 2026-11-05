"""오류 규약 — 구조화 코드 + 반드시 로그를 남긴다.

**오류를 만드는 경로가 곧 로그를 남기는 경로다.** 핸들러를 거치지 않고 오류 응답을
만들 방법을 두지 않는다 — except Exception 이 불투명한 4xx 로 치환되면 원인이
로그에 안 남고, 그때 남는 것은 "안 된다" 뿐이다.

코드 형식: <PREFIX>-<MODULE>-<NNNN>  예) APP-ACCOUNTS-0001

접두사는 `app/branding.py` 의 ERROR_PREFIX 하나다. 포크한 플랫폼이 그것을 바꾸면
`code()` 를 거치는 모든 코드가 함께 바뀐다 — 문자열을 손으로 잇지 않는 이유다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.branding import ERROR_PREFIX
from app.shared.request_context import HEADER as REQUEST_ID_HEADER
from app.shared.request_context import get_request_id

logger = logging.getLogger(__name__)


def code(module: str, number: int) -> str:
    """APP-AUTH-0001 을 만든다.

    **문자열을 손으로 잇지 않는다.** 접두사를 바꾸는 날 한 자리만 안 바뀌면 그
    오류는 로그 검색에서 영영 빠지고, 빠졌다는 사실은 아무 데도 안 뜬다.
    """
    return f"{ERROR_PREFIX}-{module.upper()}-{number:04d}"


#: 스키마 검증 실패 종류 -> 사람이 읽는 말.
#: pydantic 의 영어 메시지를 그대로 내보내면 화면에서 아무도 안 읽는다.
_VALIDATION_MESSAGES = {
    "missing": "값이 빠졌습니다",
    "string_too_short": "값이 필요합니다",
    "string_too_long": "너무 깁니다",
    "string_pattern_mismatch": "쓸 수 없는 문자가 있습니다",
    "int_parsing": "정수여야 합니다",
    "float_parsing": "숫자여야 합니다",
    "bool_parsing": "예/아니오 값이어야 합니다",
    "value_error": "값이 올바르지 않습니다",
    "greater_than_equal": "너무 작습니다",
    "too_long": "너무 많습니다",
    "too_short": "개수가 모자랍니다",
    "less_than_equal": "너무 큽니다",
    # 모르는 칸을 거절하는 본문(`extra="forbid"`) — 옛 칸 · 오타를 말없이 버리면 보낸 쪽은
    # 저장된 줄 안다.
    "extra_forbidden": "모르는 칸입니다",
}

#: 한 번에 보여 줄 항목 수. 다 늘어놓으면 첫 줄부터 안 읽는다.
_MAX_VALIDATION_ITEMS = 3


def describe_validation(errors: Sequence[Any]) -> str:
    """**어느 칸이 왜 틀렸는지 말한다.**

    "요청 형식이 올바르지 않습니다" 만 내보내면 사용자는 무엇을 고쳐야 할지 알
    수 없다. 자세한 내용은 details 에도 있지만 **화면이 보여 주는 것은 message
    하나**다.
    """
    if not errors:
        return "요청 형식이 올바르지 않습니다."

    parts: list[str] = []
    for error in errors[:_MAX_VALIDATION_ITEMS]:
        location = [
            str(item) for item in error.get("loc", ()) if item not in ("body", "query")
        ]
        where = ""
        for index, item in enumerate(location):
            where += f"[{item}]" if item.isdigit() else (f".{item}" if index else item)

        kind = str(error.get("type"))
        message = str(error.get("msg", ""))
        # 직접 쓴 검증기가 붙인 말이 있으면 **그것이 낫다.** pydantic 은 그 말
        # 앞에 접두사를 붙이는데, 우리가 적은 한국어는 그 뒤에 있다.
        reason = (
            message.removeprefix("Value error, ")
            if kind == "value_error" and message.startswith("Value error, ")
            else _VALIDATION_MESSAGES.get(kind, message)
        )
        context = error.get("ctx") or {}
        pattern = context.get("pattern")
        if pattern:
            reason = f"{reason} (허용: {pattern})"
        # **한도를 말해 주지 않으면 고칠 수 없다.** "너무 많습니다" 만 보고 몇
        # 개까지인지 알아내려면 코드를 읽어야 한다.
        limit = context.get("max_length") or context.get("actual_length")
        if limit is not None and kind in ("too_long", "too_short"):
            reason = f"{reason} (최대 {limit}개)"
        label = where or "요청"
        parts.append(f"{label} — {reason}")

    more = len(errors) - len(parts)
    summary = " / ".join(parts)
    return f"{summary} 외 {more}건" if more > 0 else summary


def _plain(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """검증 오류를 JSON 으로 낼 수 있는 형태로 바꾼다.

    pydantic 은 ValueError 를 그대로 ctx 에 담는데, 그것을 응답 본문에 실으려다
    직렬화에서 터진다 — 422 를 내려던 자리에서 500 이 나가고, 사람은 자기가 뭘
    잘못 적었는지 알 수 없다.
    """
    out: list[dict[str, Any]] = []
    for error in errors:
        item = dict(error)
        context = item.get("ctx")
        if isinstance(context, dict):
            item["ctx"] = {key: str(value) for key, value in context.items()}
        # input 에는 무엇이든 올 수 있다 — 파일 객체까지.
        if "input" in item:
            try:
                json.dumps(item["input"])
            except (TypeError, ValueError):
                item["input"] = str(item["input"])
        out.append(item)
    return out


class AppError(Exception):
    """도메인 오류. status 는 HTTP 상태, code 는 사람이 검색할 수 있는 식별자."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


class NotFound(AppError):
    def __init__(self, code: str, message: str, **kw: Any) -> None:
        super().__init__(code, message, status=404, **kw)


class Conflict(AppError):
    def __init__(self, code: str, message: str, **kw: Any) -> None:
        super().__init__(code, message, status=409, **kw)


class Forbidden(AppError):
    def __init__(self, code: str, message: str, **kw: Any) -> None:
        super().__init__(code, message, status=403, **kw)


class PlainTextError(AppError):
    """본문이 **사람이 읽을 한 줄**(text/plain)인 오류 — 끝에 오류 코드를 괄호로 단다.

    받는 쪽이 본문을 **그대로 사람에게 보이는** 서버 간 계약에서만 쓴다. HWAX 게이트웨이는 위임
    발급(`POST /api/auth/sso`)이 200 이 아니면 본문 앞 200자를 그 사람의 Claude 에 그대로
    띄우는데, 봉투(JSON)를 주면 그 200자의 앞 마흔 자가 `{"error":{"code":…` 로 차고 정작 할
    일은 잘린다. 로그는 다른 AppError 와 **같은 처리기**가 남긴다 — 응답을 만드는 경로가 곧
    로그를 남기는 경로다.
    """


def _body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": get_request_id(),
            "details": details or {},
        }
    }


#: 프레임워크가 내는 오류의 우리말. **영어 한 줄은 사람에게 아무것도 안 알려 준다.**
#:
#: 405 가 여기 있는 이유가 특히 중요하다. SPA 폴백이 모든 경로를 GET 으로 잡고
#: 있어서, /api 아래에 없는 주소를 POST·PATCH·DELETE 로 부르면 **404 가 아니라
#: 405 가 나온다.** 이 문구를 보는 사람은 대개 "서버에 그 기능이 아직 없다" 를
#: 겪는 중이다 — 그것을 말해 준다.
_HTTP_MESSAGES = {
    405: "이 주소는 그 방식의 요청을 받지 않습니다. 서버가 옛 버전일 수 있습니다.",
}


#: 이 말이 이름에 들어간 칸의 값은 오류 로그 · 응답에서 가린다.
_SECRET_NAMES = ("password", "secret", "token", "authorization", "api_key")


def _secret(name: Any) -> bool:
    lowered = str(name).lower()
    return any(word in lowered for word in _SECRET_NAMES)


def _masked(errors: list[Any]) -> list[Any]:
    """검증 오류의 `input` 에서 비밀을 가린다 — 칸 자체가 비밀이거나(`loc` 끝), 본문 통째가
    오면 그 안의 비밀 칸을."""
    out: list[Any] = []
    for one in errors:
        if not isinstance(one, dict) or "input" not in one:
            out.append(one)
            continue
        item = dict(one)
        loc = item.get("loc") or ()
        if loc and _secret(loc[-1]):
            item["input"] = "***"
        elif isinstance(item["input"], dict):
            item["input"] = {
                key: "***" if _secret(key) else value for key, value in item["input"].items()
            }
        out.append(item)
    return out


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> Response:
        # 4xx 는 정상 흐름의 일부라 warning, 5xx 는 error. 어느 쪽이든 남긴다.
        log = logger.warning if exc.status < 500 else logger.error
        log(
            "%s %s -> %s %s: %s",
            request.method,
            request.url.path,
            exc.status,
            exc.code,
            exc.message,
            extra={"details": exc.details},
        )
        if isinstance(exc, PlainTextError):
            return PlainTextResponse(f"{exc.message} ({exc.code})", status_code=exc.status)
        return JSONResponse(
            status_code=exc.status, content=_body(exc.code, exc.message, exc.details)
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # **보낸 값을 그대로 남기지 않는다** — 비밀번호 · 비밀 칸이 틀린 모양으로 오면 그 값이
        # 로그와 응답에 평문으로 실렸다(2026-10-08). 비밀스러운 이름이 붙은 값은 가린다.
        errors = _masked(_plain(exc.errors()))
        logger.warning("%s %s -> 422 validation: %s", request.method, request.url.path, errors)
        return JSONResponse(
            status_code=422,
            content=_body(
                code("COMMON", 422), describe_validation(errors), {"errors": errors}
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """프레임워크가 내는 오류도 **같은 봉투에 담는다.**

        프론트의 오류 파서는 봉투를 기대해 error.message 를 읽는다. 맨 detail 이
        그대로 나가면 **오류를 읽다가 오류가 나서**, 화면에는 원인 대신 자바스크립트
        예외가 뜬다 — 그러면 사람은 요청이 왜 실패했는지가 아니라 프론트가 깨졌다고
        읽는다.

        raise HTTPException 은 이 저장소 코드에 한 줄도 없다. 여기 오는 것은 전부
        프레임워크가 낸 것 — 405, 라우팅 단계의 404 같은 것들이다.

        **헤더는 그대로 넘긴다.** 405 는 Allow 를 달고 오는데, 그것을 떨어뜨리면
        어떤 메서드가 되는지 알 방법이 없어진다.
        """
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail else ""
        message = _HTTP_MESSAGES.get(exc.status_code) or detail or "요청을 처리할 수 없습니다."
        log = logger.warning if exc.status_code < 500 else logger.error
        log(
            "%s %s -> %s http: %s",
            request.method,
            request.url.path,
            exc.status_code,
            message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(code("COMMON", exc.status_code), message),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # 스택 트레이스를 반드시 남긴다. 사용자에게는 request_id 만 주고, 그 id 로
        # 로그에서 원본을 찾는다.
        logger.exception("%s %s -> 500 unhandled", request.method, request.url.path)
        # 이 응답은 요청 ID 미들웨어 바깥에서 나가 그 헤더가 안 붙는다 — 여기서 단다.
        return JSONResponse(
            status_code=500,
            content=_body(
                code("COMMON", 500),
                "서버 오류가 발생했습니다. 요청 ID를 관리자에게 알려주세요.",
            ),
            headers={REQUEST_ID_HEADER: get_request_id()},
        )

    @app.exception_handler(OperationalError)
    async def _db_operational(request: Request, exc: OperationalError) -> JSONResponse:
        """**시간 상한으로 끊긴 질의**(`shared/querycap`, ADR 0031)는 사람이 할 일을 말한다.

        그대로 두면 500 「서버 오류」 라 사람은 고장으로 읽고 다시 누른다 — 같은 무거운 질의가
        또 돈다. 504 인 까닭은 `querycap` 머리에(503 이면 이중화의 nginx 가 다른 서버에 다시
        보낸다). 그 밖의 DB 오류는 지금까지처럼 500 이다."""
        from starlette.concurrency import run_in_threadpool

        from app.config import get_settings
        from app.shared import cut_questions
        from app.shared.querycap import cancelled, temp_exceeded

        if temp_exceeded(exc):
            # 임시 파일 상한(`READ_TEMP_LIMIT`, ADR 0048) — 디스크를 채우기 전에 끊었다. 503 이
            # 아닌 까닭은 504 와 같다.
            limit = get_settings().read_temp_limit
            logger.warning(
                "%s %s -> 507 임시 파일 상한(%s)", request.method, request.url.path, limit
            )
            return JSONResponse(
                status_code=507,
                content=_body(
                    code("COMMON", 507),
                    f"이 물음이 서버의 임시 공간 상한({limit})을 넘어 멈췄습니다 — 디스크를 "
                    "지키려고 끊은 것이고 바뀐 것은 없습니다. 범위를 좁혀 다시 조회하세요.",
                    {"temp_limit": limit},
                ),
                headers={REQUEST_ID_HEADER: get_request_id()},
            )
        if not cancelled(exc):
            return await _unhandled(request, exc)
        seconds = get_settings().read_query_seconds
        logger.warning(
            "%s %s -> 504 질의 시간 상한(%ss)", request.method, request.url.path, seconds
        )
        # 끊긴 질문으로 남긴다 — 어느 경로가 상한에 걸리는지 실제 물음으로 본다(ADR 0031).
        await run_in_threadpool(
            cut_questions.note,
            request,
            "timeout",
            code("COMMON", 504),
            {},
        )
        return JSONResponse(
            status_code=504,
            content=_body(
                code("COMMON", 504),
                f"질의가 {seconds:g}초를 넘겨 멈췄습니다 — 서버를 붙잡지 않게 끊은 것이고 "
                "바뀐 것은 없습니다. 기간 · 조건으로 범위를 좁혀 다시 조회하세요.",
                {"seconds": seconds},
            ),
            headers={REQUEST_ID_HEADER: get_request_id()},
        )
