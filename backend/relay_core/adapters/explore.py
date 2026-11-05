"""원천 탐색 — 어댑터 설정을 지을 때(MCP `source_discover`) 「무엇이 있나」 를 보인다.

항목 몇 개를 받아 **칸의 자리(점으로 이은 길)와 보기 값**을 내고, 레시피가 읽을 열 이름을
제안한다 (`task.code` → `TASK_CODE`). 값은 원천의 것 그대로다(Claude Desktop 반출은 허용,
2026-11-05).
"""

from __future__ import annotations

import re
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
    """항목 몇 개 → 자리 · 보기 값 · 열 이름 제안(`columns` 의 초안)."""
    paths: dict[str, str] = {}
    for one in items[:5]:
        flatten_paths(one, out=paths)
    return {
        "paths": [{"path": path, "sample": sample} for path, sample in paths.items()],
        "columns": {column_name(path): path for path in paths},
        "rows_seen": len(items),
    }
