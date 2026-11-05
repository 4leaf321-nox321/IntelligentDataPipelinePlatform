"""알림 — 타이머가 돌린 실행 · 대조가 **나빠질 때 · 다시 될 때만** 시스템 관리자에게 간다.

타이머의 작업은 시킨 사람이 없어 작업 알림이 아무에게도 안 간다 — 그 빈자리를 채운다. 같은
실패가 밤마다 되풀이돼도 알림은 하나다. 사람이 시킨 작업은 그 사람에게 보내지 않는다.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.relay import services
from app.modules.workspaces.models import Workspace
from relay_core import reconcile, runner
from relay_core.runner import Outcome
from tests.api.conftest import Signed, _make_user, work_until
from tests.api.test_relay import _definition, _slug
from tests.api.test_relay_reconcile import _baseline, _Submitter
from tests.unit.test_relay_reconcile import SAME, FakeReader


def _timer(db: Session, kind: str, slug: str, **params: Any) -> None:
    from app.modules.jobs import services as job_services

    params = {"slug": slug, "by": "timer", **params}
    job = job_services.enqueue(db, kind=kind, params=params, user=None, workspace_id=None)
    db.commit()
    work_until(db, job.id)


def _bell(db: Session, email: str, slug: str) -> list[tuple[str, str]]:
    """그 사람이 받은 **그 파이프라인의** 알림 — 큐에 남은 다른 시험의 작업도 이 워커가 돌려
    모든 관리자에게 알리므로 파이프라인으로 거른다."""
    from app.modules.accounts.models import User
    from app.modules.notifications.models import Notification

    db.expire_all()
    user_id = db.scalar(select(User.id).where(User.email == email))
    rows = db.scalars(
        select(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.link == f"/relay/pipelines/{slug}",
        )
        .order_by(Notification.created_at)
    )
    return [(one.kind, one.title) for one in rows]


def _scripted(monkeypatch: pytest.MonkeyPatch, slug: str, plan: list[Any]) -> None:
    """`slug` 의 실행 결과를 차례로 — 예외면 작업이 실패한다. **그 파이프라인에만 답한다**:
    같은 작업자에서 앞선 시험이 남긴 작업(DB 에 사용 중으로 남은 다른 파이프라인)이 큐 앞에서
    먼저 돌며 결과를 가로채면 순서가 밀린다(병렬 시험에서 가끔, 2026-11-06)."""

    def run(pipeline: Any, *_args: Any, **_kwargs: Any) -> Outcome:
        if pipeline.slug != slug:
            return Outcome("nothing", "다른 시험의 작업")
        one = plan.pop(0)
        if isinstance(one, Exception):
            raise one
        assert isinstance(one, Outcome)
        return one

    monkeypatch.setattr(runner, "run", run)


def test_실행이_나빠질_때와_다시_될_때만_관리자에게_알린다(
    registry: Any, db: Session, admin: Signed, member: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    _scripted(
        monkeypatch,
        slug,
        [
            RuntimeError("원천이 안 닿습니다"),
            RuntimeError("원천이 안 닿습니다"),  # 같은 실패가 되풀이돼도 알림은 하나
            Outcome("blocked", "미해결 2줄"),
            Outcome("planned", "계획을 생성했습니다"),
            Outcome("nothing", "보낼 것이 없습니다"),
        ],
    )
    _timer(db, services.RUN_KIND, slug)
    _timer(db, services.RUN_KIND, slug)
    _timer(db, services.APPLY_KIND, slug)  # 적용할 계획이 없다 — 「실행 복구」 가 아니다
    for _ in range(3):
        _timer(db, services.RUN_KIND, slug)

    assert _bell(db, admin.email, slug) == [
        ("relay.run.failed", f"{slug} 실행 실패"),
        ("relay.run.failed", f"{slug} 실행 멈춤"),
        ("relay.run.recovered", f"{slug} 실행 복구"),
    ]
    assert _bell(db, member.email, slug) == []  # 고칠 수 없는 사람에게는 안 간다


def test_사람이_시킨_실행은_그_사람에게_안_가고_다른_관리자에게_간다(
    client: Any,
    registry: Any,
    db: Session,
    admin: Signed,
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    other = _make_user(db, workspace, label="admin2", is_system_admin=True, role="manager")
    _scripted(monkeypatch, slug, [RuntimeError("원천이 안 닿습니다")])

    queued = client.post(f"/api/relay/pipelines/{slug}/run", json={}, headers=admin.headers)
    assert queued.status_code == 202, queued.text
    work_until(db, uuid.UUID(queued.json()["job_id"]))

    assert _bell(db, admin.email, slug) == []  # 화면에서 이미 봤다
    assert _bell(db, other.email, slug) == [("relay.run.failed", f"{slug} 실행 실패")]


class _Down(FakeReader):
    def objects(self, type_slug: str) -> list[dict[str, Any]] | None:
        raise reconcile.ReconcileError("대상의 목록을 못 읽었습니다(502).")


def test_대조가_어긋나면_한_번_보정을_적용해_같아지면_한_번(
    registry: Any, db: Session, admin: Signed, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    _baseline(tmp_path, slug)
    readers: list[FakeReader] = [_Down({}, {})]
    monkeypatch.setattr(reconcile, "TargetReader", lambda _target: readers[0])
    monkeypatch.setattr(reconcile, "Submitter", _Submitter)
    monkeypatch.setattr(runner, "Submitter", _Submitter)

    _timer(db, services.RECONCILE_KIND, slug)  # 대상이 안 닿는다 → 대조 실패
    target = FakeReader({"plm_model": SAME[1:]}, {"plm_model": "plm"})
    readers[0] = target
    _timer(db, services.RECONCILE_KIND, slug)  # 어긋남 1건
    target.items["plm_model"] = SAME[2:]
    _timer(db, services.RECONCILE_KIND, slug)  # 2건 — 건수만 바뀌었다, 새 알림 아님
    _timer(db, services.RECONCILE_KIND, slug, plan=True)
    target.items["plm_model"] = SAME  # 사람이 대상에서 적용했다
    _timer(db, services.APPLY_KIND, slug)  # 적용 → 다시 대조 → 같다

    bell = _bell(db, admin.email, slug)
    assert bell == [
        ("relay.drift", f"{slug} 대조 실패"),
        ("relay.drift", f"{slug} 대상과 어긋남 1건"),
        ("relay.plan.waiting", f"{slug} 적용 대기"),  # 보정 계획이 사람을 기다린다
        ("relay.drift.resolved", f"{slug} 대상과 일치"),
    ], bell


def test_작업이_끝나면_지난_실행을_정리한다(
    registry: Any, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`RELAY_KEEP_RUNS` — 실행마다 `runs/` · `sends/` 가 늘어 안 지우면 디스크가 찬다. 정리는
    작업 끝에서 한다(규칙은 `tests/unit/test_relay_prune.py`)."""
    from app.config import get_settings
    from relay_core.state import Workdir

    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    monkeypatch.setattr(get_settings(), "relay_keep_runs", 1)
    work = Workdir(get_settings().relay_work_dir, slug)
    for i in range(1, 4):
        work.path(f"runs/20261105T{i:012d}Z").mkdir(parents=True)
    _scripted(monkeypatch, slug, [Outcome("nothing", "보낼 것이 없습니다")])

    _timer(db, services.RUN_KIND, slug)

    left = sorted(one.name for one in work.path("runs").iterdir())
    assert left == ["20261105T000000000003Z"]


def test_적용할_계획이_생길_때_한_번_알리고_적용되면_조용하다(
    registry: Any, db: Session, admin: Signed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """자동 적용이 꺼져 있으면(기본) 밤의 실행이 올린 계획은 사람을 기다린다 — 홈의 숫자만
    으로는 누가 열기 전까지 모른다. 오류가 있는 계획은 적용할 수 없으므로 「실행 멈춤」 이
    말한다."""
    from app.config import get_settings
    from relay_core.state import Pending, Workdir

    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    work = Workdir(get_settings().relay_work_dir, slug)

    def planned(ok: bool) -> Outcome:
        state = work.load_state()
        state.pending = Pending(
            run="runs/r", send="sends/s", job_id="j", ok=ok, digest="d",
            summary="생성 2 · 수정 1", at="",
        )  # fmt: skip
        counts = {"objects": {"plm_model": {"created": 2, "changed": 1, "gone": 0}}}
        state.history.append({"event": "planned", "ok": ok, "counts": counts})
        work.save_state(state)
        return Outcome("planned" if ok else "blocked", "계획을 생성했습니다")

    def applied(*_args: Any, **_kwargs: Any) -> Outcome:
        state = work.load_state()
        state.pending = None
        work.save_state(state)
        return Outcome("applied", "적용했습니다")

    runs = [lambda: planned(True), lambda: planned(True), lambda: planned(True)]
    runs += [lambda: planned(False)]
    monkeypatch.setattr(runner, "run", lambda *_a, **_k: runs.pop(0)())
    monkeypatch.setattr(runner, "apply", applied)

    _timer(db, services.RUN_KIND, slug)  # 계획이 생겼다 → 알림
    _timer(db, services.RUN_KIND, slug)  # 다음 실행이 같은 자리에 다시 올렸다 → 조용
    _timer(db, services.APPLY_KIND, slug)  # 사람이 적용했다 → 조용
    _timer(db, services.RUN_KIND, slug)  # 새 계획 → 알림
    _timer(db, services.APPLY_KIND, slug)
    _timer(db, services.RUN_KIND, slug)  # 오류가 있는 계획 → 「실행 멈춤」 만

    bell = _bell(db, admin.email, slug)
    assert bell == [
        ("relay.plan.waiting", f"{slug} 적용 대기"),
        ("relay.plan.waiting", f"{slug} 적용 대기"),
        ("relay.run.failed", f"{slug} 실행 멈춤"),
    ], bell

    from app.modules.notifications.models import Notification

    first = db.scalars(
        select(Notification.body).where(
            Notification.kind == "relay.plan.waiting",
            Notification.link == f"/relay/pipelines/{slug}",
        )
    ).first()
    assert first == "계획 — 생성 2 · 변경 1 · 삭제 0"  # 무엇이 바뀌는지 — 대상 요약이 아니라
