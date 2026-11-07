"""relay 사전 — 사람이 승인한 「이 원천 표기는 대상의 이 키」 를 레시피 판에 적어 둔 것.

    "relay_aliases": {"<대상 타입>": {"<원천 표기>": "<대상 키>", …}}

레시피(`sp-table/1`)의 맨 위 칸이다. 틀의 변환기는 이 칸을 모른다 — relay 가 변환 전에
**참조 대조의 「별칭 · 이름으로」 자리**(`KeyMatch.resolved`)에 끼워 넣는다. 그 자리는 틀이
대상 SP 의 별칭 · 이름 풀이(`resolve-many`)를 받아 두는 곳이라, relay 사전은 대상의 별칭과
같은 몫을 한다:

- 식별자 그대로 · 대소문자만 다름 · 앞부분이 하나(틀의 규칙)로 풀리는 값에는 안 쓰인다 — 그
  셋이 먼저다.
- **대상(대표 플랫폼에서 받은 기준정보)이 이미 푸는 표기는 대상의 판정이 이긴다** — 대표
  플랫폼에 별칭이 등록돼 퍼지면 relay 사전의 그 줄은 쓰이지 않고(보고서가 말한다) 지워도 된다.
- **대상에 없는 키로는 풀지 않는다** — 대표 플랫폼이 그 키를 없앴으면 그 표기는 다시
  미해결이 되어 작업함에 뜬다. 짐작해서 엉뚱한 것에 붙이지 않는다(`AGENTS.md` 「하나로 안
  정해지는 것」).

규칙 승격의 다른 갈래는 대표 플랫폼이다 — 같은 목록을 「별칭 요청 목록」(CSV)으로 내려받아
대표 플랫폼에 별칭 등록을 요청한다(`rows_for_platform`). relay 는 기준정보를 만들지 않는다.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

KEY = "relay_aliases"


@dataclass(frozen=True)
class Entry:
    target: str
    """대상 타입(참조 대조의 `match.keys`)."""
    text: str
    """원천 표기 — 미해결에 적힌 그대로(그 칸이 `upper` 면 대문자로 바꾼 뒤)."""
    key: str
    """대상의 키."""


def match_targets(body: dict[str, Any]) -> set[str]:
    """레시피가 참조 대조하는 대상 타입들(`match.keys`) — 객체의 칸 · 식별자 · 이름, 관계의
    끝."""
    found: set[str] = set()

    def visit(spec: Any) -> None:
        if not isinstance(spec, dict):
            return
        for mode in ("collect", "join"):
            if isinstance(spec.get(mode), dict):
                visit(spec[mode])
        rule = spec.get("match")
        if isinstance(rule, dict) and str(rule.get("keys") or "").strip():
            found.add(str(rule["keys"]).strip())

    for one in body.get("types") or []:
        if not isinstance(one, dict):
            continue
        visit(one.get("key"))
        visit(one.get("label"))
        for spec in (one.get("fields") or {}).values():
            visit(spec)
    for one in body.get("relations") or []:
        if isinstance(one, dict):
            visit(one.get("src"))
            visit(one.get("dst"))
    return found


def entries(body: dict[str, Any]) -> list[Entry]:
    """레시피의 relay 사전 — 모양이 틀린 줄은 뺀다(`check` 가 말한다)."""
    raw = body.get(KEY)
    out: list[Entry] = []
    if not isinstance(raw, dict):
        return out
    for target, table in raw.items():
        if not isinstance(table, dict):
            continue
        for text, key in table.items():
            if isinstance(key, str) and str(text).strip() and key.strip():
                out.append(Entry(target=str(target), text=str(text), key=key))
    return out


def check(body: dict[str, Any]) -> list[str]:
    """relay 사전의 문제 — 모양, 그리고 레시피가 참조 대조하지 않는 타입(쓰일 곳이 없다)."""
    raw = body.get(KEY)
    if raw is None:
        return []
    if not isinstance(raw, dict):
        return [f"{KEY} 는 {{대상 타입: {{원천 표기: 대상 키}}}} 모양이어야 합니다."]
    problems: list[str] = []
    targets = match_targets(body)
    for target, table in raw.items():
        if not isinstance(table, dict):
            problems.append(f"{KEY}.{target} 는 {{원천 표기: 대상 키}} 여야 합니다.")
            continue
        if target not in targets:
            problems.append(
                f"{KEY}.{target} — 레시피에 이 타입과 참조 대조(match.keys)하는 칸이 없어 "
                "쓰이지 않습니다."
            )
        for text, key in table.items():
            if not str(text).strip() or not isinstance(key, str) or not key.strip():
                problems.append(f"{KEY}.{target}: 빈 표기 · 빈 키는 적지 않습니다({text!r}).")
    return problems


def merge(body: dict[str, Any], added: Iterable[Entry]) -> dict[str, Any]:
    """레시피 본문에 대응을 더한 **새** 본문 — 같은 표기가 이미 있으면 새것으로 바꾼다."""
    out = copy.deepcopy(body)
    table = out.get(KEY)
    table = dict(table) if isinstance(table, dict) else {}
    for one in added:
        inner = table.get(one.target)
        inner = dict(inner) if isinstance(inner, dict) else {}
        inner[one.text] = one.key
        table[one.target] = dict(sorted(inner.items()))
    out[KEY] = dict(sorted(table.items()))
    return out


def inject(mapping: Any, body: dict[str, Any]) -> list[str]:
    """변환 전에 끼워 넣는다(`sp_table.Mapping` — 틀이 대상에 별칭 · 이름을 물은 **뒤**).
    보고서에 붙일 말을 돌려준다 — 안 쓰인 줄이 있으면 왜인지."""
    used: dict[str, int] = {}
    platform: list[str] = []
    missing: list[str] = []
    for one in entries(body):
        matcher = mapping.matchers.get(one.target)
        if matcher is None:
            continue  # 그 타입을 참조 대조하는 칸이 없다 — `check` 가 말한다
        if one.key not in matcher.exact:
            missing.append(f"{one.target} {one.text!r} → {one.key!r}")
            continue
        if matcher.resolved.get(one.text):
            platform.append(f"{one.target} {one.text!r}")
            continue  # 대상이 이미 푼다 — 대상의 판정이 이긴다
        matcher.resolved[one.text] = one.key
        used[one.target] = used.get(one.target, 0) + 1
    notes: list[str] = []
    if used:
        notes.append(
            "relay 사전으로 푼 표기: "
            + " · ".join(f"{target} {count}개" for target, count in sorted(used.items()))
        )
    if platform:
        notes.append(
            f"relay 사전 중 대상이 이미 푸는 표기 {len(platform)}개(대표 플랫폼의 별칭 — "
            f"relay 사전에서 지워도 됩니다): {', '.join(platform[:10])}"
        )
    if missing:
        notes.append(
            f"relay 사전 중 대상에 없는 키 {len(missing)}개 — 쓰지 않았습니다(그 표기는 "
            f"미해결로 남습니다): {', '.join(missing[:10])}"
        )
    return notes


def rows_for_platform(body: dict[str, Any]) -> list[Entry]:
    """대표 플랫폼에 별칭 등록을 요청할 목록 — relay 사전 그대로, 타입 · 키 · 표기 차례로."""
    return sorted(entries(body), key=lambda one: (one.target, one.key, one.text))
