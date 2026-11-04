"""REST JSON 원천 — 쪽 넘김 · 증분 · 삭제 표시 · 중첩 펴기 · 인증.

SP 의 데이터 소스(`rest`)가 읽는 것과 같은 축(쪽 넘김 셋 · 인증 넷)을 받되, 그쪽이 못 하는 일을
한다: **중첩 JSON 을 평평한 열로 편다**(`task.code` → `TASK_CODE`). 레시피(`sp-table/1`)는
평평한 표만 읽으므로 펴는 규칙은 여기 — 어댑터 설정의 `columns` — 에 한 번만 적는다.

재시도는 SP 연동 지침서 §8 을 따른다: 5xx · 네트워크 오류만 다시, 4xx 는 다시 보내도 같다(429
만 기다렸다 한 번 더).
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from relay_core.adapters.base import Fetched
from relay_core.records import SourceRecord

#: 여러 값을 한 칸에 — SP 규약(§7)이 `;` 로 잇는다.
MULTI_JOIN = ";"


class SourceError(RuntimeError):
    """원천이 거절했거나 모양이 다르다 — **멈춘다.** 덜 받은 채 넘어가면 부재 대조가 아직 안
    받은
    것을 지운다."""


@dataclass(frozen=True)
class Auth:
    kind: str = "none"
    """none · bearer · header · basic."""
    token: str = ""
    header: str = "Authorization"
    user: str = ""

    def headers(self) -> dict[str, str]:
        if self.kind == "bearer":
            return {"Authorization": f"Bearer {self.token}"}
        if self.kind == "header":
            return {self.header: self.token}
        if self.kind == "basic":
            raw = base64.b64encode(f"{self.user}:{self.token}".encode()).decode()
            return {"Authorization": f"Basic {raw}"}
        if self.kind == "none":
            return {}
        raise ValueError(f"모르는 인증 방식입니다: {self.kind}")


@dataclass(frozen=True)
class RestSource:
    base_url: str
    path: str
    key: str
    """항목에서 **안 바뀌는 식별자**의 자리(점으로 잇는다 — `id`, `model.id`)."""
    columns: Mapping[str, str]
    """내보낼 열 → 항목 안의 자리. 레시피가 읽는 열 이름이 여기서 정해진다."""
    rows_path: str = "items"
    paging: str = "page"
    """page · offset · cursor · none."""
    page_size: int = 200
    page_param: str = "page"
    size_param: str = "page_size"
    offset_param: str = "offset"
    cursor_param: str = "cursor"
    next_path: str = "next"
    start_page: int = 1
    since_param: str | None = None
    """증분을 받는 질의 이름(`updated_since`). 없으면 늘 전량이다."""
    updated_at: str | None = "updated_at"
    deleted: str | None = "deleted"
    """참이면 지운 것. **원천은 지운 행을 빼지 말고 이 표시로 알려야 한다**(지침서 §3.2)."""
    watermark_path: str | None = None
    """응답이 주는 「여기까지」 (`as_of`). 없으면 받은 행의 `updated_at` 최댓값."""
    params: Mapping[str, str] = field(default_factory=dict)
    auth: Auth = field(default_factory=Auth)
    timeout: float = 30.0
    retries: int = 3
    max_pages: int = 10_000


def value_at(item: Any, path: str) -> Any:
    """점으로 이은 자리의 값. 목록을 지나면 그 원소마다 — `tags.name` 은 이름 목록이 된다."""
    current: Any = item
    for part in path.split("."):
        if isinstance(current, list):
            current = [one.get(part) if isinstance(one, dict) else None for one in current]
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def as_text(value: Any) -> str:
    """표의 한 칸 — 빈 것은 빈 글, 목록은 `;` 로, 불리언은 소문자."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return MULTI_JOIN.join(as_text(one) for one in value if one not in (None, ""))
    if isinstance(value, dict):
        raise SourceError(
            f"평평하지 않은 값입니다 — columns 에 더 깊은 자리를 적으세요: {value!r}"
        )
    return str(value)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "y", "yes", "예", "참", "o"}


class RestAdapter:
    def __init__(
        self,
        source: RestSource,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.source = source
        self._client = client or httpx.Client(timeout=source.timeout)
        self._sleep = sleep

    def _get(self, params: dict[str, Any]) -> Any:
        url = self.source.base_url.rstrip("/") + "/" + self.source.path.lstrip("/")
        headers = {"Accept": "application/json", **self.source.auth.headers()}
        last: str = ""
        for attempt in range(self.source.retries):
            try:
                answer = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as failure:
                last = f"닿지 않습니다: {failure}"
            else:
                if answer.status_code == 200:
                    return answer.json()
                last = f"{answer.status_code} {answer.text[:300]}"
                if answer.status_code < 500 and answer.status_code != 429:
                    raise SourceError(f"원천이 거절했습니다({url}): {last}")
            self._sleep(min(2**attempt, 10))
        raise SourceError(
            f"원천에 {self.source.retries}번 물었지만 실패했습니다({url}): {last}"
        )

    def _record(self, item: Any) -> SourceRecord:
        if not isinstance(item, dict):
            raise SourceError(f"항목이 객체가 아닙니다: {item!r}")
        key = as_text(value_at(item, self.source.key)).strip()
        if not key:
            raise SourceError(f"식별자({self.source.key})가 빈 항목입니다: {item!r}")
        deleted = bool(self.source.deleted) and _truthy(
            value_at(item, self.source.deleted or "")
        )
        values = (
            {}
            if deleted
            else {
                out: as_text(value_at(item, path)) for out, path in self.source.columns.items()
            }
        )
        updated = (
            as_text(value_at(item, self.source.updated_at)) if self.source.updated_at else ""
        )
        return SourceRecord(
            key=key, values=values, updated_at=updated or None, deleted=deleted
        )

    def fetch(self, since: str | None) -> Fetched:
        """`since` 가 있으면 그 뒤에 바뀐 것만, 없으면 전량. 마지막 쪽까지 못 가면 예외다."""
        source = self.source
        records: list[SourceRecord] = []
        marks: list[str] = []
        page, offset, cursor = source.start_page, 0, ""
        watermark: str | None = None
        for _ in range(source.max_pages):
            params: dict[str, Any] = dict(source.params)
            if since and source.since_param:
                params[source.since_param] = since
            if source.paging == "page":
                params.update({source.page_param: page, source.size_param: source.page_size})
            elif source.paging == "offset":
                params.update(
                    {source.offset_param: offset, source.size_param: source.page_size}
                )
            elif source.paging == "cursor":
                params[source.size_param] = source.page_size
                if cursor:
                    params[source.cursor_param] = cursor
            elif source.paging != "none":
                raise ValueError(f"모르는 쪽 넘김입니다: {source.paging}")
            body = self._get(params)
            items = value_at(body, source.rows_path) if source.rows_path else body
            if not isinstance(items, list):
                raise SourceError(
                    f"행 자리({source.rows_path})에 목록이 없습니다: {body!r:.300}"
                )
            for item in items:
                one = self._record(item)
                records.append(one)
                if one.updated_at:
                    marks.append(one.updated_at)
            if source.watermark_path:
                given = value_at(body, source.watermark_path)
                watermark = str(given) if given else watermark
            if source.paging == "none":
                break
            if source.paging == "cursor":
                cursor = as_text(value_at(body, source.next_path))
                if not cursor:
                    break
            elif len(items) < source.page_size:
                break
            page += 1
            offset += len(items)
        else:
            raise SourceError(f"쪽이 {source.max_pages}개를 넘었습니다 — 끝을 알 수 없습니다.")
        if watermark is None:
            watermark = max(marks) if marks else since
        return Fetched(records=records, watermark=watermark, complete=True, full=not since)
