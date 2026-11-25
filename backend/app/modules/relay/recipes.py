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
from app.shared.errors import AppError, Conflict, NotFound, code
from relay_core import aliases, compute, sp
from relay_core.compute import check as check_computed
from relay_core.convert import Source, convert
from relay_core.describe import Described, lineage
from relay_core.diff import delta
from relay_core.mirror import Mirror
from relay_core.runner import counts_of
from relay_core.state import MirrorFile, Workdir

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
        raise NotFound(
            code("RELAY", 4), f"{slug}에 해당 레시피 버전이 없습니다(버전 {version})."
        )
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
        out.append("format이 sp-table/1이 아닙니다.")
    if not body.get("types"):
        out.append("types가 비어 있습니다. 생성할 타입을 입력하세요.")
    read = {
        column
        for step in lineage(body, found.columns, same=found.columns_all)
        for column in step.columns
    }
    unknown = sorted(read - set(found.columns))
    if unknown and not found.columns_all:
        out.append(f"어댑터가 제공하지 않는 열을 참조합니다: {', '.join(unknown)}")
    out += aliases.check(body)
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


def file_refs(body: Any, where: str = "") -> list[str]:
    """레시피가 가리키는 서버의 파일 — `ontology`(온톨로지 파일) · `match.keys` 의 `@파일`.
    DB 의 판은 작업 폴더에 옮겨 적혀 돌므로, 그 상대 경로(`@../../…`)가 서버의 아무 파일이나
    읽고 온톨로지는 묶음에 실려 대상으로 나간다. 그 둘은 운영자가 넣는 **정의 파일 옆의
    레시피**에서만 쓴다."""
    found: list[str] = []
    if isinstance(body, dict):
        for key, value in body.items():
            here = f"{where}.{key}" if where else str(key)
            ontology = key == "ontology" and not where and bool(value)
            keys_file = key == "keys" and str(value).strip().startswith("@")
            if ontology or keys_file:
                found.append(here)
            else:
                found += file_refs(value, here)
    elif isinstance(body, list):
        for index, one in enumerate(body):
            found += file_refs(one, f"{where}[{index}]")
    return found


def _refuse_files(body: Any) -> None:
    refs = file_refs(body)
    if refs:
        raise AppError(
            code("RELAY", 22),
            f"레시피 버전은 서버의 파일을 참조할 수 없습니다({', '.join(refs[:5])}). "
            "참조 대조는 대상 SP의 식별자를 사용하고(`match.keys`에 타입 지정), 온톨로지는 "
            "대상에서 정합니다. 파일이 꼭 필요하면 운영자가 정의 파일 옆의 레시피에 두어야 "
            "합니다.",
            status=422,
        )


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
        raise AppError(code("RELAY", 6), "format은 sp-table/1이어야 합니다.", status=422)
    _refuse_files(body)
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
        target_label=f"{found.slug} 레시피 버전 {row.version}",
        changes={"note": row.note, "client": row.client},
    )
    db.commit()
    db.refresh(row)
    return row, problems_of(found, body)


def activate(
    db: Session,
    user: User,
    request: Request,
    found: Described,
    version: int,
    *,
    approval: str | None = None,
) -> RelayRecipe:
    """화면의 사람이나, 대화에서 승인받은 토큰(`approval.py` — `relay:approve`)만 사용한다
    (ADR 0001 §2 · ADR 0002)."""
    from app.modules.relay import approval as approving

    approved = approving.check(
        request, user, what="레시피 버전의 사용", refused=7, approval=approval
    )
    row = get(db, found.slug, version)
    if row.status == "active":
        return row
    if row.status == "discarded":
        raise Conflict(code("RELAY", 17), f"폐기한 초안은 사용할 수 없습니다(버전 {version}).")
    _refuse_files(row.body)
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
        target_label=f"{found.slug} 레시피 버전 {row.version}",
        changes={"was": before.version if before else None},
    )
    approving.record(
        db, user, approved, action="recipe.activate", target=found.slug, version=row.version
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
    _refuse_files(row.body)  # 이 검사 전에 사용한 판이 있을 수 있다
    folder = get_settings().relay_work_dir / slug / "recipes"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"v{row.version}.table.json"
    path.write_text(json.dumps(row.body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


#: 받은 행이 없을 때의 안내 — 보내 주는 원천(push)은 사용하기 전에는 늘 이렇다.
EMPTY_NOTE = (
    "수신 행이 없어 레시피 변환을 테스트하지 않았습니다. 샘플 데이터(sample_rows)를 입력해 "
    "다시 테스트하세요. 푸시 원천이면 원천 담당자에게 테스트 전송(수신 주소에 ?trial=1)을 "
    "요청한 후 다시 테스트하세요."
)


def dry_run(
    mirror: Source,
    body: dict[str, Any],
    work: Workdir,
    source: str,
    *,
    compare: bool = True,
    target: tuple[str, str] = ("", ""),
    computed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`mirror` 를 레시피 `body` 로 바꿔 보고 지난번 적용된 출력과 견준다 — **아무것도 안
    보낸다.** 레시피 시험 · 정의 초안 시험이 같이 쓴다. `target` 은 (주소, 토큰) — 참조
    대조(`match`)가 대상의 식별자 · 별칭을 읽는 데만 쓴다(읽기만)."""
    refs = file_refs(body)
    if refs:
        return {"clean": False, "error": f"서버의 파일을 참조하는 항목: {', '.join(refs[:5])}"}
    if len(mirror) == 0:
        # 바꿔 볼 행이 없다 — 변환기는 빈 표를 오류로 던진다(500 이 됐다, 2026-11-18).
        return {"clean": None, "rows": {}, "unresolved_count": 0, "note": EMPTY_NOTE}
    server, token = target
    state = work.load_state()
    with tempfile.TemporaryDirectory(dir=work.root) as scratch:
        root = Path(scratch)
        recipe = root / "recipe.table.json"
        recipe.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        try:
            made = convert(
                compute.wrap(mirror, check_computed(computed)),
                recipe,
                root / "run",
                work=root / "work",
                server=server,
                token=token,
            )
        except (sp.pipeline.Stop, compute.ComputeError) as stop:
            return {"clean": False, "error": str(stop)}
        report = root / "run" / "table-report.txt"
        unresolved_file = made.run / sp.pipeline.UNRESOLVED
        unresolved = (
            json.loads(unresolved_file.read_text(encoding="utf-8"))
            if unresolved_file.exists()
            else []
        )
        rows: dict[str, int] = {}
        for file in sorted((made.run / sp.pipeline.OBJECTS_DIR).glob("*.json")):
            batch = json.loads(file.read_text(encoding="utf-8"))  # 파일 하나씩(5,000줄)
            slug = str(batch.get("type_slug") or file.stem)
            rows[slug] = rows.get(slug, 0) + len(batch.get("rows") or [])
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
            out["against"] = "일부 행만 수신해 '사용 시 전송 예정'을 집계하지 않았습니다"
            return out
        baseline = work.path(state.applied_run) if state.applied_run else None
        found_delta = delta(baseline, made.run, root / "send", source=source)
        out["would_send"] = counts_of(found_delta)
        out["against"] = "기준 출력" if baseline else "빈 대상 기준(첫 적재)"
        return out


def trial(
    db: Session, found: Described, version: int, sample_rows: list[Any] | None = None
) -> dict[str, Any]:
    """미러에 그 판을 돌려 본다 — **대상에 아무것도 안 보낸다.** `sample_rows` 면 미러 대신
    그 견본 행으로(정의의 식별자 · 열로 편다 — 받은 것이 아직 없을 때)."""
    from relay_core.adapters.base import Fetched

    row = get(db, found.slug, version)
    work = Workdir(get_settings().relay_work_dir, found.slug)
    mirror: Source = MirrorFile(work, work.mirror_size())  # 파일에서 흘려 쓴다
    if sample_rows is not None:
        from app.modules.relay.definitions import sample_records

        sampled = Mirror()
        sampled.apply(
            Fetched(
                records=sample_records(dict(found.body.get("adapter") or {}), sample_rows),
                complete=True,
                full=False,
            )
        )
        mirror = sampled
    if len(mirror) == 0:
        hint = (
            "정의가 아직 초안이라 수신 데이터가 없습니다. 정의 테스트(pipeline_test)에 "
            "recipe_version을 지정하면 원천 데이터를 수신해 이 레시피로 변환합니다."
            if found.draft
            else "아직 수신 데이터가 없습니다. 먼저 [즉시 실행]으로 원천 데이터를 수신하세요"
            "(미러가 생성됩니다)."
        )
        raise Conflict(code("RELAY", 8), hint)
    from app.modules.relay import guard, services

    out: dict[str, Any] = {"version": row.version, "problems": problems_of(found, row.body)}
    if found.draft:
        # 초안의 대상은 사람이 아직 안 본 주소다 — 그 토큰을 싣기 전에 울타리를 지난다.
        guard.check_target(
            {"server": found.target_server, "token_env": found.target_token_env},
            guard.fence(services.catalog(db)),
        )
    token = services.secrets_env().get(found.target_token_env, "")
    out.update(
        dry_run(
            mirror,
            row.body,
            work,
            found.source,
            # 견본 행은 원천의 전부가 아니다 — 견주면 나머지가 다 지움으로 보인다.
            compare=sample_rows is None,
            target=(found.target_server, token),
            computed=dict(found.body.get("computed") or {}),
        )
    )
    if sample_rows is not None:
        out["sample_rows"] = True
    return out


def discard(db: Session, user: User, slug: str, version: int) -> RelayRecipe:
    """초안을 버린다 — 초안만. 판 번호는 남는다(다시 쓰지 않는다). 토큰도 된다(켜는 일이
    아니다)."""
    row = get(db, slug, version)
    if row.status != "draft":
        raise Conflict(
            code("RELAY", 17),
            f"초안만 폐기할 수 있습니다. 레시피 버전 {version}의 상태는 '{row.status}'입니다.",
        )
    row.status = "discarded"
    audit.record(
        db,
        action="relay.recipe.discard",
        actor=user,
        target_table="relay_recipes",
        target_id=row.id,
        target_label=f"{slug} 레시피 버전 {row.version}",
    )
    db.commit()
    db.refresh(row)
    return row
