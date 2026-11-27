"""작업마다 따로 프로세스 — 엔진 함수(`runner.run` · `apply` · `reconcile` · `retract`)를 새
프로세스에서 돌린다.

워커 안에서 돌면 큰 미러의 실행이 잡은 메모리가 끝나도 워커 프로세스에 남고(파이썬은 잡았던
메모리를 운영체제에 잘 돌려주지 않는다), 한 실행이 서버 메모리를 다 쓰면 운영체제가 같은 서버의
datahub 를 끝낼 수 있다(2026-11-25 요청). 그래서

- **새 프로세스** 에서 돌리고 끝나면 프로세스째 끝낸다 — 메모리가 운영체제로 돌아간다. 엔진은
  DB 를 모르므로(`relay_core` 는 `app` 을 import 하지 않는다) 워커가 정의 · 인증 정보를 풀어
  파이프로 넘기고 결과만 받는다. 자식은 엔진만 불러온다 — `multiprocessing` 의 spawn 은 워커의
  `__main__`(`app.worker`)까지 다시 불러와 50MB 를 더 쓴다(2026-11-25 잼).
- **상한**(`limit_mb`, `RELAY_JOB_MEMORY_MB`) — 부모가 자식의 RSS 를 `POLL_SECONDS` 마다 보고
  넘으면 그 프로세스만 끝낸다(`MemoryLimitExceeded` — 그 단계와 최고치를 말한다). 워커와 다른
  작업은 그대로 돈다.
- 자식은 `oom_score_adj=1000` — 서버 메모리가 모자라 운영체제가 끝낼 프로세스를 고를 때 이
  작업이 먼저다. 워커가 죽으면 자식도 끝난다(`PR_SET_PDEATHSIG`).
- 자식이 단계를 바꾸면(`on_stage`) · 정해진 간격마다(`on_tick`) 부모에게 알린다 — 작업 진행 ·
  심장박동 · 취소 확인. 부모의 알림 함수가 예외를 내면(취소 · 남이 가져간 작업) 자식을 끝내고
  그 예외를 그대로 낸다.
- 자식의 예외는 부모에서 같은 예외로 다시 낸다(못 옮기면 `ChildFailed` 에 그 글을 담아).
"""

from __future__ import annotations

import contextlib
import ctypes
import importlib
import inspect
import os
import pickle
import signal
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from multiprocessing import Pipe
from multiprocessing.connection import Connection
from typing import Any

from relay_core import memory

#: 부모가 자식의 메모리 · 소식을 보는 간격.
POLL_SECONDS = 0.2
#: 단계가 바뀌지 않아도 부모에게 알리는 간격 — 작업의 심장박동 · 취소 확인.
TICK_SECONDS = 15.0
#: `prctl` — 부모가 죽으면 이 신호를 받는다(linux/prctl.h).
_PR_SET_PDEATHSIG = 1
MB = memory.MB


class MemoryLimitExceeded(RuntimeError):
    """자식의 메모리가 상한을 넘어 그 프로세스를 끝냈다 — 그때의 단계와 최고치(MB)를 든다."""

    expected = True
    """원인을 문장으로 아는 실패 — 작업 틀이 예외 이름 · 스택 없이 그 문장만 보인다."""

    def __init__(self, message: str, *, stage: str = "", peak_mb: float = 0.0) -> None:
        super().__init__(message)
        self.stage = stage
        self.peak_mb = peak_mb


class ChildFailed(RuntimeError):
    """자식이 결과 없이 끝났거나, 자식의 예외를 그대로 옮기지 못했다."""

    expected = True
    """원인을 문장으로 아는 실패 — 작업 틀이 예외 이름 · 스택 없이 그 문장만 보인다."""


#: 자식의 첫 줄 — 부모와 같은 자리에서 엔진을 불러온다.
_BOOT = (
    "import pickle, sys;"
    "sys.path[:] = pickle.load(sys.stdin.buffer);"
    "from relay_core import isolate;"
    "isolate._main(int(sys.argv[1]), int(sys.argv[2]))"
)


def _main(fd: int, parent: int) -> None:
    """자식의 시작 — 할 일을 표준 입력에서 읽고 소식을 `fd` 로 보낸다."""
    _protect(parent)
    conn = Connection(fd)
    try:
        target, args, kwargs, progress_kw, label = pickle.load(sys.stdin.buffer)
    except Exception as error:
        conn.send(("error", ChildFailed(f"작업을 넘겨받지 못했습니다: {error}"), ""))
        conn.close()
        return
    _child(conn, target, args, kwargs, progress_kw, label)


def _protect(parent: int) -> None:
    """서버 메모리가 모자랄 때 운영체제가 이 프로세스부터 끝내게(같은 서버의 datahub 를
    지킨다), 그리고 **워커가 죽으면 같이 끝나게** — 남아서 돌면 되살아난 워커가 집은 같은
    파이프라인의 작업과 겹친다.

    **멈춤 신호(SIGTERM · SIGINT)는 받지 않는다 — 끝낼 때는 부모가 정한다.** systemd 는 워커를
    멈출 때 그 묶음(cgroup)의 프로세스 전부에 SIGTERM 을 보낸다. 워커는 하던 작업을 끝내고
    멈추려는데(`TimeoutStopSec`) 이 프로세스가 그 자리에서 죽어, 업데이트 때마다 도는 실행 ·
    적용이 「메모리 부족일 수 있음」 으로 실패했다(2026-11-27 점검). 취소 · 상한은 부모가
    SIGKILL 로, 부모가 죽으면 위의 알림(PDEATHSIG)으로 끝난다."""
    for one in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(OSError, ValueError):
            signal.signal(one, signal.SIG_IGN)
    try:
        with open("/proc/self/oom_score_adj", "w", encoding="ascii") as handle:
            handle.write("1000")
    except OSError:
        pass
    try:
        ctypes.CDLL(None).prctl(_PR_SET_PDEATHSIG, int(signal.SIGKILL))
    except (OSError, AttributeError):
        return  # 리눅스가 아니다
    if parent != os.getppid():
        os._exit(1)  # 알리기를 걸기 전에 부모가 이미 죽었다


def _resolve(target: str) -> Callable[..., Any]:
    module, name = target.rsplit(".", 1)
    found: Callable[..., Any] = getattr(importlib.import_module(module), name)
    return found


def _child(
    conn: Any,
    target: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    progress_kw: str | None,
    label: str,
) -> None:
    def send(*message: Any) -> None:
        conn.send(message)

    try:
        fn = _resolve(target)
        accepts = inspect.signature(fn).parameters
        meter: memory.Meter | None = None
        if "on_stage" in accepts:
            kwargs["on_stage"] = lambda stage: send("stage", stage)
        else:
            meter = memory.Meter(lambda stage: send("stage", stage))
            meter.stage(label)
        if progress_kw:
            kwargs[progress_kw] = lambda *values: send("progress", values)
        result = fn(*args, **kwargs)
        record = meter.finish() if meter is not None else {}
        send("ok", result, record)
    except BaseException as error:  # 자식의 끝 — 무엇이든 부모에게 전한다
        text = traceback.format_exc()
        try:
            pickle.dumps(error)
            send("error", error, text)
        except Exception:
            wrapped = ChildFailed(f"{type(error).__name__}: {error}")
            wrapped.expected = False  # 엔진의 결함일 수 있다 — 작업 틀이 스택을 남긴다
            send("error", wrapped, text)
    finally:
        conn.close()


def _finished(process: subprocess.Popen[bytes]) -> bool:
    """결과를 보낸 자식은 곧 끝난다 — 잠깐 기다린다. 아니면(취소 · 상한) 끝내야 한다."""
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        return False
    return True


def _rss_of(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def call(
    target: str,
    *args: Any,
    limit_mb: int = 0,
    label: str = "작업",
    on_stage: Callable[[str], None] | None = None,
    on_tick: Callable[[str], None] | None = None,
    on_progress: Callable[..., None] | None = None,
    progress_kw: str | None = None,
    tick_seconds: float = TICK_SECONDS,
    **kwargs: Any,
) -> tuple[Any, dict[str, Any]]:
    """`target`(`relay_core.runner.run` 처럼 점으로 적은 함수)을 새 프로세스에서 돌려 (결과,
    단계별 메모리)를 돌려준다. `on_stage` 를 받는 함수(`runner.run`)는 자기 단계를 알리고,
    아니면 `label` 한 단계로 잰다. `progress_kw` 를 주면 그 인자에 진행 알림 함수를
    넣는다(`runner.apply` 의 `progress`)."""
    # 넘길 수 없는 것이면 자식을 띄우기 전에 여기서 말한다. 인증 정보도 들어 있어 인자나 파일로
    # 넘기지 않는다(파이프).
    body = pickle.dumps(sys.path) + pickle.dumps((target, args, kwargs, progress_kw, label))
    receive, send = Pipe(duplex=False)
    process = subprocess.Popen(
        [sys.executable, "-c", _BOOT, str(send.fileno()), str(os.getpid())],
        stdin=subprocess.PIPE,
        pass_fds=(send.fileno(),),
    )
    send.close()
    try:
        assert process.stdin is not None
        with process.stdin as pipe:
            pipe.write(body)
    except BrokenPipeError:
        pass  # 자식이 뜨자마자 끝났다 — 아래에서 종료 코드와 함께 말한다
    stage, peak = label, 0
    limit = max(int(limit_mb), 0) * MB
    last_tick = time.monotonic()
    try:
        while True:
            if receive.poll(POLL_SECONDS):
                try:
                    message = receive.recv()
                except EOFError:
                    break
                kind = message[0]
                if kind == "stage":
                    stage = str(message[1])
                    if on_stage is not None:
                        on_stage(stage)
                elif kind == "progress":
                    if on_progress is not None:
                        on_progress(*message[1])
                elif kind == "ok":
                    return message[1], dict(message[2] or {})
                elif kind == "error":
                    error = message[1]
                    if isinstance(error, BaseException):
                        raise error
                    raise ChildFailed(str(error))
            elif process.poll() is not None and not receive.poll():
                break
            now = _rss_of(process.pid)
            if now is not None:
                peak = max(peak, now)
                if limit and now > limit:
                    process.kill()
                    raise MemoryLimitExceeded(
                        f"작업 메모리 상한({limit_mb:,}MB)을 넘어 작업을 중단했습니다"
                        f"(단계: {stage}, 최고 {peak / MB:,.0f}MB). 같은 서버의 다른 서비스는 "
                        "영향을 받지 않았습니다. RELAY_JOB_MEMORY_MB를 늘리거나 실행 추이의 "
                        "단계별 메모리를 확인하세요.",
                        stage=stage,
                        peak_mb=round(peak / MB, 1),
                    )
            if on_tick is not None and time.monotonic() - last_tick >= tick_seconds:
                last_tick = time.monotonic()
                on_tick(stage)
        code = process.wait(timeout=5)
        raise ChildFailed(_ended(code, stage, peak))
    finally:
        receive.close()
        if process.poll() is None and not _finished(process):
            process.kill()
        process.wait()


def _ended(code: int, stage: str, peak: float) -> str:
    """결과 없이 끝난 자식의 까닭 — 종료 코드로 가린다. SIGKILL 이면 운영체제(메모리 부족)나
    서비스 중지 시간 초과다. 다른 신호 · 코드에 메모리를 짐작해 붙이면 엉뚱한 곳을 보게
    한다."""
    where = f"종료 코드 {code}, 단계: {stage}, 최고 {peak / MB:,.0f}MB"
    if code == -signal.SIGKILL:
        return (
            f"작업 프로세스가 강제 종료되었습니다({where}). 서버 메모리가 부족해 운영체제가 "
            "종료했거나, 서비스 중지가 제한 시간을 넘었을 수 있습니다."
        )
    if code < 0:
        return f"작업 프로세스가 신호를 받아 종료되었습니다({where})."
    return f"작업 프로세스가 결과 없이 종료되었습니다({where})."


def _getpid() -> int:
    """시험용 — 자식에서 돈 것인지 본다."""
    return os.getpid()


def _hog(mb: int, hold_seconds: float = 5.0) -> int:
    """시험용 — `mb` 만큼 잡고 잠시 든다."""
    held = bytearray(mb * MB)
    held[::4096] = b"x" * len(held[::4096])
    time.sleep(hold_seconds)
    return len(held)


def _boom(text: str) -> None:
    """시험용 — 자식에서 예외."""
    raise ValueError(text)


def _oom_score() -> str:
    """시험용 — 자식의 `oom_score_adj`."""
    with open("/proc/self/oom_score_adj", encoding="ascii") as handle:
        return handle.read().strip()


def _modules() -> list[str]:
    """시험용 — 자식이 불러온 모듈."""
    return sorted(sys.modules)
