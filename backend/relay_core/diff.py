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
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from relay_core import sp, spill

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


def _head(path: Path) -> tuple[dict[str, Any], list[str]]:
    """`sp_pipeline.load` 의 머리 쪽 검사 그대로 — 객체 · 선 파일은 따로 흘려 읽는다
    (`_batches`)."""
    pipeline = sp.pipeline
    problems: list[str] = []
    manifest = pipeline._read_json(path / pipeline.MANIFEST, problems)
    if manifest is None and not (path / pipeline.MANIFEST).exists():
        problems.append(f"{pipeline.MANIFEST} 가 없습니다 — 먼저 init 하세요")
    for name in (pipeline.ONTOLOGY, pipeline.TOMBSTONES):
        body = pipeline._read_json(path / name, problems)
        if body is not None and not isinstance(body, dict):
            problems.append(f"{name}: 객체({{...}})여야 합니다")
    pipeline._read_json(path / pipeline.UNRESOLVED, problems)
    return (manifest if isinstance(manifest, dict) else {}), problems


def _batches(
    path: Path, folder: str, problems: list[str]
) -> Iterator[tuple[str, Any, list[Any]]]:
    """`sp_pipeline._batches` 의 파일 하나씩 — (타입, workspace_slug, 줄). 파일 이름 차례다.
    틀은 그 뒤 타입 차례(`<folder>_order`)로 다시 세우지만 **같은 타입 안의 차례는 그대로**라
    견주기에는 필요 없다."""
    base = path / folder
    if not base.is_dir():
        return
    for file in sorted(base.glob("*.json")):
        body = sp.pipeline._read_json(file, problems)
        if body is None:
            continue
        if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
            problems.append(f'{folder}/{file.name}: {{"rows": [...]}} 모양이어야 합니다')
            continue
        yield str(body.get("type_slug") or file.stem), body.get("workspace_slug"), body["rows"]


def _files(path: Path | None) -> int:
    if path is None:
        return 0
    return sum(
        len(list((path / folder).glob("*.json")))
        for folder in (sp.pipeline.OBJECTS_DIR, sp.pipeline.RELATIONS_DIR)
    )


def _ident_of(kind: str) -> Callable[[dict[str, Any]], Any]:
    if kind == sp.pipeline.OBJECTS_DIR:
        return lambda row: str(row.get("key"))
    return _edge_id


@dataclass
class _Compared:
    counts: dict[str, Counts]
    sent: spill.Spill
    graves: list[tuple[str, Any]]
    workspaces: dict[str, Any]


def _compare(
    kind: str,
    previous: Path | None,
    current: Path,
    folder: Path,
    count: int,
    problems: list[str],
) -> _Compared:
    """한 갈래(객체 · 선)를 바구니마다 견준다 — 같은 정체(타입 · 식별자 · 선의 세 끝)는 늘 한
    바구니다. 보낼 줄은 바구니 안에서 (타입, 이번 출력의 차례)로 세워 둔다(`spill.merged` 가
    잇는다)."""
    ident = _ident_of(kind)
    objects = kind == sp.pipeline.OBJECTS_DIR
    slugs: set[str] = set()
    workspaces: dict[str, Any] = {}
    olds = spill.Spill(folder / f"{kind}-old", count)
    news = spill.Spill(folder / f"{kind}-new", count)
    if previous is not None:
        ignored: list[str] = []  # 기준의 문제는 틀도 보지 않는다(읽힌 것만 견준다)
        for slug, _workspace, rows in _batches(previous, kind, ignored):
            slugs.add(slug)
            for row in rows:
                if isinstance(row, dict):
                    one = ident(row)
                    text = one if objects else json.dumps(one, ensure_ascii=False)
                    olds.put(spill.bucket_of(text, count), [slug, one, _clean(row)])
    index = 0
    for slug, workspace, rows in _batches(current, kind, problems):
        slugs.add(slug)
        workspaces[slug] = workspace
        for row in rows:
            if isinstance(row, dict):
                one = ident(row)
                text = one if objects else json.dumps(one, ensure_ascii=False)
                news.put(spill.bucket_of(text, count), [slug, index, row])
                index += 1
    olds.close()
    news.close()

    counts = {slug: Counts() for slug in sorted(slugs)}
    sent = spill.Spill(folder / f"{kind}-sent", count)
    graves: list[tuple[str, Any]] = []
    for bucket in range(count):
        old: dict[tuple[str, Any], dict[str, Any]] = {}
        for slug, one, cleaned in olds.read(bucket):
            old[(slug, one if objects else tuple(one))] = cleaned
        fresh: dict[tuple[str, Any], list[Any]] = {}
        for slug, at, row in news.read(bucket):
            key = (slug, ident(row))
            hit = fresh.get(key)
            if hit is None:
                fresh[key] = [at, row]
            else:
                hit[1] = row  # 같은 정체가 두 번이면 뒤의 줄 · 앞의 자리(틀의 딕셔너리와 같다)
        out: list[list[Any]] = []
        for key, (at, row) in fresh.items():
            tally = counts[key[0]]
            before = old.get(key)
            if before is None:
                tally.created += 1
            elif _clean(row) != before:
                tally.changed += 1
                if objects:
                    row = _with_clears(row, before)
            else:
                tally.unchanged += 1
                continue
            out.append([key[0], at, row])
        out.sort(key=lambda item: (item[0], item[1]))
        for item in out:
            sent.put(bucket, item)
        graves.extend(key for key in old if key not in fresh)
        del old, fresh, out
    sent.close()
    olds.drop()
    news.drop()
    for slug, _one in graves:
        counts[slug].gone += 1
    if objects:
        graves.sort()
    else:
        graves.sort(key=lambda key: (key[0], repr(key[1])))
    return _Compared(counts, sent, graves, workspaces)


def _write_sorted(
    folder: Path, kind: str, rows: Iterator[list[Any]], workspaces: dict[str, Any]
) -> list[str]:
    """`write_batches` 와 같은 파일을 **흘려서** — 줄이 (타입, 차례)로 온다. 한 타입의 줄이
    `MAX_ROWS` 를 넘는지는 다음 조각의 첫 줄이 와야 안다 — 찬 조각 하나를 들고 기다린다."""
    names: list[str] = []
    (folder / kind).mkdir(parents=True, exist_ok=True)
    cap = int(sp.pipeline.MAX_ROWS)
    slug: str | None = None
    chunk: list[dict[str, Any]] = []
    written = 0

    def write(name: str, part: list[dict[str, Any]]) -> None:
        body: dict[str, Any] = {"type_slug": slug, "rows": part}
        if kind == sp.pipeline.OBJECTS_DIR:
            body["workspace_slug"] = workspaces.get(str(slug))
        (folder / kind / f"{name}.json").write_text(
            json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def finish() -> None:
        if slug is None or not chunk:
            return
        write(f"{slug}-{written + 1:03d}" if written else str(slug), chunk)
        names.append(str(slug))

    for item_slug, _at, row in rows:
        if item_slug != slug:
            finish()
            slug, chunk, written = item_slug, [], 0
        elif len(chunk) == cap:
            written += 1
            write(f"{slug}-{written:03d}", chunk)
            chunk = []
        chunk.append(row)
    finish()
    return names


def delta(
    previous: Path | None,
    current: Path,
    out: Path,
    *,
    source: str,
    options: dict[str, str] | None = None,
    bucket_rows: int = spill.BUCKET_ROWS,
    scratch: Path | None = None,
) -> Delta:
    """`out`(없어야 한다)에 바뀐 것만 담은 실행 폴더를 만든다. `previous` 가 없으면 첫 적재 —
    전부 보낸다(무덤 없음).

    `options` 는 묶음 칸(`bundle.json`) — 예: `{"human_edits": "overwrite"}`. 기본(`keep`)이면
    대상 SP 가 **사람이 화면에서 고친 칸**을 덮지 않고 계획의 줄에 적는다. 운영 Datahub 에
    처음 붙을 때 그 목록을 읽고, PLM 값으로 덮기로 정했으면 그 한 번만 `overwrite` 로 다시
    미리 본다.

    **두 출력을 메모리에 들지 않는다** — 정체(타입 · 식별자 · 선의 세 끝)의 해시로 바구니에
    나눠 바구니마다 견주고(`bucket_rows` 줄씩), 보낼 줄은 이번 출력의 차례로 다시 잇는다.
    바구니는 `scratch`(없으면 `out` 옆)에 두고 끝나면 지운다. 결과는 한꺼번에 견준 것과 같다
    (`tests/unit/test_relay_core.py`)."""
    if not source.strip():
        raise ValueError(
            "source(원천 이름 — plm · mes …)가 필요합니다. 삭제 표식은 source 묶음에만 "
            "있습니다."
        )
    if out.exists():
        raise FileExistsError(f"실행 폴더가 이미 있습니다: {out}")
    manifest_now, problems = _head(current)
    if problems:
        raise ValueError(f"이번 출력을 못 읽었습니다: {problems}")
    count = spill.count_for(
        max(_files(previous), _files(current)) * int(sp.pipeline.MAX_ROWS), bucket_rows
    )
    folder = scratch or out.with_name(f"{out.name}.spill")
    compared: list[_Compared] = []
    try:
        for kind in (sp.pipeline.OBJECTS_DIR, sp.pipeline.RELATIONS_DIR):
            compared.append(_compare(kind, previous, current, folder, count, problems))
            if problems:
                raise ValueError(f"이번 출력을 못 읽었습니다: {problems}")
        objects, edges = compared
        out.mkdir(parents=True)
        objects_order = _write_sorted(
            out,
            sp.pipeline.OBJECTS_DIR,
            spill.merged(objects.sent, key=lambda item: (item[0], item[1])),
            objects.workspaces,
        )
        relations_order = _write_sorted(
            out,
            sp.pipeline.RELATIONS_DIR,
            spill.merged(edges.sent, key=lambda item: (item[0], item[1])),
            {},
        )
    finally:
        for one in compared:
            one.sent.drop()
        shutil.rmtree(folder, ignore_errors=True)

    result = Delta(run=out, objects=objects.counts, relations=edges.counts)
    graves_objects = [
        {"type_slug": slug, "key": key, "merged_into": None} for slug, key in objects.graves
    ]
    graves_edges: list[dict[str, Any]] = []
    for slug, ident in edges.graves:
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

    manifest = dict(manifest_now)
    # 차례는 이번 출력의 것을 따른다 — 참조되는 타입이 먼저다(레시피의 `types` 차례).
    manifest["objects_order"] = [
        slug for slug in manifest_now.get("objects_order", []) if slug in objects_order
    ] or objects_order
    manifest["relations_order"] = [
        slug for slug in manifest_now.get("relations_order", []) if slug in relations_order
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
