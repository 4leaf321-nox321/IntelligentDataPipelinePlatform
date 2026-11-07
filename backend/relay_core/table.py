"""원천 표 → 실행 폴더를 **나눠서** — 틀의 변환기(`sp_table.convert`)와 같은 결과를, 표를
메모리에 통째로 들지 않고.

틀의 변환기는 표 전체를 읽어 들고(행마다 딕셔너리) 타입마다 모든 행을 한꺼번에 모은다 — 원천
20만 행에 0.85GB, 50만 행에 2GB 남짓(2026-11-06 실측). 여기서는

1. 표를 **파일에서 흘려 읽는다**(`_Rows` — 돌 때마다 파일을 다시 연다). 검사 · 행 번호 · 지문은
   틀의 `read_table` 과 같다.
2. 행이 바구니 하나(`spill.BUCKET_ROWS`)를 넘으면 타입마다 **식별자의 해시로** · 관계마다
   **출발점의 해시로** 행을 바구니에 나눠 둔다 — 그 타입이 읽는 칸만. 같은 식별자의 행은 한
   바구니에 원래 차례로 들므로, 틀의 모으기(`gather` · `gather_edges`)를 바구니마다 불러도
   객체 · 선 · 갈린 값이 같다.
3. 바구니의 결과를 **첫 행 차례로** 다시 이어(`spill.merged`) 틀과 같은 이름 · 같은 크기의
   파일로 쓴다. 미해결도 틀의 차례로 다시 세운다.

같다는 것은 시험이 묶는다(`tests/unit/test_relay_table.py` — 틀의 변환기와 파일을 바이트로
견준다). 보고서(`table-report.txt`)의 수는 같고, **횟수가 같은 예시끼리의 차례만** 다를 수 있다
(바구니마다 처음 본 차례다). 바구니가 하나면(작은 표) 보고서까지 같다.

틀의 해석기(`parsers`)는 행마다 해석을 기억한다 — 해석기를 쓰는 레시피는 그 몫이 행 수에 비례해
남는다(틀 그대로 둔다).
"""

from __future__ import annotations

import codecs
import csv
import dataclasses
import hashlib
import json
import shutil
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from relay_core import aliases, sp, spill

Row = tuple[int, dict[str, str]]


class _Rows:
    """`Table.rows` 의 자리 — 돌 때마다 `read()` 로 새로 읽는다. 수는 미리 센 것."""

    def __init__(self, read: Callable[[], Iterator[Row]], count: int) -> None:
        self._read, self._count = read, count

    def __iter__(self) -> Iterator[Row]:
        return self._read()

    def __len__(self) -> int:
        return self._count


class _Sized:
    """보고서는 객체 · 선의 **수**만 본다(`len`) — 바구니에서 흘려 쓴 것을 다시 들지 않는다."""

    def __init__(self, count: int) -> None:
        self._count = count

    def __len__(self) -> int:
        return self._count


def _decodes(path: Path, encoding: str) -> bool:
    decoder = codecs.getincrementaldecoder(encoding)()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


def _records(path: Path, encoding: str, delimiter: str) -> Iterator[tuple[int, list[str]]]:
    """머리글 뒤의 (행 번호, 칸들) — 머리글이 1행."""
    with path.open(encoding=encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        next(reader, None)
        yield from enumerate(reader, start=2)


def read_table(path: Path, *, encoding: str = "auto", delimiter: str = ",") -> Any:
    """틀의 `read_table` 과 같은 검사 · 같은 `Table` — 다만 `rows` 는 흘려 읽는다."""
    stop = sp.table.Stop
    if not path.is_file():
        raise stop(f"원천을 읽을 수 없습니다: {path}")
    tried = sp.table.ENCODINGS if encoding == "auto" else (encoding,)
    name = next((one for one in tried if _decodes(path, one)), None)
    if name is None:
        raise stop(
            f"{path.name}: 글자를 읽을 수 없습니다({', '.join(tried)}) — "
            "대응 파일의 source.encoding 에 인코딩을 적으세요"
        )
    with path.open(encoding=name, newline="") as handle:
        header = [one.strip() for one in next(csv.reader(handle, delimiter=delimiter), [])]
    if not any(header):
        raise stop(f"{path.name}: 첫 줄(머리글)이 비어 있습니다")
    doubled = sorted(one for one, count in Counter(header).items() if count > 1 and one)
    if doubled:
        raise stop(f"{path.name}: 머리글에 같은 이름이 둘 이상입니다 — {', '.join(doubled)}")
    count = blank = 0
    for number, record in _records(path, name, delimiter):
        if not any(cell.strip() for cell in record):
            blank += 1
            continue
        if len(record) > len(header) and any(cell.strip() for cell in record[len(header) :]):
            raise stop(f"{path.name} {number}행: 머리글보다 칸이 많습니다({len(record)}칸)")
        count += 1
    digest = hashlib.sha256()
    with path.open("rb") as raw:
        while block := raw.read(1 << 20):
            digest.update(block)

    def rows() -> Iterator[Row]:
        for number, record in _records(path, name, delimiter):
            if not any(cell.strip() for cell in record):
                continue
            yield (
                number,
                {one: (record[i] if i < len(record) else "") for i, one in enumerate(header)},
            )

    return sp.table.Table(
        name=path.name,
        encoding=name,
        header=header,
        rows=_Rows(rows, count),
        blank_rows=blank,
        sha256=digest.hexdigest(),
    )


# --------------------------------------------------------------------------
# 바구니 — 타입(관계)마다 식별자(출발점)의 해시로
# --------------------------------------------------------------------------


def _columns(mapping: Any, specs: list[Any]) -> list[str]:
    """`specs` 를 값으로 바꿀 때 읽는 칸 — `Mapping.value` 의 차례 그대로(모으기 → 고정값 →
    `key_of` → 해석기 → 칸). 바구니에는 이 칸만 둔다."""
    found: dict[str, None] = {}

    def walk(spec: Any) -> None:
        if not isinstance(spec, dict):
            return
        for mode in sp.table.AGGREGATES:
            if mode in spec:
                walk(spec[mode])
                return
        if "value" in spec:
            return
        if "key_of" in spec:
            walk(mapping.by_slug[spec["key_of"]]["key"])
            return
        if "parser" in spec:
            parser = mapping.parsers[str(spec["parser"])]
            found[parser.column] = None
            for check in parser.checks:
                for name in ("suffix_of", "when_column"):
                    if check.get(name):
                        found[str(check[name])] = None
            return
        if "column" in spec:
            found[str(spec["column"])] = None

    for one in specs:
        walk(one)
    return list(found)


@dataclasses.dataclass
class _Phase:
    """모으기 한 번 — 타입 하나 또는 관계 하나."""

    one: dict[str, Any]
    edge: bool
    names: list[str]
    """값을 뽑는 칸의 이름 — 틀이 한 행에서 부르는 차례(미해결의 차례를 세울 때)."""
    columns: list[str]
    at: str
    """`Mapping` 이 문제 · 참조 대조를 적는 자리의 앞쪽 — 타입 slug · `관계 <slug>`."""

    @property
    def lead(self) -> Any:
        return self.one["src"] if self.edge else self.one["key"]


def _phases(mapping: Any) -> list[_Phase]:
    out: list[_Phase] = []
    for one in mapping.types:
        specs = {"key": one["key"], "label": one.get("label") or one["key"]}
        specs.update(one.get("fields") or {})
        out.append(
            _Phase(
                one,
                False,
                list(specs),
                _columns(mapping, list(specs.values())),
                one["type_slug"],
            )
        )
    for one in mapping.relations:
        specs = {"src": one["src"], "dst": one["dst"], **(one.get("properties") or {})}
        if "evidence_note" in one:
            specs["evidence_note"] = one["evidence_note"]
        out.append(
            _Phase(
                one,
                True,
                list(specs),
                _columns(mapping, list(specs.values())),
                f"관계 {one['relation']}",
            )
        )
    return out


class _Scratch:
    """바구니를 고르려고 식별자 · 출발점을 **한 번 더** 뽑는 `Mapping` — 진짜 쪽의 통계(문제 ·
    참조 대조 · 해석기)를 건드리지 않게 따로 두고, 그 통계는 버린다. 참조 대조의 답(플랫폼이
    별칭으로 푼 것)은 진짜 쪽 것을 함께 쓴다 — 같은 값이면 같은 바구니다."""

    def __init__(self, mapping: Any, spec: dict[str, Any], keys: Any) -> None:
        self.mapping = sp.table.Mapping(spec, mapping.table, keys=keys)
        self.mapping.matchers = mapping.matchers
        self._seen = 0

    def text(self, phase: _Phase, number: int, row: dict[str, str]) -> str:
        value = self.mapping.value(phase.lead, number, row, at=(phase.at, "lead"))
        self._seen += 1
        if self._seen % 1000 == 0:
            self.forget()
        return "" if value in (None, "") else str(value)

    def forget(self) -> None:
        self.mapping.stats.clear()
        self.mapping.match_stats.clear()
        self.mapping.match_unresolved.clear()
        for parser in self.mapping.parsers.values():
            getattr(parser, "_cache", {}).clear()
            parser.stats = sp.table.ParserStats()


def _first_rows(
    scratch: _Scratch, phase: _Phase, rows: list[Row]
) -> dict[tuple[str, str], int]:
    """선의 세 끝이 처음 나온 행 — 틀의 선은 출처를 적지 않아, 바구니를 이을 차례를 따로
    센다."""
    first: dict[tuple[str, str], int] = {}
    for number, row in rows:
        src = scratch.mapping.value(phase.one["src"], number, row, at=(phase.at, "src"))
        dst = scratch.mapping.value(phase.one["dst"], number, row, at=(phase.at, "dst"))
        if src in (None, "") or dst in (None, ""):
            continue
        first.setdefault((str(src), str(dst)), number)
    scratch.forget()
    return first


def _min_row(item: dict[str, Any]) -> int:
    """미해결 한 건이 처음 나온 행 — 갈린 값의 행들 중 가장 앞."""
    rows = (item.get("_source") or {}).get("rows") or []
    numbers = [
        one for group in rows for one in (group if isinstance(group, list) else [group])
    ]
    return min((int(one) for one in numbers), default=0)


@dataclasses.dataclass
class _Gathered:
    result: Any
    """틀의 `TypeResult` · `EdgeResult` — 줄은 `_Sized`(수만)."""
    spill: spill.Spill | None
    """바구니마다 이은 줄 — 없으면 `rows` 에 다 들었다(바구니 하나)."""
    rows: list[Any]


def _gather(
    mapping: Any, scratch: _Scratch | None, phases: list[_Phase], folder: Path, count: int
) -> list[_Gathered]:
    table = mapping.table
    if scratch is None or count == 1:
        # 작은 표 — 틀의 모으기를 그대로(행은 흘려 읽는다).
        out: list[_Gathered] = []
        for phase in phases:
            if phase.edge:
                result = sp.table.gather_edges(mapping, phase.one)
                out.append(_Gathered(result, None, result.rows))
            else:
                result = sp.table.gather(mapping, phase.one)
                out.append(_Gathered(result, None, result.objects))
        return out

    # 1. 나눠 둔다 — 한 번 흘려 읽으며 모으기마다 바구니로.
    sorted_in = [
        spill.Spill(folder / f"in-{index:02d}", count) for index in range(len(phases))
    ]
    for number, row in table.rows:
        for phase, bin_ in zip(phases, sorted_in, strict=True):
            bucket = spill.bucket_of(scratch.text(phase, number, row), count)
            bin_.put(bucket, [number, *(row[one] for one in phase.columns)])
    for bin_ in sorted_in:
        bin_.close()

    # 2. 바구니마다 틀의 모으기 — 결과는 바구니 안에서 첫 행 차례다.
    gathered: list[_Gathered] = []
    for index, (phase, bin_) in enumerate(zip(phases, sorted_in, strict=True)):
        before = {key: len(value) for key, value in mapping.match_unresolved.items()}
        bucketed = spill.Spill(folder / f"out-{index:02d}", count)
        total = blank = conflicts_n = 0
        conflicts: Counter[str] = Counter()
        unresolved: list[dict[str, Any]] = []
        workspace: str | None = None
        for bucket in range(count):
            rows: list[Row] = [
                (int(item[0]), dict(zip(phase.columns, item[1:], strict=True)))
                for item in bin_.read(bucket)
            ]
            mapping.table = dataclasses.replace(table, rows=rows)
            try:
                if phase.edge:
                    result = sp.table.gather_edges(mapping, phase.one)
                    first = _first_rows(scratch, phase, rows)
                    for edge in result.rows:
                        bucketed.put(bucket, [first[(edge["src"], edge["dst"])], edge])
                    total += len(result.rows)
                    blank += result.blank_ends
                    conflicts_n += result.conflicts
                else:
                    result = sp.table.gather(mapping, phase.one)
                    for one in result.objects:
                        bucketed.put(bucket, one)
                    total += len(result.objects)
                    blank += result.blank_keys
                    conflicts.update(result.conflicts)
            finally:
                mapping.table = table
            unresolved.extend(result.unresolved)
            workspace = result.workspace_slug if not phase.edge else None
            del rows, result
        bucketed.close()
        bin_.drop()
        unresolved.sort(key=_min_row)  # 틀의 차례 — 식별자가 처음 나온 차례(안정 정렬)
        _settle_matches(mapping, before, index, phase)
        merged: Any
        if phase.edge:
            merged = sp.table.EdgeResult(
                relation=str(phase.one["relation"]), type_slug=str(phase.one["type_slug"]),
                rows=_Sized(total), blank_ends=blank, conflicts=conflicts_n,
                unresolved=unresolved,
            )  # fmt: skip
        else:
            merged = sp.table.TypeResult(
                slug=str(phase.one["type_slug"]), workspace_slug=workspace,
                objects=_Sized(total), blank_keys=blank, conflicts=conflicts,
                unresolved=unresolved,
            )  # fmt: skip
        gathered.append(_Gathered(merged, bucketed, []))
    _order_matches(mapping)
    return gathered


#: 참조 대조의 미해결이 처음 생긴 자리 — (모으기 차례, 그 모으기 안의 첫 행, 칸 차례).
_MATCH_AT = "_relay_match_at"


def _settle_matches(
    mapping: Any, before: dict[tuple[str, str, str], int], index: int, phase: _Phase
) -> None:
    """모으기 하나가 끝나면 그 동안 붙은 참조 대조 미해결의 행을 차례로 — 틀은 행 차례로 돌아
    늘 오름차순이고, 바구니는 바구니 차례로 돈다."""
    seen: dict[tuple[str, str, str], tuple[int, int, int]] = getattr(mapping, _MATCH_AT, {})
    for key, numbers in mapping.match_unresolved.items():
        start = before.get(key, 0)
        if start >= len(numbers):
            continue
        numbers[start:] = sorted(numbers[start:])
        if key not in before:
            field_at = phase.names.index(key[1]) if key[1] in phase.names else len(phase.names)
            seen[key] = (index, numbers[start], field_at)
    setattr(mapping, _MATCH_AT, seen)


def _order_matches(mapping: Any) -> None:
    seen: dict[tuple[str, str, str], tuple[int, int, int]] = getattr(mapping, _MATCH_AT, {})
    ordered = sorted(
        mapping.match_unresolved.items(), key=lambda item: seen.get(item[0], (0, 0, 0))
    )
    mapping.match_unresolved = dict(ordered)


# --------------------------------------------------------------------------
# 쓰기 — 틀의 `convert` 와 같은 파일
# --------------------------------------------------------------------------


def _write_rows(
    folder: Path, name: str, total: int, rows: Iterator[Any], head: dict[str, Any]
) -> None:
    """틀처럼 `MAX_ROWS` 줄마다 파일 하나 — 하나면 `<name>.json`, 여럿이면
    `<name>-001.json` …"""
    cap = int(sp.pipeline.MAX_ROWS)
    many = total > cap
    chunk: list[Any] = []
    index = 0

    def flush() -> None:
        nonlocal index, chunk
        index += 1
        file = f"{name}-{index:03d}" if many else name
        sp.pipeline._write(folder / f"{file}.json", {**head, "rows": chunk})
        chunk = []

    for row in rows:
        chunk.append(row)
        if len(chunk) == cap:
            flush()
    if chunk:
        flush()


def convert(
    mapping_path: Path,
    source_path: Path,
    run: Path,
    *,
    server: str = "",
    token: str = "",
    bucket_rows: int = spill.BUCKET_ROWS,
    scratch: Path | None = None,
) -> tuple[bool, str]:
    """`sp_table.convert` 와 같다 — (미해결이 없나, 보고서). `scratch` 는 바구니를 둘
    폴더(없으면 `run` 옆) — 끝나면 지운다."""
    table_mod, pipeline = sp.table, sp.pipeline
    spec = table_mod._load_mapping(mapping_path)
    source = spec.get("source") or {}
    table = read_table(
        source_path,
        encoding=str(source.get("encoding") or "auto"),
        delimiter=str(source.get("delimiter") or ","),
    )
    if not len(table.rows):
        raise table_mod.Stop(f"{table.name}: 값이 있는 행이 없습니다")
    cache: dict[str, set[str]] = {}

    def keys(target: str) -> set[str]:
        if target not in cache:
            if target.startswith("@"):
                cache[target] = pipeline.read_keys_file(
                    (mapping_path.parent / target[1:]).resolve()
                )
            elif not server or not token:
                raise table_mod.Stop(
                    f"참조 대조({target})에 플랫폼 식별자가 필요합니다 — SP_SERVER · SP_TOKEN"
                )
            else:
                cache[target] = pipeline.fetch_keys(server, token, target)
        return cache[target]

    mapping = table_mod.Mapping(spec, table, keys=keys)
    table_mod._ask_platform(mapping, server, token)
    # relay 사전 — 대상이 별칭 · 이름으로 못 푼 표기만, 대상에 있는 키로(`relay_core.aliases`).
    notes = aliases.inject(mapping, spec)
    ontology: dict[str, Any] | None = None
    if spec.get("ontology"):
        ontology = table_mod._load_mapping(
            (mapping_path.parent / str(spec["ontology"])).resolve()
        )

    phases = _phases(mapping)
    folder = scratch or run.with_name(f"{run.name}.spill")
    count = spill.count_for(len(table.rows), bucket_rows)
    helper = _Scratch(mapping, spec, keys) if count > 1 else None
    gathered: list[_Gathered] = []
    try:
        gathered = _gather(mapping, helper, phases, folder, count)
        _write_run(mapping, mapping_path, spec, table, run, ontology, phases, gathered)
    finally:
        for one in gathered:
            if one.spill is not None:
                one.spill.drop()
        if count > 1:
            shutil.rmtree(folder, ignore_errors=True)
    results = [
        one.result for one, phase in zip(gathered, phases, strict=True) if not phase.edge
    ]
    edges = [one.result for one, phase in zip(gathered, phases, strict=True) if phase.edge]
    unresolved = _unresolved(mapping, table, results, edges)
    pipeline._write(run / pipeline.UNRESOLVED, unresolved)
    text = table_mod._report(mapping, mapping_path, results, run, len(unresolved), edges)
    if notes:
        text += "\n\n" + "\n".join(notes)
    (run / table_mod.REPORT).write_text(text + "\n", encoding="utf-8")
    return not unresolved, text


def _write_run(
    mapping: Any,
    mapping_path: Path,
    spec: dict[str, Any],
    table: Any,
    run: Path,
    ontology: dict[str, Any] | None,
    phases: list[_Phase],
    gathered: list[_Gathered],
) -> None:
    table_mod, pipeline = sp.table, sp.pipeline
    pipeline.cmd_init(run, title=str(spec.get("title") or table.name))
    manifest_path = run / pipeline.MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(mapping_path.read_bytes()).hexdigest()
    manifest["sources"] = [
        {
            "name": table.name,
            "sha256": table.sha256,
            "rows": len(table.rows),
            "encoding": table.encoding,
        }
    ]
    manifest["objects_order"] = [phase.one["type_slug"] for phase in phases if not phase.edge]
    relations = [phase.one["relation"] for phase in phases if phase.edge]
    if relations:
        manifest["relations_order"] = relations
    manifest["notes"] = table_mod._mapping_notes(mapping, mapping_path, digest)
    pipeline._write(manifest_path, manifest)
    if ontology is not None:
        pipeline._write(run / pipeline.ONTOLOGY, ontology)

    for phase, one in zip(phases, gathered, strict=True):
        result = one.result
        rows: Iterator[Any]
        if one.spill is None:
            rows = iter(one.rows)
        elif phase.edge:
            rows = (item[1] for item in spill.merged(one.spill, key=lambda item: item[0]))
        else:
            rows = spill.merged(one.spill, key=lambda item: item["_source"]["row"])
        if phase.edge:
            total = len(result.rows)
            folder, name = run / pipeline.RELATIONS_DIR, result.relation
            head: dict[str, Any] = {"type_slug": result.type_slug}
        else:
            total = len(result.objects)
            folder, name = run / pipeline.OBJECTS_DIR, result.slug
            head = {"type_slug": result.slug, "workspace_slug": result.workspace_slug}
        _write_rows(folder, name, total, rows, head)


def _unresolved(
    mapping: Any, table: Any, results: list[Any], edges: list[Any]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for result in results:
        out.extend(result.unresolved)
    for result in edges:
        out.extend(result.unresolved)
    for (slug, name, text), numbers in mapping.match_unresolved.items():
        out.append(
            {
                "what": f"{slug} 의 칸 {name} 값 {text!r}",
                "question": "코어에 없는(또는 여럿과 맞는) 식별자입니다 — "
                "어느 것을 가리키나요? 맞는 것이 없으면 비워 넣습니다",
                "options": [],
                "_source": {"file": table.name, "rows": numbers[:20]},
            }
        )
    return out
