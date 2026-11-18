"""Parquet — 파일 원천(`*.parquet`)과 값의 꼴. 십진수는 글 그대로(자릿수), 시각은 ISO, NaN 은
빈 값, 구조체는 점 자리 · 목록은 펴기, 바이트는 base64. 흘려 읽어 `Records` 에 넣는다."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from relay_core.adapters import parquet
from relay_core.adapters.file import FileAdapter, FileSource
from relay_core.adapters.rest import SourceError
from relay_core.records import Records


def _table() -> pa.Table:
    return pa.table(
        {
            "LOT_ID": ["L1", "L2", "L3"],
            "QTY": pa.array([3, None, 5], type=pa.int64()),
            "YIELD": [0.5, float("nan"), 1.0],
            "PRICE": pa.array(
                [Decimal("12.3400"), Decimal("0.10"), None], type=pa.decimal128(10, 4)
            ),
            "MADE_AT": pa.array(
                [datetime(2026, 11, 1, 9, 30, tzinfo=UTC), None, None],
                type=pa.timestamp("ms", tz="UTC"),
            ),
            "MADE_ON": [date(2026, 11, 1), date(2026, 11, 2), None],
            "SPEC": [{"weight": 1.5, "color": "R"}, {"weight": 2.0, "color": None}, None],
            "STEPS": [[{"seq": 1}, {"seq": 2}], [], [{"seq": 9}]],
            "RAW": [b"\x00\x01", None, b"z"],
            "GONE": [False, False, True],
        }
    )


def _write(path: Path, table: pa.Table, **kw: object) -> Path:
    pq.write_table(table, path, **kw)
    return path


def test_값은_JSON_꼴로_십진수는_글_그대로() -> None:
    buf = io.BytesIO()
    pq.write_table(_table(), buf)
    raw = buf.getvalue()
    assert parquet.looks_like(raw) and not parquet.looks_like(b"LOT_ID,QTY\nL1,3\n")
    first, second, third = parquet.rows(raw)
    assert first["PRICE"] == "12.3400" and second["PRICE"] == "0.1000"
    assert first["MADE_AT"] == "2026-11-01T09:30:00+00:00"
    assert first["MADE_ON"] == "2026-11-01" and third["MADE_ON"] is None
    assert second["YIELD"] is None  # NaN
    assert first["SPEC"] == {"weight": 1.5, "color": "R"} and third["SPEC"] is None
    assert first["STEPS"] == [{"seq": 1}, {"seq": 2}]
    assert first["RAW"] == "AAE="
    assert parquet.count(raw) == 3 and parquet.columns(raw)[:2] == ["LOT_ID", "QTY"]
    assert [one["LOT_ID"] for one in parquet.rows(raw, limit=2)] == ["L1", "L2"]
    with pytest.raises(SourceError, match="Parquet 파일이 아닙니다"):
        list(parquet.rows(b"PAR1 but not really"))


def test_파일_원천은_parquet_을_흘려_읽고_점_자리_펴기_삭제를_본다(tmp_path: Path) -> None:
    # 묶음(row group)을 작게 — 여러 묶음을 이어 읽어도 같다.
    _write(tmp_path / "lots-20261101.parquet", _table(), row_group_size=1)
    source = FileSource(
        location=str(tmp_path / "lots-*.parquet"),
        key="LOT_ID",
        columns={"LOT_ID": "LOT_ID", "WEIGHT": "SPEC.weight", "PRICE": "PRICE"},
        deleted="GONE",
    )
    got = FileAdapter(source).fetch(None)
    assert isinstance(got.records, Records) and got.full and got.complete
    assert got.watermark and got.watermark.startswith("sha256:")
    rows = {one.key: one for one in got.records}
    assert rows["L1"].values == {"LOT_ID": "L1", "WEIGHT": "1.5", "PRICE": "12.3400"}
    assert rows["L3"].deleted

    steps = FileSource(
        location=str(tmp_path / "lots-20261101.parquet"),
        key=["LOT_ID", "STEPS.seq"],
        columns={"LOT_ID": "LOT_ID", "SEQ": "STEPS.seq"},
        explode="STEPS",
    )
    exploded = FileAdapter(steps).fetch(None)
    assert sorted(one.key for one in exploded.records) == ["L1|1", "L1|2", "L3|9"]

    star = FileAdapter(
        FileSource(location=str(tmp_path / "lots-20261101.parquet"), key="LOT_ID")
    )
    cut = star.fetch(None, limit=2)
    assert len(cut.records) == 2 and not cut.complete
    found = star.discover()
    assert found["rows"] == 3 and "LOT_ID" in found["columns"]
    assert "3행" in star.probe()
    # 머리글 그대로(`*`)는 구조체 · 목록 칸을 열로 내지 않는다(점 자리로 적는다).
    (one,) = [r for r in star.fetch(None).records if r.key == "L1"]
    assert "SPEC" not in one.values and one.values["QTY"] == "3"
