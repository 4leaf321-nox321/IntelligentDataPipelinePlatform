"""계정 API 의 요청·응답 형태."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

#: 아이디 최소 길이. **이메일 형식을 강제하지 않는다** — 사내 관리자 계정은 admin
#: 처럼 짧은 아이디를 쓰고, 폐쇄망은 .local 같은 도메인을 쓴다.
_ID_FIELD = Field(min_length=3, max_length=254)
_PASSWORD_FIELD = Field(min_length=8, max_length=200)


class SignupRequest(BaseModel):
    email: str = _ID_FIELD
    password: str = _PASSWORD_FIELD
    display_name: str = Field(min_length=1, max_length=100)
    workspace_slug: str = Field(min_length=1, max_length=64)
    """희망 부서. 승인 시 이 부서의 멤버가 된다."""


class AccountSummaryOut(BaseModel):
    """활성 시스템 관리자 수.

    **1명이면 그 사람이 잠기는 순간 복구 경로가 서버 콘솔뿐이다.** 목록은 쪽
    단위라 화면이 세면 틀린다 — 서버가 센다.
    """

    active_system_admins: int
    pending: int
    """승인 대기 수. 관리 화면에 들어가야만 보이면 대기가 며칠씩 방치된다."""


class AccountWorkspaceOut(BaseModel):
    """소속 한 줄 — **이름과 경로를 함께 준다.**

    slug 만 주면 화면은 「hq」 라고 쓰게 된다. 그것은 주소지 부서 이름이 아니고,
    사람은 자기 부서를 「본사」 로 안다.
    """

    slug: str
    name: str
    path: str
    """개발본부 / 재료시험팀 — 같은 이름의 팀이 본부마다 있을 수 있다."""
    role: str
    is_home: bool
    """대표 소속인가 — 이 사람이 로그인해서 처음 서는 부서."""


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    status: str
    is_system_admin: bool
    must_change_password: bool
    home_workspace_slug: str | None
    home_workspace_name: str | None
    requested_workspace_slug: str | None
    requested_workspace_name: str | None
    memberships: list[str]
    """부서 slug 목록. 상세 역할은 부서 멤버 화면에서 다룬다."""
    workspaces: list[AccountWorkspaceOut]
    """같은 소속을 이름·경로·역할까지. 화면은 이것으로 그린다."""
    created_at: datetime
    decided_at: datetime | None
    decision_note: str | None


class ApproveRequest(BaseModel):
    workspace_slug: str | None = None
    """비우면 신청한 부서를 그대로 쓴다."""
    role: str = "member"


class RejectRequest(BaseModel):
    note: str = Field(min_length=1, max_length=500)
    """거절 사유. 메일이 없어 통보가 앱 안에서만 되므로 반드시 남긴다."""


class TokenRevokeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    """폐기 사유 — 감사 기록과 토큰 주인에게 가는 알림에 그대로 실린다. 비워도 되지만,
    비우면 주인은 「왜」 를 관리자에게 따로 물어야 한다."""


class CreateAccountRequest(BaseModel):
    """관리자가 직접 계정을 만들 때. 승인 절차 없이 바로 활성이다."""

    email: str = _ID_FIELD
    display_name: str = Field(min_length=1, max_length=100)
    workspace_slug: str = Field(min_length=1, max_length=64)
    role: str = "member"
    is_system_admin: bool = False


class HomeWorkspaceRequest(BaseModel):
    """대표 소속을 정한다.

    **비우는 길은 두지 않는다.** 대표 소속이 없으면 로그인이 소속 중 첫 부서로
    떨어지는데 그것은 사람이 정한 값이 아니다. 지우고 싶은 상황은 실제로는
    "다른 부서로 옮긴다" 이므로 그때도 새 값을 준다.
    """

    workspace_slug: str = Field(min_length=1, max_length=64)


class MembershipsRequest(BaseModel):
    """소속을 **통째로** 정한다 — 여기 적힌 부서가 소속의 전부가 된다.

    「빼기」 와 「넣기」 를 따로 받지 않는 이유: 화면은 지금 소속을 보고 고친 결과를
    보낸다. 차이를 화면이 계산하게 하면, 그 사이에 다른 관리자가 넣은 부서를 화면이
    모른 채 지운다.
    """

    workspace_slugs: list[str] = Field(min_length=1, max_length=50)
    """**비울 수 없다.** 소속 없는 계정은 로그인해도 설 자리가 없다 — 그럴 때
    하려던 일은 대개 「정지」 다."""
    home_workspace_slug: str | None = None
    """비우면 지금 대표 소속을 유지하고, 그것이 빠졌으면 첫 부서가 된다."""


class SystemAdminRequest(BaseModel):
    """시스템 관리자 권한을 주거나 뺀다.

    **한 엔드포인트에 참·거짓을 실어 보낸다.** 이 값은 상태가 아니라 권한 한
    칸이다 — 화면이 지금 값을 보고 반대를 보내면 되고, 지정과 해제가 따로 있으면
    지금 값을 모르는 쪽이 둘 중 하나를 골라야 한다.
    """

    is_system_admin: bool


class TemporaryPasswordResponse(BaseModel):
    """임시 비밀번호는 이 응답에서 한 번만 나온다.

    메일이 없어 보낼 수 없으므로 관리자가 화면에서 읽어 구두로 전달한다. 받는
    사람은 첫 로그인 때 변경이 강제된다.
    """

    account: AccountOut
    temporary_password: str
