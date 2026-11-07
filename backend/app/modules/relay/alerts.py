"""파이프라인이 **나빠질 때 · 다시 될 때** 시스템 관리자에게 알린다.

타이머가 밤에 돌린 실행 · 대조는 시킨 사람이 없어 작업 알림(`jobs.announce`)이 아무에게도 가지
않는다 — 실패해도 누가 홈 화면의 「남은 일」 을 열기 전까지 모른다(2026-11-05).

네 갈래를 따로 본다.

    실행   실행 · 적용 작업 — `failed`(작업 실패) · `blocked`(멈춤) · `ok`
    늦음   일정대로라면 끝났어야 할 실행이 끝나지 않았나 — `late` · `ok`. 작업이 끝날 때 보는
           셋과 달리 **일정 타이머가 5분마다 본다**(`watch_late`) — 작업 워커가 멈추면 끝나는
           작업이 없어 다른 갈래는 아무 말도 못 한다. 타이머는 워커와 다른 프로세스다.
    대조   대조 작업, 보정 계획을 적용한 뒤의 다시 대조 — `same` · `drift` · `problem`
    계획   적용할 수 있는 대기 계획이 있나 — `waiting` · `none`. 자동 적용은 기본이 꺼져 있어
           밤의 실행이 올린 계획은 사람을 기다린다 — 알리지 않으면 대상이 옛 값인 채로 남는다.
           생길 때만 알린다(적용돼 없어지는 것은 사람이 한 일이다). 오류가 있는 계획은
           적용할 수 없으므로 「실행 멈춤」 이 대신 말한다.

**갈래의 상태가 바뀔 때만** 알린다. 지난번 상태는 작업 폴더의 `alerts.json` 에 둔다(이중화면
공용 폴더). 매일 밤 같은 실패를 알리면 그 종은 곧 안 읽힌다. 어긋난 건수만 바뀐 것(3 → 5)도 새
알림이 아니다. 다시 되면 그것도 알린다 — 안 알리면 사람은 실패 알림 하나를 들고 「아직인가」 를
보러 간다.

**시킨 사람에게는 보내지 않는다** — 그 사람은 화면에서 결과를 봤다(오래 걸렸으면 작업 알림이
따로 간다). 타이머의 작업은 시킨 사람이 없어 시스템 관리자 모두에게 간다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

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
GOOD = {"run": "ok", "reconcile": "same", "plan": "none", "late": "ok"}
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


def _tally(group: dict[str, Any] | None) -> tuple[int, int, int]:
    rows = [one for one in (group or {}).values() if isinstance(one, dict)]

    def total(key: str) -> int:
        return sum(int(one.get(key) or 0) for one in rows)

    return total("created"), total("changed"), total("gone")


def counts_line(counts: dict[str, Any] | None) -> str:
    """「생성 3 · 변경 1 · 삭제 0」 — 화면(`countsLine`)과 같은 말. 관계가 움직였으면 뒤에
    붙인다."""
    if not counts:
        return ""
    made, changed, gone = _tally(counts.get("objects"))
    line = f"생성 {made} · 변경 {changed} · 삭제 {gone}"
    r_made, r_changed, r_gone = _tally(counts.get("relations"))
    if r_made + r_changed + r_gone:
        line += f" (관계 생성 {r_made} · 변경 {r_changed} · 삭제 {r_gone})"
    return line


def _plan_state(slug: str) -> str:
    pending = (services.read_state(slug) or {}).get("pending") or {}
    return "waiting" if pending and pending.get("ok") else "none"


def _message(lane: str, state: str, slug: str, job: Job) -> tuple[str, str, str] | None:
    """(알림 종류, 제목, 본문) — 알리지 않는 바뀜이면 None."""
    summary = str((job.result or {}).get("summary") or "")
    if lane == "plan":
        if state != "waiting":
            return None
        saved = services.read_state(slug) or {}
        pending = saved.get("pending") or {}
        what = "보정 계획" if pending.get("origin") == "reconcile" else "계획"
        # 건수는 그 계획을 만든 이력에 있다 — 대상의 미리 보기 요약(「결과: 괜찮음」)은 무엇이
        # 바뀌는지 말하지 않는다.
        made = [one for one in saved.get("history") or [] if one.get("event") == "planned"]
        line = counts_line(made[-1].get("counts")) if made else ""
        said = line or str(pending.get("summary") or summary)
        return notifications.RELAY_PLAN_WAITING, f"{slug} 적용 대기", f"{what} — {said}"
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


def _seen(db: Session, slug: str) -> dict[str, str]:
    """지난번 알린 상태 — 이중화의 두 워커 · 두 타이머가 같은 파이프라인을 함께 봐도 상태
    파일은 하나씩(트랜잭션의 자문 잠금 — 커밋에서 풀린다). 폴더를 미리 만들지 않는다 — 정의가
    지워진 파이프라인의 작업이 빈 폴더를 되살리지 않게."""
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"{LOCK}:{slug}"})
    file = get_settings().relay_work_dir / slug / FILE
    return {**GOOD, **(json.loads(file.read_text(encoding="utf-8")) if file.is_file() else {})}


def watch_late(db: Session, now: datetime | None = None) -> list[str]:
    """일정 타이머(`scripts/relay_due.py`)가 부른다 — 늦음 갈래가 바뀐 파이프라인에 알린다.
    알린 slug 를 돌려준다. **부르는 쪽이 커밋한다.**"""
    told: list[str] = []
    for found in services.catalog(db):
        reason = services.lateness(db, found, now)
        state = "late" if reason else "ok"
        seen = _seen(db, found.slug)
        if seen.get("late") == state:
            continue
        if state == "late":
            kind, title, body = notifications.RELAY_RUN_LATE, f"{found.slug} 실행 늦음", reason
        else:
            kind, title = notifications.RELAY_RUN_RECOVERED, f"{found.slug} 실행 재개"
            body = (
                "늦어지던 실행이 다시 끝났습니다 — 결과는 파이프라인 상세의 실행 이력에서 "
                "확인하세요."
            )
        notifications.notify_system_admins(
            db,
            kind=kind,
            title=title,
            body=body[:BODY_MAX],
            link=f"/relay/pipelines/{found.slug}",
        )
        Workdir(get_settings().relay_work_dir, found.slug).write_json(
            FILE, {**seen, "late": state}
        )
        told.append(found.slug)
    return told


def after_job(db: Session, job: Job) -> None:
    """중계 작업이 끝난 뒤(`jobs.Kind.after`) — 갈래의 상태가 바뀌었으면 알린다."""
    slug = str((job.params or {}).get("slug") or "")
    if not slug or job.status not in ("done", "failed"):
        return
    now: dict[str, str] = {}
    # 적용할 계획이 없어 아무 일도 안 한 적용은 실행 갈래를 바꾸지 않는다 — 실패한 실행 뒤에
    # 누른 「적용」 이 「실행 복구」 로 읽히면 안 된다(된 실행은 없다, 2026-11-06 개발 서버).
    result = job.result or {}
    idle = job.kind == services.APPLY_KIND and result.get("status") == "nothing"
    run_like = job.kind in (services.RUN_KIND, services.APPLY_KIND)
    if run_like and not (job.status == "done" and idle):
        now["run"] = _run_state(job)
    reconciled = _reconcile_state(job, slug)
    if reconciled:
        now["reconcile"] = reconciled
    now["plan"] = _plan_state(slug)
    seen = _seen(db, slug)
    changed = {lane: state for lane, state in now.items() if seen.get(lane) != state}
    for lane, state in changed.items():
        said = _message(lane, state, slug, job)
        if said is None:
            continue
        kind, title, body = said
        notifications.notify_system_admins(
            db,
            kind=kind,
            title=title,
            body=body[:BODY_MAX] or None,
            link=f"/relay/pipelines/{slug}",
            skip=job.requested_by_id,
        )
    if changed:
        Workdir(get_settings().relay_work_dir, slug).write_json(FILE, {**seen, **now})
