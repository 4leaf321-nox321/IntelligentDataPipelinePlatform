"""받기 점검 — 대량 삭제 · 빈 칸이면 **아무것도 반영하지 않고** 멈추고, 사람이 확인한 만큼만
지나간다. 그리고 실행마다 실행 추이에 한 줄이 남는다.

원천은 가짜 어댑터(받을 것을 시험이 정한다), 대상은 `test_relay_rest` 의 가짜 제출기.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from relay_core import intake, runner
from relay_core.adapters.base import Fetched
from relay_core.adapters.rest import SourceError
from relay_core.definition import DefinitionError, Pipeline, Target, check_body
from relay_core.intake import Accept, IntakeError, Policy
from relay_core.records import SourceRecord
from relay_core.state import Workdir
from tests.unit.test_relay_core import RECIPE
from tests.unit.test_relay_rest import FakeSubmitter


@dataclass
class _Source:
    full_every: int = 0
    deleted: str | None = None


class _Adapter:
    """받을 것을 시험이 정한다 — `full` 이면 전량(안 온 것은 삭제로 읽힌다)."""

    def __init__(self) -> None:
        self.source = _Source()
        self.records: list[SourceRecord] = []
        self.full = True
        self.calls = 0
        self.since: str | None = None
        self.failure: Exception | None = None

    def fetch(self, since: str | None, *, limit: int | None = None) -> Fetched:
        self.calls += 1
        self.since = since
        if self.failure is not None:
            raise self.failure
        return Fetched(
            records=list(self.records),
            watermark=f"w{self.calls}",
            complete=True,
            full=self.full,
        )


def _model(number: int, *, kind: str = "A", base: str = "") -> SourceRecord:
    key = f"M{number:03d}"
    return SourceRecord(
        key=key,
        values={"MODEL_ID": key, "MODEL_NAME": f"모델 {number}", "KIND": kind, "BASE": base},
    )


def _pipeline(tmp_path: Path, adapter: _Adapter) -> Pipeline:
    tmp_path.mkdir(parents=True, exist_ok=True)
    recipe = tmp_path / "recipe.table.json"
    recipe.write_text(json.dumps(RECIPE, ensure_ascii=False), encoding="utf-8")
    return Pipeline(
        slug="t",
        source="plm",
        adapter=adapter,  # type: ignore[arg-type]
        recipe=recipe,
        target=Target(name="sp", server="http://sp.test", token="x"),
        path=tmp_path / "pipeline.json",
    )


def _loaded(
    tmp_path: Path, count: int = 100, **over: Any
) -> tuple[_Adapter, Pipeline, Workdir, FakeSubmitter]:
    """원천 `count` 행을 첫 적재해 적용까지 해 둔다."""
    adapter = _Adapter()
    adapter.records = [_model(n, **over) for n in range(1, count + 1)]
    pipeline = _pipeline(tmp_path, adapter)
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    first = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert first.status == "planned", first.message
    assert runner.apply(pipeline, work, fake).status == "applied"  # type: ignore[arg-type]
    return adapter, pipeline, work, fake


def _trend(work: Workdir) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = work.read_json("trend.json")
    return rows


def test_전량에서_원천이_일부만_주면_반영하지_않고_멈춘다(tmp_path: Path) -> None:
    adapter, pipeline, work, fake = _loaded(tmp_path)
    previews = len(fake.previews)

    adapter.records = adapter.records[:20]  # 쪽 넘기기가 도중에 끊겼다
    held = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert held.status == "blocked" and "대량 삭제" in held.message, held.message
    assert "미러 100건 중 80건(80.0%)" in held.message
    state = work.load_state()
    assert state.held is not None and state.held["findings"]["deleted"] == 80
    assert state.history[-1]["event"] == "held"
    # 아무것도 반영하지 않았다 — 미러 · 워터마크 그대로, 임시 파일도 없다.
    assert work.mirror_size() == 100 and state.watermark == "w1"
    assert not work.path("mirror.json.tmp").exists()
    assert len(fake.previews) == previews
    last = _trend(work)[-1]
    assert last["status"] == "blocked" and last["held"] and last["mirror"] == 100

    # 원천이 그대로면 다시 멈춘다(확인하지 않았다).
    again = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert again.status == "blocked" and len(fake.previews) == previews

    # 사람이 확인하고 진행 — 그 삭제가 계획이 된다.
    accept = Accept.of(work.load_state().held, by="관리자")
    assert accept == Accept(deleted=80, columns=(), by="관리자")
    done = runner.run(pipeline, work, fake, accept=accept)  # type: ignore[arg-type]
    assert done.status == "planned", done.message
    assert done.counts["objects"]["plm_model"]["gone"] == 80
    state = work.load_state()
    assert state.held is None and work.mirror_size() == 20
    accepted = [one for one in state.history if one["event"] == "held_accepted"]
    assert accepted and accepted[-1]["by"] == "관리자"


def test_확인한_것보다_나빠지면_다시_멈춘다(tmp_path: Path) -> None:
    adapter, pipeline, work, fake = _loaded(tmp_path)
    adapter.records = adapter.records[:20]
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    accept = Accept.of(work.load_state().held)

    adapter.records = adapter.records[:10]  # 확인한 사이에 더 줄었다
    worse = runner.run(pipeline, work, fake, accept=accept)  # type: ignore[arg-type]
    assert worse.status == "blocked" and "90건" in worse.message, worse.message
    assert work.mirror_size() == 100


def test_작은_삭제는_지나간다(tmp_path: Path) -> None:
    adapter, pipeline, work, fake = _loaded(tmp_path)
    adapter.records = adapter.records[:95]  # 5% 이하
    assert runner.run(pipeline, work, fake).status == "planned"  # type: ignore[arg-type]

    # 작은 원천에서는 비율이 커도 `delete_rows` 건 이하면 지나간다.
    small, pipeline, work, fake = _loaded(tmp_path / "small", count=12)
    small.records = small.records[:2]
    assert runner.run(pipeline, work, fake).status == "planned"  # type: ignore[arg-type]


def test_칸_이름이_바뀌면_빈_칸으로_멈춘다(tmp_path: Path) -> None:
    adapter, pipeline, work, fake = _loaded(tmp_path)
    adapter.full = False
    # 원천의 칸 이름이 바뀌어 어댑터가 빈 값을 낸다 — 바뀐 다섯 행.
    adapter.records = [_model(n, kind="") for n in range(1, 6)]
    held = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert held.status == "blocked" and "빈 칸 KIND" in held.message, held.message
    blanks = work.load_state().held["findings"]["blanks"]  # type: ignore[index]
    assert blanks == [{"column": "KIND", "lost": 5, "filled": 100}]
    assert work.mirror_size() == 100

    # 칸이 아예 없어도(파일의 머리글이 바뀜) 같다.
    adapter.records = [
        dataclasses.replace(one, values={k: v for k, v in one.values.items() if k != "KIND"})
        for one in (_model(n) for n in range(1, 6))
    ]
    assert runner.run(pipeline, work, fake).status == "blocked"  # type: ignore[arg-type]

    # 확인하면 그 칸까지만 지나간다.
    accept = Accept.of(work.load_state().held)
    assert accept is not None and accept.columns == ("KIND",)
    done = runner.run(pipeline, work, fake, accept=accept)  # type: ignore[arg-type]
    assert done.status == "planned", done.message


def test_새_행만_비었거나_대개_빈_칸이면_지나간다(tmp_path: Path) -> None:
    adapter, pipeline, work, fake = _loaded(tmp_path)
    adapter.full = False
    # 새로 생긴 행은 아직 안 채운 칸이 있다 — 값을 잃는 기존 행이 없다.
    adapter.records = [_model(n, kind="") for n in range(101, 106)]
    assert runner.run(pipeline, work, fake).status == "planned"  # type: ignore[arg-type]

    # 대개 비어 있는 칸(미러의 절반 이하에만 값)은 받은 행 모두에서 비어도 이상하지 않다.
    based, pipeline, work, fake = _loaded(tmp_path / "base", count=100)
    based.full = False
    based.records = [_model(n, base="M001") for n in range(2, 12)]
    assert runner.run(pipeline, work, fake).status == "planned"  # type: ignore[arg-type]
    runner.apply(pipeline, work, fake)  # type: ignore[arg-type]
    based.records = [_model(n, base="") for n in range(2, 12)]
    later = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert later.status == "planned", later.message


def test_점검을_끄면_그대로_간다(tmp_path: Path) -> None:
    adapter, pipeline, work, fake = _loaded(tmp_path)
    adapter.records = adapter.records[:20]
    off = dataclasses.replace(pipeline, intake=None)
    assert runner.run(off, work, fake).status == "planned"  # type: ignore[arg-type]


def test_기준의_모양() -> None:
    assert intake.check(None) == Policy() and intake.check(True) == Policy()
    assert intake.check(False) is None
    assert intake.check({"delete_ratio": 0.2, "delete_rows": 0}) == Policy(
        delete_ratio=0.2, delete_rows=0
    )
    for wrong in (
        {"delete_ratio": 0},
        {"delete_ratio": 1.5},
        {"delete_ratio": True},
        {"delete_rows": -1},
        {"blank_rows": 0},
        {"x": 1},
        "on",
    ):
        with pytest.raises(IntakeError):
            intake.check(wrong)
    assert intake.label(None) == "미사용"
    assert "10건" in intake.label(Policy()) and "5%" in intake.label(Policy())

    body: dict[str, Any] = {
        "format": "relay-pipeline/1",
        "slug": "t",
        "source": "plm",
        "adapter": {
            "kind": "rest",
            "base_url": "http://plm.test",
            "path": "/api/models",
            "key": "id",
            "columns": {"MODEL_ID": "id"},
        },
        "target": {"server": "http://sp.test", "token_env": "T"},
        "intake": {"delete_ratio": 2},
    }
    with pytest.raises(DefinitionError, match="delete_ratio"):
        check_body(body)


def test_실행마다_추이에_한_줄이_남는다(tmp_path: Path) -> None:
    adapter, pipeline, work, fake = _loaded(tmp_path, count=10)
    first = _trend(work)[0]
    assert first["status"] == "planned" and first["received"] == 10
    assert first["mirror"] == 10 and first["added"] == 10 and first["full"] is True
    assert first["plan"] == {"created": 10, "changed": 0, "gone": 0}
    assert first["seconds"] >= 0 and "stage" not in first

    assert runner.run(pipeline, work, fake).status == "nothing"  # type: ignore[arg-type]
    assert _trend(work)[-1]["status"] == "nothing"

    # 실패한 실행도 — 어디까지 갔는지와 함께.
    adapter.failure = SourceError("원천에 닿지 않습니다")
    with pytest.raises(SourceError):
        runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    failed = _trend(work)[-1]
    assert failed["status"] == "failed" and failed["stage"] == "받기"


def test_전량_다시_받기는_워터마크를_두고_처음부터_받는다(tmp_path: Path) -> None:
    adapter, pipeline, work, fake = _loaded(tmp_path, count=100)
    adapter.full = False
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert adapter.since == "w1"  # 증분 — 지난 워터마크 뒤로
    adapter.full = True
    again = runner.run(pipeline, work, fake, full=True)  # type: ignore[arg-type]
    assert adapter.since is None and "전량을 다시 받습니다" in again.message
    assert work.load_state().watermark == "w3" and _trend(work)[-1]["full"] is True
    # 전량에서 일부만 오면 받기 점검이 그대로 지킨다.
    adapter.records = adapter.records[:2]
    held = runner.run(pipeline, work, fake, full=True)
    assert held.status == "blocked" and "대량 삭제" in held.message
