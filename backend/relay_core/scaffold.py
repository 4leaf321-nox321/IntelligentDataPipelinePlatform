"""한 번에 초안 — 원천의 열과 대상 타입의 칸을 **이름으로** 잇는 레시피의 첫 판(MCP
`pipeline_scaffold`). 화면의 「새 연결」 이 사람에게 고르게 하는 것을 AI 가 한 번에 하게 하는
재료다.

- 칸 이름 · 표시 이름이 열 이름 · 원천 자리와 같으면 잇는다(대소문자 · `_` · `.` 무시). 열
  이름이 칸 이름으로 끝나는 것(`TASK_STATUS` ↔ `status`), 칸 이름이 열 이름으로 끝나는 것
  (`model_revision` ↔ `REVISION`)은 4자 이상 · 하나뿐 · 아직 안 쓴 열일 때만.
- 날짜 칸(`date`)은 날짜로 바꾼다(`"date": true`).
- **참조 칸(`object_ref`)은 잇지 않는다** — 상대 타입의 식별자로 맞춰야 해서(`match`) 이름
  만으로 고를 수 없다. 남은 칸 · 참조 칸 · 안 쓴 열을 함께 돌려준다 — AI 가 읽고 레시피를
  고친다.
- 식별자가 여러 칸이면 그 값을 잇는 계산 칸(`RELAY_KEY = {A}|{B}`)을 정의에 더하고 그 열을
  레시피의 식별자로 쓴다(미러의 식별자와 같은 모양 — 화면의 「새 연결」 과 같다).

이것은 제안이다 — 저장 전에 시험(`pipeline_test`)이 「사용하면 보낼 것」 으로 확인한다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

#: 여러 칸 식별자의 계산 칸 이름 — 어댑터의 열과 겹치면 뒤에 수를 붙인다.
KEY_COLUMN = "RELAY_KEY"
_REF = {"object_ref", "ref", "reference", "relation"}
_LABEL_WORDS = ("NAME", "TITLE", "LABEL", "NM")


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(text).lower())


def key_column(columns: Mapping[str, str], key: str | Sequence[str]) -> dict[str, Any]:
    """식별자 자리 → 레시피가 읽을 열. 여러 칸이면 계산 칸까지 — `{"column", "computed"?}`."""
    by_path = {path: column for column, path in columns.items()}
    paths = [key] if isinstance(key, str) else list(key)
    missing = [path for path in paths if path not in by_path]
    if missing:
        raise ValueError(f"식별자 자리가 열(columns)에 없습니다: {', '.join(missing)}")
    if len(paths) == 1:
        return {"column": by_path[paths[0]]}
    name, n = KEY_COLUMN, 2
    while name in columns:
        name, n = f"{KEY_COLUMN}_{n}", n + 1
    template = "|".join("{" + by_path[path] + "}" for path in paths)
    return {"column": name, "computed": {name: {"template": template}}}


def label_column(columns: Mapping[str, str], hints: Sequence[str] = ()) -> str:
    """이름 칸 — 탐색의 단서(`hints.label`)가 먼저, 아니면 이름 · 제목 같은 열."""
    by_path = {path: column for column, path in columns.items()}
    for path in hints:
        if path in by_path:
            return by_path[path]
    for column in columns:
        if any(word in column.upper() for word in _LABEL_WORDS):
            return column
    return ""


def first_recipe(
    type_slug: str,
    columns: Mapping[str, str],
    props: Mapping[str, Mapping[str, Any]],
    key_col: str,
    label_col: str,
) -> dict[str, Any]:
    """`{"recipe", "matched", "unmatched", "references", "unused"}` — 레시피의 첫 판과
    그 근거."""
    names: dict[str, set[str]] = {}
    for column, path in columns.items():
        names[column] = {_norm(column), _norm(path), _norm(path.rsplit(".", 1)[-1])}
    fields: dict[str, Any] = {}
    matched: list[dict[str, str]] = []
    unmatched: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    used: set[str] = {key_col, label_col}
    for key, prop in props.items():
        kind = str(prop.get("data_type") or "")
        brief = {"property": key, "label": prop.get("label") or "", "data_type": kind}
        if kind in _REF:
            references.append(brief)
            continue
        want = _norm(key)
        label = _norm(str(prop.get("label") or ""))
        column, why = "", ""
        exact = [c for c, n in names.items() if want and want in n]
        if exact:
            column, why = exact[0], "이름이 같음"
        elif label and (same := [c for c, n in names.items() if label in n]):
            column, why = same[0], "표시 이름이 같음"
        else:
            free = [c for c in columns if c not in used]
            tail = [c for c in free if len(want) >= 4 and _norm(c).endswith(want)]
            head = [c for c in free if len(_norm(c)) >= 4 and want.endswith(_norm(c))]
            if len(tail) == 1:
                column, why = tail[0], "열 이름이 칸 이름으로 끝남"
            elif not tail and len(head) == 1:
                column, why = head[0], "칸 이름이 열 이름으로 끝남"
        if not column:
            unmatched.append(brief)
            continue
        fields[key] = (
            {"column": column, "date": True} if kind == "date" else {"column": column}
        )
        matched.append({"property": key, "column": column, "why": why})
        used.add(column)
    recipe = {
        "format": "sp-table/1",
        "title": f"{type_slug}(한 번에 초안)",
        "types": [
            {
                "type_slug": type_slug,
                "key": {"column": key_col},
                "label": {"column": label_col or key_col},
                "fields": fields,
            }
        ],
    }
    return {
        "recipe": recipe,
        "matched": matched,
        "unmatched": unmatched,
        "references": references,
        "unused": [column for column in columns if column not in used],
    }
