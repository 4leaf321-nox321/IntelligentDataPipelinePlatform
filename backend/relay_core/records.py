"""원천의 행 한 줄 — 어댑터가 내고 미러가 쥔다."""

from __future__ import annotations

import heapq
import pickle
import shutil
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import overload


@dataclass(frozen=True)
class SourceRecord:
    """원천의 **평평한** 행 하나(SP 연동 지침서 §3 의 모양).

    - `key` 는 원천의 **안 바뀌는** 식별자다. 이름으로 맞추지 않는다.
    - `values` 는 열 이름 → 글. 중첩은 어댑터가 펴서 온다(`owner.name` → `owner_name`).
    - `deleted` 면 원천에서 지운 것 — 미러에서 빠지고, 다음 출력 비교에서 무덤이 된다.
    """

    key: str
    values: Mapping[str, str] = field(default_factory=dict)
    updated_at: str | None = None
    deleted: bool = False


#: 받은 행을 메모리에 두는 상한 — 넘으면 키 차례로 정렬해 디스크에 쓴다(`Records`).
KEEP = 100_000
#: 넘친 행을 쓸 폴더 — 실행이 받는 동안만 정한다(`spill_to`). 없으면 넘쳐도 메모리에 둔다
#: (시험 · 정의 시험처럼 앞부분만 받는 자리).
_SPILL: ContextVar[Path | None] = ContextVar("relay_records_spill", default=None)


@contextmanager
def spill_to(folder: Path) -> Iterator[None]:
    """이 안에서 만든 `Records` 는 넘치면 `folder` 에 쓴다."""
    token = _SPILL.set(folder)
    try:
        yield
    finally:
        _SPILL.reset(token)


class Records:
    """어댑터가 받은 행 — `KEEP` 줄까지는 메모리에, 넘으면 **키 차례로 정렬한 묶음**(pickle,
    1,000줄씩)을 디스크에 쓴다. 원천이 100만 행이면 그 목록만 1.2GB 였다(2026-11-06 실측).

    넘친 뒤 `iter` 의 차례는 **키 차례**다(받은 차례가 아니다) — 미러에 넣는 일
    (`mirror.Merge`)은 키 차례로 하고, 같은 키의 행은 받은 차례(`seq`)를 지킨다. 다 쓰면
    `close`."""

    def __init__(self, keep: int | None = None) -> None:
        self._keep = keep or KEEP
        self._mem: list[tuple[str, int, SourceRecord]] = []
        self._runs: list[Path] = []
        self._seq = 0
        self._folder = _SPILL.get()
        self.latest: str | None = None
        """받은 행의 `updated_at` 중 가장 늦은 것 — 워터마크를 행 목록 없이 정한다."""

    def append(self, one: SourceRecord) -> None:
        self._mem.append((one.key, self._seq, one))
        self._seq += 1
        if one.updated_at and (self.latest is None or one.updated_at > self.latest):
            self.latest = one.updated_at
        if self._folder is not None and len(self._mem) >= self._keep:
            self._spill()

    def extend(self, many: Iterable[SourceRecord]) -> None:
        for one in many:
            self.append(one)

    def _spill(self) -> None:
        assert self._folder is not None
        self._folder.mkdir(parents=True, exist_ok=True)
        file = self._folder / f"run-{len(self._runs):05d}.pickle"
        self._mem.sort(key=lambda item: (item[0], item[1]))
        with file.open("wb") as handle:
            for at in range(0, len(self._mem), 1000):
                pickle.dump(
                    self._mem[at : at + 1000], handle, protocol=pickle.HIGHEST_PROTOCOL
                )
        self._runs.append(file)
        self._mem = []

    @property
    def spilled(self) -> bool:
        return bool(self._runs)

    def __len__(self) -> int:
        return self._seq

    def _read(self, file: Path) -> Iterator[tuple[str, int, SourceRecord]]:
        with file.open("rb") as handle:
            while True:
                try:
                    yield from pickle.load(handle)
                except EOFError:
                    return

    def in_key_order(self) -> Iterator[SourceRecord]:
        """키 차례 — 같은 키는 받은 차례로."""
        streams = [self._read(one) for one in self._runs]
        streams.append(iter(sorted(self._mem, key=lambda item: (item[0], item[1]))))
        for _key, _seq, one in heapq.merge(*streams, key=lambda item: (item[0], item[1])):
            yield one

    def __iter__(self) -> Iterator[SourceRecord]:
        if self._runs:
            return self.in_key_order()
        return (one for _key, _seq, one in self._mem)

    @overload
    def __getitem__(self, index: int) -> SourceRecord: ...

    @overload
    def __getitem__(self, index: slice) -> list[SourceRecord]: ...

    def __getitem__(self, index: int | slice) -> SourceRecord | list[SourceRecord]:
        return list(self)[index]

    def close(self) -> None:
        """넘친 묶음을 지운다 — 미러에 넣은 뒤에."""
        for one in self._runs:
            one.unlink(missing_ok=True)
        if self._folder is not None and self._folder.is_dir():
            shutil.rmtree(self._folder, ignore_errors=True)
        self._runs = []
