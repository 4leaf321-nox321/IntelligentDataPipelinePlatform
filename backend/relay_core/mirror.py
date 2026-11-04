"""미러 — 원천 키마다 **최신 행** 하나.

레시피(`sp-table/1`)는 여러 원천 행을 객체 하나로 모은다(과제 하나가 모델 행 수십 개에 걸쳐
나온다). 그래서 바뀐 행만 변환하면 모은 값이 일부만 남는다 — **늘 미러 전체를 다시 변환하고**,
줄이는 일은 변환이 끝난 출력끼리의 비교(`diff`)가 한다. 변환은 같은 입력이면 같은 결과라 비교가
맞는다.

여기는 메모리의 모양만 안다. 어디에 저장할지(DB · 파일)는 부르는 쪽이 정한다(`to_rows` ·
`from_rows`).
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relay_core.adapters.base import Fetched
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
        columns = self.columns()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for one in self.records():
                writer.writerow([one.values.get(column, "") for column in columns])
        return path

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
