"""보낼 묶음을 **흘려서** 읽는다 — 지문 · 크기를 메모리에 다 들지 않고.

틀의 지문(`sp_pipeline.digest(payload(load(run)))`)은 실행 폴더 전체를 메모리에 들고 JSON 글
하나로 만든 뒤 sha256 을 잰다 — 첫 적재(원천 100만 행)면 그 글만 GB 다. 같은 글
(`json.dumps(…, sort_keys=True, ensure_ascii=False)`)을 **조각조각 이어 쓰며** 해시하면 지문이
바이트까지 같다(`tests/unit/test_relay_bundle.py` 가 틀의 것과 견준다). 크기(`_size` — 키
차례만 다른 같은 글)도 같이 센다.

파일은 틀의 `load` 와 같은 차례로 읽는다 — 폴더 안 이름 차례, 그다음 정의의 `*_order`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from relay_core import sp


@dataclass
class Batch:
    """묶음 파일 하나의 머리 — 행은 필요할 때 다시 읽는다(`rows`)."""

    type_slug: str
    path: Path
    workspace_slug: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def rows(self) -> list[Any]:
        body = json.loads(self.path.read_text(encoding="utf-8"))
        rows = body.get("rows") if isinstance(body, dict) else None
        return rows if isinstance(rows, list) else []


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def batches(run: Path, folder: str, manifest: dict[str, Any]) -> list[Batch]:
    """틀의 `_batches` 와 같은 차례 · 같은 머리 — 행은 들지 않는다(파일마다 한 번 읽고
    버린다)."""
    base = run / folder
    if not base.is_dir():
        return []
    found: list[Batch] = []
    for file in sorted(base.glob("*.json")):
        body = _read(file)
        if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
            continue
        found.append(
            Batch(
                type_slug=str(body.get("type_slug") or file.stem),
                path=file,
                workspace_slug=body.get("workspace_slug"),
                extra={
                    k: body[k] for k in ("aliases_mode", "mode") if body.get(k) is not None
                },
            )
        )
    order = [str(one) for one in manifest.get(f"{folder}_order") or []]
    return sorted(
        found,
        key=lambda one: order.index(one.type_slug) if one.type_slug in order else len(order),
    )


def _dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


@dataclass
class Payload:
    digest: str
    size: int
    """보내는 본문의 바이트(들여쓰기 없이 — `parts._size` 와 같다)."""
    edges: int


def _batch_pieces(one: Batch, *, objects: bool) -> Iterator[str]:
    head: dict[str, Any] = {"type_slug": one.type_slug, **one.extra}
    if objects:
        head["workspace_slug"] = one.workspace_slug
    head["rows"] = None  # 자리만 — 행은 흘려 쓴다
    yield "{"
    for index, key in enumerate(sorted(head)):
        yield (", " if index else "") + _dumps(key) + ": "
        if key != "rows":
            yield _dumps(head[key])
            continue
        yield "["
        first = True
        for row in one.rows():
            if isinstance(row, dict):
                yield ("" if first else ", ") + _dumps(_clean(row))
                first = False
        yield "]"
    yield "}"


def pieces(run: Path) -> Iterator[str]:
    """`json.dumps(sp_pipeline.payload(load(run)), sort_keys=True, ensure_ascii=False)` 의
    조각들 — 이어 붙이면 그 글과 같다."""
    manifest = _read(run / sp.pipeline.MANIFEST)
    manifest = manifest if isinstance(manifest, dict) else {}
    ontology = _read(run / sp.pipeline.ONTOLOGY)
    if not (
        isinstance(ontology, dict) and any(ontology.get(k) for k in sp.pipeline.ONTOLOGY_KEYS)
    ):
        ontology = None
    graves = _read(run / sp.pipeline.TOMBSTONES)
    if not (isinstance(graves, dict) and (graves.get("objects") or graves.get("relations"))):
        graves = None
    top: dict[str, Any] = {"ontology": ontology, "objects": None, "relations": None}
    if graves:
        top["tombstones"] = graves
    source = str(manifest.get("source") or "").strip()
    if source:
        top["source"] = source
    top.update(sp.pipeline.bundle_options(manifest))
    lists = {
        "objects": batches(run, sp.pipeline.OBJECTS_DIR, manifest),
        "relations": batches(run, sp.pipeline.RELATIONS_DIR, manifest),
    }
    yield "{"
    for index, key in enumerate(sorted(top)):
        yield (", " if index else "") + _dumps(key) + ": "
        if key not in lists:
            yield _dumps(top[key])
            continue
        yield "["
        for at, one in enumerate(lists[key]):
            if at:
                yield ", "
            yield from _batch_pieces(one, objects=key == "objects")
        yield "]"
    yield "}"


def payload(run: Path) -> Payload:
    """지문 · 크기 · 선의 줄 수 — 한 번 흘려 읽는다."""
    hasher = hashlib.sha256()
    size = 0
    for piece in pieces(run):
        raw = piece.encode("utf-8")
        hasher.update(raw)
        size += len(raw)
    manifest = _read(run / sp.pipeline.MANIFEST)
    edges = sum(
        sum(1 for row in one.rows() if isinstance(row, dict))
        for one in batches(
            run, sp.pipeline.RELATIONS_DIR, manifest if isinstance(manifest, dict) else {}
        )
    )
    return Payload(digest=hasher.hexdigest(), size=size, edges=edges)
