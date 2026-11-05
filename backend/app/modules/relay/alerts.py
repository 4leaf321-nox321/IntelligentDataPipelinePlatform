"""파이프라인이 **나빠질 때 · 다시 될 때** 시스템 관리자에게 알린다.

타이머가 밤에 돌린 실행 · 대조는 시킨 사람이 없어 작업 알림(`jobs.announce`)이 아무에게도 가지
않는다 — 실패해도 누가 홈 화면의 「남은 일」 을 열기 전까지 모른다(2026-11-05).

두 갈래를 따로 본다.

    실행   실행 · 적용 작업 — `failed`(작업 실패) · `blocked`(멈춤) · `ok`
    대조   대조 작업, 보정 계획을 적용한 뒤의 다시 대조 — `same` · `drift` · `problem`

**갈래의 상태가 바뀔 때만** 알린다. 지난번 상태는 작업 폴더의 `alerts.json` 에 둔다(이중화면
공용 폴더). 매일 밤 같은 실패를 알리면 그 종은 곧 안 읽힌다. 어긋난 건수만 바뀐 것(3 → 5)도 새
알림이 아니다. 다시 되면 그것도 알린다 — 안 알리면 사람은 실패 알림 하나를 들고 「아직인가」 를
보러 간다.

**시킨 사람에게는 보내지 않는다** — 그 사람은 화면에서 결과를 봤다(오래 걸렸으면 작업 알림이
따로 간다). 타이머의 작업은 시킨 사람이 없어 시스템 관리자 모두에게 간다.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.jobs.models import Job
from app.modules.notifications import services as notifications
from app.modules.relay import services
from relay_core.state import Workdir

FILE = "alerts.json"
LOCK = "datarelay:relay_alert"
#: 알린 적이 없으면 좋은 상태로 본다 — 첫 실행이 실패하면 그것부터 알린다.
GOOD = {"run": "ok", "reconcile": "same"}
BODY_MAX = 500


def _run_state(job: Job) -> str:
    if job.status == "failed":
        return "failed"
    return "blocked" if (job.result or {}).get("status") == "blocked" else "ok"


def _reconcile_state(job: Job, slug: str) -> str | None:
    """이 작업이 대조를 했으면 그 결과 — 안 했으면 None(그 갈래를 건드리지 않는다).

    결과는 보고서(`reconcile.json`)에서 읽는다 — 대조 작업도, 보정 계획을 적용한 뒤의 다시
    대조도 같은 보고서를 쓴다. 대조 작업이 실패로 끝났으면 보고서는 그대로라 `problem`."""
    if job.kind == services.RECONCILE_KIND:
        if job.status == "failed":
            return "problem"
    elif not (job.status == "done" and (job.result or {}).get("reconciled")):
        return None
    return services.reconcile_status(services.read_report(slug))


def _message(lane: str, state: str, slug: str, job: Job) -> tuple[str, str, str]:
    """(알림 종류, 제목, 본문)."""
    summary = str((job.result or {}).get("summary") or "")
    if lane == "run":
        if state == "failed":
            return notifications.RELAY_RUN_FAILED, f"{slug} 실행 실패", job.error or ""
        if state == "blocked":
            return notifications.RELAY_RUN_FAILED, f"{slug} 실행 멈춤", summary
        return notifications.RELAY_RUN_RECOVERED, f"{slug} 실행 복구", summary
    report = services.read_report(slug) or {}
    said = str(report.get("summary") or summary)
    if state == "drift":
        count = int(report.get("drift") or 0)
        return notifications.RELAY_DRIFT, f"{slug} 대상과 어긋남 {count}건", said
    if state == "problem":
        why = job.error if job.status == "failed" else " / ".join(report.get("problems") or [])
        return notifications.RELAY_DRIFT, f"{slug} 대조 실패", why or said
    return notifications.RELAY_DRIFT_RESOLVED, f"{slug} 대상과 일치", said


def after_job(db: Session, job: Job) -> None:
    """중계 작업이 끝난 뒤(`jobs.Kind.after`) — 갈래의 상태가 바뀌었으면 알린다."""
    slug = str((job.params or {}).get("slug") or "")
    if not slug or job.status not in ("done", "failed"):
        return
    now: dict[str, str] = {}
    if job.kind in (services.RUN_KIND, services.APPLY_KIND):
        now["run"] = _run_state(job)
    reconciled = _reconcile_state(job, slug)
    if reconciled:
        now["reconcile"] = reconciled
    if not now:
        return
    # 이중화의 두 워커가 같은 파이프라인의 두 작업을 함께 끝내도 상태 파일은 하나씩 —
    # 트랜잭션의 자문 잠금(`settle` 의 커밋에서 풀린다).
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{LOCK}:{slug}"})
    work = Workdir(get_settings().relay_work_dir, slug)
    seen = {**GOOD, **(work.read_json(FILE) or {})}
    changed = {lane: state for lane, state in now.items() if seen.get(lane) != state}
    for lane, state in changed.items():
        kind, title, body = _message(lane, state, slug, job)
        notifications.notify_system_admins(
            db,
            kind=kind,
            title=title,
            body=body[:BODY_MAX] or None,
            link=f"/relay/pipelines/{slug}",
            skip=job.requested_by_id,
        )
    if changed:
        work.write_json(FILE, {**seen, **now})
