"""비밀번호 해시와 토큰 발급 — 암호 관련 원시 연산만 모은다.

**bcrypt 앞에 sha256 을 한 번 건다.** bcrypt 는 입력을 72바이트에서 자르는데,
한글 비밀번호는 글자당 3바이트라 24자를 넘으면 뒤가 조용히 무시된다. sha256 으로
길이를 고정한 뒤 bcrypt 에 넣으면 그 제한이 사라진다.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.config import get_settings

#: 이름을 바꾸기 전에 발급된 표식. **발급은 안 하고 알아보기만 한다** — 이미 나간
#: 토큰을 죽이면 그것을 붙여 둔 설정은 사람이 손으로 찾아가 고쳐야 하고, 그것은
#: "이름만 바꾸는 일" 의 대가로 너무 크다. 전부 재발급한 뒤 지운다.
LEGACY_PAT_PREFIXES: tuple[str, ...] = ()


def pat_prefix() -> str:
    """PAT 평문 앞에 붙는 표식 — `<app_slug>_pat_`. 로그와 소스에서 유출을 눈으로 찾게 한다.

    **설치의 slug 에서 나온다.** 플랫폼마다 달라야 하는데, 따로 적으면 옆 플랫폼의 토큰을
    붙여 넣었을 때 "형식은 맞는데 인증이 안 되는" 상태가 되고, 그것은 오타와 구별되지 않는다.

    길이를 줄이지 않는다. 이 표식이 하는 일은 **로그와 소스에서 눈으로 찾는 것**이라,
    `matpylon_pat_` 이 `mpl_pat_` 보다 낫다."""
    return f"{get_settings().app_slug}_pat_"


def accepted_pat_prefixes() -> tuple[str, ...]:
    """받을 때 쓰는 목록. 발급은 항상 `pat_prefix()` 하나로만 한다."""
    return (pat_prefix(), *LEGACY_PAT_PREFIXES)


def _prepared(password: str) -> bytes:
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


#: bcrypt 라운드. **일부러 느린 함수이고 그것이 값이다** — 운영에서는 12 가 맞다.
#:
#: 시험만 낮춘다. 시험 하나가 계정을 만들고(해시) 로그인하므로(검증) 라운드 12 면
#: 테스트당 0.4초를 여기서 쓰는데, 그 시간은 인증 로직이 아니라 **bcrypt 의 설계
#: 목적**을 재는 데 쓰인다. 낮춰도 검사하는 것은 그대로다: 같은 알고리즘, 같은
#: 전처리, 같은 경로. 환경변수로 두는 이유는 코드가 시험을 알면 안 되기 때문이다.
BCRYPT_ROUNDS = int(os.environ.get("APP_BCRYPT_ROUNDS", "12"))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepared(password), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("ascii")


#: 「비밀번호 없음」 의 표식 — bcrypt 해시는 `$` 로 시작하므로 겹치지 않는다.
UNUSABLE_PASSWORD_PREFIX = "!"


def unusable_password() -> str:
    """**어떤 비밀번호로도 안 맞는** 저장값 — 포털로 가입 신청된 계정(ADR 0028)이 쓴다.

    아는 사람이 없는 난수를 해시해 두는 것과 달리 「비밀번호가 없다」 가 값에 드러나고, 검증이
    bcrypt 를 부르지 않고 바로 거절한다. 그 사람이 아이디 · 비밀번호로도 들어와야 하면 관리자가
    「비밀번호 초기화」 로 임시 비밀번호를 준다."""
    return UNUSABLE_PASSWORD_PREFIX + secrets.token_hex(16)


@functools.cache
def _timing_hash() -> str:
    """비밀번호 없는 계정에 견줄 해시 — 한 번만 만든다(`services._dummy_hash` 와 같은 까닭)."""
    return hash_password("unusable-password")


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith(UNUSABLE_PASSWORD_PREFIX):
        # **같은 만큼 일하고 거절한다** — 바로 돌아가면 응답 시간으로 「이 아이디는 포털로만
        # 들어오는 계정」 이 샌다(없는 계정에 미리 만든 해시를 견주는 것과 같은 까닭).
        bcrypt.checkpw(_prepared(password), _timing_hash().encode("ascii"))
        return False
    try:
        return bcrypt.checkpw(_prepared(password), password_hash.encode("ascii"))
    except ValueError:
        # 저장된 해시가 손상된 경우. 인증 실패로 처리한다.
        return False


def hash_token(raw: str) -> str:
    """불투명 토큰(refresh·PAT)의 저장용 해시. 원문은 어디에도 남기지 않는다."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def new_pat() -> tuple[str, str, str]:
    """(평문, 표시용 prefix, 해시). 평문은 발급 응답에서 한 번만 노출된다."""
    prefix = pat_prefix()
    raw = prefix + secrets.token_urlsafe(32)
    return raw, raw[: len(prefix) + 6], hash_token(raw)


def create_access_token(user_id: uuid.UUID, epoch: int = 0) -> tuple[str, int]:
    """(JWT, 만료까지 초). 토큰 하나하나를 폐기하지는 않는다 — 그 사람의 로그인을 통째로 끊을
    때는 세대(`epoch`, `User.session_epoch`)를 올려, 그 전에 받은 것을 한꺼번에 무효로 한다."""
    settings = get_settings()
    ttl = timedelta(minutes=settings.access_token_minutes)
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "typ": "access",
        "sep": epoch,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, int(ttl.total_seconds())


#: 발급·검증 사이의 시계 어긋남 허용치.
CLOCK_LEEWAY_SECONDS = 30


def decode_access_token(token: str) -> dict[str, Any] | None:
    """검증에 실패하면 None.

    실패 사유를 호출자에게 넘기지 않는다 — 응답으로 새면 공격자에게 힌트가 된다.
    사유는 호출부에서 로그로 남긴다.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=["HS256"],
            # **시계가 뒤로 튀는 기계가 있다.** WSL2 가 NTP 로 재동기화하면 몇 초가
            # 되감기고, 그러면 방금 발급한 토큰의 iat 가 「미래」 라 거절된다 — 로그인
            # 직후 401 이 산발적으로 난다. 30초는 만료 판정에 실질적 영향이 없다.
            leeway=CLOCK_LEEWAY_SECONDS,
        )
    except jwt.PyJWTError:
        return None
    return payload if payload.get("typ") == "access" else None
