"""미러 — 원천 키마다 **최신 행** 하나.

레시피(`sp-table/1`)는 여러 원천 행을 객체 하나로 모은다(과제 하나가 모델 행 수십 개에 걸쳐
나온다). 그래서 바뀐 행만 변환하면 모은 값이 일부만 남는다 — **바뀐 행과 식별자가 같은 행
전부**를 다시 모은다(`relay_core.incremental`). 그것을 미러가 있어 할 수 있다. 이어 갈 수
없으면 미러 전체를 변환한다(식별자로 나눠서 — `relay_core.table`). 어느 쪽이든 같은 입력이면
같은 결과라 출력끼리의 비교(`diff`)가 맞는다.

여기는 모양 · 규칙만 안다. 어디에 저장할지(DB · 파일)는 부르는 쪽이 정한다(`to_rows` ·
`from_rows`). 실행은 미러를 메모리에 들지 않는다 — 파일의 키 차례 행과 받은 것을 이어 새 파일을
쓴다(`Merge` · `Workdir.merge_mirror`). 규칙 · 셈은 `Mirror.apply` 와 같다.
"""

from __future__ import annotations

import csv
import itertools
import pickle
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relay_core.adapters.base import Fetched
from relay_core.intake import Tally
from relay_core.records import Records, SourceRecord


@dataclass(frozen=True)
class Applied:
    added: int
    changed: int
    unchanged: int
    removed: int


class Mirror:
    def __init__(self, records: Iterable[SourceRecord] = ()) -> None:
        self._rows: dict[str, SourceRecord] = {
            one.key: one for one in records if not one.deleted
        }

    def __len__(self) -> int:
        return len(self._rows)

    def apply(self, fetched: Fetched) -> Applied:
        """받은 것을 반영한다. 지운 표시는 빼고, **전량이고 끝까지 받았으면** 이번에 안 온 것도
        뺀다(부재 대조). 증분(`since`)이면 안 온 것은 그대로 둔다 — 안 바뀐 것이다."""
        added = changed = unchanged = removed = 0
        seen: set[str] = set()
        for one in fetched.records:
            seen.add(one.key)
            before = self._rows.get(one.key)
            if one.deleted:
                if before is not None:
                    del self._rows[one.key]
                    removed += 1
                continue
            if before is None:
                added += 1
            elif dict(before.values) != dict(one.values):
                changed += 1
            else:
                unchanged += 1
            self._rows[one.key] = one
        whole = fetched.full and fetched.complete
        covers = fetched.covers if fetched.complete else None
        if whole or covers is not None:
            for key in [key for key in self._rows if key not in seen]:
                if whole or (covers is not None and covers(key)):
                    del self._rows[key]
                    removed += 1
        return Applied(added=added, changed=changed, unchanged=unchanged, removed=removed)

    def rows(self) -> Iterator[SourceRecord]:
        """키 차례로 — `state.MirrorFile.rows` 와 같은 모양(계산 칸이 얹힌다)."""
        return iter(self.records())

    def records(self) -> list[SourceRecord]:
        """키 순서로 — 같은 미러면 같은 차례다(변환이 같은 결과를 내는 전제)."""
        return [self._rows[key] for key in sorted(self._rows)]

    def columns(self) -> list[str]:
        """나온 차례대로 모든 열."""
        out: dict[str, None] = {}
        for one in self.records():
            for column in one.values:
                out.setdefault(column, None)
        return list(out)

    def write_csv(self, path: Path) -> Path:
        """`sp_table` 이 읽는 표 — BOM 붙은 UTF-8(그 도구가 먼저 시도하는 인코딩)."""
        return write_csv(self.records, path)

    def to_rows(self) -> list[dict[str, Any]]:
        return [
            {"key": one.key, "values": dict(one.values), "updated_at": one.updated_at}
            for one in self.records()
        ]

    @classmethod
    def from_rows(cls, rows: Iterable[dict[str, Any]]) -> Mirror:
        return cls(
            SourceRecord(
                key=str(row["key"]),
                values={str(k): str(v) for k, v in dict(row.get("values") or {}).items()},
                updated_at=row.get("updated_at"),
            )
            for row in rows
        )


def write_csv(rows: Callable[[], Iterable[SourceRecord]], path: Path) -> Path:
    """키 차례의 행 → `sp_table` 이 읽는 표. 열은 나온 차례대로 모든 열이라 **두 번 읽는다**
    (`rows()` 가 부를 때마다 처음부터 낸다 — 파일의 미러도 그렇게 흘려 쓴다)."""
    seen: dict[str, None] = {}
    for one in rows():
        for column in one.values:
            seen.setdefault(column, None)
    columns = list(seen)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for one in rows():
            writer.writerow([one.values.get(column, "") for column in columns])
    return path


class Merge:
    """`Mirror.apply` 를 **흘려서** — 키 차례의 옛 행과 받은 것을 이어 키 차례의 새 행을 낸다.
    같은 키가 여러 번 오면 온 차례대로 하나씩 반영한다 — `apply` 처럼. 수(`applied` ·
    `count`)는 `rows` 를 끝까지 돈 뒤에 맞다.

    받은 것이 디스크로 넘쳤으면(`records.Records` — 원천이 크다) 그 묶음들을 **키 차례로 이어
    읽는다** — 메모리에는 받은 것도 다 들지 않는다. 아니면 받은 것만 메모리에 든다."""

    def __init__(self, fetched: Fetched, changes: Changes | None = None) -> None:
        self._changes = changes
        self._full = fetched.full and fetched.complete
        self._covers = fetched.covers if fetched.complete else None
        """증분이라도 이번에 안 온 이 키는 지운 것인가 — 펼친 항목의 원소 목록을 다 받았다."""
        self._source: Records | None = (
            fetched.records
            if isinstance(fetched.records, Records) and fetched.records.spilled
            else None
        )
        self._incoming: dict[str, list[SourceRecord]] = {}
        self.tally = Tally()
        """받기 점검(`intake`)의 재료 — 반영 전 미러 · 받은 행에서 칸마다 값이 있던 수."""
        for one in fetched.records:
            if self._source is None:
                self._incoming.setdefault(one.key, []).append(one)
            if not one.deleted:
                self.tally.received += 1
                self.tally.filled.update(c for c, v in one.values.items() if _has(v))
        self.added = self.changed = self.unchanged = self.removed = 0
        self.count = 0
        """새 미러의 행 수."""

    @property
    def applied(self) -> Applied:
        return Applied(
            added=self.added,
            changed=self.changed,
            unchanged=self.unchanged,
            removed=self.removed,
        )

    def _groups(self) -> Iterator[tuple[str, list[SourceRecord]]]:
        """받은 것을 키 차례로 — 키마다 받은 차례의 목록."""
        if self._source is None:
            for key in sorted(self._incoming):
                yield key, self._incoming[key]
            return
        for key, group in itertools.groupby(self._source.in_key_order(), key=lambda r: r.key):
            yield key, list(group)

    def _settle(
        self, before: SourceRecord | None, incoming: list[SourceRecord]
    ) -> SourceRecord | None:
        old = before
        settled = self._fold(before, incoming)
        if old is not None and settled is not None:
            # 값이 있던 칸을 이번에 잃었나 — 빈 값이든 칸이 아예 없든(파일의 머리글이 바뀜).
            self.tally.lost.update(
                c for c, v in old.values.items() if _has(v) and not _has(settled.values.get(c))
            )
        return settled

    def _fold(
        self, before: SourceRecord | None, incoming: list[SourceRecord]
    ) -> SourceRecord | None:
        for one in incoming:
            if one.deleted:
                if before is not None:
                    self.removed += 1
                    before = None
                continue
            if before is None:
                self.added += 1
            elif dict(before.values) != dict(one.values):
                self.changed += 1
            else:
                self.unchanged += 1
            before = one
        return before

    def rows(self, existing: Iterable[SourceRecord]) -> Iterator[SourceRecord]:
        """`existing` 는 키 차례(파일의 미러) — 어긋나면 멈춘다(이어 쓴 미러가 틀어진다).

        `changes` 를 주면 바뀐 행을 적는다(증분 계획 — `relay_core.incremental`): 지운 행은 옛
        자리, 새 행은 옛 미러에 끼어드는 자리와 새 자리, 값이 바뀐 행은 두 자리와 옛 · 새 값.
        자리는 0부터의 차례다(원천 표의 행 번호는 자리 + 2)."""
        groups = self._groups()
        waiting = next(groups, None)
        last: str | None = None
        changes = self._changes
        old_at = 0
        for old in existing:
            if last is not None and old.key <= last:
                raise ValueError(
                    f"미러의 정렬 순서가 올바르지 않습니다({last!r} 다음에 {old.key!r})"
                )
            last = old.key
            self.tally.before += 1
            self.tally.old_filled.update(c for c, v in old.values.items() if _has(v))
            while waiting is not None and waiting[0] < old.key:
                new = self._settle(None, waiting[1])
                waiting = next(groups, None)
                if new is not None:
                    if changes is not None:
                        changes.added(new.key, old_at, self.count, new.values)
                    self.count += 1
                    yield new
            if waiting is not None and waiting[0] == old.key:
                new = self._settle(old, waiting[1])
                waiting = next(groups, None)
                if new is not None:
                    if changes is not None and dict(new.values) != dict(old.values):
                        changes.changed(new.key, old_at, self.count, old.values, new.values)
                    self.count += 1
                    yield new
                elif changes is not None:
                    changes.removed(old.key, old_at, old.values)
                old_at += 1
                continue
            if self._full or (self._covers is not None and self._covers(old.key)):
                # 전량을 끝까지 받았는데 안 왔다(부재 대조) · 원소 목록을 받은 항목인데 그
                # 원소가 없다(목록에서 빠졌다).
                self.removed += 1
                if changes is not None:
                    changes.removed(old.key, old_at, old.values)
                old_at += 1
                continue
            self.count += 1
            old_at += 1
            yield old
        while waiting is not None:
            new = self._settle(None, waiting[1])
            waiting = next(groups, None)
            if new is not None:
                if changes is not None:
                    changes.added(new.key, old_at, self.count, new.values)
                self.count += 1
                yield new
        self.tally.removed = self.removed
        if changes is not None:
            changes.close(before=old_at, after=self.count)


class Changes:
    """미러 반영에서 바뀐 행 — 디스크에 흘려 쓴다(전량 다시 받기 날에는 미러 전체가 바뀐다).

    한 줄은 `(종류, 키, 옛 자리, 새 자리, 옛 값, 새 값)` — 종류는 `added` · `changed` ·
    `removed`, 없는 자리 · 값은 None. 미러의 키 차례로 온다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: Any = path.open("wb")
        self._chunk: list[tuple[Any, ...]] = []
        self.count = 0
        self.before = 0
        self.after = 0
        self.base = ""
        """이 변경을 반영하기 **전** 미러의 표지(`Workdir.mirror_mark`) — 색인이 본 미러와
        같아야 이 변경만으로 이어 갈 수 있다."""

    def _put(self, item: tuple[Any, ...]) -> None:
        self._chunk.append(item)
        self.count += 1
        if len(self._chunk) >= 1000:
            pickle.dump(self._chunk, self._handle, protocol=pickle.HIGHEST_PROTOCOL)
            self._chunk = []

    def added(self, key: str, insert_at: int, new_at: int, values: Any) -> None:
        self._put(("added", key, insert_at, new_at, None, dict(values)))

    def changed(self, key: str, old_at: int, new_at: int, old: Any, new: Any) -> None:
        self._put(("changed", key, old_at, new_at, dict(old), dict(new)))

    def removed(self, key: str, old_at: int, values: Any) -> None:
        self._put(("removed", key, old_at, None, dict(values), None))

    def close(self, *, before: int = 0, after: int = 0) -> None:
        if self._handle.closed:
            return
        self.before, self.after = before, after
        if self._chunk:
            pickle.dump(self._chunk, self._handle, protocol=pickle.HIGHEST_PROTOCOL)
            self._chunk = []
        self._handle.close()

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        with self.path.open("rb") as handle:
            while True:
                try:
                    chunk = pickle.load(handle)
                except EOFError:
                    return
                yield from chunk

    def drop(self) -> None:
        self.close()
        self.path.unlink(missing_ok=True)


def _has(value: str | None) -> bool:
    return bool(value and value.strip())
