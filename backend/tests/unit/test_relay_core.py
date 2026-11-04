"""엔진(`relay_core`) — 미러 · 변환 · 출력 비교.

변환은 틀의 `sp_table.convert()` 를 **그대로** 부른다(고치지 않은 사본). 여기서 보는 것은 그
앞뒤다: 미러가 증분 · 전량을 어떻게 반영하나, 출력 비교가 바뀐 것과 사라진 것만 남기나, 그
결과가 틀의 검증(`sp_pipeline.validate`)을 통과하나.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from relay_core import sp
from relay_core.adapters.base import Fetched
from relay_core.convert import convert
from relay_core.diff import delta
from relay_core.mirror import Mirror
from relay_core.records import SourceRecord

RECIPE: dict[str, Any] = {
    "format": "sp-table/1",
    "title": "개발모델 표(시험)",
    "types": [
        {
            "type_slug": "plm_model",
            "key": {"column": "MODEL_ID"},
            "label": {"column": "MODEL_NAME"},
            "fields": {"model_kind": {"column": "KIND", "map": {"A": "양산", "B": "선행"}}},
        }
    ],
    "relations": [
        {
            "relation": "plm_derived_from",
            "type_slug": "plm_model",
            "src": {"key_of": "plm_model"},
            "dst": {"column": "BASE"},
        }
    ],
}


def _model(key: str, name: str, kind: str, base: str = "") -> SourceRecord:
    return SourceRecord(
        key=key, values={"MODEL_ID": key, "MODEL_NAME": name, "KIND": kind, "BASE": base}
    )


@pytest.fixture
def recipe(tmp_path: Path) -> Path:
    path = tmp_path / "plm-models.table.json"
    path.write_text(json.dumps(RECIPE, ensure_ascii=False), encoding="utf-8")
    return path


def _rows(run: Path, folder: str) -> list[dict[str, object]]:
    loaded = sp.pipeline.load(run)
    batches = loaded.objects if folder == "objects" else loaded.relations
    return [row for batch in batches for row in batch.rows]


def test_미러는_증분이면_안_온_것을_두고_전량이면_뺀다() -> None:
    mirror = Mirror([_model("M1", "갤럭시", "A"), _model("M2", "폴드", "B")])
    got = mirror.apply(Fetched(records=[_model("M2", "폴드2", "B"), _model("M3", "링", "A")]))
    assert (got.added, got.changed, got.removed) == (1, 1, 0)
    assert [one.key for one in mirror.records()] == ["M1", "M2", "M3"]

    gone = mirror.apply(Fetched(records=[SourceRecord(key="M1", deleted=True)]))
    assert gone.removed == 1 and len(mirror) == 2

    # 전량을 끝까지 받았을 때만 「이번에 안 온 것」 을 지운 것으로 본다(부재 대조).
    half = mirror.apply(Fetched(records=[_model("M3", "링", "A")], full=True, complete=False))
    assert half.removed == 0 and len(mirror) == 2
    full = mirror.apply(Fetched(records=[_model("M3", "링", "A")], full=True, complete=True))
    assert full.removed == 1 and [one.key for one in mirror.records()] == ["M3"]

    assert Mirror.from_rows(mirror.to_rows()).records() == mirror.records()


def test_첫_적재는_전부_보내고_무덤이_없다(tmp_path: Path, recipe: Path) -> None:
    mirror = Mirror([_model("M1", "갤럭시", "A"), _model("M2", "갤럭시 엣지", "B", "M1")])
    made = convert(mirror, recipe, tmp_path / "run-1", work=tmp_path / "work")
    assert made.clean, made.report

    first = delta(None, made.run, tmp_path / "send-1", source="plm")
    assert first.objects["plm_model"].created == 2
    assert first.relations["plm_model"].created == 1
    rows = _rows(first.run, "objects")
    assert {row["key"]: row.get("model_kind") for row in rows} == {"M1": "양산", "M2": "선행"}
    assert not (first.run / "tombstones.json").exists()
    manifest = json.loads((first.run / "bundle.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "plm"


def test_다음_실행은_바뀐_것과_사라진_것만_보낸다(tmp_path: Path, recipe: Path) -> None:
    before = Mirror(
        [
            _model("M1", "갤럭시", "A"),
            _model("M2", "갤럭시 엣지", "B", "M1"),
            _model("M3", "노트", "A"),
        ]
    )
    applied = convert(before, recipe, tmp_path / "run-1", work=tmp_path / "work").run

    after = Mirror(before.records())
    after.apply(
        Fetched(
            records=[
                _model("M1", "갤럭시", "B"),  # 바뀜
                _model("M2", "갤럭시 엣지", "B"),  # 파생 선이 끊김
                SourceRecord(key="M3", deleted=True),  # 원천에서 지움
                _model("M4", "링", "A"),  # 새것
            ]
        )
    )
    current = convert(after, recipe, tmp_path / "run-2", work=tmp_path / "work").run
    send = delta(applied, current, tmp_path / "send-2", source="plm")

    counts = send.objects["plm_model"]
    assert (counts.created, counts.changed, counts.unchanged, counts.gone) == (1, 1, 1, 1)
    assert sorted(str(row["key"]) for row in _rows(send.run, "objects")) == ["M1", "M4"]
    graves = json.loads((send.run / "tombstones.json").read_text(encoding="utf-8"))
    assert graves["objects"] == [{"type_slug": "plm_model", "key": "M3", "merged_into": None}]
    assert graves["relations"] == [
        {"type_slug": "plm_model", "src": "M2", "relation": "plm_derived_from", "dst": "M1"}
    ]

    # 결과는 틀의 도구가 그대로 받는 실행 폴더다 — 보내기 전 검사를 통과한다.
    report = sp.pipeline.validate(sp.pipeline.load(send.run))
    assert report.errors == [], report.errors


def test_빈_칸은_바뀐_것이_아니고_비워진_칸은_지우기로_보낸다(
    tmp_path: Path, recipe: Path
) -> None:
    """SP 에서 빈 값은 「안 보냄」 이다 — 새 칸이 비어 있는 것은 바뀐 것이 아니고, 원천에서
    값이
    비워진 칸은 `\\null` 로 보내야 옛 값이 남지 않는다."""
    applied = convert(
        Mirror([_model("M1", "갤럭시", "A"), _model("M2", "엣지", "B")]),
        recipe,
        tmp_path / "a",
        work=tmp_path,
    ).run
    after = Mirror([_model("M1", "갤럭시", ""), _model("M2", "엣지", "B")])
    current = convert(after, recipe, tmp_path / "b", work=tmp_path).run
    sent = delta(applied, current, tmp_path / "send", source="plm")
    counts = sent.objects["plm_model"]
    assert (counts.changed, counts.unchanged) == (1, 1)
    (row,) = _rows(sent.run, "objects")
    assert row["key"] == "M1" and row["model_kind"] == "\\null"


def test_같은_입력이면_보낼_것이_없다(tmp_path: Path, recipe: Path) -> None:
    mirror = Mirror([_model("M1", "갤럭시", "A")])
    one = convert(mirror, recipe, tmp_path / "run-1", work=tmp_path / "work").run
    two = convert(mirror, recipe, tmp_path / "run-2", work=tmp_path / "work").run
    assert delta(one, two, tmp_path / "send", source="plm").empty


def test_묶음_칸을_실어_보낸다_사람이_고친_칸을_덮을지(tmp_path: Path, recipe: Path) -> None:
    run = convert(Mirror([_model("M1", "갤럭시", "A")]), recipe, tmp_path / "r", work=tmp_path)
    sent = delta(
        None, run.run, tmp_path / "send", source="plm", options={"human_edits": "overwrite"}
    )
    body = sp.pipeline.payload(sp.pipeline.load(sent.run))
    assert body["source"] == "plm" and body["human_edits"] == "overwrite"


def test_원천_이름_없이는_비교하지_않는다(tmp_path: Path, recipe: Path) -> None:
    run = convert(Mirror([_model("M1", "갤럭시", "A")]), recipe, tmp_path / "r", work=tmp_path)
    with pytest.raises(ValueError, match="source"):
        delta(None, run.run, tmp_path / "send", source=" ")


def test_엔진은_웹_앱을_모른다() -> None:
    """`relay_core` 는 DB 도 웹도 모른다 — CLI 와 웹 앱이 같은 함수를 부르려면 이쪽이 그쪽을
    import 하면 안 된다(방향이 거꾸로 서면 import 순서 하나로 워커가 안 뜬다)."""
    root = Path(__file__).resolve().parents[2] / "relay_core"
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if any(
            line.strip().startswith(("from app", "import app"))
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    ]
    assert offenders == []


def test_고정한_판을_안다() -> None:
    assert sp.version()["tag"].startswith("v")
