"""느린 요청 · 큰 응답 — **사람이 말하기 전에 본다**(ADR 0026).

작업 목록 한 번이 93MB · 4.4초였던 것을, 사용자가 「적용을 누르면 화면이 너무 느리다」 고
말하고 나서야 알았다(2026-10-09). 그 수치는 그 전부터 요청마다 났는데 남는 곳이 없었다.
미들웨어가 요청마다 걸린 시간 · 응답 크기를 남기고(`shared/access_log.py`), 여기서 **경로
틀**마다 묶어 서버 화면과 홈 「남은 일」 에 낸다.

작업(`jobs`)이 얼마나 걸리고 얼마나 기다리는지도 같은 자리에서 센다 — 오래 걸리는 일을 요청에서
작업으로 옮겼는데 거기서 느려지면, 다시 아무도 모르게 된다.

**SQL 이 센다**(`percentile_cont`). 파이썬으로 가져와 세면 그 가져오기가 가장 느린 요청이 된다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import ColumnElement, Float, String, cast, delete, extract, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, aggregate_order_by
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.audit.models import CutQuestion, RequestTiming
from app.modules.jobs import kinds
from app.modules.jobs.models import Job
from app.shared import cut_questions, extensions

Period = Literal["1h", "24h", "7d"]
PERIODS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}

RequestSort = Literal["p95", "max_ms", "max_bytes", "avg_bytes", "calls"]
CaseOrder = Literal["duration", "bytes"]

MAX_ROWS = 1_000_000
"""집계 한 번이 세는 **기록의 상한**. 넘으면 기간을 최근 쪽으로 좁히고 그렇다고 말한다
(`capped`).

묶어서 백분위를 내려면 기간의 기록을 통째로 정렬해야 한다 — 경로 틀 600개에 70만 줄이 0.6초,
100만 줄이 0.55 ~ 0.6초였다(2026-10-10 실측, 상한을 찾는 데 0.05초). 하루 백만 요청인 설치에서
7일을 그대로 세면 수 초가 되고, 그 집계를 홈이 부른다. 최근 100만 건이면 1초 안쪽이고,
그만큼이면 「어디가 느린가」 에 답하기에 충분하다. `work_mem` 을 늘려도 · 경로를 해시로 묶어도
빨라지지 않았다(정렬보다 묶음마다의 백분위가 무겁다)."""

CACHE_SECONDS = 300.0
"""홈 「남은 일」 의 판정을 이만큼 기억한다 — 시스템 관리자가 홈을 열 때마다 24시간을 다시
묶지 않게. 문턱은 몇 분 늦게 떠도 되는 일이다."""

PURGE_BATCH = 50_000
PURGE_MAX_BATCHES = 20
"""한 번에 지우는 양 — 보관 기간을 줄인 날 수백만 줄을 한 문장으로 지우면 워커 바퀴가 그동안
멎는다(박동도). 한 시간에 백만 줄까지, 남은 것은 다음 차례에."""


@dataclass(frozen=True)
class Thresholds:
    """「남은 일」 의 문턱 — `.env` 로 바꾼다(`config.py`)."""

    p95_ms: int
    min_calls: int
    response_bytes: int


def thresholds() -> Thresholds:
    settings = get_settings()
    return Thresholds(
        p95_ms=round(settings.slow_request_p95_seconds * 1000),
        min_calls=settings.slow_request_min_calls,
        response_bytes=round(settings.large_response_mb * 1024 * 1024),
    )


@dataclass(frozen=True)
class Window:
    """실제로 센 기간. 기록이 상한보다 많으면 `since` 가 기간의 시작보다 늦다."""

    since: datetime
    capped: bool


def window(db: Session, period: Period) -> Window:
    since = datetime.now(UTC) - PERIODS[period]
    # 최근 쪽에서 상한 번째 기록의 시각 — 색인(`created_at`)만 훑는다.
    cutoff = db.scalar(
        select(RequestTiming.created_at)
        .where(RequestTiming.created_at >= since)
        .order_by(RequestTiming.created_at.desc())
        .offset(MAX_ROWS)
        .limit(1)
    )
    if cutoff is None:
        return Window(since=since, capped=False)
    return Window(since=cutoff, capped=True)


def first_recorded_at(db: Session) -> datetime | None:
    """가장 오래된 기록 — 빈 화면이 「아직 안 남겼다」 인지 「그때 없었다」 인지 말한다."""
    return db.scalar(select(func.min(RequestTiming.created_at)))


def _is_json() -> ColumnElement[bool]:
    kind = RequestTiming.content_type
    return or_(kind == "application/json", kind.like("%+json"))


@dataclass(frozen=True)
class RouteStat:
    method: str
    route: str
    calls: int
    p50_ms: int
    p95_ms: int
    max_ms: int
    avg_bytes: int
    max_bytes: int
    file: bool
    """대부분이 JSON 이 아닌 응답(파일 · 사진)인가. 그런 경로는 크고 받는 쪽 속도를 타는 것이
    정상이라 문턱 판정에서 뺀다."""
    slow: bool
    large: bool


def route_stats(
    db: Session,
    since: datetime,
    *,
    sort: RequestSort = "p95",
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[RouteStat], int]:
    """경로 틀 · 메서드마다 — 호출 수 · p50 · p95 · 최대 · 평균/최대 크기. (줄, 묶음 수)."""
    t = RequestTiming
    calls = func.count().label("calls")
    p50 = func.percentile_cont(0.5).within_group(t.duration_ms).label("p50")
    p95 = func.percentile_cont(0.95).within_group(t.duration_ms).label("p95")
    max_ms = func.max(t.duration_ms).label("max_ms")
    avg_bytes = func.avg(t.response_bytes).label("avg_bytes")
    max_bytes = func.max(t.response_bytes).label("max_bytes")
    json_calls = func.count().filter(_is_json()).label("json_calls")
    groups = func.count().over().label("groups")
    order = {
        "p95": p95,
        "max_ms": max_ms,
        "max_bytes": max_bytes,
        "avg_bytes": avg_bytes,
        "calls": calls,
    }[sort]
    stmt = (
        select(t.method, t.route, calls, p50, p95, max_ms, avg_bytes, max_bytes)
        .add_columns(json_calls, groups)
        .where(t.created_at >= since)
        .group_by(t.route, t.method)
        .order_by(order.desc(), t.route, t.method)
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = db.execute(stmt).all()

    if rows:
        total = int(rows[0].groups)
    elif offset:
        # 쪽을 넘겨 빈 자리에 왔다 — 묶음 수는 따로 센다(창 함수가 실릴 줄이 없다).
        total = int(
            db.scalar(
                select(func.count()).select_from(
                    select(t.route, t.method)
                    .where(t.created_at >= since)
                    .group_by(t.route, t.method)
                    .subquery()
                )
            )
            or 0
        )
    else:
        total = 0

    limits = thresholds()
    out: list[RouteStat] = []
    for row in rows:
        file = int(row.json_calls) * 2 < int(row.calls)
        out.append(
            RouteStat(
                method=row.method,
                route=row.route,
                calls=int(row.calls),
                p50_ms=round(row.p50),
                p95_ms=round(row.p95),
                max_ms=int(row.max_ms),
                avg_bytes=round(row.avg_bytes),
                max_bytes=int(row.max_bytes),
                file=file,
                slow=(
                    not file and int(row.calls) >= limits.min_calls and row.p95 > limits.p95_ms
                ),
                large=not file and int(row.max_bytes) > limits.response_bytes,
            )
        )
    return out, total


@dataclass(frozen=True)
class Case:
    """최악 사례 한 줄 — 누가 · 언제 · 상태 · 시간 · 크기 · 요청 ID."""

    created_at: datetime
    user_label: str | None
    path: str
    status_code: int
    duration_ms: int
    response_bytes: int
    content_type: str | None
    request_id: str | None


def cases(
    db: Session,
    *,
    route: str,
    method: str,
    since: datetime,
    order: CaseOrder = "duration",
    limit: int = 20,
) -> list[Case]:
    """그 경로 틀의 **최근 최악 사례** — 가장 오래 걸린 순(또는 가장 큰 순)."""
    t = RequestTiming
    key = t.duration_ms if order == "duration" else t.response_bytes
    rows = db.execute(
        select(t, User.display_name)
        .outerjoin(User, User.id == t.user_id)
        .where(t.route == route, t.method == method, t.created_at >= since)
        .order_by(key.desc(), t.created_at.desc())
        .limit(limit)
    ).all()
    return [
        Case(
            created_at=one.created_at,
            user_label=name,
            path=one.path,
            status_code=one.status_code,
            duration_ms=one.duration_ms,
            response_bytes=one.response_bytes,
            content_type=one.content_type,
            request_id=one.request_id,
        )
        for one, name in rows
    ]


@dataclass(frozen=True)
class JobKindStat:
    """작업 종류 하나 — 끝난 수와 소요 · 대기(초)."""

    kind: str
    label: str
    done: int
    failed: int
    cancelled: int
    open: int
    """지금 대기 · 실행 중인 것."""
    run_p50_s: float | None
    run_p95_s: float | None
    run_max_s: float | None
    """워커가 집어 끝낼 때까지. 끝난 것이 없으면 비어 있다."""
    wait_p50_s: float | None
    wait_p95_s: float | None
    wait_max_s: float | None
    """들어와 워커가 집기까지. 워커가 모자라거나 앞의 작업이 길면 여기가 는다."""
    queued_oldest_s: float | None
    """**아직 대기인 것** 가운데 가장 오래 기다린 것 — 집힌 것만 세는 대기 시간은 아무것도 안
    집히는 동안(워커가 멎었을 때)을 못 본다."""


def _seconds(value: float | None) -> float | None:
    return None if value is None else round(float(value), 1)


def job_stats(db: Session, since: datetime) -> list[JobKindStat]:
    """작업 종류마다 — 그 기간에 들어온 작업의 소요 · 대기. 오래 걸린 종류가 위로."""
    run = cast(extract("epoch", Job.finished_at - Job.started_at), Float)
    wait = cast(extract("epoch", Job.started_at - Job.created_at), Float)
    queued_for = cast(extract("epoch", func.now() - Job.created_at), Float)
    run_p95 = func.percentile_cont(0.95).within_group(run).label("run_p95")
    rows = db.execute(
        # 백분위는 빈 값(아직 안 끝난 · 안 집힌 것)을 건너뛴다 — 거르지 않아도 된다.
        select(
            Job.kind,
            func.count().filter(Job.status == "done"),
            func.count().filter(Job.status == "failed"),
            func.count().filter(Job.status == "cancelled"),
            func.count().filter(Job.status.in_(("queued", "running"))),
            func.percentile_cont(0.5).within_group(run),
            run_p95,
            func.max(run),
            func.percentile_cont(0.5).within_group(wait),
            func.percentile_cont(0.95).within_group(wait),
            func.max(wait),
            func.max(queued_for).filter(Job.status == "queued"),
        )
        .where(Job.created_at >= since)
        .group_by(Job.kind)
        .order_by(run_p95.desc().nulls_last(), Job.kind)
    ).all()
    out: list[JobKindStat] = []
    for row in rows:
        registered = kinds.get(row[0])
        out.append(
            JobKindStat(
                kind=row[0],
                label=registered.label if registered else row[0],
                done=int(row[1]),
                failed=int(row[2]),
                cancelled=int(row[3]),
                open=int(row[4]),
                run_p50_s=_seconds(row[5]),
                run_p95_s=_seconds(row[6]),
                run_max_s=_seconds(row[7]),
                wait_p50_s=_seconds(row[8]),
                wait_p95_s=_seconds(row[9]),
                wait_max_s=_seconds(row[10]),
                queued_oldest_s=_seconds(row[11]),
            )
        )
    return out


#: 문턱 → (판정한 때, 「남은 일」). 문턱을 바꾸면(재시작) 열쇠가 달라진다.
_cached: dict[Thresholds, tuple[float, list[extensions.MaintenanceItem]]] = {}


@dataclass
class CutStat:
    """같은 모양으로 끊긴 질문 묶음 하나 — 몇 번 · 몇 명 · 마지막."""

    kind: str
    kind_label: str
    code: str
    method: str
    route: str
    shape: dict[str, Any]
    count: int
    users: int
    last_at: datetime
    last_request_id: str | None


def cut_stats(db: Session, since: datetime, limit: int = 100) -> tuple[list[CutStat], int]:
    """끊긴 질문(ADR 0031)을 **같은 모양끼리** — 많이 끊긴 것부터. (줄들, 모양 수)."""
    latest = func.array_agg(
        aggregate_order_by(CutQuestion.request_id, CutQuestion.created_at.desc()),
        type_=ARRAY(String),
    )[1]
    keys = (
        CutQuestion.kind,
        CutQuestion.code,
        CutQuestion.method,
        CutQuestion.route,
        CutQuestion.shape,
    )
    grouped = (
        select(
            *keys,
            func.count().label("n"),
            func.count(func.distinct(CutQuestion.user_id)).label("users"),
            func.max(CutQuestion.created_at).label("last_at"),
            latest.label("rid"),
        )
        .where(CutQuestion.created_at >= since)
        .group_by(*keys)
        .subquery()
    )
    total = int(db.scalar(select(func.count()).select_from(grouped)) or 0)
    rows = db.execute(
        select(grouped).order_by(grouped.c.n.desc(), grouped.c.last_at.desc()).limit(limit)
    ).all()
    return [
        CutStat(
            kind=row.kind,
            kind_label=cut_questions.KIND_LABELS.get(row.kind, row.kind),
            code=row.code,
            method=row.method,
            route=row.route,
            shape=dict(row.shape or {}),
            count=int(row.n),
            users=int(row.users),
            last_at=row.last_at,
            last_request_id=row.rid,
        )
        for row in rows
    ], total


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """최근 24시간에 문턱을 넘은 경로 틀이 있으면 **시스템 관리자의 홈**에.

    고칠 수 있는 사람(코드 · 설정)에게만 — 못 고치는 사람에게 띄우면 못 지우는 숫자가 된다.
    `info` 로 둔다: 느린 것은 고장이 아니다. 경고는 멎은 것(워커 · 동기화)의 색이다.
    """
    if not viewer.is_system_admin:
        return []
    limits = thresholds()
    now = time.monotonic()
    hit = _cached.get(limits)
    if hit is not None and now - hit[0] < CACHE_SECONDS:
        return hit[1]

    stats, _ = route_stats(db, window(db, "24h").since)
    slow = sum(1 for one in stats if one.slow)
    large = sum(1 for one in stats if one.large)
    items: list[extensions.MaintenanceItem] = []
    if slow:
        items.append(
            extensions.MaintenanceItem(
                key="slow_requests",
                label=(
                    f"느린 요청 — 최근 24시간 p95 가 {limits.p95_ms / 1000:g}초를 넘은 경로"
                ),
                count=slow,
                link="/admin/server?tab=performance&period=24h&sort=p95",
            )
        )
    if large:
        items.append(
            extensions.MaintenanceItem(
                key="large_responses",
                label=(
                    "큰 응답 — 최근 24시간 "
                    f"{get_settings().large_response_mb:g}MB 를 넘은 응답을 낸 경로"
                ),
                count=large,
                link="/admin/server?tab=performance&period=24h&sort=max_bytes",
            )
        )
    _, cut = cut_stats(db, datetime.now(UTC) - PERIODS["24h"], limit=1)
    if cut:
        # 답을 못 받은 물음 — 지표로 미리 세어 둘 후보(ADR 0031). 느린 것과 달리 사람이 답을
        # 못 받았다.
        items.append(
            extensions.MaintenanceItem(
                key="cut_questions",
                label=(
                    "끊긴 질문 — 최근 24시간 시간 상한 · 크기에 걸려 답을 못 받은 물음"
                    "(모양 수)"
                ),
                count=cut,
                link="/admin/server?tab=performance&period=24h",
            )
        )
    _cached.clear()
    _cached[limits] = (now, items)
    return items


def purge_old_timings(db: Session) -> int:
    """`log_retention_days` 보다 오래된 요청 시간 기록을 지운다 — 워커가 한 시간마다."""
    cut = datetime.now(UTC) - timedelta(days=get_settings().log_retention_days)
    gone = 0
    for _ in range(PURGE_MAX_BATCHES):
        chunk = (
            select(RequestTiming.id)
            .where(RequestTiming.created_at < cut)
            .limit(PURGE_BATCH)
            .scalar_subquery()
        )
        removed = extensions.rows_changed(
            db.execute(delete(RequestTiming).where(RequestTiming.id.in_(chunk)))
        )
        db.commit()
        gone += removed
        if removed < PURGE_BATCH:
            break
    # 끊긴 질문도 같은 보관 기간 — 드물게 생겨 한 번에 지운다.
    gone += extensions.rows_changed(
        db.execute(delete(CutQuestion).where(CutQuestion.created_at < cut))
    )
    db.commit()
    return gone
