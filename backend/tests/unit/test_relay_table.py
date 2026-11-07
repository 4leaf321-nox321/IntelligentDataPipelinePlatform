"""나눠 변환 · 나눠 비교 · 흘려 쓰는 미러 — **한꺼번에 한 것과 바이트까지 같다.**

변환(`relay_core.table`)은 틀의 변환기(`sp_table.convert`)와 견준다 — 해석기 · 참조
대조(미해결 · 그대로 · 비움) · 모으는 칸(collect · join) · 갈린 값 · 빈 식별자 · 두 갈래 관계와
그 속성까지 든 레시피로. 바구니를 아주 작게(수십 행) 잡아 식별자 하나의 행이 여러 바구니에
흩어질 일이 없는지, 이은 차례가 틀과 같은지 본다. 비교(`diff.delta`)는 바구니 하나
(= 한꺼번에)와 여럿을 견준다.
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from typing import Any

import pytest

from relay_core import diff, sp, table
from relay_core.adapters.base import Fetched
from relay_core.mirror import Mirror
from relay_core.records import SourceRecord
from relay_core.state import MirrorFile, Workdir

RECIPE: dict[str, Any] = {
    "format": "sp-table/1",
    "title": "나눠 변환 시험",
    "dictionaries": {"region": ["KOR", "USA", "EUR"]},
    "parsers": {
        "code": {
            "column": "CODE",
            "separator": "_",
            "slots": [
                {"name": "series", "pattern": "^[A-Z]{2}-\\d+$"},
                {"name": "region", "dictionary": "region", "optional": True},
            ],
            "checks": [{"require": "region", "when_column": "MARKET", "in": ["EXPORT"]}],
        }
    },
    "types": [
        {
            "type_slug": "t_task",
            "key": {"column": "TASK"},
            "label": {"column": "TASK_NAME"},
            "fields": {
                "status": {"column": "STATUS", "map": {"IP": "진행", "DN": "완료"}},
                "owners": {"collect": {"column": "OWNER"}},
                "tags": {"join": {"column": "TAG"}, "separator": " / "},
                "phase": {"column": "PHASE"},
                "vendor": {
                    "column": "VENDOR",
                    "upper": True,
                    "match": {"keys": "@vendors.txt", "on_missing": "unresolved"},
                },
            },
        },
        {
            "type_slug": "t_model",
            "key": {"column": "MODEL_ID"},
            "label": {"column": "NAME"},
            "fields": {
                "task": {"key_of": "t_task"},
                "series": {"parser": "code", "slot": "series"},
                "region": {"parser": "code", "slot": "region"},
                "weight": {"column": "W", "map": {"L": 1, "H": 2.5, "<blank>": 0}},
                "site": {
                    "column": "SITE",
                    "match": {"keys": "@sites.txt", "on_missing": "keep", "prefix": True},
                },
                "made_on": {"column": "DATE", "date": True, "blank": ["-"]},
                "fixed": {"value": "고정"},
            },
        },
    ],
    "relations": [
        {
            "relation": "t_derived_from",
            "type_slug": "t_model",
            "src": {"key_of": "t_model"},
            "dst": {"column": "BASE"},
            "properties": {"strength": {"column": "STRENGTH"}},
            "evidence_note": {"value": "시험의 BASE 칸"},
        },
        {
            "relation": "t_supplied_by",
            "type_slug": "t_task",
            "src": {"key_of": "t_task"},
            "dst": {
                "column": "VENDOR",
                "match": {"keys": "@vendors.txt", "on_missing": "blank"},
            },
        },
    ],
}

COLUMNS = [
    "MODEL_ID", "NAME", "CODE", "MARKET", "TASK", "TASK_NAME", "STATUS", "OWNER", "TAG",
    "PHASE", "VENDOR", "W", "SITE", "DATE", "BASE", "STRENGTH",
]  # fmt: skip


def _rows(count: int, seed: int = 7) -> list[list[str]]:
    """식별자가 겹치는 행(같은 모델 두 번 · 과제 하나에 모델 여럿), 갈린 값, 빈 칸, 대응 없는
    값, 해석 못 하는 코드가 섞인 표."""
    pick = random.Random(seed)
    out: list[list[str]] = []
    for at in range(count):
        model = f"M-{pick.randrange(count * 3 // 4):05d}"  # 겹친다
        task = f"T-{pick.randrange(count // 6 + 1):04d}"
        region = pick.choice(["KOR", "USA", "EUR", "", "XXX"])
        code = f"GA-{pick.randrange(90)}" + (f"_{region}" if region else "")
        if at % 37 == 0:
            code = "깨진코드"
        out.append(
            [
                "" if at % 53 == 0 else model,
                f"모델 {model}" + ("*" if at % 41 == 0 else ""),  # 가끔 이름이 갈린다
                code,
                pick.choice(["DOMESTIC", "EXPORT"]),
                task,
                f"과제 {task}",
                pick.choice(["IP", "DN", "XX", ""]),
                pick.choice(["김", "이", "박", ""]),
                pick.choice(["가", "나", "다"]),
                "1차" if at % 29 else "2차",  # 과제마다 거의 같고 가끔 갈린다
                pick.choice(["acme", "Beta", "gamma", "", "ACME"]),
                pick.choice(["L", "H", "", "Z"]),
                pick.choice(["S1", "S1_KR", "S2", "S9", ""]),
                pick.choice(["2026-01-02", "2026.3.4", "-", "어제", ""]),
                f"M-{pick.randrange(count // 2):05d}" if at % 3 == 0 else "",
                pick.choice(["강", "약"]) if at % 5 else "",
            ]
        )
    return out


def _write(folder: Path, rows: list[list[str]], *, encoding: str = "utf-8-sig") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "vendors.txt").write_text("ACME\nBETA\n", encoding="utf-8")
    (folder / "sites.txt").write_text("S1_KR\nS2\n", encoding="utf-8")
    recipe = folder / "t.table.json"
    recipe.write_text(json.dumps(RECIPE, ensure_ascii=False), encoding="utf-8")
    source = folder / "t.source.csv"
    with source.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for index, row in enumerate(rows):
            writer.writerow(row)
            if index % 97 == 0:
                writer.writerow([])  # 빈 행 — 행 번호는 그대로 센다
    return recipe


def _files(run: Path) -> dict[str, bytes]:
    out = {}
    for one in sorted(run.rglob("*")):
        if one.is_file():
            body = one.read_bytes()
            if one.name == sp.pipeline.MANIFEST:
                manifest = json.loads(body)
                manifest.pop("created_at", None)
                body = json.dumps(manifest, ensure_ascii=False).encode()
            elif one.name == sp.table.REPORT:
                body = b"\n".join(
                    line for line in body.splitlines() if b"validate" not in line
                )
            out[one.relative_to(run).as_posix()] = body
    return out


@pytest.mark.parametrize("per", [10**6, 37, 5])
def test_나눠_변환해도_틀의_변환기와_파일이_같다(tmp_path: Path, per: int) -> None:
    recipe = _write(tmp_path, _rows(600))
    source = tmp_path / "t.source.csv"
    whole = sp.table.convert(recipe, source, tmp_path / "whole")
    parted = table.convert(recipe, source, tmp_path / "parted", bucket_rows=per)

    assert parted[0] == whole[0] is False  # 갈린 값 · 참조 대조 미해결이 있다
    got, want = _files(tmp_path / "parted"), _files(tmp_path / "whole")
    assert sorted(got) == sorted(want)
    unresolved = json.loads(want[sp.pipeline.UNRESOLVED])
    assert any("코어에 없는" in one["question"] for one in unresolved)
    assert any("행마다 값이 다릅니다" in one["question"] for one in unresolved)
    # 보고서까지 같다 — 일반에는 횟수가 같은 예시끼리의 차례가 다를 수 있지만(바구니마다
    # 처음 본 차례) 이 표에서는 같다.
    for name in want:
        assert got[name] == want[name], name
    assert not list(tmp_path.glob("*.spill")), "바구니는 끝나면 지운다"


def test_5천_줄이_넘는_타입도_같은_이름으로_나눠_쓴다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sp.pipeline, "MAX_ROWS", 40)
    recipe = _write(tmp_path, _rows(400, seed=3))
    source = tmp_path / "t.source.csv"
    sp.table.convert(recipe, source, tmp_path / "whole")
    table.convert(recipe, source, tmp_path / "parted", bucket_rows=23)
    got, want = _files(tmp_path / "parted"), _files(tmp_path / "whole")
    assert "objects/t_model-002.json" in want
    assert {k: v for k, v in got.items() if k.endswith(".json")} == {
        k: v for k, v in want.items() if k.endswith(".json")
    }


def test_원천_표의_검사와_인코딩은_틀과_같다(tmp_path: Path) -> None:
    recipe = _write(tmp_path, _rows(50), encoding="cp949")
    source = tmp_path / "t.source.csv"
    sp.table.convert(recipe, source, tmp_path / "whole")
    table.convert(recipe, source, tmp_path / "parted", bucket_rows=7)
    manifest = json.loads(_files(tmp_path / "parted")[sp.pipeline.MANIFEST])
    assert manifest["sources"][0]["encoding"] == "cp949"
    assert _files(tmp_path / "parted") == _files(tmp_path / "whole")

    bad = tmp_path / "bad.csv"
    bad.write_text("A,A\n1,2\n", encoding="utf-8")
    with pytest.raises(sp.table.Stop, match="같은 이름이 둘 이상"):
        table.read_table(bad)
    bad.write_text("A,B\n1,2,3\n", encoding="utf-8")
    with pytest.raises(sp.table.Stop, match="2행: 머리글보다 칸이 많습니다"):
        table.read_table(bad)


# --------------------------------------------------------------------------
# 나눠 비교
# --------------------------------------------------------------------------


def _run(
    folder: Path, objects: dict[str, list[list[dict[str, Any]]]], edges: list[Any]
) -> Path:
    """손으로 만든 실행 폴더 — 타입마다 파일 여럿(이름 차례가 곧 줄의 차례)."""
    (folder / "objects").mkdir(parents=True)
    (folder / "relations").mkdir()
    order = list(objects)
    for slug, files in objects.items():
        for index, rows in enumerate(files, start=1):
            body = {"type_slug": slug, "workspace_slug": f"ws-{index}", "rows": rows}
            (folder / "objects" / f"{slug}-{index:03d}.json").write_text(
                json.dumps(body, ensure_ascii=False), encoding="utf-8"
            )
    body = {"type_slug": "t_model", "rows": edges}
    (folder / "relations" / "t_link.json").write_text(json.dumps(body), encoding="utf-8")
    manifest = {"title": "t", "objects_order": order, "relations_order": ["t_link"]}
    (folder / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    pick = random.Random(11)
    keys = [f"K{at:04d}" for at in range(300)]

    def models(version: int) -> list[list[dict[str, Any]]]:
        rows = []
        for key in keys:
            if version and pick.random() < 0.1:
                continue  # 사라졌다
            note = pick.choice(["가", "나"]) if version else "가"
            rows.append({"key": key, "label": key, "note": note, "_source": {"row": 1}})
        if version:
            rows += [{"key": f"N{at}", "label": "새것"} for at in range(20)]
            rows.append({"key": "K0001", "label": "두 번째"})  # 같은 식별자가 또 — 뒤의 줄
        return [rows[:150], rows[150:]]

    def links(version: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for at in range(120):
            row: dict[str, Any] = {"src": keys[at], "relation": "t_link", "dst": keys[at + 1]}
            if at % 7 == 0:
                row.update(valid_from="2026-01-01", valid_to=None if version else "2026-12-31")
            if version and at % 11 == 0:
                continue  # 끊겼다
            if version and at % 5 == 0:
                row["evidence_note"] = "바뀐 근거"  # 정체는 그대로 · 붙은 값만 — 변경
            out.append(row)
        return out

    before = _run(tmp_path / "before", {"t_model": models(0), "t_site": [[]]}, links(0))
    after = _run(
        tmp_path / "after", {"t_model": models(1), "t_new": [[{"key": "X"}]]}, links(1)
    )
    return before, after


def test_나눠_비교해도_한꺼번에_견준_것과_같다(tmp_path: Path) -> None:
    before, after = _pair(tmp_path)
    whole = diff.delta(before, after, tmp_path / "whole", source="plm", bucket_rows=10**6)
    parted = diff.delta(before, after, tmp_path / "parted", source="plm", bucket_rows=13)

    assert whole.objects == parted.objects and whole.relations == parted.relations
    edges = whole.relations["t_model"]  # 선 묶음의 타입은 출발 타입
    assert whole.objects["t_model"].gone > 0 and edges.changed > 0 and edges.gone > 0
    assert list(whole.objects) == sorted(whole.objects)  # 타입 차례는 이름 차례(틀과 같다)
    assert _files(tmp_path / "parted") == _files(tmp_path / "whole")
    assert not list(tmp_path.glob("*.spill"))


def test_나눠_쓰는_파일은_한꺼번에_쓰는_것과_이름이_같다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sp.pipeline, "MAX_ROWS", 4)
    rows = {
        "a": [{"key": f"a{at}"} for at in range(4)],  # 꼭 한 파일
        "b": [{"key": f"b{at}"} for at in range(9)],  # 4 · 4 · 1
        "c": [{"key": "c0"}],
    }
    workspaces = {"a": "w", "b": None, "c": "w"}
    names = diff.write_batches(tmp_path / "want", "objects", rows, workspaces)
    stream = ([slug, at, row] for slug, items in rows.items() for at, row in enumerate(items))
    got = diff._write_sorted(tmp_path / "got", "objects", stream, workspaces)
    assert got == names == ["a", "b", "c"]
    assert _files(tmp_path / "got") == _files(tmp_path / "want")
    assert sorted(_files(tmp_path / "got")) == [
        "objects/a.json", "objects/b-001.json", "objects/b-002.json", "objects/b-003.json",
        "objects/c.json",
    ]  # fmt: skip


# --------------------------------------------------------------------------
# 미러 — 파일에서 흘려 이어 쓰기
# --------------------------------------------------------------------------


def _fetch(pick: random.Random, keys: list[str], *, full: bool) -> Fetched:
    records = []
    for key in pick.sample(keys, k=len(keys) if full else len(keys) // 5):
        if pick.random() < 0.1:
            records.append(SourceRecord(key=key, deleted=True))
        else:
            records.append(SourceRecord(key=key, values={"V": pick.choice("abc")}))
        if pick.random() < 0.05:  # 같은 키가 한 번 더 — 온 차례대로
            records.append(SourceRecord(key=key, values={"V": pick.choice("abcd")}))
    return Fetched(records=records, complete=pick.random() < 0.8, full=full)


def test_미러를_흘려_이어_써도_메모리의_반영과_같다(tmp_path: Path) -> None:
    pick = random.Random(5)
    keys = [f"K{at:03d}" for at in range(200)]
    memory = Mirror()
    work = Workdir(tmp_path, "p")
    for turn in range(12):
        fetched = _fetch(pick, keys, full=turn % 4 == 0)
        want = memory.apply(fetched)
        got, size = work.merge_mirror(fetched)
        assert got == want and size == len(memory), turn
        assert [(one.key, dict(one.values)) for one in work.mirror_rows()] == [
            (one.key, dict(one.values)) for one in memory.records()
        ]
    assert work.load_mirror().records() == memory.records()
    path = tmp_path / "s.csv"
    MirrorFile(work, len(memory)).write_csv(path)
    assert path.read_bytes() == memory.write_csv(tmp_path / "m.csv").read_bytes()


def test_옛_판의_미러도_읽고_차례가_어긋나면_멈춘다(tmp_path: Path) -> None:
    work = Workdir(tmp_path, "p")
    rows = [{"key": "B", "values": {"V": "2"}}, {"key": "A", "values": {"V": "1"}}]
    work.path("mirror.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    assert [one.key for one in work.mirror_rows()] == ["A", "B"]  # 들여쓴 한 덩이(0.2.2 까지)
    work.merge_mirror(Fetched(records=[SourceRecord(key="C", values={})], complete=True))
    assert (
        work.path("mirror.json")
        .read_text(encoding="utf-8")
        .splitlines()[1]
        .startswith('{"key": "A"')
    )

    lines = ["[", '{"key": "B", "values": {}},', '{"key": "A", "values": {}}', "]"]
    work.path("mirror.json").write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises(ValueError, match="차례가 어긋났습니다"):
        work.merge_mirror(Fetched(records=[], complete=True))
