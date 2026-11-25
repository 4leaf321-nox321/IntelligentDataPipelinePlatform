"""AI 승인 위임 — 화면에서 사람이 하던 결정을 **사람이 대화에서 승인한 뒤** MCP 가 실행한다.

대상: 정의 · 레시피 버전의 사용, 계획 적용, 재개, 확인 후 진행, 허용 주소 변경, 대상 SP 타입
계획 적용. 파이프라인이 늘면 이 버튼들을 누르러 화면을 오가는 일이 하루 일과가 된다(2026-11-25
사용자 요청, ADR 0002). 그전에는 토큰(MCP) 요청을 모두 거부했다 — 원천 데이터에 심은 문구가 AI
를 움직여도 대상에 반영되지 않게 하는 보호 장치였다. 그 보호를 다음 셋으로 대신한다.

1. **범위** — 토큰에 `relay:approve` 가 있을 때만 된다. 사람이 화면([내 정보] › 액세스
   토큰)에서 한 번 선택한다. 범위가 없는 토큰은 지금처럼 거부한다. 이것이 사람의 결정이다.
2. **승인 문구** — 요청에 사람이 승인한 말(`approval`)이 있어야 한다. 서버는 그 말이 정말
   사람의 것인지 알 수 없으므로, 감사 기록 · 작업 · 이력에 그대로 남겨 나중에 대조할 수 있게
   한다. MCP 도구는 `destructiveHint` 를 달아 클라이언트가 도구 실행 전에 사람에게 한 번 더
   묻게 한다.
3. **본 것과 실행하는 것이 같아야 한다** — 계획 적용은 계획 번호가, 확인 후 진행은 중단 건수가,
   타입 계획 적용은 그 파이프라인이 올린 계획 번호가 맞을 때만. AI 가 보여 준 뒤 그 사이 계획이
   바뀌었으면 거부한다.

**인증 정보 입력은 여전히 화면에서만 한다** — 값이 대화 기록에 남는다(`secrets.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.shared import audit
from app.shared.errors import AppError, Forbidden, code

SCOPE = "relay:approve"
#: 감사 기록 · 작업에 싣는 승인 문구의 길이.
APPROVAL_MAX = 300


@dataclass(frozen=True)
class Approved:
    """누가 결정했나 — 화면의 사람이거나, 대화에서 승인한 사람(토큰)."""

    by: str
    """작업 · 이력에 남기는 이름 — 토큰이면 `홍길동 (MCP 승인)`."""
    approval: str
    """사람의 승인 문구 — 화면에서 한 것이면 빈 문자열."""
    via_token: bool


def topic(name: str) -> str:
    """'계획 적용은' · '재개는' — 끝 글자의 받침에 맞춘 주제 조사."""
    last = name[-1:] if name else ""
    final = ord(last) - 0xAC00 if "가" <= last <= "힣" else -1
    return f"{name}{'은' if final >= 0 and final % 28 else '는'}"


def via_token(request: Request) -> bool:
    return getattr(request.state, "token_scopes", None) is not None


def check(
    request: Request, user: User, *, what: str, refused: int, approval: str | None
) -> Approved:
    """토큰 요청이면 승인 범위와 승인 문구를 요구한다 — 사람 세션은 그대로 통과한다.

    `what` 은 동작의 이름('계획 적용'), `refused` 는 범위가 없을 때의 오류 번호(그 동작이 원래
    쓰던 번호 — 로그 검색이 이어지게)."""
    granted: list[str] | None = getattr(request.state, "token_scopes", None)
    if granted is None:
        return Approved(by=user.display_name, approval="", via_token=False)
    if SCOPE not in granted:
        raise Forbidden(
            code("RELAY", refused),
            f"{topic(what)} 화면에서 하거나, {SCOPE} 범위가 있는 토큰(MCP)으로 사람의 승인을 "
            f"받은 뒤 실행할 수 있습니다. 이 토큰에는 {SCOPE} 범위가 없습니다. 화면에서 "
            f"진행하거나, [내 정보]에서 {SCOPE} 범위를 선택한 토큰을 발급하세요.",
            details={"needed": SCOPE},
        )
    said = (approval or "").strip()
    if not said:
        raise AppError(
            code("RELAY", 47),
            f"{what}에는 사람의 승인 문구(approval)가 필요합니다. 실행할 내용을 사람에게 보여 "
            "주고, 사람이 승인한 말을 그대로 입력하세요.",
            status=422,
        )
    return Approved(
        by=f"{user.display_name} (MCP 승인)", approval=said[:APPROVAL_MAX], via_token=True
    )


def record(
    db: Session, user: User, approved: Approved, *, action: str, target: str, **changes: object
) -> None:
    """토큰으로 승인한 실행을 감사 기록에 남긴다 — 화면에서 한 것은 각 동작이 이미 남긴다."""
    if not approved.via_token:
        return
    audit.record(
        db,
        action=f"relay.approve.{action}",
        actor=user,
        target_table="relay_pipelines",
        target_id=None,
        target_label=target,
        changes={"approval": approved.approval, **changes},
    )
