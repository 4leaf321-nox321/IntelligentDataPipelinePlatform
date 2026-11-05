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
from sqlalchemy import create_engine, text

from relay_core.adapters.file import FileAdapter, FileSource
from relay_core.adapters.rest import SourceError
from relay_core.adapters.sql import SqlAdapter, SqlSource
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


# --- DB --------------------------------------------------------------------------------


@pytest.fixture
def lots_db(tmp_path: Path) -> Any:
    engine = create_engine(f"sqlite:///{tmp_path / 'mes.db'}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE v_lot (lot_id TEXT, qty INTEGER, gone INTEGER, updated TEXT)")
        )
        conn.execute(
            text("INSERT INTO v_lot VALUES (:a, :b, :c, :d)"),
            [
                {"a": "L1", "b": 10, "c": 0, "d": "2026-11-01 00:00:00"},
                {"a": "L2", "b": 5, "c": 1, "d": "2026-11-03 00:00:00"},
            ],
        )
    return engine


def test_DB_는_표를_읽고_증분은_바뀐_때로(lots_db: Any) -> None:
    source = SqlSource(
        dsn_env="X", key="lot_id", table="v_lot", modified_column="updated", deleted="gone"
    )
    adapter = SqlAdapter(source, engine=lots_db)
    full = adapter.fetch(None)
    assert [one.key for one in full.records] == ["L1", "L2"] and full.full
    assert full.records[1].deleted and full.watermark == "2026-11-03 00:00:00"
    later = adapter.fetch("2026-11-02 00:00:00")
    assert [one.key for one in later.records] == ["L2"] and not later.full
    assert adapter.discover()["table_columns"][0]["name"] == "lot_id"


def test_DB_는_읽기_전용_한_문장만_받는다(lots_db: Any) -> None:
    ok = SqlAdapter(
        SqlSource(
            dsn_env="X", key="lot_id", query="SELECT lot_id, qty FROM v_lot WHERE qty > 6"
        ),
        engine=lots_db,
    )
    assert [one.key for one in ok.fetch(None).records] == ["L1"]
    for wrong in ("DELETE FROM v_lot", "SELECT 1; DROP TABLE v_lot", "UPDATE v_lot SET qty=0"):
        with pytest.raises(SourceError, match="읽기 전용"):
            SqlAdapter(SqlSource(dsn_env="X", key="lot_id", query=wrong), engine=lots_db)
    with pytest.raises(SourceError, match="표 이름"):
        SqlAdapter(SqlSource(dsn_env="X", key="lot_id", table="v_lot; drop"), engine=lots_db)


def test_정의의_비밀은_환경_변수_이름으로만(tmp_path: Path) -> None:
    with pytest.raises(DefinitionError, match="비밀"):
        build_adapter(
            {"kind": "sql", "dsn_env": "X", "dsn": "postgresql://u:p@h/db", "key": "k"}, {}
        )
    built = build_adapter(
        {"kind": "sql", "dsn_env": "MES_DB", "key": "lot_id", "table": "v_lot"},
        {"MES_DB": f"sqlite:///{tmp_path / 'x.db'}"},
    )
    assert built.source.dsn.startswith("sqlite:///")
    with pytest.raises(DefinitionError, match="MES_DB"):
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


# --- 공유 폴더(SMB) · 시험용 상한 · Oracle · MSSQL ---------------------------------------


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


def test_시험용_상한이면_앞만_받고_덜_받았다고_적는다(tmp_path: Path, lots_db: Any) -> None:
    path = tmp_path / "many.csv"
    path.write_text("K\n" + "".join(f"R{i}\n" for i in range(10)), encoding="utf-8")
    got = FileAdapter(FileSource(location=str(path), key="K")).fetch(None, limit=3)
    assert [one.key for one in got.records] == ["R0", "R1", "R2"] and not got.complete
    assert FileAdapter(FileSource(location=str(path), key="K")).fetch(None, limit=50).complete

    sql = SqlAdapter(SqlSource(dsn_env="X", key="lot_id", table="v_lot"), engine=lots_db)
    capped = sql.fetch(None, limit=1)
    assert len(capped.records) == 1 and not capped.complete
    assert sql.fetch(None, limit=2).complete

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


def test_DB_칸_이름은_큰_작은_글자를_가리지_않고_세_마디_표_이름도_받는다(
    tmp_path: Path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'o.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE v_lot (lot_id TEXT, qty INTEGER, del_yn TEXT)"))
        conn.execute(text("INSERT INTO v_lot VALUES ('L1', 3, 'N'), ('L2', 4, 'Y')"))
    # Oracle 은 SQLAlchemy 를 거치며 이름이 작은 글자로 온다 — 정의는 대개 큰 글자로 적는다
    source = SqlSource(
        dsn_env="X", key="LOT_ID", table="v_lot", deleted="DEL_YN", columns={"QTY": "QTY"}
    )
    got = SqlAdapter(source, engine=engine).fetch(None)
    assert [(one.key, one.deleted) for one in got.records] == [("L1", False), ("L2", True)]
    assert dict(got.records[0].values) == {"QTY": "3"}
    SqlAdapter(SqlSource(dsn_env="X", key="k", table="MESDB.dbo.v_lot"), engine=engine)
    with pytest.raises(SourceError, match="표 이름"):
        SqlAdapter(SqlSource(dsn_env="X", key="k", table="a.b.c.d"), engine=engine)


def test_Oracle_과_MSSQL_드라이버가_이미지에_들어_있다() -> None:
    for dsn, dialect in (
        ("oracle+oracledb://u:p@mesdb01:1521/?service_name=MES", "oracle"),
        ("mssql+pymssql://u:p@mesdb02:1433/MES", "mssql"),
    ):
        adapter = SqlAdapter(SqlSource(dsn_env="X", key="k", table="v_lot", dsn=dsn))
        assert adapter.engine.dialect.name == dialect  # 접속은 하지 않는다 — 드라이버를 싣기만
