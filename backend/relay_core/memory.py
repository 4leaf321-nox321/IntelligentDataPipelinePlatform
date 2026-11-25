"""단계별 최고 메모리 — 실행의 수신 · 미러 · 변환 · 비교 · 미리 보기마다 그 단계 동안의 **최고
RSS**(MB).

큰 미러의 실행이 어디서 메모리를 쓰는지 잰 적이 없었다(2026-11-25). 작업을 따로 프로세스로
돌리고(`isolate`) 계획을 증분으로 만드는 일(`incremental`)의 효과를 이 기록으로 잰다.

- 리눅스는 `/proc/self/clear_refs` 에 `5` 를 써 프로세스의 최고 RSS(`VmHWM`)를 지금 값으로
  되돌릴 수 있다. 단계가 바뀔 때 되돌리고 단계가 끝날 때 읽으면 **그 단계의 정확한
  최고치**다(표본이 놓치는 순간도 잡는다).
- 그것이 안 되면(권한 · 다른 운영체제) 50ms 마다 `VmRSS` 를 읽는 표본으로 — 짧은 순간은 놓칠 수
  있다.
- `/proc` 가 없으면 아무것도 적지 않는다(빈 기록).

**프로세스 전체의 값이다** — 같은 프로세스에서 다른 일이 함께 돌면 그 몫도 든다. 작업마다 따로
프로세스로 돌릴 때(`RELAY_JOB_PROCESS`, 기본) 그 작업의 값이 된다. 같은 단계 이름이 여러 번
나오면(분할 번들의 적용) 가장 큰 값을 남긴다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

STATUS = Path("/proc/self/status")
CLEAR_REFS = Path("/proc/self/clear_refs")
SAMPLE_SECONDS = 0.05
MB = 1024 * 1024


def _read_kb(field: str) -> int | None:
    """`/proc/self/status` 의 한 줄(`VmHWM:  123456 kB`)을 바이트로 — 없으면 None."""
    try:
        text = STATUS.read_text(encoding="ascii")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith(f"{field}:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return None


def rss() -> int | None:
    """지금의 RSS(바이트)."""
    return _read_kb("VmRSS")


def _reset_peak() -> bool:
    """최고 RSS 를 지금 값으로 되돌린다 — 되면 참."""
    try:
        CLEAR_REFS.write_text("5", encoding="ascii")
    except OSError:
        return False
    return True


class Meter:
    """단계마다 최고 RSS — `stage(이름)` 으로 다음 단계를 시작하고 `finish()` 로 끝낸다.
    `on_stage` 는 단계가 바뀔 때 부른다(작업 진행 표시 — `isolate`)."""

    def __init__(self, on_stage: Callable[[str], None] | None = None) -> None:
        self.stages: dict[str, float] = {}
        self.seconds: dict[str, float] = {}
        """단계마다 걸린 시간(초) — 같은 단계가 여러 번이면 더한다."""
        self._began = time.monotonic()
        self._on_stage = on_stage
        self._current: str | None = None
        self._exact = _read_kb("VmHWM") is not None and _reset_peak()
        self._sampled = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if not self._exact and rss() is not None:
            self._thread = threading.Thread(
                target=self._sample, name="relay-meter", daemon=True
            )
            self._thread.start()

    @property
    def current(self) -> str | None:
        return self._current

    def _sample(self) -> None:
        while not self._stop.wait(SAMPLE_SECONDS):
            now = rss()
            if now is not None:
                with self._lock:
                    self._sampled = max(self._sampled, now)

    def _close_stage(self) -> None:
        if self._current is None:
            return
        spent = time.monotonic() - self._began
        self.seconds[self._current] = round(self.seconds.get(self._current, 0.0) + spent, 1)
        if self._exact:
            peak = _read_kb("VmHWM")
        else:
            with self._lock:
                peak, self._sampled = self._sampled, 0
            now = rss()
            peak = max(peak or 0, now or 0) or None
        if peak is not None:
            mb = round(peak / MB, 1)
            self.stages[self._current] = max(self.stages.get(self._current, 0.0), mb)

    def stage(self, name: str) -> None:
        """앞 단계를 닫고(그 최고치를 적고) `name` 단계를 시작한다."""
        self._close_stage()
        self._current = name
        self._began = time.monotonic()
        if self._exact:
            _reset_peak()
        else:
            with self._lock:
                self._sampled = rss() or 0
        if self._on_stage is not None:
            self._on_stage(name)

    def finish(self) -> dict[str, Any]:
        """마지막 단계를 닫고 기록을 돌려준다 — `{"memory": {단계: MB}, "peak_mb": MB,
        "seconds_by_stage": {단계: 초}}`."""
        self._close_stage()
        self._current = None
        self._stop.set()
        return self.to_json()

    @property
    def peak_mb(self) -> float | None:
        return max(self.stages.values()) if self.stages else None

    def to_json(self) -> dict[str, Any]:
        if not self.stages:
            return {}
        return {
            "memory": dict(self.stages),
            "seconds_by_stage": dict(self.seconds),
            "peak_mb": self.peak_mb,
        }
