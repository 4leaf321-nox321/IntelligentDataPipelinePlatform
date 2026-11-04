"""작업 워커 — `python -m app.worker`.

앱과 같은 코드 · 같은 `.env` 로 돈다. 표(`jobs`)에서 `queued` 를 하나씩 집어 돌리고, 30초마다
심장박동을 남기고, 심장박동이 멎은 남의 작업을 되돌린다. A · B 두 대에 하나씩 떠도 같은 행을
두 번 안 집는다(`FOR UPDATE SKIP LOCKED`). 설계는 `docs/sp/작업-워커-설계.md`.

    cd backend && .venv/bin/python -m app.worker          # 개발
    systemd: <slug>-worker.service                          # 운영(deploy.sh 가 만든다)
"""

from __future__ import annotations

import contextlib
import logging
import signal
import threading
import time
from typing import Any

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import SessionLocal
from app.logging_setup import setup_logging
from app.modules.audit import retention
from app.modules.jobs import services
from app.modules.server import performance

log = logging.getLogger("app.worker")

HEARTBEAT_EVERY = 30.0
RECOVER_EVERY = 60.0
PURGE_EVERY = 3600.0
IDLE_MAX = 5.0
#: 고아 첨부 정리는 기동 한 시간 뒤가 첫 차례 — 업데이트로 워커가 자주 다시 떠도 밀리지 않게,
#: 그렇다고 기동하자마자 저장소 전체를 훑지는 않게.
GC_FIRST_AFTER = 3600.0


def _clean_orphans() -> None:
    """고아 첨부 정리 한 차례 — 워커의 바퀴와 **다른 스레드**에서(제 세션으로) 돈다."""
    from app.modules.files import gc

    try:
        with SessionLocal() as db:
            done = gc.clean(db, gc.scan(db))
    except Exception:
        log.exception("고아 첨부 정리 실패 — 다음 차례에 다시")
        return
    if done.files or done.temp:
        log.info(
            "고아 첨부 정리: 파일 %d개(%.1fMB) · 임시 파일 %d개",
            done.files,
            done.bytes / 1024 / 1024,
            done.temp,
        )


class Waker:
    """**작업이 들어오면 바로 깬다** — `LISTEN`(`services.WAKE_CHANNEL`) 한 연결로 기다린다.

    예전에는 빈 워커가 2초에서 5초까지 늦춰 가며 물어, 넣고 집기까지 1 ~ 5초가 들었다 —
    계획과 적용 두 번이면 그것이 두 번이다(2026-10-09). 연결이 안 되거나 끊기면 그냥 그
    시간만큼 잔다(예전과 같다) — 다음 기다림에 다시 붙는다. 일하는 동안 온 알림은 연결에 쌓여
    있다가 다음 기다림을 곧장 끝낸다(빈 바퀴 한 번이면 된다)."""

    def __init__(self) -> None:
        self._conn: psycopg.Connection[Any] | None = None

    def _connected(self) -> psycopg.Connection[Any] | None:
        if self._conn is not None and not self._conn.closed:
            return self._conn
        try:
            url = make_url(get_settings().database_url).set(drivername="postgresql")
            conn = psycopg.connect(url.render_as_string(hide_password=False), autocommit=True)
            conn.execute(sql.SQL("LISTEN {}").format(sql.Identifier(services.WAKE_CHANNEL)))
        except Exception:
            log.warning(
                "작업 알림을 들을 연결을 못 열었습니다 — 시간마다 묻습니다", exc_info=True
            )
            self._conn = None
            return None
        self._conn = conn
        return conn

    def wait(self, seconds: float) -> None:
        conn = self._connected()
        if conn is None:
            time.sleep(seconds)
            return
        try:
            for _ in conn.notifies(timeout=seconds, stop_after=1):
                break
        except Exception:
            log.warning("작업 알림 연결이 끊겼습니다 — 다음에 다시 붙습니다", exc_info=True)
            self.close()
            time.sleep(seconds)

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):  # 닫는 것은 실패해도 할 일이 없다
                self._conn.close()
        self._conn = None


class Worker:
    def __init__(self) -> None:
        self.worker_id = services.worker_identity()
        self.stop = False
        self.waker = Waker()
        self._last_beat = 0.0
        self._last_recover = 0.0
        self._last_purge = 0.0
        hours = get_settings().filestore_gc_hours
        self._gc_every = hours * 3600.0 if hours > 0 else 0.0
        self._last_gc = time.monotonic() - max(self._gc_every - GC_FIRST_AFTER, 0.0)
        self._gc_thread: threading.Thread | None = None

    def _tick_housekeeping(self) -> None:
        now = time.monotonic()
        if now - self._last_beat >= HEARTBEAT_EVERY:
            services.heartbeat(self.worker_id, None)
            self._last_beat = now
        if now - self._last_recover >= RECOVER_EVERY:
            with SessionLocal() as db:
                revived = services.recover_stale(db)
            if revived:
                log.warning("멎은 작업 %d개를 되살렸습니다", revived)
            self._last_recover = now
        if now - self._last_purge >= PURGE_EVERY:
            with SessionLocal() as db:
                gone = services.purge_expired_files(db)
                # 기록도 함께 — 타이머가 하루 288행을 넣는다. 안 지우면 사람이 올린 작업이
                # 그 사이에 파묻힌다.
                old_jobs = services.purge_old_jobs(db)
                # 요청 시간 기록은 요청마다 한 줄이다 — 가장 빨리 자라는 표(ADR 0026).
                old_timings = performance.purge_old_timings(db)
                # 접근 로그는 보관 기간(기본 1년)이 지난 것만. 감사 기록은 안 지운다.
                old_access = retention.purge_old_access_logs(db)
            if gone or old_jobs or old_timings or old_access:
                log.info(
                    "정리: 작업 파일 %d개 · 끝난 작업 기록 %d개 · 요청 시간 기록 %d줄 · "
                    "접근 로그 %d줄",
                    gone,
                    old_jobs,
                    old_timings,
                    old_access,
                )
            self._last_purge = now
        if self._gc_every and now - self._last_gc >= self._gc_every:
            # **아무 첨부도 안 가리키는 파일** — 거절된 업로드 · 뗀 첨부의 파일이 쌓이기만 했다
            # (`files/gc.py`). 서버마다 저장소가 따로면 각자 제 것을, 같이 쓰면 둘이 같은 것을
            # 훑는다 — 지우기는 없는 파일을 넘어가므로 겹쳐도 된다.
            # **실패해도 차례는 넘긴다** — 시각을 안 옮기면 5초마다 저장소 전체를 다시 훑는다.
            self._last_gc = now
            if self._gc_thread is not None and self._gc_thread.is_alive():
                log.warning("앞 차례의 고아 첨부 정리가 아직 돕니다 — 이번 차례는 건너뜁니다")
            else:
                # ⚠️ **따로 돈다.** 이 바퀴 안에서 돌면 큰 저장소를 훑는 동안 박동이 멎어, 5분을
                #    넘기면 살아 있는 워커가 화면에서 「꺼짐」 이 되고 그동안 작업 · 웹훅도 못
                #    집었다(2026-10-08). 정리는 파일과 제 연결만 만지므로 바퀴와 나란히 돌아도
                #    된다.
                self._gc_thread = threading.Thread(
                    target=_clean_orphans, name="filestore-gc", daemon=True
                )
                self._gc_thread.start()

    def run_forever(self) -> None:
        settings = get_settings()
        idle = settings.worker_poll_seconds
        log.info("워커 시작: %s", self.worker_id)
        services.heartbeat(self.worker_id, None)
        while not self.stop:
            try:
                self._tick_housekeeping()
                worked = services.process_one(self.worker_id)
                if worked:
                    # 작업을 돌렸으면 `process_one` 이 끝에서 박동을 남겼다 — 30초를 거기서
                    # 다시 센다. ⚠️ **빈 바퀴에서는 되감지 않는다.** 예전에는 바퀴마다 되감아,
                    # 쉬는 워커(바퀴가 몇 초)는 30초에 영영 못 닿고 기동 뒤로 박동이 멎었다 —
                    # 5분 뒤 살아 있는 워커가 화면에서 「꺼짐」 이 됐다.
                    self._last_beat = time.monotonic()
            except Exception:  # 워커는 죽지 않는다 — DB 가 잠깐 끊겨도 다음 바퀴에 다시.
                log.exception("워커 바퀴 실패 — %.0f초 뒤 다시", IDLE_MAX)
                worked = False
                time.sleep(IDLE_MAX)
                continue
            if worked:
                idle = settings.worker_poll_seconds
            else:
                # 비어 있으면 천천히 — 빈 표를 2초마다 두드리는 것은 DB 에 대한 예의가 아니다.
                # 기다리는 동안 작업이 들어오면 알림으로 바로 깬다(`Waker`).
                self.waker.wait(idle)
                idle = min(idle * 1.5, IDLE_MAX)
        if self._gc_thread is not None and self._gc_thread.is_alive():
            # 데몬 스레드라 프로세스와 함께 끊긴다 — 지우기는 파일 하나씩이라 반쯤 남아도
            # 다음 차례가 처음부터 다시 훑는다.
            log.info("고아 첨부 정리가 도는 중에 멈춥니다 — 다음 차례에 처음부터 다시")
        self.waker.close()
        log.info("워커 종료: %s", self.worker_id)

    def handle_signal(self, signum: int, _frame: Any) -> None:
        log.info("신호 %s — 하던 것을 끝내고 멈춥니다", signum)
        self.stop = True


def _assemble() -> None:
    """**앱과 같은 조립**을 이 프로세스에도 건다 — 레지스트리(`app/main.py` 의
    `_register_extensions` · 확장의 `register`)는 조립 지점 한 곳이 채운다.

    워커는 그것을 안 거쳐 레지스트리가 **비어** 있었다. 처리 함수가 등록을 부르는 자리에서
    조용히 다른 길로 갔다 — 사진 일괄 업로드의 적용은 「첨부 자리」
    (`register_attachment_owner`)를 못 찾아 객체의 부서 대신 전역으로 판정했고(부서 관리자는
    거절, 시스템 관리자는 전역 첨부), 부서 자료 이동은 「멤버」 말고는 옮길 종류를 몰랐다
    (2026-10-10). 시험은 앱을 띄운 프로세스에서 워커를 돌려 이것을 못 본다
    (`tests/api/test_worker_assembly.py` 가 따로 띄워 본다).

    **변경 이벤트를 듣는 쪽도 앱과 같다**(2026-10-10) — 예전에는 비워 둬서 작업으로 한 일은
    웹훅 · 지켜보기 알림이 하나도 안 갔다. 웹훅은 작업마다 **요약 한 건**만 받고(줄마다 보내면
    일괄 입력 10만 줄이 웹훅 10만 건이다 — `events.job_scope`), 지켜보기는 지켜보는 객체가
    바뀔 때마다 받는다. 적재 뒤의 일(지표 다시 세기)은 여전히 작업 본문이 넣는다(ADR 0013)."""
    import app.main  # noqa: F401 — import 가 곧 조립이다(`create_app`)


def main() -> None:
    setup_logging(get_settings())
    _assemble()
    worker = Worker()
    signal.signal(signal.SIGTERM, worker.handle_signal)
    signal.signal(signal.SIGINT, worker.handle_signal)
    worker.run_forever()


if __name__ == "__main__":
    main()
