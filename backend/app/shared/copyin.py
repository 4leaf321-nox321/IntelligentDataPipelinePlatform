"""행 여럿을 **COPY 한 문장으로** 넣는다 — 일괄 입력의 쓰기 길.

ORM 의 flush 는 새 행을 1,000줄짜리 `INSERT … VALUES (…), (…) RETURNING …` 으로 묶어 보낸다.
그 문장은 4KB 를 훌쩍 넘어 psycopg 가 캐시하지 못하고, **덩어리마다 문장 전체를 다시
쪼갠다**(`_query2pg_nocache`) — 5만 줄 적용에서 그것만 수 초였다. 한 줄씩 보내는
executemany 는 그 비용이 없지만, `objects` 의 참조 색인 트리거가 **문장마다** 돌아(문장 단위
트리거, `refindex.py`) 오히려 더 느렸다. 객체 5만 줄을 넣는 데(2026-10-09, 개발 DB 실측):

    ORM flush        15.7초      감사 5만 줄  8.2초
    executemany      20.9초                   2.6초
    COPY              9.6초                   0.9초

COPY 는 ORM 을 거치지 않는다 — 그래서 부르는 쪽이 알아야 할 것:

- 파이썬 쪽 기본값(`default=uuid.uuid4` 같은)이 안 붙는다. 넘기지 않은 칸은 **DB 기본값**
  (server_default · identity)만 받는다 — DB 기본값 없이 파이썬 기본값만 있는 칸을 빼면
  거절한다(조용히 NULL 이 들어가면 ORM 으로 넣은 줄과 모양이 갈린다).
- JSON 칸의 `None` 은 ORM 과 같게 JSON `null` 이다(`none_as_null` 을 켠 칸만 SQL NULL).
- 세션의 identity map 에 안 든다. 행은 **곧바로** DB 에 들어가므로 같은 트랜잭션의 질의는 본다.
- ORM 이벤트는 안 돈다. 표의 트리거는 돈다(COPY 도 INSERT 트리거를 부른다 — 문장 단위
  트리거는 COPY 한 번에 한 번).
- 세션에 아직 안 쓴(flush 안 한) 행을 가리키는 외래키는 서지 않는다 — 부르는 쪽이 먼저
  flush 한다.

## 고친 줄은 덩어리 UPDATE 로(`update_rows`)

flush 는 고친 객체마다 `UPDATE … WHERE id = …` 를 하나씩 보낸다(psycopg 가 파이프라인으로
묶어도 문장은 줄마다다). `objects` 의 참조 색인 트리거는 **문장 단위**라 그것이 줄마다 돌았다 —
5만 줄 한 칸 고치기 적용 41초 중 UPDATE 가 25초, 그중 트리거가 11초였다(2026-10-10, 개발 DB
실측). `update_rows` 는 같은 줄들을 `UPDATE … FROM unnest(…)` 한 문장으로 쓴다(덩어리마다
트리거 한 번).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb
from sqlalchemy import JSON, Column, Table, text
from sqlalchemy.orm import Session, attributes
from sqlalchemy.sql import ClauseElement


def copy_rows(db: Session, table: Table, rows: Sequence[Mapping[str, Any]]) -> int:
    """`rows`(칸 이름 → 값, **모두 같은 칸**)를 `table` 에 COPY 로 넣는다. 넣은 줄 수.

    세션의 트랜잭션(세이브포인트 포함) 안에서 돈다 — 롤백하면 함께 사라진다."""
    if not rows:
        return 0
    names = list(rows[0])
    _require_defaults(table, names)
    as_json = [_json_null(table, name) for name in names]
    driver = cast("psycopg.Connection[Any]", db.connection().connection.driver_connection)
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(*([table.schema] if table.schema else []), table.name),
        sql.SQL(", ").join(sql.Identifier(table.c[name].name) for name in names),
    )
    with driver.cursor() as cursor, cursor.copy(statement) as copy:
        for row in rows:
            copy.write_row(
                [
                    row[name]
                    if json_null is None or (row[name] is None and json_null)
                    else Jsonb(row[name])
                    for name, json_null in zip(names, as_json, strict=True)
                ]
            )
    return len(rows)


def _json_null(table: Table, name: str) -> bool | None:
    """JSON 칸이면 「`None` 을 SQL NULL 로 넣나」(`none_as_null`), JSON 칸이 아니면 None."""
    kind = table.c[name].type
    return bool(kind.none_as_null) if isinstance(kind, JSON) else None


def _require_defaults(table: Table, names: list[str]) -> None:
    """빠진 칸이 **DB 기본값으로 ORM 과 같아지나** — 파이썬 기본값만 있는 칸이 빠지면 거절."""
    given = set(names)
    unknown = sorted(given - set(table.c.keys()))
    if unknown:
        raise ValueError(f"{table.name} 에 없는 칸입니다: {', '.join(unknown)}")
    for column in table.c:
        if column.key in given:
            continue
        if column.server_default is not None or column.identity is not None:
            continue
        if column.default is None and column.nullable:
            continue
        raise ValueError(
            f"{table.name}.{column.key} 는 DB 기본값이 없습니다 — 값을 넘겨야 합니다"
        )


#: 고친 칸 자리에 「onupdate 식(now())으로」 를 적는 표식 — 값이 아니라 SQL 식이 든 칸이다.
_BY_ONUPDATE = object()

#: 바뀐 칸이 같은 줄끼리 — (표, ((속성, SQL 식인가), …)).
_Shape = tuple[Table, tuple[tuple[str, bool], ...]]


def update_rows(db: Session, rows: Sequence[object]) -> int:
    """고친 ORM 객체들을 **바뀐 칸만, 덩어리 UPDATE 한 문장으로** 쓴다 — 쓴 줄 수.

    고를 칸은 flush 와 같은 판정이다: 객체를 읽은 뒤 손댄 칸(`committed_state`) 가운데 값이
    다른 것만 — 같은 값을 다시 넣은 칸은 안 쓰고, 그런 칸뿐이면 그 줄은 아예 안 쓴다(바뀐 때도
    그대로다). `onupdate` 칸(`updated_at`)은 flush 처럼 그 식으로 함께 고친다. 바뀐 칸이 같은
    줄끼리 한 문장이다(보통 한 덩어리가 통째로 한 문장).

    쓴 뒤에는 그 값을 객체에 **「쓴 것」 으로** 맞춘다(`set_committed_value`, 바뀐 때는 DB 가
    돌려준 값) — 다음 flush 가 같은 줄을 또 보내지 않는다. 맡지 않는 객체(새로 만든 것 ·
    onupdate 가 아닌 칸에 SQL 식을 담은 것 · 파이썬 함수로 onupdate 하는 표)는 그대로 두므로
    flush 가 예전처럼 보낸다.

    ⚠️ ORM 의 갱신 이벤트(`before_update` 등)는 안 돈다 — `objects` · `object_relations` 에는
       그런 이벤트가 없다. 걸게 되면 여기도 함께 본다.
    """
    dialect = db.get_bind().dialect
    known: dict[Table, dict[str, ClauseElement]] = {}
    groups: dict[_Shape, list[tuple[Any, dict[str, Any]]]] = {}
    for one in {id(one): one for one in rows}.values():  # 같은 객체가 두 번 와도 한 줄
        found = _pending(one, dialect, known)
        if found is None:
            continue
        table, changed = found
        shape = tuple(sorted((key, value is _BY_ONUPDATE) for key, value in changed.items()))
        groups.setdefault((table, shape), []).append((one, changed))
    done = 0
    for (table, shape), members in groups.items():
        keys = [key for key, by_sql in shape if not by_sql]
        done += _update_group(db, table, keys, members, known[table])
    return done


def _pending(
    one: object, dialect: Any, known: dict[Table, dict[str, ClauseElement]]
) -> tuple[Table, dict[str, Any]] | None:
    """이 객체가 쓸 칸(속성 → 값) — flush 의 `_collect_update_commands` 와 같은 판정. 쓸 것이
    없거나 맡을 수 없는 객체면 None. `known` 은 표마다 onupdate 식(한 번만 본다)."""
    state = attributes.instance_state(one)
    if not state.persistent or not state.committed_state:
        return None
    table = cast("Table", state.mapper.local_table)
    if table not in known:
        known[table] = _onupdate_of(table, dialect)
    later = known[table]
    if not later or len(table.primary_key.columns) != 1:
        return None
    changed: dict[str, Any] = {}
    for key, before in list(state.committed_state.items()):
        if key not in state.mapper.column_attrs:
            continue
        if _column(state.mapper, key).primary_key:
            return None  # 기본키를 바꾸는 줄은 flush 가(그 줄을 옛 키로 찾는다)
        value: Any = state.dict.get(key)
        if hasattr(value, "__clause_element__"):
            value = value.__clause_element__()
        if isinstance(value, ClauseElement):
            # SQL 식은 onupdate 칸에 그 식 그대로일 때만(별칭이 「바뀐 때」 만 고친 것).
            expected = later.get(_column(state.mapper, key).name)
            if expected is None or not expected.compare(value):
                return None
            changed[key] = _BY_ONUPDATE
        elif state.manager[key].impl.is_equal(value, before) is not True:
            changed[key] = value
    return (table, changed) if changed else None


def _column(mapper: Any, key: str) -> Column[Any]:
    return cast("Column[Any]", mapper.column_attrs[key].columns[0])


def _onupdate_of(table: Table, dialect: Any) -> dict[str, ClauseElement]:
    """표의 onupdate 칸 → 그 SQL 식. **맡을 수 없는 표면 빈 것**(onupdate 가 없거나, 파이썬
    함수 · 바인드 값이라 문장 하나에 못 싣는다) — 그러면 flush 에 맡긴다."""
    out: dict[str, ClauseElement] = {}
    for column in table.c:
        if column.onupdate is None:
            continue
        expression = getattr(column.onupdate, "arg", None)
        if not isinstance(expression, ClauseElement):
            return {}
        if expression.compile(dialect=dialect).params:
            return {}
        out[column.name] = expression
    return out


def _update_group(
    db: Session,
    table: Table,
    keys: list[str],
    members: list[tuple[Any, dict[str, Any]]],
    onupdate: dict[str, ClauseElement],
) -> int:
    """바뀐 칸이 같은 줄들 — `UPDATE t SET … FROM unnest(id[], 칸[]…) v WHERE t.id = v.id`."""
    dialect = db.get_bind().dialect
    quote = dialect.identifier_preparer.quote
    mapper = attributes.instance_state(members[0][0]).mapper
    (pk,) = table.primary_key.columns
    columns = [_column(mapper, key) for key in keys]
    later = {name: str(one.compile(dialect=dialect)) for name, one in onupdate.items()}
    params: dict[str, Any] = {"p0": [_id_of(one) for one, _ in members]}
    casts = [f"CAST(:p0 AS {pk.type.compile(dialect=dialect)}[])"]
    for i, (key, column) in enumerate(zip(keys, columns, strict=True), start=1):
        as_json = _json_null(table, column.name)
        params[f"p{i}"] = [
            changed[key]
            if as_json is None or (changed[key] is None and as_json)
            else Jsonb(changed[key])
            for _one, changed in members
        ]
        casts.append(f"CAST(:p{i} AS {column.type.compile(dialect=dialect)}[])")
    names = [quote(pk.name), *(quote(column.name) for column in columns)]
    sets = [f"{name} = v.{name}" for name in names[1:]]
    sets += [
        f"{quote(name)} = {expression}"
        for name, expression in later.items()
        if name not in {column.name for column in columns}
    ]
    back = [name for name in later if name not in {column.name for column in columns}]
    statement = (
        f"UPDATE {quote(table.name)} AS t SET {', '.join(sets)} "
        f"FROM unnest({', '.join(casts)}) AS v({', '.join(names)}) "
        f"WHERE t.{names[0]} = v.{names[0]} "
        f"RETURNING t.{names[0]}{''.join(f', t.{quote(name)}' for name in back)}"
    )
    by_id = {_id_of(one): (one, changed) for one, changed in members}
    done = 0
    for found in db.execute(text(statement), params):
        one, changed = by_id[found[0]]
        for key in keys:
            attributes.set_committed_value(one, key, changed[key])
        for name, value in zip(back, found[1:], strict=True):
            attributes.set_committed_value(one, _key_of(mapper, name), value)
        done += 1
    return done


def _id_of(one: object) -> Any:
    """읽어 온 객체의 기본키 값(한 칸)."""
    identity = attributes.instance_state(one).identity
    assert identity is not None  # 읽어 온(persistent) 객체만 온다
    return identity[0]


def _key_of(mapper: Any, column_name: str) -> str:
    """칸 이름 → 매핑된 속성 이름(둘은 대개 같다)."""
    for key, prop in mapper.column_attrs.items():
        if prop.columns[0].name == column_name:
            return str(key)
    raise KeyError(column_name)  # pragma: no cover - 표의 칸은 다 매핑돼 있다
