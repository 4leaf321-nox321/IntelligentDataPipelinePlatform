"""워커의 박동 — **쉬고 있어도 뛴다.**

화면(작업 · 서버)과 홈의 「남은 일」 은 `worker_heartbeats.last_seen` 으로 워커가 살아 있는지
본다. 박동이 `worker_stale_seconds`(5분)보다 오래 없으면 「꺼짐」 이다.

예전에는 바퀴마다 「방금 박동했다」 로 시계를 되감았다 — 작업이 없어 박동을 **안 남긴**
바퀴까지. 쉬는 워커는 바퀴가 몇 초라 30초에 영영 못 닿았고, 기동 때 한 번 뛴 뒤로 박동이
멎었다. 그래서 작업이 끝나고 5분이 지나면 **살아서 표를 두드리는 워커가 「꺼짐」 으로**
보였다. 시계를 가짜로 돌려 그 10분을 흉내 낸다 — DB 는 안 쓴다.
"""

from __future__ import annotations

import contextlib
import threading
from itertools import pairwise
from typing import Any

import pytest

from app import worker as worker_module
from app.config import get_settings
from app.modules.audit import retention
from app.modules.jobs import services as job_services
from app.modules.server import performance


class _Clock:
    """`time` 대신 — 잠들면 그만큼 시계가 간다. 끝 시각을 넘으면 워커를 세운다."""

    def __init__(self, worker: worker_module.Worker, until: float) -> None:
        self.now = 1_000.0
        self.worker = worker
        self.until = self.now + until

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.now >= self.until:
            self.worker.stop = True

    # 빈 워커의 기다림(`Waker` — 작업이 들어오면 바로 깬다)도 이 시계로 — 진짜는 DB 연결로
    # 기다려 시계가 안 가고, 워커가 영영 안 선다.
    def wait(self, seconds: float) -> None:
        self.sleep(seconds)

    def close(self) -> None:
        pass


def _run(monkeypatch: pytest.MonkeyPatch, *, minutes: int, jobs: set[int]) -> list[float]:
    """워커를 `minutes` 분 돌리고 박동이 남은 시각들을 돌려준다 — **끝 시각을 마지막에 붙여.**

    끝 시각까지 넣는 까닭: 고장은 「마지막 박동 뒤로 끝까지 조용한 것」 이었다. 박동 사이의
    간격만 재면 그 꼬리를 못 본다(처음 쓴 시험이 그래서 고장 난 워커에서도 통과했다).

    `jobs` 는 작업을 집는 바퀴 번호들이다(나머지 바퀴는 빈 표).
    """
    worker = worker_module.Worker()
    clock = _Clock(worker, until=minutes * 60)
    beats: list[float] = []
    turn = {"n": 0}

    def process_one(_worker_id: str) -> bool:
        turn["n"] += 1
        if turn["n"] in jobs:
            # 진짜 `process_one` 처럼 — 작업을 돌리면 끝에서 박동을 남긴다.
            clock.now += 3.0
            beats.append(clock.now)
            return True
        return False

    # 워커가 부르는 것은 이 모듈들의 속성이다 — 같은 모듈 객체를 고친다.
    services: Any = job_services
    monkeypatch.setattr(worker_module, "time", clock)
    monkeypatch.setattr(worker, "waker", clock)
    monkeypatch.setattr(worker_module, "SessionLocal", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(services, "heartbeat", lambda *_a: beats.append(clock.now))
    monkeypatch.setattr(services, "process_one", process_one)
    monkeypatch.setattr(services, "recover_stale", lambda _db: 0)
    for name in (
        "purge_expired_files",
        "purge_old_jobs",
    ):
        monkeypatch.setattr(services, name, lambda _db: 0)
    # 요청 시간 기록 · 접근 로그 정리도 — 흉내 안 내면 가짜 세션(None)에서 바퀴마다 예외가 나
    # 정리 시각이 안 옮겨지고, 시험이 보려는 박동과 상관없는 길을 돈다.
    monkeypatch.setattr(performance, "purge_old_timings", lambda _db: 0)
    monkeypatch.setattr(retention, "purge_old_access_logs", lambda _db: 0)
    worker.run_forever()
    return [*beats, clock.now]


def _longest_gap(beats: list[float]) -> float:
    return max(after - before for before, after in pairwise(beats))


def test_쉬는_워커도_박동한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """작업이 하나도 없는 10분 — 박동이 30초(+한 바퀴)를 넘게 끊기면 안 된다."""
    beats = _run(monkeypatch, minutes=10, jobs=set())
    limit = worker_module.HEARTBEAT_EVERY + worker_module.IDLE_MAX
    assert len(beats) >= 10, beats
    assert _longest_gap(beats) <= limit, beats
    # 화면이 「꺼짐」 으로 판정하는 문턱보다 훨씬 안쪽이어야 한다.
    assert _longest_gap(beats) < get_settings().worker_stale_seconds


def test_작업을_돌린_뒤에도_박동이_이어진다(monkeypatch: pytest.MonkeyPatch) -> None:
    """작업 몇 개 뒤 다시 쉴 때 — 고장은 정확히 여기서 보였다(끝나고 5분 뒤 「꺼짐」)."""
    beats = _run(monkeypatch, minutes=10, jobs={3, 4, 10})
    limit = worker_module.HEARTBEAT_EVERY + worker_module.IDLE_MAX
    assert _longest_gap(beats) <= limit, beats


def test_고아_첨부_정리가_오래_걸려도_박동과_작업은_이어진다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정리는 바퀴 안에서 돌았다 — 큰 저장소를 훑는 동안 박동이 멎어 5분을 넘기면 살아 있는
    워커가 「꺼짐」 이 되고, 그동안 작업도 못 집었다(2026-10-08).

    가짜 정리는 **바퀴가 작업을 몇 번 집을 때까지** 안 끝난다. 바퀴 안에서 돌면 그 신호가 영영
    안 와서 2초를 기다린 뒤 10분이 흐른 것으로 치고 끝난다 — 그 10분이 박동의 빈틈으로 남는다.
    """
    from app.modules.files import gc

    worker = worker_module.Worker()
    assert worker._gc_every, "시험 설정에서 정리가 꺼져 있다"
    clock = _Clock(worker, until=5 * 60)
    worker._last_gc = clock.now - worker._gc_every  # 첫 바퀴가 정리 차례다
    beats: list[float] = []
    turns = {"n": 0}
    released = threading.Event()
    scans = {"n": 0}

    def slow_scan(_db: Any, **_kw: Any) -> gc.Found:
        scans["n"] += 1
        if not released.wait(timeout=2.0):
            clock.now += 600.0
        return gc.Found()

    def process_one(_worker_id: str) -> bool:
        turns["n"] += 1
        if turns["n"] == 3:
            released.set()  # 정리하는 동안 바퀴가 돌아 작업을 집었다
        return False

    services: Any = job_services
    monkeypatch.setattr(worker_module, "time", clock)
    monkeypatch.setattr(worker, "waker", clock)
    monkeypatch.setattr(worker_module, "SessionLocal", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(gc, "scan", slow_scan)
    monkeypatch.setattr(gc, "clean", lambda _db, _found, **_kw: gc.Cleaned())
    monkeypatch.setattr(services, "heartbeat", lambda *_a: beats.append(clock.now))
    monkeypatch.setattr(services, "process_one", process_one)
    monkeypatch.setattr(services, "recover_stale", lambda _db: 0)
    for name in (
        "purge_expired_files",
        "purge_old_jobs",
    ):
        monkeypatch.setattr(services, name, lambda _db: 0)
    # 요청 시간 기록 · 접근 로그 정리도 — 흉내 안 내면 가짜 세션(None)에서 바퀴마다 예외가 나
    # 정리 시각이 안 옮겨지고, 시험이 보려는 박동과 상관없는 길을 돈다.
    monkeypatch.setattr(performance, "purge_old_timings", lambda _db: 0)
    monkeypatch.setattr(retention, "purge_old_access_logs", lambda _db: 0)
    try:
        worker.run_forever()
    finally:
        released.set()
        if worker._gc_thread is not None:
            worker._gc_thread.join(timeout=5.0)
    assert scans["n"] == 1, "정리가 한 차례만 돌아야 한다"
    assert turns["n"] >= 3, "정리하는 동안 바퀴가 작업을 집지 못했다"
    limit = worker_module.HEARTBEAT_EVERY + worker_module.IDLE_MAX
    assert _longest_gap([*beats, clock.now]) <= limit, beats
