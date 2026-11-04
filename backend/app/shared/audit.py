"""감사 기록을 남기는 **한 곳.**

오류 규약이 "오류 본문을 라우트에서 직접 만들지 않는다" 라고 정한 것과 같은
이유로 감사도 한 곳을 거친다. 라우트마다 손으로 만들면 어떤 곳은 사유를 빼먹고
어떤 곳은 대상 이름을 안 박고, 나중에 그 차이를 메울 방법이 없다.

## 무엇을 남기나

**되돌릴 수 없거나 권한이 실린 것**만이다. 값 하나 고친 것까지 남기면 그 안에서
정작 찾을 것을 못 찾는다.

## 커밋은 부르는 쪽이 한다

여기서 commit 하지 않는다. 감사 기록은 **그 변경과 같은 트랜잭션**에 있어야
한다 — 변경은 됐는데 기록이 없거나, 기록은 있는데 변경이 롤백되는 상태를 안
만든다.

## 도메인이 자기 항목을 더할 때

아래 상수 옆에 자기 것을 적는다. **과거형으로 적고**, 더하기 전에 "이걸 반년 뒤에
누가 찾을까" 를 먼저 묻는다 — 답이 없으면 안 넣는 편이 낫다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, Select, Table, func, insert, literal, select
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.audit.models import AuditEntry
from app.shared import copyin, events
from app.shared.request_context import (
    get_actor_client,
    get_actor_token,
    get_request_id,
)

# --- 공통 틀이 남기는 일 -----------------------------------------------------
#
# **과거형으로 적는다** — 일어난 일의 기록이지 명령이 아니다.

ACCOUNT_DECIDED = "account.decided"
"""가입 신청을 승인하거나 거절했다."""
ACCOUNT_CREATED = "account.created"
"""관리자가 계정을 직접 만들었다(가입 신청을 거치지 않고)."""
ACCOUNT_SUSPENDED = "account.suspended"
ACCOUNT_ACTIVATED = "account.activated"
"""정지했던 계정을 다시 켰다 — 정지와 같은 이름으로 적으면 기록만 보고는 어느 쪽인지 모른다."""
ACCOUNT_ADMIN_CHANGED = "account.admin_changed"
ACCOUNT_HOME_CHANGED = "account.home_changed"
ACCOUNT_WORKSPACES_CHANGED = "account.workspaces_changed"
ACCOUNT_DELETED = "account.deleted"
ACCOUNT_PASSWORD_RESET = "account.password_reset"
"""관리자가 임시 비밀번호로 되돌렸다 — 그 사람의 세션(로그인)도 함께 끊었다."""
ACCOUNT_TOKEN_REVOKED = "account.token_revoked"
"""시스템 관리자가 **그 사람의** 액세스 토큰(PAT) 하나를 폐기했다 — 대상은 토큰, 이름 칸에
「소유자 · 토큰 이름」, changes 에 소유자 · 앞자리 · 범위, 사유는 reason. 연동이 갑자기
401 을 받기 시작한 날 「누가 왜 끊었나」 를 답하는 줄이다."""
LOGIN_THROTTLED = "auth.login_throttled"
"""같은 계정의 실패가 문턱을 넘어 응답을 늦추기 시작했다. 실패마다 남기면 넘치므로
문턱을 넘는 순간 한 번만."""
PORTAL_LOGIN = "auth.portal_login"
"""HWAX 포털이 보증한 신원으로 로그인했다(ADR 0028) — 통로(`actor_client`)는 `hwax-portal`,
changes 에 포털의 `sub` · 무엇으로 찾았나(`matched_by`: email | sub) · `jti`. 비밀번호 로그인은
남기지 않는데 이것은 남긴다: **우리가 확인하지 않은 신원을 받아들인 일**이라, 「이 계정에 누가
어디로 들어왔나」 를 물을 날 그 근거가 여기뿐이다."""
ACCOUNT_PORTAL_REQUESTED = "account.portal_requested"
"""처음 보는 사람이 포털로 들어와 **가입 신청(승인 대기)** 이 만들어졌다 — 이름 · 아이디는
포털의 토큰에서, 비밀번호는 없다(포털로만 들어온다). 승인은 다른 신청과 같다."""
GATEWAY_DELEGATED = "auth.gateway_delegated"
"""HWAX 게이트웨이가 그 사람 명의의 개인 토큰을 받아 갔다(게이트웨이 위임 발급, ADR 0028) —
통로 `hwax-gateway`, 대상은 새 토큰, changes 에 client · 범위 · 만료 · 회수한 직전 토큰 수 ·
원격 주소. 포털 로그인처럼 **우리가 확인하지 않은 신원을 받아들인 일**이라 남긴다(게이트웨이가
12시간마다 다시 받으므로 쓰는 사람마다 하루 두 줄 남짓)."""
GATEWAY_DELEGATION_REVOKED = "auth.gateway_delegation_revoked"
"""게이트웨이가 그 사람의 위임 토큰을 회수해 갔다(`POST /api/auth/sso/revoke`)."""
WORKSPACE_DELETED = "workspace.deleted"
WORKSPACE_MOVED = "workspace.moved"
"""조직 개편 — 상위 부서나 형제 순서가 바뀌었다. **자료는 하나도 안 움직인다.**"""
WORKSPACE_REASSIGNED = "workspace.reassigned"
"""자료를 다른 부서로 통째 옮겼다. 부서 통폐합의 앞 단계 — 옮기고 나서 보관하거나
지운다. 무엇이 몇 건 옮겨졌는지가 changes 에 남는다."""


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """바뀐 것만 남긴다.

    통째로 스냅샷하면 표가 커지고 **무엇이 바뀌었는지는 오히려 안 보인다.** 안
    바뀐 값 스무 개 사이에서 바뀐 하나를 찾게 된다.
    """
    return {
        key: {"before": before.get(key), "after": after[key]}
        for key in after
        if before.get(key) != after[key]
    }


def relation_endpoints(edge: Any, src_label: str, dst_label: str) -> dict[str, Any]:
    """관계 기록에 양 끝을 **id 로** 남긴다.

    이름만 남기면 객체의 이력에서 「이 관계가 나에게 걸린 것」 을 찾을 수 없다 —
    이름은 바뀌고 겹친다.
    """
    return relation_ends(
        edge.relation,
        edge.src_object_id,
        edge.dst_object_id,
        src_label,
        dst_label,
        valid_from=getattr(edge, "valid_from", None),
        valid_to=getattr(edge, "valid_to", None),
    )


def relation_ends(
    relation: str,
    src_id: uuid.UUID,
    dst_id: uuid.UUID,
    src_label: str,
    dst_label: str,
    *,
    valid_from: date | None = None,
    valid_to: date | None = None,
) -> dict[str, Any]:
    """`relation_endpoints` 와 같은 모양 — 선을 ORM 객체 없이 넣는 자리(일괄 입력의 COPY)가
    쓴다. 모양은 여기 한 벌이다(객체 이력이 이 키로 찾는다).

    **유효 기간이 있으면 함께**(ADR 0039) — 같은 두 객체 사이에 기간을 달리한 선이 여럿일 수
    있어, 끝점 · 종류만으로는 어느 선의 기록인지 모른다. 기간이 없는 선(대부분)은 예전 모양
    그대로다."""
    out: dict[str, Any] = {
        "relation": relation,
        "src": str(src_id),
        "dst": str(dst_id),
        "src_label": src_label,
        "dst_label": dst_label,
    }
    if valid_from is not None or valid_to is not None:
        out["valid_from"] = valid_from.isoformat() if valid_from else None
        out["valid_to"] = valid_to.isoformat() if valid_to else None
    return out


def relation_state(edge: Any) -> dict[str, Any]:
    """관계를 고친 기록의 앞 · 뒤 — 속성 · 근거 · 유효 기간. `diff` 로 바뀐 것만 남긴다."""
    return {
        "properties": dict(edge.properties or {}),
        "evidence_note": edge.evidence_note or "",
        "valid_from": edge.valid_from.isoformat() if edge.valid_from else None,
        "valid_to": edge.valid_to.isoformat() if edge.valid_to else None,
    }


_QUIET = "audit_quiet"


def quiet(db: Session, on: bool = True) -> None:
    """줄마다 남기는 기록을 **잠시 멈춘다** — 백필처럼 한 묶음이 수만 줄일 때.

    ⚠️ 켜는 쪽은 **묶음 한 줄을 반드시 남긴다**(`summary=True`). 아무것도 안 남기면
       「아무도 안 했는데 몇만 건이 바뀌었다」 가 되고, 그것이 가장 설명하기 어려운 상태다.
       백필 수만 줄이 로그를 뒤덮으면 사람이 한 일을 그 사이에서 못 찾는 것도 같은 문제라,
       둘 중 하나를 고르게 한다.
    """
    if on:
        db.info[_QUIET] = True
    else:
        db.info.pop(_QUIET, None)


def record(
    db: Session,
    *,
    action: str,
    actor: User | None,
    target_table: str,
    target_id: uuid.UUID | None,
    target_label: str,
    workspace_id: uuid.UUID | None = None,
    changes: dict[str, Any] | None = None,
    reason: str | None = None,
    summary: bool = False,
) -> AuditEntry:
    """감사 기록 하나. **부르는 쪽이 커밋한다.**

    actor 가 없을 수 있다(시스템이 한 일). 그때도 남긴다 — 안 남기면 "아무도 안
    했는데 바뀌었다" 가 되고, 그것이 가장 설명하기 어려운 상태다.

    `summary` 는 **묶음 한 줄**이라는 표시다 — `quiet` 로 줄마다 남기기를 멈춘 동안에도
    이것은 남는다.
    """
    values = _values(
        action=action,
        actor=actor,
        target_table=target_table,
        target_id=target_id,
        target_label=target_label,
        workspace_id=workspace_id,
        changes=changes,
        reason=reason,
    )
    entry = AuditEntry(**values)
    if db.info.get(_QUIET) and not summary:
        # **줄마다 남기지 않는 동안이다.** 표에도 안 넣고 바깥에도 안 알린다 — 켠 쪽이
        # 묶음 한 줄을 남긴다. 만들어서 돌려주기는 한다(부르는 쪽이 값을 쓴다).
        return entry
    db.add(entry)
    _stage(db, values)
    return entry


def _values(
    *,
    action: str,
    actor: User | None,
    target_table: str,
    target_id: uuid.UUID | None,
    target_label: str,
    workspace_id: uuid.UUID | None,
    changes: dict[str, Any] | None,
    reason: str | None,
) -> dict[str, Any]:
    """기록 한 줄의 칸들 — `record` 와 `Batch` 가 **같은 것**을 쓴다(두 벌이면 갈린다)."""
    return {
        "action": action,
        "actor_id": actor.id if actor else None,
        # **그때의 이름을 박는다.** 계정이 지워지면 누가 했는지 모르게 되는데,
        # 그건 감사 로그가 존재하는 이유와 정면으로 어긋난다.
        "actor_label": (actor.display_name or actor.email) if actor else "시스템",
        # **사람과 통로를 함께 남긴다.** 소유자만 남기면 사람이 넣은 것과 스크립트가
        # 넣은 것이 구별되지 않는다 — 그리고 그 구별이 필요해지는 날은 반드시 온다.
        "actor_client": get_actor_client(),
        "actor_token": get_actor_token(),
        "target_table": target_table,
        "target_id": target_id,
        "target_label": target_label[:300],
        "workspace_id": workspace_id,
        "changes": changes or {},
        "reason": reason,
        "request_id": get_request_id(),
    }


def _stage(db: Session, values: dict[str, Any]) -> None:
    """커밋되면 바깥(웹훅 · 지켜보기)에 알린다 — 감사가 곧 「알릴 만한 변경」 의 정의다."""
    events.stage(
        db,
        events.ChangeEvent(
            action=values["action"],
            target_table=values["target_table"],
            target_id=values["target_id"],
            target_label=values["target_label"],
            workspace_id=values["workspace_id"],
            actor_id=values["actor_id"],
            actor_label=values["actor_label"],
            actor_client=values["actor_client"],
            actor_token=values["actor_token"],
            changes=values["changes"],
            reason=values["reason"],
            request_id=values["request_id"],
            at=datetime.now(UTC),
        ),
    )


class Batch:
    """줄마다의 기록을 **모았다가 한 문장으로**(COPY) 넣는다 — 줄도 알림도 `record` 와 같다.

    일괄 입력 5만 줄이면 기록도 5만 줄이다. `record` 는 줄마다 ORM 객체를 만들고, flush 가
    그것을 1,000줄짜리 `INSERT … RETURNING` 으로 묶어 보냈다 — 5만 줄에 8.2초(COPY 0.9초,
    2026-10-09 실측, `shared/copyin.py`). `record_rows` 도 빠르지만 **바깥에 줄마다 알리지
    않는다**(종류 변경용) — 일괄 입력의 줄마다 기록은 객체 이력 · 지켜보기 알림 · 웹훅이
    기대므로 그것으로 바꿔 끼우면 뜻이 달라진다. 그래서 줄은 `record` 와 같은 칸(`_values`)으로
    만들고 알림도 줄마다 같은 것을 **부를 때** 적되(`events.stage`), 표에 넣는 것만 묶는다.

    ⚠️ `write` 전에는 표에 없다 — 부르는 쪽이 커밋 전에, 그리고 **뒤에 남길 다른 기록보다
       먼저** 부른다. 차례(`seq`)는 넣는 차례라, 늦게 쓰면 묶음 한 줄(`object.import`)이 줄마다
       기록보다 앞선다. 앞서 `record` 로 남겨 세션에 있는 것은 `write` 가 먼저 flush 해 앞에
       세운다. `quiet` 동안에는 `record` 처럼 아무것도 안 남긴다.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.rows: list[dict[str, Any]] = []

    def record(
        self,
        *,
        action: str,
        actor: User | None,
        target_table: str,
        target_id: uuid.UUID | None,
        target_label: str,
        workspace_id: uuid.UUID | None = None,
        changes: dict[str, Any] | None = None,
        reason: str | None = None,
    ) -> None:
        if self.db.info.get(_QUIET):
            return
        values = _values(
            action=action,
            actor=actor,
            target_table=target_table,
            target_id=target_id,
            target_label=target_label,
            workspace_id=workspace_id,
            changes=changes,
            reason=reason,
        )
        _stage(self.db, values)
        # id 는 여기서 — COPY 는 파이썬 기본값(`uuid4`)을 안 붙인다.
        self.rows.append({"id": uuid.uuid4(), **values})

    def write(self) -> None:
        """모은 것을 넣는다 — 여러 번 불러도 된다(넣은 것은 비운다)."""
        if not self.rows:
            return
        # 세션에 아직 안 쓴 기록(`record` — 먼저 부른 것)이 이 줄들보다 **앞에** 서게 — COPY 는
        # 세션을 거치지 않아, 그대로 두면 그것이 커밋 때 뒤에 들어간다.
        self.db.flush()
        copyin.copy_rows(self.db, cast("Table", AuditEntry.__table__), self.rows)
        self.rows.clear()


def record_rows(
    db: Session,
    *,
    action: str,
    actor: User | None,
    target_table: str,
    rows: list[tuple[uuid.UUID, str, uuid.UUID | None, dict[str, Any]]],
    reason: str | None = None,
) -> None:
    """같은 일의 기록 여럿을 **한 번에** — (대상 id, 이름표, 부서, 바뀐 것) 줄마다.

    정의 하나가 객체 수백만 개를 고치는 일(종류 변경)에 쓴다. 객체마다 이력은 남기되 **바깥
    (웹훅 · 지켜보기)에는 줄마다 알리지 않는다** — 부르는 쪽이 정의 한 줄(`record`)로 알린다.
    줄마다 알리면 200만 건의 알림을 아무도 못 읽고, 그 이벤트가 커밋까지 메모리에 쌓인다
    (실측: 4.6GB). `quiet` 동안에는 남기지 않는다(`record` 와 같다).
    """
    if not rows or db.info.get(_QUIET):
        return
    actor_id = actor.id if actor else None
    actor_label = (actor.display_name or actor.email) if actor else "시스템"
    client, token, request_id = get_actor_client(), get_actor_token(), get_request_id()
    db.execute(
        insert(AuditEntry),
        [
            {
                "id": uuid.uuid4(),
                "action": action,
                "actor_id": actor_id,
                "actor_label": actor_label,
                "actor_client": client,
                "actor_token": token,
                "target_table": target_table,
                "target_id": target_id,
                "target_label": label[:300],
                "workspace_id": workspace_id,
                "changes": changes,
                "reason": reason,
                "request_id": request_id,
            }
            for target_id, label, workspace_id, changes in rows
        ],
    )


def record_select(
    db: Session,
    *,
    action: str,
    actor: User | None,
    target_table: str,
    rows: Select[Any],
    reason: str | None = None,
) -> int:
    """같은 일의 기록 여럿을 **DB 안에서 한 문장으로** — `rows` 는 (대상 id, 이름표, 부서, 바뀐
    것) 네 열을 내는 select 다. 넣은 줄 수를 돌려준다.

    `record_rows` 는 줄을 파이썬에 모아 넘긴다. 부서 통폐합 한 번이 객체 수십만 개를 옮기면 그
    목록(줄마다 dict 하나)만으로 수백 MB 이고, DB 에서 읽은 값을 그대로 다시 보내는 왕복이다 —
    여기서는 `INSERT … SELECT` 하나다. 바깥(웹훅 · 지켜보기)에는 줄마다 알리지 않는다
    (`record_rows` 와 같다 — 부르는 쪽이 한 줄 `record` 로 알린다). `quiet` 동안에는 안 남긴다.
    """
    if db.info.get(_QUIET):
        return 0
    found = list(rows.subquery().c)
    if len(found) != 4:  # pragma: no cover - 부르는 쪽의 실수
        raise ValueError("rows 는 (대상 id, 이름표, 부서, 바뀐 것) 네 열이어야 합니다")
    target_id, label, workspace_id, changes = found
    table = AuditEntry.__table__

    def fixed(name: str, value: Any) -> Any:
        return literal(value, table.c[name].type)

    names = (
        "id",
        "action",
        "actor_id",
        "actor_label",
        "actor_client",
        "actor_token",
        "target_table",
        "target_id",
        "target_label",
        "workspace_id",
        "changes",
        "reason",
        "request_id",
    )
    done = db.execute(
        insert(AuditEntry).from_select(
            list(names),
            select(
                # **id 를 DB 가 만든다** — 파이썬 기본값(`uuid4`)은 이 문장에서 한 번만
                # 불려 모든 줄이 같은 id 를 받는다.
                func.gen_random_uuid(),
                fixed("action", action),
                fixed("actor_id", actor.id if actor else None),
                fixed(
                    "actor_label", (actor.display_name or actor.email) if actor else "시스템"
                ),
                fixed("actor_client", get_actor_client()),
                fixed("actor_token", get_actor_token()),
                fixed("target_table", target_table),
                target_id,
                func.left(label, 300),
                workspace_id,
                changes,
                fixed("reason", reason),
                fixed("request_id", get_request_id()),
            ),
        )
    )
    return int(cast("CursorResult[Any]", done).rowcount or 0)
