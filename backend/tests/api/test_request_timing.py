"""느린 요청 · 큰 응답(ADR 0026) — 요청마다 걸린 시간 · 응답 크기를 남기고, 묶어서 보인다.

작업 목록 한 번이 93MB · 4.4초였던 것을 사용자가 「화면이 느리다」 고 말하고 나서야 알았다
(2026-10-09). 그 뒤로는 사람보다 먼저 보려고 남긴다 — 여기서는 그 수가 맞는지 본다:

- 미들웨어가 **마지막 바이트까지**의 시간 · 보낸 바이트 · 경로 틀을 남기나(스트리밍 · 500 포함)
- 묶음의 p50 · p95 · 정렬이 맞나, 최악 사례가 그 순서인가
- 시스템 관리자만 보나, 「남은 일」 의 문턱(파일 응답 · 적은 호출은 빼고)이 맞나
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.modules.accounts.models import User
from app.modules.audit.models import RequestTiming
from app.modules.jobs.models import Job
from app.modules.server import performance
from app.shared.access_log import UNMATCHED_ROUTE, AccessLogMiddleware
from app.shared.request_context import RequestIdMiddleware
from app.shared.request_timing import writer
from tests.api.conftest import Signed, maintenance_counts


def _rows(db: Session, **match: Any) -> list[RequestTiming]:
    """모인 것을 넣고 그 줄을 읽는다 — 기록은 몇 초씩 모아 넣으므로 시험은 직접 넣는다."""
    writer.flush()
    db.expire_all()
    stmt = select(RequestTiming)
    for key, value in match.items():
        stmt = stmt.where(getattr(RequestTiming, key) == value)
    return list(db.scalars(stmt))


def _user_id(db: Session, who: Signed) -> uuid.UUID:
    found = db.scalar(select(User.id).where(User.email == who.email))
    assert found is not None
    return found


def _put(
    db: Session,
    route: str,
    durations: list[int],
    *,
    sizes: list[int] | None = None,
    content_type: str = "application/json",
    method: str = "GET",
    ago: timedelta = timedelta(minutes=5),
    user_id: uuid.UUID | None = None,
) -> None:
    """합성 기록 — 경로 틀은 시험마다 새로 지어 남의 줄과 안 섞인다."""
    at = datetime.now(UTC) - ago
    db.add_all(
        [
            RequestTiming(
                created_at=at - timedelta(seconds=index),
                method=method,
                route=route,
                path=route.replace("{id}", str(index)),
                status_code=200,
                duration_ms=duration,
                response_bytes=(sizes[index] if sizes else 100),
                content_type=content_type,
                user_id=user_id,
                request_id=f"t{index}",
            )
            for index, duration in enumerate(durations)
        ]
    )
    db.commit()


def _route() -> str:
    return f"/api/perf-{uuid.uuid4().hex[:8]}/{{id}}"


def _all(client: TestClient, who: Signed, period: str, sort: str) -> list[dict[str, Any]]:
    """묶음 전부 — 시험 DB 를 스위트가 함께 쓰므로 남의 경로가 쪽을 채울 수 있다."""
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        got = client.get(
            "/api/server/performance/requests",
            params={"period": period, "sort": sort, "limit": 200, "offset": offset},
            headers=who.headers,
        )
        assert got.status_code == 200, got.text
        body = got.json()
        items.extend(body["items"])
        offset += 200
        if offset >= body["total"]:
            return items


def _one(items: list[dict[str, Any]], route: str) -> dict[str, Any]:
    found = [one for one in items if one["route"] == route]
    assert len(found) == 1, route
    return found[0]


# --- 미들웨어 ---------------------------------------------------------------------


def test_요청마다_걸린_시간_크기_경로_틀을_남긴다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """원 경로가 아니라 **경로 틀**로도 남는다 — 객체마다 다른 주소로는 묶을 수 없다.
    조회(GET)도 남는다: 93MB 였던 작업 목록이 조회였다."""
    missing = uuid.uuid4()
    got = client.get(f"/api/jobs/{missing}", headers=admin.headers)
    assert got.status_code == 404, got.text
    rows = _rows(db, request_id=got.headers["x-request-id"])
    assert len(rows) == 1
    one = rows[0]
    assert one.route == "/api/jobs/{job_id}"
    assert one.path == f"/api/jobs/{missing}"
    assert (one.method, one.status_code) == ("GET", 404)
    assert one.response_bytes == len(got.content)
    assert one.content_type == "application/json"
    assert one.duration_ms >= 0
    assert one.user_id == _user_id(db, admin)


def test_폴링_경로는_안_남기고_없는_경로는_하나로_모은다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """헬스체크 · 배지 폴링은 접근 로그처럼 뺀다. 맞는 라우트가 없던 요청은 원 경로로 묶으면
    아무 주소나 두드리는 요청이 묶음을 끝없이 늘린다."""
    health = client.get("/api/health")
    assert not _rows(db, request_id=health.headers["x-request-id"])

    stray = client.get(f"/api/no-such-{uuid.uuid4().hex[:8]}", headers=admin.headers)
    assert stray.status_code == 404
    rows = _rows(db, request_id=stray.headers["x-request-id"])
    assert [one.route for one in rows] == [UNMATCHED_ROUTE]


@pytest.fixture
def probe() -> Iterator[TestClient]:
    """스트리밍 · 처리 못 한 예외를 내는 작은 앱 — 같은 미들웨어를 같은 순서로 두른다.

    라우터를 두 겹으로 둔다(`/api` 밖 · `/timing-probe` 안) — 라우트의 `path_format` 은 자기
    라우터 안의 경로라, 바깥 접두어를 다시 붙이는 길을 시험이 지나가야 한다.
    """
    app = FastAPI()
    inner = APIRouter(prefix="/timing-probe")

    @inner.get("/stream/{count}")
    def stream(count: int) -> StreamingResponse:
        def chunks() -> Iterator[bytes]:
            for index in range(count):
                if index:
                    time.sleep(0.1)
                yield b"x" * 1000

        return StreamingResponse(chunks(), media_type="application/octet-stream")

    @inner.get("/boom/{token}")
    def boom(token: str) -> None:
        raise RuntimeError(f"시험용 실패 {token}")

    outer = APIRouter(prefix="/api")
    outer.include_router(inner)
    app.include_router(outer)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.state.session_factory = SessionLocal
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_스트리밍은_마지막_바이트까지_잰다(probe: TestClient, db: Session) -> None:
    """첫 바이트까지만 재면 큰 응답 · 스트리밍 · 내려받기가 빠른 요청으로 보인다 — 조각 셋
    사이에 0.1초씩 쉬면 0.2초 넘게 걸린 것으로 남아야 한다."""
    got = probe.get("/api/timing-probe/stream/3")
    assert got.status_code == 200
    assert len(got.content) == 3000
    rows = _rows(db, request_id=got.headers["x-request-id"])
    assert len(rows) == 1
    one = rows[0]
    assert one.route == "/api/timing-probe/stream/{count}"
    assert one.response_bytes == 3000
    assert one.content_type == "application/octet-stream"
    assert one.duration_ms >= 200


def test_처리_못_한_예외도_500_으로_남는다(probe: TestClient, db: Session) -> None:
    """느려서 터진 요청이 가장 보고 싶은 줄이다 — 예외가 미들웨어를 지나가도 남긴다."""
    token = uuid.uuid4().hex[:8]
    got = probe.get(f"/api/timing-probe/boom/{token}")
    assert got.status_code == 500
    rows = _rows(db, path=f"/api/timing-probe/boom/{token}")
    assert [(one.route, one.status_code) for one in rows] == [
        ("/api/timing-probe/boom/{token}", 500)
    ]


# --- 묶음 · 최악 사례 ----------------------------------------------------------------


def test_경로_틀마다_p50_p95_최대_크기를_SQL_로_센다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    fast, slow, old = _route(), _route(), _route()
    # 0 ~ 100ms 101번 — p50 50 · p95 95 가 딱 떨어진다.
    _put(db, fast, list(range(101)), sizes=[1000] * 100 + [9000])
    _put(db, slow, [4000] * 20, sizes=[6 * 1024 * 1024] + [10] * 19)
    # 한 시간 밖 — 「최근 1시간」 에는 없고 「24시간」 에는 있다.
    _put(db, old, [50] * 3, ago=timedelta(hours=3))

    hour = _all(client, admin, "1h", "p95")
    one = _one(hour, fast)
    assert (one["calls"], one["p50_ms"], one["p95_ms"], one["max_ms"]) == (101, 50, 95, 100)
    assert one["max_bytes"] == 9000
    assert one["avg_bytes"] == round((1000 * 100 + 9000) / 101)
    assert (one["file"], one["slow"], one["large"]) == (False, False, False)
    heavy = _one(hour, slow)
    assert (heavy["p95_ms"], heavy["slow"], heavy["large"]) == (4000, True, True)
    assert not [item for item in hour if item["route"] == old]
    assert _one(_all(client, admin, "24h", "p95"), old)["calls"] == 3

    # 느린 순 · 큰 순 · 많이 불린 순
    order = [item["route"] for item in hour]
    assert order.index(slow) < order.index(fast)
    by_bytes = [item["route"] for item in _all(client, admin, "1h", "max_bytes")]
    assert by_bytes.index(slow) < by_bytes.index(fast)
    by_calls = [item["route"] for item in _all(client, admin, "1h", "calls")]
    assert by_calls.index(fast) < by_calls.index(slow)


def test_묶음_응답이_문턱과_기록_시작을_함께_준다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """빈 목록이 「그때 요청이 없었다」 인지 「아직 안 남겼다」 인지 가르는 값과, 화면이
    표시를 달 문턱을 같은 응답에 싣는다."""
    _put(db, _route(), [5])
    got = client.get("/api/server/performance/requests", headers=admin.headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["period"] == "24h"
    assert body["capped"] is False
    assert body["first_recorded_at"] is not None
    settings = get_settings()
    assert body["thresholds"] == {
        "p95_ms": round(settings.slow_request_p95_seconds * 1000),
        "min_calls": settings.slow_request_min_calls,
        "response_bytes": round(settings.large_response_mb * 1024 * 1024),
    }


def test_기록이_상한보다_많으면_최근_쪽만_세고_그렇게_말한다(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _put(db, _route(), [1, 2, 3, 4, 5])
    monkeypatch.setattr(performance, "MAX_ROWS", 2)
    got = client.get(
        "/api/server/performance/requests", params={"period": "7d"}, headers=admin.headers
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["capped"] is True
    since = datetime.fromisoformat(body["since"])
    assert since > datetime.now(UTC) - timedelta(days=7) + timedelta(minutes=1)


def test_최악_사례는_오래_걸린_순_큰_순이고_누가_불렀나를_싣는다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    route = _route()
    _put(db, route, [10, 500, 30], sizes=[1000, 10, 99999], user_id=_user_id(db, admin))
    got = client.get(
        "/api/server/performance/requests/cases",
        params={"route": route, "method": "GET", "period": "1h"},
        headers=admin.headers,
    )
    assert got.status_code == 200, got.text
    cases = got.json()
    assert [one["duration_ms"] for one in cases] == [500, 30, 10]
    assert {one["user_label"] for one in cases} == {"admin"}
    assert cases[0]["path"] == route.replace("{id}", "1")
    assert cases[0]["request_id"] == "t1"

    by_size = client.get(
        "/api/server/performance/requests/cases",
        params={"route": route, "method": "GET", "period": "1h", "order": "bytes"},
        headers=admin.headers,
    )
    assert [one["response_bytes"] for one in by_size.json()] == [99999, 1000, 10]

    other = client.get(
        "/api/server/performance/requests/cases",
        params={"route": route, "method": "POST", "period": "1h"},
        headers=admin.headers,
    )
    assert other.json() == []


# --- 작업 종류별 소요 시간 ----------------------------------------------------------


def test_작업_종류마다_소요와_대기를_센다(
    client: TestClient, admin: Signed, db: Session
) -> None:
    """대기는 들어와 워커가 집기까지, 소요는 집어 끝낼 때까지. 아직 대기인 것은 집힌 것만
    세는 대기 시간에 안 잡히므로 따로 「가장 오래 기다린 것」 으로 낸다 — 워커가 멎으면 거기만
    는다."""
    kind = f"perf_probe_{uuid.uuid4().hex[:8]}"
    base = datetime.now(UTC) - timedelta(minutes=30)
    finished = [("done", 2, 10), ("done", 4, 20), ("done", 6, 30), ("failed", 8, 40)]
    jobs = [
        Job(
            kind=kind,
            status=status,
            created_at=base,
            started_at=base + timedelta(seconds=wait),
            finished_at=base + timedelta(seconds=wait + run),
        )
        for status, wait, run in finished
    ]
    jobs.append(Job(kind=kind, status="queued", created_at=base + timedelta(minutes=10)))
    db.add_all(jobs)
    db.commit()
    try:
        got = client.get(
            "/api/server/performance/jobs", params={"period": "1h"}, headers=admin.headers
        )
        assert got.status_code == 200, got.text
        found = [one for one in got.json()["items"] if one["kind"] == kind]
        assert len(found) == 1
        one = found[0]
        assert (one["done"], one["failed"], one["cancelled"], one["open"]) == (3, 1, 0, 1)
        assert one["label"] == kind  # 등록되지 않은 종류는 이름 그대로
        assert (one["run_p50_s"], one["run_p95_s"], one["run_max_s"]) == (25.0, 38.5, 40.0)
        assert (one["wait_p50_s"], one["wait_p95_s"], one["wait_max_s"]) == (5.0, 7.7, 8.0)
        assert one["queued_oldest_s"] >= 20 * 60 - 5
    finally:
        # **대기인 작업을 남기지 않는다** — 뒤의 시험이 워커를 돌리면 모르는 종류를 집는다.
        db.execute(delete(Job).where(Job.kind == kind))
        db.commit()


# --- 권한 · 「남은 일」 · 정리 --------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/server/performance/requests",
        "/api/server/performance/requests/cases?route=/api/x&method=GET",
        "/api/server/performance/jobs",
    ],
)
def test_시스템_관리자만_본다(
    client: TestClient, manager: Signed, member: Signed, path: str
) -> None:
    """누가 언제 무엇을 불렀나가 함께 나간다 — 접근 로그와 같은 무게다."""
    assert client.get(path).status_code == 401
    for who in (manager, member):
        got = client.get(path, headers=who.headers)
        assert got.status_code == 403, got.text


def test_문턱을_넘은_경로가_시스템_관리자의_남은_일에_뜬다(
    client: TestClient,
    admin: Signed,
    member: Signed,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """p95 가 문턱을 넘은 경로 · 문턱보다 큰 응답을 낸 경로를 센다. **파일 응답과 몇 번 안 불린
    경로는 뺀다** — 내려받기는 크고 받는 쪽 속도를 타는 것이 정상이고, 한두 번 느린 것은 찬
    캐시일 수 있다. 둘을 세면 그 줄은 늘 떠 있는 잡음이 된다."""
    monkeypatch.setattr(performance, "CACHE_SECONDS", 0.0)
    before = maintenance_counts(client, admin)

    limits = performance.thresholds()
    over = limits.p95_ms + 1000
    _put(db, _route(), [over] * limits.min_calls)  # 느림
    _put(db, _route(), [over] * limits.min_calls, content_type="application/zip")  # 파일
    _put(db, _route(), [over] * (limits.min_calls - 1))  # 너무 적게 불림
    _put(db, _route(), [5], sizes=[limits.response_bytes + 1])  # 큼
    _put(db, _route(), [5], sizes=[limits.response_bytes + 1], content_type="image/png")

    after = maintenance_counts(client, admin)
    assert after.get("slow_requests", 0) == before.get("slow_requests", 0) + 1
    assert after.get("large_responses", 0) == before.get("large_responses", 0) + 1
    # 고칠 수 없는 사람에게는 안 뜬다.
    seen = maintenance_counts(client, member)
    assert "slow_requests" not in seen
    assert "large_responses" not in seen

    # 문턱을 올리면(`.env`) 그 경로는 더 이상 느린 것이 아니다.
    monkeypatch.setattr(get_settings(), "slow_request_p95_seconds", (over + 1000) / 1000)
    raised = maintenance_counts(client, admin)
    assert raised.get("slow_requests", 0) < after["slow_requests"]


def test_보관_기간이_지난_기록은_워커가_지운다(db: Session) -> None:
    keep, gone = _route(), _route()
    days = get_settings().log_retention_days
    _put(db, keep, [1])
    _put(db, gone, [1, 2], ago=timedelta(days=days, hours=1))
    assert performance.purge_old_timings(db) >= 2
    db.expire_all()
    assert len(_rows(db, route=keep)) == 1
    assert _rows(db, route=gone) == []
