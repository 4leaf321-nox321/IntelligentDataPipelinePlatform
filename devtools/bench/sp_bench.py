"""대용량 시험 — **진짜 SP**(시험 인스턴스)로 첫 적재 · 증분 · 대조를 잰다.

    devtools/bench/sp_bench.sh 20000 50000   # 크기마다 시험 인스턴스를 새로 세운다

`relay_bench.py` 는 대상 SP 를 가짜로 두고 엔진만 잰다. 이것은 같은 모양의 원천 N 행을 진짜
SP 에 미리 보고 · 적용하고, 1% 증분을 다시 보내고, 대조가 대상을 끝까지 읽는 시간을 잰다.
SP 의 작업 시간(대기 · 처리)은 그 작업의 기록에서, 워커의 최대 메모리는 `/proc` 에서 읽는다.

환경: `SANDBOX_URL` · `SANDBOX_WORK`(토큰 `relay.token`, 워커 PID `worker.pid`).
작업 폴더는 `SANDBOX_WORK/relay` — 크기마다 지운다(시험 인스턴스도 새로 세운다).
"""

from __future__ import annotations

import os
import resource
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from relay_bench import RECIPE, Adapter, body_size, rows  # noqa: E402

from relay_core import parts, reconcile, runner  # noqa: E402
from relay_core.adapters.base import Fetched  # noqa: E402
from relay_core.definition import Pipeline, Target  # noqa: E402
from relay_core.state import Workdir  # noqa: E402
from relay_core.submit import Submitter  # noqa: E402

URL = os.environ["SANDBOX_URL"]
WORK = Path(os.environ["SANDBOX_WORK"])
TOKEN = (WORK / "relay.token").read_text(encoding="utf-8").strip()
#: 조각 상한(MB) — `PART_MB=32 sp_bench.sh 200000` 처럼 바꿔 조각 크기에 따른 SP 시간을 견준다.
PART_MB = int(os.environ.get("PART_MB") or parts.DEFAULT_MB)


def _sp_time(since: datetime) -> str:
    """`since` 뒤에 생긴 SP 작업들(나눠 보내면 조각마다 미리 보기 · 적용)의 대기 · 처리 합."""
    import httpx

    got = httpx.get(
        f"{URL}/api/jobs?limit=100", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=60
    )
    got.raise_for_status()
    waited = took = 0.0
    count = 0
    for one in got.json().get("items", []):
        made, began, ended = (
            datetime.fromisoformat(one[name]) if one.get(name) else None
            for name in ("created_at", "started_at", "finished_at")
        )
        if made is None or made < since or one.get("kind") != "bundle_import":
            continue
        count += 1
        if began and ended:
            waited += (began - made).total_seconds()
            took += (ended - began).total_seconds()
    return f"SP 작업 {count}개 · 대기 {waited:.0f}초 · 처리 {took:.0f}초"


def _now() -> datetime:
    return datetime.now().astimezone()


def _worker_peak() -> str:
    pid = (WORK / "worker.pid").read_text(encoding="utf-8").strip()
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return f"{int(line.split()[1]) / 1024:,.0f}MB"
    return "?"


def step(name: str, started: float, extra: str = "") -> float:
    now = time.perf_counter()
    print(f"  {name:<14} {now - started:8.1f}초  {extra}", flush=True)
    return now


def main(n: int) -> None:
    root = WORK / "relay"
    shutil.rmtree(root, ignore_errors=True)
    adapter = Adapter()
    pipeline = Pipeline(
        slug="bench",
        source="plm",
        adapter=adapter,  # type: ignore[arg-type]
        recipe=RECIPE,
        target=Target(name="sp", server=URL, token=TOKEN, part_mb=PART_MB),
        path=root / "p.json",
    )
    work = Workdir(root, "bench")
    submit = Submitter(pipeline.target)
    print(f"N = {n:,} 행 — 대상 {URL} · 조각 상한 {PART_MB}MB", flush=True)

    t, since = time.perf_counter(), _now()
    adapter.next = Fetched(records=rows(n), watermark="w1", complete=True, full=True)
    first = runner.run(pipeline, work, submit)
    pending = work.load_state().pending
    assert pending is not None, first.message
    size = body_size(work.path(pending.send))
    shown = f"조각 {len(pending.parts)}개" if pending.parts else "나누지 않음"
    t = step(
        "첫 미리 보기",
        t,
        f"{first.status} · 본문 {size / 1e6:.1f}MB · {shown} · {_sp_time(since)}",
    )
    if first.status != "planned":
        print(f"  멈춤: {first.message[:400]}", flush=True)
        return
    since = _now()
    done = runner.apply(pipeline, work, submit)
    t = step("첫 적용", t, f"{done.status} · {_sp_time(since)}")
    if done.status != "applied":
        print(f"  멈춤: {done.message[:400]}", flush=True)
        return

    changed = [r for r in rows(n, 1) if r.values["MODEL_NAME"].endswith("v2")]
    adapter.next = Fetched(records=changed, watermark="w2", complete=True, full=False)
    since = _now()
    again = runner.run(pipeline, work, submit)
    pending = work.load_state().pending
    assert pending is not None, again.message
    size = body_size(work.path(pending.send))
    t = step(
        "1% 미리 보기", t, f"{again.status} · 본문 {size / 1e6:.2f}MB · {_sp_time(since)}"
    )
    since = _now()
    runner.apply(pipeline, work, submit)
    t = step("1% 적용", t, _sp_time(since))

    report = reconcile.compare(pipeline, work)
    t = step("대조", t, report.summary()[:90])
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"  최대 메모리 relay {peak:,.0f}MB · SP 워커 {_worker_peak()}", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10_000)
