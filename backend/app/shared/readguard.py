"""오래 도는 읽기의 안전장치 — **요청한 쪽이 떠나면 멈추고, 요청 하나의 시간과 동시 개수에
상한을 둔다**(ADR 0048).

FastAPI 는 `def` 라우트를 스레드에서 돌린다. 사람이 화면을 닫아도 그 스레드와 DB 질의는 끝까지
돈다. 정적 그리기(ADR 0045)는 질의를 단계마다 여섯 번 넘게 돌리므로 질의마다의 시간 상한
(`querycap`, 60초)으로는 요청 하나가 몇 분이고, 닫고 다시 열면 그 위에 하나가 더 돈다 —
운영에서 그렇게 쌓여 서버가 멈췄다(2026-11-02).

- **떠나면 멈춘다** — 요청한 쪽의 연결을 0.5초마다 보고, 끊겼으면 지금 도는 질의를 취소하고
  (DB 에 취소 요청 — `cancel_safe`) 그 요청의 다음 질의는 보내지 않는다(`Stopped`).
- **요청 하나의 시간 상한** — 질의마다가 아니라 요청 전체에. 넘으면 같은 길로 멈추고 504.
- **동시 개수** — 설치 전체(이중화의 두 서버 · 서버마다 워커 넷)에서 몇 개까지. DB 의 자문
  잠금이라 한 벌이다. 자리가 없으면 몇 초 기다리고(앞 요청이 방금 취소되는 중일 수 있다) 429.

취소는 DB 에 「지금 도는 질의」 를 끊어 달라는 말이라, 질의 사이(파이썬이 일하는 동안)에 닿으면
아무 일도 없다. 그래서 다음 질의를 막는 자리(모든 엔진의 `before_cursor_execute`)를 함께 두고,
일이 끝날 때까지 0.5초마다 취소를 다시 보낸다.

자리(자문 잠금)는 트랜잭션이 끝나면 풀린다 — 이것을 쓰는 읽기는 중간에 커밋하지 않는다.

**작업에도 같은 길을 쓴다**(ADR 0049) — 질의 하나가 몇십 분인 작업(지식 그래프 지도)은
단계 사이가 오지 않아 「취소」 가 먹지 않았다. 워커가 작업을 `watching()` 안에서 돌리고, 박동
스레드가 취소 요청을 보면 `stop(guard, "cancel")` 으로 도는 질의를 끊는다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.shared.errors import AppError, code

log = logging.getLogger(__name__)

Why = Literal["gone", "time", "cancel"]

#: 끊겼나 · 시간이 다 됐나를 보는 간격(초).
POLL = 0.5
#: 자리가 빌 때까지 다시 보는 간격(초).
SLOT_RETRY = 0.25
#: 자리가 없을 때 기다리는 시간(초) — 앞 요청이 방금 끊겨 취소되는 중일 수 있다(그림의 단계를
#: 바꾸면 화면이 앞 요청을 끊고 곧바로 새로 묻는다).
SLOT_WAIT = 5.0


class Stopped(Exception):
    """멈춘 요청이 다음 질의를 보내려 했다 — 그 질의는 보내지 않았다."""


@dataclass
class Guard:
    why: Why | None = None
    raw: Any = None
    """마지막 질의를 보낸 DB 연결(psycopg) — 취소를 보낼 곳."""


_current: ContextVar[Guard | None] = ContextVar("readguard", default=None)


@event.listens_for(Engine, "before_cursor_execute")
def _refuse_stopped(conn: Any, *_args: Any) -> None:
    """지켜보는 요청의 질의마다 — 멈췄으면 보내지 않고, 아니면 그 연결을 적어 둔다."""
    guard = _current.get()
    if guard is None:
        return
    if guard.why is not None:
        raise Stopped(guard.why)
    guard.raw = conn.connection.dbapi_connection


@dataclass(frozen=True)
class Slots:
    """설치 전체에서 동시에 도는 개수 — 자문 잠금 `<name>:<번호>` 중 하나를 쥔다. 0 이면 안
    센다."""

    name: str
    count: int


def _take(db: Session, slots: Slots, what: str) -> None:
    """빈자리를 쥔다(트랜잭션이 끝나면 풀린다). 없으면 `SLOT_WAIT` 초 동안 다시 보고 429."""
    until = time.monotonic() + SLOT_WAIT
    while True:
        for at in range(slots.count):
            if db.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                {"k": f"{slots.name}:{at}"},
            ):
                return
        if time.monotonic() >= until:
            raise AppError(
                code("COMMON", 429),
                f"지금 {what} {slots.count}개가 돌고 있습니다 — 서버를 지키려고 한 번에 "
                "그만큼만 돌립니다. 앞의 것이 끝나면 다시 해 주세요.",
                status=429,
                details={"slots": slots.count},
            )
        time.sleep(SLOT_RETRY)


@contextmanager
def watching() -> Iterator[Guard]:
    """이 맥락(스레드)의 질의를 지켜본다 — 다른 스레드가 `stop` 하면 도는 질의를 취소하고 다음
    질의를 보내지 않는다."""
    guard = Guard()
    token = _current.set(guard)
    try:
        yield guard
    finally:
        _current.reset(token)


def stop(guard: Guard, why: Why) -> None:
    """멈춘다(다른 스레드에서 부른다) — 도는 질의에 취소를 보낸다. 여러 번 불러도 된다(질의
    사이에 닿은 취소는 아무 일도 없으니 다시 보낸다)."""
    if guard.why is None:
        guard.why = why
    _cancel(guard)


def stopped() -> Why | None:
    """이 맥락이 멈췄으면 그 까닭."""
    guard = _current.get()
    return guard.why if guard is not None else None


def _cancel(guard: Guard) -> None:
    raw = guard.raw
    if raw is None:
        return
    try:
        raw.cancel_safe(timeout=2.0)
    except Exception:  # 취소가 안 닿아도 다음 질의는 막힌다(`_refuse_stopped`)
        log.warning("질의 취소를 보내지 못했습니다", exc_info=True)


async def run[T](
    request: Request,
    db: Session,
    work: Callable[[], T],
    *,
    seconds: float,
    what: str,
    hint: str,
    slots: Slots | None = None,
) -> T:
    """`work`(동기 — `db` 로 읽는다)를 스레드에서 돌리며 지켜본다. 요청한 쪽이 떠나면 499,
    `seconds`(0 이면 없음)를 넘기면 504 로 멈춘다. `what` · `hint` 는 안내에 실린다."""
    guard = Guard()

    def body() -> T:
        token = _current.set(guard)
        try:
            if slots is not None and slots.count > 0:
                _take(db, slots, what)
            return work()
        finally:
            _current.reset(token)

    task = asyncio.ensure_future(run_in_threadpool(body))
    until = time.monotonic() + seconds if seconds > 0 else None
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=POLL)
            if task.done():
                break
            if guard.why is None:
                if await request.is_disconnected():
                    guard.why = "gone"
                elif until is not None and time.monotonic() >= until:
                    guard.why = "time"
            if guard.why is not None:
                await run_in_threadpool(_cancel, guard)
    except asyncio.CancelledError:
        # 서버가 내려가는 중 — 스레드의 질의도 멈춘다.
        guard.why = guard.why or "gone"
        _cancel(guard)
        raise
    try:
        return task.result()
    except Exception as caught:
        if guard.why == "gone":
            raise AppError(
                code("COMMON", 499),
                f"요청한 쪽이 떠나 멈췄습니다({what}).",
                status=499,
            ) from caught
        if guard.why == "time":
            raise AppError(
                code("COMMON", 504),
                f"{what} — {seconds:g}초 안에 끝나지 않아 멈췄습니다. 서버를 붙잡지 않게 끊은 "
                f"것이고 바뀐 것은 없습니다. {hint}",
                status=504,
                details={"seconds": seconds},
            ) from caught
        raise
