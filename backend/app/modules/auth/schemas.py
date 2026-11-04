"""인증 API 의 요청·응답 형태.

이 파일이 프론트 타입의 원본이다 — OpenAPI 를 거쳐 schema.d.ts 가 생성된다.

    cd backend  ; python scripts/export_openapi.py
    cd frontend ; npm run api:types
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    """EmailStr 을 쓰지 않는다.

    email-validator 는 .local 처럼 특수 용도로 예약된 도메인을 문법 단계에서
    거부하는데, 폐쇄망 사내 계정은 그런 주소를 쓰는 경우가 흔하다. 형식을 강하게
    검사해서 얻는 것보다 **로그인 자체가 성립하지 않는 손해**가 크다.
    """

    password: str = Field(min_length=1, max_length=200)


class WorkspaceMembershipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: uuid.UUID
    slug: str
    name: str
    path: str
    """개발본부 / 재료시험팀. 부서 선택기가 이름만 보여 주면 같은 이름의 팀이
    본부마다 있을 때 어느 쪽인지 알 수 없다."""
    depth: int
    role: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    status: str
    is_system_admin: bool
    must_change_password: bool
    home_workspace_slug: str | None
    memberships: list[WorkspaceMembershipOut]


class PortalExchangeRequest(BaseModel):
    """화면이 포털의 표준 launch(`POST /systems/{id}/launch`)로 받은 토큰(ADR 0028)."""

    token: str = Field(min_length=1, max_length=8192)


class LoginResponse(BaseModel):
    access_token: str
    expires_in: int
    """초 단위. 프론트가 만료 전에 갱신을 걸 수 있게 한다."""
    user: UserOut


class ProfileUpdateRequest(BaseModel):
    """자기 정보 수정.

    **표시 이름만 바꾼다.** 아이디(email)는 로그인 식별자라 본인이 바꾸면 감사
    로그·알림이 가리키는 대상이 흔들린다 — 그것은 관리자의 일로 남긴다.
    """

    display_name: str = Field(min_length=1, max_length=100)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)
    """길이 하한을 두지 않는다.

    우리가 정한 숫자가 기관 규칙과 어긋나면 사람은 규칙을 지키는 대신 **우회할
    길을 찾는다**(스크립트로 직접 바꾸기 등) — 그 경로가 오히려 강제 변경을
    건너뛴다. 지키려던 것은 길이가 아니라 "이전과 다른 비밀번호" 검사와
    must_change_password 가 한다.
    """


class PatCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    """어디에 쓰는 토큰인지. **폐기할 때 이것만 보고 판단하게 된다** — 그리고
    감사 기록에 이 이름이 남는다."""
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    """이 토큰으로 할 수 있는 일. 아는 범위는 `GET /auth/token-scopes` 가 준다.

    **안 주면 읽기뿐이다.** 기본값이 전권이면 「일단 만들고 나중에 좁히자」 가 되고,
    나중은 오지 않는다."""


class PatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    scopes: list[str]
    """이 토큰으로 할 수 있는 일. **목록에서 보인다** — 폐기할지 판단하는 근거가
    이름과 이것뿐이다."""
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class PatCreateResponse(BaseModel):
    token: str
    """평문은 이 응답에서 한 번만 나온다. 다시 볼 수 없다."""
    pat: PatOut


class TokenScopesOut(BaseModel):
    """이 설치가 아는 범위. **화면이 목록을 손으로 들지 않는다** — 도메인이 범위를
    더하면 화면은 아무것도 안 고쳐도 새 범위를 보여 준다."""

    scopes: list[str]


class DelegatedTokenOut(BaseModel):
    """HWAX 게이트웨이의 위임 발급 응답(`POST /api/auth/sso`, ADR 0028) — **봉투 없이.**

    게이트웨이는 응답을 깊이 뒤져 `access_token` 과 **같은 객체의** `expires_in` 을
    읽는다(Report Archive 는 `{success, data: {…}}` 봉투, TestScope 권장은 이 모양). 받은
    토큰을 최대 12시간, `expires_in` 보다 2분 먼저 버린다 — 수명 하루면 12시간마다 다시
    받는다."""

    access_token: str
    """그 사람 명의의 개인 토큰(`<slug>_pat_…`) — 사람이 화면에서 만드는 것과 같은 자격이다."""
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    """만료까지 초."""


class DelegationRevokeOut(BaseModel):
    """위임 회수(`POST /api/auth/sso/revoke`) — 그 사람의 그 client 토큰을 몇 개 끊었나."""

    revoked: int


class GatewayRefusalOut(BaseModel):
    """게이트웨이 위임을 마지막으로 거절한 일."""

    at: datetime
    status: int
    """응답 숫자 — 400 머리 없음 · 401 비밀 다름 · 403 계정 · 주소 · 404 꺼짐."""
    reason: str
    """사람 말. 거절한 프로세스가 이 진단을 답하는 프로세스면 자세한 사유, 아니면 숫자의 뜻
    (워커 · 서버가 여럿이다 — 자세한 것은 앱 로그의 `게이트웨이 위임 거절`)."""


class GatewayDelegationOut(BaseModel):
    """HWAX 게이트웨이의 사람별 위임(`POST /api/auth/sso`, ADR 0028) — 포털 로그인과 따로
    켜진다.

    게이트웨이는 위임 발급이 200 이 아니면 그 사람의 도구 호출을 **거절**한다(서비스 계정으로
    내려가지 않는다) — 그래서 「켜졌나」 와 함께 마지막으로 발급한 때 · 거절한 때를 말한다."""

    enabled: bool
    """`HEAX_SSO_SECRET` 이 있나. 없으면 `/api/auth/sso` 가 404 JSON."""
    sso_path: str
    """게이트웨이 `<접두>_SSO_URL` 의 길 — **앱 포트로 직접**(`http://<서버>:<앱 포트>` +
    이것). 접두어 없이 적는다(포털 nginx 를 안 거친다)."""
    allowed_ips: list[str]
    """`HEAX_SSO_ALLOWED_IPS` — 비면 주소를 보지 않는다(비밀만)."""
    invalid_ips: list[str]
    """그중 못 읽은 값 — 하나라도 있으면 **모든 발급을 거절한다**(오타로 제한이 풀리지
    않게)."""
    scopes: list[str]
    """위임 토큰이 받는 범위 — `read` + 등록된 쓰기 범위(관리 범위는 없다)."""
    token_seconds: int
    live_tokens: int
    """지금 살아 있는 위임 토큰 수 — 대개 쓰는 사람 수와 같다(client 마다 하나)."""
    last_issued_at: datetime | None
    """마지막으로 발급한 때 — 모든 서버 · 프로세스(DB)."""
    last_refusal: GatewayRefusalOut | None
    """마지막으로 거절한 일 — 모든 서버 · 프로세스(접근 로그)."""


class PortalStatusOut(BaseModel):
    """HWAX 포털 로그인의 상태(ADR 0028) — 시스템 관리자의 서버 화면이 본다.

    「켜졌나」 만이 아니라 **「이 서버에서 포털의 공개 키가 닿나」** 를 지금 받아 보고 말한다 —
    설정만 보면 켜짐인데, 주소가 안 닿는 것은 첫 사람이 로그인을 시도해야 드러난다.
    """

    enabled: bool
    """`PORTAL_JWKS_URL` 이 있나. 없으면 콜백이 404 JSON(포털 진단표의 「SSO 꺼짐」)."""
    jwks_url: str | None
    audience: str
    """토큰의 `aud` 로 받는 값 — 포털 타일의 `audience` 와 같아야 한다."""
    issuer: str
    launch_url: str | None
    """로그인 화면의 「HWAX 포털로 로그인」 이 가는 곳. 비면(또는 꺼져 있으면) 단추가 없다."""
    callback_path: str
    """포털 타일의 `url` 에 적을 길 — 이 설치의 접두어가 붙은 모양."""
    reachable: bool | None
    """공개 키를 지금 받았나(또는 5분 안에 받아 둔 것이 있나). 꺼져 있으면 null."""
    kids: list[str]
    """받은 서명 키의 kid — 포털이 키를 바꾸면 여기가 바뀐다."""
    fetched_at: datetime | None
    """**이 프로세스가** 마지막으로 받은 때 — 워커(프로세스)마다 따로 든다."""
    last_error: str | None
    """이 프로세스가 마지막으로 못 받은 까닭 — 뒤에 받았어도 남는다(`fetched_at` 과 견준다)."""
    last_error_at: datetime | None
    problems: list[str]
    """설정이 서로 안 맞는 것 — 접두어 없음 · 접두어와 audience 다름 · TRUST_PROXY 꺼짐 · 위임
    비밀이 짧음 · 허용 IP 를 못 읽음 등."""
    delegation: GatewayDelegationOut
    """HWAX 게이트웨이의 사람별 위임(MCP) — 포털 로그인이 꺼져 있어도 따로 켜질 수 있다."""
