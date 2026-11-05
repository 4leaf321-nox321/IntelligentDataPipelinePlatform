"""DB 원천 — 원천 시스템의 **뷰 · 표를 읽기만** 한다. **설치마다 꺼져 있다**
(`RELAY_ALLOW_SQL`).

    {"kind": "sql", "dsn_env": "MES_DB_URL", "table": "mes.v_lot_summary", "key": "lot_id",
     "modified_column": "updated_at", "columns": "*"}

- 접속 문자열은 **비밀**이다 — 정의에는 환경 변수 이름(`dsn_env`)만 적는다. 값은
  SQLAlchemy URL —
  Oracle `oracle+oracledb://<계정>:<비번>@<호스트>:1521/?service_name=<서비스>`
  (thin — Oracle 클라이언트가 필요 없다),
  MSSQL `mssql+pymssql://<계정>:<비번>@<호스트>:1433/<DB>`(FreeTDS 가 바퀴에 들었다),
  PostgreSQL `postgresql+psycopg://…`. 드라이버는 서버 이미지에 들어 있다.
- 칸 이름은 큰 · 작은 글자를 가리지 않고 찾는다(Oracle 은 이름이 작은 글자로 온다). 표 이름은
  `표` · `스키마.표` · `DB.스키마.표`(MSSQL).
- **읽기 전용** — `table`(이름 규칙을 본다) 또는 `query`(`SELECT` · `WITH` 로 시작, `;` 없음).
  원천 쪽에도 **읽기 전용 계정**을 받는다. 트랜잭션은 끝에 되돌린다(아무것도 안 쓴다).
- 증분은 `modified_column > :since`. 큰 표는 서버 쪽 커서로 `page_size` 씩 흘려 받는다.
- 바깥 DB 를 바로 읽는 일은 사내 보안 승인이 필요하다 — 그래서 기본은 꺼져 있다(ADR 0001 ·
  개발계획).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from relay_core.adapters.base import Fetched
from relay_core.adapters.explore import suggest
from relay_core.adapters.rest import SourceError, truthy
from relay_core.records import SourceRecord

_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*(\.[A-Za-z_][A-Za-z0-9_$#]*){0,2}$")
_READ = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SqlSource:
    dsn_env: str
    key: str
    columns: Mapping[str, str] | str = "*"
    table: str = ""
    query: str = ""
    """`table` 대신 — `SELECT …`(증분이면 `:since` 를 쓴다)."""
    modified_column: str | None = None
    deleted: str | None = None
    page_size: int = 1000
    dsn: str = ""
    """풀린 접속 문자열 — **정의에 적지 않는다**(`definition` 이 `dsn_env` 에서 채운다)."""


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class _Row(dict[str, Any]):
    """칸 이름을 **큰 · 작은 글자 가리지 않고** 찾는다 — Oracle 은 SQLAlchemy 를 거치며 이름이
    작은 글자로 오고(`LOT_ID` → `lot_id`), 정의는 대개 큰 글자로 적는다."""

    def get(self, name: Any, default: Any = None) -> Any:
        if name in self:
            return self[name]
        lowered = str(name).lower()
        for key, value in self.items():
            if str(key).lower() == lowered:
                return value
        return default


def _since_value(since: str) -> Any:
    """DB 가 시각으로 견주게 — ISO 글이면 datetime 으로(글로 견주면 Postgres 가 거절한다)."""
    try:
        return datetime.fromisoformat(since.replace("Z", "+00:00"))
    except ValueError:
        return since


class SqlAdapter:
    def __init__(self, source: SqlSource, *, engine: Engine | None = None) -> None:
        if not source.table and not source.query:
            raise SourceError("table 이나 query 하나를 적어야 합니다.")
        if source.table and not _NAME.match(source.table):
            raise SourceError(f"표 이름이 맞지 않습니다: {source.table!r}")
        if source.query and (not _READ.match(source.query) or ";" in source.query):
            raise SourceError(
                "query 는 SELECT · WITH 로 시작하는 한 문장이어야 합니다(읽기 전용)."
            )
        for name in (source.key, source.modified_column, source.deleted):
            if name and not _NAME.match(name):
                raise SourceError(f"칸 이름이 맞지 않습니다: {name!r}")
        self.source = source
        self._engine = engine

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            if not self.source.dsn:
                raise SourceError(
                    f"접속 문자열이 없습니다 — 환경 변수 {self.source.dsn_env} 를 확인하세요."
                )
            self._engine = create_engine(self.source.dsn, pool_pre_ping=True)
        return self._engine

    def _statement(
        self, since: str | None, limit: int | None = None
    ) -> tuple[Any, dict[str, Any]]:
        source = self.source
        params: dict[str, Any] = {}
        if source.query:
            sql = source.query
            if ":since" in sql:
                params["since"] = _since_value(since) if since else None
        else:
            wanted = (
                "*"
                if isinstance(source.columns, str)
                else ", ".join(
                    sorted(
                        {source.key, *source.columns.values()}
                        | {one for one in (source.deleted, source.modified_column) if one}
                    )
                )
            )
            sql = f"SELECT {wanted} FROM {source.table}"
            if since and source.modified_column:
                sql += f" WHERE {source.modified_column} > :since"
                params["since"] = _since_value(since)
            sql += f" ORDER BY {source.key}"
        return text(sql), params

    def _rows(self, since: str | None, limit: int | None = None) -> list[dict[str, Any]]:
        statement, params = self._statement(since)
        out: list[dict[str, Any]] = []
        try:
            with self.engine.connect() as conn:
                result = conn.execution_options(stream_results=True).execute(statement, params)
                while True:
                    chunk = result.mappings().fetchmany(limit or self.source.page_size)
                    out.extend(dict(one) for one in chunk)
                    if not chunk or (limit and len(out) >= limit):
                        break
                conn.rollback()
        except SQLAlchemyError as failure:
            raise SourceError(
                f"DB 읽기가 실패했습니다: {str(failure).splitlines()[0][:300]}"
            ) from None
        return out[:limit] if limit else out

    def _record(self, raw: dict[str, Any]) -> SourceRecord:
        source = self.source
        row = _Row(raw)
        key = _cell(row.get(source.key)).strip()
        if not key:
            raise SourceError(f"식별자({source.key})가 빈 행이 있습니다.")
        deleted = bool(source.deleted) and truthy(row.get(source.deleted or ""))
        columns: Mapping[str, str] = (
            {str(name): str(name) for name in raw}
            if isinstance(source.columns, str)
            else source.columns
        )
        values = (
            {} if deleted else {out: _cell(row.get(path)) for out, path in columns.items()}
        )
        updated = _cell(row.get(source.modified_column)) if source.modified_column else ""
        return SourceRecord(
            key=key, values=values, updated_at=updated or None, deleted=deleted
        )

    def fetch(self, since: str | None, *, limit: int | None = None) -> Fetched:
        """`limit` 이면 앞 그만큼만(시험용 — `complete=False`)."""
        if limit:
            rows = self._rows(since, limit=limit + 1)
            if len(rows) > limit:
                return Fetched(
                    records=[self._record(row) for row in rows[:limit]], complete=False
                )
        else:
            rows = self._rows(since)
        records = [self._record(row) for row in rows]
        marks = [one.updated_at for one in records if one.updated_at]
        incremental = bool(since) and (
            bool(self.source.modified_column) or ":since" in self.source.query
        )
        return Fetched(
            records=records,
            watermark=max(marks) if marks else since,
            complete=True,
            full=not incremental,
        )

    def probe(self) -> str:
        rows = self._rows(None, limit=1)
        first = _cell(rows[0].get(self.source.key)) if rows else "(빈 표)"
        return f"읽기 정상 — 첫 행 식별자 {first}"

    def discover(self) -> dict[str, Any]:
        info: dict[str, Any] = {}
        if self.source.table:
            schema, _, name = self.source.table.rpartition(".")
            try:
                found = inspect(self.engine).get_columns(name, schema=schema or None)
            except SQLAlchemyError as failure:
                raise SourceError(
                    f"표를 못 읽었습니다: {str(failure).splitlines()[0][:200]}"
                ) from None
            info["table_columns"] = [
                {"name": c["name"], "type": str(c["type"])} for c in found
            ]
        rows = self._rows(None, limit=5)
        return {**info, **suggest([{k: _cell(v) for k, v in row.items()} for row in rows])}
