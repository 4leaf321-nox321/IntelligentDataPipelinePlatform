"""OData 원천 — v4 · v2 의 봉투 · 쪽 넘김 · 증분 · 날짜를 안다.

    v4   {"value": [...], "@odata.nextLink": "..."}            날짜 2026-11-05T01:23:45Z
    v2   {"d": {"results": [...], "__next": "..."}}            날짜 /Date(1730764800000)/

- **쪽 넘김은 서버가 주는 다음 주소(`nextLink` · `__next`)를 그대로 따른다.** 서버가 안 주면
  `$top` · `$skip` 으로 넘긴다(덜 찬 쪽에서 끝).
- **증분은 변경 시각 칸에 `$filter` 를 건다** — `modified_field gt <since>`(v4 는 맨 값,
  v2 는 `datetime'…'`). 경계가 「이상」 이어야 안전하면 `since_op: "ge"`(같은 행이 한 번 더 올
  뿐이다).
- 펼친 탐색 속성(`$expand`)은 점으로 잇는다 — `Task.Project.Name`. v2 의 `results` ·
  `__metadata` · `__deferred` 는 여기서 걷어낸다.
- OData 는 지운 행을 대개 목록에서 **뺀다** — 삭제 칸(`deleted`)이 없으면 전량을 받는 날의
  부재 대조(`Mirror.apply`, `full`)로 사라진 것을 안다. 그래서 증분 `full_every` 번마다 한 번
  전량을 받는다(실행기가 센다).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from relay_core.adapters.base import Fetched
from relay_core.adapters.rest import Auth, SourceError, as_text, get_json, truthy, value_at
from relay_core.records import SourceRecord

_V2_DATE = re.compile(r"^/Date\((-?\d+)([+-]\d{4})?\)/$")


def _clean(value: Any) -> Any:
    """v2 의 껍질(`results` · `__metadata` · `__deferred`)을 벗기고 `/Date()/` 를 ISO 로."""
    if isinstance(value, dict):
        if "results" in value and isinstance(value["results"], list):
            return [_clean(one) for one in value["results"]]
        return {
            key: _clean(one)
            for key, one in value.items()
            if key != "__metadata" and not (isinstance(one, dict) and "__deferred" in one)
        }
    if isinstance(value, list):
        return [_clean(one) for one in value]
    if isinstance(value, str):
        found = _V2_DATE.match(value)
        if found:
            moment = datetime.fromtimestamp(int(found.group(1)) / 1000, tz=UTC)
            return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return value


@dataclass(frozen=True)
class ODataSource:
    service_url: str
    """서비스 뿌리 — `https://plm.example.com/odata/v4/PlmService`."""
    entity_set: str
    key: str
    columns: Mapping[str, str]
    version: str = "auto"
    """4 · 2 · auto(응답의 봉투로 가른다)."""
    expand: str = ""
    select: str = ""
    filter: str = ""
    """늘 거는 조건(예: `PlantCode eq 'P1'`). 증분 조건과 `and` 로 묶인다."""
    orderby: str = ""
    """비우면 식별자 순 — 쪽 넘김 중에 순서가 바뀌면 행이 빠지거나 겹친다."""
    modified_field: str | None = None
    since_op: str = "gt"
    deleted: str | None = None
    page_size: int = 500
    full_every: int = 7
    """증분 몇 번에 한 번 전량을 받나(부재 대조) — 삭제 칸이 없는 원천을 위해. 0 이면 안
    한다. 횟수는 실행기가 작업 폴더에 센다(`runner`)."""
    params: Mapping[str, str] = field(default_factory=dict)
    auth: Auth = field(default_factory=Auth)
    timeout: float = 60.0
    retries: int = 3
    max_pages: int = 100_000


class ODataAdapter:
    def __init__(
        self,
        source: ODataSource,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.source = source
        self._client = client or httpx.Client(timeout=source.timeout)
        self._sleep = sleep

    def _since_literal(self, since: str, version: str) -> str:
        if version == "2":
            return f"datetime'{since.rstrip('Z')}'"
        return since

    def _first_params(self, since: str | None, version: str) -> dict[str, Any]:
        source = self.source
        conditions = [f"({source.filter})"] if source.filter else []
        if since and source.modified_field:
            literal = self._since_literal(since, version)
            conditions.append(f"{source.modified_field} {source.since_op} {literal}")
        params: dict[str, Any] = dict(source.params)
        params["$orderby"] = source.orderby or source.key.replace(".", "/")
        params["$top"] = source.page_size
        if conditions:
            params["$filter"] = " and ".join(conditions)
        if source.select:
            params["$select"] = source.select
        if source.expand:
            params["$expand"] = source.expand
        if version == "2":
            params["$format"] = "json"
        return params

    def _record(self, item: Any) -> SourceRecord:
        if not isinstance(item, dict):
            raise SourceError(f"항목이 객체가 아닙니다: {item!r}")
        source = self.source
        key = as_text(value_at(item, source.key)).strip()
        if not key:
            raise SourceError(f"식별자({source.key})가 빈 항목입니다: {item!r}")
        deleted = bool(source.deleted) and truthy(value_at(item, source.deleted or ""))
        values = (
            {}
            if deleted
            else {out: as_text(value_at(item, path)) for out, path in source.columns.items()}
        )
        updated = (
            as_text(value_at(item, source.modified_field)) if source.modified_field else ""
        )
        return SourceRecord(
            key=key, values=values, updated_at=updated or None, deleted=deleted
        )

    def probe(self) -> str:
        """닿나 — `$top=1` 한 번(`relay check`). 실패면 `SourceError`."""
        source = self.source
        version = source.version if source.version in ("2", "4") else "4"
        params = {**self._first_params(None, version), "$top": 1}
        body = _clean(
            get_json(
                self._client,
                f"{source.service_url.rstrip('/')}/{source.entity_set}",
                params=params,
                headers=source.auth.headers(),
                retries=1,
                sleep=self._sleep,
            )
        )
        if isinstance(body, dict) and "value" in body:
            items = body["value"]
        elif isinstance(body, dict) and "d" in body:
            inner = body["d"]
            items = inner if isinstance(inner, list) else inner.get("results", [])
        else:
            raise SourceError("OData 봉투(value · d)가 아닙니다")
        first = f", 식별자 {as_text(value_at(items[0], source.key))}" if items else ""
        return f"응답 정상 — {len(items)}줄{first}"

    def fetch(self, since: str | None) -> Fetched:
        source = self.source
        version = source.version if source.version in ("2", "4") else "4"
        url = f"{source.service_url.rstrip('/')}/{source.entity_set}"
        params: dict[str, Any] | None = self._first_params(since, version)
        records: list[SourceRecord] = []
        marks: list[str] = []
        skip = 0
        for _ in range(source.max_pages):
            body = _clean(
                get_json(
                    self._client,
                    url,
                    params=params,
                    headers=source.auth.headers(),
                    retries=source.retries,
                    sleep=self._sleep,
                )
            )
            if isinstance(body, dict) and "value" in body:
                items, nxt = body["value"], body.get("@odata.nextLink")
            elif isinstance(body, dict) and "d" in body:
                inner = body["d"]
                items = inner if isinstance(inner, list) else inner.get("results", inner)
                nxt = inner.get("__next") if isinstance(inner, dict) else None
            else:
                raise SourceError(f"OData 봉투(value · d)가 아닙니다: {body!r:.300}")
            if not isinstance(items, list):
                raise SourceError(f"OData 행이 목록이 아닙니다: {items!r:.300}")
            for item in items:
                one = self._record(item)
                records.append(one)
                if one.updated_at:
                    marks.append(one.updated_at)
            if nxt:
                url, params = str(nxt), None
            elif len(items) >= source.page_size:
                skip += len(items)
                params = {**self._first_params(since, version), "$skip": skip}
            else:
                break
        else:
            raise SourceError(f"쪽이 {source.max_pages}개를 넘었습니다 — 끝을 알 수 없습니다.")
        watermark = max(marks) if marks else since
        return Fetched(records=records, watermark=watermark, complete=True, full=not since)
