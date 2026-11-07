"""바구니 — 큰 표를 **식별자의 해시로 나눠** 디스크에 두고 바구니마다 다시 읽는다.

변환(`convert.transform`)과 비교(`diff.delta`)가 쓴다. 한 식별자의 줄은 다 한 바구니에 든다 —
모으기 · 견주기는 식별자 안에서만 일어나므로 바구니마다 따로 해도 결과가 같다. 바구니의 결과는
원래 차례(첫 행 · 파일 차례)로 다시 잇는다(`merged`). 메모리에는 바구니 하나만 든다.

해시는 `zlib.crc32` 다 — 파이썬의 `hash()` 는 프로세스마다 달라 같은 입력이 다른 바구니로 간다.
바구니 파일은 **같은 프로세스가 쓰고 곧 읽는 임시 파일**이라 pickle 로 1,000줄씩 묶어 쓴다 —
줄마다 JSON 으로 쓰고 읽으면 비교 한 번이 두 배 넘게 걸렸다(50만 행에 11초 → 26초).
"""

from __future__ import annotations

import heapq
import pickle
import shutil
import zlib
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

#: 바구니 하나에 드는 줄 — 이만큼을 메모리에 든다(모델 타입으로 약 200MB). 이보다 작은 표는
#: 바구니 하나 — 나누지 않는다.
BUCKET_ROWS = 100_000


def count_for(rows: int, per: int = BUCKET_ROWS) -> int:
    """줄 `rows` 를 바구니마다 `per` 줄 안으로 담을 바구니 수."""
    return max(1, -(-rows // max(1, per)))


def bucket_of(text: str, count: int) -> int:
    return zlib.crc32(text.encode("utf-8")) % count if count > 1 else 0


class Spill:
    """바구니 `count` 개 — 바구니마다 줄을 넣은 차례로 쌓는 파일. `folder` 는 다 쓰면 지운다
    (`drop`)."""

    #: 바구니마다 이만큼 모아 한 번에 쓴다.
    BATCH = 1000

    def __init__(self, folder: Path, count: int) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.folder, self.count = folder, count
        self._out = [self._path(one).open("wb") for one in range(count)]
        self._held: list[list[Any]] = [[] for _ in range(count)]

    def _path(self, bucket: int) -> Path:
        return self.folder / f"{bucket:04d}.bin"

    def put(self, bucket: int, item: Any) -> None:
        held = self._held[bucket]
        held.append(item)
        if len(held) >= self.BATCH:
            pickle.dump(held, self._out[bucket], protocol=pickle.HIGHEST_PROTOCOL)
            self._held[bucket] = []

    def close(self) -> None:
        for bucket, handle in enumerate(self._out):
            if handle.closed:
                continue
            if self._held[bucket]:
                pickle.dump(self._held[bucket], handle, protocol=pickle.HIGHEST_PROTOCOL)
                self._held[bucket] = []
            handle.close()

    def read(self, bucket: int) -> Iterator[Any]:
        """바구니 하나를 넣은 차례로 — 닫은 뒤에 읽는다."""
        with self._path(bucket).open("rb") as handle:
            while True:
                try:
                    batch = pickle.load(handle)
                except EOFError:
                    return
                yield from batch

    def drop(self) -> None:
        self.close()
        shutil.rmtree(self.folder, ignore_errors=True)


def merged(spill: Spill, key: Callable[[Any], Any]) -> Iterator[Any]:
    """바구니들을 `key` 차례로 잇는다 — 바구니 안이 이미 그 차례여야 한다."""
    streams: Iterable[Iterator[Any]] = (spill.read(one) for one in range(spill.count))
    return heapq.merge(*streams, key=key)
