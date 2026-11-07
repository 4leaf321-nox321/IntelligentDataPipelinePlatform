"""미해결 작업함 — 멈춘 실행의 미해결을 풀 자리(화면 · 시스템 관리자).

    읽기     지금 미해결로 멈춘 실행의 미해결 — 참조(대상 타입의 키를 골라 푼다)와 값
             갈림(레시피 · 원천에서 고친다). 참조마다 대상의 후보(식별자 · 이름 · 별칭과
             견준 것, 대상이 「여럿과 맞음」 으로 준 것).
    제안     GLM 이 그 후보 안에서 하나를 고른다(`relay_core.llm`) — 설정했을 때만.
    초안     승인한 대응을 지금 레시피에 더해 **레시피 판 초안**으로 저장한다(relay 사전,
             `relay_core.aliases`). 시험 · 사용은 레시피 판에서 사람이.
    요청 목록 relay 사전 그대로를 대표 플랫폼에 별칭 등록을 요청할 CSV 로 — 대상이 이미 푸는
             표기는 그렇다고 적는다(relay 사전에서 지워도 된다).

대상에는 **읽기만** 한다(`GET /api/objects/{타입}` · `resolve-many`) — 보내는 것은 실행이다.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

import httpx
from fastapi import Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.relay import recipes, services
from app.modules.relay.schemas import (
    CandidateOut,
    EntryIn,
    InboxOut,
    RecipeSaved,
    ReferenceOut,
    SplitOut,
    SuggestOut,
)
from app.shared.errors import AppError, Conflict, code
from relay_core import aliases, inbox
from relay_core.describe import Described
from relay_core.inbox import Candidate, TargetObject
from relay_core.llm import Llm, LlmError
from relay_core.state import Workdir

#: 대상의 한 타입에서 읽을 객체의 상한 — 후보를 고르려고 읽는다(기준정보 타입은 대개 수천).
OBJECTS_MAX = 20_000
PAGE = 500
TIMEOUT = 10.0
#: 한 번에 승인할 수 있는 대응의 수.
ENTRIES_MAX = 500
CSV_HEADER = [
    "대상 타입",
    "대상 키",
    "대상 이름",
    "원천 표기",
    "원천",
    "파이프라인",
    "대상이 이미 푸나",
]


def _auth(found: Described) -> tuple[str, dict[str, str]]:
    token = services.secrets_env().get(found.target_token_env, "")
    return found.target_server, ({"Authorization": f"Bearer {token}"} if token else {})


def target_objects(found: Described, type_slug: str) -> list[TargetObject]:
    """대상의 그 타입 객체 — 키 · 이름 · 별칭 · 상태(쪽마다, `OBJECTS_MAX` 까지)."""
    server, headers = _auth(found)
    items: list[dict[str, Any]] = []
    while len(items) < OBJECTS_MAX:
        url = f"{server}/api/objects/{type_slug}"
        try:
            answer = httpx.get(
                url,
                params={"limit": PAGE, "offset": len(items)},
                headers=headers,
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as failure:
            raise AppError(
                code("RELAY", 25),
                f"대상({server})에 닿지 않아 {type_slug} 의 객체를 못 읽었습니다: {failure}",
                status=502,
            ) from failure
        if answer.status_code != 200:
            raise AppError(
                code("RELAY", 25),
                f"대상이 {type_slug} 의 객체를 주지 않습니다({answer.status_code}) — 타입이 "
                "대상에 있는지, 토큰이 읽을 수 있는지 확인하세요.",
                status=502,
            )
        body = answer.json()
        page = list(body.get("items") or [])
        items += page
        if not page or len(items) >= int(body.get("total") or 0):
            break
    return inbox.as_objects(items)


def _resolve_many(found: Described, type_slug: str, texts: list[str]) -> list[dict[str, Any]]:
    """대상의 판정(`resolve-many`) — 식별자 → 별칭 → 이름. 못 물으면 빈 것(후보만 줄어든다)."""
    server, headers = _auth(found)
    if not texts:
        return []
    try:
        answer = httpx.post(
            f"{server}/api/objects/{type_slug}/resolve-many",
            json={"names": texts[:500]},
            headers=headers,
            timeout=TIMEOUT,
        )
    except httpx.HTTPError:
        return []
    if answer.status_code != 200:
        return []
    return [one for one in answer.json().get("items") or [] if isinstance(one, dict)]


def platform_candidates(
    found: Described, type_slug: str, texts: list[str]
) -> dict[str, list[TargetObject]]:
    """대상이 「여럿과 맞음」 으로 준 후보들 — 표기마다."""
    out: dict[str, list[TargetObject]] = {}
    for one in _resolve_many(found, type_slug, texts):
        items = list(one.get("candidates") or [])
        if one.get("match") == "exact" and one.get("object"):
            items = [one["object"]]
        out[str(one.get("name"))] = inbox.as_objects(items)
    return out


def _resolved(found: Described, type_slug: str, texts: list[str]) -> set[str]:
    """대상이 하나로 푸는 표기 — 대표 플랫폼의 별칭 · 이름으로."""
    return {
        str(one.get("name"))
        for one in _resolve_many(found, type_slug, texts)
        if one.get("match") == "exact"
    }


def current_recipe(db: Session, found: Described) -> tuple[dict[str, Any] | None, str]:
    """지금 도는 레시피 — 사용 중인 판, 없으면 정의 파일의 것."""
    row = recipes.active(db, found.slug)
    if row is not None:
        return dict(row.body), f"레시피 판 {row.version}"
    if found.recipe is not None and found.recipe.is_file():
        return json.loads(found.recipe.read_text(encoding="utf-8")), "정의 파일의 레시피"
    return None, ""


def _work(slug: str) -> Workdir | None:
    root = get_settings().relay_work_dir / slug
    return Workdir(get_settings().relay_work_dir, slug) if root.is_dir() else None


def _llm() -> Llm | None:
    settings = get_settings()
    if not settings.relay_llm_url or not settings.relay_llm_model:
        return None
    return Llm(
        settings.relay_llm_url,
        settings.relay_llm_model,
        settings.relay_llm_token,
        min_confidence=settings.relay_llm_min_confidence,
    )


def _merged(rule: list[Candidate], platform: list[TargetObject]) -> list[CandidateOut]:
    """대상이 준 후보(「여럿과 맞음」)를 앞에, 규칙의 후보를 뒤에 — 같은 키는 한 번."""
    out: list[CandidateOut] = []
    seen: set[str] = set()
    for given in platform:
        if given.status not in ("active", "") or given.key in seen:
            continue
        seen.add(given.key)
        score = next((c.score for c in rule if c.key == given.key), 0.7)
        out.append(
            CandidateOut(
                key=given.key,
                label=given.label,
                score=score,
                why="대상 — 여럿과 맞음",
                by="대상",
            )
        )
    for one in rule:
        if one.key in seen:
            continue
        seen.add(one.key)
        out.append(
            CandidateOut(key=one.key, label=one.label, score=one.score, why=one.why, by=one.by)
        )
    return out[:6]


def read(db: Session, slug: str) -> InboxOut:
    found = services.by_slug(slug, db)
    llm = _llm() is not None
    work = _work(slug)
    if work is None:
        return InboxOut(
            note="아직 실행한 적이 없습니다 — 미해결은 실행이 멈출 때 생깁니다.", llm=llm
        )
    blocked = inbox.blocked_run(work)
    if blocked is None:
        return InboxOut(
            note="미해결로 멈춘 실행이 없습니다 — 마지막 실행이 미해결 없이 지나갔거나 다른 "
            "까닭(받기 점검 · 계획 오류)으로 멈췄습니다.",
            llm=llm,
        )
    run, at = blocked
    current, label = current_recipe(db, found)
    # 미해결을 낸 그때의 레시피로 읽는다 — 그 뒤 판이 바뀌었어도 칸 · 대상 타입이 맞게.
    stamp = run.rsplit("/", 1)[-1]
    copy = work.path(f"work/{stamp}.recipe.table.json")
    used = json.loads(copy.read_text(encoding="utf-8")) if copy.is_file() else current or {}
    references, splits = inbox.parse(inbox.read(work, run), used)
    known = {(one.target, one.text): one.key for one in aliases.entries(current or {})}

    grouped: dict[tuple[str, str], ReferenceOut] = {}
    for one in references:
        place = f"{one.owner}.{one.name}"
        slot = grouped.get((one.target, one.text))
        if slot is None:
            grouped[(one.target, one.text)] = ReferenceOut(
                target=one.target,
                text=one.text,
                places=[place],
                columns=[one.column] if one.column else [],
                rows=one.rows,
                more=one.more,
                sample_rows=one.sample_rows,
                in_dictionary=known.get((one.target, one.text)),
            )
            continue
        slot.places.append(place)
        if one.column and one.column not in slot.columns:
            slot.columns.append(one.column)
        slot.rows = max(slot.rows, one.rows)
        slot.more = slot.more or one.more
    problems: list[str] = []
    by_target: dict[str, list[ReferenceOut]] = {}
    for item in grouped.values():
        by_target.setdefault(item.target, []).append(item)
    for target, items in sorted(by_target.items()):
        if not target or target.startswith("@"):
            problems.append(
                f"{target or '(대상 타입을 모름)'} — 대상 SP 의 타입이 아니라 후보를 낼 수 "
                "없습니다."
            )
            continue
        try:
            objects = target_objects(found, target)
        except AppError as failure:
            problems.append(failure.message)
            continue
        platform = platform_candidates(found, target, [item.text for item in items])
        for item in items:
            item.candidates = _merged(
                inbox.candidates(item.text, objects), platform.get(item.text, [])
            )
    return InboxOut(
        run=run,
        at=at,
        recipe_label=label,
        llm=llm,
        references=sorted(grouped.values(), key=lambda one: (one.target, -one.rows, one.text)),
        splits=[
            SplitOut(what=one.what, question=one.question, options=one.options, rows=one.rows)
            for one in splits
        ],
        problems=problems,
    )


def suggest(db: Session, slug: str, target: str, text: str, column: str) -> SuggestOut:
    found = services.by_slug(slug, db)
    llm = _llm()
    if llm is None:
        raise Conflict(
            code("RELAY", 26),
            "GLM 이 설정되지 않았습니다 — 서버의 RELAY_LLM_URL · RELAY_LLM_MODEL 을 적으면 "
            "제안을 받을 수 있습니다.",
        )
    objects = target_objects(found, target)
    rule = inbox.candidates(text, objects, limit=20)
    labels = {one.key: one for one in objects}
    platform = platform_candidates(found, target, [text]).get(text, [])
    picks = [
        Candidate(one.key, one.label, 0.7, "대상 — 여럿과 맞음", by="대상")
        for one in platform
        if one.status in ("active", "")
    ]
    picks += [one for one in rule if one.key not in {p.key for p in picks}]
    try:
        got = llm.pick(
            text=text,
            target=target,
            column=column,
            candidates=picks,
            aliases={
                one.key: list(labels[one.key].aliases) for one in picks if one.key in labels
            },
        )
    except LlmError as failure:
        raise AppError(code("RELAY", 27), str(failure), status=502) from failure
    label = labels[got.key].label if got.key and got.key in labels else ""
    return SuggestOut(
        key=got.key, label=label, confidence=got.confidence, reason=got.reason, note=got.note
    )


def draft(
    db: Session, user: User, request: Request, slug: str, entries: list[EntryIn], note: str
) -> RecipeSaved:
    """승인한 대응을 지금 레시피에 더해 **초안**으로 — 사용은 레시피 판에서 사람이."""
    found = services.by_slug(slug, db)
    body, label = current_recipe(db, found)
    if body is None:
        raise Conflict(
            code("RELAY", 28), f"{slug} 에 레시피가 없습니다 — 먼저 레시피를 작성하세요."
        )
    if not entries or len(entries) > ENTRIES_MAX:
        raise AppError(
            code("RELAY", 28), f"대응은 한 번에 1 ~ {ENTRIES_MAX}개를 저장합니다.", status=422
        )
    targets = aliases.match_targets(body)
    wanted: dict[str, set[str]] = {}
    for one in entries:
        if one.target not in targets:
            raise AppError(
                code("RELAY", 28),
                f"레시피가 {one.target} 와 참조 대조하지 않습니다 — relay 사전에 둘 곳이 "
                "없습니다.",
                status=422,
            )
        wanted.setdefault(one.target, set()).add(one.key)
    for target, keys in wanted.items():
        live = {o.key for o in target_objects(found, target) if o.status in ("active", "")}
        missing = sorted(keys - live)
        if missing:
            raise AppError(
                code("RELAY", 28),
                f"대상의 {target} 에 없거나 사용 중지된 키입니다: {', '.join(missing[:10])} — "
                "대상에 있는 키만 고를 수 있습니다.",
                status=422,
            )
    merged = aliases.merge(
        body, [aliases.Entry(one.target, one.text.strip(), one.key.strip()) for one in entries]
    )
    row, problems = recipes.save_draft(
        db,
        user,
        request,
        found,
        merged,
        note.strip() or f"미해결 작업함 — 대응 {len(entries)}개({label}에 더함)",
    )
    return RecipeSaved(version=row.version, problems=problems)


def alias_csv(db: Session, slug: str) -> str:
    """대표 플랫폼에 별칭 등록을 요청할 목록 — 지금 레시피의 relay 사전 그대로."""
    found = services.by_slug(slug, db)
    body, _label = current_recipe(db, found)
    rows = aliases.rows_for_platform(body or {})
    labels: dict[str, dict[str, str]] = {}
    done: dict[str, set[str]] = {}
    for target in sorted({one.target for one in rows}):
        try:
            labels[target] = {o.key: o.label for o in target_objects(found, target)}
        except AppError:
            labels[target] = {}
        done[target] = _resolved(
            found, target, [one.text for one in rows if one.target == target]
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for one in rows:
        writer.writerow(
            [
                one.target,
                one.key,
                labels.get(one.target, {}).get(one.key, ""),
                one.text,
                found.source,
                found.slug,
                "예" if one.text in done.get(one.target, set()) else "",
            ]
        )
    return buffer.getvalue()
