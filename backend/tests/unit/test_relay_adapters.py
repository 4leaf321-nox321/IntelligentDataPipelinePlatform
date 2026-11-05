"""어댑터 — 파일(CSV · Excel · JSON) · XML/SOAP · DB. 약속은 같다: 평평한 행 + 삭제 표시 +
워터마크.

원천은 시험이 만든다(임시 파일 · `httpx.MockTransport` · SQLite). 진짜 시스템의 형식은 붙는 날
정의에 적는다 — MES 가 무엇으로 줄지는 아직 모른다(2026-11-05).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from relay_core.adapters.file import FileAdapter, FileSource
from relay_core.adapters.rest import SourceError
from relay_core.adapters.xml import XmlAdapter, XmlSource
from relay_core.definition import DefinitionError, build_adapter

# --- 파일 ---------------------------------------------------------------------------------


def test_CSV_는_CP949_도_읽고_머리글_줄을_고를_수_있다(tmp_path: Path) -> None:
    path = tmp_path / "lots.csv"
    body = "내보낸 날 2026-11-05\nLOT_ID,품목,수량,삭제\nL1,브래킷,10,\nL2,커버,5,Y\n"
    path.write_bytes(body.encode("cp949"))
    got = FileAdapter(
        FileSource(location=str(path), key="LOT_ID", header_row=2, deleted="삭제")
    ).fetch("아무거나")
    assert got.full and got.complete and got.watermark and got.watermark.startswith("sha256:")
    first, second = got.records
    assert dict(first.values) == {"LOT_ID": "L1", "품목": "브래킷", "수량": "10", "삭제": ""}
    assert second.deleted and dict(second.values) == {}


def test_여러_파일_중_가장_늦게_바뀐_것을_읽는다(tmp_path: Path) -> None:
    old, new = tmp_path / "lots-1104.csv", tmp_path / "lots-1105.csv"
    old.write_text("LOT_ID\nOLD\n", encoding="utf-8")
    new.write_text("LOT_ID\nNEW\n", encoding="utf-8")
    past = time.time() - 3600
    os.utime(old, (past, past))
    got = FileAdapter(FileSource(location=str(tmp_path / "lots-*.csv"), key="LOT_ID")).fetch(
        None
    )
    assert [one.key for one in got.records] == ["NEW"]
    with pytest.raises(SourceError, match="없습니다"):
        FileAdapter(FileSource(location=str(tmp_path / "none-*.csv"), key="X")).fetch(None)


def test_JSON_과_Excel_과_URL(tmp_path: Path) -> None:
    doc = {
        "data": {"rows": [{"id": "A", "lot": {"no": "L1"}}, {"id": "B", "lot": {"no": "L2"}}]}
    }
    path = tmp_path / "x.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    source = FileSource(
        location=str(path), key="id", rows_path="data.rows", columns={"LOT": "lot.no"}
    )
    assert [dict(one.values) for one in FileAdapter(source).fetch(None).records] == [
        {"LOT": "L1"},
        {"LOT": "L2"},
    ]

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "생산"
    sheet.append(["LOT_ID", "수량", "일자"])
    sheet.append(["L1", 10.0, None])
    sheet.append([None, None, None])
    book.save(tmp_path / "x.xlsx")
    rows = FileAdapter(
        FileSource(location=str(tmp_path / "x.xlsx"), key="LOT_ID", sheet="생산")
    ).fetch(None)
    assert [dict(one.values) for one in rows.records] == [
        {"LOT_ID": "L1", "수량": "10", "일자": ""}
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer t"
        return httpx.Response(200, content=b"LOT_ID\nU1\n")

    from relay_core.adapters.rest import Auth

    remote = FileAdapter(
        FileSource(
            location="https://share.test/drop/lots.csv", key="LOT_ID", auth=Auth("bearer", "t")
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )
    assert [one.key for one in remote.fetch(None).records] == ["U1"]
    assert remote.discover()["columns"] == {"LOT_ID": "LOT_ID"}


# --- XML · SOAP ------------------------------------------------------------------------------

XML = b"""<?xml version="1.0"?>
<r:Lots xmlns:r="urn:mes"><r:Lot line="A1"><r:Id>L1</r:Id><r:Qty>10</r:Qty>
<r:Tags><r:Tag>x</r:Tag><r:Tag>y</r:Tag></r:Tags><r:At>2026-11-01T00:00:00Z</r:At></r:Lot>
<r:Lot line="A2"><r:Id>L2</r:Id><r:Qty>5</r:Qty><r:Gone>true</r:Gone></r:Lot></r:Lots>"""


def _xml(handler: Any, **over: Any) -> XmlAdapter:
    spec: dict[str, Any] = {
        "url": "http://mes.test/ws/lots",
        "rows_xpath": "//r:Lot",
        "key": "r:Id",
        "columns": {"QTY": "r:Qty", "LINE": "@line", "TAGS": "r:Tags/r:Tag"},
        "namespaces": {"r": "urn:mes"},
        "updated_at": "r:At",
        "deleted": "r:Gone",
    }
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return XmlAdapter(XmlSource(**{**spec, **over}), client=client, sleep=lambda _s: None)


def test_XML_은_XPath_로_집고_속성과_여러_값을_읽는다() -> None:
    got = _xml(lambda _r: httpx.Response(200, content=XML)).fetch(None)
    first, second = got.records
    assert dict(first.values) == {"QTY": "10", "LINE": "A1", "TAGS": "x;y"}
    assert second.deleted and got.watermark == "2026-11-01T00:00:00Z"


def test_SOAP_은_봉투에_증분을_넣고_Fault_면_멈춘다() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=XML)

    adapter = _xml(
        handler,
        soap_action="urn:mes#GetLots",
        envelope="<Envelope><Body><GetLots since='{since}'/></Body></Envelope>",
    )
    got = adapter.fetch("2026-10-31T00:00:00Z")
    assert seen[0].method == "POST" and seen[0].headers["SOAPAction"] == "urn:mes#GetLots"
    assert b"since='2026-10-31T00:00:00Z'" in seen[0].content and not got.full

    fault = b"<Envelope><Body><Fault><faultstring>bad</faultstring></Fault></Body></Envelope>"
    with pytest.raises(SourceError, match="SOAP Fault"):
        _xml(lambda _r: httpx.Response(200, content=fault)).fetch(None)


def test_XML_은_읽기만_한다_다른_방식_봉투_없는_POST_는_거절() -> None:
    """XML 원천으로 남의 API 를 부르지 못하게(2026-11-06 보안 검토) — 대상 SP 의 적용도
    POST 다. 정의에서 먼저 거절하고(`check_adapter`), 어댑터도 다시 본다."""
    from relay_core.definition import check_adapter

    base: dict[str, Any] = {"kind": "xml", "url": "http://mes.test/ws", "rows_xpath": "//r"}
    base |= {"key": "k", "columns": {"K": "k"}}
    wrong: list[dict[str, Any]] = [
        {"method": "DELETE"},
        {"method": "PUT", "envelope": "<E/>"},
        {"method": "POST"},
        {"soap_action": "urn:x#Apply"},
        {"envelope": "<E/>", "content_type": "application/json"},
        {"envelope": "<E/>", "soap_action": "a\r\nX-Evil: 1"},
    ]
    for over in wrong:
        with pytest.raises(DefinitionError):
            check_adapter({**base, **over})
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, content=XML)

    for over in wrong:
        with pytest.raises(SourceError):
            _xml(handler, **over).fetch(None)
    assert asked == []  # 한 번도 나가지 않았다
    _xml(handler, method="POST", envelope="<E/>").fetch(None)
    assert asked[-1].method == "POST"


def test_원천의_오류_글에_실은_비밀이_되돌아오지_않는다() -> None:
    """받은 머리글을 그대로 찍는 서버 — 그 글이 작업 오류 · 알림 · MCP 로 퍼진다."""
    from relay_core.adapters.rest import Auth, RestAdapter, RestSource

    def echo(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"bad credentials: {request.headers['Authorization']}")

    client = httpx.Client(transport=httpx.MockTransport(echo))
    for auth in (Auth("bearer", "sekret-token-123"), Auth("basic", "sekret-pass-9", user="u")):
        source = RestSource(
            base_url="http://plm.test", path="/m", key="id", columns={}, auth=auth
        )
        rest = RestAdapter(source, client=client, sleep=lambda _s: None)
        with pytest.raises(SourceError) as failure:
            rest.fetch(None)
        assert "sekret" not in str(failure.value) and "***" in str(failure.value)
        assert "sekret" not in repr(auth)  # 객체 표현(로그 · 오류 추적)에도
    xml = _xml(echo, auth=Auth("header", "sekret-key-77", header="Authorization"))
    with pytest.raises(SourceError) as failure:
        xml.fetch(None)
    assert "sekret" not in str(failure.value)


def test_숫자_칸은_범위_안이어야_한다() -> None:
    from relay_core.definition import check_adapter

    base: dict[str, Any] = {"kind": "rest", "base_url": "http://x", "path": "/r", "key": "id"}
    base["columns"] = {"ID": "id"}
    for over in ({"timeout": 0}, {"timeout": 3600}, {"retries": True}, {"max_pages": 10**9}):
        with pytest.raises(DefinitionError, match="이하여야"):
            check_adapter({**base, **over})
    assert check_adapter({**base, "timeout": 60, "retries": 3})[1]["timeout"] == 60


def test_XML_은_바깥_엔티티를_따라가지_않는다(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    evil = (
        f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file://{secret}">]>'
        '<r:Lots xmlns:r="urn:mes"><r:Lot><r:Id>&x;</r:Id></r:Lot></r:Lots>'
    ).encode()
    adapter = _xml(lambda _r: httpx.Response(200, content=evil), columns={})
    try:
        records = adapter.fetch(None).records
    except SourceError:
        return  # 식별자가 비어 멈춘 것 — 그것도 안전하다
    assert all("TOP-SECRET" not in one.key for one in records)


def test_XML_탐색은_행의_자리를_보인다() -> None:
    found = _xml(lambda _r: httpx.Response(200, content=XML)).discover()
    assert found["rows_seen"] == 2
    assert "@line" in found["columns"].values() and "LINE" in found["columns"]


def test_정의의_비밀은_환경_변수_이름으로만() -> None:
    rest: dict[str, Any] = {"kind": "rest", "base_url": "http://x", "path": "/r", "key": "id"}
    rest["columns"] = {"ID": "id"}
    bearer = {"kind": "bearer", "token": "abc"}
    for leaked in ({**rest, "token": "abc"}, {**rest, "auth": bearer}):
        with pytest.raises(DefinitionError, match="비밀"):
            build_adapter(leaked, {})
    named = {**rest, "auth": {"kind": "bearer", "token_env": "PLM_TOKEN"}}
    assert build_adapter(named, {"PLM_TOKEN": "t"}).source.auth.token == "t"
    with pytest.raises(DefinitionError, match="PLM_TOKEN"):
        build_adapter(named, {})


def test_DB_를_바로_읽는_원천은_없다() -> None:
    """원천은 API(OData · REST · XML) · 파일 · 공유 폴더로만 받는다 — 바깥 DB 에 바로 붙을
    권한도 일도 없다(2026-11-05). 옛 정의가 `sql` 을 적었으면 조용히 넘기지 않고 거절한다."""
    with pytest.raises(DefinitionError, match="모르는 어댑터"):
        build_adapter({"kind": "sql", "dsn_env": "MES_DB", "key": "k", "table": "t"}, {})


def test_파일_어댑터는_읽어도_되는_폴더_밖을_링크로도_못_읽는다(tmp_path: Path) -> None:
    inside, outside = tmp_path / "drop", tmp_path / "secret"
    inside.mkdir()
    outside.mkdir()
    (outside / "keys.csv").write_text("K\nTOP-SECRET\n", encoding="utf-8")
    (inside / "ok.csv").write_text("K\nfine\n", encoding="utf-8")
    (inside / "link.csv").symlink_to(outside / "keys.csv")
    spec = {"kind": "file", "location": str(inside / "ok.csv"), "key": "K"}
    assert [
        one.key for one in build_adapter(spec, {}, file_roots=[inside]).fetch(None).records
    ] == ["fine"]
    linked = build_adapter(
        {**spec, "location": str(inside / "link.csv")}, {}, file_roots=[inside]
    )
    with pytest.raises(SourceError, match="RELAY_FILE_ROOTS"):
        linked.fetch(None)


def test_탐색은_식별자_열을_몰라도_된다() -> None:
    from relay_core.definition import check_adapter

    kind, body = check_adapter(
        {"kind": "odata", "service_url": "http://x/odata"}, partial=True
    )
    assert kind == "odata" and body["key"] == "" and body["entity_set"] == ""
    with pytest.raises(DefinitionError, match="service_url"):
        check_adapter({"kind": "odata"}, partial=True)
    with pytest.raises(DefinitionError, match="key"):
        check_adapter({"kind": "odata", "service_url": "http://x/odata", "entity_set": "A"})


# --- 공유 폴더(SMB) · 시험용 상한 -----------------------------------------------------


class _Share:
    """가짜 공유 폴더 — `\\\\서버\\공유\\폴더` 아래 파일 `(이름, 바뀐 시각, 내용)`."""

    def __init__(self, files: dict[str, tuple[float, bytes]]) -> None:
        self.files = files
        self.asked: list[str] = []

    def listdir(self, folder: str) -> list[tuple[str, float]]:
        self.asked.append(folder)
        return [(name, mtime) for name, (mtime, _body) in self.files.items()]

    def read(self, path: str) -> bytes:
        self.asked.append(path)
        return self.files[path.rsplit("\\", 1)[-1]][1]


def test_공유_폴더에서_이름이_맞는_것_중_가장_늦게_바뀐_것을_읽는다() -> None:
    share = _Share(
        {
            "LOTS-1104.CSV": (100.0, b"LOT_ID\nOLD\n"),
            "lots-1105.csv": (200.0, b"LOT_ID\nNEW\n"),
            "readme.txt": (300.0, b"x"),
        }
    )
    source = FileSource(location="smb://mesfs01/export/daily/lots-*.csv", key="LOT_ID")
    got = FileAdapter(source, smb=share, sleep=lambda _s: None).fetch(None)
    assert [one.key for one in got.records] == ["NEW"]
    assert share.asked == [
        "\\\\mesfs01\\export\\daily",
        "\\\\mesfs01\\export\\daily\\lots-1105.csv",
    ]

    for wrong in ("smb://mesfs01/export/*/lots.csv", "smb://u:p@mesfs01/export/lots.csv"):
        with pytest.raises(SourceError):
            FileAdapter(FileSource(location=wrong, key="LOT_ID"), smb=share).fetch(None)
    empty = _Share({})
    with pytest.raises(SourceError, match="없습니다"):
        FileAdapter(source, smb=empty).fetch(None)


def test_공유_폴더가_안_닿으면_되풀이하고_까닭을_말한다() -> None:
    class Down(_Share):
        def listdir(self, folder: str) -> list[tuple[str, float]]:
            raise OSError("STATUS_LOGON_FAILURE")

    source = FileSource(location="smb://mesfs01/export/lots.csv", key="K", retries=2)
    with pytest.raises(SourceError, match=r"2번.*STATUS_LOGON_FAILURE"):
        FileAdapter(source, smb=Down({}), sleep=lambda _s: None).fetch(None)


def test_시험용_상한이면_앞만_받고_덜_받았다고_적는다(tmp_path: Path) -> None:
    path = tmp_path / "many.csv"
    path.write_text("K\n" + "".join(f"R{i}\n" for i in range(10)), encoding="utf-8")
    got = FileAdapter(FileSource(location=str(path), key="K")).fetch(None, limit=3)
    assert [one.key for one in got.records] == ["R0", "R1", "R2"] and not got.complete
    assert FileAdapter(FileSource(location=str(path), key="K")).fetch(None, limit=50).complete

    pages = [{"items": [{"id": f"A{i}"} for i in range(3)]}] * 5
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=pages[len(calls) - 1])

    from relay_core.adapters.rest import RestAdapter, RestSource

    rest = RestAdapter(
        RestSource(
            base_url="http://x", path="/r", key="id", columns={"ID": "id"}, page_size=3
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _s: None,
    )
    got = rest.fetch(None, limit=4)
    assert len(got.records) == 4 and not got.complete and len(calls) == 2


def test_slug_는_끝의_줄바꿈도_거절한다() -> None:
    """`^…$` 를 `match` 로 보면 `"plm\\n"` 이 지난다 — 그 slug 가 작업 폴더 · 주소가 된다."""
    from relay_core.definition import check_body

    body: dict[str, Any] = {
        "format": "relay-pipeline/1",
        "slug": "plm-model",
        "source": "plm",
        "adapter": {"kind": "rest", "base_url": "http://x", "path": "/r", "key": "id"},
        "target": {"server": "http://sp", "token_env": "SP_TOKEN"},
    }
    body["adapter"]["columns"] = {"ID": "id"}
    assert check_body(body) == ("plm-model", "plm")
    for slug, source in (("plm-model\n", "plm"), ("plm-model", "plm\n")):
        with pytest.raises(DefinitionError):
            check_body({**body, "slug": slug, "source": source})
