"""HWAX 게이트웨이의 사람별 위임 — 게이트웨이가 그 사람 명의의 개인 토큰을 받아 간다(ADR 0028).

## 흐름

사람의 Claude 는 포털의 MCP 게이트웨이(`https://<포털>/mcp-gw/mcp`, 포털 개인 토큰)에 붙는다.
그 사람이 우리 도구를 부르면 게이트웨이가 **앱 포트로 직접**(포털 nginx 를 안 거치고) 여기를
부른다 — 본문 없이 머리만(Report Archive 의 `POST /api/auth/sso` 와 같은 계약):

    X-Heax-Gateway-Secret   이 설치만의 공유 비밀(`HEAX_SSO_SECRET`) — 상수 시간으로 견준다
    X-Hwax-Delegate-Email   그 사람(게이트웨이가 확인한 신원) — 없으면 아래
    X-Heax-User-Email       같은 값(옛 계약). 둘 다 없으면 400
    X-Heax-User-Name        퍼센트 인코딩 이름 — 계정을 만들지 않으므로 쓰지 않는다
    X-Heax-Client           `gateway` · `hwax-portal`(포털이 직접) — 토큰 이름에 든다

여기서 그 사람의 **개인 토큰**(PAT)을 내준다 — 사람이 화면에서 만드는 것과 같은 자격이라 범위 ·
권한 · 감사가 그대로 걸린다. 게이트웨이는 그 토큰으로 우리 MCP 를 부르고, MCP 는 그것을
백엔드에 그대로 넘긴다. 토큰이 401 을 받으면 게이트웨이가 한 번 다시 받는다(MCP 의 401 문구,
`server.py`).

## 응답 — 게이트웨이가 읽는 대로

200 은 `{access_token, token_type, expires_in}`(봉투 없이 — 게이트웨이는 토큰과 **같은 객체의**
`expires_in` 을 읽는다). **200 이 아니면 게이트웨이는 본문 앞 200자를 그 사람에게 그대로 보이고
거절한다**(서비스 계정으로 내려가지 않는다) — 그래서 거절 본문은 사람이 읽을 한국어 한 줄이다
(`PlainTextError`). 꺼짐만 404 JSON 이다(포털 콜백 · RA 와 같은 숫자 — 진단표가 「꺼짐」 으로
읽는다).

처음 보는 사람을 만들지 않는다(JIT 없음) — 포털 로그인(타일 · 「HWAX 포털로 로그인」)이 가입
신청을 만들고 관리자가 승인하는 문이 이미 있다. 거절 본문이 그 길을 말한다.

## 창구 셋 — 같은 머리 · 같은 거절

`/sso`(발급, 200 토큰) · `/sso/revoke`(회수, 200 `{revoked}`) · `/sso/verify`(연결 점검,
204). 셋 다 꺼짐 404 JSON → 허용 밖 403 → 비밀 401 순서로 보고(`_guard`), 발급 · 회수는 그
뒤에 이메일(없으면 400)을 본다. 점검은 이메일을 안 본다 — 게이트웨이가 「이 앱과 연결 설정이
맞나」 만 묻는 자리라, `/sso` 로 점검하면 토큰이 실제로 나간다(ComponentCore · SimEngBay 와
같은 계약).

## 토큰

이름 `HWAX 게이트웨이(<client>)` · 수명 하루(게이트웨이는 최대 12시간 쓴다) · 범위는 `read` +
등록된 쓰기 범위 전부(그 사람이 화면에서 받을 수 있는 최대 — 계정 · 서버 관리는 어느 범위로도
안 열린다). 발급하면 **같은 client 의 직전 토큰을 회수**하고, 그 전에 이미 죽은 위임 토큰 줄은
지운다 — 게이트웨이가 12시간마다 다시 받으므로 안 지우면 「내 정보」 의 토큰 목록이 하루 두
줄씩 는다. 무엇을 언제 내줬나는 감사(`auth.gateway_delegated`)에 남는다(감사는 토큰을 id 가
아니라 이름으로 적는다).
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.modules.accounts.models import User
from app.modules.audit.models import AccessLog
from app.modules.auth import security
from app.modules.auth.models import PersonalAccessToken
from app.modules.auth.schemas import (
    DelegatedTokenOut,
    DelegationRevokeOut,
    GatewayDelegationOut,
    GatewayRefusalOut,
)
from app.modules.auth.services import normalize_login_id
from app.modules.server import services as server_services
from app.shared import audit, scopes
from app.shared.access_log import GATEWAY_SSO, USER_ID_SCOPE_KEY
from app.shared.errors import AppError, NotFound, PlainTextError, code
from app.shared.request_context import set_actor_client

logger = logging.getLogger(__name__)

#: 감사 · 접근 로그의 통로 — 위임 발급 요청과, **위임 토큰으로 든 요청**
#: 모두(`shared/auth.py`).
CLIENT = "hwax-gateway"
#: `X-Heax-Client` 가 없을 때 — 게이트웨이의 일반 위임(`PER_USER_SSO_APPS`)이 보내는 값과 같다.
DEFAULT_CLIENT = "gateway"
#: 위임 토큰의 수명. 게이트웨이는 `min(12시간, expires_in - 2분)` 들고 있다 — 수명이 그보다
#: 길어야 캐시가 산다(600초 이하면 게이트웨이가 경고하고, 짧을수록 다시 받기 · 회수가 잦다).
TOKEN_LIFETIME = timedelta(days=1)
#: 게이트웨이 `<접두>_SSO_URL` 의 길 — 앱 포트로 직접이라 접두어(`PUBLIC_PATH`)가 안 붙는다.
SSO_PATH = "/api/auth/sso"
#: 비밀의 권장 하한 — `openssl rand -hex 32` 는 64자다.
SECRET_MIN_CHARS = 32

SECRET_HEADER = "x-heax-gateway-secret"
EMAIL_HEADER = "x-heax-user-email"
DELEGATE_HEADER = "x-hwax-delegate-email"
CLIENT_HEADER = "x-heax-client"
#: 원격 주소를 서버(uvicorn `proxy_headers`)가 바꿔 적게 하는 머리 — 허용 IP 를 볼 때는 받지
#: 않는다.
_PROXY_HEADERS = ("x-forwarded-for", "forwarded")

#: client 는 토큰 이름에 들어간다 — 이름 칸(100자)에 맞고 눈으로 읽히는 모양만.
_CLIENT_SHAPE = re.compile(r"[A-Za-z0-9._-]{1,40}")

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def enabled(settings: Settings) -> bool:
    """위임이 켜져 있나 — `HEAX_SSO_SECRET` 이 있으면."""
    return bool(settings.heax_sso_secret.strip())


def token_name(client: str) -> str:
    """위임 토큰의 이름 — 「내 정보」 의 토큰 목록과 감사(`actor_token`)에 이대로 보인다."""
    return f"HWAX 게이트웨이({client})"


def granted_scopes(db: Session) -> list[str]:
    """위임 토큰의 범위 — `read` + 등록된 쓰기 범위 전부(꺼진 확장의 것은 빼고).

    관리 범위는 주지 않는다 — 지금 그런 범위는 없고(계정 · 부서 · 서버 관리는 어느 범위로도 안
    열린다, `shared/auth.py` 의 「모르는 것은 막는다」), 누가 `…:admin` 같은 것을 등록하는
    날에도 게이트웨이 너머의 AI 에게 저절로 가지 않게 이름으로 거른다."""
    live = scopes.write_scopes(server_services.enabled_names(db))
    return [scopes.READ, *(one for one in live if not _admin_like(one))]


def _admin_like(scope: str) -> bool:
    return scope == "admin" or scope.endswith(":admin")


def allowed_networks(settings: Settings) -> tuple[list[Network], list[str]]:
    """(읽은 허용 대역, 못 읽은 값). 둘 다 비면 주소를 보지 않는다."""
    networks: list[Network] = []
    invalid: list[str] = []
    for raw in settings.heax_sso_allowed_ips.split(","):
        one = raw.strip()
        if not one:
            continue
        try:
            networks.append(ipaddress.ip_network(one, strict=False))
        except ValueError:
            invalid.append(one)
    return networks, invalid


# --- 거절 ----------------------------------------------------------------------


@dataclass(frozen=True)
class _Refusal:
    at: datetime
    status: int
    reason: str


class _Refusals:
    """이 프로세스가 마지막으로 거절한 것 — 서버 화면이 **자세한 사유**를 말할 때 쓴다.

    프로세스(워커 넷 · 서버 둘)마다 따로라, 「언제 · 몇 번 응답으로」 는 접근 로그(DB)에서 읽고
    사유는 그 거절을 이 프로세스가 했을 때만 붙인다(`status`)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.last: _Refusal | None = None

    def note(self, status: int, reason: str) -> None:
        with self._lock:
            self.last = _Refusal(datetime.now(UTC), status, reason)

    def reset(self) -> None:
        with self._lock:
            self.last = None


refusals = _Refusals()

#: 거절한 숫자의 뜻 — 이 프로세스가 본 거절이 아닐 때 서버 화면이 쓴다.
_MEANING = {
    400: "누구의 토큰인지 알 수 없었다(이메일 머리 없음) 또는 client 모양이 틀렸다",
    401: "게이트웨이의 비밀이 HEAX_SSO_SECRET 과 다르다",
    403: "승인된 계정이 없다(처음 보는 사람 · 승인 대기 · 정지 · 삭제) 또는 허용 주소 밖",
    404: "위임이 꺼져 있었다(HEAX_SSO_SECRET 없음)",
}


def _refuse(error: AppError, detail: str) -> NoReturn:
    """거절한다 — 이 프로세스의 마지막 거절로 적고, 로그에 자세한 것(누구 · 어디서)을 남긴다.

    본문(`error.message`)에는 자세한 것을 싣지 않는다 — 게이트웨이가 그 사람에게 그대로
    보인다."""
    refusals.note(error.status, detail)
    # 처음 보는 사람 · 대기는 사람이 할 일이 있는 정상적인 거절이고, 비밀 · 주소는 살펴볼
    # 일이다.
    level = logging.INFO if error.status in (403, 404) else logging.WARNING
    logger.log(level, "게이트웨이 위임 거절(%s): %s", error.status, detail)
    raise error


def _remote(request: Request) -> str:
    return request.client.host if request.client else ""


def _address(raw: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        found = ipaddress.ip_address(raw)
    except ValueError:
        return None
    # IPv6 소켓으로 받은 IPv4(`::ffff:10.0.0.5`)는 IPv4 로 견준다 — 목록에는 IPv4 로 적는다.
    if isinstance(found, ipaddress.IPv6Address) and found.ipv4_mapped is not None:
        return found.ipv4_mapped
    return found


def _check_origin(request: Request, settings: Settings) -> None:
    """허용 IP — **TCP 의 원격 주소로만.** 게이트웨이는 직접 부르므로 프록시 머리를 믿을 까닭이
    없다.

    ⚠️ `TRUST_PROXY` 를 켠 설치(포털 뒤면 켠다)는 서버(uvicorn)가 `X-Forwarded-For` 로 원격
       주소를 **바꿔 적은 뒤** 앱에 넘긴다 — 앱에서는 원래 주소가 안 보인다. 그 머리를 누구나
       적을 수 있으므로 목록이 있으면 그 머리가 붙은 요청은 받지 않는다(머리가 없으면 서버는
       주소를 안 바꾼다 — 그때 보이는 것이 TCP 의 원격 주소다).
    """
    networks, invalid = allowed_networks(settings)
    if not networks and not invalid:
        return
    name = settings.app_name
    remote = _remote(request)
    if invalid:
        _refuse(
            PlainTextError(
                code("AUTH", 19),
                f"{name} 의 위임 허용 주소 설정을 읽지 못해 받지 않습니다 — {name} 관리자에게 "
                "알려 주세요.",
                status=403,
            ),
            f"HEAX_SSO_ALLOWED_IPS 에 못 읽은 값 {invalid} — 오타 하나로 제한이 풀리지 않게 "
            f"전부 거절한다(원격 {remote})",
        )
    if settings.trust_proxy and any(one in request.headers for one in _PROXY_HEADERS):
        _refuse(
            PlainTextError(
                code("AUTH", 19),
                f"{name} 은 프록시를 거친 위임 요청을 받지 않습니다 — 게이트웨이는 앱 "
                "포트로 직접 부릅니다(포털 담당: 위임 주소를 확인해 주세요).",
                status=403,
            ),
            f"프록시 머리가 붙은 요청(보이는 원격 {remote}) — TRUST_PROXY 라 원래 주소를 "
            "알 수 없다",
        )
    address = _address(remote)
    if address is None or not any(address in network for network in networks):
        _refuse(
            PlainTextError(
                code("AUTH", 19),
                f"{name} 은 이 주소에서 오는 위임 요청을 받지 않습니다 — 포털 담당과 {name} "
                "관리자가 허용 주소(HEAX_SSO_ALLOWED_IPS)를 맞춥니다.",
                status=403,
            ),
            f"허용 IP 밖: {remote or '(모름)'} — 허용 {settings.heax_sso_allowed_ips}",
        )


def _check_secret(request: Request, settings: Settings) -> None:
    given = request.headers.get(SECRET_HEADER, "")
    want = settings.heax_sso_secret.strip()
    # **상수 시간으로.** `==` 는 앞에서부터 갈려 맞은 앞자리 길이를 응답 시간으로 흘린다. str
    # 의 compare_digest 는 ASCII 밖의 글자에 TypeError 를 낸다 — 바이트로 견준다.
    if not hmac.compare_digest(given.encode("utf-8"), want.encode("utf-8")):
        name = settings.app_name
        _refuse(
            PlainTextError(
                code("AUTH", 12),
                f"{name} 의 게이트웨이 비밀이 맞지 않습니다 — 포털 담당에게 알려 주세요"
                f"(게이트웨이의 위임 비밀과 {name} 의 HEAX_SSO_SECRET 이 다릅니다).",
                status=401,
            ),
            f"비밀이 다르다(원격 {_remote(request)}, 머리 {'있음' if given else '없음'})",
        )


def _guard(request: Request, settings: Settings) -> None:
    """꺼짐 · 주소 · 비밀 — 위임 창구 셋(발급 · 회수 · 점검)이 **같은 순서로** 본다. 순서가 곧
    뜻이다 — 비밀을 모르는 쪽에게는 그 뒤의 것(누가 있나)을 말하지 않는다."""
    # 이 요청에서 남는 감사 · 로그는 전부 게이트웨이 통로다. 머리(X-Client)는 믿지 않는다.
    set_actor_client(CLIENT)
    if not enabled(settings):
        _refuse(
            NotFound(
                code("AUTH", 11),
                "HWAX 게이트웨이 위임이 꺼져 있습니다 — 이 설치의 .env 에 HEAX_SSO_SECRET 이 "
                "없습니다.",
            ),
            "꺼짐(HEAX_SSO_SECRET 없음)",
        )
    _check_origin(request, settings)
    _check_secret(request, settings)


def _gate(request: Request, settings: Settings) -> tuple[str, str]:
    """`_guard` 에 더해 client · 이메일을 본다 → (아이디, client)."""
    _guard(request, settings)

    client = (request.headers.get(CLIENT_HEADER) or "").strip() or DEFAULT_CLIENT
    if not _CLIENT_SHAPE.fullmatch(client):
        _refuse(
            PlainTextError(
                code("AUTH", 14),
                "X-Heax-Client 는 영문 · 숫자 · 「. _ -」 40자 이내여야 합니다.",
                status=400,
            ),
            f"client 모양이 틀렸다: {client[:60]!r}",
        )

    # 위임 머리가 먼저다 — 게이트웨이는 X-Heax-User-Email 이 도중의 프록시(Caddy)에서 서비스
    # 토큰의 주인으로 바뀌는 앱이 있어 이것을 따로 싣는다. 우리에게는 직접 오므로 둘이 같다.
    delegate = (request.headers.get(DELEGATE_HEADER) or "").strip()
    claimed = (request.headers.get(EMAIL_HEADER) or "").strip()
    if delegate and claimed and normalize_login_id(delegate) != normalize_login_id(claimed):
        logger.warning(
            "게이트웨이 위임: 위임 머리(%s)와 이메일 머리(%s)가 다르다 — 위임 머리로 간다",
            delegate,
            claimed,
        )
    login = normalize_login_id(delegate or claimed)
    if not login or len(login) > 254:
        _refuse(
            PlainTextError(
                code("AUTH", 13),
                "누구의 토큰인지 알 수 없습니다 — X-Heax-User-Email 머리가 없습니다.",
                status=400,
            ),
            f"이메일 머리가 없거나 길다({len(login)}자, 원격 {_remote(request)})",
        )
    return login, client


def _signup_hint(settings: Settings) -> str:
    """처음 보는 사람이 할 일 — 포털 로그인이 켜져 있으면 타일 한 번이 곧 가입 신청이다."""
    name = settings.app_name
    launch = settings.portal_launch_url.strip()
    if launch and settings.portal_jwks_url.strip():
        return (
            f"HWAX 포털에서 「{name}」 을 한 번 열면({launch}) 가입 신청이 되고, "
            "관리자가 승인한 뒤 쓸 수 있습니다."
        )
    return f"「{name}」 화면에서 가입 신청하세요 — 관리자가 승인한 뒤 쓸 수 있습니다."


def _approved(db: Session, login: str, settings: Settings) -> User:
    """그 아이디의 **승인된 활성 계정.** 시스템 관리자 계정도 이메일이 맞으면 된다 — 이 길은
    비밀을 아는 게이트웨이만 부르고, 게이트웨이는 포털이 확인한 신원만 싣는다."""
    name = settings.app_name
    user = db.scalar(select(User).where(User.email == login))
    if user is None:
        _refuse(
            PlainTextError(
                code("AUTH", 15),
                f"{name}에 가입 승인된 계정이 없습니다({login}) — {_signup_hint(settings)}",
                status=403,
            ),
            f"처음 보는 사람 {login} — 계정을 만들지 않는다(JIT 없음)",
        )
    if user.deleted_at is not None:
        _refuse(
            PlainTextError(
                code("AUTH", 18),
                f"{name}에서 삭제된 계정입니다({login}) — {name} 관리자에게 문의하세요.",
                status=403,
            ),
            f"삭제된 계정 {login}",
        )
    if user.status == "pending":
        _refuse(
            PlainTextError(
                code("AUTH", 16),
                f"{name} 가입 승인을 기다리는 중입니다({login}) — 관리자가 승인하면 쓸 수 "
                "있습니다.",
                status=403,
            ),
            f"승인 대기 {login}",
        )
    if user.status != "active":
        _refuse(
            PlainTextError(
                code("AUTH", 17),
                f"{name}에서 정지된 계정입니다({login}) — {name} 관리자에게 문의하세요.",
                status=403,
            ),
            f"정지된 계정({user.status}) {login}",
        )
    return user


def _delegated(db: Session, user: User, client: str) -> list[PersonalAccessToken]:
    return list(
        db.scalars(
            select(PersonalAccessToken).where(
                PersonalAccessToken.user_id == user.id,
                PersonalAccessToken.delegated_client == client,
            )
        ).all()
    )


def _alive(token: PersonalAccessToken, now: datetime) -> bool:
    return token.revoked_at is None and (token.expires_at is None or token.expires_at > now)


# --- 발급 · 회수 ----------------------------------------------------------------


def issue(db: Session, request: Request, settings: Settings) -> DelegatedTokenOut:
    """그 사람 명의의 위임 토큰 — 같은 client 의 직전 토큰은 회수한다. 아니면 거절(위 순서)."""
    login, client = _gate(request, settings)
    user = _approved(db, login, settings)

    # **한 사람의 발급을 줄 세운다** — 게이트웨이가 둘이거나 재시도가 겹치면 둘 다 「직전
    # 토큰」 을 같은 것으로 보고 새 토큰 둘을 살려 둔다. 그 사람의 줄을 잡는 동안만이다.
    db.execute(select(User.id).where(User.id == user.id).with_for_update())
    now = datetime.now(UTC)
    mine = _delegated(db, user, client)
    alive = [one for one in mine if _alive(one, now)]
    for one in alive:
        one.revoked_at = now
    # 이미 죽은 줄(지난번에 회수한 것 · 만료된 것)은 지운다 — 방금 회수한 것만 남겨 「언제
    # 바뀌었나」 를 목록에서 보인다. 감사는 토큰을 이름으로 적으므로 잃는 것이 없다.
    kept = {one.id for one in alive}
    for one in mine:
        if one.id not in kept:
            db.delete(one)

    raw, prefix, token_hash = security.new_pat()
    granted = granted_scopes(db)
    name = token_name(client)
    token = PersonalAccessToken(
        user_id=user.id,
        name=name,
        prefix=prefix,
        token_hash=token_hash,
        scopes=granted,
        expires_at=now + TOKEN_LIFETIME,
        delegated_client=client,
    )
    db.add(token)
    db.flush()
    audit.record(
        db,
        action=audit.GATEWAY_DELEGATED,
        actor=user,
        target_table="personal_access_tokens",
        target_id=token.id,
        target_label=f"{user.email} · {name}",
        changes={
            "client": client,
            "scopes": granted,
            "expires_at": (now + TOKEN_LIFETIME).isoformat(),
            "revoked_previous": len(alive),
            "remote": _remote(request) or None,
        },
    )
    db.commit()
    # 접근 로그가 「누가」 를 알게 — 미들웨어는 인증보다 바깥이라 스스로 모른다.
    request.scope[USER_ID_SCOPE_KEY] = user.id
    logger.info(
        "게이트웨이 위임 발급: %s (%s, 직전 %d개 회수)", user.email, client, len(alive)
    )
    return DelegatedTokenOut(access_token=raw, expires_in=int(TOKEN_LIFETIME.total_seconds()))


def verify(request: Request, settings: Settings) -> None:
    """연결 점검 — 게이트웨이가 「이 앱과 위임 설정(주소 · 비밀)이 맞나」 만 본다. 맞으면 아무
    일도 안 하고 돌아간다(라우트가 204).

    **이메일 머리는 요구하지 않는다** — 누구의 토큰도 내주지 않으므로. 계정 · 토큰 · 감사를
    건드리지 않는다(`/sso` 로 점검하면 토큰이 실제로 나가고 직전 토큰이 회수된다). 거절은
    발급과 같은 길(`_refuse`)이라 서버 화면의 「마지막으로 거절한 때」 에도 잡힌다(접근 로그
    `GATEWAY_SSO`)."""
    _guard(request, settings)
    logger.debug("게이트웨이 위임 점검 통과(원격 %s)", _remote(request))


def revoke(db: Session, request: Request, settings: Settings) -> DelegationRevokeOut:
    """그 사람의 그 client 위임 토큰을 끊는다 — 계정 상태와 무관하게(정지된 사람의 것도
    끊는다). 모르는 사람이면 끊을 것이 없다(0)."""
    login, client = _gate(request, settings)
    user = db.scalar(select(User).where(User.email == login))
    if user is None:
        return DelegationRevokeOut(revoked=0)
    now = datetime.now(UTC)
    alive = [one for one in _delegated(db, user, client) if _alive(one, now)]
    for one in alive:
        one.revoked_at = now
    if alive:
        audit.record(
            db,
            action=audit.GATEWAY_DELEGATION_REVOKED,
            actor=user,
            target_table="users",
            target_id=user.id,
            target_label=user.email,
            changes={"client": client, "revoked": len(alive)},
        )
    db.commit()
    request.scope[USER_ID_SCOPE_KEY] = user.id
    return DelegationRevokeOut(revoked=len(alive))


# --- 진단 ----------------------------------------------------------------------


def problems(settings: Settings) -> list[str]:
    """위임 설정끼리 안 맞는 것 — 서버 화면의 「HWAX 포털 로그인」 이 함께 보인다."""
    found: list[str] = []
    if not enabled(settings):
        return found
    secret = settings.heax_sso_secret.strip()
    if len(secret) < SECRET_MIN_CHARS:
        found.append(
            f"HEAX_SSO_SECRET 이 {len(secret)}자로 짧습니다 — openssl rand -hex 32(64자)로 "
            "다시 만들고 포털 담당에게 다시 전하세요(게이트웨이의 위임 비밀과 같아야 합니다)."
        )
    _networks, invalid = allowed_networks(settings)
    if invalid:
        found.append(
            "HEAX_SSO_ALLOWED_IPS 에 읽지 못한 값이 있어 게이트웨이 위임 발급을 모두 "
            "거절합니다: " + ", ".join(invalid)
        )
    return found


def status(db: Session, settings: Settings) -> GatewayDelegationOut:
    """「켜졌나 · 마지막으로 내준 때 · 마지막으로 거절한 때」 — 시스템 관리자의 서버 화면이
    본다."""
    networks, invalid = allowed_networks(settings)
    now = datetime.now(UTC)
    delegated = PersonalAccessToken.delegated_client.is_not(None)
    live = db.scalar(
        select(func.count())
        .select_from(PersonalAccessToken)
        .where(
            delegated,
            PersonalAccessToken.revoked_at.is_(None),
            PersonalAccessToken.expires_at > now,
        )
    )
    last_issued = db.scalar(select(func.max(PersonalAccessToken.created_at)).where(delegated))
    return GatewayDelegationOut(
        enabled=enabled(settings),
        sso_path=SSO_PATH,
        allowed_ips=[str(one) for one in networks],
        invalid_ips=invalid,
        scopes=granted_scopes(db),
        token_seconds=int(TOKEN_LIFETIME.total_seconds()),
        live_tokens=int(live or 0),
        last_issued_at=last_issued,
        last_refusal=_last_refusal(db),
    )


def _last_refusal(db: Session) -> GatewayRefusalOut | None:
    """접근 로그(모든 서버 · 프로세스)의 마지막 거절 — 이 프로세스가 한 거절이면 자세한 사유를
    붙인다."""
    row = db.execute(
        select(AccessLog.created_at, AccessLog.status_code)
        .where(AccessLog.action == GATEWAY_SSO, AccessLog.status_code >= 400)
        .order_by(AccessLog.created_at.desc())
        .limit(1)
    ).first()
    seen = refusals.last
    if row is None:
        if seen is None:
            return None
        return GatewayRefusalOut(at=seen.at, status=seen.status, reason=seen.reason)
    at, status_code = row
    # 접근 로그는 응답을 보낸 뒤에 적힌다 — 몇 초 안의 같은 숫자면 같은 거절이다.
    mine = (
        seen is not None
        and seen.status == status_code
        and abs((at - seen.at).total_seconds()) < 10
    )
    if seen is not None and mine:
        reason = seen.reason
    else:
        meaning = _MEANING.get(status_code, f"HTTP {status_code}")
        reason = f"{meaning} — 자세한 사유는 앱 로그의 「게이트웨이 위임 거절」"
    return GatewayRefusalOut(at=at, status=status_code, reason=reason)
