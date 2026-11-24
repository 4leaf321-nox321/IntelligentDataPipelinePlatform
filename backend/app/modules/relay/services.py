"""연결 지도 · 파이프라인 상세 — **정의에서 매번 계산한다. 손으로 그린 그림을 두지 않는다.**

재료는 셋이다: 정의 파일(`relay_core.describe` — 비밀 없이), 작업 폴더의 상태(`state.json`),
그리고 대상 SP 가 지금 하는 말(`/api/health` · 정의 · 코어 현황). 대상에 못 닿으면 그렇다고
그리고 나머지는 그대로 그린다 — 대상 하나가 꺼졌다고 지도 전체가 안 뜨면 원인을 볼 자리가
없어진다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, get_settings
from app.modules.accounts.models import User
from app.modules.jobs.models import Job
from app.modules.relay import guard
from app.modules.relay import targets as target_status
from app.modules.relay.schemas import (
    LineageStep,
    MapEdge,
    MapNode,
    PipelineDetail,
    RelayJob,
    RelayMap,
    TrendOut,
    TrendRow,
)
from app.shared import extensions
from app.shared.errors import AppError, NotFound, code
from relay_core import reconcile
from relay_core.describe import Described, describe, find
from relay_core.state import TREND, Workdir

#: 작업 종류 — `relay/jobs.py` 가 등록한다.
RUN_KIND = "relay_run"
APPLY_KIND = "relay_apply"
RECONCILE_KIND = "relay_reconcile"
RETRACT_KIND = "relay_retract"
#: 타이머의 자문 잠금 이름 — 이중화에서 한 대만 넣는다(`enqueue_due`).
DUE_LOCK = "datarelay:relay_due"

#: 대상에 묻는 시간 — 지도 한 장을 그리는 데 대상마다 이만큼까지.
PROBE_SECONDS = 3.0

STATUS_LABEL = {
    "ok": "정상",
    "pending": "적용 대기",
    "blocked": "중단",
    "late": "지연",
    "failed": "실패",
    "broken": "정의 오류",
    "never": "미실행",
    "draft": "초안(미사용)",
    "stopped": "회수 후 중지",
}


def server_secrets() -> dict[str, str]:
    """운영자가 서버에 둔 비밀 — `backend/.env` 와 `RELAY_SECRETS_FILE`(뒤가 이긴다)."""
    merged: dict[str, str] = {}
    settings = get_settings()
    for path in (BACKEND_DIR / ".env", settings.relay_secrets_file):
        if path is not None and Path(path).is_file():
            merged.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    return merged


def secrets_env() -> Mapping[str, str]:
    """`token_env` 를 풀 자리 — 프로세스 환경 변수 › **화면에서 넣은 비밀**(`secrets.py`) ›
    `RELAY_SECRETS_FILE` › `backend/.env`."""
    from app.modules.relay import secrets

    merged = server_secrets()
    merged.update(secrets.stored())
    merged.update(os.environ)
    return merged


def catalog(db: Session | None = None) -> list[Described]:
    """정의 전부 — 정의 파일과 **DB 의 정의 판**(`definitions`). 같은 slug 면 켜진 판이 파일을
    이긴다. 켜진 판이 없는 새 slug 는 초안으로 선다(돌지 않는다). `db` 를 주면 **켜진 레시피
    판**으로 계보 · 타입을 다시 센다(실제로 도는 판)."""
    settings = get_settings()
    root = settings.relay_pipelines_dir
    files = [describe(path) for path in find(root)] if root.is_dir() else []
    found = list(files)
    if db is not None:
        from app.modules.relay import definitions, recipes

        by_file = {one.slug: one for one in files}
        for slug, row in definitions.current(db).items():
            file = by_file.get(slug)
            if row.status != "active" and file is not None:
                continue  # 파일로 도는 파이프라인의 초안 — 켜기 전까지 파일이 돈다
            one = definitions.describe_row(row, file)
            found = [other for other in found if other.slug != slug] + [one]
        for one in found:
            recipe = recipes.active(db, one.slug)
            if recipe is not None:
                recipes.with_recipe(one, recipe.body, f"버전 {recipe.version}")
            elif one.recipe is None and one.origin != "파일" and not one.draft:
                one.problems.append("레시피가 없습니다. 레시피 버전을 작성하고 사용하세요.")
    roots = settings.relay_file_root_paths
    for one in found:
        if one.adapter_kind == "file" and not one.endpoint.startswith(guard.REMOTE):
            head = Path(re.split(r"[*?\[]", one.endpoint, maxsplit=1)[0] or ".").resolve()
            if not any(head == r or head.is_relative_to(r) for r in roots):
                one.problems.append(
                    f"파일 경로가 허용 폴더(RELAY_FILE_ROOTS) 밖에 있습니다: {one.endpoint}"
                )
    return sorted(found, key=lambda one: one.slug)


def by_slug(slug: str, db: Session | None = None) -> Described:
    for one in catalog(db):
        if one.slug == slug:
            return one
    raise NotFound(code("RELAY", 1), f"파이프라인이 없습니다: {slug}")


def read_state(slug: str) -> dict[str, Any] | None:
    """작업 폴더의 상태 — 읽기만 한다(폴더를 만들지 않는다)."""
    file = get_settings().relay_work_dir / slug / "state.json"
    if not file.is_file():
        return None
    body: dict[str, Any] = json.loads(file.read_text(encoding="utf-8"))
    return body


#: 화면을 열 때 대상에 「적용됐나」 를 묻는 사이 — 같은 파이프라인은 이만큼(초)에 한 번.
SETTLE_EVERY = 30.0
_asked: dict[str, float] = {}
_down: dict[str, float] = {}


class _Applied:
    """`runner.settle_pending` 에 넘길 답 — 이미 대상에 물어 적용된 것을 안다."""

    def applied(self, job_id: str) -> bool:
        return True


def settle_seen(db: Session, found: Described, env: Mapping[str, str] | None = None) -> str:
    """**SP 화면에서 적용한 계획을 화면을 열 때 알아챈다** — 지도 · 상세 · 추적이 부른다.

    대기 계획이 있으면 대상의 작업(`/api/jobs/<id>`)에 적용 기록(`applied_by`)이 있나 묻고,
    있으면 실행 · 대조가 하는 것과 같이 기준으로 올린다(`runner.settle_pending`). 그렇지 않으면
    다음 실행 · 대조까지 지도가 「적용 대기」 · 추적이 「아직 적용한 것이 없습니다」 로 남았다
    (2026-11-18). 묻는 것은 짧게(`PROBE_SECONDS`) · 같은 파이프라인은 `SETTLE_EVERY` 초에
    한 번 · 닿지 않은 대상은 그만큼 건너뛴다. 실행 · 적용 · 대조가 대기 중이거나 도는 중이면
    그쪽이 스스로 본다 — 상태 파일을 둘이 쓰지 않게."""
    import time

    from relay_core import runner

    state = read_state(found.slug) or {}
    pending = state.get("pending") or {}
    job_id = str(pending.get("job_id") or "")
    if found.draft or not job_id or not found.target_server:
        return ""
    now = time.monotonic()
    if now - _asked.get(found.slug, -SETTLE_EVERY) < SETTLE_EVERY:
        return ""
    if now - _down.get(found.target_server, -SETTLE_EVERY) < SETTLE_EVERY:
        return ""
    _asked[found.slug] = now
    if any(
        one.status in ("queued", "running") for one in recent_jobs(db, found.slug, limit=3)
    ):
        return ""
    token = (env or secrets_env()).get(found.target_token_env, "")
    if not token:
        return ""
    status, answer = probe_http(found.target_server, token, f"/api/jobs/{job_id}")
    if status == 0:
        _down[found.target_server] = now
        return ""
    if status != 200 or not isinstance(answer, dict) or not answer.get("applied_by"):
        return ""
    work = Workdir(get_settings().relay_work_dir, found.slug)
    fresh = work.load_state()
    if fresh.pending is None or fresh.pending.job_id != job_id:
        return ""  # 그 사이 실행이 바꿨다
    note = runner.settle_pending(fresh, _Applied())  # type: ignore[arg-type]
    if note:
        fresh.history.append({"at": datetime.now(UTC).isoformat(), "event": "seen_applied"})
        work.save_state(fresh)
    return note


def target_version(server: str) -> str:
    """대상 SP 의 판(`/api/health` 의 `version` — `v0.4.101`). 못 닿으면 빈 글."""
    if not server:
        return ""
    status_code, health = probe_http(server, "", "/api/health")
    if status_code != 200 or not isinstance(health, dict):
        return ""
    return str(health.get("version") or "")


def retracted_count(slug: str) -> int:
    """거둔 적이 있는 식별자의 수(`relay_core.retract.LEDGER`) — 화면이 「앞서 걷어낸 것」 을
    낼지 정한다. 읽기만 한다."""
    from relay_core import retract

    file = get_settings().relay_work_dir / slug / retract.LEDGER
    if not file.is_file():
        return 0
    body = json.loads(file.read_text(encoding="utf-8"))
    return sum(len(keys) for keys in body.values() if isinstance(keys, list))


def read_report(slug: str) -> dict[str, Any] | None:
    """마지막 대조의 보고서(`reconcile.json`) — 없으면 None. 읽기만 한다."""
    file = get_settings().relay_work_dir / slug / reconcile.REPORT
    if not file.is_file():
        return None
    body: dict[str, Any] = json.loads(file.read_text(encoding="utf-8"))
    return body


def trend(slug: str, limit: int) -> TrendOut:
    """실행 추이(`trend.json`) — 끝의 `limit` 줄. 읽기만 한다(폴더를 만들지 않는다)."""
    file = get_settings().relay_work_dir / slug / TREND
    raw = json.loads(file.read_text(encoding="utf-8")) if file.is_file() else []
    rows = [one for one in raw if isinstance(one, dict)] if isinstance(raw, list) else []
    return TrendOut(
        rows=[TrendRow.model_validate(one) for one in rows[-limit:]], total=len(rows)
    )


def reconcile_status(report: dict[str, Any] | None) -> str | None:
    """마지막 대조 — `same`(대상과 같음) · `drift`(어긋남) · `problem`(대조 못 함), 안 했으면
    None."""
    if not report:
        return None
    if report.get("problems"):
        return "problem"
    return "drift" if int(report.get("drift") or 0) else "same"


def recent_jobs(
    db: Session,
    slug: str,
    limit: int = 10,
    kinds: tuple[str, ...] = (RUN_KIND, APPLY_KIND, RECONCILE_KIND, RETRACT_KIND),
) -> list[Job]:
    rows = db.scalars(
        select(Job)
        .where(Job.kind.in_(kinds), Job.params["slug"].as_string() == slug)
        .order_by(Job.created_at.desc())
        .limit(limit)
    ).all()
    return list(rows)


def status_of(
    found: Described, state: dict[str, Any] | None, last_job: Job | None, late: str = ""
) -> str:
    """`late` 는 `lateness` 의 까닭 — 있으면 정상 · 적용 대기 · 실행 전 대신 「늦음」 이다(실패
    · 멈춤은 그대로 — 그쪽이 할 일을 말한다)."""
    if found.draft:
        return "draft"
    if found.problems:
        return "broken"
    # 대조는 실행 상태가 아니다 — 멈춘 실행 뒤에 대조가 돌아도 「멈춤」 은 그대로다.
    history = [
        one for one in (state or {}).get("history") or [] if one.get("event") != "reconciled"
    ]
    last_event = history[-1] if history else {}
    if last_job is not None and last_job.status == "failed":
        finished = (last_job.finished_at or last_job.created_at).isoformat()
        if not last_event or str(last_event.get("at", "")) < finished:
            return "failed"
    if state is None:
        return "late" if late else "never"
    # 보낸 것을 걷어내고 멈췄다(`relay_core.retract`) — 걷어내기 계획이 대기 중이면
    # 「적용 대기」.
    if state.get("stopped") and not state.get("pending"):
        return "stopped"
    # 받기 점검이 멈췄다 — 대기 계획이 있어도(앞 실행의 것) 새 원천은 사람을 기다린다.
    if state.get("held"):
        return "blocked"
    pending = state.get("pending")
    if pending and not pending.get("ok"):
        return "blocked"
    if not pending and last_event.get("event") == "blocked":
        return "blocked"
    if late:
        return "late"
    return "pending" if pending else "ok"


def lateness(db: Session, found: Described, now: datetime | None = None) -> str:
    """일정대로라면 끝났어야 할 실행이 끝나지 않았나 — 까닭 한 줄, 아니면 빈 글
    (`relay_core.schedule.late`, `RELAY_LATE_MINUTES`). 일정이 없거나 초안 · 정의 오류면 빈
    글이다(돌 차례가 없다)."""
    from relay_core.schedule import late

    minutes = get_settings().relay_late_minutes
    if found.draft or found.problems or not found.schedule or not minutes:
        return ""
    if (read_state(found.slug) or {}).get("stopped"):
        return ""  # 걷어내고 멈췄다 — 돌 차례가 없다
    moment = now or datetime.now().astimezone()
    jobs = recent_jobs(db, found.slug, limit=20, kinds=(RUN_KIND,))
    finished = next(
        (one for one in jobs if one.status in ("done", "failed", "cancelled")), None
    )
    waiting = [one.created_at for one in jobs if one.status in ("queued", "running")]
    return late(
        found.schedule,
        last=finished.created_at.astimezone(moment.tzinfo) if finished else None,
        waiting=min(waiting).astimezone(moment.tzinfo) if waiting else None,
        now=moment,
        grace=timedelta(minutes=minutes),
    )


def _last_counts(state: dict[str, Any] | None) -> dict[str, Any]:
    for one in reversed((state or {}).get("history") or []):
        if one.get("counts"):
            return dict(one["counts"])
    return {}


def _sends(counts: dict[str, Any], type_slug: str | None = None) -> int:
    total = 0
    for group in counts.values():
        for slug, one in group.items():
            if type_slug is None or slug == type_slug:
                total += int(one.get("created", 0)) + int(one.get("changed", 0))
                total += int(one.get("gone", 0))
    return total


def probe_http(server: str, token: str, path: str) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        answer = httpx.get(f"{server}{path}", headers=headers, timeout=PROBE_SECONDS)
    except httpx.HTTPError:
        return 0, None
    try:
        return answer.status_code, answer.json()
    except ValueError:
        return answer.status_code, None


def _node_id(kind: str, *parts: str) -> str:
    return f"{kind}:" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def build_map(db: Session, *, probe: bool = False) -> RelayMap:
    """연결 지도 — `probe` 면 대상 상태를 저장값 대신 지금 조회한다([대상 다시 조회])."""
    env = secrets_env()
    nodes: dict[str, MapNode] = {}
    edges: list[MapEdge] = []
    problems: list[str] = []
    targets: dict[str, tuple[str, str]] = {}

    for found in catalog(db):
        settle_seen(db, found, env)
        state = read_state(found.slug)
        jobs = recent_jobs(db, found.slug, limit=1, kinds=(RUN_KIND, APPLY_KIND))
        late = lateness(db, found)
        status = status_of(found, state, jobs[0] if jobs else None, late)
        counts = _last_counts(state)
        report = read_report(found.slug) or {}
        system = _node_id("system", found.system or found.source)
        nodes.setdefault(
            system, MapNode(id=system, kind="system", label=found.system, sub=found.source)
        )
        endpoint = _node_id("endpoint", found.endpoint)
        nodes.setdefault(
            endpoint,
            MapNode(
                id=endpoint, kind="endpoint", label=found.endpoint_label, sub=found.endpoint
            ),
        )
        pending = (state or {}).get("pending") or {}
        last = jobs[0] if jobs else None
        pipeline = f"pipeline:{found.slug}"
        nodes[pipeline] = MapNode(
            id=pipeline,
            kind="pipeline",
            label=found.slug,
            sub=f"source {found.source} · {found.adapter_kind} · {found.schedule_label}",
            status=status,
            status_label=STATUS_LABEL[status],
            link=f"/relay/pipelines/{found.slug}",
            detail={
                "applied_at": (state or {}).get("applied_at"),
                "watermark": (state or {}).get("watermark"),
                "pending_job": pending.get("job_id"),
                "pending_link": (
                    f"{found.target_server}/jobs?job={pending['job_id']}"
                    if pending.get("job_id")
                    else ""
                ),
                "problems": found.problems,
                "counts": counts,
                "schedule": found.schedule_label,
                "drift": int(report.get("drift") or 0),
                "reconcile_status": reconcile_status(report),
                "reconciled_at": report.get("at"),
                "late": late,
                "held": list(((state or {}).get("held") or {}).get("reasons") or []),
                # 마지막 실행 · 적용 작업 — 목록의 '마지막 실행' 열과 요약의 실패 사유.
                "last_job_at": (
                    (last.finished_at or last.created_at).isoformat() if last else None
                ),
                "last_error": (
                    (last.error or "")[:200] if last and last.status == "failed" else ""
                ),
            },
        )
        edges.append(MapEdge(source=system, target=endpoint))
        edges.append(MapEdge(source=endpoint, target=pipeline, status=status))
        if not found.target_server:
            continue
        target = _node_id("target", found.target_server)
        if not found.draft:  # 초안의 대상 · 토큰은 묻지 않는다 — 사람이 아직 안 본 주소다
            token = env.get(found.target_token_env, "")
            targets.setdefault(target, (found.target_server, token))
        nodes.setdefault(
            target,
            MapNode(
                id=target,
                kind="target",
                label=found.target_name or found.target_server,
                sub=found.target_server,
            ),
        )
        edges.append(
            MapEdge(source=pipeline, target=target, weight=_sends(counts), status=status)
        )
        for type_slug in found.types:
            type_node = _node_id("type", found.target_server, type_slug)
            nodes.setdefault(
                type_node,
                MapNode(id=type_node, kind="type", label=type_slug, sub=found.target_name),
            )
            edges.append(
                MapEdge(source=target, target=type_node, weight=_sends(counts, type_slug))
            )

    for one in nodes.values():
        if one.kind == "target" and one.id not in targets:
            one.status, one.status_label = "draft", "미확인(초안)"
    # 대상이 지금 하는 말 — 판 · slug(엉뚱한 곳에 붙지 않았나) · 그 너머(코어 현황의 수신자).
    # 대상의 상태는 타이머가 조회해 둔 값을 읽는다(`targets.py`) — 없거나 오래된 대상만 지금
    # 조회한다.
    checked = target_status.current(dict(targets.values()), probe_http, force=probe)
    for target_id, (server, _token) in targets.items():
        node = nodes[target_id]
        entry = checked.get(server) or {}
        node.detail = {"server": server, "checked_at": entry.get("checked_at")}
        if not entry.get("reachable"):
            node.status, node.status_label = "unreachable", "연결 불가"
            problems.append(f"{node.label}({server})에 연결할 수 없습니다.")
            continue
        node.status, node.status_label = "ok", "연결됨"
        node.sub = f"{entry.get('slug') or ''} · {entry.get('version') or ''}"
        node.detail.update(
            slug=entry.get("slug"), version=entry.get("version"), app=entry.get("app")
        )
        for consumer in entry.get("consumers") or []:
            name = str(consumer.get("name") or "")
            if not name or name.startswith("datarelay"):
                continue  # 자기 자신(relay 의 토큰)은 「그 너머」 가 아니다
            downstream = _node_id("downstream", server, name)
            nodes.setdefault(
                downstream,
                MapNode(
                    id=downstream,
                    kind="downstream",
                    label=name,
                    sub=f"마지막 수신 {str(consumer.get('last_used_at') or '-')[:16]}",
                    detail={"owner": consumer.get("owner")},
                ),
            )
            edges.append(MapEdge(source=target_id, target=downstream))

    return RelayMap(
        generated_at=datetime.now(UTC).isoformat(),
        nodes=list(nodes.values()),
        edges=edges,
        problems=problems,
    )


def target_schema(
    found: Described, env: Mapping[str, str], db: Session | None = None
) -> tuple[dict[str, dict[str, Any]], str]:
    """대상 SP 의 정의 — `{타입: {칸: 정의}}`. 못 읽으면 빈 것과 까닭."""
    model, note = target_model(found, env, db)
    return {slug: one["properties"] for slug, one in model["types"].items()}, note


def target_model(
    found: Described, env: Mapping[str, str], db: Session | None = None
) -> tuple[dict[str, Any], str]:
    """대상 SP 의 정의 전부 — `{"types": {slug: {"label", "managed_by", "nav_group_slug",
    "properties"}}, "relations": [관계 종류], "groups": [사이드바 묶음]}`. 못 읽으면 빈 것과
    까닭. 초안이면 울타리(`guard.check_target`)를 먼저 지난다 — 사람이 아직 안 본 주소 ·
    토큰이다."""
    empty: dict[str, Any] = {"types": {}, "relations": [], "groups": []}
    if found.draft:
        try:
            guard.check_target(
                {"server": found.target_server, "token_env": found.target_token_env},
                guard.fence(catalog(db)),
            )
        except AppError as failure:
            return empty, f"초안의 대상은 조회하지 않았습니다. {failure.message}"
    token = env.get(found.target_token_env, "")
    if not token:
        return (
            empty,
            f"대상 토큰({found.target_token_env})을 읽을 수 없어 대상의 정의와 비교하지 "
            "않았습니다.",
        )
    status_code, schema = probe_http(found.target_server, token, "/api/ontology/schema")
    if status_code != 200 or not isinstance(schema, dict):
        return empty, f"대상의 정의를 조회할 수 없습니다({status_code or '연결 불가'})."
    types: dict[str, Any] = {}
    for one in schema.get("types") or []:
        types[str(one.get("slug"))] = {
            "label": str(one.get("label") or ""),
            "managed_by": str(one.get("managed_by") or ""),
            "nav_group_slug": str(one.get("nav_group_slug") or ""),
            "properties": {str(p.get("key")): p for p in one.get("properties") or []},
        }
    relations = [one for one in schema.get("relation_types") or [] if isinstance(one, dict)]
    # 사이드바 묶음 — 새 타입을 어느 묶음에 둘지 고르는 재료(`typedraft.suggest_group`).
    groups = [
        {
            k: one.get(k)
            for k in ("slug", "label", "icon", "audience", "parent_slug", "sort_order")
            if k in one
        }
        for one in schema.get("groups") or []
        if isinstance(one, dict) and one.get("slug") and one.get("is_active", True)
    ]
    return {"types": types, "relations": relations, "groups": groups}, ""


def detail(db: Session, slug: str) -> PipelineDetail:
    found = by_slug(slug, db)
    settle_seen(db, found)
    state = read_state(slug)
    jobs = recent_jobs(db, slug)
    runs = [one for one in jobs if one.kind in (RUN_KIND, APPLY_KIND)]
    late = lateness(db, found)
    status = status_of(found, state, runs[0] if runs else None, late)
    schema, note = target_schema(found, secrets_env(), db) if found.target_server else ({}, "")
    steps: list[LineageStep] = []
    for one in found.lineage:
        props = schema.get(one.target_type)
        prop = (props or {}).get(one.target_field)
        fixed = one.target_field in ("key", "label") or one.target_field.startswith("→")
        steps.append(
            LineageStep(
                target_type=one.target_type,
                target_field=one.target_field,
                target_label=str((prop or {}).get("label") or ""),
                target_data_type=str((prop or {}).get("data_type") or ""),
                target_missing=bool(schema) and not fixed and prop is None,
                columns=one.columns,
                source_paths=one.source_paths,
                transform=one.transform,
            )
        )
    safe_state = {
        key: value
        for key, value in (state or {}).items()
        if key
        in (
            "watermark",
            "applied_run",
            "applied_at",
            "pending",
            "incrementals",
            "held",
            "stopped",
        )
    }
    report = read_report(slug)
    if report is not None:
        report = reconcile.Report.from_json(report).to_json(sample=reconcile.SAMPLE)
    version = target_version(found.target_server) if not found.draft else ""
    from relay_core import retract

    return PipelineDetail(
        slug=found.slug,
        system=found.system,
        source=found.source,
        adapter_kind=found.adapter_kind,
        endpoint=found.endpoint,
        endpoint_label=found.endpoint_label,
        target_name=found.target_name,
        target_server=found.target_server,
        target_token_env=found.target_token_env,
        definition_path=str(found.path),
        recipe_path=str(found.recipe or ""),
        types=found.types,
        relations=found.relations,
        status=status,
        status_label=STATUS_LABEL[status],
        problems=found.problems,
        lineage=steps,
        state=safe_state,
        history=list(reversed((state or {}).get("history") or []))[:50],
        jobs=[
            RelayJob(
                id=str(job.id),
                kind=job.kind,
                status=job.status,
                created_at=job.created_at.isoformat(),
                finished_at=job.finished_at.isoformat() if job.finished_at else None,
                summary=str((job.result or {}).get("summary") or ""),
                error=job.error,
            )
            for job in jobs
        ],
        target_schema_note=note,
        recipe_label=found.recipe_label,
        schedule_label=found.schedule_label,
        definition_label=found.origin,
        auto_apply_label=found.auto_apply_label,
        intake_label=found.intake_label,
        late=late,
        draft=found.draft,
        reconcile=report,
        reconcile_label=reconcile_label(),
        target_version=version,
        target_deletes=retract.supports_delete(version),
        retracted=retracted_count(slug),
    )


def trace_value(db: Session, slug: str, type_slug: str, key: str) -> dict[str, Any]:
    """거꾸로 보기 — `relay_core.trace`. 기준 실행의 레시피 사본이 없을 때만 지금 레시피(사용
    중인 판, 없으면 정의 파일의 것)로 읽는다. **읽기만 한다** — 작업 폴더를 새로 만들지
    않는다."""
    from app.modules.relay import recipes
    from relay_core.trace import TraceError, trace

    found = by_slug(slug, db)
    if not (get_settings().relay_work_dir / slug).is_dir():
        raise NotFound(
            code("RELAY", 19),
            f"{slug}은 아직 원천 데이터를 수신하지 않았습니다. 먼저 실행하세요.",
        )
    settle_seen(db, found)
    active = recipes.active(db, slug)
    now: dict[str, Any] | Path | None = active.body if active is not None else found.recipe
    try:
        got = trace(Workdir(get_settings().relay_work_dir, slug), now, type_slug, key)
    except TraceError as failure:
        raise NotFound(code("RELAY", 19), str(failure)) from failure
    return got.to_json()


def reconcile_label() -> str:
    days = get_settings().relay_reconcile_days
    at = get_settings().relay_reconcile_at
    return f"{days}일마다 {at}" if days else "수동"


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """**적용을 기다리는 계획 · 멈춘 · 실패한 파이프라인**을 홈이 말한다 — 지도를 안 열어도."""
    if not viewer.is_system_admin:
        return []
    counts = {
        "pending": 0,
        "blocked": 0,
        "late": 0,
        "failed": 0,
        "broken": 0,
        "drift": 0,
        "stopped": 0,
    }
    env = secrets_env()
    for found in catalog(db):
        settle_seen(db, found, env)
        jobs = recent_jobs(db, found.slug, limit=1, kinds=(RUN_KIND, APPLY_KIND))
        late = lateness(db, found)
        status = status_of(found, read_state(found.slug), jobs[0] if jobs else None, late)
        if status in counts:
            counts[status] += 1
        if not found.draft and reconcile_status(read_report(found.slug)) in (
            "drift",
            "problem",
        ):
            counts["drift"] += 1
    items = [
        extensions.MaintenanceItem(
            key="relay_pending",
            label="적용 대기 중계 계획",
            count=counts["pending"],
            link="/relay/map",
        ),
        extensions.MaintenanceItem(
            key="relay_trouble",
            label="중단 · 실패 · 지연 파이프라인",
            count=counts["blocked"] + counts["late"] + counts["failed"] + counts["broken"],
            link="/relay/map",
            severity="warning",
        ),
        extensions.MaintenanceItem(
            key="relay_drift",
            label="대상 불일치 · 대조 실패 파이프라인",
            count=counts["drift"],
            link="/relay/map",
            severity="warning",
        ),
        # 걷어내고 멈춘 것은 사고가 아니다 — 다시 켜는 것을 잊지 않게만.
        extensions.MaintenanceItem(
            key="relay_stopped",
            label="회수 후 중지 파이프라인",
            count=counts["stopped"],
            link="/relay/map",
        ),
    ]
    return [one for one in items if one.count]


def enqueue_due(db: Session, now: datetime | None = None) -> list[str]:
    """**차례가 된 파이프라인을 작업으로 넣는다** — 타이머(`scripts/relay_due.py`, 5분마다)가
    부른다.

    이미 대기 · 도는 실행이 있으면 넣지 않는다(새벽에 원천이 느려 한 바퀴가 5분을 넘어도 겹치지
    않게).
    시킨 사람 없이 넣는다 — 감사 기록의 actor 가 「타이머」 가 된다."""
    from app.modules.jobs import services as job_services
    from relay_core.schedule import due

    moment = now or datetime.now().astimezone()
    queued: list[str] = []
    # **이중화의 두 타이머가 같은 순간에 돌아도 한 대만 넣는다** — 트랜잭션의 자문
    # 잠금(커밋에서 풀린다). 「이미 대기 중」 검사만으로는 두 서버가 동시에 「없다」 를
    # 보고 둘 다 넣는다.
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"), {"k": DUE_LOCK}):
        return queued
    for found in catalog(db):
        if found.draft or found.problems:
            continue
        if found.schedule and not (read_state(found.slug) or {}).get("stopped"):
            jobs = recent_jobs(db, found.slug, limit=5, kinds=(RUN_KIND,))
            last = jobs[0].created_at.astimezone(moment.tzinfo) if jobs else None
            busy = any(one.status in ("queued", "running") for one in jobs)
            if not busy and due(found.schedule, last, moment):
                job_services.enqueue(
                    db,
                    kind=RUN_KIND,
                    params={"slug": found.slug, "by": "timer"},
                    user=None,
                    workspace_id=None,
                )
                queued.append(found.slug)
        if reconcile_due(db, found.slug, moment):
            job_services.enqueue(
                db,
                kind=RECONCILE_KIND,
                params={"slug": found.slug, "by": "timer"},
                user=None,
                workspace_id=None,
            )
            queued.append(f"{found.slug}(대조)")
    db.commit()
    return queued


def reconcile_due(db: Session, slug: str, moment: datetime) -> bool:
    """대조할 차례인가 — 적용한 것이 있고, `RELAY_RECONCILE_DAYS` 일에 한 번, 그날
    `RELAY_RECONCILE_AT` 뒤 첫 차례에. 마지막 대조(작업)가 그 안에 있으면 아니다."""
    settings = get_settings()
    applied_at = (read_state(slug) or {}).get("applied_at")
    if not settings.relay_reconcile_days or not applied_at:
        return False
    jobs = recent_jobs(db, slug, limit=1, kinds=(RECONCILE_KIND,))
    if jobs and jobs[0].status in ("queued", "running"):
        return False
    hour, minute = (int(part) for part in settings.relay_reconcile_at.split(":"))
    slot = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > moment:
        slot -= timedelta(days=1)
    if not jobs:
        # 처음 — 첫 적용 뒤 처음 맞는 그 시각에(적용하고 5분 만에 견주지 않게).
        first = datetime.fromisoformat(str(applied_at)).astimezone(moment.tzinfo)
        return first < slot
    last = jobs[0].created_at.astimezone(moment.tzinfo)
    return last < slot - timedelta(days=settings.relay_reconcile_days - 1)
