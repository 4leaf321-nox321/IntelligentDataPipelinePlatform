"""부서 API 의 요청·응답 형태."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: URL 에 들어가므로 소문자·숫자·하이픈만 받는다. 한글 부서명은 name 이 갖는다.
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{1,49}$"


class WorkspaceOption(BaseModel):
    """가입 화면에서 희망 부서를 고르기 위한 최소 정보. 인증 없이 노출된다."""

    slug: str
    name: str
    path: str
    """개발본부 / 재료시험팀. **같은 이름의 팀이 본부마다 있을 수 있다** — 이름만
    보여 주면 신청자가 어느 쪽인지 고를 수 없다."""
    depth: int


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str
    parent_slug: str | None
    depth: int
    path: str
    sort_order: int
    is_active: bool
    created_at: datetime
    member_count: int
    my_role: str | None
    """요청한 사람의 역할. 화면이 버튼을 보일지 정하는 데 쓴다."""


class WorkspaceCreateRequest(BaseModel):
    slug: str = Field(pattern=SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=255)
    parent_slug: str | None = None


class WorkspaceUpdateRequest(BaseModel):
    """**안 보낸 것과 비운 것을 구별한다.** None 은 "안 바꿈" 이다.

    구별하지 않으면 이름만 고칠 때마다 다른 칸이 함께 초기화되고, 그 손실은
    저장한 사람 눈에 안 보인다.
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None
    """false 로 두면 보관 상태. 자료는 남기고 새 활동만 막는다(삭제하지 않는다)."""


class WorkspaceMoveRequest(BaseModel):
    """상위 부서 바꾸기. null 이면 뿌리로 올린다.

    이름 변경(PATCH)과 분리한 이유: PATCH 로 받으면 "안 바꿈" 과 "뿌리로 올림" 이
    둘 다 null 이라 구분할 수 없다.
    """

    parent_slug: str | None = None
    position: int | None = Field(default=None, ge=0)
    """형제 사이 몇 번째 자리인가(0 부터). 안 주면 끝에 붙인다.

    끌어 놓기가 이 값을 준다. 화면이 형제 순서를 제 손으로 다시 매겨 여러 번
    저장하면, 중간에 하나가 실패했을 때 **트리가 반쯤 뒤섞인 채로 남는다.**"""


class WorkspaceReorderRequest(BaseModel):
    direction: str = Field(pattern=r"^(up|down)$")


class WorkspaceReferenceOut(BaseModel):
    """이 부서를 가리키는 참조 하나. **삭제 버튼을 누르기 전에 보여 준다.**"""

    table: str
    label: str
    count: int
    blocks_delete: bool
    """지우려면 먼저 정리해야 하는가. RESTRICT 도 여기 들어간다 — DB 가 거부한다."""


class WorkspaceContentOut(BaseModel):
    """이 부서가 **가진** 것 한 종류. 옮기기 화면이 고를 목록으로 쓴다.

    `WorkspaceReferenceOut` 과 다르다 — 저기는 「가리켜서 삭제를 막는 것」 이고
    여기는 「다른 부서로 넘길 수 있는 것」 이다. 0 건도 나간다(빠지면 사람은 그
    종류가 안 옮겨지는 줄 안다)."""

    kind: str
    label: str
    count: int


class WorkspaceReassignRequest(BaseModel):
    """자료를 다른 부서로 통째 옮긴다 — 부서 통폐합의 앞 단계. 작업(`workspace_reassign`)의
    계획을 넣는다 — 옮긴 수는 적용 작업의 `result.moved`."""

    target_slug: str
    kinds: list[str] = Field(min_length=1)
    """옮길 종류. **고른 것만 옮긴다** — 멤버는 두고 객체만 넘기는 개편이 흔하다."""


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    status: str
    role: str
    joined_at: datetime


class MemberAddRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: str = "member"


class MemberRoleRequest(BaseModel):
    role: str


class WorkspaceImportRequest(BaseModel):
    """붙여 넣은 표(다른 플랫폼의 부서 정보 내보내기) — 행 그대로. `text` 를 주면 서버가
    표로 읽는다."""

    rows: list[dict[str, Any]] | None = None
    text: str | None = None
    apply: bool = False


class WorkspaceImportRowOut(BaseModel):
    row: int
    slug: str
    action: str
    label: str
    changes: list[str]
    message: str


class WorkspaceImportPlanOut(BaseModel):
    applied: bool
    rows: list[WorkspaceImportRowOut]
    errors: list[str]
    counts: dict[str, int]
