"""미해결 작업함 — 멈춘 실행의 미해결을 풀 자리(화면 · 시스템 관리자).

    읽기     지금 미해결로 멈춘 실행의 미해결 — 참조(대상 타입의 키를 골라 푼다)와 값
             갈림(레시피 · 원천에서 고친다). 참조마다 대상의 후보(식별자 · 이름 · 별칭과
             견준 것, 대상이 「여럿과 맞음」 으로 준 것).
    제안     GLM 이 그 후보 안에서 하나를 고른다(`relay_core.llm`) — 설정했을 때만.
    초안     승인한 대응을 지금 레시피에 더해 **레시피 판 초안**으로 저장한다(relay 사전,
             `relay_core.aliases`). 시험 · 사용은 레시피 판에서 사람이.
    요청 목록 relay 사전 그대로를 대표 플랫폼에 별칭 등록을 요청할 CSV 로 — 대상이 이미 푸는
             표기는 그렇다고 적는다(그 줄은 relay 사전에서 걷는다 — 정본은 SP 의 별칭).

대상에는 **읽기만** 한다(`GET /api/objects/{타입}` · `resolve-many`) — 보내는 것은 실행이다.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.relay import recipes, services
from app.modules.relay.schemas import (
    AggregateIn,
    CandidateOut,
    EntryIn,
    InboxOut,
    RecipeSaved,
    ReferenceOut,
    SplitFieldOut,
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
    "대상 처리 여부",
]


#: 대상 SP 의 타입 slug — 주소에 들어가므로 모양을 본다(`../` · `?` 로 다른 경로를 못 부르게).
_TYPE = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")


def _active(found: Described) -> None:
    """**사용 중인 정의만** — 초안의 대상은 사람이 아직 안 본 주소라 그 토큰을 싣지 않는다
    (초안은 실행된 적도 없어 작업함이 없다, 2026-11-08 보안 점검)."""
    if found.draft:
        raise Conflict(
            code("RELAY", 30),
            f"{found.slug}은 아직 초안입니다. 미해결 처리함은 정의 버전을 사용해 실행한 후에 "
            "사용할 수 있습니다.",
        )


def _auth(found: Described) -> tuple[str, dict[str, str]]:
    """대상의 주소와 토큰 — 사용 중인 정의만(`_active`)."""
    _active(found)
    token = services.secrets_env().get(found.target_token_env, "")
    return found.target_server, ({"Authorization": f"Bearer {token}"} if token else {})


def _type_path(type_slug: str) -> str:
    if not _TYPE.fullmatch(type_slug):
        raise AppError(
            code("RELAY", 28),
            f"대상 타입 이름 형식이 올바르지 않습니다: {type_slug!r}",
            status=422,
        )
    return quote(type_slug, safe="")


def target_objects(found: Described, type_slug: str) -> list[TargetObject]:
    """대상의 그 타입 객체 — 키 · 이름 · 별칭 · 상태(쪽마다, `OBJECTS_MAX` 까지)."""
    server, headers = _auth(found)
    path = _type_path(type_slug)
    items: list[dict[str, Any]] = []
    while len(items) < OBJECTS_MAX:
        url = f"{server}/api/objects/{path}"
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
                f"대상({server})에 연결할 수 없어 {type_slug}의 객체를 조회하지 못했습니다: "
                f"{failure}",
                status=502,
            ) from failure
        if answer.status_code != 200:
            raise AppError(
                code("RELAY", 25),
                f"대상에서 {type_slug}의 객체를 조회할 수 없습니다({answer.status_code}). "
                "타입이 대상에 있는지, 토큰에 읽기 권한이 있는지 확인하세요.",
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
            f"{server}/api/objects/{_type_path(type_slug)}/resolve-many",
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
        return dict(row.body), f"레시피 버전 {row.version}"
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
                why="대상 — 여러 건 일치",
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
    if found.draft:
        return InboxOut(
            note="아직 초안입니다. 정의 버전을 사용해 실행한 후 미해결이 발생하면 여기에 "
            "표시됩니다.",
            llm=llm,
        )
    work = _work(slug)
    if work is None:
        return InboxOut(
            note="아직 실행하지 않았습니다. 미해결은 실행이 중단될 때 발생합니다.", llm=llm
        )
    blocked = inbox.blocked_run(work)
    if blocked is None:
        return InboxOut(
            note="미해결로 중단된 실행이 없습니다. 마지막 실행이 미해결 없이 완료되었거나 "
            "다른 사유(수신 검증, 계획 오류)로 중단되었습니다.",
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
        if not target or target.startswith("@") or not _TYPE.fullmatch(target):
            problems.append(
                f"{target or '(대상 타입 미확인)'}: 대상 SP의 타입이 아니므로 후보를 제시할 "
                "수 없습니다."
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
        split_fields=[
            SplitFieldOut(type_slug=owner, field=name, entities=count)
            for owner, name, count in inbox.split_fields(splits)
        ],
        problems=problems,
    )


def suggest(db: Session, slug: str, target: str, text: str, column: str) -> SuggestOut:
    found = services.by_slug(slug, db)
    _active(found)
    llm = _llm()
    if llm is None:
        raise Conflict(
            code("RELAY", 26),
            "GLM이 설정되지 않았습니다. 서버 설정에 RELAY_LLM_URL, RELAY_LLM_MODEL을 입력하면 "
            "제안을 받을 수 있습니다.",
        )
    current, _label = current_recipe(db, found)
    if target not in aliases.match_targets(current or {}):
        raise AppError(
            code("RELAY", 28),
            f"현재 레시피는 {target}와 참조 대조하지 않습니다. 제안할 대상이 없습니다.",
            status=422,
        )
    objects = target_objects(found, target)
    rule = inbox.candidates(text, objects, limit=20)
    labels = {one.key: one for one in objects}
    platform = platform_candidates(found, target, [text]).get(text, [])
    picks = [
        Candidate(one.key, one.label, 0.7, "대상 — 여러 건 일치", by="대상")
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
    db: Session,
    user: User,
    request: Request,
    slug: str,
    entries: list[EntryIn],
    note: str,
    aggregates: list[AggregateIn] | None = None,
) -> RecipeSaved:
    """승인한 대응(relay 사전)과 모으는 칸을 지금 레시피에 더해 **초안**으로 — 사용은 레시피
    판에서 사람이."""
    aggregates = aggregates or []
    found = services.by_slug(slug, db)
    _active(found)
    body, label = current_recipe(db, found)
    if body is None:
        raise Conflict(
            code("RELAY", 28), f"{slug}에 레시피가 없습니다. 먼저 레시피를 작성하세요."
        )
    if (not entries and not aggregates) or len(entries) > ENTRIES_MAX:
        raise AppError(
            code("RELAY", 28),
            f"relay 사전 항목은 한 번에 1~{ENTRIES_MAX}개까지 저장할 수 있습니다.",
            status=422,
        )
    targets = aliases.match_targets(body)
    wanted: dict[str, set[str]] = {}
    for one in entries:
        if one.target not in targets:
            raise AppError(
                code("RELAY", 28),
                f"레시피가 {one.target}와 참조 대조하지 않습니다. relay 사전에 추가할 수 "
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
                f"대상의 {target}에 없거나 사용 중지된 키입니다: {', '.join(missing[:10])}. "
                "대상에 있는 키만 선택할 수 있습니다.",
                status=422,
            )
    merged = (
        aliases.merge(
            body,
            [aliases.Entry(one.target, one.text.strip(), one.key.strip()) for one in entries],
        )
        if entries
        else dict(body)
    )
    for asked in aggregates:
        try:
            merged = inbox.aggregate(
                merged, asked.type_slug, asked.field, asked.mode, asked.separator
            )
        except ValueError as failure:
            raise AppError(code("RELAY", 28), str(failure), status=422) from failure
    row, problems = recipes.save_draft(
        db,
        user,
        request,
        found,
        merged,
        note.strip()
        or f"미해결 처리함 — relay 사전 {len(entries)}건 · 집계 속성 {len(aggregates)}개"
        f"({label}에 추가)",
    )
    return RecipeSaved(version=row.version, problems=problems)


def alias_csv(db: Session, slug: str) -> str:
    """대표 플랫폼에 별칭 등록을 요청할 목록 — 지금 레시피의 relay 사전 그대로."""
    found = services.by_slug(slug, db)
    _active(found)
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
                _cell(value)
                for value in (
                    one.target,
                    one.key,
                    labels.get(one.target, {}).get(one.key, ""),
                    one.text,
                    found.source,
                    found.slug,
                    "예" if one.text in done.get(one.target, set()) else "",
                )
            ]
        )
    return buffer.getvalue()


def _cell(value: str) -> str:
    """Excel 이 수식으로 읽지 않게 — `=` · `+` · `-` · `@` · 탭 · CR 로 시작하는 칸은 `'` 를
    앞에(원천 표기 · 대상 이름은 바깥에서 온 글이다)."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value
