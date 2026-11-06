"""나눠 보내기 — 보낼 묶음이 대상 SP 가 한 번에 받는 크기를 넘으면 **조각**으로.

대상 SP 는 묶음 하나를 작업 파일로 받아(`job_file_max_bytes`, 기본 100MB — 넘으면 413) **전부
아니면 무**로 넣는다. 첫 적재는 원천 30만 행쯤에서 그 상한을 넘었다(2026-11-06 대용량 시험).
그래서 묶음이 조각 상한(`target.part_mb`, 기본 8MB)을 넘거나 선이 5,000줄(`MAX_EDGES`)을
넘으면 `sends/<때>/parts/001` … 로 나눈다. 조각 하나하나가 그대로 SP 의 실행 폴더다.

    차례     객체(참조되는 타입이 먼저 — `objects_order`) → 선 → 무덤. 뒤 조각은 앞
             조각이 만든 객체를 가리킨다.
    미리 보기  **조각은 차례로만 미리 본다.** SP 는 적용할 때 미리 본 계획과 지문이
             같은지 본다 — 앞 조각이 들어가기 전에 뒤 조각을 미리 보면(가리킬 객체가
             아직 없다) 그 계획은 적용되지 않는다. 그래서 실행은 첫 조각만 미리 보고,
             「적용」 이 조각마다 미리 보기 → 적용을 잇는다(`runner.apply`).
    멈춤     뒤 조각의 미리 보기에 오류가 나면 거기서 멈춘다 — 앞 조각들은 들어가 있다.
             원인을 고친 뒤 「적용」 이 그 조각부터 다시 미리 본다.
    접기     일부만 들어간 채 원천이 바뀌면, 들어간 조각을 기준에 접어 넣고(`fold`)
             거기서 다시 견준다 — 안 그러면 들어간 것을 모른 채 견줘 무덤을 빠뜨린다.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from relay_core import sp
from relay_core.diff import _edge_id, _rows_by_type, write_batches

#: 조각 상한의 기본(MB) — 대상 SP 의 상한(100MB)보다 넉넉히 작게. SP 는 받은 묶음을 메모리에
#: 다 올려 푼다. 객체는 개수에 비례해 들어간다(8MB · 모델 6.4만 개에 미리 보기 22초,
#: 2026-11-06 진짜 SP 실측).
DEFAULT_MB = 8
#: 조각마다 **선은 이만큼까지** — SP 가 선을 넣는 시간은 한 묶음 안의 선 수의 제곱으로 는다
#: (선 r 천 줄이면 약 0.4 * r^2 초 — 4.2만 줄 조각의 미리 보기가 692초, 2026-11-06 실측). 선
#: 줄은 작아 바이트로는 한 조각에 수만 줄이 들어가므로 따로 센다. SP 가 한 번에 받는 배치
#: (5,000줄)와 같다.
MAX_EDGES = 5000
PARTS = "parts"
#: 조각마다 머리(정의 · 묶음 칸 · 배치의 이름)에 남겨 두는 몫.
_HEAD = 64 * 1024


def _size(body: Any) -> int:
    """`sp_pipeline._post_bundle` 이 보내는 모양(들여쓰기 없이)으로 잰 바이트."""
    return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))


def _sent(row: dict[str, Any]) -> dict[str, Any]:
    """보낼 때 떼는 `_` 칸(출처 · 메모)을 뺀 모양 — `sp_pipeline.payload` 와 같이."""
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


def on_disk(send: Path) -> tuple[int, int]:
    """파일로 잰 크기 · 선 파일 수 — 들여쓰기가 있어 보내는 본문보다 늘 크고, 선 파일은 5,000줄
    까지라 하나면 `MAX_EDGES` 를 넘지 않는다(둘 다 작으면 열어 볼 것도 없다)."""
    edges = list((send / sp.pipeline.RELATIONS_DIR).glob("*.json"))
    files = [send / sp.pipeline.TOMBSTONES, *edges]
    files += list((send / sp.pipeline.OBJECTS_DIR).glob("*.json"))
    return sum(one.stat().st_size for one in files if one.is_file()), len(edges)


@dataclass
class Split:
    parts: list[Path] = field(default_factory=list)
    """조각 폴더들 — 비면 나누지 않았다(`send` 하나로 간다)."""
    size: int = 0
    """보내는 본문 전체의 바이트(나누지 않았으면 파일로 잰 크기일 수 있다)."""
    digest: str = ""
    """전체 묶음의 지문 — 나눴을 때만(대기 계획과 같은 것인지 견준다)."""


@dataclass
class _Part:
    objects: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    relations: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    graves: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"objects": [], "relations": []}
    )
    used: int = _HEAD
    edges: int = 0

    @property
    def empty(self) -> bool:
        graves = self.graves["objects"] or self.graves["relations"]
        return not (self.objects or self.relations or graves)


def split(send: Path, limit: int, max_edges: int | None = None) -> Split:
    """`send` 의 본문이 `limit` 바이트를 넘거나 선이 `max_edges`(기본 `MAX_EDGES`) 줄을 넘으면
    `send/parts/` 에 조각을 만든다."""
    max_edges = MAX_EDGES if max_edges is None else max_edges
    disk, edge_files = on_disk(send)
    if disk <= limit and edge_files <= 1 and max_edges >= sp.pipeline.MAX_ROWS:
        return Split(size=disk)
    run = sp.pipeline.load(send)
    body = sp.pipeline.payload(run)
    whole = _size(body)
    digest = str(sp.pipeline.digest(body))
    edges = sum(len(batch.rows) for batch in run.relations)
    if whole <= limit and edges <= max_edges:
        return Split(size=whole, digest=digest)

    parts: list[_Part] = [_Part()]

    def put(cost: int, edge: bool = False) -> _Part:
        last = parts[-1]
        full = last.used + cost > limit or (edge and last.edges >= max_edges)
        if full and not last.empty:
            parts.append(_Part())
        parts[-1].used += cost
        parts[-1].edges += edge
        return parts[-1]

    for batch in run.objects:  # 차례는 `objects_order` 그대로(load 가 맞춘다)
        for row in batch.rows:
            if isinstance(row, dict):
                part = put(_size(_sent(row)) + 1)
                part.objects.setdefault(batch.type_slug, []).append(row)
    for batch in run.relations:
        for row in batch.rows:
            if isinstance(row, dict):
                part = put(_size(_sent(row)) + 1, edge=True)
                part.relations.setdefault(batch.type_slug, []).append(row)
    graves = run.tombstones or {}
    for kind in ("objects", "relations"):
        for grave in graves.get(kind) or []:
            put(_size(grave) + 1).graves[kind].append(grave)

    workspaces = {batch.type_slug: batch.workspace_slug for batch in run.objects}
    folders = []
    for number, part in enumerate(parts, start=1):
        folder = send / PARTS / f"{number:03d}"
        _write(folder, run.manifest, part, workspaces, number, len(parts))
        for name in (sp.pipeline.ONTOLOGY, sp.pipeline.UNRESOLVED):
            if (send / name).exists():
                shutil.copyfile(send / name, folder / name)
        folders.append(folder)
    return Split(parts=folders, size=whole, digest=digest)


def _write(
    folder: Path,
    manifest: dict[str, Any],
    part: _Part,
    workspaces: dict[str, Any],
    number: int,
    total: int,
) -> None:
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    objects = write_batches(folder, sp.pipeline.OBJECTS_DIR, part.objects, workspaces)
    relations = write_batches(folder, sp.pipeline.RELATIONS_DIR, part.relations, {})
    head = dict(manifest)
    head["objects_order"] = [
        slug for slug in manifest.get("objects_order") or [] if slug in objects
    ] or objects
    head["relations_order"] = [
        slug for slug in manifest.get("relations_order") or [] if slug in relations
    ] or relations
    head["title"] = f"{manifest.get('title') or '묶음'} — 조각 {number}/{total}"
    head["part"] = {"number": number, "total": total}
    (folder / sp.pipeline.MANIFEST).write_text(
        json.dumps(head, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if part.graves["objects"] or part.graves["relations"]:
        (folder / sp.pipeline.TOMBSTONES).write_text(
            json.dumps(part.graves, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _grave_edge(grave: dict[str, Any]) -> tuple[Any, ...]:
    """선 무덤의 정체 — `diff._edge_id` 와 같은 차례(기간이 없으면 None)."""
    return (
        grave.get("src"),
        grave.get("relation"),
        grave.get("dst"),
        grave.get("valid_from"),
        grave.get("valid_to"),
    )


def fold(baseline: Path | None, full: Path, applied: list[Path], out: Path) -> None:
    """**들어간 조각을 기준에 접는다** — `baseline`(지난번 적용된 출력, 첫 적재면 없음) 위에
    조각들이 보낸 줄을 `full`(그 계획의 전체 출력)의 모양으로 얹고, 무덤은 뺀다. 결과는
    `out` 에 실행 폴더로(있으면 바꿔 끼운다) — 대상에 지금 들어가 있는 것과 같다."""
    before = sp.pipeline.load(baseline) if baseline is not None else None
    now = sp.pipeline.load(full)
    objects = {
        slug: {str(row.get("key")): row for row in rows}
        for slug, rows in _rows_by_type(before.objects if before else []).items()
    }
    edges = {
        slug: {_edge_id(row): row for row in rows}
        for slug, rows in _rows_by_type(before.relations if before else []).items()
    }
    fresh = {
        slug: {str(row.get("key")): row for row in rows}
        for slug, rows in _rows_by_type(now.objects).items()
    }
    fresh_edges = {
        slug: {_edge_id(row): row for row in rows}
        for slug, rows in _rows_by_type(now.relations).items()
    }
    for folder in applied:
        part = sp.pipeline.load(folder)
        for slug, rows in _rows_by_type(part.objects).items():
            for row in rows:
                key = str(row.get("key"))
                objects.setdefault(slug, {})[key] = fresh.get(slug, {}).get(key, row)
        for slug, rows in _rows_by_type(part.relations).items():
            for row in rows:
                ident = _edge_id(row)
                edges.setdefault(slug, {})[ident] = fresh_edges.get(slug, {}).get(ident, row)
        graves = part.tombstones or {}
        for grave in graves.get("objects") or []:
            objects.get(str(grave.get("type_slug")), {}).pop(str(grave.get("key")), None)
        for grave in graves.get("relations") or []:
            edges.get(str(grave.get("type_slug")), {}).pop(_grave_edge(grave), None)

    scratch = out.with_name(out.name + ".tmp")
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    workspaces = {batch.type_slug: batch.workspace_slug for batch in now.objects}
    write_batches(
        scratch,
        sp.pipeline.OBJECTS_DIR,
        {slug: list(rows.values()) for slug, rows in objects.items()},
        workspaces,
    )
    write_batches(
        scratch,
        sp.pipeline.RELATIONS_DIR,
        {slug: list(rows.values()) for slug, rows in edges.items()},
        {},
    )
    manifest = dict(now.manifest)
    manifest["folded"] = {"from": baseline.name if baseline else None, "parts": len(applied)}
    (scratch / sp.pipeline.MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name in (sp.pipeline.ONTOLOGY, sp.pipeline.UNRESOLVED):
        if (full / name).exists():
            shutil.copyfile(full / name, scratch / name)
    if out.exists():
        shutil.rmtree(out)
    scratch.rename(out)
