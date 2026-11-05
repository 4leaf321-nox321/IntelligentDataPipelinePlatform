"""XML · SOAP 원천 — 오래된 MES · PLM 이 흔히 낸다. 행은 XPath 로 집는다.

    {"kind": "xml", "url": "http://mes/ws/Lots", "rows_xpath": "//m:Lot", "key": "m:LotId",
     "columns": {"LOT_ID": "m:LotId", "QTY": "m:Qty", "LINE": "@line"},
     "namespaces": {"m": "urn:mes"}}

- **GET**(REST 식 XML)이 기본이다. `soap_action` 이나 `envelope` 를 주면 **POST** 로 SOAP
  봉투를 보낸다 — 봉투 글에 `{since}` · `{page}` · `{page_size}` 를 적으면 그 자리에 값이
  들어간다.
- `columns` 의 자리는 **행 요소 기준** XPath — 요소면 그 글(`string()`), 속성이면 그 값,
  여럿이면 `;`.
- **안전하게 읽는다**(lxml): 바깥 엔티티 · 네트워크를 안 따라가고, 거대한 문서를 거절한다. SOAP
  `Fault` 가 오면 멈춘다.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from lxml import etree

from relay_core.adapters.base import Fetched
from relay_core.adapters.rest import Auth, SourceError, truthy
from relay_core.records import SourceRecord

_PARSER = etree.XMLParser(
    resolve_entities=False, no_network=True, huge_tree=False, remove_blank_text=True
)


@dataclass(frozen=True)
class XmlSource:
    url: str
    rows_xpath: str
    key: str
    columns: Mapping[str, str]
    namespaces: Mapping[str, str] = field(default_factory=dict)
    method: str = "GET"
    soap_action: str = ""
    envelope: str = ""
    """SOAP 봉투 — `{since}` · `{page}` · `{page_size}` 자리. 주면 POST 로 보낸다."""
    content_type: str = "text/xml; charset=utf-8"
    params: Mapping[str, str] = field(default_factory=dict)
    since_param: str | None = None
    """GET 일 때 증분 질의 이름. SOAP 은 봉투의 `{since}`."""
    paging: str = "none"
    """none · page — page 면 덜 찬 쪽에서 끝."""
    page_param: str = "page"
    size_param: str = "page_size"
    page_size: int = 200
    start_page: int = 1
    deleted: str | None = None
    updated_at: str | None = None
    auth: Auth = field(default_factory=Auth)
    timeout: float = 60.0
    retries: int = 3
    max_pages: int = 10_000


def _text(found: Any) -> str:
    """XPath 결과 → 글. 요소는 안의 글 전부, 여럿은 `;`."""
    if isinstance(found, list):
        parts = [_text(one) for one in found]
        return ";".join(part for part in parts if part)
    if isinstance(found, etree._Element):
        return "".join(found.itertext()).strip()
    if found is None:
        return ""
    if isinstance(found, float) and found.is_integer():
        return str(int(found))
    return str(found).strip()


class XmlAdapter:
    def __init__(
        self,
        source: XmlSource,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.source = source
        self._client = client or httpx.Client(timeout=source.timeout)
        self._sleep = sleep

    @property
    def _soap(self) -> bool:
        return bool(self.source.envelope or self.source.soap_action)

    def _request(self, since: str | None, page: int) -> Any:
        source = self.source
        headers = {**source.auth.headers(), "Accept": "application/xml, text/xml"}
        params: dict[str, Any] = dict(source.params)
        body: bytes | None = None
        method = "POST" if self._soap else source.method.upper()
        if self._soap:
            headers["Content-Type"] = source.content_type
            if source.soap_action:
                headers["SOAPAction"] = source.soap_action
            body = (
                source.envelope.replace("{since}", since or "")
                .replace("{page}", str(page))
                .replace("{page_size}", str(source.page_size))
                .encode("utf-8")
            )
        else:
            if since and source.since_param:
                params[source.since_param] = since
            if source.paging == "page":
                params.update({source.page_param: page, source.size_param: source.page_size})
        last = ""
        for attempt in range(source.retries):
            try:
                got = self._client.request(
                    method, source.url, params=params, content=body, headers=headers
                )
            except httpx.HTTPError as failure:
                last = f"닿지 않습니다: {failure}"
            else:
                if got.status_code == 200:
                    return self._parse(got.content)
                last = f"{got.status_code} {got.text[:300]}"
                if got.status_code < 500 and got.status_code != 429:
                    raise SourceError(f"원천이 거절했습니다({source.url}): {last}")
            self._sleep(min(2**attempt, 10))
        raise SourceError(
            f"원천에 {source.retries}번 물었지만 실패했습니다({source.url}): {last}"
        )

    def _parse(self, raw: bytes) -> Any:
        try:
            root = etree.fromstring(raw, parser=_PARSER)
        except etree.XMLSyntaxError as failure:
            raise SourceError(f"XML 이 아닙니다: {failure}") from None
        fault = root.xpath("//*[local-name()='Fault']")
        if fault:
            raise SourceError(f"SOAP Fault: {_text(fault[0])[:300]}")
        return root

    def _rows(self, root: Any) -> list[Any]:
        try:
            found = root.xpath(self.source.rows_xpath, namespaces=dict(self.source.namespaces))
        except etree.XPathError as failure:
            raise SourceError(
                f"rows_xpath 가 틀렸습니다({self.source.rows_xpath}): {failure}"
            ) from None
        if not isinstance(found, list):
            raise SourceError("rows_xpath 가 요소 목록을 주지 않습니다.")
        return [one for one in found if isinstance(one, etree._Element)]

    def _value(self, row: Any, path: str) -> str:
        try:
            return _text(row.xpath(path, namespaces=dict(self.source.namespaces)))
        except etree.XPathError as failure:
            raise SourceError(f"자리가 틀렸습니다({path}): {failure}") from None

    def _record(self, row: Any) -> SourceRecord:
        source = self.source
        key = self._value(row, source.key)
        if not key:
            raise SourceError(f"식별자({source.key})가 빈 행이 있습니다.")
        deleted = bool(source.deleted) and truthy(self._value(row, source.deleted or ""))
        values = (
            {}
            if deleted
            else {out: self._value(row, path) for out, path in source.columns.items()}
        )
        updated = self._value(row, source.updated_at) if source.updated_at else ""
        return SourceRecord(
            key=key, values=values, updated_at=updated or None, deleted=deleted
        )

    def fetch(self, since: str | None, *, limit: int | None = None) -> Fetched:
        """`limit` 이면 그만큼만 받고 멈춘다(시험용 — `complete=False`)."""
        source = self.source
        records: list[SourceRecord] = []
        marks: list[str] = []
        page = source.start_page
        for _ in range(source.max_pages):
            rows = self._rows(self._request(since, page))
            for row in rows:
                one = self._record(row)
                records.append(one)
                if one.updated_at:
                    marks.append(one.updated_at)
            if limit and len(records) >= limit:
                return Fetched(records=records[:limit], complete=False)
            if source.paging != "page" or len(rows) < source.page_size:
                break
            page += 1
        else:
            raise SourceError(f"쪽이 {source.max_pages}개를 넘었습니다.")
        return Fetched(
            records=records,
            watermark=max(marks) if marks else since,
            complete=True,
            full=not since or not (source.since_param or "{since}" in source.envelope),
        )

    def probe(self) -> str:
        rows = self._rows(self._request(None, self.source.start_page))
        first = f", 식별자 {self._value(rows[0], self.source.key)}" if rows else ""
        return f"응답 정상 — 행 {len(rows)}개{first}"

    def discover(self) -> dict[str, Any]:
        """첫 행들의 자식 요소 · 속성 자리 → 열 제안(행 요소 기준 XPath)."""
        from relay_core.adapters.explore import column_name

        rows = self._rows(self._request(None, self.source.start_page))
        paths: dict[str, str] = {}
        for row in rows[:5]:
            for name, value in row.attrib.items():
                paths.setdefault(f"@{etree.QName(name).localname}", str(value)[:60])
            for child in row.iter():
                if child is row or not isinstance(child.tag, str) or len(child):
                    continue
                trail = []
                node = child
                while node is not None and node is not row:
                    trail.append(f"*[local-name()='{etree.QName(node).localname}']")
                    node = node.getparent()
                paths.setdefault("/".join(reversed(trail)), (child.text or "").strip()[:60])

        def label(path: str) -> str:
            bare = (
                path.replace("*[local-name()='", "")
                .replace("']", "")
                .replace("/", ".")
                .lstrip("@")
            )
            return column_name(bare)

        return {
            "rows_seen": len(rows),
            "paths": [{"path": path, "sample": sample} for path, sample in paths.items()],
            "columns": {label(path): path for path in paths},
        }
