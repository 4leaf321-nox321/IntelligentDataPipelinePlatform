"""읽기 질의의 시간 상한 — 화면 · AI 가 묻는 목록 · 통계가 서버를 붙잡지 않게(ADR 0031).

이어진 것 너머를 걷는 기준 · 조건은 **지나가는 줄 수**만큼 든다 — 끝이 좁으면 네 걸음도
2초인데, 기록 2,000만 건을 지나가면 세 걸음도 수십 초, 네 걸음은 3분이다(실측, ADR 0031).
상한이 없으면 그동안 DB 연결 하나와 API 작업자 하나가 묶이고, 사람은 화면을 닫아도 질의는
끝까지 돈다. 이중화의 nginx 는 600초를 넘기면 **같은 요청을 다른 서버에 다시 보낸다**
(`proxy_next_upstream timeout`).

임시 파일(정렬 · 해시가 디스크로 넘어간 것)에도 상한을 건다(`read_temp_limit`, ADR 0048) —
넘으면 그 질의만 끊고 507 「범위를 좁히라」. 질의 하나가 디스크를 다 채우면 DB 가 멈춘다.
superuser 만 바꾸는 설정이라 배포가 권한을 준 설치에서만 걸린다(`temp_limit_allowed`).

상한은 `SET LOCAL` 이라 그 요청의 트랜잭션에만 걸린다 — 작업(내보내기 · 지표 계산)은 따로
돈다. 넘으면 PostgreSQL 이 질의를 취소하고(`57014`), 오류 처리기가 「좁히거나 지표로」 를
504 로 낸다(`errors.register_error_handlers`). 503 이 아닌 까닭: nginx 가 503 이면 다른 서버로
다시 보내 같은 무거운 질의가 한 번 더 돈다.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.config import get_settings

log = logging.getLogger(__name__)

#: PostgreSQL 의 「질의 취소」 — statement_timeout 을 넘겼거나 누가 취소했다.
QUERY_CANCELED = "57014"
#: 「임시 파일이 temp_file_limit 를 넘었다」(configuration_limit_exceeded).
TEMP_LIMIT_EXCEEDED = "53400"

#: `work_mem` 으로 받는 모양 — 틀린 값은 걸지 않고 서버 설정을 따른다(기동은 막지 않는다).
_MEMORY_RE = re.compile(r"^[1-9][0-9]*(kB|MB|GB)$")

#: 계획기가 조인 순서를 **다 따져 보는** 조인 수. 기본(8 · GEQO 12)이면 네 걸음 기준과 세
#: 걸음 세부 기준에 조건까지 건 통계(조인 17개)가 순서를 어림으로 골라 100초 걸려 끊겼고,
#: 20 이면 같은 질의가 13~20초였다(기록 2,000만, ADR 0031). 조인이 8개 아래인 보통 질의에는
#: 아무 차이가 없고, 계획에 드는 시간도 상한 안에 든다.
PLANNER_JOINS = 20


def tuning() -> dict[str, str]:
    """상한을 거는 읽기에 거는 설정 — 이름 → 값. 개발 · 운영이 같은 `.env` 로 정한다."""
    settings = get_settings()
    out = {
        "join_collapse_limit": str(PLANNER_JOINS),
        "from_collapse_limit": str(PLANNER_JOINS),
        "geqo_threshold": str(PLANNER_JOINS),
    }
    if _MEMORY_RE.match(settings.read_work_mem):
        # 2,000만 건 통계의 정렬 · 해시가 디스크로 안 넘어가게(ADR 0031 — 병렬과 함께 25~30%).
        out["work_mem"] = settings.read_work_mem
    if settings.read_parallel_workers >= 0:
        out["max_parallel_workers_per_gather"] = str(settings.read_parallel_workers)
    if settings.read_query_seconds > 0:
        out["statement_timeout"] = str(max(1, round(settings.read_query_seconds * 1000)))
    if _MEMORY_RE.match(settings.read_temp_limit) and temp_limit_allowed(
        settings.read_temp_limit
    ):
        # 정렬 · 해시가 디스크로 넘어가는 크기의 상한 — 넘으면 그 질의만 실패한다(ADR 0048).
        # 없으면 한 질의가 디스크를 다 채워 DB 가 멈춘다.
        out["temp_file_limit"] = settings.read_temp_limit
    return out


#: 걸어 본 답 — 값마다. **확실한 「못 건다」**(권한 없음 · 틀린 값)만 기억한다 — 기동 직후 DB
#: 가 잠깐 안 닿은 것을 기억하면 그 프로세스는 재시작까지 상한 없이 돈다.
_TEMP_ALLOWED: dict[str, bool] = {}
_DEFINITELY_NOT = {"42501", "22023"}  # insufficient_privilege · invalid_parameter_value


def temp_limit_allowed(value: str) -> bool:
    """이 접속 계정이 `temp_file_limit` 를 걸 수 있나 — 프로세스마다 값마다 한 번 **걸어
    본다**.

    superuser 만 바꾸는 설정이라 배포가 권한을 준다(`GRANT SET ON PARAMETER`, PostgreSQL 15
    부터 — `deploy.sh`). 권한 없이 걸면 그 요청이 통째로 실패하므로, 걸어 본 것이 된 때만 건다.
    """
    known = _TEMP_ALLOWED.get(value)
    if known is not None:
        return known
    from app.database import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT set_config('temp_file_limit', :v, true)"), {"v": value})
    except DBAPIError as caught:
        if _sqlstate(caught) in _DEFINITELY_NOT:
            log.info(
                "임시 파일 상한(%s)을 걸 수 없어 서버 설정을 따릅니다: %s", value, caught.orig
            )
            _TEMP_ALLOWED[value] = False
        else:
            log.warning("임시 파일 상한을 걸 수 있는지 보지 못했습니다(다음 요청에 다시)")
        return False
    _TEMP_ALLOWED[value] = True
    return True


def cap(db: Session) -> None:
    """이 트랜잭션의 질의마다 `read_query_seconds` 상한을 걸고(0 이면 안 건다), 이어진 것
    너머를 많이 걷는 질의를 위해 계획기를 넓히고(`PLANNER_JOINS`), 큰 묶기에 메모리 · 병렬
    작업자를 준다(`read_work_mem` · `read_parallel_workers`). **한 번 왕복**으로 — 요청마다
    붙는 일이라 설정 하나에 한 번씩 보내면 그만큼 늦다. `set_config(…, true)` 는 `SET LOCAL`
    과 같다."""
    found = tuning()
    calls = ", ".join(f"set_config(:n{i}, :v{i}, true)" for i in range(len(found)))
    params: dict[str, Any] = {}
    for i, (name, value) in enumerate(found.items()):
        params[f"n{i}"], params[f"v{i}"] = name, value
    db.execute(text(f"SELECT {calls}"), params)


def cancelled(caught: BaseException) -> bool:
    """시간 상한(또는 취소)으로 끊긴 질의인가 — SQLAlchemy 가 감싼 psycopg 오류의 sqlstate."""
    return _sqlstate(caught) == QUERY_CANCELED


def temp_exceeded(caught: BaseException) -> bool:
    """임시 파일 상한(`read_temp_limit`)을 넘겨 끊긴 질의인가."""
    return _sqlstate(caught) == TEMP_LIMIT_EXCEEDED


def _sqlstate(caught: BaseException) -> str | None:
    orig: Any = getattr(caught, "orig", None)
    return getattr(orig, "sqlstate", None)
