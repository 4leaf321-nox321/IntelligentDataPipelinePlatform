"""부서 로직 — 생성·보관, 멤버와 역할.

**부서를 함부로 지우지 않는다.** is_active=false 로 보관하는 것이 기본이다. 부서에는
자료가 매달리고, 자료는 조직보다 오래 산다 — 무엇이 그 부서를 참조하는지 답할 수
있을 때만 삭제를 허용한다(references).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.jobs.models import Job
from app.modules.workspaces.models import Workspace, WorkspaceMember
from app.modules.workspaces.schemas import (
    SLUG_PATTERN,
    MemberOut,
    WorkspaceContentOut,
    WorkspaceOption,
    WorkspaceOut,
    WorkspaceReferenceOut,
)
from app.shared import audit, extensions, system_sources
from app.shared.errors import AppError, Conflict, NotFound, code
from app.shared.permissions import membership_of, workspace_by_slug
from app.shared.text import clean, compare_key

#: 부서 주소 — 만들기 화면(`schemas.SLUG_PATTERN`)과 **같은 규칙**이어야 한다. 갈리면 붙여
#: 넣기로는 되는데 화면으로는 안 만들어지는 slug 가 생긴다.
SLUG_RE = re.compile(SLUG_PATTERN)

ROLES = ("member", "manager")


def member_count(db: Session, workspace_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.workspace_id == workspace_id)
        )
        or 0
    )


# --- 트리 --------------------------------------------------------------------


def ordered_tree(db: Session) -> list[tuple[Workspace, int, str]]:
    """(부서, 깊이, 경로) 를 **트리 순서**로. 화면은 이 순서 그대로 그린다.

    순서를 서버가 정하는 이유: 화면이 평면 목록을 받아 스스로 트리를 세우면, 부서
    선택기·관리 화면·가입 화면이 각자 다른 정렬을 갖게 된다. 조직도의 순서는 한
    곳에서만 정해져야 한다.

    부모가 없는(끊어진) 행도 뿌리로 취급해 반드시 내보낸다. 데이터가 이상해도
    **화면에서 사라지는 것이 가장 나쁘다** — 사라지면 고칠 수도 없다.
    """
    rows = list(db.scalars(select(Workspace)))
    by_parent: dict[uuid.UUID | None, list[Workspace]] = {}
    known = {row.id for row in rows}
    for row in rows:
        parent = row.parent_id if row.parent_id in known else None
        by_parent.setdefault(parent, []).append(row)
    for siblings in by_parent.values():
        siblings.sort(key=lambda item: (item.sort_order, item.name))

    out: list[tuple[Workspace, int, str]] = []

    def walk(parent: uuid.UUID | None, depth: int, prefix: str) -> None:
        for node in by_parent.get(parent, []):
            path = f"{prefix} / {node.name}" if prefix else node.name
            out.append((node, depth, path))
            walk(node.id, depth + 1, path)

    walk(None, 0, "")

    # 순환이 생겨 walk 가 못 닿은 행이 있으면 뒤에 붙인다. 조용히 빠뜨리지 않는다.
    reached = {node.id for node, _, _ in out}
    for row in rows:
        if row.id not in reached:
            out.append((row, 0, f"{row.name} (연결 끊김)"))
    return out


def paths(db: Session) -> dict[uuid.UUID, str]:
    """부서 id → 「개발본부 / 재료시험팀」. 이름만 보여 주면 같은 이름의 팀이 본부마다
    있을 때 어느 쪽인지 알 수 없다."""
    return {node.id: path for node, _, path in ordered_tree(db)}


def _descendant_ids(db: Session, workspace_id: uuid.UUID) -> set[uuid.UUID]:
    """자신 + 모든 하위. 부모를 바꿀 때 순환을 막는 데 쓴다."""
    rows = list(db.scalars(select(Workspace)))
    by_parent: dict[uuid.UUID | None, list[Workspace]] = {}
    for row in rows:
        by_parent.setdefault(row.parent_id, []).append(row)

    found = {workspace_id}
    stack = [workspace_id]
    while stack:
        current = stack.pop()
        for child in by_parent.get(current, []):
            if child.id not in found:
                found.add(child.id)
                stack.append(child.id)
    return found


def workspace_out(
    db: Session,
    workspace: Workspace,
    viewer: User,
    *,
    depth: int | None = None,
    path: str | None = None,
    parent_slug: str | None = None,
) -> WorkspaceOut:
    """트리 위치는 **안 주면 스스로 찾는다.**

    목록은 이미 한 번 순회했으니 그 값을 넘겨 준다. 단건 응답(만들기·옮기기)은
    넘길 값이 없는데, 그때 깊이 0·경로=이름으로 두면 방금 자식으로 만든 부서가
    화면에서 뿌리로 보인다.
    """
    if depth is None or path is None:
        for node, node_depth, node_path in ordered_tree(db):
            if node.id == workspace.id:
                depth, path = node_depth, node_path
                if node.parent_id:
                    parent = db.get(Workspace, node.parent_id)
                    parent_slug = parent.slug if parent else None
                break

    membership = membership_of(db, workspace_id=workspace.id, user_id=viewer.id)
    return WorkspaceOut(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        description=workspace.description,
        parent_slug=parent_slug,
        depth=depth or 0,
        path=path or workspace.name,
        sort_order=workspace.sort_order,
        is_active=workspace.is_active,
        created_at=workspace.created_at,
        member_count=member_count(db, workspace.id),
        my_role=membership.role if membership else None,
    )


def options(db: Session) -> list[WorkspaceOption]:
    """가입 신청 화면용. 인증 없이 나가므로 이름과 주소만 담는다."""
    return [
        WorkspaceOption(slug=node.slug, name=node.name, path=path, depth=depth)
        for node, depth, path in ordered_tree(db)
        if node.is_active
    ]


def list_for(db: Session, user: User, *, all_workspaces: bool) -> list[WorkspaceOut]:
    """내 소속만, 또는 전체(시스템 관리자)."""
    mine = set(
        db.scalars(
            select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user.id)
        )
    )
    out: list[WorkspaceOut] = []
    for node, depth, path in ordered_tree(db):
        if not all_workspaces and not user.is_system_admin and node.id not in mine:
            continue
        parent = db.get(Workspace, node.parent_id) if node.parent_id else None
        out.append(
            workspace_out(
                db,
                node,
                user,
                depth=depth,
                path=path,
                parent_slug=parent.slug if parent else None,
            )
        )
    return out


def create(
    db: Session,
    *,
    slug: str,
    name: str,
    description: str,
    creator: User,
    parent_slug: str | None,
) -> Workspace:
    if db.scalar(select(Workspace).where(Workspace.slug == slug)) is not None:
        raise Conflict(code("WORKSPACES", 4), f"이미 있는 부서 주소입니다: {slug}")

    parent = workspace_by_slug(db, parent_slug) if parent_slug else None
    # 형제 끝에 붙인다. 순서는 사람이 나중에 바꾼다.
    last = db.scalar(
        select(func.max(Workspace.sort_order)).where(
            Workspace.parent_id == (parent.id if parent else None)
        )
    )
    workspace = Workspace(
        slug=slug,
        name=clean(name),
        description=clean(description),
        parent_id=parent.id if parent else None,
        sort_order=(last or 0) + 1,
    )
    db.add(workspace)
    db.flush()

    # **만든 사람이 그 부서의 관리자로 들어간다.** 안 그러면 방금 만든 부서에
    # 아무도 손댈 수 없고, 첫 멤버를 넣는 것조차 막힌다.
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=creator.id, role="manager"))
    db.commit()
    db.refresh(workspace)
    return workspace


def update(
    db: Session,
    *,
    slug: str,
    name: str | None,
    description: str | None,
    is_active: bool | None,
) -> Workspace:
    """**안 보낸 것과 비운 것을 구별한다.** None 은 "안 바꿈" 이다 — 구별하지 않으면
    이름만 고칠 때마다 보관 상태가 함께 초기화된다."""
    workspace = workspace_by_slug(db, slug)
    if name is not None:
        workspace.name = clean(name)
    if description is not None:
        workspace.description = clean(description)
    if is_active is not None:
        workspace.is_active = is_active
    db.commit()
    db.refresh(workspace)
    return workspace


def move(
    db: Session,
    *,
    slug: str,
    parent_slug: str | None,
    position: int | None = None,
    actor: User | None = None,
) -> Workspace:
    """상위 부서 바꾸기 — 조직 개편. `position` 을 주면 **형제 사이 자리까지** 정한다.

    **자료는 하나도 안 움직인다.** 자료는 부서 id 를 가리키고, 트리를 옮겨도 그 id 는
    그대로다. 조직 식별자를 데이터에 직접 박았다면 개편에 대응할 수단이 없다.

    끌어 놓기가 자리를 한 번에 정하는 이유: 화면이 형제들의 순서를 제 손으로 다시
    매겨 PATCH 를 여러 번 보내면, 중간에 하나가 실패했을 때 **트리가 반쯤 뒤섞인
    채로 남는다.** 여기서 한 트랜잭션으로 끝낸다.
    """
    workspace = workspace_by_slug(db, slug)
    before_parent = db.get(Workspace, workspace.parent_id) if workspace.parent_id else None
    if parent_slug is None:
        parent_id = None
    else:
        parent = workspace_by_slug(db, parent_slug)
        # **자기 하위로는 못 간다.** 막지 않으면 트리에서 통째로 사라지고, 화면에
        # 안 나오니 되돌릴 수도 없다.
        if parent.id in _descendant_ids(db, workspace.id):
            raise AppError(
                code("WORKSPACES", 5),
                "자기 자신이나 하위 부서 아래로는 옮길 수 없습니다.",
                status=400,
            )
        parent_id = parent.id
    moved_out = parent_id != workspace.parent_id
    workspace.parent_id = parent_id

    siblings = sorted(
        (
            item
            for item in db.scalars(select(Workspace).where(Workspace.parent_id == parent_id))
            if item.id != workspace.id
        ),
        key=lambda item: (item.sort_order, item.name),
    )
    if position is None:
        # 자리를 안 주면 **끝에 붙인다.** 옛 자리의 sort_order 를 들고 오면 새 형제들
        # 사이 아무 데나 끼어드는데, 그것은 아무도 시키지 않은 순서다.
        workspace.sort_order = (siblings[-1].sort_order + 1) if siblings else 0
    else:
        index = max(0, min(position, len(siblings)))
        siblings.insert(index, workspace)
        # **자리를 통째로 다시 매긴다** — 옛 데이터에 같은 값이 여럿이면 끼워 넣기만
        # 해서는 순서가 안 바뀐 것처럼 보인다.
        for order, item in enumerate(siblings):
            item.sort_order = order
    if moved_out or position is not None:
        audit.record(
            db,
            action=audit.WORKSPACE_MOVED,
            actor=actor,
            target_table="workspaces",
            target_id=workspace.id,
            target_label=workspace.name,
            workspace_id=workspace.id,
            changes=audit.diff(
                {"parent": before_parent.slug if before_parent else None},
                {"parent": parent_slug},
            ),
        )
    db.commit()
    db.refresh(workspace)
    return workspace


def reorder(db: Session, *, slug: str, direction: str) -> Workspace:
    """형제 사이 순서. 조직도 순서는 이름순도 생성순도 아니다 — 사람이 정한다."""
    workspace = workspace_by_slug(db, slug)
    siblings = sorted(
        db.scalars(select(Workspace).where(Workspace.parent_id == workspace.parent_id)),
        key=lambda item: (item.sort_order, item.name),
    )
    index = next(i for i, item in enumerate(siblings) if item.id == workspace.id)
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(siblings):
        siblings[index], siblings[target] = siblings[target], siblings[index]
        # **자리를 통째로 다시 매긴다.** 두 값만 맞바꾸면 옛 데이터에 같은 값이
        # 여럿일 때 순서가 안 바뀐 것처럼 보인다.
        for position, item in enumerate(siblings):
            item.sort_order = position
        db.commit()
    db.refresh(workspace)
    return workspace


# --- 삭제 전에 무엇이 걸려 있나 ----------------------------------------------


def _own_references(
    db: Session, workspace_id: uuid.UUID
) -> list[extensions.WorkspaceReference]:
    """부서 모듈 자신이 아는 참조. 도메인 것은 레지스트리가 더한다."""
    children = (
        db.scalar(
            select(func.count())
            .select_from(Workspace)
            .where(Workspace.parent_id == workspace_id)
        )
        or 0
    )
    return [
        extensions.WorkspaceReference(
            table="workspace_members",
            label="멤버",
            count=member_count(db, workspace_id),
            blocks_delete=False,
        ),
        extensions.WorkspaceReference(
            table="workspaces",
            label="하위 부서",
            count=children,
            # RESTRICT — DB 가 거부한다. 먼저 옮기지 않으면 삭제가 500 으로 실패한다.
            blocks_delete=True,
        ),
    ]


def references(db: Session, *, slug: str) -> list[WorkspaceReferenceOut]:
    """무엇이 이 부서를 가리키는가. **삭제 확인 화면이 먼저 부른다.**

    누르기 전에 아는 것이 이 틀의 무늬다 — 지우고 나서 "자료 12건이 사라졌다" 를
    알게 되면 되돌릴 방법이 없다.

    도메인이 자기 표를 `extensions.register_workspace_reference` 로 등록한다.
    **등록하지 않으면 그 표는 여기 안 뜨고**, 사람은 아무것도 안 걸린 줄 안다.
    """
    workspace = workspace_by_slug(db, slug)
    found = [
        one for one in _own_references(db, workspace.id) if one.count
    ] + extensions.workspace_references(db, workspace.id)
    return [
        WorkspaceReferenceOut(
            table=one.table,
            label=one.label,
            count=one.count,
            blocks_delete=one.blocks_delete,
        )
        for one in found
    ]


def delete(db: Session, *, slug: str, actor: User) -> None:
    """부서를 지운다. 막는 참조가 하나라도 있으면 거절한다.

    **보관이 여전히 기본 수단이다.** 삭제는 잘못 만든 부서처럼 자료가 아예 없는
    경우를 위한 것이다.
    """
    workspace = workspace_by_slug(db, slug)
    running = pending_reassign(db, workspace)
    if running is not None:
        raise Conflict(
            code("WORKSPACES", 17),
            "이 부서의 자료 이동이 아직 끝나지 않았습니다 — 「작업」 화면에서 끝난 것을 "
            "확인한 뒤에 삭제하세요.",
            details={"job_id": str(running.id)},
        )
    blocking = [one for one in references(db, slug=slug) if one.blocks_delete]
    if blocking:
        detail = ", ".join(f"{one.label} {one.count}건" for one in blocking)
        raise Conflict(
            code("WORKSPACES", 6),
            f"이 부서를 가리키는 것이 남아 있습니다({detail}). 먼저 옮기거나 정리하세요.",
        )
    audit.record(
        db,
        action=audit.WORKSPACE_DELETED,
        actor=actor,
        target_table="workspaces",
        target_id=workspace.id,
        target_label=workspace.name,
    )
    db.delete(workspace)
    db.commit()


# --- 자료 옮기기(부서 통폐합) ------------------------------------------------


def _move_members(db: Session, source_id: uuid.UUID, target_id: uuid.UUID) -> int:
    """멤버십을 옮긴다. **양쪽에 이미 있는 사람은 원본 쪽만 지운다** — 짝이 유일해야
    하고(uq_workspace_members_pair), 옮기다 막히면 통폐합 전체가 멈춘다.

    역할은 높은 쪽을 남긴다. 대상 부서에서 관리자였던 사람을 멤버로 낮추면, 통폐합
    직후에 그 부서를 고칠 수 있는 사람이 줄어든다.
    """
    existing = {
        row.user_id: row
        for row in db.scalars(
            select(WorkspaceMember).where(WorkspaceMember.workspace_id == target_id)
        )
    }
    moved = 0
    for row in list(
        db.scalars(select(WorkspaceMember).where(WorkspaceMember.workspace_id == source_id))
    ):
        already = existing.get(row.user_id)
        if already is not None:
            if row.role == "manager":
                already.role = "manager"
            db.delete(row)
        else:
            row.workspace_id = target_id
            existing[row.user_id] = row
        moved += 1
    # 대표 소속이 사라지는 부서를 가리키고 있으면 함께 옮긴다. FK 가 SET NULL 이라
    # 안 옮기면 삭제 순간 조용히 비고, 그 사람은 다음 로그인에서 아무 데도 안 선다.
    db.execute(
        sa_update(User)
        .where(User.home_workspace_id == source_id)
        .values(home_workspace_id=target_id)
    )
    return moved


def own_content(db: Session, workspace_id: uuid.UUID) -> list[extensions.WorkspaceContent]:
    """부서 모듈 자신이 옮길 수 있는 것. 도메인 것은 레지스트리가 더한다."""
    return [
        extensions.WorkspaceContent(
            kind="members",
            label="멤버",
            count=member_count(db, workspace_id),
            move=_move_members,
        )
    ]


def _contents(db: Session, workspace_id: uuid.UUID) -> list[extensions.WorkspaceContent]:
    return own_content(db, workspace_id) + extensions.workspace_contents(db, workspace_id)


def contents(db: Session, *, slug: str) -> list[WorkspaceContentOut]:
    """이 부서가 **가진** 것 — 옮기기 화면이 먼저 부른다.

    삭제 확인의 `references` 와 다르다. 저기는 「이 부서를 가리켜서 삭제를 막는 것」
    이고, 여기는 「다른 부서로 넘길 수 있는 것」 이다. 넘길 수 없는 참조(관계 선·
    속성 값)는 여기 안 뜬다 — 자동으로 바꾸면 담당 부서가 사람 모르게 바뀐다.
    """
    workspace = workspace_by_slug(db, slug)
    return [
        WorkspaceContentOut(kind=one.kind, label=one.label, count=one.count)
        for one in _contents(db, workspace.id)
    ]


#: 통폐합의 작업 종류 — `jobs/kinds.py` 가 이 이름으로 등록한다.
REASSIGN_KIND = "workspace_reassign"


def _reassign_parts(
    db: Session, *, slug: str, target_slug: str, kinds: list[str]
) -> tuple[Workspace, Workspace, list[extensions.WorkspaceContent]]:
    """(원본, 대상, 고른 종류) — 같은 부서 · 모르는 종류는 여기서 거절한다. 고른 종류는 등록된
    차례 그대로(멤버가 먼저)."""
    source = workspace_by_slug(db, slug)
    target = workspace_by_slug(db, target_slug)
    if source.id == target.id:
        raise AppError(code("WORKSPACES", 14), "같은 부서로는 옮길 수 없습니다.", status=400)
    available = _contents(db, source.id)
    known = {one.kind for one in available}
    unknown = [one for one in kinds if one not in known]
    if unknown:
        raise AppError(
            code("WORKSPACES", 15),
            f"옮길 수 없는 종류입니다: {', '.join(unknown)}. "
            f"이 설치가 아는 것: {', '.join(one.kind for one in available)}",
            status=422,
        )
    return source, target, [one for one in available if one.kind in kinds]


def check_reassign(db: Session, *, slug: str, target_slug: str, kinds: list[str]) -> None:
    """**넣는 순간에** 본다 — 없는 부서 · 같은 부서 · 모르는 종류를 몇 분 뒤 작업의 「실패」 가
    아니라 지금 거절한다. 무엇이 몇 건이고 무엇이 막히나는 계획(작업)이 센다."""
    _reassign_parts(db, slug=slug, target_slug=target_slug, kinds=kinds)


def _blockers(
    db: Session,
    source: Workspace,
    target: Workspace,
    picked: list[extensions.WorkspaceContent],
) -> list[str]:
    out: list[str] = []
    for one in picked:
        if one.check is not None:
            out.extend(
                f"{one.label}: {reason}" for reason in one.check(db, source.id, target.id)
            )
    return out


def _reassign_fingerprint(
    source: Workspace,
    target: Workspace,
    picked: list[extensions.WorkspaceContent],
    blockers: list[str],
) -> str:
    """계획의 지문 — 무엇을 어디서 어디로 몇 건, 무엇이 막혔나. 미리 본 뒤 원본 부서에 객체가
    들어오거나 나가면 수가 달라지고, 그러면 적용하지 않는다(다시 미리 본다)."""
    body = {
        "source": str(source.id),
        "target": str(target.id),
        "items": [(one.kind, one.count) for one in picked],
        "blockers": blockers,
    }
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _reassign_out(
    source: Workspace,
    target: Workspace,
    picked: list[extensions.WorkspaceContent],
    blockers: list[str],
    *,
    fingerprint: str,
    moved: dict[str, int] | None = None,
) -> dict[str, Any]:
    """작업 결과(`jobs.result`) — 작업 화면 · 통폐합 창이 같은 모양을 그린다.

    `counts` 를 쓰지 않는다 — 작업의 공통 판정(홈의 「적용 대기」 · 알림)은 `counts` 를 일괄
    입력의 새로 · 고침 · 오류로 읽는다. 그 이름이면 옮길 것이 있어도 「바뀌는 것 없음」 으로
    빠진다(사진 일괄 업로드의 `tally` 와 같은 까닭). 적용할 것이 있나는 `ok` 가 말한다."""
    applied = moved is not None
    items: list[dict[str, Any]] = []
    for one in picked:
        item: dict[str, Any] = {"kind": one.kind, "label": one.label, "count": one.count}
        if moved is not None:
            item["moved"] = moved.get(one.kind, 0)
        items.append(item)
    total = sum(moved.values()) if moved is not None else sum(one.count for one in picked)
    shown = " · ".join(
        f"{one['label']} {int(one['moved'] if applied else one['count']):,}"
        for one in items
        if (one["moved"] if applied else one["count"])
    )
    head = "적용 — 이동" if applied else "계획 — 이동할 것"
    summary = f"{head}: {shown or '없음'} ({source.name} → {target.name})"
    if blockers:
        summary += f" · 막힘 {len(blockers)}"
    return {
        "reassign": True,
        "applied": applied,
        "ok": not blockers and (applied or total > 0),
        "source_slug": source.slug,
        "source_name": source.name,
        "target_slug": target.slug,
        "target_name": target.name,
        "items": items,
        "total": total,
        "moved": moved or {},
        "errors": blockers,
        "summary": summary,
        "fingerprint": fingerprint,
    }


def plan_reassign(
    db: Session, *, slug: str, target_slug: str, kinds: list[str]
) -> dict[str, Any]:
    """통폐합의 **계획** — 무엇이 몇 건 옮겨지나 · 겹쳐서 막히는 것. 아무것도 바꾸지 않는다."""
    source, target, picked = _reassign_parts(
        db, slug=slug, target_slug=target_slug, kinds=kinds
    )
    blockers = _blockers(db, source, target, picked)
    mark = _reassign_fingerprint(source, target, picked, blockers)
    return _reassign_out(source, target, picked, blockers, fingerprint=mark)


def reassign(
    db: Session,
    *,
    slug: str,
    target_slug: str,
    kinds: list[str],
    actor: User,
    progress: Callable[[str, int, int], None] | None = None,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    """자료를 다른 부서로 통째 옮긴다 — **부서 통폐합의 앞 단계.**

    옮기고 나서 원본을 보관하거나 지운다. 삭제만 있고 이 길이 없으면, 없어지는 부서의
    자료를 살리는 방법이 「하나씩 손으로 고치기」 뿐이다 — 그 일은 아무도 끝내지 못하고,
    결국 쓰지 않는 부서가 목록에 영원히 남는다.

    **고른 종류만 옮기고, 하나라도 실패하면 아무것도 안 옮긴다.** 절반만 옮겨진 부서는
    어디까지 됐는지 화면으로 알 수 없다. 그래서 **커밋하지 않는다** — 작업
    (`workspace_reassign`)의 본 트랜잭션 하나에서 돌고 워커가 끝에서 한 번 커밋한다. 취소 ·
    빼앗김(`progress` 가 던진다) · 실패면 워커가 롤백해 아무것도 안 옮겨진다.

    작업인 이유: 한 부서 객체 20만 개면 90초 남짓이라(대부분 옮기는 갱신, 2026-10-08 실측)
    요청 안에서 돌면 브라우저 · 이중화의 nginx(메인 서버)가 먼저 끊었다(2026-10-10).

    `fingerprint` 를 주면 지금 계획이 미리 본 계획과 같을 때만 옮긴다.
    """
    source, target, picked = _reassign_parts(
        db, slug=slug, target_slug=target_slug, kinds=kinds
    )
    blockers = _blockers(db, source, target, picked)
    mark = _reassign_fingerprint(source, target, picked, blockers)
    if fingerprint and mark != fingerprint:
        raise Conflict(
            code("JOBS", 20),
            "미리 본 것과 달라졌습니다 — 그 사이에 원본 부서의 자료가 바뀌었습니다. 아무것도 "
            "옮기지 않았으니 다시 미리 보고 적용하세요.",
        )
    if blockers:
        raise Conflict(code("WORKSPACES", 16), " / ".join(blockers))
    total = sum(one.count for one in picked)
    done = 0
    moved: dict[str, int] = {}
    for one in picked:
        stage = f"{one.label} 이동"
        if progress is not None:
            progress(stage, done, total)
        if one.move_steps is not None and progress is not None:
            step = _step(progress, stage, done, total)
            count = one.move_steps(db, source.id, target.id, actor, step)
        elif one.move_by is not None:
            # 누가 옮기는지를 받는 종류는 그쪽으로 — 옮긴 것마다 기록에 사람이 박힌다.
            count = one.move_by(db, source.id, target.id, actor)
        else:
            count = one.move(db, source.id, target.id)
        moved[one.kind] = count
        done += count
    if progress is not None:
        progress("기록", done, total)
    audit.record(
        db,
        action=audit.WORKSPACE_REASSIGNED,
        actor=actor,
        target_table="workspaces",
        target_id=source.id,
        target_label=source.name,
        workspace_id=source.id,
        changes={"target": target.slug, "moved": moved},
        reason=f"{source.name} → {target.name}",
    )
    db.flush()
    return _reassign_out(source, target, picked, blockers, fingerprint=mark, moved=moved)


def _step(
    progress: Callable[[str, int, int], None], stage: str, before: int, total: int
) -> extensions.MoveProgress:
    """한 종류의 덩어리 진행 → 통폐합 전체의 진행(앞 종류에서 옮긴 수를 더해)."""

    def step(count: int, _all: int) -> None:
        progress(stage, before + count, total)

    return step


def pending_reassign(db: Session, workspace: Workspace) -> Job | None:
    """이 부서가 원본이나 대상인 **안 끝난** 통폐합 작업 — 있으면 그 부서를 지우지 않는다.

    통폐합이 작업이 되면서(2026-10-10) 「옮기기」 를 누른 뒤 곧장 「삭제」 를 누를 수 있게
    됐다. 대기 중인 작업이 멤버를 옮기기 전에 원본을 지우면 멤버십은 부서와 함께 사라지고
    (막는 참조가 아니다), 작업은 뒤늦게 「부서를 찾을 수 없습니다」 로 실패한다."""
    return db.scalar(
        select(Job)
        .where(
            Job.kind == REASSIGN_KIND,
            Job.status.in_(("queued", "running")),
            or_(
                Job.params["slug"].astext == workspace.slug,
                Job.params["target_slug"].astext == workspace.slug,
            ),
        )
        .limit(1)
    )


# --- 멤버 --------------------------------------------------------------------


def members(db: Session, *, workspace: Workspace) -> list[MemberOut]:
    rows = db.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace.id)
        .order_by(User.display_name)
    ).all()
    return [
        MemberOut(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            status=user.status,
            role=member.role,
            joined_at=member.created_at,
        )
        for member, user in rows
    ]


def _member_out(db: Session, member: WorkspaceMember) -> MemberOut:
    user = db.get(User, member.user_id)
    if user is None:  # pragma: no cover - FK 가 막는다
        raise NotFound(code("WORKSPACES", 7), "계정을 찾을 수 없습니다.")
    return MemberOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        role=member.role,
        joined_at=member.created_at,
    )


def add_member(db: Session, *, workspace: Workspace, email: str, role: str) -> MemberOut:
    if role not in ROLES:
        raise AppError(code("WORKSPACES", 8), f"모르는 역할입니다: {role}", status=400)

    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise NotFound(code("WORKSPACES", 9), f"그 아이디의 계정이 없습니다: {email}")

    existing = membership_of(db, workspace_id=workspace.id, user_id=user.id)
    if existing is not None:
        raise Conflict(code("WORKSPACES", 10), "이미 이 부서의 멤버입니다.")

    member = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role)
    db.add(member)
    db.commit()
    db.refresh(member)
    return _member_out(db, member)


def manager_count(db: Session, workspace_id: uuid.UUID) -> int:
    """이 부서의 관리자 수. **마지막 한 명을 빼거나 강등하지 않으려고** 센다 —
    계정 화면의 소속 변경도 같은 셈을 쓴다."""
    return (
        db.scalar(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == "manager",
            )
        )
        or 0
    )


def set_role(db: Session, *, workspace: Workspace, user_id: uuid.UUID, role: str) -> MemberOut:
    if role not in ROLES:
        raise AppError(code("WORKSPACES", 8), f"모르는 역할입니다: {role}", status=400)

    member = membership_of(db, workspace_id=workspace.id, user_id=user_id)
    if member is None:
        raise NotFound(code("WORKSPACES", 11), "이 부서의 멤버가 아닙니다.")

    # **마지막 관리자를 강등하지 않는다.** 그러면 그 부서는 아무도 못 고치는
    # 상태가 되고, 복구는 시스템 관리자를 찾아가는 길밖에 없다.
    if member.role == "manager" and role != "manager" and manager_count(db, workspace.id) <= 1:
        raise Conflict(
            code("WORKSPACES", 12),
            "부서의 마지막 관리자입니다. 다른 사람을 관리자로 올린 뒤에 바꾸세요.",
        )

    member.role = role
    db.commit()
    db.refresh(member)
    return _member_out(db, member)


def remove_member(db: Session, *, workspace: Workspace, user_id: uuid.UUID) -> None:
    member = membership_of(db, workspace_id=workspace.id, user_id=user_id)
    if member is None:
        raise NotFound(code("WORKSPACES", 11), "이 부서의 멤버가 아닙니다.")
    if member.role == "manager" and manager_count(db, workspace.id) <= 1:
        raise Conflict(
            code("WORKSPACES", 12),
            "부서의 마지막 관리자입니다. 다른 사람을 관리자로 올린 뒤에 빼세요.",
        )
    db.delete(member)
    db.commit()


# --- 내보내기 ----------------------------------------------------------------

#: 부서 정보 CSV 의 컬럼. **순서까지 고정한다** — 사람이 엑셀에서 두 파일을 나란히
#: 놓고 본다. 한 칸이라도 어긋나면 그 대조는 눈으로 못 한다.
EXPORT_HEADER = (
    "slug",
    "name",
    "parent_slug",
    "parent_name",
    "depth",
    "path",
    "status",
    "description",
    "sort_order",
    "member_count",
    "managers",
    "created_at",
)


def export_rows(db: Session) -> list[list[object]]:
    """부서 정보를 CSV 행으로. **트리 순서 그대로** — 화면과 같은 순서다."""
    rows = ordered_tree(db)
    by_id = {row.id: row for row, _, _ in rows}

    counts = {
        workspace_id: int(total)
        for workspace_id, total in db.execute(
            select(WorkspaceMember.workspace_id, func.count(WorkspaceMember.id)).group_by(
                WorkspaceMember.workspace_id
            )
        )
    }
    managers: dict[uuid.UUID, list[str]] = {}
    for workspace_id, label in db.execute(
        select(WorkspaceMember.workspace_id, User.display_name)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.role == "manager")
        .order_by(User.display_name)
    ):
        managers.setdefault(workspace_id, []).append(label)

    out: list[list[object]] = []
    for workspace, depth, path in rows:
        parent = by_id.get(workspace.parent_id) if workspace.parent_id else None
        out.append(
            [
                workspace.slug,
                workspace.name,
                parent.slug if parent else "",
                parent.name if parent else "",
                depth,
                # 화면은 " / " 로 잇는다. **파일은 " > " 로 낸다** — 사내 다른
                # 시스템의 조직 CSV 가 그 규약이고, 받는 쪽 규약을 따르는 편이
                # 대조를 눈으로 할 수 있게 한다.
                path.replace(" / ", " > "),
                "active" if workspace.is_active else "archived",
                workspace.description or "",
                workspace.sort_order,
                counts.get(workspace.id, 0),
                "; ".join(managers.get(workspace.id, [])),
                workspace.created_at.isoformat() if workspace.created_at else "",
            ]
        )
    return out


# --- 온톨로지가 부서를 비추는 길 --------------------------------------------


def _system_ref(node: Workspace, path: str) -> system_sources.SystemRef:
    return system_sources.SystemRef(
        id=node.id, key=node.slug, label=node.name, hint=path, active=node.is_active
    )


def _system_search(
    db: Session, _viewer: User, q: str | None, limit: int, offset: int
) -> tuple[list[system_sources.SystemRef], int]:
    """**트리 순서 그대로.** 부서 선택기가 어디서 열리든 같은 차례여야 한다.

    보는 사람의 소속과 무관하게 전부 준다 — 「담당 부서」 를 고르려면 남의 부서도
    골라야 하고, 부서 이름은 가입 화면(`/options`)이 이미 로그인 없이 보여 준다.
    """
    needle = compare_key(q) if q else ""
    rows = [
        _system_ref(node, path)
        for node, _depth, path in ordered_tree(db)
        if not needle
        or needle in compare_key(node.name)
        or needle in compare_key(node.slug)
        or needle in compare_key(path)
    ]
    return rows[offset : offset + limit], len(rows)


def _system_lookup(
    db: Session, ids: list[uuid.UUID]
) -> dict[uuid.UUID, system_sources.SystemRef]:
    if not ids:
        return {}
    paths = {node.id: path for node, _depth, path in ordered_tree(db)}
    found = db.scalars(select(Workspace).where(Workspace.id.in_(ids)))
    return {row.id: _system_ref(row, paths.get(row.id, row.name)) for row in found}


def _system_list_all(db: Session) -> list[system_sources.SystemRef]:
    return [_system_ref(node, path) for node, _depth, path in ordered_tree(db)]


#: `kind_class='system'` 타입이 `system_source='workspace'` 로 가리키는 원 표.
SYSTEM_SOURCE = system_sources.SystemSource(
    key="workspace",
    label="부서",
    search=_system_search,
    lookup=_system_lookup,
    list_all=_system_list_all,
)


# --- 붙여 넣어 추가 — 다른 플랫폼(ReportArchive 등)의 부서 정보 내보내기를 그대로 ------------
#
# 내보내기 CSV 의 열 이름이 그대로 열쇠다(slug·name·parent_slug·status·description·sort_order).
# ReportArchive 것에는 kind·external_view_default · restricted 같은 열이 더 있는데 모르는 열은
# **무시한다** — 부서 정보는 정의가 고정돼 있어 「모르는 열 = 오타」 가 아니라 「저쪽에만 있는
# 열」 이다. 다만 personal(개인 공간)은 부서가 아니니 건너뛴다.
#
# 규칙은 객체 일괄 입력과 같다: 계획 먼저, 전부 아니면 무, 같은 slug 면 고침, 빈 칸은 안
# 건드림. 상위 부서는 같은 표 안의 것이어도 된다 — 먼저 전부 만들고 뒤에 잇는다.

IMPORT_COLUMNS = (
    "slug",
    "name",
    "parent_slug",
    "status",
    "description",
    "sort_order",
)


@dataclass
class ImportRow:
    row: int
    slug: str
    action: str
    """create · update · unchanged · skip · error."""
    label: str = ""
    changes: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class ImportPlan:
    rows: list[ImportRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out = {"create": 0, "update": 0, "unchanged": 0, "skip": 0, "error": 0}
        for one in self.rows:
            out[one.action] = out.get(one.action, 0) + 1
        return out

    @property
    def ok(self) -> bool:
        return not self.errors and self.counts["error"] == 0


def _cell(row: dict[str, Any], name: str) -> str | None:
    """없으면 None(안 건드림), 있으면 다듬은 글자(빈 글자 포함)."""
    if name not in row or row[name] is None:
        return None
    return str(row[name]).strip()


def plan_import(db: Session, rows: list[dict[str, Any]]) -> ImportPlan:
    plan = ImportPlan()
    existing = {row.slug: row for row in db.scalars(select(Workspace))}
    seen: dict[str, int] = {}
    incoming: set[str] = set()
    for row in rows:
        slug = (_cell(row, "slug") or "").lower()
        if slug and SLUG_RE.match(slug):
            incoming.add(slug)
    for index, row in enumerate(rows, start=1):
        kind = (_cell(row, "kind") or "").lower()
        slug = (_cell(row, "slug") or "").lower()
        name = _cell(row, "name")
        if kind == "personal":
            plan.rows.append(
                ImportRow(
                    row=index,
                    slug=slug,
                    action="skip",
                    label=name or slug,
                    message="개인 공간은 부서가 아니라 건너뜁니다",
                )
            )
            continue
        if not slug or not SLUG_RE.match(slug):
            plan.rows.append(
                ImportRow(
                    row=index,
                    slug=slug,
                    action="error",
                    label=name or "",
                    message="slug 는 소문자·숫자·하이픈 2~50자여야 합니다",
                )
            )
            continue
        if slug in seen:
            plan.rows.append(
                ImportRow(
                    row=index,
                    slug=slug,
                    action="error",
                    label=name or slug,
                    message=f"같은 slug 가 {seen[slug]}행에도 있습니다",
                )
            )
            continue
        seen[slug] = index
        parent = _cell(row, "parent_slug")
        parent = parent.lower() if parent else parent
        if parent and parent not in existing and parent not in incoming:
            plan.rows.append(
                ImportRow(
                    row=index,
                    slug=slug,
                    action="error",
                    label=name or slug,
                    message=f"상위 부서를 찾을 수 없습니다: {parent}",
                )
            )
            continue
        if parent == slug:
            plan.rows.append(
                ImportRow(
                    row=index,
                    slug=slug,
                    action="error",
                    label=name or slug,
                    message="자기 자신을 상위로 둘 수 없습니다",
                )
            )
            continue
        status = _cell(row, "status")
        if status is not None and status and status.lower() not in ("active", "archived"):
            plan.rows.append(
                ImportRow(
                    row=index,
                    slug=slug,
                    action="error",
                    label=name or slug,
                    message=f"status 는 active 나 archived 여야 합니다: {status}",
                )
            )
            continue
        current = existing.get(slug)
        if current is None:
            if not name:
                plan.rows.append(
                    ImportRow(
                        row=index,
                        slug=slug,
                        action="error",
                        message="새 부서에는 name 이 있어야 합니다",
                    )
                )
                continue
            plan.rows.append(ImportRow(row=index, slug=slug, action="create", label=name))
            continue
        changes: list[str] = []
        if name and clean(name) != current.name:
            changes.append("name")
        description = _cell(row, "description")
        if description is not None and clean(description) != (current.description or ""):
            changes.append("description")
        if status and (status.lower() == "active") != current.is_active:
            changes.append("status")
        current_parent = db.get(Workspace, current.parent_id) if current.parent_id else None
        if parent is not None and (parent or None) != (
            current_parent.slug if current_parent else None
        ):
            changes.append("parent_slug")
        plan.rows.append(
            ImportRow(
                row=index,
                slug=slug,
                action="update" if changes else "unchanged",
                label=current.name,
                changes=changes,
            )
        )
    return plan


def apply_import(db: Session, rows: list[dict[str, Any]], *, actor: User) -> ImportPlan:
    """계획을 다시 세우고, 오류가 없을 때만 **한 트랜잭션으로.** 상위는 전부 만든 뒤에
    잇는다."""
    plan = plan_import(db, rows)
    if not plan.ok:
        return plan
    by_slug = {row.slug: row for row in db.scalars(select(Workspace))}
    by_row = {one.row: one for one in plan.rows}
    # 1) 만들고 고친다 — 상위는 아직 안 잇는다.
    for index, row in enumerate(rows, start=1):
        planned = by_row[index]
        if planned.action in ("skip", "unchanged"):
            continue
        slug = planned.slug
        name = _cell(row, "name")
        description = _cell(row, "description")
        status = _cell(row, "status")
        order = _cell(row, "sort_order")
        if planned.action == "create":
            workspace = Workspace(
                slug=slug,
                name=clean(name or slug),
                description=clean(description or ""),
                is_active=(status or "active").lower() == "active",
                sort_order=int(order) if order and order.lstrip("-").isdigit() else 0,
            )
            db.add(workspace)
            db.flush()
            by_slug[slug] = workspace
            audit.record(
                db,
                action="workspace.create",
                actor=actor,
                target_table="workspaces",
                target_id=workspace.id,
                target_label=workspace.name,
                workspace_id=workspace.id,
                reason="붙여 넣어 추가",
            )
            continue
        workspace = by_slug[slug]
        before = {
            "name": workspace.name,
            "description": workspace.description,
            "is_active": workspace.is_active,
        }
        if "name" in planned.changes and name:
            workspace.name = clean(name)
        if "description" in planned.changes and description is not None:
            workspace.description = clean(description)
        if "status" in planned.changes and status:
            workspace.is_active = status.lower() == "active"
        after = {
            "name": workspace.name,
            "description": workspace.description,
            "is_active": workspace.is_active,
        }
        audit.record(
            db,
            action="workspace.update",
            actor=actor,
            target_table="workspaces",
            target_id=workspace.id,
            target_label=workspace.name,
            workspace_id=workspace.id,
            changes=audit.diff(before, after),
            reason="붙여 넣어 추가",
        )
    # 2) 상위를 잇는다 — 같은 표 안의 것이 이제 다 있다.
    for index, row in enumerate(rows, start=1):
        planned = by_row[index]
        if planned.action == "create" or "parent_slug" in planned.changes:
            parent = _cell(row, "parent_slug")
            parent = parent.lower() if parent else None
            workspace = by_slug[planned.slug]
            workspace.parent_id = by_slug[parent].id if parent else None
    db.flush()
    # 3) 순환이 생겼으면 아무것도 안 넣는다 — 트리가 무한히 돈다.
    for workspace in by_slug.values():
        seen: set[uuid.UUID] = set()
        node: Workspace | None = workspace
        while node is not None:
            if node.id in seen:
                db.rollback()
                plan.errors.append(f"상위 관계가 순환합니다: {workspace.slug}")
                return plan
            seen.add(node.id)
            node = db.get(Workspace, node.parent_id) if node.parent_id else None
    audit.record(
        db,
        action="workspace.import",
        actor=actor,
        target_table="workspaces",
        target_id=None,
        target_label="붙여 넣어 추가",
        changes={"rows": len(rows), **plan.counts},
    )
    db.commit()
    return plan
