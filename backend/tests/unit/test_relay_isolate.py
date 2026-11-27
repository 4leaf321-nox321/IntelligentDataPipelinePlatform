"""작업마다 따로 프로세스(`relay_core.isolate`) — 자식에서 돌고, 예외를 그대로 옮기고, 상한을
넘으면 그 프로세스만 끝낸다. 부모(워커)는 그대로 다음 일을 한다.
"""

from __future__ import annotations

import json
import os
import pickle
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from relay_core import isolate


def test_자식_프로세스에서_돌고_결과와_단계별_메모리를_돌려준다() -> None:
    stages: list[str] = []
    pid, record = isolate.call(
        "relay_core.isolate._getpid", label="시험", on_stage=stages.append
    )
    assert pid != os.getpid()
    assert stages == ["시험"] and record["memory"]["시험"] > 0
    # 서버 메모리가 모자랄 때 이 작업부터 끝나게.
    score, _record = isolate.call("relay_core.isolate._oom_score")
    assert score == "1000"


def test_자식은_엔진만_불러온다() -> None:
    """워커의 앱(DB · 화면)까지 다시 불러오면 자식마다 50MB 가 더 든다."""
    loaded, _record = isolate.call("relay_core.isolate._modules")
    assert "relay_core.memory" in loaded
    assert not [one for one in loaded if one.split(".")[0] in ("app", "sqlalchemy", "fastapi")]


def test_넘길_수_없는_것이면_자식을_띄우기_전에_말한다() -> None:
    with pytest.raises((TypeError, AttributeError, pickle.PicklingError)):
        isolate.call("relay_core.isolate._hog", lambda: None)


def test_자식의_예외는_부모에서_같은_예외로() -> None:
    with pytest.raises(ValueError, match="원천에 연결할 수 없습니다"):
        isolate.call("relay_core.isolate._boom", "원천에 연결할 수 없습니다")


def test_상한을_넘으면_그_프로세스만_끝내고_부모는_계속한다() -> None:
    started = time.monotonic()
    with pytest.raises(isolate.MemoryLimitExceeded, match="작업 메모리 상한") as caught:
        isolate.call("relay_core.isolate._hog", 600, 10.0, limit_mb=300, label="변환")
    assert caught.value.stage == "변환" and caught.value.peak_mb > 300
    assert time.monotonic() - started < 8  # 다 잡을 때까지 기다리지 않는다
    # 부모는 멀쩡하다 — 다음 작업이 돈다.
    done, _record = isolate.call("relay_core.isolate._hog", 50, 0.0, limit_mb=300)
    assert done == 50 * isolate.MB


def test_부모의_알림이_멈추라고_하면_자식을_끝낸다() -> None:
    class Cancelled(Exception):
        pass

    def tick(_stage: str) -> None:
        raise Cancelled()

    started = time.monotonic()
    with pytest.raises(Cancelled):
        isolate.call("relay_core.isolate._hog", 10, 30.0, on_tick=tick, tick_seconds=0.5)
    assert time.monotonic() - started < 5


def test_워커가_죽으면_작업_프로세스도_끝난다() -> None:
    """남아서 돌면 되살아난 워커가 집은 같은 파이프라인의 작업과 겹친다."""
    worker = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from relay_core import isolate;isolate.call('relay_core.isolate._hog', 10, 60.0)",
        ],
        cwd=Path(__file__).resolve().parents[2],
    )
    children = Path(f"/proc/{worker.pid}/task/{worker.pid}/children")
    try:
        deadline = time.monotonic() + 20
        jobs: list[int] = []
        while not jobs and time.monotonic() < deadline:
            time.sleep(0.2)
            jobs = [
                int(one)
                for one in children.read_text().split()
                if "isolate._main" in Path(f"/proc/{one}/cmdline").read_text()
            ]
        assert jobs
        time.sleep(1.5)  # 자식이 알리기를 걸 때까지
    finally:
        worker.kill()
        worker.wait()
    deadline = time.monotonic() + 10
    while any(Path(f"/proc/{one}").exists() for one in jobs) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not any(Path(f"/proc/{one}").exists() for one in jobs)


def test_멈춤_신호는_작업_프로세스를_끝내지_않는다() -> None:
    """systemd 는 워커를 멈출 때 묶음(cgroup)의 프로세스 전부에 SIGTERM 을 보낸다. 워커는 하던
    작업을 끝내고 멈추는데 작업 프로세스가 그 자리에서 죽어, 업데이트 때마다 도는 실행이
    「메모리 부족일 수 있음」 으로 실패했다(2026-11-27 점검). 끝낼 때는 부모가 정한다."""
    script = (
        "import signal;from relay_core import isolate;"
        "signal.signal(signal.SIGTERM, lambda *_: None);"  # 워커처럼 — 하던 일을 끝낸다
        "print(isolate.call('relay_core.isolate._hog', 1, 3.0)[0], flush=True)"
    )
    worker = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        stdout=subprocess.PIPE,
        start_new_session=True,
    )
    children = Path(f"/proc/{worker.pid}/task/{worker.pid}/children")
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            time.sleep(0.2)
            alive = [
                one
                for one in children.read_text().split()
                if "isolate._main" in Path(f"/proc/{one}/cmdline").read_text()
            ]
            if alive:
                break
        time.sleep(1.0)  # 자식이 신호를 막을 때까지
        os.killpg(worker.pid, signal.SIGTERM)  # 묶음 전부에 — systemd 처럼
        out, _ = worker.communicate(timeout=30)
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()
    assert worker.returncode == 0, out
    assert out.strip() == str(isolate.MB).encode()


def test_결과_없이_끝난_까닭은_종료_코드로_가린다() -> None:
    assert "메모리가 부족" in isolate._ended(-signal.SIGKILL, "변환", 0)
    assert "메모리" not in isolate._ended(-signal.SIGTERM, "변환", 0)
    assert "결과 없이 종료" in isolate._ended(1, "변환", 0)


BEARER = {"kind": "bearer", "token_env": "T"}
COLUMNS = {"N": "name"}
ADAPTERS: list[dict[str, Any]] = [
    {"kind": "odata", "service_url": "http://x/o", "entity_set": "A", "key": "K"},
    {"kind": "rest", "base_url": "http://x", "path": "/r", "key": "id", "auth": BEARER},
    {"kind": "xml", "url": "http://x/ws", "rows_xpath": "//r", "key": "id"},
    {"kind": "file", "location": "/drop/*.csv", "key": "K"},
    {"kind": "push", "key": "K", "auth": BEARER},
    {
        "kind": "kafka",
        "bootstrap": "b:9092",
        "topic": "t",
        "group_id": "relay-t",
        "key": "id",
        "schema_registry": {"url": "http://sr:8081"},
    },
]


@pytest.mark.parametrize("spec", ADAPTERS, ids=lambda one: str(one["kind"]))
def test_어느_원천의_정의든_작업_프로세스로_넘어간다(spec: dict[str, Any]) -> None:
    from relay_core.definition import ADAPTERS as KINDS
    from relay_core.definition import build_adapter

    assert {str(one["kind"]) for one in ADAPTERS} == set(KINDS)  # 새 어댑터도 여기에
    body = spec if spec["kind"] == "file" else {**spec, "columns": COLUMNS}
    made = build_adapter(body, {"T": "secret"}, file_roots=[Path("/drop")])
    again = pickle.loads(pickle.dumps(made))
    assert type(again) is type(made) and again.source == made.source
    # 설정 말고 넘어가는 것 — 읽어도 되는 폴더, 수신함.
    for name in ("roots", "inbox"):
        assert getattr(again, name, None) == getattr(made, name, None)


def test_실행이_작업_프로세스에서_끝까지_가고_실패도_그대로_온다(tmp_path: Path) -> None:
    """원천 수신 → 변환 → 비교까지 자식에서 — 대상에 닿지 못하면 그 오류가 부모로 온다."""
    from relay_core.definition import Pipeline, Target, build_adapter
    from relay_core.state import Workdir
    from relay_core.submit import SubmitError

    drop = tmp_path / "drop"
    drop.mkdir()
    (drop / "a.csv").write_text("K,N\nA1,하나\nA2,둘\n", encoding="utf-8")
    recipe = tmp_path / "recipe.table.json"
    recipe.write_text(
        json.dumps(
            {
                "format": "sp-table/1",
                "title": "시험",
                "types": [
                    {"type_slug": "t", "key": {"column": "K"}, "label": {"column": "N"}}
                ],
            }
        ),
        encoding="utf-8",
    )
    source = {"kind": "file", "location": str(drop / "a.csv"), "key": "K"}
    pipeline = Pipeline(
        slug="p",
        source="plm",
        adapter=build_adapter(source, {}, file_roots=[drop]),
        recipe=recipe,
        target=Target(name="sp", server="http://127.0.0.1:9", token="t"),
        path=tmp_path / "pipeline.json",
    )
    work = Workdir(tmp_path / "work", "p")
    stages: list[str] = []
    with pytest.raises((SubmitError, OSError, ConnectionError)):
        isolate.call("relay_core.runner.run", pipeline, work, on_stage=stages.append)
    assert stages == ["준비", "수신", "미러", "변환", "비교", "미리 보기"]
    row = work.read_json("trend.json")[-1]
    assert row["status"] == "failed" and row["stage"] == "미리 보기"
    assert row["memory"]["변환"] > 0 and work.mirror_size() == 2
