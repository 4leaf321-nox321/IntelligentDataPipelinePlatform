"""거꾸로 보기 — 대상 객체 하나의 값이 어느 원천 행 · 칸 · 레시피 규칙에서 왔나.

실제 한 바퀴(실행 → 적용)로 기준을 만든다. 과제(`plm_task`)는 모델 행 여럿에서 **모이므로**
원천 행이 여럿이고, 모델은 한 행이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from relay_core import runner
from relay_core.adapters.base import Fetched
from relay_core.definition import Pipeline, Target
from relay_core.records import SourceRecord
from relay_core.state import Workdir
from relay_core.trace import TraceError, trace
from tests.unit.test_relay_rest import FakeSubmitter

RECIPE: dict[str, Any] = {
    "format": "sp-table/1",
    "title": "과제 · 모델(시험)",
    "types": [
        {
            "type_slug": "plm_task",
            "key": {"column": "TASK_CODE"},
            "label": {"column": "TASK_NAME"},
            "fields": {
                "task_status": {"column": "TASK_STATUS", "map": {"IP": "진행", "DN": "완료"}}
            },
        },
        {
            "type_slug": "plm_model",
            "key": {"column": "MODEL_ID"},
            "label": {"column": "MODEL_NAME"},
            "fields": {
                "task": {"key_of": "plm_task"},
                "model_kind": {"column": "KIND", "map": {"A": "양산", "B": "선행"}},
            },
        },
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


def _row(model: str, name: str, kind: str, task: str, base: str = "") -> SourceRecord:
    values = {
        "MODEL_ID": model,
        "MODEL_NAME": name,
        "KIND": kind,
        "BASE": base,
        "TASK_CODE": task,
        "TASK_NAME": f"{task} 과제",
        "TASK_STATUS": "IP",
    }
    return SourceRecord(key=model, values=values)


ROWS = [
    _row("M1", "갤럭시", "A", "T1"),
    _row("M2", "엣지", "B", "T1", base="M1"),
    _row("M3", "노트", "A", "T2"),
]


@dataclass
class _Source:
    full_every: int = 0
    deleted: str | None = None


class _Adapter:
    source = _Source()

    def fetch(self, since: str | None, *, limit: int | None = None) -> Fetched:
        return Fetched(records=list(ROWS), complete=True, full=True)


@pytest.fixture
def applied(tmp_path: Path) -> tuple[Workdir, Path]:
    recipe = tmp_path / "recipe.table.json"
    recipe.write_text(json.dumps(RECIPE, ensure_ascii=False), encoding="utf-8")
    pipeline = Pipeline(
        slug="t",
        source="plm",
        adapter=_Adapter(),  # type: ignore[arg-type]
        recipe=recipe,
        target=Target(name="sp", server="http://sp.test", token="x"),
        path=tmp_path / "pipeline.json",
    )
    work = Workdir(tmp_path / "work", "t")
    fake: Any = FakeSubmitter()
    assert runner.run(pipeline, work, fake).status == "planned"
    assert runner.apply(pipeline, work, fake).status == "applied"
    return work, recipe


def test_모인_객체는_원천_행을_모두_보이고_규칙과_원래_값을_붙인다(
    applied: tuple[Workdir, Path],
) -> None:
    work, recipe = applied
    got = trace(work, recipe, "plm_task", "T1")

    assert got.found and got.label == "T1 과제" and got.basis == "기준 실행의 원천 표"
    assert got.recipe_basis == "기준 실행의 레시피" and not got.problems
    assert got.rows_total == 2 and [one["row"] for one in got.rows] == [2, 3]
    status = next(one for one in got.fields if one.name == "task_status")
    assert status.value == "진행" and status.columns == ["TASK_STATUS"]
    assert "대응 IP→진행" in status.rule
    assert status.seen == [{"value": "IP", "rows": [2, 3]}]
    assert got.rows[0]["values"] == {
        "TASK_CODE": "T1",
        "TASK_NAME": "T1 과제",
        "TASK_STATUS": "IP",
    }


def test_한_행의_객체는_참조_칸의_출처와_관계의_양쪽을_보인다(
    applied: tuple[Workdir, Path],
) -> None:
    work, recipe = applied
    edge = trace(work, recipe, "plm_model", "M2")
    assert edge.rows_total == 1 and edge.rows[0]["row"] == 3
    task = next(one for one in edge.fields if one.name == "task")
    assert task.rule == "plm_task 의 식별자" and task.columns == ["TASK_CODE"]
    assert edge.relations == [
        {"relation": "plm_derived_from", "direction": "out", "other": "M1"}
    ]
    base = trace(work, recipe, "plm_model", "M1")
    assert base.relations == [
        {"relation": "plm_derived_from", "direction": "in", "other": "M2"}
    ]


def test_마지막_대조의_상태를_함께_말한다(applied: tuple[Workdir, Path]) -> None:
    work, recipe = applied
    assert trace(work, recipe, "plm_model", "M1").reconcile is None  # 아직 대조 안 함
    # 시험의 적용은 지금 시각에 일어난다 — 대조 시각을 날짜로 박으면 그 시각이 지나 「적용
    # 전의 대조」(stale)가 된다(2026-11-06 03:00Z 에 실제로 깨졌다). 늘 뒤 · 늘 앞인 시각으로.
    report: dict[str, Any] = {
        "at": "2999-01-01T00:00:00Z",
        "types": {
            "plm_model": {
                "missing": ["M3"],
                "deprecated": [],
                "changed": {"M2": ["label"]},
                "extra": [],
            }
        },
    }
    work.write_json("reconcile.json", report)
    assert trace(work, recipe, "plm_model", "M2").reconcile == {
        "state": "changed",
        "at": "2999-01-01T00:00:00Z",
        "fields": ["label"],
        "stale": False,
    }
    assert trace(work, recipe, "plm_model", "M3").reconcile["state"] == "missing"  # type: ignore[index]
    assert trace(work, recipe, "plm_model", "M1").reconcile["state"] == "same"  # type: ignore[index]
    work.write_json("reconcile.json", {**report, "at": "2000-01-01T00:00:00Z"})
    assert trace(work, recipe, "plm_model", "M1").reconcile["stale"] is True  # type: ignore[index]


def test_기준_뒤에_레시피가_바뀌어도_기준의_규칙으로_읽는다(
    applied: tuple[Workdir, Path],
) -> None:
    work, recipe = applied
    changed = dict(RECIPE)
    changed["types"] = [
        {**one, "fields": {**(one.get("fields") or {}), "model_kind": {"column": "KIND"}}}
        if one["type_slug"] == "plm_model"
        else one
        for one in RECIPE["types"]
    ]
    recipe.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    kind = next(
        one
        for one in trace(work, recipe, "plm_model", "M1").fields
        if one.name == "model_kind"
    )
    assert "대응 A→양산" in kind.rule  # 지금 레시피에는 대응이 없다


def test_원천_표가_없으면_지금_미러로_찾고_그렇다고_적는다(
    applied: tuple[Workdir, Path],
) -> None:
    work, recipe = applied
    for one in work.path("work").glob("*.source.csv"):
        one.unlink()
    got = trace(work, recipe, "plm_task", "T1")
    assert got.basis == "지금 미러" and got.rows_total == 2
    assert any("지금 미러" in one for one in got.problems)


def test_모르는_타입과_어디에도_없는_식별자는_까닭을_말한다(
    applied: tuple[Workdir, Path],
) -> None:
    work, recipe = applied
    with pytest.raises(TraceError, match="plm_task · plm_model"):
        trace(work, recipe, "plm_part", "X")
    with pytest.raises(TraceError, match="찾지 못했습니다"):
        trace(work, recipe, "plm_model", "M404")


def test_지금_기준보다_앞선_대조는_그렇다고_적는다(applied: tuple[Workdir, Path]) -> None:
    work, recipe = applied
    work.write_json("reconcile.json", {"at": "2000-01-01T00:00:00Z", "types": {}})
    got = trace(work, recipe, "plm_model", "M1").reconcile
    assert got is not None and got["stale"] and got["state"] == "unknown"


def test_5천_줄이_넘어_나뉜_타입에서도_찾는다(tmp_path: Path) -> None:
    """변환기 · SP 도구는 5,000줄이 넘는 타입을 `<slug>-001.json` … 으로 나눈다 — 실제 PLM 의
    모델은 그보다 많다(2026-11-06 대용량 시험에서 1만 행의 모델을 하나도 못 찾았다)."""
    from relay_core.trace import _baseline_row

    objects = tmp_path / "run" / "objects"
    objects.mkdir(parents=True)
    for index, keys in enumerate((["M1", "M2"], ["M3"]), start=1):
        body = {"type_slug": "plm_model", "rows": [{"key": key, "label": key} for key in keys]}
        (objects / f"plm_model-{index:03d}.json").write_text(
            json.dumps(body), encoding="utf-8"
        )
    other = {"type_slug": "plm_model_kind", "rows": [{"key": "M3", "label": "남의 타입"}]}
    (objects / "plm_model_kind.json").write_text(json.dumps(other), encoding="utf-8")
    assert _baseline_row(tmp_path / "run", "plm_model", "M3") == {"key": "M3", "label": "M3"}
    assert _baseline_row(tmp_path / "run", "plm_model", "M9") is None
