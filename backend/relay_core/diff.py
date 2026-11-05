"""출력 비교 — 이번 실행 폴더를 **지난번 적용된** 실행 폴더와 견줘 바뀐 것만 남긴다.

    지난번 적용된 출력 ──┐
                         ├─▶ 바뀐 객체 · 새 객체 · 바뀐 선 · 새 선  → objects/ · relations/
    이번 출력 ───────────┘   지난번엔 있고 이번엔 없는 것           → tombstones.json

기준이 「제출한 것」 이 아니라 **「적용된 것」** 이다. 계획이 아직 적용되지 않은 채 다음 실행이
돌면, 새 실행은 적용된 기준과 다시 견줘 대기 중인 계획을 대신한다 — 파이프라인마다 대기 계획은
하나다.

무덤은 `source` 가 붙은 묶음에만 있다(SP 연동 지침서 §6.4) — 그래서 `source` 를 꼭 받는다.
받는 쪽은 지우지 않고 사용 중지 · 끊기로 받는다.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from relay_core import sp

#: 선의 정체 — 세 끝과 유효 기간(SP ADR 0039: 기간까지가 선의 정체다).
_EDGE_KEYS = ("src", "relation", "dst", "valid_from", "valid_to")


@dataclass
class Counts:
    created: int = 0
    changed: int = 0
    unchanged: int = 0
    gone: int = 0

    @property
    def sends(self) -> int:
        return self.created + self.changed + self.gone


@dataclass
class Delta:
    run: Path
    objects: dict[str, Counts] = field(default_factory=dict)
    relations: dict[str, Counts] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        """보낼 것이 없나 — 그러면 대상에 미리보기도 안 올린다(검토함에 빈 계획이 쌓이지
        않게)."""
        return not any(one.sends for one in (*self.objects.values(), *self.relations.values()))


#: SP 의 「지우기」 표기 — 빈 값(`null` · `""`)은 「안 보냄」 이라 옛 값이 남는다(지침서 §7).
CLEAR = "\\null"


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """견줄 모양 — `_` 칸(출처 · 메모)과 **빈 값**은 뺀다. SP 에서 빈 값은 「안 보냄」 이라, 새
    칸이
    비어 있는 것은 바뀐 것이 아니다."""
    return {
        key: value
        for key, value in row.items()
        if not str(key).startswith("_") and not _empty(value)
    }


def _with_clears(row: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    """**원천에서 비워진 칸은 `\\null` 로** — 안 그러면 SP 는 「안 보냄」 으로 읽고 옛 값을
    그대로
    둔다(원천에서 지운 값이 대상에 영영 남는다)."""
    out = dict(row)
    for key in before:
        if key in ("key", "label") or _empty(before[key]):
            continue
        if _empty(out.get(key)):
            out[key] = CLEAR
    return out


def _rows_by_type(batches: Iterable[Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for batch in batches:
        out.setdefault(batch.type_slug, []).extend(
            row for row in batch.rows if isinstance(row, dict)
        )
    return out


def _workspace_of(batches: Iterable[Any]) -> dict[str, str | None]:
    return {batch.type_slug: batch.workspace_slug for batch in batches}


def _edge_id(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in _EDGE_KEYS)


def write_batches(
    folder: Path, kind: str, rows: dict[str, list[dict[str, Any]]], workspaces: dict[str, Any]
) -> list[str]:
    """타입마다 파일 — 5,000줄이 넘으면 `<slug>-001.json` … 으로 나눈다(SP 도구의 상한)."""
    names: list[str] = []
    (folder / kind).mkdir(parents=True, exist_ok=True)
    cap = int(sp.pipeline.MAX_ROWS)
    for slug, items in rows.items():
        if not items:
            continue
        chunks = [items[at : at + cap] for at in range(0, len(items), cap)]
        for index, chunk in enumerate(chunks, start=1):
            name = slug if len(chunks) == 1 else f"{slug}-{index:03d}"
            body: dict[str, Any] = {"type_slug": slug, "rows": chunk}
            if kind == sp.pipeline.OBJECTS_DIR:
                body["workspace_slug"] = workspaces.get(slug)
            (folder / kind / f"{name}.json").write_text(
                json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        names.append(slug)
    return names


def delta(
    previous: Path | None,
    current: Path,
    out: Path,
    *,
    source: str,
    options: dict[str, str] | None = None,
) -> Delta:
    """`out`(없어야 한다)에 바뀐 것만 담은 실행 폴더를 만든다. `previous` 가 없으면 첫 적재 —
    전부 보낸다(무덤 없음).

    `options` 는 묶음 칸(`bundle.json`) — 예: `{"human_edits": "overwrite"}`. 기본(`keep`)이면
    대상
    SP 가 **사람이 화면에서 고친 칸**을 덮지 않고 계획의 줄에 적는다. 운영 Datahub 에 처음 붙을
    때 그
    목록을 읽고, PLM 값으로 덮기로 정했으면 그 한 번만 `overwrite` 로 다시 미리 본다."""
    if not source.strip():
        raise ValueError(
            "source(원천 이름 — plm · mes …)가 필요합니다. 무덤은 source 묶음에만 있다."
        )
    if out.exists():
        raise FileExistsError(f"실행 폴더가 이미 있습니다: {out}")
    now = sp.pipeline.load(current)
    if now.problems:
        raise ValueError(f"이번 출력을 못 읽었습니다: {now.problems}")
    before = sp.pipeline.load(previous) if previous is not None else None

    result = Delta(run=out)
    now_objects = _rows_by_type(now.objects)
    old_objects = _rows_by_type(before.objects) if before else {}
    send_objects: dict[str, list[dict[str, Any]]] = {}
    graves_objects: list[dict[str, Any]] = []
    for slug in sorted(set(now_objects) | set(old_objects)):
        counts = result.objects.setdefault(slug, Counts())
        old = {str(row.get("key")): _clean(row) for row in old_objects.get(slug, [])}
        fresh = {str(row.get("key")): row for row in now_objects.get(slug, [])}
        for key, row in fresh.items():
            if key not in old:
                counts.created += 1
            elif _clean(row) != old[key]:
                counts.changed += 1
                row = _with_clears(row, old[key])
            else:
                counts.unchanged += 1
                continue
            send_objects.setdefault(slug, []).append(row)
        for key in sorted(set(old) - set(fresh)):
            counts.gone += 1
            graves_objects.append({"type_slug": slug, "key": key, "merged_into": None})

    now_edges = _rows_by_type(now.relations)
    old_edges = _rows_by_type(before.relations) if before else {}
    send_edges: dict[str, list[dict[str, Any]]] = {}
    graves_edges: list[dict[str, Any]] = []
    for slug in sorted(set(now_edges) | set(old_edges)):
        counts = result.relations.setdefault(slug, Counts())
        old_e = {_edge_id(row): _clean(row) for row in old_edges.get(slug, [])}
        fresh_e = {_edge_id(row): row for row in now_edges.get(slug, [])}
        for ident, row in fresh_e.items():
            if ident not in old_e:
                counts.created += 1
            elif _clean(row) != old_e[ident]:
                counts.changed += 1
            else:
                counts.unchanged += 1
                continue
            send_edges.setdefault(slug, []).append(row)
        for ident in sorted(set(old_e) - set(fresh_e), key=repr):
            counts.gone += 1
            src, relation, dst, valid_from, valid_to = ident
            grave: dict[str, Any] = {
                "type_slug": slug,
                "src": src,
                "relation": relation,
                "dst": dst,
            }
            if valid_from is not None or valid_to is not None:
                grave.update(valid_from=valid_from, valid_to=valid_to, period=True)
            graves_edges.append(grave)

    out.mkdir(parents=True)
    objects_order = write_batches(
        out, sp.pipeline.OBJECTS_DIR, send_objects, _workspace_of(now.objects)
    )
    relations_order = write_batches(out, sp.pipeline.RELATIONS_DIR, send_edges, {})
    manifest = dict(now.manifest)
    # 차례는 이번 출력의 것을 따른다 — 참조되는 타입이 먼저다(레시피의 `types` 차례).
    manifest["objects_order"] = [
        slug for slug in now.manifest.get("objects_order", []) if slug in objects_order
    ] or objects_order
    manifest["relations_order"] = [
        slug for slug in now.manifest.get("relations_order", []) if slug in relations_order
    ] or relations_order
    manifest["source"] = source
    manifest.update(options or {})
    (out / sp.pipeline.MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if graves_objects or graves_edges:
        (out / sp.pipeline.TOMBSTONES).write_text(
            json.dumps(
                {"objects": graves_objects, "relations": graves_edges},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    for name in (sp.pipeline.ONTOLOGY, sp.pipeline.UNRESOLVED):
        if (current / name).exists():
            shutil.copyfile(current / name, out / name)
    return result
