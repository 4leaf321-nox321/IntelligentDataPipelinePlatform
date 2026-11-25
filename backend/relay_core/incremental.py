"""증분 계획 — 이번에 받은 행이 닿는 객체만 다시 모아 지난 실행의 출력에 끼워 넣는다.

실행마다 미러 전체를 다시 변환하던 것(`mirror.py` 의 처음 결정)을, 바뀐 행이 닿는 모으기(타입 ·
관계)만 다시 모으고 나머지는 **지난 변환의 출력에서 옮긴다**. 결과는 전체 변환과 **바이트까지
같다** — `objects/` · `relations/` · `unresolved.json` · `manifest.json` · `ontology.json`.
보고서(`table-report.txt`)는 수가 같고 예시의 차례만 다를 수 있다(전체 변환도 바구니 수에 따라
그렇다).

## 왜 전체와 같아지나

객체 하나는 그 타입의 식별자가 같은 행들로 만들어지고(선은 출발점이 같은 행들로), 그 행들과
참조 대조의 답에만 달려 있다. 그래서 값이 바뀐 행 · 추가 · 삭제된 행의 **옛 식별자와 새
식별자**에 걸린 모으기만 다시 모으면 된다(그 식별자를 가진 행은 전부 — 일부만 모으면 모은 값이
일부만 남는다). 값 충돌 검사도 그 안에서 지금처럼 한다.

- 그 타입이 읽는 칸이 하나도 안 바뀐 행은 그 타입의 객체를 바꾸지 않는다 — 과제의 상태만 바뀐
  날 모델을 다시 모으지 않는다.
- **참조 대조**(`match`)의 답은 대상의 식별자 · 별칭이 정한다. 실행마다 이미 본 **서로 다른
  값**을 지금 대상에 다시 풀고, 답이 바뀐 값을 가진 행도 다시 모은다 — 나중에 생긴 참조 대상이
  다음 계획에서 저절로 채워진다.
- 나머지 객체의 **행 번호**(`_source.row`)는 추가 · 삭제된 키의 자리로 다시 매기고(미러는 키
  차례라 남은 행의 앞뒤는 그대로다) 출처 파일 이름을 바꾼다. 선은 처음 나온 행을 실행 옆의 자리
  파일(`<때>.edges`)에서.
- **보고서의 통계**는 지난 통계 - (다시 모은 모으기의 옛 몫) + (새 몫)이다. 옛 몫은 옛 행을
  **그때의 참조 답** (색인에 적어 둔 것)으로 다시 모아 센다.

## 전체로 돌아가는 때(`NotIncremental` — 까닭을 실행 요약에 적는다)

레시피 · 계산 필드 · 온톨로지 · 변환 코드(플랫폼 판)가 바뀜, 지난 출력이나 색인이 없음, 색인이
본 미러가 지금 미러와 다름(반영 뒤 변환이 멈췄거나 미러를 되돌렸다), 레시피에
해석기(`parser`)나 `on_missing: unresolved` 가 있음(해석기는 행마다 기억하는 통계가 있고,
미해결 참조는 실행을 멈춘다), 바뀐 행이 너무 많음, 옮긴 객체 수가 색인과 어긋남(안전장치 — 틀린
출력을 내느니 전체로).

## 비교

지난번 적용된 출력(기준)과 견줄 때도 건드린 것만 견준다(`Touched` · `delta`). 다시 모으지 않은
것은 지난 출력에서 그대로 옮겼으니 지난 출력이 기준이면 견줄 것이 없다. 적용을 기다리는 동안
여러 번 돌면 **기준과 다를 수 있는 앞 값과 기준에 있던 그 줄**을 실행 옆(`<때>.base`)에 이어
적는다. 기준이 이어지지 않으면 출력 전체를 견준다(`diff.delta`). 만드는 폴더는 같다.

색인은 파이프라인마다 작업 폴더의 `incremental.sqlite` 하나다(그 안의 `run` 이 어느 실행의
것인지 말한다). 전체 변환이 새로 짓고 증분 변환이 이어 쓴다. 둘 다 트랜잭션 하나로 — 도중에
멈추면 지난 실행의 색인이 남고, 색인이 가리키는 실행의 출력은 정리(`Workdir.prune`)가 남긴다.
"""

from __future__ import annotations

import ast
import functools
import hashlib
import heapq
import json
import logging
import pickle
import re
import shutil
import sqlite3
from array import array
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from relay_core import aliases, compute, diff, mirror, records, sp, spill, table
from relay_core.mirror import Changes
from relay_core.state import Workdir

VERSION = 1
INDEX = "incremental.sqlite"
#: 이보다 많은 행이 바뀌면 전체로 — 바뀐 행은 메모리에 든다.
MAX_CHANGED = 100_000
#: 기준과 다를 수 있는 앞 값(식별자 · 출발점)이 이보다 많아지면 전체로 견준다 — 메모리에 든다.
MAX_TOUCHED = 200_000
#: 다시 모을 행이 이보다 많으면 전체로 — 그 키를 메모리에 든다.
MAX_GATHERED = 600_000


log = logging.getLogger(__name__)


class NotIncremental(Exception):
    """증분으로 할 수 없다 — 까닭은 실행 요약에 적고 전체 변환으로 간다."""


# --------------------------------------------------------------------------
# 색인
# --------------------------------------------------------------------------


def fingerprint(pipeline: Any) -> str:
    """증분이 이어질 수 있는 설정인가 — 레시피 · 온톨로지 · 계산 필드 · 원천 이름이 같아야
    한다."""
    spec_path = Path(pipeline.recipe)
    digest = hashlib.sha256()
    digest.update(f"v{VERSION}|{sp.pipeline.MAX_ROWS}|{pipeline.source}".encode())
    digest.update(_code())
    digest.update(spec_path.read_bytes())
    spec = sp.table._load_mapping(spec_path)
    if spec.get("ontology"):
        digest.update((spec_path.parent / str(spec["ontology"])).resolve().read_bytes())
    digest.update(json.dumps(pipeline.computed, sort_keys=True, default=str).encode())
    return digest.hexdigest()


@functools.cache
def _code() -> bytes:
    """변환하는 코드의 지문 — 판을 올려 변환이 달라졌으면, 옛 코드가 만든 출력에 이어 붙이지
    않고 전체로 다시 만든다."""
    digest = hashlib.sha256()
    for module in (sp.pipeline, sp.table, table, compute, aliases, spill, records, mirror):
        digest.update(Path(str(module.__file__)).read_bytes())
    digest.update(Path(__file__).read_bytes())
    return digest.digest()


def index_path(work: Workdir) -> Path:
    return work.root / INDEX


def index_run(work: Workdir) -> str | None:
    """색인이 이어질 실행(`runs/<때>`) — 없으면 None. 정리(`Workdir.prune`)가 그 실행을
    남긴다."""
    file = index_path(work)
    if not file.exists():
        return None
    try:
        db = _open(file, readonly=True)
        try:
            row = db.execute("SELECT v FROM meta WHERE k = 'run'").fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return str(pickle.loads(row[0])) if row else None


def _phase_columns(count: int) -> str:
    return "".join(f', "l{index}" TEXT' for index in range(count))


def _schema(db: sqlite3.Connection, phases: int) -> None:
    db.executescript(
        f"""
        CREATE TABLE meta(k TEXT PRIMARY KEY, v BLOB) WITHOUT ROWID;
        CREATE TABLE rows(key TEXT PRIMARY KEY{_phase_columns(phases)}) WITHOUT ROWID;
        CREATE TABLE members(
            phase INTEGER, lead TEXT, key TEXT, PRIMARY KEY(phase, lead, key)
        ) WITHOUT ROWID;
        CREATE TABLE lookups(
            target TEXT, prefix INTEGER, text TEXT, found TEXT, outcome TEXT,
            PRIMARY KEY(target, prefix, text)
        ) WITHOUT ROWID;
        CREATE TABLE row_texts(
            target TEXT, prefix INTEGER, text TEXT, key TEXT,
            PRIMARY KEY(target, prefix, text, key)
        ) WITHOUT ROWID;
        CREATE INDEX row_texts_key ON row_texts(key);
        """
    )


def _journal(file: Path) -> Path:
    """이어 쓰다 멈춘 흔적(SQLite 의 롤백 저널)."""
    return file.with_name(f"{file.name}-journal")


def _open(file: Path, *, readonly: bool = False, fresh: bool = False) -> sqlite3.Connection:
    """색인을 연다. 파일 잠금은 쓰지 않는다(`nolock`) — 파이프라인마다 작업은 하나뿐이고, 공용
    폴더(NFS)의 잠금에 걸리지 않게. `fresh` 는 새로 짓는 것 — 다 짓고 바꿔 끼우므로 되돌릴
    기록이 필요 없다."""
    mode = "ro" if readonly else "rwc"
    db = sqlite3.connect(f"{file.resolve().as_uri()}?mode={mode}&nolock=1", uri=True)
    if fresh:
        db.execute("PRAGMA journal_mode=OFF")
        db.execute("PRAGMA synchronous=OFF")
    return db


class Builder:
    """전체 변환을 지켜보며 색인을 새로 짓는다(`table.Observer`). `keys` 는 미러의 키를
    차례로 — 원천 표의 행 번호(자리 + 2)와 짝을 맞춘다."""

    def __init__(self, work: Workdir, keys: Iterable[str], phases: int, order: Path) -> None:
        self.file = index_path(work)
        self.tmp = self.file.with_suffix(".sqlite.tmp")
        self.tmp.unlink(missing_ok=True)
        self.broken: Exception | None = None
        """색인을 짓지 못한 까닭 — 변환은 그대로 간다(다음 실행은 전체 변환이다)."""
        self.db = _open(self.tmp, fresh=True)
        _schema(self.db, phases)
        self.db.executescript(
            "CREATE TEMP TABLE tk(number INTEGER PRIMARY KEY, key TEXT);"
            "CREATE TEMP TABLE tx(number INTEGER, target TEXT, prefix INTEGER, text TEXT,"
            " found TEXT, outcome TEXT);"
        )
        self.phases = phases
        self._keys = iter(keys)
        self._pos = -1
        self._key = ""
        self._number = -1
        self._leads: list[str] = []
        self._rows: list[tuple[Any, ...]] = []
        self._members: list[tuple[Any, ...]] = []
        self._numbers: list[tuple[Any, ...]] = []
        self._texts: list[tuple[Any, ...]] = []
        self._order = _OrderWriter(order)
        self.totals: dict[str, Any] = {}

    def lead(self, phase: int, number: int, text: str) -> None:
        if self.broken is not None:
            return
        if number != self._number:
            self._close_row()
            self._number = number
            while self._pos < number - 2:
                found = next(self._keys, None)
                if found is None:
                    self.broken = ValueError("미러와 원천 표의 행 수가 다름")
                    return
                self._key = found
                self._pos += 1
            self._numbers.append((number, self._key))
        if text:
            self._members.append((phase, text, self._key))
        self._leads.append(text)

    def _close_row(self) -> None:
        if self._number < 0:
            return
        self._rows.append((self._key, *self._leads))
        self._leads = []
        if len(self._rows) >= 10_000:
            self._flush()

    def _flush(self) -> None:
        marks = ", ".join("?" for _ in range(1 + self.phases))
        try:
            self.db.executemany(f"INSERT INTO rows VALUES ({marks})", self._rows)
            self.db.executemany(
                "INSERT OR IGNORE INTO members VALUES (?, ?, ?)", self._members
            )
            self.db.executemany("INSERT INTO tk VALUES (?, ?)", self._numbers)
            self.db.executemany("INSERT INTO tx VALUES (?, ?, ?, ?, ?, ?)", self._texts)
        except (sqlite3.Error, OSError) as error:
            self.broken = error
        self._rows, self._members, self._numbers, self._texts = [], [], [], []

    def lookup(
        self,
        at: tuple[str, str],
        target: str,
        prefix: bool,
        text: str,
        number: int,
        key: str | None,
        outcome: str,
    ) -> None:
        if self.broken is not None:
            return
        self._texts.append((number, target, int(prefix), text, key, outcome))
        if len(self._texts) >= 10_000:
            self._flush()

    def edges(self, phase: int, first: int) -> None:
        if self.broken is not None:
            return
        try:
            self._order.put(phase, first)
        except OSError as error:
            self.broken = error

    def finish(self, mapping: Any, results: list[Any], edges: list[Any], table: Any) -> None:
        self._close_row()
        self._flush()
        self.totals = _plain_totals(mapping, results, edges)

    def seal(self, meta: Mapping[str, Any]) -> None:
        """색인을 굳히고 앞 색인을 갈아 끼운다(선이 처음 나온 행도)."""
        if self.broken is not None:
            raise self.broken
        db = self.db
        db.executescript(
            "INSERT OR IGNORE INTO lookups "
            "SELECT DISTINCT target, prefix, text, found, outcome FROM tx;"
            "INSERT OR IGNORE INTO row_texts SELECT tx.target, tx.prefix, tx.text, tk.key"
            " FROM tx JOIN tk ON tk.number = tx.number;"
        )
        self._order.close()
        _write_meta(db, {**meta, "totals": self.totals})
        db.commit()
        db.close()
        # 앞 색인을 이어 쓰다 멈춘 흔적이 남으면 새 색인에 되감긴다 — 먼저 지운다.
        _journal(self.file).unlink(missing_ok=True)
        self.tmp.replace(self.file)

    def abandon(self) -> None:
        self.db.close()
        self.tmp.unlink(missing_ok=True)


def _write_meta(db: sqlite3.Connection, values: Mapping[str, Any]) -> None:
    db.executemany(
        "INSERT OR REPLACE INTO meta VALUES (?, ?)",
        [(key, pickle.dumps(value)) for key, value in values.items()],
    )


def _read_meta(db: sqlite3.Connection) -> dict[str, Any]:
    return {key: pickle.loads(value) for key, value in db.execute("SELECT k, v FROM meta")}


class _OrderWriter:
    """선이 처음 나온 행 — 모으기마다 파일 하나에 흘려 쓴다(8바이트 수). 선이 수백만이어도
    메모리에 들지 않는다."""

    def __init__(self, folder: Path) -> None:
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True)
        self.folder = folder
        self._phase: int | None = None
        self._handle: Any = None
        self._held: array[int] = array("Q")

    def put(self, phase: int, first: int) -> None:
        if phase != self._phase:
            self.close()
            self._phase = phase
            self._handle = (self.folder / f"{phase:03d}.bin").open("ab")
        self._held.append(first)
        if len(self._held) >= 16_384:
            self._flush()

    def _flush(self) -> None:
        self._held.tofile(self._handle)
        self._held = array("Q")

    def close(self) -> None:
        if self._handle is not None:
            self._flush()
            self._handle.close()
            self._handle, self._phase = None, None


def _read_order(folder: Path, phase: int) -> Iterator[int]:
    file = folder / f"{phase:03d}.bin"
    if not file.exists():
        return
    with file.open("rb") as handle:
        while data := handle.read(16_384 * 8):
            one: array[int] = array("Q")
            one.frombytes(data)
            yield from one


def _plain_stats(mapping: Any) -> dict[str, Any]:
    """틀의 통계를 평범한 사전으로 — 색인에 적고 다시 세운다(틀의 클래스에 기대지 않게)."""
    return {
        "stats": {
            at: {what: Counter(found) for what, found in one.problems.items()}
            for at, one in mapping.stats.items()
        },
        "match": {
            at: {
                "target": one.target,
                "outcomes": Counter(one.outcomes),
                "misses": {what: Counter(found) for what, found in one.misses.items()},
            }
            for at, one in mapping.match_stats.items()
        },
    }


def _plain_totals(mapping: Any, results: list[Any], edges: list[Any]) -> dict[str, Any]:
    """변환이 끝난 뒤의 통계 — 보고서를 다시 지을 만큼."""
    return {
        **_plain_stats(mapping),
        "types": [
            {
                "objects": len(one.objects),
                "blank_keys": one.blank_keys,
                "conflicts": Counter(one.conflicts),
            }
            for one in results
        ],
        "edges": [
            {
                "rows": len(one.rows),
                "blank_ends": one.blank_ends,
                "conflicts": int(one.conflicts),
            }
            for one in edges
        ],
    }


def _combine(
    base: dict[str, Any], minus: dict[str, Any], plus: dict[str, Any]
) -> dict[str, Any]:
    """`base - minus + plus` — 다시 모은 모으기의 옛 몫을 빼고 새 몫을 더한다. 셋 다 같은
    모양."""
    out: dict[str, Any] = {}
    for group in ("stats", "match"):
        merged: dict[Any, Any] = {}
        for at in {*base[group], *minus[group], *plus[group]}:
            one = _combine_one(
                base[group].get(at), minus[group].get(at), plus[group].get(at), group
            )
            # 전체 변환이면 아무것도 안 센 자리는 없다 — 보고서에 빈 줄로 나오지 않게.
            if group == "stats" and not one:
                continue
            if group == "match" and not one["outcomes"]:
                continue
            merged[at] = one
        out[group] = merged
    out["types"] = [
        {
            "objects": b["objects"] - m["objects"] + p["objects"],
            "blank_keys": b["blank_keys"] - m["blank_keys"] + p["blank_keys"],
            "conflicts": _add(b["conflicts"], m["conflicts"], p["conflicts"]),
        }
        for b, m, p in zip(base["types"], minus["types"], plus["types"], strict=True)
    ]
    out["edges"] = [
        {
            "rows": b["rows"] - m["rows"] + p["rows"],
            "blank_ends": b["blank_ends"] - m["blank_ends"] + p["blank_ends"],
            "conflicts": b["conflicts"] - m["conflicts"] + p["conflicts"],
        }
        for b, m, p in zip(base["edges"], minus["edges"], plus["edges"], strict=True)
    ]
    return out


def _add(base: Any, minus: Any, plus: Any) -> Counter[str]:
    """세는 값들의 `base - minus + plus` — 0 이 된 것은 없앤다(전체 변환이면 나오지 않는다)."""
    out: Counter[str] = Counter(base or {})
    out.subtract(Counter(minus or {}))
    out.update(Counter(plus or {}))
    return Counter({key: value for key, value in out.items() if value})


def _combine_one(base: Any, minus: Any, plus: Any, group: str) -> Any:
    if group == "stats":
        whats = {*(base or {}), *(minus or {}), *(plus or {})}
        merged = {
            what: _add((base or {}).get(what), (minus or {}).get(what), (plus or {}).get(what))
            for what in whats
        }
        return {what: found for what, found in merged.items() if found}
    target = next(one["target"] for one in (base, minus, plus) if one)
    outcomes = _add(
        (base or {}).get("outcomes"),
        (minus or {}).get("outcomes"),
        (plus or {}).get("outcomes"),
    )
    whats = {
        *((base or {}).get("misses") or {}),
        *((minus or {}).get("misses") or {}),
        *((plus or {}).get("misses") or {}),
    }
    misses = {
        what: _add(
            ((base or {}).get("misses") or {}).get(what),
            ((minus or {}).get("misses") or {}).get(what),
            ((plus or {}).get("misses") or {}).get(what),
        )
        for what in whats
    }
    return {
        "target": target,
        "outcomes": outcomes,
        "misses": {what: found for what, found in misses.items() if found},
    }


def _report_stats(totals: dict[str, Any]) -> tuple[dict[Any, Any], dict[Any, Any]]:
    """`_report` 가 읽는 모양으로 — `mapping.stats` · `mapping.match_stats`."""
    stats = {
        at: SimpleNamespace(problems=dict(found)) for at, found in totals["stats"].items()
    }
    match = {
        at: SimpleNamespace(
            target=one["target"], outcomes=one["outcomes"], misses=dict(one["misses"])
        )
        for at, one in totals["match"].items()
    }
    return stats, match


# --------------------------------------------------------------------------
# 준비
# --------------------------------------------------------------------------


class _Replay:
    """옛 실행의 참조 답을 되풀이한다 — 색인에 적어 둔 (글자 → 식별자 · 결과)."""

    def __init__(self, target: str, db: sqlite3.Connection) -> None:
        self.target, self._db = target, db
        self.exact: set[str] = set()
        self.resolved: dict[str, str | None] = {}
        self._held: dict[tuple[str, bool], tuple[Any, str]] = {}

    def find(self, text: str, *, prefix: bool = False) -> tuple[Any, str]:
        got = self._held.get((text, prefix))
        if got is None:
            row = self._db.execute(
                "SELECT found, outcome FROM lookups"
                " WHERE target = ? AND prefix = ? AND text = ?",
                (self.target, int(prefix), text),
            ).fetchone()
            if row is None:
                raise NotIncremental(f"색인에 없는 참조 값: {self.target} {text!r}")
            got = (row[0], str(row[1]))
            _hold(self._held, (text, prefix), got)
        return got


def _hold(held: dict[Any, Any], key: Any, value: Any) -> None:
    """작은 기억 — 넘치면 비운다."""
    if len(held) >= 20_000:
        held.clear()
    held[key] = value


class _Exact:
    """`KeyMatch.exact` 의 자리 — 그 식별자가 대상에 있나."""

    def __init__(self, owner: _DiskMatch) -> None:
        self._owner = owner

    def __contains__(self, text: object) -> bool:
        return self._owner.has(str(text))


class _DiskMatch:
    """`sp_table.KeyMatch` 와 **같은 답**을 디스크에 둔 식별자에서 — 대상의 식별자가 수십만이면
    메모리의 것(`exact` · `upper` · `fronts`)이 백 MB 를 넘는다. 증분은 그 가운데 몇만 개만
    찾는다."""

    def __init__(self, db: sqlite3.Connection, number: int, keys: Collection[str]) -> None:
        self._db, self._number = db, number
        self.exact = _Exact(self)
        self.resolved: dict[str, str | None] = {}
        self._held: dict[tuple[str, bool], tuple[Any, str] | None] = {}
        boundaries = sp.table.BOUNDARIES

        def fronts() -> Iterator[tuple[int, str, str]]:
            for key in keys:
                for index, char in enumerate(key):
                    if index and char in boundaries:
                        yield number, key[:index].upper(), key

        db.executemany(
            "INSERT OR IGNORE INTO uppers VALUES (?, ?, ?)",
            ((number, key.upper(), key) for key in keys),
        )
        db.executemany("INSERT OR IGNORE INTO fronts VALUES (?, ?, ?)", fronts())

    @staticmethod
    def store(path: Path) -> sqlite3.Connection:
        path.unlink(missing_ok=True)
        db = _open(path, fresh=True)
        db.executescript(
            "CREATE TABLE uppers(n INTEGER, up TEXT, key TEXT, PRIMARY KEY(n, up, key))"
            " WITHOUT ROWID;"
            "CREATE TABLE fronts(n INTEGER, front TEXT, key TEXT,"
            " PRIMARY KEY(n, front, key)) WITHOUT ROWID;"
        )
        return db

    def has(self, text: str) -> bool:
        found = self._db.execute(
            "SELECT 1 FROM uppers WHERE n = ? AND up = ? AND key = ?",
            (self._number, text.upper(), text),
        ).fetchone()
        return found is not None

    def _two(self, table: str, column: str, text: str) -> list[str]:
        return [
            str(key)
            for (key,) in self._db.execute(
                f"SELECT key FROM {table} WHERE n = ? AND {column} = ? LIMIT 2",
                (self._number, text.upper()),
            )
        ]

    def _by_key(self, text: str, prefix: bool) -> tuple[Any, str] | None:
        """식별자만 본 답 — 별칭 · 이름으로 풀 것이면 None."""
        if self.has(text):
            return text, "그대로"
        found = self._two("uppers", "up", text)
        if len(found) == 1:
            return found[0], "대소문자만 다름"
        if found:
            return None, "여러 개"
        if prefix:
            found = self._two("fronts", "front", text)
            if len(found) == 1:
                return found[0], "앞부분이 하나"
            if found:
                return None, "여러 개"
        return None

    def find(self, text: str, *, prefix: bool = False) -> tuple[Any, str]:
        at = (text, prefix)
        if at in self._held:
            got = self._held[at]
        else:
            got = self._by_key(text, prefix)
            _hold(self._held, at, got)
        if got is not None:
            return got
        picked = self.resolved.get(text)
        return (picked, "별칭·이름으로") if picked else (None, "없음")


@dataclass
class _Change:
    """바뀐 행 하나 — 모으기들이 읽는 칸의 옛 · 새 값. 빈 행(변환이 건너뛴다)이면 None."""

    key: str
    old: dict[str, str] | None
    new: dict[str, str] | None


@dataclass
class _Mark:
    """바뀐 행에서 메모리에 남기는 것 — 옛 · 새 값이 있나, 읽는 칸이 안 바뀐 모으기(비트)."""

    old: bool
    new: bool
    same: int


@dataclass
class _Phase:
    """이번에 다시 모을 모으기 하나의 닿은 것들."""

    leads: set[str] = field(default_factory=set)
    """다시 모을 앞 값(식별자 · 출발점) — 옛 것과 새 것."""
    old_keys: set[str] = field(default_factory=set)
    """옛 미러에서 그 앞 값이었던 원천 키."""
    touched: set[str] = field(default_factory=set)
    """이 모으기에 닿은 바뀐 행의 키(추가 · 변경 · 삭제 · 참조 답이 바뀐 행)."""

    def touch(self, key: str, *leads: str) -> None:
        self.touched.add(key)
        self.leads.update(lead for lead in leads if lead)


def _aligned(
    work: Workdir, rows: Iterable[tuple[int, dict[str, str]]]
) -> Iterator[tuple[str, int, dict[str, str] | None]]:
    """미러의 키와 원천 표의 행을 나란히 — (키, 행 번호, 행). 빈 행은 행 없이 낸다."""
    table_rows = iter(rows)
    waiting = next(table_rows, None)
    for position, key in enumerate(work.mirror_keys()):
        number = position + 2
        row: dict[str, str] | None = None
        if waiting is not None and waiting[0] == number:
            row = waiting[1]
            waiting = next(table_rows, None)
        yield key, number, row


class _Spool:
    """모으기 하나의 부분 행 — 키 차례로 온 (행 번호, 행)을 디스크에."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = path.open("wb")
        self._chunk: list[tuple[int, dict[str, str]]] = []
        self.count = 0
        self._next = 2

    def put(self, row: dict[str, str], number: int | None = None) -> int:
        if number is None:
            number = self._next
        self._next = number + 1
        self._chunk.append((number, row))
        self.count += 1
        if len(self._chunk) >= 5000:
            self._write()
        return number

    def _write(self) -> None:
        pickle.dump(self._chunk, self._handle, protocol=pickle.HIGHEST_PROTOCOL)
        self._chunk = []

    def close(self) -> None:
        if self._chunk:
            self._write()
        self._handle.close()

    def rows(self) -> Any:
        """`table.rows` 의 자리 — 부를 때마다 처음부터."""

        def read() -> Iterator[tuple[int, dict[str, str]]]:
            with self.path.open("rb") as handle:
                while True:
                    try:
                        yield from pickle.load(handle)
                    except EOFError:
                        return

        return table._Rows(read, self.count)


def _prepare(
    pipeline: Any, source: Path, *, server: str, token: str
) -> tuple[dict[str, Any], Any, list[Any], Callable[[str], Collection[str]]]:
    """`table.convert` 와 같은 준비 — (레시피, 원천 표, 모으기 목록, 대상의 식별자 읽기). 값이
    있는 행이 없거나 증분으로 되풀이할 수 없는 레시피면 전체 변환으로."""
    spec = sp.table._load_mapping(pipeline.recipe)
    refused = _refused_recipe(spec)
    if refused:
        raise NotIncremental(refused)
    source_cfg = spec.get("source") or {}
    got = table.read_table(
        source,
        encoding=str(source_cfg.get("encoding") or "auto"),
        delimiter=str(source_cfg.get("delimiter") or ","),
    )
    if not len(got.rows):
        raise NotIncremental("값이 있는 행이 없음")

    def keys(target: str) -> Collection[str]:
        if target.startswith("@"):
            found: Collection[str] = sp.pipeline.read_keys_file(
                (Path(pipeline.recipe).parent / target[1:]).resolve()
            )
            return found
        if not server or not token:
            raise NotIncremental(f"참조 대조({target})에 대상 SP의 식별자가 필요함")
        found = sp.pipeline.fetch_keys(server, token, target)
        return found

    return spec, got, table._phases(_mapping(spec, got)), keys


def _mapping(spec: dict[str, Any], got: Any) -> Any:
    """식별자를 들지 않은 `Mapping` — 참조 대조는 뒤에 끼운다(`matchers`)."""
    return sp.table.Mapping(spec, got, keys=lambda _target: set())


def _refused_recipe(spec: dict[str, Any]) -> str:
    """증분으로 되풀이할 수 없는 레시피의 까닭 — 없으면 빈 글자."""
    names: list[tuple[str, str]] = []
    for group, name in (("types", "type_slug"), ("relations", "relation")):
        names.extend((group, str(one.get(name))) for one in spec.get(group) or [])
    if len(set(names)) != len(names):
        return "레시피에 같은 타입 · 관계가 두 번 있음"

    def walk(found: Any) -> Iterator[dict[str, Any]]:
        if isinstance(found, dict):
            yield found
            for mode in sp.table.AGGREGATES:
                if mode in found:
                    yield from walk(found[mode])

    asked: list[Any] = []
    """대상에 별칭 · 이름을 묻는 자리(`sp_table._ask_platform`) — 타입의 칸과 관계의 양 끝."""
    unasked: list[Any] = []
    for one in spec.get("types") or []:
        unasked.extend([one.get("key"), one.get("label")])
        asked.extend((one.get("fields") or {}).values())
    for one in spec.get("relations") or []:
        asked.extend([one.get("src"), one.get("dst")])
        unasked.append(one.get("evidence_note"))
        unasked.extend((one.get("properties") or {}).values())
    for places, askable in ((asked, True), (unasked, False)):
        for found in places:
            for part in walk(found):
                rule = part.get("match") or {}
                if part.get("parser"):
                    return "레시피에 해석기(parser)가 있음"
                if rule.get("on_missing") == "unresolved":
                    return "참조 대조의 on_missing이 unresolved임"
                target = str(rule.get("keys") or "").strip()
                if not askable and target and not target.startswith("@"):
                    # 여기 나온 표기는 전체 변환이 대상에 묻지 않는다 — 색인의 표기는 어느
                    # 자리에서 나왔는지 가리지 않아 그대로 되풀이할 수 없다.
                    return "식별자 · 이름 · 관계 속성에 대상 SP와의 참조 대조가 있음"
    return ""


def _recheck(
    db: sqlite3.Connection,
    fresh: set[tuple[str, int, str]],
    matchers: Mapping[str, Any],
    spec: dict[str, Any],
    server: str,
    token: str,
) -> tuple[list[str], set[tuple[str, int, str]]]:
    """이미 본 값을 지금 대상으로 다시 풀고, 처음 보는 값(`fresh`)도 대상에 묻는다 — 보고서에
    붙일 말과, **답이 바뀐 값**. 묻는 값은 전체 변환(`sp_table._ask_platform`)과 같다(그쪽은
    행마다, 여기는 서로 다른 값마다 본다)."""
    if server and token:
        for target, matcher in matchers.items():
            if target.startswith("@"):
                continue
            left: set[str] = set()

            def look(text: str, prefix: int, matcher: Any = matcher, left: Any = left) -> None:
                if not text or text in matcher.exact or text in matcher.resolved:
                    return
                found, _outcome = matcher.find(text, prefix=bool(prefix))
                if found is None:
                    left.add(text)

            for prefix, text in db.execute(
                "SELECT prefix, text FROM lookups WHERE target = ?", (target,)
            ):
                look(text, prefix)
            for one, prefix, text in fresh:
                if one == target:
                    look(text, prefix)
            if not left:
                continue
            answered = sp.pipeline.resolve_names(server, token, target, sorted(left))
            if answered is None:
                continue
            for text, verdict in answered.items():
                picked = (
                    (verdict or {}).get("object") if verdict.get("match") == "exact" else None
                )
                matcher.resolved[text] = (
                    str(picked["key"]) if picked and picked.get("key") else None
                )
    notes = aliases.inject(SimpleNamespace(matchers=matchers), spec)
    moved = {
        (target, prefix, text)
        for target, prefix, text, found, outcome in db.execute(
            "SELECT target, prefix, text, found, outcome FROM lookups"
        )
        if matchers[target].find(text, prefix=bool(prefix)) != (found, outcome)
    }
    return notes, moved


# --------------------------------------------------------------------------
# 변환
# --------------------------------------------------------------------------


@dataclass
class Plan:
    """증분으로 할 수 있는지 본 결과 — 준비한 것."""

    meta: dict[str, Any]
    db_file: Path
    prev: Path


def ready(work: Workdir, pipeline: Any, changes: Changes) -> Plan:
    """할 수 있나 — 아니면 `NotIncremental`."""
    file = index_path(work)
    if not file.exists():
        raise NotIncremental("색인이 없음(첫 실행이거나 지워짐)")
    if _journal(file).exists():
        raise NotIncremental("색인을 이어 쓰다 중단됨")
    db = _open(file, readonly=True)
    try:
        meta = _read_meta(db)
    finally:
        db.close()
    if meta.get("version") != VERSION:
        raise NotIncremental("색인 판이 다름")
    if meta.get("digest") != fingerprint(pipeline):
        raise NotIncremental("레시피 · 계산 필드 · 온톨로지가 바뀜")
    if meta.get("mirror") != changes.base:
        raise NotIncremental("지난 변환 뒤 미러가 따로 바뀜(변환이 멈췄거나 미러를 되돌림)")
    prev = work.path(str(meta["run"]))
    if not (prev / sp.pipeline.MANIFEST).exists():
        raise NotIncremental("지난 출력이 없음")
    order = work.path(str(meta["order"]))
    if not order.exists():
        raise NotIncremental("지난 실행의 선 자리 기록이 없음")
    if changes.count > MAX_CHANGED:
        raise NotIncremental(f"바뀐 행 {changes.count:,}건(상한 {MAX_CHANGED:,})")
    return Plan(meta=meta, db_file=file, prev=prev)


def convert(
    work: Workdir,
    pipeline: Any,
    source: Path,
    run: Path,
    changes: Changes,
    *,
    server: str = "",
    token: str = "",
    bucket_rows: int = spill.BUCKET_ROWS,
    baseline: str | None = None,
) -> tuple[Any, Touched | None]:
    """증분 변환 — 지난 출력에서 바뀐 모으기만 다시 모아 `run` 을 짓고 색인을 이어 쓴다. 할 수
    없으면 `NotIncremental`(색인은 그대로 — 전체 변환으로). `convert.Converted` 와, `baseline`(
    지금 기준 출력)과 견줄 것(`Touched` — 이어 견줄 수 없으면 None)을 돌려준다."""
    from relay_core.convert import Converted

    plan = ready(work, pipeline, changes)
    spec, got, phases, keys = _prepare(pipeline, source, server=server, token=token)
    columns_of = [list(phase.columns) for phase in phases]
    feed = _Feed(changes, pipeline.computed, columns_of)
    folder = work.path(f"work/{run.name}.spill")
    folder.mkdir(parents=True, exist_ok=True)
    db = _open(plan.db_file, readonly=True)
    store = _DiskMatch.store(folder / "keys.sqlite")
    try:
        texts = _Log(folder / "texts")
        marks, removed_at, inserted_at = _survey(feed, _texter(spec, got, phases, texts))
        texts.close()
        # 참조 답을 지금 대상으로 다시 풀어 본다 — 답이 바뀐 값을 가진 행도 다시 모은다.
        probe = _mapping(spec, got)
        matchers: dict[str, Any] = {
            target: _DiskMatch(store, number, keys(target))
            for number, target in enumerate(probe.matchers)
        }
        probe.matchers = matchers
        fresh = {
            (target, prefix, text)
            for _phase, _key, target, prefix, text in _read_log(texts.path)
        }
        notes, moved_texts = _recheck(db, fresh, matchers, spec, server, token)
        moved_keys = _rows_with_texts(db, moved_texts)
        text_keys = moved_keys - marks.keys()
        if len(text_keys) > MAX_CHANGED:
            raise NotIncremental(f"참조 답이 바뀐 행 {len(text_keys):,}건")

        scratch = table._Scratch(probe, spec, lambda _target: set())
        old_leads = _old_leads(db, {*marks, *text_keys}, len(phases))
        new_leads = {
            one.key: _leads(scratch, phases, one.new) for one in feed if one.new is not None
        }
        if text_keys:  # 값은 그대로인데 참조 답이 바뀐 행
            for key, _number, row in _aligned(work, got.rows):
                if key in text_keys and row is not None:
                    new_leads[key] = _leads(scratch, phases, row)
        parts = _affected(len(phases), marks, text_keys, old_leads, new_leads, moved_keys)
        _members(db, parts)
        text, unresolved, totals, touched = _splice(
            work,
            pipeline,
            plan,
            db,
            spec,
            got,
            phases,
            columns_of,
            run,
            feed,
            marks,
            removed_at,
            inserted_at,
            text_keys,
            old_leads,
            new_leads,
            parts,
            notes,
            matchers,
            bucket_rows,
            baseline,
        )
        db.close()
        _update_index(
            work,
            plan.db_file,
            run,
            totals=totals,
            marks=marks,
            text_keys=text_keys,
            old_leads=old_leads,
            new_leads=new_leads,
            moved_texts=moved_texts,
            matchers=matchers,
            texts=texts.path,
            digest=plan.meta["digest"],
            mark=work.mirror_mark(),
            baseline=(
                {"base": touched.baseline, "base_file": touched.relative}
                if touched is not None
                else {"base": None, "base_file": None}
            ),
        )
    finally:
        db.close()
        store.close()
    return Converted(run=run, clean=not unresolved, report=text), touched


class _Feed:
    """바뀐 행을 키 차례로 — 돌 때마다 기록(`Changes`)에서 다시 읽는다. 하루치 수만 행의 옛 ·
    새 값을 메모리에 들지 않는다."""

    def __init__(self, changes: Changes, rules: Any, columns_of: list[list[str]]) -> None:
        self.changes, self.rules, self.columns_of = changes, rules, columns_of
        self._columns = sorted({one for found in columns_of for one in found})

    def _row(self, values: Mapping[str, Any] | None) -> dict[str, str] | None:
        if values is None:
            return None
        full = compute.values_with(dict(values), self.rules) if self.rules else dict(values)
        if not any(str(one).strip() for one in full.values()):
            return None  # 빈 행은 변환이 건너뛴다
        return {one: str(full.get(one, "") or "") for one in self._columns}

    def __iter__(self) -> Iterator[_Change]:
        for _kind, key, _old_at, _new_at, old, new in self.changes:
            yield _Change(key, self._row(old), self._row(new))


def _survey(
    feed: _Feed, note_texts: Callable[[str, dict[str, str]], None]
) -> tuple[dict[str, _Mark], list[int], list[int]]:
    """바뀐 행을 훑는다 — 키마다 남길 것, 지운 행의 옛 자리, 끼어든 행의 옛 자리(행 번호 다시
    매기기). 새 값이 부르는 참조 표기도 적는다(`note_texts`)."""
    marks: dict[str, _Mark] = {}
    removed: list[int] = []
    inserted: list[int] = []
    for (kind, _key, old_at, _new_at, _old, _new), one in zip(feed.changes, feed, strict=True):
        if kind == "added":
            inserted.append(int(old_at))
        elif kind == "removed":
            removed.append(int(old_at))
        same = 0
        if one.old is not None and one.new is not None:
            for index, columns in enumerate(feed.columns_of):
                if all(one.old[name] == one.new[name] for name in columns):
                    same |= 1 << index
        marks[one.key] = _Mark(one.old is not None, one.new is not None, same)
        if one.new is not None:
            note_texts(one.key, one.new)
    return marks, removed, inserted


def _leads(scratch: Any, phases: list[Any], row: dict[str, str]) -> list[str]:
    """그 행의 앞 값들 — 모으기마다(식별자 · 출발점)."""
    found = [scratch.text(phase, 0, row) for phase in phases]
    scratch.forget()
    return found


def _rows_with_texts(db: sqlite3.Connection, moved: set[tuple[str, int, str]]) -> set[str]:
    out: set[str] = set()
    for target, prefix, text in moved:
        for (key,) in db.execute(
            "SELECT key FROM row_texts WHERE target = ? AND prefix = ? AND text = ?",
            (target, int(prefix), text),
        ):
            out.add(key)
    return out


def _old_leads(db: sqlite3.Connection, keys: set[str], phases: int) -> dict[str, list[str]]:
    """옛 미러에서 그 키의 앞 값들 — 색인에 없으면(빈 행이었거나 새 키) 빈 목록."""
    names = ", ".join(f'"l{index}"' for index in range(phases))
    out: dict[str, list[str]] = {}
    for key in keys:
        row = db.execute(f"SELECT {names} FROM rows WHERE key = ?", (key,)).fetchone()
        if row is not None:
            out[key] = [str(one or "") for one in row]
    return out


def _affected(
    count: int,
    marks: Mapping[str, _Mark],
    text_keys: set[str],
    old_leads: dict[str, list[str]],
    new_leads: dict[str, list[str]],
    moved_keys: set[str],
) -> list[_Phase]:
    """`moved_keys` — 참조 답이 바뀐 행. 값이 그대로여도, 다른 칸만 바뀌었어도 모든 모으기를
    다시 모은다(어느 모으기가 그 답을 쓰는지 가리지 않는다)."""
    parts = [_Phase() for _ in range(count)]
    blank = [""] * count
    for key, mark in marks.items():
        if not mark.old and not mark.new:
            continue
        before = old_leads.get(key, blank) if mark.old else blank
        after = new_leads.get(key, blank) if mark.new else blank
        for index, part in enumerate(parts):
            unmoved = mark.old and mark.new and key not in moved_keys
            if unmoved and before[index] == after[index] and mark.same >> index & 1:
                continue  # 이 모으기가 읽는 칸이 안 바뀌었다
            part.touch(key, before[index], after[index])
    for key in text_keys:
        before, after = old_leads.get(key, blank), new_leads.get(key, blank)
        for index, part in enumerate(parts):
            part.touch(key, before[index], after[index])
    return parts


def _members(db: sqlite3.Connection, parts: list[_Phase]) -> None:
    """다시 모을 앞 값을 옛 미러에서 가졌던 키 — 모으기마다."""
    for index, part in enumerate(parts):
        for lead in part.leads:
            for (key,) in db.execute(
                "SELECT key FROM members WHERE phase = ? AND lead = ?", (index, lead)
            ):
                part.old_keys.add(key)


def _objects_of(prev: Path, folder: str, name: str) -> list[Path]:
    """지난 출력의 그 타입(관계) 파일들 — 나눠 쓴 것은 번호 차례."""
    base = prev / folder
    single = base / f"{name}.json"
    if single.exists():
        return [single]
    found = [
        (int(match.group(1)), path)
        for path in base.glob(f"{name}-*.json")
        if (match := re.fullmatch(rf"{re.escape(name)}-(\d+)\.json", path.name))
    ]
    return [path for _number, path in sorted(found)]


def _prev_rows(files: list[Path]) -> Iterator[dict[str, Any]]:
    for path in files:
        yield from json.loads(path.read_text(encoding="utf-8"))["rows"]


class _Renumber:
    """옛 행 번호 → 새 행 번호 — 지운 행의 옛 자리와 끼어든 행의 옛 자리로."""

    def __init__(self, removed: list[int], inserted: list[int]) -> None:
        self.removed, self.inserted = removed, inserted

    def __call__(self, number: int) -> int:
        at = number - 2
        return at - bisect_left(self.removed, at) + bisect_right(self.inserted, at) + 2


def _renumber_rows(found: Any, shift: Callable[[int], int]) -> Any:
    if isinstance(found, list):
        return [_renumber_rows(one, shift) for one in found]
    return shift(int(found))


def _move_item(item: dict[str, Any], shift: Callable[[int], int], file: str) -> dict[str, Any]:
    """미해결 한 건의 출처를 새 행 번호 · 새 파일 이름으로 — 자리만 바뀌고 나머지는
    그대로다."""
    source = item.get("_source") or {}
    if "rows" in source:
        source["rows"] = _renumber_rows(source["rows"], shift)
    source["file"] = file
    for option in item.get("options") or []:
        if isinstance(option, dict) and isinstance(option.get("rows"), list):
            option["rows"] = _renumber_rows(option["rows"], shift)
    return item


def _item_lead(item: dict[str, Any], phase: Any) -> str | None:
    """미해결 한 건의 앞 값 — 틀의 문구(`값 충돌`)에서. 이 모으기의 것이 아니면 None."""
    what = str(item.get("what") or "")
    if phase.edge:
        match = re.fullmatch(
            rf"관계 (.*?) -{re.escape(str(phase.one['relation']))}-> (.*) 의 칸 (\S+)", what
        )
        return match.group(1) if match else None
    match = re.fullmatch(rf"{re.escape(str(phase.one['type_slug']))} (.+) 의 칸 (\S+)", what)
    if match is None:
        return None
    try:
        return str(ast.literal_eval(match.group(1)))
    except (ValueError, SyntaxError):
        return None


@dataclass
class _Gathered:
    """모으기 하나를 부분 행으로 다시 모은 결과."""

    result: Any
    items: Iterator[tuple[int, dict[str, Any]]]
    """(첫 행, 객체 · 선) — 첫 행 차례. 선은 첫 행을 세 끝이 처음 나온 행에서."""


def _gather_phase(
    mapping: Any,
    scratch: Any,
    phase: Any,
    rows: Any,
    table_: Any,
    folder: Path,
    bucket_rows: int,
) -> _Gathered:
    """`table._gather` 를 모으기 하나에 — 작으면 메모리에서, 크면 바구니로. 결과는 첫 행
    차례."""
    import dataclasses

    mapping.table = dataclasses.replace(table_, rows=rows)
    count = spill.count_for(len(rows), bucket_rows)
    got = table._gather(mapping, scratch, [phase], folder, count)[0]
    if got.spill is None:
        items: Iterator[tuple[int, dict[str, Any]]]
        if phase.edge:
            first = table._first_rows(scratch, phase, list(rows))
            items = iter([(first[(one["src"], one["dst"])], one) for one in got.rows])
        else:
            items = iter([(int(one["_source"]["row"]), one) for one in got.rows])
    elif phase.edge:
        items = (
            (int(one[0]), one[1]) for one in spill.merged(got.spill, key=lambda one: one[0])
        )
    else:
        items = (
            (int(one["_source"]["row"]), one)
            for one in spill.merged(got.spill, key=lambda one: one["_source"]["row"])
        )
    return _Gathered(got.result, items)


def _splice(
    work: Workdir,
    pipeline: Any,
    plan: Plan,
    db: sqlite3.Connection,
    spec: dict[str, Any],
    got: Any,
    phases: list[Any],
    columns_of: list[list[str]],
    run: Path,
    feed: _Feed,
    marks: Mapping[str, _Mark],
    removed_at: list[int],
    inserted_at: list[int],
    text_keys: set[str],
    old_leads: dict[str, list[str]],
    new_leads: dict[str, list[str]],
    parts: list[_Phase],
    notes: list[str],
    matchers: dict[str, Any],
    bucket_rows: int,
    baseline: str | None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any], Touched | None]:
    """부분 행을 흘려 모으고 지난 출력에 끼워 넣는다 — (보고서, 미해결, 통계, 견줄 것)."""
    count = len(phases)
    file_name = got.name
    base_totals = plan.meta["totals"]
    shift = _Renumber(removed_at, inserted_at)
    folder = work.path(f"work/{run.name}.spill")

    # 1. 부분 행을 디스크에 — 모으기마다 새 행과 옛 행(통계의 옛 몫) 둘.
    new_spools = [_Spool(folder / f"new-{index:02d}") for index in range(count)]
    old_spools = [_Spool(folder / f"old-{index:02d}") for index in range(count)]
    candidates = {key for key, one in marks.items() if one.new} | text_keys
    for part in parts:
        candidates |= part.old_keys
    if len(candidates) > MAX_GATHERED:
        raise NotIncremental(f"다시 모을 행이 너무 많음({len(candidates):,})")
    blank_leads = [""] * count

    def put_old(change: _Change) -> None:
        """바뀐 · 지운 행의 옛 값 — 옛 앞 값이 다시 모을 것이거나 비었으면."""
        if change.old is None:
            return
        leads = old_leads.get(change.key, blank_leads)
        for index, part in enumerate(parts):
            if change.key not in part.touched and change.key not in part.old_keys:
                continue
            if not leads[index] or leads[index] in part.leads:
                old_spools[index].put({one: change.old[one] for one in columns_of[index]})

    pending = iter(feed)
    waiting = next(pending, None)
    for key, number, row in _aligned(work, got.rows):
        while waiting is not None and waiting.key < key:
            put_old(waiting)  # 지운 행
            waiting = next(pending, None)
        change: _Change | None = None
        if waiting is not None and waiting.key == key:
            change, waiting = waiting, next(pending, None)
            put_old(change)
        if row is None or key not in candidates:
            continue
        for index, part in enumerate(parts):
            member, touched_row = key in part.old_keys, key in part.touched
            if not member and not touched_row:
                continue
            cells = {one: row[one] for one in columns_of[index]}
            # 옛 쪽 — 값이 그대로인 행(답만 바뀐 행 · 그대로인 구성원)은 지금 값이 옛 값이다.
            if change is None:
                old_spools[index].put(cells)
            # 새 쪽 — 앞 값이 다시 모을 것인 행만(비었으면 빈 식별자로 센다).
            lead = new_leads[key][index] if touched_row else ""
            if not touched_row or not lead or lead in part.leads:
                new_spools[index].put(cells, number)
    while waiting is not None:
        put_old(waiting)
        waiting = next(pending, None)
    for spool in (*new_spools, *old_spools):
        spool.close()

    # 2. 모으기마다 다시 모은다 — 새 행은 지금 대상의 참조 답으로, 옛 행은 색인의 옛 답으로.
    current = _mapping(spec, got)
    current.matchers = matchers
    current_scratch = table._Scratch(current, spec, lambda _target: set())
    replay = _mapping(spec, got)
    replay.matchers = {target: _Replay(target, db) for target in replay.matchers}
    replay_scratch = table._Scratch(replay, spec, lambda _target: set())
    new_results: list[Any] = []
    old_results: list[Any] = []
    gathered: list[_Gathered] = []
    for index, phase in enumerate(phases):
        one = _gather_phase(
            current,
            current_scratch,
            phase,
            new_spools[index].rows(),
            got,
            folder / f"g-new-{index:02d}",
            bucket_rows,
        )
        gathered.append(one)
        new_results.append(one.result)
        old = _gather_phase(
            replay,
            replay_scratch,
            phase,
            old_spools[index].rows(),
            got,
            folder / f"g-old-{index:02d}",
            bucket_rows,
        )
        for _ in old.items:
            pass  # 다 흘려야 통계가 찬다(바구니는 모을 때 다 읽는다)
        old_results.append(old.result)

    # 3. 출력을 짓는다 — 머리, 타입 · 관계마다 지난 출력에 다시 모은 것을 끼워.
    table.write_head(
        current,
        pipeline.recipe,
        spec,
        got,
        run,
        _ontology(pipeline, spec),
        phases,
    )
    unresolved: list[dict[str, Any]] = []
    carried_unresolved = _carried_unresolved(plan.prev, parts, phases, shift, file_name)
    order_folder = work.path(f"work/{run.name}.edges")
    orders = _OrderWriter(order_folder)
    prev_order = work.path(str(plan.meta["order"]))
    edges_from = sum(1 for phase in phases if not phase.edge)
    touched = Touched.begin(work, plan.meta, baseline, run, parts, phases, spec)
    for index, phase in enumerate(phases):
        part = parts[index]
        new_result, old_result = new_results[index], old_results[index]
        head: dict[str, Any] = {"type_slug": str(phase.one["type_slug"])}
        if phase.edge:
            folder_name, name = sp.pipeline.RELATIONS_DIR, str(phase.one["relation"])
            old_count, new_count = len(old_result.rows), len(new_result.rows)
            prev_total = int(base_totals["edges"][index - edges_from]["rows"])
        else:
            folder_name, name = sp.pipeline.OBJECTS_DIR, str(phase.one["type_slug"])
            head["workspace_slug"] = phase.one.get("workspace_slug") or spec.get(
                "workspace_slug"
            )
            old_count, new_count = len(old_result.objects), len(new_result.objects)
            prev_total = int(base_totals["types"][index]["objects"])
        total = prev_total - old_count + new_count
        order = _read_order(prev_order, index) if phase.edge else None
        splice = _Splice(index, phase, part.leads, shift, file_name, touched, orders)
        carried = splice.carried(_prev_rows(_objects_of(plan.prev, folder_name, name)), order)
        merged = heapq.merge(carried, gathered[index].items, key=lambda one: one[0])
        table._write_rows(run / folder_name, name, total, splice.placed(merged, total), head)
        # 안전장치 — 지난 출력에서 뺀 것이 옛 몫과, 쓴 것이 헤아린 수와 같아야 한다.
        if splice.dropped != old_count or splice.written != total:
            raise NotIncremental(
                f"{name}: 옮길 객체의 수가 어긋남({splice.dropped} ≠ {old_count} · "
                f"{splice.written} ≠ {total})"
            )
        # 미해결 — 옮긴 것과 새 것을 첫 행 차례로.
        both = [
            *(_move_item(one, shift, file_name) for one in carried_unresolved.get(index, [])),
            *new_result.unresolved,
        ]
        both.sort(key=table._min_row)
        unresolved.extend(both)
    orders.close()
    if touched is not None:
        touched.close()
    table_text, totals = _finish(
        work,
        pipeline,
        spec,
        got,
        run,
        phases,
        gathered,
        new_results,
        old_results,
        replay,
        current,
        base_totals,
        unresolved,
        notes,
    )
    return table_text, unresolved, totals, touched


class _Splice:
    """모으기 하나의 끼워 넣기 — 지난 출력을 **한 번만** 읽으며 옮길 것은 번호를 다시 매겨
    내고, 다시 모은 앞 값의 것은 뺀다. 견줄 것(`Touched`)도 여기서 적는다."""

    def __init__(
        self,
        index: int,
        phase: Any,
        leads: set[str],
        shift: Callable[[int], int],
        file: str,
        touched: Touched | None,
        orders: _OrderWriter,
    ) -> None:
        self.orders = orders
        self.index, self.phase, self.leads = index, phase, leads
        self.shift, self.file, self.touched = shift, file, touched
        self.lead_key = "src" if phase.edge else "key"
        self.dropped = 0
        self.written = 0

    def carried(
        self, rows: Iterator[dict[str, Any]], order: Iterator[int] | None
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        if order is not None:
            for first, edge in zip(order, rows, strict=True):
                if str(edge["src"]) in self.leads:
                    self._drop(edge)
                else:
                    yield self.shift(int(first)), edge
            return
        for one in rows:
            if str(one["key"]) in self.leads:
                self._drop(one)
            else:
                yield (
                    self.shift(int(one["_source"]["row"])),
                    _moved_object(one, self.shift, self.file),
                )

    def _drop(self, item: dict[str, Any]) -> None:
        self.dropped += 1
        if self.touched is not None:
            self.touched.old(self.index, str(item[self.lead_key]), item)

    def placed(
        self, merged: Iterator[tuple[int, dict[str, Any]]], total: int
    ) -> Iterator[dict[str, Any]]:
        touched = self.touched
        for first, item in merged:
            if self.phase.edge:
                self.orders.put(self.index, first)
            if touched is not None:
                touched.new(self.index, str(item[self.lead_key]), item, self.written, total)
            self.written += 1
            yield item


def _moved_object(
    one: dict[str, Any], shift: Callable[[int], int], file: str
) -> dict[str, Any]:
    source = one["_source"]
    source["row"] = shift(int(source["row"]))
    source["file"] = file
    return one


def _ontology(pipeline: Any, spec: dict[str, Any]) -> dict[str, Any] | None:
    if not spec.get("ontology"):
        return None
    found: dict[str, Any] = sp.table._load_mapping(
        (Path(pipeline.recipe).parent / str(spec["ontology"])).resolve()
    )
    return found


def _texter(
    spec: dict[str, Any], got: Any, phases: list[Any], log: _Log
) -> Callable[[str, dict[str, str]], None]:
    """바뀐 행이 부르는 참조 대조의 표기 **전부**를 `log` 에 적는다 — 이번에 다시 모으지 않는
    모으기의 것까지. 처음 보는 표기는 대상에 묻고, 나중에 답이 바뀌면(대상에 식별자가 생기거나
    사라짐) 이 행을 찾아 다시 모은다."""
    mapping = _mapping(spec, got)
    current = [""]

    class _Noter:
        exact: frozenset[str] = frozenset()

        def __init__(self, target: str) -> None:
            self.target = target
            self.resolved: dict[str, str | None] = {}

        def find(self, text: str, *, prefix: bool = False) -> tuple[Any, str]:
            log.put(0, current[0], self.target, int(prefix), text)
            return None, "없음"

    mapping.matchers = {target: _Noter(target) for target in mapping.matchers}

    def note(key: str, row: dict[str, str]) -> None:
        current[0] = key
        for phase in phases:
            for one in table._phase_specs(phase):
                mapping.value(one, 0, row, at=(phase.at, ""))
        mapping.stats.clear()
        mapping.match_stats.clear()
        mapping.match_unresolved.clear()

    return note


def _carried_unresolved(
    prev: Path, parts: list[_Phase], phases: list[Any], shift: Callable[[int], int], file: str
) -> dict[int, list[dict[str, Any]]]:
    """지난 미해결 중 옮길 것 — 모으기마다. 다시 모을 앞 값의 것은 버린다(새로 센다)."""
    path = prev / sp.pipeline.UNRESOLVED
    if not path.exists():
        return {}
    found = json.loads(path.read_text(encoding="utf-8"))
    out: dict[int, list[dict[str, Any]]] = {}
    for item in found:
        for index, (phase, part) in enumerate(zip(phases, parts, strict=True)):
            lead = _item_lead(item, phase)
            if lead is None:
                continue
            if lead not in part.leads:
                out.setdefault(index, []).append(item)
            break
        else:
            raise NotIncremental("미해결의 문구를 읽지 못함")
    return out


def _finish(
    work: Workdir,
    pipeline: Any,
    spec: dict[str, Any],
    got: Any,
    run: Path,
    phases: list[Any],
    gathered: list[_Gathered],
    new_results: list[Any],
    old_results: list[Any],
    replay: Any,
    current: Any,
    base_totals: dict[str, Any],
    unresolved: list[dict[str, Any]],
    notes: list[str],
) -> tuple[str, dict[str, Any]]:
    """통계를 갱신하고 미해결 · 보고서를 쓴다."""
    type_results = [
        one for one, phase in zip(new_results, phases, strict=True) if not phase.edge
    ]
    edge_results = [one for one, phase in zip(new_results, phases, strict=True) if phase.edge]
    old_types = [one for one, phase in zip(old_results, phases, strict=True) if not phase.edge]
    old_edges = [one for one, phase in zip(old_results, phases, strict=True) if phase.edge]
    totals = _combine(
        base_totals,
        _plain_totals(replay, old_types, old_edges),
        _plain_totals(current, type_results, edge_results),
    )
    types = [phase for phase in phases if not phase.edge]
    edges = [phase for phase in phases if phase.edge]
    table_types = [
        sp.table.TypeResult(
            slug=str(phase.one["type_slug"]),
            workspace_slug=phase.one.get("workspace_slug") or spec.get("workspace_slug"),
            objects=table._Sized(one["objects"]),
            blank_keys=one["blank_keys"],
            conflicts=one["conflicts"],
            unresolved=[],
        )
        for phase, one in zip(types, totals["types"], strict=True)
    ]
    table_edges = [
        sp.table.EdgeResult(
            relation=str(phase.one["relation"]),
            type_slug=str(phase.one["type_slug"]),
            rows=table._Sized(one["rows"]),
            blank_ends=one["blank_ends"],
            conflicts=one["conflicts"],
            unresolved=[],
        )
        for phase, one in zip(edges, totals["edges"], strict=True)
    ]
    pipeline_mod = sp.pipeline
    pipeline_mod._write(run / pipeline_mod.UNRESOLVED, unresolved)
    stats, match = _report_stats(totals)
    viewed = SimpleNamespace(table=got, stats=stats, match_stats=match, parsers={})
    mapping_path = Path(pipeline.recipe)
    text: str = sp.table._report(
        viewed, mapping_path, table_types, run, len(unresolved), table_edges
    )
    if notes:
        text += "\n\n" + "\n".join(notes)
    (run / sp.table.REPORT).write_text(text + "\n", encoding="utf-8")
    return text, totals


def _update_index(
    work: Workdir,
    db_file: Path,
    run: Path,
    *,
    totals: dict[str, Any],
    marks: Mapping[str, _Mark],
    text_keys: set[str],
    old_leads: dict[str, list[str]],
    new_leads: dict[str, list[str]],
    moved_texts: set[tuple[str, int, str]],
    matchers: dict[str, Any],
    texts: Path,
    digest: str,
    mark: str,
    baseline: Mapping[str, Any],
) -> None:
    """색인을 이번 실행의 것으로 이어 쓴다 — 트랜잭션 하나(도중에 멈추면 저널이 남아 다음
    실행이 믿지 않는다)."""
    db = _open(db_file)
    try:
        db.execute("BEGIN IMMEDIATE")
        for key in (*marks, *text_keys):
            for phase, lead in enumerate(old_leads.get(key, [])):
                if lead:
                    db.execute(
                        "DELETE FROM members WHERE phase = ? AND lead = ? AND key = ?",
                        (phase, lead, key),
                    )
            db.execute("DELETE FROM rows WHERE key = ?", (key,))
        for key, leads in new_leads.items():
            marks_sql = ", ".join("?" for _ in range(1 + len(leads)))
            db.execute(f"INSERT OR REPLACE INTO rows VALUES ({marks_sql})", (key, *leads))
            db.executemany(
                "INSERT OR IGNORE INTO members VALUES (?, ?, ?)",
                [(phase, lead, key) for phase, lead in enumerate(leads) if lead],
            )
        # 참조 값 — 바뀐 행의 것을 지우고 새로. 답이 바뀐 값은 답만 고친다.
        db.executemany("DELETE FROM row_texts WHERE key = ?", ((key,) for key in marks))

        def store(target: str, prefix: int, text: str) -> None:
            found, outcome = matchers[target].find(text, prefix=bool(prefix))
            db.execute(
                "INSERT OR REPLACE INTO lookups VALUES (?, ?, ?, ?, ?)",
                (target, prefix, text, found, outcome),
            )

        for _phase, key, target, prefix, text in _read_log(texts):
            store(target, prefix, text)
            db.execute(
                "INSERT OR IGNORE INTO row_texts VALUES (?, ?, ?, ?)",
                (target, prefix, text, key),
            )
        for target, prefix, text in moved_texts:
            store(target, prefix, text)
        db.execute(
            "DELETE FROM lookups WHERE NOT EXISTS (SELECT 1 FROM row_texts t WHERE "
            "t.target = lookups.target AND t.prefix = lookups.prefix "
            "AND t.text = lookups.text)"
        )
        _write_meta(
            db,
            {
                "run": f"runs/{run.name}",
                "order": f"work/{run.name}.edges",
                "totals": totals,
                "digest": digest,
                "mirror": mark,
                **baseline,
            },
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


# --------------------------------------------------------------------------
# 비교 — 건드린 것만
# --------------------------------------------------------------------------


class _Log:
    """(모으기, 줄 …)을 차례로 쌓는 파일 — `Touched` 의 옛 줄 · 새 줄."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: Any = path.open("wb")
        self._chunk: list[Any] = []
        self.count: Counter[int] = Counter()

    def put(self, phase: int, *item: Any) -> None:
        self._chunk.append((phase, *item))
        self.count[phase] += 1
        if len(self._chunk) >= 1000:
            self._write()

    def _write(self) -> None:
        pickle.dump(self._chunk, self._handle, protocol=pickle.HIGHEST_PROTOCOL)
        self._chunk = []

    def close(self) -> None:
        if self._handle.closed:
            return
        if self._chunk:
            self._write()
        self._handle.close()


def _read_log(path: Path) -> Iterator[tuple[Any, ...]]:
    with path.open("rb") as handle:
        while True:
            try:
                yield from pickle.load(handle)
            except EOFError:
                return


class Touched:
    """기준 출력(지난번 적용된 것)과 다를 수 있는 객체 · 선 — 증분 비교(`delta`)는 이것만
    견준다.

    나머지는 **내용이 같다**: 이번에 다시 모으지 않은 것은 지난 출력에서 그대로 옮겼고, 지난
    출력은 기준 그 자체이거나(적용됨) 기준과 어디가 다른지를 앞 실행이 적어 두었다. 적용을
    기다리는 동안 여러 번 돌아도 그 목록을 이어 간다 — `.base` 파일에 다를 수 있는 앞
    값(`leads`)과 **기준에 있던 그 줄**을 적는다.

    기준이 다른 것으로 바뀌었으면(나눠 보내다 접음 · 걷어냄 · 전체로 다시 만듦) 이어 견줄 수
    없다 — 전체 비교로."""

    def __init__(
        self,
        work: Workdir,
        baseline: str,
        run: Path,
        phases: list[Any],
        leads: list[set[str]],
        carried: list[set[str]],
        workspaces: Mapping[int, Any],
    ) -> None:
        self.work, self.baseline, self.run = work, baseline, run
        self.phases = phases
        self.leads = leads
        """모으기마다 다를 수 있는 앞 값 — 앞 실행들이 적어 둔 것까지."""
        self._carried = carried
        """앞 실행들이 적어 둔 앞 값 — 기준에 있던 줄은 이미 적혀 있다."""
        self.relative = f"work/{run.name}.base"
        self._old = _Log(work.path(self.relative))
        self.new_path = work.path(f"work/{run.name}.spill/touched")
        self._new = _Log(self.new_path)
        self.totals: list[int] = [0] * len(phases)
        self.workspaces = workspaces
        """모으기 → 객체를 둘 작업공간(관계는 없다)."""
        self._old.put(-1, [sorted(one) for one in leads])

    @classmethod
    def begin(
        cls,
        work: Workdir,
        meta: Mapping[str, Any],
        baseline: str | None,
        run: Path,
        parts: list[_Phase],
        phases: list[Any],
        spec: Mapping[str, Any],
    ) -> Touched | None:
        """이어 견줄 수 있으면 그 자리 — 아니면 None(전체 비교)."""
        if baseline is None:
            return None
        before: list[set[str]] = [set() for _ in phases]
        earlier: Path | None = None
        if baseline != meta.get("run"):
            if baseline != meta.get("base"):
                return None
            relative = meta.get("base_file")
            if relative:
                earlier = work.path(str(relative))
                if not earlier.exists():
                    return None
                first = next(_read_log(earlier))
                before = [set(one) for one in first[1]]
        leads = [found | part.leads for found, part in zip(before, parts, strict=True)]
        if sum(len(one) for one in leads) > MAX_TOUCHED:
            return None
        workspaces = {
            index: phase.one.get("workspace_slug") or spec.get("workspace_slug")
            for index, phase in enumerate(phases)
            if not phase.edge
        }
        made = cls(work, baseline, run, phases, leads, before, workspaces)
        if earlier is not None:
            for phase, *item in _read_log(earlier):
                if phase >= 0:
                    made._old.put(phase, *item)
        return made

    def old(self, phase: int, lead: str, item: dict[str, Any]) -> None:
        """지난 출력에서 뺀 줄 — 그 앞 값이 처음 다르게 된 것이면 기준에 있던 줄이다."""
        if lead not in self._carried[phase]:
            self._old.put(phase, item)

    def new(
        self, phase: int, lead: str, item: dict[str, Any], position: int, total: int
    ) -> None:
        self.totals[phase] = total
        if lead in self.leads[phase]:
            self._new.put(phase, position, item)

    def close(self) -> None:
        self._old.close()
        self._new.close()

    def count(self, phases: Iterable[int]) -> int:
        """견줄 줄 수(옛 것과 새 것 가운데 큰 쪽) — 바구니 수를 정한다."""
        wanted = set(phases)
        return max(
            sum(found for one, found in log.count.items() if one in wanted)
            for log in (self._old, self._new)
        )

    def slug(self, phase: int) -> str:
        return str(self.phases[phase].one["type_slug"])

    def name(self, phase: int) -> str:
        one = self.phases[phase].one
        return str(one["relation"] if self.phases[phase].edge else one["type_slug"])

    def place(self, phase: int, position: int) -> tuple[str, int]:
        """이번 출력에서의 차례 — 전체 비교가 파일을 읽는 차례(파일 이름, 그 안의 자리)."""
        cap = int(sp.pipeline.MAX_ROWS)
        name = self.name(phase)
        if self.totals[phase] > cap:
            name = f"{name}-{position // cap + 1:03d}"
        return f"{name}.json", position

    def old_rows(self, wanted: set[int]) -> Iterator[tuple[str, dict[str, Any]]]:
        for phase, *item in _read_log(self._old.path):
            if phase in wanted:
                yield self.slug(phase), item[0]

    def new_rows(self, wanted: set[int]) -> Iterator[tuple[str, Any, dict[str, Any]]]:
        for phase, position, item in _read_log(self.new_path):
            if phase in wanted:
                # 파일에 쓴 것과 같은 모양으로 — 전체 비교는 JSON 을 읽는다.
                row = json.loads(json.dumps(item, ensure_ascii=False))
                yield self.slug(phase), self.place(phase, position), row


def delta(
    touched: Touched,
    out: Path,
    *,
    source: str,
    options: dict[str, str] | None = None,
    bucket_rows: int = spill.BUCKET_ROWS,
    scratch: Path | None = None,
) -> diff.Delta:
    """`diff.delta` 와 **같은 폴더**를 건드린 것만 견줘 만든다 — 바이트도 건수도 같다."""
    if out.exists():
        raise FileExistsError(f"실행 폴더가 이미 있습니다: {out}")
    current = touched.run
    manifest_now, problems = diff.head(current)
    if problems:
        raise ValueError(f"이번 실행의 출력을 읽을 수 없습니다: {problems}")
    folder = scratch or out.with_name(f"{out.name}.spill")
    compared: list[diff.Compared] = []
    try:
        for kind in (sp.pipeline.OBJECTS_DIR, sp.pipeline.RELATIONS_DIR):
            edge = kind == sp.pipeline.RELATIONS_DIR
            wanted = {
                index for index, phase in enumerate(touched.phases) if phase.edge is edge
            }
            # 전체 비교가 세는 타입 — 어느 쪽이든 파일이 있는 것(줄이 없는 타입은 파일이 없다).
            now = {one for one in wanted if touched.totals[one]}
            slugs = {
                touched.slug(one) for one in wanted if one in now or touched._old.count[one]
            }
            found = diff.compare_rows(
                kind,
                touched.old_rows(wanted),
                touched.new_rows(wanted),
                folder,
                spill.count_for(touched.count(wanted), bucket_rows),
                slugs,
                {touched.slug(one): touched.workspaces.get(one) for one in now},
            )
            for slug, tally in found.counts.items():
                total = sum(touched.totals[one] for one in now if touched.slug(one) == slug)
                tally.unchanged = total - tally.created - tally.changed
            compared.append(found)
        objects, edges = compared
        out.mkdir(parents=True)
        orders = [
            diff.write_sorted(
                out,
                kind,
                spill.merged(one.sent, key=lambda item: (item[0], item[1])),
                one.workspaces if kind == sp.pipeline.OBJECTS_DIR else {},
            )
            for kind, one in (
                (sp.pipeline.OBJECTS_DIR, objects),
                (sp.pipeline.RELATIONS_DIR, edges),
            )
        ]
    finally:
        for one in compared:
            one.sent.drop()
        shutil.rmtree(folder, ignore_errors=True)
    diff.finish(
        out,
        current,
        manifest_now,
        source=source,
        options=options,
        objects_order=orders[0],
        relations_order=orders[1],
        graves_objects=objects.graves,
        graves_edges=edges.graves,
    )
    return diff.Delta(run=out, objects=objects.counts, relations=edges.counts)


def compared(work: Workdir, run: Path, baseline: str | None, *, empty: bool) -> None:
    """비교를 마쳤다 — 기준과 **같으면** 다음 실행은 이 출력에서 이어 견준다(기준 폴더는
    그대로여도). 다르면 변환이 적어 둔 것을 둔다."""
    if not empty or baseline is None or _journal(index_path(work)).exists():
        return
    if index_run(work) != f"runs/{run.name}":
        return
    db = _open(index_path(work))
    try:
        with db:
            _write_meta(db, {"base": baseline, "base_file": None})
    finally:
        db.close()


# --------------------------------------------------------------------------
# 실행 쪽 자리 — 전체 변환과 증분 변환 가운데 고른다
# --------------------------------------------------------------------------


def eligible_recipe(pipeline: Any) -> str:
    """레시피가 증분으로 할 수 없는 모양이면 그 까닭 — 색인을 짓지 않고(매번 다시 짓는 값이
    든다) 늘 전체로 간다. 할 수 있으면 빈 글자."""
    try:
        return _refused_recipe(sp.table._load_mapping(pipeline.recipe))
    except (AttributeError, TypeError, ValueError, OSError, sp.table.Stop):
        return "레시피를 읽지 못함"  # 무엇이 잘못인지는 전체 변환이 말한다


def phase_count(pipeline: Any) -> int:
    spec = sp.table._load_mapping(pipeline.recipe)
    return len(spec.get("types") or []) + len(spec.get("relations") or [])


def record_changes(work: Workdir, pipeline: Any, path: Path) -> Changes | None:
    """미러 반영에서 바뀐 행을 적을 자리 — 이어 쓸 색인이 있을 때만(첫 적재는 미러 전체가
    바뀐 것이다)."""
    if not index_path(work).exists() or eligible_recipe(pipeline):
        return None
    return Changes(path)


@dataclass
class Conversion:
    made: Any
    """`convert.Converted`."""
    mode: str
    """`incremental` · `full`."""
    reason: str
    """전체로 간 까닭."""
    touched: Touched | None
    """기준과 견줄 것 — 없으면 출력 전체를 견준다."""


def run_conversion(
    work: Workdir,
    pipeline: Any,
    source: Path,
    run: Path,
    changes: Changes | None,
    *,
    server: str = "",
    token: str = "",
    enabled: bool = True,
    transform: Callable[..., Any],
    baseline: str | None = None,
) -> Conversion:
    """변환 — 증분에 실패하면 실행 폴더를 지우고 전체 변환(색인도 새로 짓는다)으로 간다.
    `baseline` 은 지금 기준 출력(지난번 적용된 것)의 상대 경로 — 견줄 것을 적어 둘 때 쓴다."""
    reason = ""
    if not enabled:
        reason = "증분 계획을 사용하지 않음"
    elif changes is None:
        reason = eligible_recipe(pipeline) or "색인이 없음(첫 실행이거나 다시 지음)"
    else:
        try:
            made, touched = convert(
                work,
                pipeline,
                source,
                run,
                changes,
                server=server,
                token=token,
                baseline=baseline,
            )
            return Conversion(made, "incremental", "", touched)
        except NotIncremental as refused:
            reason = str(refused)
        except Exception as error:
            # 증분의 결과를 믿을 수 없다 — 전체로 만든 것이 늘 맞다.
            log.warning("증분 변환 실패 — 전체로 다시 변환합니다", exc_info=True)
            reason = f"증분 변환 오류({type(error).__name__})"
        if run.exists():
            shutil.rmtree(run)
        shutil.rmtree(work.path(f"work/{run.name}.spill"), ignore_errors=True)
        work.path(f"work/{run.name}.base").unlink(missing_ok=True)
    builder: Builder | None = None
    if enabled and not eligible_recipe(pipeline):
        try:
            builder = Builder(
                work,
                work.mirror_keys(),
                phase_count(pipeline),
                work.path(f"work/{run.name}.edges"),
            )
        except (sqlite3.Error, OSError):
            log.warning("증분 계획의 색인을 짓지 못했습니다: %s", work.root, exc_info=True)
    try:
        made = transform(
            source, pipeline.recipe, run, server=server, token=token, observer=builder
        )
    except BaseException:
        if builder is not None:
            builder.abandon()
        raise
    if builder is not None:
        try:
            builder.seal(
                {
                    "version": VERSION,
                    "run": f"runs/{run.name}",
                    "digest": fingerprint(pipeline),
                    "mirror": work.mirror_mark(),
                    "order": f"work/{run.name}.edges",
                    "base": None,
                    "base_file": None,
                }
            )
        except (sqlite3.Error, OSError, ValueError):
            # 색인은 다음 실행을 빠르게 할 뿐이다 — 짓지 못해도 변환한 것은 맞다.
            log.warning("증분 계획의 색인을 짓지 못했습니다: %s", work.root, exc_info=True)
            builder.abandon()
    return Conversion(made, "full", reason, None)
