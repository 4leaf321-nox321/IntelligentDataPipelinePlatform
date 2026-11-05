"""대조 — 대상에 **지금 실제로 있는 것**과 지난번 적용된 출력(기준)을 견주고, 어긋난 줄만 다시
보내는 보정 계획을 올린다.

원천 · 제출기는 `test_relay_rest` 의 가짜 그대로. 대상의 목록은 가짜 읽개(`FakeReader`)가
준다 — 진짜 SP 로 끝까지 도는 것은 `devtools/`(샘플 PLM + SP 시험 인스턴스)가 본다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from relay_core import reconcile, runner
from relay_core.reconcile import Report, _Refs, compare, resync
from relay_core.state import Workdir
from tests.unit.test_relay_rest import ROWS, FakeSubmitter, _pipeline


class FakeReader:
    def __init__(
        self, items: dict[str, list[dict[str, Any]] | None], managed: dict[str, Any]
    ) -> None:
        self.items = items
        self.managed = managed

    def objects(self, type_slug: str) -> list[dict[str, Any]] | None:
        return self.items.get(type_slug)

    def managed_by(self) -> dict[str, str | None]:
        return dict(self.managed)


def _item(key: str, label: str, kind: str, status: str = "active") -> dict[str, Any]:
    return {
        "id": f"id-{key}",
        "key": key,
        "label": label,
        "status": status,
        "properties": {"model_kind": kind},
        "ref_labels": {},
    }


#: 기준 그대로의 대상 — `ROWS` 를 레시피(A → 양산, B → 선행)로 옮긴 것.
SAME = [
    _item("M1", "갤럭시", "양산"),
    _item("M2", "엣지", "선행"),
    _item("M3", "노트", "양산"),
]


def _applied(tmp_path: Path) -> tuple[Any, Workdir, Any]:
    work = Workdir(tmp_path / "work", "t")
    fake: Any = FakeSubmitter()
    pipeline = _pipeline(tmp_path, [dict(one) for one in ROWS])
    assert runner.run(pipeline, work, fake).status == "planned"
    assert runner.apply(pipeline, work, fake).status == "applied"
    return pipeline, work, fake


def test_기준이_없으면_견주지_않는다(tmp_path: Path) -> None:
    work = Workdir(tmp_path / "work", "t")
    pipeline = _pipeline(tmp_path, [dict(one) for one in ROWS])
    fake: Any = FakeSubmitter()
    report = compare(pipeline, work, FakeReader({}, {}), fake)
    assert report.problems and "적용한 것이 없습니다" in report.summary()


def test_대상과_같으면_어긋남이_없고_이력에_남는다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    reader = FakeReader({"plm_model": SAME}, {"plm_model": "plm"})
    report = compare(pipeline, work, reader, fake)
    assert report.drift == 0 and not report.problems, report.summary()
    assert "같습니다(객체 3건)" in report.summary()
    last = work.load_state().history[-1]
    assert last["event"] == "reconciled" and last["drift"] == 0
    assert reconcile.last_report(work) == Report.from_json(work.read_json(reconcile.REPORT))


def test_빠짐_안씀_다름_대상에만을_가른다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    items = [
        _item("M2", "엣지", "선행", status="deprecated"),
        _item("M3", "노트 2", "양산"),
        _item("M9", "누가 만든 것", "양산"),
        _item("M8", "예전에 지운 것", "양산", status="deprecated"),  # 이미 무덤 — 셈에서 뺀다
    ]
    report = compare(pipeline, work, FakeReader({"plm_model": items}, {}), fake)
    one = report.types["plm_model"]
    assert (one.missing, one.deprecated, one.changed, one.extra) == (
        ["M1"],
        ["M2"],
        {"M3": ["label"]},
        ["M9"],
    )
    assert report.drift == 4 and not one.managed
    assert "어긋남 4건(빠짐 1 · 안 씀 1 · 다름 1 · 대상에만 1)" in report.summary()


def test_보정_계획은_어긋난_줄과_닿는_선을_싣고_잠긴_타입이면_무덤도(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    baseline = work.load_state().applied_run
    items = [
        _item("M2", "엣지", "선행", status="deprecated"),
        _item("M3", "노트", "선행"),
        _item("M9", "되돌린 백업에서 살아난 것", "양산"),
    ]
    reader = FakeReader({"plm_model": items}, {"plm_model": "plm"})
    outcome = resync(pipeline, work, reader, fake)
    assert outcome.status == "planned", outcome.message
    assert "보정 계획" in outcome.message

    sent = fake.previews[-1]
    rows = json.loads((sent / "objects" / "plm_model.json").read_text(encoding="utf-8"))[
        "rows"
    ]
    by_key = {row["key"]: row for row in rows}
    assert set(by_key) == {"M1", "M2", "M3"}
    assert by_key["M2"]["status"] == "active" and "status" not in by_key["M1"]
    graves = json.loads((sent / "tombstones.json").read_text(encoding="utf-8"))
    assert graves["objects"] == [{"type_slug": "plm_model", "key": "M9", "merged_into": None}]
    edges = json.loads((sent / "relations" / "plm_model.json").read_text(encoding="utf-8"))
    assert [(row["src"], row["dst"]) for row in edges["rows"]] == [("M2", "M1")]
    manifest = json.loads((sent / "bundle.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "plm" and "보정 계획" in manifest["title"]

    state = work.load_state()
    assert state.pending is not None and state.pending.origin == "reconcile"
    assert state.pending.run == baseline  # 적용돼도 기준은 그대로다(원천이 안 바뀌었다)
    assert outcome.counts["objects"]["plm_model"] == {
        "created": 1,
        "changed": 2,
        "unchanged": 0,
        "gone": 1,
    }

    # 기다리는 계획이 있으면 또 올리지 않는다 — 사람이 보던 계획이 사라진다
    assert resync(pipeline, work, reader, fake).status == "waiting"
    assert runner.apply(pipeline, work, fake).status == "applied"
    assert work.load_state().applied_run == baseline


def test_잠기지_않은_타입의_대상에만은_사람이_정한다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    reader = FakeReader({"plm_model": [*SAME, _item("M9", "사람이 만든 것", "양산")]}, {})
    outcome = resync(pipeline, work, reader, fake)
    assert outcome.status == "nothing" and "사람이 정합니다" in outcome.message
    assert len(fake.previews) == 1  # 처음 적재뿐


def test_대상에_타입이_없으면_멈춘다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    outcome = resync(pipeline, work, FakeReader({"plm_model": None}, {}), fake)
    assert outcome.status == "blocked" and "타입이 없습니다" in outcome.message


def test_값은_모양이_달라도_같으면_같다() -> None:
    refs = _Refs()
    refs.add([{"id": "u-1", "key": "T-1"}])
    same = refs.same
    assert same("12.50", 12.5, {}) and same("1,200", 1200, {}) and not same("12", 13, {})
    assert same("true", True, {}) and not same("false", True, {})
    assert same("2026-01-05 00:00:00", "2026-01-05T00:00:00Z", {})
    assert same("a;b", ["b", "a"], {})
    # 참조 — 같은 대조에서 받은 객체면 식별자로, 아니면 이름으로, 둘 다 아니면 모른다
    assert same("T-1", "u-1", {}) is True
    assert same("T-2", "u-1", {}) is False
    assert same("서울 시험소", "u-9", {"u-9": "서울 시험소"}) is True
    assert same("S-1", "u-9", {"u-9": "서울 시험소"}) is None
