"""Parquet 읽기 — 파일 원천(`format: parquet`)과 보내 주는 원천(본문이 Parquet)이 함께 쓴다.

- **묶음(row group · batch)으로 흘려 읽는다** — 수백만 행 파일도 한 번에 행을 다 펴지 않는다
  (파일 원천은 받은 행을 `Records` 에 넣어 넘치면 디스크로 보낸다).
- 값은 JSON 이 담는 꼴로 바꾼다 — 다른 원천(REST · 파일)의 행과 같은 길(`records_of`)을
  타게: 시각 · 날짜 · 시간은 ISO 글, 십진수(decimal)는 **글 그대로**(float 로 바꾸면 자릿수를
  잃는다), 바이트는 base64, NaN 은 빈 값. 구조체(struct)는 객체, 목록(list)은 목록 — 자리를
  점으로 잇고(`spec.weight`) 목록은 펴기(`explode`)로 읽는다.
- 맨 앞 · 맨 끝의 `PAR1` 로 알아본다(`looks_like`).
"""

from __future__ import annotations

import base64
import io
import math
from collections.abc import Iterator
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from relay_core.adapters.rest import SourceError

MAGIC = b"PAR1"
#: 한 번에 펴는 행 — 묶음 하나의 메모리를 묶는다.
BATCH = 10_000


def looks_like(raw: bytes) -> bool:
    return len(raw) >= 12 and raw[:4] == MAGIC and raw[-4:] == MAGIC


def plain(value: Any) -> Any:
    """Parquet 의 값 → JSON 이 담는 값."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode()
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        # map 형은 (키, 값) 쌍의 목록으로 온다 — 객체로 편다.
        if value and all(isinstance(one, tuple) and len(one) == 2 for one in value):
            return {str(k): plain(v) for k, v in value}
        return [plain(one) for one in value]
    return str(value)


def _file(raw: bytes) -> Any:
    try:
        import pyarrow.parquet as pq
    except ImportError as failure:  # pragma: no cover - 이미지에는 있다
        raise SourceError("Parquet 을 읽을 pyarrow 가 없습니다.") from failure
    if not looks_like(raw):
        raise SourceError("Parquet 파일이 아닙니다(앞뒤의 PAR1 이 없음).")
    try:
        return pq.ParquetFile(io.BytesIO(raw))
    except Exception as failure:  # pyarrow 의 오류는 여러 갈래다
        raise SourceError(f"Parquet 을 읽지 못했습니다: {failure}") from None


def count(raw: bytes) -> int:
    """행 수 — 꼬리(footer)의 메타데이터만 읽는다."""
    return int(_file(raw).metadata.num_rows)


def columns(raw: bytes) -> list[str]:
    return [str(name) for name in _file(raw).schema_arrow.names]


def rows(raw: bytes, *, limit: int | None = None) -> Iterator[dict[str, Any]]:
    """행들을 차례로 — `limit` 이면 그만큼만 읽고 멈춘다(탐색 · 시험)."""
    book = _file(raw)
    seen = 0
    size = min(BATCH, limit) if limit else BATCH
    try:
        for batch in book.iter_batches(batch_size=size):
            for row in batch.to_pylist():
                yield {str(k): plain(v) for k, v in row.items()}
                seen += 1
                if limit and seen >= limit:
                    return
    except SourceError:
        raise
    except Exception as failure:
        raise SourceError(f"Parquet 을 읽다 멈췄습니다({seen}행 뒤): {failure}") from None
