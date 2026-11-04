"""긴 목록을 **나눠 묻는다** — 한 문장의 바인드 변수는 65,535개까지다.

`column.in_(ids)` 는 원소마다 바인드 변수 하나를 쓴다. 10만 행짜리 일괄 입력이나 인기 객체를
가리키는 10만 건을 한 번에 넘기면 질의가 **보내지지도 않고** 실패한다(실측: 「number of
parameters must be between 0 and 65535」). 그 오류는 화면에 「서버 오류」 로만 뜬다.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from typing import Any

from sqlalchemy import ColumnElement, any_, bindparam
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import QueryableAttribute

#: 한 번에 넘기는 원소 수 — 한도보다 넉넉히 작게(한 문장에 목록이 둘 셋 실려도 안전하게).
CHUNK = 10_000


def chunks[T](items: Iterable[T], size: int = CHUNK) -> Iterator[list[T]]:
    """`items` 를 `size` 개씩. 빈 목록이면 아무것도 안 낸다(빈 `IN ()` 를 만들지 않는다)."""
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def any_of(
    column: ColumnElement[Any] | QueryableAttribute[Any], values: Sequence[Any]
) -> ColumnElement[bool]:
    """`column IN (…)` 대신 `column = ANY(:배열)` — 목록이 **바인드 하나**다.

    `in_` 은 원소마다 자리표(`%(key_1_1)s`)를 펼쳐 만 개짜리 목록이면 문장이 수백 KB 가 된다.
    psycopg 는 4KB 넘는 문장을 캐시하지 않아 **보낼 때마다 문장 전체를 다시 쪼갠다** — 일괄
    입력 계획이 식별자 5만 개를 물을 때 그 쪼개기가 DB 시간만큼 들었다(0.56초 → 0.28초,
    2026-10-09 실측). 색인은 그대로 탄다(btree 의 `= ANY`).
    """
    element = column if isinstance(column, ColumnElement) else column.expression
    return element == any_(bindparam(None, list(values), type_=ARRAY(element.type)))
