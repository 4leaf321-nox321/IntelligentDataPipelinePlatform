"""API 시험이 쓰는 준비물.

**진짜 앱을 부른다.** 서비스 함수를 직접 부르면 라우터·의존성·권한 판정이 통째로
빠지는데, 실제로 깨지는 자리는 대개 거기다.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.auth import security
from app.modules.workspaces.models import Workspace, WorkspaceMember

PASSWORD = "test-account-password"


@dataclass(frozen=True)
class Signed:
    """로그인한 사람. 헤더를 매번 손으로 만들지 않게 한다."""

    email: str
    token: str
    workspace: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _login(client: TestClient, email: str) -> str:
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _make_user(
    db: Session,
    workspace: Workspace,
    *,
    label: str,
    is_system_admin: bool,
    role: str,
) -> User:
    email = f"{label}-{uuid.uuid4().hex[:8]}@example.local"
    user = User(
        email=email,
        password_hash=security.hash_password(PASSWORD),
        display_name=label,
        status="active",
        is_system_admin=is_system_admin,
        home_workspace_id=workspace.id,
    )
    db.add(user)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role))
    db.commit()
    return user


@pytest.fixture
def department_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """조회를 **전역 + 내 부서**로 좁힌 설치(`VIEW_SCOPE=department`).

    기본은 누구나 전부 본다(ADR 0034). 남의 부서 것을 가리는 길(이름 가리기 · 수만 말하기 ·
    없는 것과 같게)은 그대로 남아 있고, 이 설정을 켠 설치에서 다시 쓰인다 — 그 길을 지키는
    시험은 이것을 켜고 돈다. 안 켜면 가릴 것이 없어 그 시험들은 아무것도 지키지 못한다."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "view_scope", "department")


@pytest.fixture
def workspace(db: Session) -> Workspace:
    row = Workspace(slug=f"team-{uuid.uuid4().hex[:8]}", name="시험팀")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture
def admin(client: TestClient, db: Session, workspace: Workspace) -> Signed:
    """시스템 관리자 한 명. 첫 관리자는 설치 시드처럼 직접 만든다."""
    user = _make_user(db, workspace, label="admin", is_system_admin=True, role="manager")
    return Signed(email=user.email, token=_login(client, user.email), workspace=workspace.slug)


@pytest.fixture
def manager(client: TestClient, db: Session, workspace: Workspace) -> Signed:
    """시스템 관리자가 **아닌** 부서 관리자.

    부서 소유 자산(첨부·객체)을 만들려면 그 부서의 관리자여야 한다
    (`resolve_owner_workspace`). 시스템 관리자로만 시험하면 **그 문턱이 실제로
    있는지**를 아무도 안 보게 된다.
    """
    user = _make_user(db, workspace, label="manager", is_system_admin=False, role="manager")
    return Signed(email=user.email, token=_login(client, user.email), workspace=workspace.slug)


@pytest.fixture
def member(client: TestClient, db: Session, workspace: Workspace) -> Signed:
    """같은 부서의 평범한 멤버. **권한 시험에는 관리자 아닌 사람이 필요하다** —
    관리자만으로 도는 시험은 무엇도 막지 못한다."""
    user = _make_user(db, workspace, label="member", is_system_admin=False, role="member")
    return Signed(email=user.email, token=_login(client, user.email), workspace=workspace.slug)


def maintenance_counts(client: TestClient, who: Signed) -> dict[str, int]:
    """홈 「남은 일」 을 {열쇠: 수} 로. **시험 DB 를 스위트가 함께 쓰므로 차이로 본다** —
    남이 남긴 줄이 이미 있을 수 있다."""
    got = client.get("/api/server/maintenance", headers=who.headers)
    # **봉투가 오면 여기서 말한다.** 안 그러면 「문자열 인덱스」 라는 엉뚱한 오류가 나고,
    # 진짜 원인(어느 제공자가 터졌나)은 아무 데도 안 적힌다.
    assert got.status_code == 200, got.text
    return {one["key"]: one["count"] for one in got.json()}


def notifications_of(client: TestClient, who: Signed, kind: str) -> list[dict[str, Any]]:
    got = client.get("/api/notifications", headers=who.headers)
    assert got.status_code == 200, got.text
    body: Any = got.json()
    rows: list[dict[str, Any]] = body["items"] if isinstance(body, dict) else body
    return [one for one in rows if one["kind"] == kind]


#: 「나중 일」 이 그 앞의 시각을 넘어야 하는 자리 — 바뀐 것을 시각으로 가르는 곳 전부. 1분
#: 넘게 앞선 시각은 시계 튐이 아니라 시험이 심은 값이다 — 그것을 기다리면 부를 때마다 상한까지
#: 선다.
_LATEST_WRITE = """
    WITH marks(at) AS (
        SELECT max(created_at) FROM audit_entries
        UNION ALL SELECT max(created_at) FROM access_logs
    )
    SELECT max(at) FROM marks WHERE at < clock_timestamp() + interval '1 minute'
"""


def clock_settled(after: datetime | None = None, *, limit: float = 10.0) -> None:
    """**DB 시계가 지금까지 찍힌 가장 늦은 시각(과 `after`)을 지날 때까지** 기다린다 —
    「나중 일」 을 일으키기 직전에 부른다. 시계가 그대로 갔으면 질의 한 번으로 지나간다.

    이 PC(WSL)의 벽시계는 뒤로 튄다(2026-10-25 실측 — 7분에 13번, 가장 큰 것 2.3초, 단조
    시계와 32초 어긋남). 그러면 나중 일이 앞의 일보다 이른 시각으로 찍힌다: 계산 전에 쓴
    기록이 계산의 워터마크 뒤로 읽혀 증분이 전량으로 가고, 「여기까지 봤다」 뒤의 통폐합 ·
    다시 받기의 시작이 앞으로 찍혀 빠지고, 나중 거절이 앞 거절보다 이르게 찍힌다. 시험이
    바라는 시간 차례를 되살릴 뿐이다 — 제품은 시계가 앞으로만 간다고 본다(운영 서버는 시각
    동기화가 천천히 맞춘다).
    """
    from sqlalchemy import text

    from app.database import SessionLocal

    with SessionLocal() as db:
        latest = db.scalar(text(_LATEST_WRITE))
    marks = [one for one in (latest, after) if one is not None]
    if not marks:
        return
    target = max(marks)
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            if db.scalar(text("SELECT clock_timestamp() > :at"), {"at": target}):
                return
        time.sleep(0.05)


def finish_job(client: TestClient, who: Signed, job: dict[str, Any]) -> dict[str, Any]:
    """워커를 **이 프로세스에서** 돌려 그 작업이 끝날 때까지. 시험은 워커 프로세스를 안
    띄운다 — 같은 코드를 같은 DB 로 부르면 그것이 곧 워커다. 남이 넣은 작업이 앞에 있어도
    집어 준다."""
    from app.modules.jobs import services as job_services

    for _ in range(50):
        got = client.get(f"/api/jobs/{job['id']}", headers=who.headers)
        assert got.status_code == 200, got.text
        current: dict[str, Any] = got.json()
        if current["status"] in ("done", "failed", "cancelled"):
            return current
        if not job_services.process_one("test-worker"):
            break
    got = client.get(f"/api/jobs/{job['id']}", headers=who.headers)
    return dict(got.json())


def work_until(db: Session, job_id: uuid.UUID) -> None:
    """워커를 **그 작업이 끝날 때까지** 돌린다(이 프로세스에서). 큐 앞에 남의 작업이 있어도.

    한 번만 돌리고 「내 것이 집혔다」 고 믿으면 안 된다 — 병렬 시험(`-n`)에서는 같은 작업자에서
    앞서 돈 시험이 남긴 작업이 큐 앞에 있을 수 있다(워커는 오래된 것부터 집는다). 순서대로
    돌 때는 사이의 시험들이 그것을 비워 줘서 우연히 맞았다.
    """
    from sqlalchemy import select

    from app.modules.jobs import services as job_services
    from app.modules.jobs.models import Job

    for _ in range(50):
        db.expire_all()
        status = db.scalar(select(Job.status).where(Job.id == job_id))
        if status not in ("queued", "running"):
            return
        if not job_services.process_one("test-worker"):
            return
