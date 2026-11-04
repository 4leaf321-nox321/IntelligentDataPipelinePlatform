"""HWAX 포털 로그인 — 포털이 보증한 신원을 받아 세션을 연다(jwt-handoff, ADR 0028).

## 흐름

포털(타일 · `/launch/<id>`)이 그 사람으로 **RS256 launch 토큰**(90초)을 만들고, 브라우저가 숨은
폼으로 `POST <접두어>/api/auth/portal-callback`(필드 `token`)에 보낸다. 여기서

1. 포털의 공개 키(JWKS)로 서명을 확인하고 `iss` · `aud` · `exp` · `nbf` · `scope == "launch"`
   를 본다.
2. `jti` 를 DB 에 적는다 — 한 토큰은 한 번만(`PortalLaunchJti`, 이중화 두 서버가 함께 본다).
3. 사람을 찾는다(`email` → 없으면 `sub`). 활성이면 비밀번호 로그인과 **같은 세션**(refresh
   쿠키)을 연다 — 라우트가 SPA 루트로 303 하고, SPA 는 기동 때 그 쿠키로 access 를 받는다. 처음
   보는 사람은 가입 신청(승인 대기)으로 만든다.

실패는 전부 로그인 화면으로 303(`?sso=<사유>`) 이다. 사유는 **사람이 할 일이 갈리는 만큼만**
싣고(만료 · 확인 실패 · 공개 키 못 받음 · 승인 대기 · 정지 · 삭제) 자세한 것(서명인지 aud 인지
재사용인지)은 로그에만 남긴다 — 응답에 실으면 토큰을 꾸며 보는 사람에게 힌트가 된다.

## 포털의 진단표와 맞춘다

포털 update-all 이 콜백에 가짜 토큰을 던져 상태 코드로 가른다: 200+HTML = 라우트 없음(포털
몫) · 405 = 옛 판 · **404+JSON = SSO 꺼짐**(`PORTAL_JWKS_URL` 없음) · 502/000 = 불통 · **3xx =
정상**. 그래서 꺼져 있으면 404 JSON(라우트), 켜져 있으면 가짜 토큰에도 303 이다.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, NoReturn
from urllib.parse import urlsplit

import httpx
import jwt
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.modules.accounts import services as accounts_services
from app.modules.accounts.models import User
from app.modules.auth import delegation
from app.modules.auth import services as auth_services
from app.modules.auth.models import PortalLaunchJti
from app.modules.auth.schemas import PortalStatusOut
from app.shared import audit
from app.shared.request_context import set_actor_client

logger = logging.getLogger(__name__)

#: 감사 기록의 통로(`actor_client`) — 이 길로 들어온 일은 이 이름이 붙는다.
CLIENT = "hwax-portal"

#: exp · nbf 의 여유(초). 포털과 이 서버의 시계가 몇 초 어긋나도 막 만든 토큰이 「아직 아님」
#: 으로 튕기지 않게 — 크게 두면 90초짜리 토큰의 수명이 그만큼 는다.
LEEWAY_SECONDS = 10
#: 공개 키를 들고 있는 시간 — 포털이 `Cache-Control: max-age=300` 으로 준다.
JWKS_TTL_SECONDS = 300
#: 모르는 kid 로 **다시 받는** 간격의 하한 — 가짜 kid 를 든 요청마다 포털에 받으러 가지 않게.
REFETCH_COOLDOWN_SECONDS = 10
FETCH_TIMEOUT_SECONDS = 5.0
#: 받는 토큰 · jti 의 길이 상한. 포털의 토큰은 1KB 남짓, jti 는 22자다.
MAX_TOKEN_CHARS = 8192
MAX_JTI_CHARS = 256

# 로그인 화면의 `?sso=` — 화면(`modules/auth/portal.ts`)이 사람 말로 바꾼다.
INVALID = "invalid"
"""서명 · iss · aud · scope · 모르는 kid · 모양 · **이미 쓴 토큰** — 포털에서 다시 열면
된다."""
EXPIRED = "expired"
"""exp 가 지났거나 nbf 전 — 오래 머문 폼이거나 두 서버의 시계가 어긋났다."""
UNAVAILABLE = "unavailable"
"""포털의 공개 키를 못 받았다 — 이 서버에서 `PORTAL_JWKS_URL` 이 안 닿는다(관리자 몫)."""
PENDING = "pending"
SUSPENDED = "suspended"
DELETED = "deleted"
ADMIN_BY_SUB = "admin_sub"
"""`sub` 로만 맞은 계정이 시스템 관리자다 — 붙이지 않는다(`find_user`)."""


class PortalLoginError(Exception):
    """로그인 화면으로 돌려보낼 실패 — `reason` 은 화면에, `detail` 은 로그에만."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def enabled(settings: Settings | None = None) -> bool:
    """포털 로그인이 켜져 있나 — `PORTAL_JWKS_URL` 이 있으면."""
    return bool((settings or get_settings()).portal_jwks_url.strip())


# --- 포털 공개 키(JWKS) ---------------------------------------------------------


@dataclass(frozen=True)
class _Fetched:
    url: str
    keys: dict[str, jwt.PyJWK]
    monotonic: float
    at: datetime


def _rsa_keys(body: Any) -> dict[str, jwt.PyJWK]:
    """JWKS 에서 **RS256 서명 키**만 kid 로. 모르는 종류(암호화 키 · 다른 알고리즘)는
    건너뛴다 — 포털이 키를 더한 날 그 하나 때문에 전부를 거절하지 않게."""
    entries = body.get("keys") if isinstance(body, dict) else None
    found: dict[str, jwt.PyJWK] = {}
    if not isinstance(entries, list):
        return found
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        kid = entry.get("kid")
        if not isinstance(kid, str) or not kid:
            continue
        if entry.get("kty") != "RSA" or entry.get("use") not in (None, "sig"):
            continue
        if entry.get("alg") not in (None, "RS256"):
            continue
        try:
            found[kid] = jwt.PyJWK(entry, algorithm="RS256")
        except jwt.PyJWTError:
            logger.warning("포털 JWKS 의 키 하나를 못 읽어 건너뛴다 (kid=%s)", kid)
    return found


class JwksCache:
    """포털 공개 키 — **프로세스마다** 5분 들고, 모르는 kid 면 한 번 다시 받는다(포털이 키를
    바꾼 날).

    ⚠️ **받기에 실패하면 묵은 키로 넘기지 않는다.** 받기를 못 하는 동안 포털이 키를 바꿨다면 —
       유출로 바꾼 날이 바로 그날이다 — 묵은 키가 옛 키로 서명한 토큰을 계속 받는다. 포털이
       안 닿으면 포털이 토큰을 낼 수도 없으니 잃는 것이 적다. 실패는 로그와 `portal-status` 에
       「마지막 오류」 로 남는다.
    """

    def __init__(self) -> None:
        self.transport: httpx.BaseTransport | None = None
        """시험이 갈아 끼운다(웹훅의 `Dispatcher.transport` 와 같은 무늬)."""
        self._lock = threading.Lock()
        self._fetched: _Fetched | None = None
        self._last_try = 0.0
        self.last_error: str | None = None
        self.last_error_at: datetime | None = None

    def reset(self) -> None:
        with self._lock:
            self._fetched = None
            self._last_try = 0.0
            self.last_error = None
            self.last_error_at = None

    @property
    def fetched_at(self) -> datetime | None:
        return self._fetched.at if self._fetched else None

    def _fetch(self, url: str) -> dict[str, jwt.PyJWK]:
        try:
            with httpx.Client(
                timeout=FETCH_TIMEOUT_SECONDS, transport=self.transport
            ) as client:
                response = client.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as caught:
            raise PortalLoginError(
                UNAVAILABLE, f"포털 JWKS 를 못 받았다({url}): {type(caught).__name__} {caught}"
            ) from caught
        if response.status_code != 200:
            raise PortalLoginError(
                UNAVAILABLE,
                f"포털 JWKS 가 {response.status_code} 로 답했다({url}): {response.text[:200]}",
            )
        try:
            body = response.json()
        except ValueError as caught:
            # 포털 nginx 에 그 길이 없으면 포털 SPA(HTML 200)가 온다 — 그 경우를 가려 말한다.
            raise PortalLoginError(
                UNAVAILABLE,
                f"포털 JWKS 가 JSON 이 아니다({url}) — 주소가 포털 화면으로 떨어졌을 수 있다: "
                f"{response.text[:120]!r}",
            ) from caught
        keys = _rsa_keys(body)
        if not keys:
            raise PortalLoginError(UNAVAILABLE, f"포털 JWKS 에 RS256 서명 키가 없다({url})")
        return keys

    def load(self, url: str, *, force: bool = False) -> dict[str, jwt.PyJWK]:
        """키 목록. 5분이 지났거나 `force`(모르는 kid)면 받는다 — 단 `force` 는 10초에 한
        번."""
        with self._lock:
            now = time.monotonic()
            cached = self._fetched if self._fetched and self._fetched.url == url else None
            if cached is not None:
                fresh = now - cached.monotonic < JWKS_TTL_SECONDS
                if fresh and (not force or now - self._last_try < REFETCH_COOLDOWN_SECONDS):
                    return cached.keys
            self._last_try = now
            try:
                keys = self._fetch(url)
            except PortalLoginError as failed:
                self.last_error = failed.detail
                self.last_error_at = datetime.now(UTC)
                logger.error("포털 로그인: %s", failed.detail)
                if cached is not None and now - cached.monotonic >= JWKS_TTL_SECONDS:
                    self._fetched = None  # 묵은 키로 넘기지 않는다(클래스 설명)
                raise
            self._fetched = _Fetched(url=url, keys=keys, monotonic=now, at=datetime.now(UTC))
            return keys

    def key_for(self, url: str, kid: str) -> jwt.PyJWK:
        keys = self.load(url)
        if kid not in keys:
            # 포털이 키를 바꿨을 수 있다 — 한 번 다시 받는다.
            keys = self.load(url, force=True)
        if kid not in keys:
            raise PortalLoginError(
                INVALID, f"모르는 kid {kid!r} — 포털 JWKS 의 kid: {sorted(keys)}"
            )
        return keys[kid]


jwks = JwksCache()


# --- 토큰 확인 ----------------------------------------------------------------


def verify(token: str, settings: Settings) -> dict[str, Any]:
    """서명과 클레임을 확인한 launch 토큰의 클레임. 아니면 `PortalLoginError`."""
    if not token or len(token) > MAX_TOKEN_CHARS:
        raise PortalLoginError(INVALID, f"토큰이 비었거나 너무 길다({len(token)}자)")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as caught:
        raise PortalLoginError(INVALID, f"토큰 머리를 못 읽었다: {caught}") from caught
    # **RS256 만.** 머리의 alg 를 그대로 믿으면 `none` 이나 공개 키를 HMAC 비밀로 쓰는 위조가
    # 열린다.
    if header.get("alg") != "RS256":
        raise PortalLoginError(INVALID, f"alg {header.get('alg')!r} — RS256 만 받는다")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise PortalLoginError(INVALID, "토큰 머리에 kid 가 없다")
    key = jwks.key_for(settings.portal_jwks_url.strip(), kid)
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key=key.key,
            algorithms=["RS256"],
            audience=settings.portal_audience,
            issuer=settings.portal_issuer.strip(),
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "nbf", "iat", "iss", "aud", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError as caught:
        raise PortalLoginError(EXPIRED, f"만료된 토큰: {caught}") from caught
    except jwt.ImmatureSignatureError as caught:
        raise PortalLoginError(
            EXPIRED, f"아직 유효하지 않은 토큰(nbf · iat — 시계 차이?): {caught}"
        ) from caught
    except jwt.PyJWTError as caught:
        raise PortalLoginError(INVALID, f"{type(caught).__name__}: {caught}") from caught
    # 포털은 같은 키로 다른 쓰임의 토큰도 낸다(세션 · PAT) — launch 가 아니면 이 문은 아니다.
    if claims.get("scope") != "launch":
        raise PortalLoginError(INVALID, f"scope {claims.get('scope')!r} — launch 가 아니다")
    return claims


def consume_jti(db: Session, jti: str, exp: float) -> None:
    """**한 토큰은 한 번만.** 이미 쓴 jti 면 거절한다 — 그리고 지난 줄을 치운다. 커밋한다:
    뒤의 단계(정지 계정 등)에서 돌려보내도 그 토큰은 이미 쓴 것이다."""
    now = datetime.now(UTC)
    db.execute(delete(PortalLaunchJti).where(PortalLaunchJti.expires_at < now))
    digest = hashlib.sha256(jti.encode("utf-8")).hexdigest()
    until = datetime.fromtimestamp(exp, UTC) + timedelta(seconds=LEEWAY_SECONDS)
    # **RETURNING 으로 본다** — 이 드라이버(psycopg 3)는 ON CONFLICT 삽입의 rowcount 를 -1 로
    # 주어, 「몇 줄 들어갔나」 로 가리면 재사용이 통과하거나 전부 막힌다(시험이 둘 다 잡았다).
    fresh = (
        db.execute(
            pg_insert(PortalLaunchJti)
            .values(jti_hash=digest, expires_at=until)
            .on_conflict_do_nothing(index_elements=["jti_hash"])
            .returning(PortalLaunchJti.jti_hash)
        ).scalar_one_or_none()
        is not None
    )
    db.commit()
    if not fresh:
        raise PortalLoginError(
            INVALID, f"이미 쓴 토큰(jti {jti[:8]}…) — 폼 재전송이거나 가로챈 토큰"
        )


# --- 사람 찾기 · 세션 -----------------------------------------------------------


def _text(claims: dict[str, Any], key: str) -> str:
    value = claims.get(key)
    return value.strip() if isinstance(value, str) else ""


def find_user(db: Session, *, email: str, sub: str) -> tuple[User | None, str]:
    """(계정, 무엇으로 찾았나). `email` 로 먼저, 없으면 `sub` 로.

    **email 이 먼저인 까닭** — 우리 아이디(`users.email`)는 대개 사내 메일 주소이고, 포털도 SSO
    의 메일 Claim 을 정본으로 든다(포털 `SAML_ATTR_EMAIL` — 없으면 포털이 로그인을 거절한다).
    둘 다 `normalize_login_id`(앞뒤 공백 · 소문자)로 맞춰 견준다 — 대소문자 하나로 다른 사람이
    되지 않게.

    **sub 로도 찾는 까닭** — 관리자가 계정을 짧은 아이디(사번 · knox id)로 만든 설치가 있다
    (`users.email` 은 형식을 강제하지 않는다). 포털은 `SAML_SUBJECT_SOURCE` 로 `sub` 를 메일이
    아닌 값(NameID · 사번 Claim)으로 둘 수 있고, 그때 그 아이디와 맞는다. 기본(sub = 메일)이면
    email 과 같은 값이라 두 번 찾을 것이 없다.

    ⚠️ sub 로 붙는 것은 「우리 아이디와 포털의 sub 가 같은 이름 공간」 이라는 가정이다 — 로컬
       관리자 `admin` 같은 아이디를 포털이 누군가의 sub 로 내면 그 사람이 그 계정으로 들어온다.
       그래서 sub 로 찾은 것은 감사(`matched_by: sub`)와 로그에 남기고, **시스템 관리자
       계정에는 sub 로 붙이지 않는다**(라우트가 `admin_sub` 로 돌려보낸다 — 아이디를 메일로
       바꾸면 된다).
    """
    login_email = auth_services.normalize_login_id(email) if email else ""
    if login_email:
        found = db.scalar(select(User).where(User.email == login_email))
        if found is not None:
            return found, "email"
    login_sub = auth_services.normalize_login_id(sub) if sub else ""
    if login_sub and login_sub != login_email:
        found = db.scalar(select(User).where(User.email == login_sub))
        if found is not None:
            return found, "sub"
    return None, ""


def _request_signup(db: Session, *, login_id: str, name: str, sub: str) -> NoReturn:
    """처음 보는 사람 — 가입 신청(승인 대기)으로 만들고 **늘 `PENDING` 으로 돌려보낸다.**"""
    try:
        user = accounts_services.request_from_portal(db, login_id=login_id, display_name=name)
        audit.record(
            db,
            action=audit.ACCOUNT_PORTAL_REQUESTED,
            actor=user,
            target_table="users",
            target_id=user.id,
            target_label=user.email,
            changes={"status": {"before": None, "after": "pending"}, "sub": sub},
        )
        db.commit()
        logger.info("포털 로그인: 처음 보는 사람 — 가입 신청(승인 대기) (%s)", login_id)
    except IntegrityError:
        # 같은 사람이 두 창으로 동시에 들어왔다 — 먼저 들어간 신청이 있다. 같은 말로
        # 돌려보낸다.
        db.rollback()
        logger.info("포털 로그인: 같은 아이디의 신청이 방금 먼저 생겼다 (%s)", login_id)
    raise PortalLoginError(PENDING, f"가입 신청(승인 대기) — {login_id}")


def sign_in(db: Session, token: str, user_agent: str | None) -> tuple[User, str, int, str]:
    """launch 토큰으로 로그인한다 — (사람, access, access 수명(초), refresh 평문). 아니면
    `PortalLoginError`. 콜백(브라우저의 폼)은 refresh 만 쿠키로 심고, 교환(화면의 요청)은
    비밀번호 로그인처럼 access 도 돌려준다.

    세션은 비밀번호 로그인과 **같은 것**(`issue_session`)이다 — access 토큰에 그 사람의 세대
    (`session_epoch`)가 박히므로 정지 · 비밀번호 초기화가 끊는 것도 같다.
    """
    settings = get_settings()
    # 이 요청에서 남는 감사는 전부 포털 통로다(로그인 · 가입 신청).
    set_actor_client(CLIENT)
    claims = verify(token, settings)
    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti or len(jti) > MAX_JTI_CHARS:
        raise PortalLoginError(INVALID, f"jti 가 문자열이 아니거나 너무 길다: {jti!r}"[:200])
    consume_jti(db, jti, float(claims["exp"]))

    email, sub, name = _text(claims, "email"), _text(claims, "sub"), _text(claims, "name")
    user, matched_by = find_user(db, email=email, sub=sub)
    if user is None:
        login_id = email or sub
        if not login_id or len(login_id) > 254:
            raise PortalLoginError(
                INVALID, f"아이디로 쓸 email · sub 가 없거나 길다: {login_id!r}"
            )
        _request_signup(db, login_id=login_id, name=name or login_id, sub=sub)

    if matched_by == "sub" and user.is_system_admin:
        raise PortalLoginError(
            ADMIN_BY_SUB,
            f"sub {sub!r} 로만 맞은 계정 {user.email!r} 이 시스템 관리자다 — 포털 "
            "로그인으로 붙이지 않는다(아이디를 포털의 메일 주소로 바꾸면 email 로 맞는다)",
        )
    # 비밀번호 로그인과 같은 판정(`ensure_can_sign_in`)을 사유별로 — 화면 말이 그것과 같다.
    if user.deleted_at is not None:
        raise PortalLoginError(DELETED, f"삭제된 계정 — {user.email}")
    if user.status == "pending":
        raise PortalLoginError(PENDING, f"승인 대기 중 — {user.email}")
    if user.status != "active":
        raise PortalLoginError(SUSPENDED, f"정지된 계정({user.status}) — {user.email}")

    audit.record(
        db,
        action=audit.PORTAL_LOGIN,
        actor=user,
        target_table="users",
        target_id=user.id,
        target_label=user.email,
        changes={"matched_by": matched_by, "sub": sub, "jti": jti},
    )
    if matched_by == "sub":
        logger.info(
            "포털 로그인: email(%s)이 아니라 sub(%s)로 %s 에 붙였다", email, sub, user.email
        )
    # 감사 기록과 refresh 가 한 커밋이다(`issue_session` 이 커밋한다).
    access, expires_in, refresh_raw = auth_services.issue_session(db, user, user_agent)
    return user, access, expires_in, refresh_raw


# --- 진단 ----------------------------------------------------------------------


def status(db: Session, settings: Settings) -> PortalStatusOut:
    """「포털 로그인이 켜졌나 · 공개 키가 닿나」 — 시스템 관리자의 서버 화면이 본다.
    게이트웨이의 사람별 위임(`delegation`)도 같은 자리에서 말한다 — 포털에 붙는 일이 둘로 갈려
    보이지 않게.

    켜져 있으면 **지금 받아 본다**(5분 안에 받은 것이 있으면 그것) — 설정만 보고 「켜짐」
    이라고 하면, 이 서버에서 JWKS 주소가 안 닿는 것은 첫 사람이 로그인을 시도해야 드러난다.
    """
    on = enabled(settings)
    kids: list[str] = []
    reachable: bool | None = None
    problems: list[str] = []
    if on:
        try:
            kids = sorted(jwks.load(settings.portal_jwks_url.strip()))
            reachable = True
        except PortalLoginError:
            reachable = False
        base = settings.base_path
        if not base:
            problems.append(
                "PUBLIC_PATH 가 비어 있습니다 — 포털은 /<id>/ 를 벗겨 넘기므로 로그인 뒤 "
                "돌아갈 곳이 포털의 첫 화면(/)이 됩니다. .env 에 PUBLIC_PATH=/<포털 id> 를 "
                "적으세요."
            )
        elif base.strip("/") != settings.portal_audience:
            problems.append(
                f"접두어({base})와 PORTAL_AUDIENCE({settings.portal_audience})가 다릅니다 "
                "— 포털 타일의 id · audience 와 맞는지 확인하세요(보통 셋이 같다)."
            )
        if not settings.trust_proxy:
            problems.append(
                "TRUST_PROXY 가 꺼져 있습니다 — 포털이 https 로 받아도 앱은 http 로 보고 "
                "로그인 쿠키에 Secure 를 붙이지 않습니다(로그인은 됩니다)."
            )
        problems.extend(_mcp_problems(settings))
    elif settings.portal_launch_url.strip():
        problems.append(
            "PORTAL_LAUNCH_URL 은 있는데 PORTAL_JWKS_URL 이 없습니다 — 포털 로그인이 꺼져 "
            "있어 로그인 화면에 단추를 표시하지 않습니다."
        )
    return PortalStatusOut(
        enabled=on,
        jwks_url=settings.portal_jwks_url.strip() or None,
        audience=settings.portal_audience,
        issuer=settings.portal_issuer.strip(),
        launch_url=settings.portal_launch_url.strip() or None,
        callback_path=f"{settings.base_path}/api/auth/portal-callback",
        reachable=reachable,
        kids=kids,
        fetched_at=jwks.fetched_at,
        last_error=jwks.last_error,
        last_error_at=jwks.last_error_at,
        problems=[*problems, *delegation.problems(settings)],
        delegation=delegation.status(db, settings),
    )


def _mcp_problems(settings: Settings) -> list[str]:
    """화면(「내 정보」)이 안내할 MCP 주소가 포털 뒤에서 닿나.

    **포털은 MCP 경로를 열지 않는다**(Report Archive 와 같다 — ADR 0028). 사람들의 Claude 는
    포털의 게이트웨이(`/mcp-gw/mcp`)로 붙고, 게이트웨이가 우리 MCP 포트를 직접 부른다. 그런데
    화면은 `MCP_PUBLIC_URL` 이 비면 들어온 주소에 `/mcp` 를 붙여 짐작한다 — 포털 뒤에서는
    `https://<포털>/<id>/mcp` 가 되고 그 길은 없다(앱으로 떨어져 404). 직접 붙는 사람을 위해
    MCP 포트 주소를 적게 한다.
    """
    public = settings.mcp_public_url.strip()
    portal_host = urlsplit(settings.portal_launch_url.strip()).netloc
    under_portal = (
        bool(public) and bool(portal_host) and urlsplit(public).netloc == portal_host
    )
    if public and not under_portal:
        return []
    return [
        "MCP_PUBLIC_URL 이 "
        + ("포털 주소 아래입니다" if under_portal else "비어 있습니다")
        + " — 포털은 MCP 경로(/<id>/mcp)를 열지 않습니다(Claude 는 포털의 /mcp-gw/mcp 로 "
        "붙고, 게이트웨이가 MCP 포트를 직접 부릅니다). 화면의 Claude 연결 안내가 닿는 주소를 "
        "보이도록 MCP_PUBLIC_URL=http://<이 서버>:<MCP 포트>/mcp 를 적으세요."
    ]
