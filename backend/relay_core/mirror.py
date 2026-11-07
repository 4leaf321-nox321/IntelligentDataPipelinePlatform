"""미러 — 원천 키마다 **최신 행** 하나.

레시피(`sp-table/1`)는 여러 원천 행을 객체 하나로 모은다(과제 하나가 모델 행 수십 개에 걸쳐
나온다). 그래서 바뀐 행만 변환하면 모은 값이 일부만 남는다 — **늘 미러 전체를 다시 변환하고**
(식별자로 나눠서 — `relay_core.table`), 줄이는 일은 변환이 끝난 출력끼리의 비교(`diff`)가 한다.
변환은 같은 입력이면 같은 결과라 비교가 맞는다.

여기는 모양 · 규칙만 안다. 어디에 저장할지(DB · 파일)는 부르는 쪽이 정한다(`to_rows` ·
`from_rows`). 실행은 미러를 메모리에 들지 않는다 — 파일의 키 차례 행과 받은 것을 이어 새 파일을
쓴다(`Merge` · `Workdir.merge_mirror`). 규칙 · 셈은 `Mirror.apply` 와 같다.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relay_core.adapters.base import Fetched
from relay_core.intake import Tally
from relay_core.records import SourceRecord


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
        if fetched.full and fetched.complete:
            for key in [key for key in self._rows if key not in seen]:
                del self._rows[key]
                removed += 1
        return Applied(added=added, changed=changed, unchanged=unchanged, removed=removed)

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
    받은 것만 메모리에 든다(증분이면 몇 건). 같은 키가 여러 번 오면 온 차례대로 하나씩 반영한다
    — `apply` 처럼. 수(`applied` · `count`)는 `rows` 를 끝까지 돈 뒤에 맞다."""

    def __init__(self, fetched: Fetched) -> None:
        self._full = fetched.full and fetched.complete
        self._incoming: dict[str, list[SourceRecord]] = {}
        self.tally = Tally()
        """받기 점검(`intake`)의 재료 — 반영 전 미러 · 받은 행에서 칸마다 값이 있던 수."""
        for one in fetched.records:
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

    def _settle(self, before: SourceRecord | None, key: str) -> SourceRecord | None:
        old = before
        settled = self._fold(before, key)
        if old is not None and settled is not None:
            # 값이 있던 칸을 이번에 잃었나 — 빈 값이든 칸이 아예 없든(파일의 머리글이 바뀜).
            self.tally.lost.update(
                c for c, v in old.values.items() if _has(v) and not _has(settled.values.get(c))
            )
        return settled

    def _fold(self, before: SourceRecord | None, key: str) -> SourceRecord | None:
        for one in self._incoming[key]:
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
        """`existing` 는 키 차례(파일의 미러) — 어긋나면 멈춘다(이어 쓴 미러가 틀어진다)."""
        waiting = sorted(self._incoming)
        at = 0
        last: str | None = None
        for old in existing:
            if last is not None and old.key <= last:
                raise ValueError(f"미러의 차례가 어긋났습니다({last!r} 뒤에 {old.key!r})")
            last = old.key
            self.tally.before += 1
            self.tally.old_filled.update(c for c, v in old.values.items() if _has(v))
            while at < len(waiting) and waiting[at] < old.key:
                new = self._settle(None, waiting[at])
                at += 1
                if new is not None:
                    self.count += 1
                    yield new
            if at < len(waiting) and waiting[at] == old.key:
                new = self._settle(old, old.key)
                at += 1
                if new is not None:
                    self.count += 1
                    yield new
                continue
            if self._full:
                self.removed += 1  # 전량을 끝까지 받았는데 안 왔다 — 부재 대조
                continue
            self.count += 1
            yield old
        while at < len(waiting):
            new = self._settle(None, waiting[at])
            at += 1
            if new is not None:
                self.count += 1
                yield new
        self.tally.removed = self.removed


def _has(value: str | None) -> bool:
    return bool(value and value.strip())
