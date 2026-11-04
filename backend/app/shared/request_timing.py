"""요청 시간 기록을 **모아서** 넣는다 — 기록이 요청을 늦추지 않게(ADR 0026).

접근 로그는 상태를 바꾸는 요청만이라 요청마다 한 번 커밋해도 됐다. 이것은 조회까지 전부다 —
같은 방식이면 요청마다 DB 왕복 하나가 **이벤트 루프를 막는다**(미들웨어는 루프 위에서 돌고,
막힌 동안 다른 요청은 아무도 못 나간다). 그래서 줄을 메모리에 모았다가, 따로 도는 스레드가
몇 초마다 한 번에 넣는다.

**잃어도 되는 기록이다.** 프로세스가 죽으면 마지막 몇 초가 사라지고, DB 가 막히면 그동안의
것을 버린다(쌓아 두면 메모리가 대신 터진다). 성능을 보려는 기록이 성능을 해치면 안 된다.

프로세스마다 하나다 — 운영은 uvicorn 워커가 넷이라 넷이 각자 모아 넣는다.
"""

from __future__ import annotations

import atexit
import logging
import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.modules.audit.models import RequestTiming

log = logging.getLogger(__name__)

#: 몇 초마다 넣나. 화면은 이만큼 늦게 본다 — 성능을 보는 데는 충분하다.
FLUSH_EVERY = 3.0
#: 이만큼 모이면 기다리지 않고 넣는다.
FLUSH_AT = 500
#: 이보다 많이 밀리면 버린다 — DB 가 막혔을 때 메모리가 대신 터지지 않게.
MAX_PENDING = 50_000

SessionFactory = Callable[[], Session]


class TimingWriter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # **넣는 일은 한 번에 하나다.** 시험이 `flush()` 를 부른 순간 스레드가 앞 묶음을 넣는
        # 중이면, 그 묶음이 커밋되기 전에 시험이 표를 읽어 「안 남았다」 로 본다.
        self._flushing = threading.Lock()
        self._wake = threading.Event()
        self._pending: list[tuple[SessionFactory, dict[str, Any]]] = []
        self._dropped = 0
        self._thread: threading.Thread | None = None
        self._exit_hooked = False
        self.background = True
        """따로 도는 스레드가 넣나. **시험은 끈다**(`tests/conftest.py`) — 스레드가 같은
        엔진으로 제 차례에 넣으면, 엔진의 문장 수를 세는 시험에 그 문장이 섞여 가끔
        떨어졌다(2026-10-10). 끄면 모아 두기만 하고, 시험이 `flush()` 로 넣는다."""

    def add(self, factory: SessionFactory, row: dict[str, Any]) -> None:
        """한 줄을 맡긴다 — 루프 위에서 불리므로 DB 를 만지지 않는다."""
        with self._lock:
            if len(self._pending) >= MAX_PENDING:
                self._dropped += 1
                return
            self._pending.append((factory, row))
            full = len(self._pending) >= FLUSH_AT
            if not self.background:
                return
            if self._thread is None or not self._thread.is_alive():
                self._start()
        if full:
            self._wake.set()

    def discard(self) -> None:
        """모인 것을 넣지 않고 버린다 — 시험이 앞 시험의 줄을 치울 때."""
        with self._lock:
            self._pending = []
            self._dropped = 0

    def _start(self) -> None:
        # **처음 줄이 올 때 띄운다** — 기동 때 띄우면 워커 프로세스를 나누기 전의 스레드가
        # 되어 나뉜 쪽에는 없다. 데몬이라 프로세스와 함께 끝나고, 남은 것은 종료 때 넣는다.
        self._thread = threading.Thread(target=self._run, name="request-timing", daemon=True)
        self._thread.start()
        if not self._exit_hooked:
            atexit.register(self.flush)
            self._exit_hooked = True

    def _run(self) -> None:
        while True:
            self._wake.wait(FLUSH_EVERY)
            self._wake.clear()
            try:
                self.flush()
            except Exception:  # 스레드는 죽지 않는다 — 죽으면 그 뒤로 아무것도 안 남는다.
                log.exception("요청 시간 기록 실패 — 다음 차례에 다시")

    def flush(self) -> int:
        """모인 것을 지금 넣는다. 넣은 줄 수. 시험과 종료가 부른다."""
        with self._flushing:
            with self._lock:
                batch, self._pending = self._pending, []
                dropped, self._dropped = self._dropped, 0
            if dropped:
                log.warning("요청 시간 기록 %d줄을 버렸습니다 — 넣기가 밀렸습니다", dropped)
            if not batch:
                return 0
            # 시험은 세션 공장을 바꿔 끼운다(`app.state.session_factory`) — 공장마다 따로.
            grouped: dict[SessionFactory, list[dict[str, Any]]] = {}
            for factory, row in batch:
                grouped.setdefault(factory, []).append(row)
            written = 0
            for factory, rows in grouped.items():
                try:
                    with factory() as db:
                        db.execute(insert(RequestTiming), rows)
                        db.commit()
                    written += len(rows)
                except Exception:
                    log.exception("요청 시간 기록 %d줄을 못 넣었습니다 — 버립니다", len(rows))
            return written


writer = TimingWriter()
