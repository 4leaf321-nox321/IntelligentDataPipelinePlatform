"""레시피의 판 — 초안 저장 · 시험 · 켜기 · 실행에 쓸 판 고르기.

    초안   AI(MCP) · 사람 누구나(시스템 관리자) — 판 번호가 하나 는다. 켜진 판은 그대로다.
    시험   미러에 그 판을 돌려 본다 — **대상에 아무것도 안 보낸다.** 「켜면 무엇이
           바뀌나」 를 지난번 적용된 출력과 견줘 보인다(생성 · 고침 · 무덤), 미해결 · 가린
           패턴 보고서와 함께.
    켜기   **사람이 화면에서만**(PAT 를 거절한다) — 다음 실행부터 그 판이 돈다.

켜진 판이 없으면 정의 파일의 `recipe` 가 돈다(개발 · 처음 들일 때). 정의가 DB 의 판뿐이면
켜진 레시피 판이 있어야 돈다.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.relay.models import RelayRecipe
from app.shared import audit
from app.shared.errors import AppError, Conflict, Forbidden, NotFound, code
from relay_core import sp
from relay_core.convert import convert
from relay_core.describe import Described, lineage
from relay_core.diff import delta
from relay_core.mirror import Mirror
from relay_core.runner import counts_of
from relay_core.state import Workdir

#: 시험이 돌려줄 보고서의 길이 — MCP 응답이 끝없이 커지지 않게.
REPORT_MAX = 20_000


def versions(db: Session, slug: str) -> list[RelayRecipe]:
    return list(
        db.scalars(
            select(RelayRecipe)
            .where(RelayRecipe.pipeline_slug == slug)
            .order_by(RelayRecipe.version.desc())
        )
    )


def get(db: Session, slug: str, version: int) -> RelayRecipe:
    row = db.scalar(
        select(RelayRecipe).where(
            RelayRecipe.pipeline_slug == slug, RelayRecipe.version == version
        )
    )
    if row is None:
        raise NotFound(code("RELAY", 4), f"{slug} 의 레시피 판 {version} 이 없습니다.")
    return row


def active(db: Session, slug: str) -> RelayRecipe | None:
    return db.scalar(
        select(RelayRecipe).where(
            RelayRecipe.pipeline_slug == slug, RelayRecipe.status == "active"
        )
    )


def problems_of(found: Described, body: dict[str, Any]) -> list[str]:
    """그 레시피가 이 파이프라인의 어댑터와 맞나 — 어댑터가 안 내는 열을 읽으면 변환이
    멈춘다."""
    out: list[str] = []
    if body.get("format") != "sp-table/1":
        out.append("format 이 sp-table/1 이 아닙니다.")
    if not body.get("types"):
        out.append("types 가 비었습니다 — 무엇을 만들지 적어야 합니다.")
    read = {
        column
        for step in lineage(body, found.columns, same=found.columns_all)
        for column in step.columns
    }
    unknown = sorted(read - set(found.columns))
    if unknown and not found.columns_all:
        out.append(f"어댑터가 안 내는 열을 읽습니다: {', '.join(unknown)}")
    return out


def with_recipe(found: Described, body: dict[str, Any], label: str) -> Described:
    """켜진 판으로 계보 · 타입을 다시 센다 — 지도와 상세가 **실제로 도는 판**을 그리게."""
    found.types = [
        str(one["type_slug"]) for one in body.get("types") or [] if "type_slug" in one
    ]
    found.relations = sorted(
        {
            str(one.get("relation"))
            for one in body.get("relations") or []
            if one.get("relation")
        }
    )
    found.lineage = lineage(body, found.columns, same=found.columns_all)
    found.problems = [one for one in found.problems if not one.startswith("레시피")]
    found.problems += problems_of(found, body)
    found.recipe_label = label
    return found


def save_draft(
    db: Session,
    user: User,
    request: Request,
    found: Described,
    body: dict[str, Any],
    note: str,
) -> tuple[RelayRecipe, list[str]]:
    if not isinstance(body, dict):
        raise AppError(code("RELAY", 5), "레시피는 JSON 객체여야 합니다.", status=422)
    if body.get("format") != "sp-table/1":
        raise AppError(code("RELAY", 6), "format 이 sp-table/1 이어야 합니다.", status=422)
    latest = db.scalar(
        select(func.max(RelayRecipe.version)).where(RelayRecipe.pipeline_slug == found.slug)
    )
    via_token = getattr(request.state, "token_scopes", None) is not None
    row = RelayRecipe(
        pipeline_slug=found.slug,
        version=int(latest or 0) + 1,
        status="draft",
        body=body,
        note=note.strip(),
        client=request.headers.get("X-Client") or ("토큰" if via_token else "화면"),
        created_by_id=user.id,
    )
    db.add(row)
    db.flush()
    audit.record(
        db,
        action="relay.recipe.draft",
        actor=user,
        target_table="relay_recipes",
        target_id=row.id,
        target_label=f"{found.slug} 판 {row.version}",
        changes={"note": row.note, "client": row.client},
    )
    db.commit()
    db.refresh(row)
    return row, problems_of(found, body)


def activate(
    db: Session, user: User, request: Request, found: Described, version: int
) -> RelayRecipe:
    """**사람이 화면에서만** — 토큰(MCP · 스크립트)으로 온 요청은 거절한다(ADR 0001 §2)."""
    if getattr(request.state, "token_scopes", None) is not None:
        raise Forbidden(
            code("RELAY", 7),
            "레시피를 켜는 일은 화면에서 사람이 합니다 — 토큰(MCP)으로는 켜지 않습니다. "
            "시험 결과를 화면에서 보고 「켜기」 를 누르세요.",
        )
    row = get(db, found.slug, version)
    if row.status == "active":
        return row
    if row.status == "discarded":
        raise Conflict(code("RELAY", 17), f"버린 초안은 켜지 않습니다 — 판 {version}.")
    before = active(db, found.slug)
    db.execute(
        update(RelayRecipe)
        .where(RelayRecipe.pipeline_slug == found.slug, RelayRecipe.status == "active")
        .values(status="retired")
    )
    row.status = "active"
    row.activated_by_id = user.id
    row.activated_at = datetime.now(UTC)
    audit.record(
        db,
        action="relay.recipe.activate",
        actor=user,
        target_table="relay_recipes",
        target_id=row.id,
        target_label=f"{found.slug} 판 {row.version}",
        changes={"was": before.version if before else None},
    )
    db.commit()
    db.refresh(row)
    return row


def materialize(db: Session, slug: str) -> Path | None:
    """켜진 판을 작업 폴더에 파일로 — 변환기(`sp_table.convert`)는 파일을 읽는다. 없으면
    None."""
    row = active(db, slug)
    if row is None:
        return None
    folder = get_settings().relay_work_dir / slug / "recipes"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"v{row.version}.table.json"
    path.write_text(json.dumps(row.body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def dry_run(
    mirror: Mirror, body: dict[str, Any], work: Workdir, source: str, *, compare: bool = True
) -> dict[str, Any]:
    """`mirror` 를 레시피 `body` 로 바꿔 보고 지난번 적용된 출력과 견준다 — **아무것도 안
    보낸다.** 레시피 시험 · 정의 초안 시험이 같이 쓴다."""
    state = work.load_state()
    with tempfile.TemporaryDirectory(dir=work.root) as scratch:
        root = Path(scratch)
        recipe = root / "recipe.table.json"
        recipe.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        try:
            made = convert(mirror, recipe, root / "run", work=root / "work")
        except sp.pipeline.Stop as stop:
            return {"clean": False, "error": str(stop)}
        report = root / "run" / "table-report.txt"
        unresolved_file = made.run / sp.pipeline.UNRESOLVED
        unresolved = (
            json.loads(unresolved_file.read_text(encoding="utf-8"))
            if unresolved_file.exists()
            else []
        )
        loaded = sp.pipeline.load(made.run)
        rows: dict[str, int] = {}
        for batch in loaded.objects:
            rows[batch.type_slug] = rows.get(batch.type_slug, 0) + len(batch.rows)
        out: dict[str, Any] = {
            "clean": made.clean,
            "rows": rows,
            "unresolved_count": len(unresolved),
            "unresolved": unresolved[:20],
            "report": report.read_text(encoding="utf-8")[:REPORT_MAX]
            if report.exists()
            else "",
        }
        if not compare:  # 덜 받은 채 견주면 나머지가 다 지움(무덤)으로 보인다
            out["against"] = "앞부분만 받아 「켜면 보낼 것」 은 세지 않았습니다"
            return out
        baseline = work.path(state.applied_run) if state.applied_run else None
        found_delta = delta(baseline, made.run, root / "send", source=source)
        out["would_send"] = counts_of(found_delta)
        out["against"] = "지난번 적용된 출력" if baseline else "대상이 비었다고 보고(첫 적재)"
        return out


def trial(db: Session, found: Described, version: int) -> dict[str, Any]:
    """미러에 그 판을 돌려 본다 — **대상에 아무것도 안 보낸다.**"""
    row = get(db, found.slug, version)
    work = Workdir(get_settings().relay_work_dir, found.slug)
    mirror = work.load_mirror()
    if len(mirror) == 0:
        hint = (
            "정의가 아직 초안이라 받은 것이 없습니다 — 정의 시험(pipeline_test)에 "
            "recipe_version 을 주면 원천을 받아 이 레시피로 바꿔 봅니다."
            if found.draft
            else "아직 받은 것이 없습니다 — 먼저 「지금 실행」 으로 원천을 한 번 받으세요"
            "(미러가 생깁니다)."
        )
        raise Conflict(code("RELAY", 8), hint)
    out: dict[str, Any] = {"version": row.version, "problems": problems_of(found, row.body)}
    out.update(dry_run(mirror, row.body, work, found.source))
    return out


def discard(db: Session, user: User, slug: str, version: int) -> RelayRecipe:
    """초안을 버린다 — 초안만. 판 번호는 남는다(다시 쓰지 않는다). 토큰도 된다(켜는 일이
    아니다)."""
    row = get(db, slug, version)
    if row.status != "draft":
        raise Conflict(
            code("RELAY", 17), f"초안만 버립니다 — 판 {version} 은 {row.status} 입니다."
        )
    row.status = "discarded"
    audit.record(
        db,
        action="relay.recipe.discard",
        actor=user,
        target_table="relay_recipes",
        target_id=row.id,
        target_label=f"{slug} 판 {row.version}",
    )
    db.commit()
    db.refresh(row)
    return row
