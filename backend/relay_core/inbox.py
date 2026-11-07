"""미해결 작업함 — 멈춘 실행의 미해결(`unresolved.json`)을 사람이 풀 모양으로.

    참조     「T 의 칸 F 값 'X'」 — 참조 대조(`match`)가 대상에서 못 찾은 원천 표기. 대상
             타입의 키를 골라 relay 사전에 올리고(`aliases`), 같은 목록으로 대표 플랫폼에
             별칭 등록을 요청한다.
    값 갈림  같은 식별자(같은 선)인데 행마다 값이 다르다 — 레시피(모으는 칸 `collect` ·
             `join`)나 원천에서 고친다. 고를 키가 없다.

후보(`candidates`)는 **대상에 있는 것 안에서만** 낸다 — 식별자 · 이름 · 별칭과 표기를 견준
점수다. 정하는 것은 사람이다(화면의 승인). GLM 의 제안(`llm`)도 이 후보 안에서만 고른다.

틀의 미해결 글(`what`)을 읽어 나눈다 — 틀을 새로 가져오면(`sync_sp_pipeline.sh`) 그 모양이
바뀌었는지 `tests/unit/test_relay_inbox.py` 가 진짜 변환으로 먼저 본다.
"""

from __future__ import annotations

import ast
import json
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from relay_core.state import Workdir

#: 참조 대조의 미해결 — `sp_table.convert` 의 `f"{slug} 의 칸 {name} 값 {text!r}"`. 관계의
#: 끝이면 slug 자리가 「관계 <이름>」 이다.
_REFERENCE = re.compile(r"^(?P<owner>.+?) 의 칸 (?P<name>\S+) 값 (?P<text>(['\"]).*\4)$")
_RELATION = "관계 "
#: 견줄 때 지우는 글자 — 띄어쓰기 · 이음표 · 괄호는 표기마다 다르게 쓴다.
_NOISE = re.compile(r"[\s_\-/.·()\[\]]+")
#: 이보다 낮은 점수는 후보로 내지 않는다 — 아무것이나 늘어놓으면 사람은 첫 줄을 고른다.
FLOOR = 0.45
#: 「한쪽이 다른 쪽을 품음」 으로 견줄 가장 짧은 글자 수 — 두 글자 코드(`US`)는 아무 키에나
#: 우연히 들어 있다(`S-AUSTIN`, 시험 인스턴스에서 실제로 그렇게 나왔다).
CONTAINS_MIN = 3
#: 틀이 미해결 하나에 적는 행 번호의 수(`numbers[:20]`).
ROWS_NOTED = 20


@dataclass
class Reference:
    """참조 대조가 못 찾은 표기 하나(같은 칸 · 같은 표기는 한 줄)."""

    owner: str
    """그 칸이 있는 타입 — 관계의 끝이면 「관계 <이름>」."""
    name: str
    """그 칸 — `key` · `label` · 속성 · 관계의 `src` · `dst`."""
    text: str
    target: str = ""
    """참조 대조의 대상 타입(`match.keys`) — 레시피에서 못 찾았으면 비어 있다."""
    column: str = ""
    rows: int = 0
    more: bool = False
    """행이 더 있다 — 틀은 미해결마다 행 번호를 20개까지만 적는다."""
    sample_rows: list[int] = field(default_factory=list)


@dataclass
class Split:
    """값 갈림 — 같은 식별자인데 행마다 값이 다르다."""

    what: str
    question: str
    options: list[str]
    rows: int


@dataclass(frozen=True)
class TargetObject:
    key: str
    label: str = ""
    aliases: tuple[str, ...] = ()
    status: str = "active"


@dataclass(frozen=True)
class Candidate:
    key: str
    label: str
    score: float
    why: str
    by: str = "규칙"
    """누가 냈나 — 규칙(표기 견주기) · 대상(대상의 「여럿과 맞음」 후보) · GLM."""


def blocked_run(work: Workdir) -> tuple[str, str] | None:
    """미해결로 멈춘 **지금의** 실행 — (`runs/<때>`, 그 시각). 그 뒤에 실행이 지나갔거나 다른
    까닭(받기 점검)으로 멈췄으면 None."""
    state = work.load_state()
    history = [one for one in state.history if one.get("event") != "reconciled"]
    if not history or state.held:
        return None
    last = history[-1]
    run = str(last.get("run") or "")
    if last.get("event") != "blocked" or not run:
        return None
    if not (work.path(run) / "unresolved.json").is_file():
        return None
    return run, str(last.get("at") or "")


def read(work: Workdir, run: str) -> list[dict[str, Any]]:
    file = work.path(run) / "unresolved.json"
    if not file.is_file():
        return []
    body = json.loads(file.read_text(encoding="utf-8"))
    return [one for one in body if isinstance(one, dict)] if isinstance(body, list) else []


def _specs(body: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """(타입 · 칸) → 참조 대조하는 칸의 규칙 — 미해결의 자리(`at`)와 같은 이름으로."""
    out: dict[tuple[str, str], dict[str, Any]] = {}

    def inner(spec: Any) -> dict[str, Any] | None:
        for mode in ("collect", "join"):
            if isinstance(spec, dict) and isinstance(spec.get(mode), dict):
                spec = spec[mode]
        return spec if isinstance(spec, dict) and isinstance(spec.get("match"), dict) else None

    for one in body.get("types") or []:
        if not isinstance(one, dict):
            continue
        slug = str(one.get("type_slug") or "")
        named = {"key": one.get("key"), "label": one.get("label"), **(one.get("fields") or {})}
        for name, spec in named.items():
            found = inner(spec)
            if found is not None:
                out[(slug, str(name))] = found
    for one in body.get("relations") or []:
        if not isinstance(one, dict):
            continue
        owner = f"{_RELATION}{one.get('relation')}"
        for name in ("src", "dst"):
            found = inner(one.get(name))
            if found is not None:
                out[(owner, name)] = found
    return out


def parse(
    unresolved: list[dict[str, Any]], body: dict[str, Any]
) -> tuple[list[Reference], list[Split]]:
    """미해결을 참조 · 값 갈림으로 — 참조에는 레시피에서 대상 타입 · 원천 칸을 붙인다."""
    specs = _specs(body)
    references: list[Reference] = []
    splits: list[Split] = []
    for one in unresolved:
        what = str(one.get("what") or "")
        rows = (one.get("_source") or {}).get("rows") or []
        found = _REFERENCE.match(what)
        if found and not one.get("options"):
            try:
                text = str(ast.literal_eval(found["text"]))
            except (ValueError, SyntaxError):
                text = found["text"][1:-1]
            owner, name = found["owner"], found["name"]
            spec = specs.get((owner, name)) or {}
            numbers = [int(n) for n in rows if isinstance(n, int)]
            references.append(
                Reference(
                    owner=owner,
                    name=name,
                    text=text,
                    target=str((spec.get("match") or {}).get("keys") or "").strip(),
                    column=str(spec.get("column") or ""),
                    rows=len(numbers),
                    more=len(numbers) >= ROWS_NOTED,
                    sample_rows=numbers[:5],
                )
            )
            continue
        options = [
            str(option.get("value") if isinstance(option, dict) else option)
            for option in one.get("options") or []
        ]
        flat = [n for group in rows for n in (group if isinstance(group, list) else [group])]
        splits.append(
            Split(
                what=what,
                question=str(one.get("question") or ""),
                options=options,
                rows=len(flat),
            )
        )
    return references, splits


def _norm(text: str) -> str:
    return _NOISE.sub("", unicodedata.normalize("NFKC", text)).casefold()


def candidates(text: str, objects: list[TargetObject], *, limit: int = 5) -> list[Candidate]:
    """대상의 객체 중 `text` 와 닮은 것 — 식별자 · 이름 · 별칭과 견준다. 사용 중지된
    것(안 씀)은 내지 않는다 — 그리로 보내면 대상이 받지 않는다."""
    wanted = _norm(text)
    if not wanted:
        return []
    best: dict[str, Candidate] = {}
    for one in objects:
        if one.status not in ("active", ""):
            continue
        for kind, value in (
            ("식별자", one.key),
            ("이름", one.label),
            *(("별칭", alias) for alias in one.aliases),
        ):
            have = _norm(value)
            if not have:
                continue
            if have == wanted:
                score, why = 1.0, f"{kind} — 띄어쓰기 · 대소문자 · 이음표만 다름"
            elif min(len(have), len(wanted)) >= CONTAINS_MIN and (
                have in wanted or wanted in have
            ):
                score = 0.6 + 0.3 * min(len(have), len(wanted)) / max(len(have), len(wanted))
                why = f"{kind} — 한쪽이 다른 쪽을 품음"
            else:
                score = 0.8 * SequenceMatcher(None, wanted, have).ratio()
                why = f"{kind} — 비슷한 글자"
            if score < FLOOR:
                continue
            if one.key not in best or best[one.key].score < score:
                best[one.key] = Candidate(one.key, one.label, round(score, 2), why)
    ranked = sorted(best.values(), key=lambda one: (-one.score, one.key))
    return ranked[:limit]


def as_objects(items: list[dict[str, Any]]) -> list[TargetObject]:
    """대상 SP 의 객체 목록(`GET /api/objects/{타입}` 의 items) → `TargetObject`."""
    out: list[TargetObject] = []
    for one in items:
        key = str(one.get("key") or "")
        if not key:
            continue
        out.append(
            TargetObject(
                key=key,
                label=str(one.get("label") or ""),
                aliases=tuple(str(a) for a in one.get("aliases") or [] if a),
                status=str(one.get("status") or "active"),
            )
        )
    return out
