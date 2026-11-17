"""원천 탐색 — 어댑터 설정을 지을 때(MCP `source_discover`) 「무엇이 있나」 를 보인다.

항목 몇 개를 받아 **칸의 자리(점으로 이은 길)와 보기 값**을 내고, 레시피가 읽을 열 이름을
제안한다 (`task.code` → `TASK_CODE`). 값은 원천의 것 그대로다(Claude Desktop 반출은 허용,
2026-11-05).

**값을 모르는 사람 대신 AI 가 고르게** 단서도 낸다(`hints` · `shape`) — 식별자 · 변경 시각 ·
삭제 표시 · 이름 칸의 후보, 그리고 REST 응답의 모양(목록 자리 · 쪽 넘김 · 전체 건수). 단서는
제안일 뿐이다 — 정하는 것은 정의를 쓰는 쪽(AI · 사람)이고, 시험이 맞는지 본다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: 한 자리에 보일 보기 값의 길이.
SAMPLE_MAX = 60


def flatten_paths(
    item: Any, prefix: str = "", out: dict[str, str] | None = None
) -> dict[str, str]:
    """항목의 끝 값마다 `{자리: 보기 값}` — 목록은 원소의 자리를 같은 이름으로 모은다."""
    out = {} if out is None else out
    if isinstance(item, dict):
        for key, value in item.items():
            if str(key).startswith(("@odata.", "__")):
                continue
            flatten_paths(value, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(item, list):
        for one in item[:3]:
            flatten_paths(one, prefix, out)
        if not item and prefix:
            out.setdefault(prefix, "")
    elif prefix:
        text = "" if item is None else str(item)
        out.setdefault(prefix, text[:SAMPLE_MAX])
    return out


def column_name(path: str) -> str:
    """자리 → 레시피의 열 이름 제안(`Task.Project.Name` → `TASK_PROJECT_NAME`)."""
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", path)
    return re.sub(r"[^A-Za-z0-9]+", "_", snake).strip("_").upper()


def suggest(items: list[Any]) -> dict[str, Any]:
    """항목 몇 개 → 자리 · 보기 값 · 열 이름 제안(`columns` 의 초안)과 칸의 단서(`hints`)."""
    paths: dict[str, str] = {}
    for one in items[:5]:
        flatten_paths(one, out=paths)
    return {
        "paths": [{"path": path, "sample": sample} for path, sample in paths.items()],
        "columns": {column_name(path): path for path in paths},
        "rows_seen": len(items),
        "hints": field_hints(items[:20]),
    }


# --- 칸의 단서 ------------------------------------------------------------------------------


#: 이름으로 견줄 때 — 소문자에 영숫자 · 한글만 남긴 꼴(`Updated_At` → `updatedat`).
def _norm(name: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", name.lower())


_KEY_END = ("id", "code", "key", "no", "number", "cd", "uuid", "guid")
_UPDATED = (
    "updated",
    "modified",
    "changed",
    "lastupdate",
    "lastmodified",
    "upddt",
    "moddt",
    "chgdt",
    "uptdt",
    "updtdt",
    "updatetime",
    "modifytime",
    "변경일",
    "수정일",
)
_DELETED = (
    "deleted",
    "isdeleted",
    "delyn",
    "delflag",
    "deleteflag",
    "deletedyn",
    "isdel",
    "removed",
    "isremoved",
    "삭제",
)
_LABEL = ("name", "title", "label", "nm", "desc", "이름", "명칭")
_BOOLISH = {"true", "false", "1", "0", "y", "n", "yes", "no", "t", "f", ""}
_DATELIKE = re.compile(
    r"^\d{4}-?\d{2}-?\d{2}([ T]\d{2}:?\d{2}(:?\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$"
)


def _values(items: list[Any], path: str) -> list[str]:
    out: list[str] = []
    for one in items:
        current: Any = one
        for part in path.split("."):
            current = current.get(part) if isinstance(current, dict) else None
        if current is not None and not isinstance(current, (dict, list)):
            out.append(str(current).strip())
    return out


def field_hints(items: list[Any]) -> dict[str, list[str]]:
    """항목들 → 칸 자리의 후보(앞이 더 그럴듯하다). 이름과 값을 함께 본다:

    - `key`: 받은 항목에서 값이 모두 있고 서로 다른 칸 — 이름이 `id` · `…_id` · `…code` 면 앞에
    - `updated_at`: 이름이 변경 시각 같고 값이 날짜 · 시각 꼴
    - `deleted`: 이름이 삭제 표시 같고 값이 참 · 거짓 꼴
    - `label`: 이름 · 제목 같은 글 칸
    """
    paths: dict[str, str] = {}
    for one in items[:20]:
        flatten_paths(one, out=paths)
    seen = [one for one in items if isinstance(one, dict)]
    keys: list[tuple[int, str]] = []
    updated: list[str] = []
    deleted: list[str] = []
    labels: list[str] = []
    for path in paths:
        if "." in path and path.count(".") > 1:
            continue
        last = _norm(path.rsplit(".", 1)[-1])
        values = _values(seen, path)
        if not values:
            continue
        filled = [v for v in values if v]
        unique = (
            len(seen) >= 2 and len(filled) == len(seen) and len(set(filled)) == len(filled)
        )
        if unique and "." not in path:
            rank = 0 if last == "id" else 1 if last.endswith(_KEY_END) else 2
            if rank < 2 or len(filled[0]) <= 40:
                keys.append((rank, path))
        if any(word in last for word in _UPDATED) and all(_DATELIKE.match(v) for v in filled):
            updated.append(path)
        if any(word in last for word in _DELETED) and all(
            v.lower() in _BOOLISH for v in values
        ):
            deleted.append(path)
        if (
            any(word in last for word in _LABEL)
            and filled
            and not all(v.isdigit() for v in filled)
        ):
            labels.append(path)
    keys.sort()
    return {
        "key": [path for _rank, path in keys][:5],
        "updated_at": updated[:3],
        "deleted": deleted[:3],
        "label": labels[:3],
    }


# --- REST 응답의 모양 -----------------------------------------------------------------------

_NEXT = ("next", "nextlink", "odatanextlink", "nexturl", "nextpage", "nextpageurl", "nexthref")
_CURSOR = (
    "nextcursor",
    "cursor",
    "nextpagetoken",
    "pagetoken",
    "continuationtoken",
    "nexttoken",
    "endcursor",
    "after",
)
_TOTAL = (
    "total",
    "totalcount",
    "count",
    "totalelements",
    "totalitems",
    "totalrecords",
    "totalsize",
    "totalrows",
    "odatacount",
)


def _spots(body: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, Any]]:
    """객체 안의 (자리, 값) — 두 겹까지."""
    out: list[tuple[str, Any]] = []
    if isinstance(body, dict) and depth < 2:
        for key, value in body.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.append((path, value))
            if isinstance(value, dict):
                out += _spots(value, path, depth + 1)
    return out


def shape(
    body: Any, headers: Mapping[str, str] | None, rows_path: str, asked: int
) -> dict[str, Any]:
    """REST 응답 한 쪽 → 목록 자리 · 쪽 넘김 · 전체 건수의 단서와 사람 말의 메모.
    `asked` 는 탐색이 한 쪽에 달라고 한 줄 수다(쪽 넘김이 듣는지 보려고)."""
    notes: list[str] = []
    out: dict[str, Any] = {"body": "list" if isinstance(body, list) else type(body).__name__}
    spots = _spots(body)
    lists: list[dict[str, Any]] = [
        {"path": path, "items": len(value)}
        for path, value in spots
        if isinstance(value, list)
        and value
        and all(isinstance(one, dict) for one in value[:3])
    ]
    lists.sort(key=lambda one: -int(one["items"]))
    candidates: list[dict[str, Any]]
    if isinstance(body, list):
        current_ok = rows_path == ""
        candidates = [{"path": "", "items": len(body)}]
    else:
        current_ok = any(one["path"] == rows_path for one in lists)
        candidates = lists[:4]
    out["rows_path"] = {"current": rows_path, "found": current_ok, "candidates": candidates}
    if not current_ok and candidates:
        best = candidates[0]["path"]
        notes.append(
            f"행 자리(rows_path)는 「{best}」 로 보입니다 — 지금 값 「{rows_path}」 에는 "
            "목록이 없습니다."
            if best
            else '응답 자체가 목록입니다 — rows_path 를 빈 글("")로 두세요'
            f"(지금 「{rows_path}」)."
        )
    if isinstance(body, dict):
        out["top"] = {
            path: value
            for path, value in spots
            if "." not in path and not isinstance(value, (dict, list))
        }
    total: dict[str, Any] | None = None
    paging: dict[str, Any] = {"guess": "", "evidence": ""}
    link = (headers or {}).get("link", "") if headers is not None else ""
    if re.search(r'rel\s*=\s*"?next"?', link or ""):
        paging = {"guess": "link", "evidence": '응답 머리글 Link 에 rel="next" 가 있습니다.'}
    for path, value in spots:
        last = _norm(path.rsplit(".", 1)[-1])
        if isinstance(value, (dict, list)):
            continue
        if last in _TOTAL and str(value).isdigit():
            total = {"path": path, "value": int(value)}
        if not paging["guess"] and last in _NEXT and isinstance(value, str) and value.strip():
            paging = {
                "guess": "next_url",
                "next_path": path,
                "evidence": f"응답의 「{path}」 에 다음 쪽 주소가 있습니다.",
            }
        if not paging["guess"] and last in _CURSOR and value not in (None, "", False):
            paging = {
                "guess": "cursor",
                "next_path": path,
                "evidence": f"응답의 「{path}」 에 다음 쪽의 커서 값이 있습니다"
                "(보낼 질의 이름은 지침서로).",
            }
    out["total"] = total
    seen = candidates[0]["items"] if candidates else 0
    if not paging["guess"]:
        if total and total["value"] > seen:
            paging = {
                "guess": "page",
                "evidence": f"전체 {total['value']}건 중 이번 쪽에 {seen}건 — 쪽 넘김이 "
                "있습니다. 질의 이름(page · page_size · offset · limit 등)은 API 지침서"
                "(source_spec)로 확인하세요.",
            }
        elif seen and seen <= asked:
            paging = {
                "guess": "",
                "evidence": f"한 쪽에 {asked}건을 달라고 해 {seen}건이 왔습니다 — 쪽 넘김 "
                "질의가 들었는지 지침서로 확인하세요.",
            }
        elif seen > asked:
            paging = {
                "guess": "none",
                "evidence": f"{asked}건을 달라고 했는데 {seen}건이 왔습니다 — 쪽 크기 질의가 "
                "안 듣거나 쪽 넘김이 없는 원천입니다.",
            }
    out["paging"] = paging
    if paging.get("evidence"):
        notes.append(paging["evidence"])
    out["notes"] = notes
    return out
