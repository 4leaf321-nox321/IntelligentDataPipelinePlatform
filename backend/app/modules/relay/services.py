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
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, get_settings
from app.modules.accounts.models import User
from app.modules.jobs.models import Job
from app.modules.relay.schemas import (
    LineageStep,
    MapEdge,
    MapNode,
    PipelineDetail,
    RelayJob,
    RelayMap,
)
from app.shared import extensions
from app.shared.errors import NotFound, code
from relay_core.describe import Described, describe, find

#: 작업 종류 — `relay/jobs.py` 가 등록한다.
RUN_KIND = "relay_run"
APPLY_KIND = "relay_apply"

#: 대상에 묻는 시간 — 지도 한 장을 그리는 데 대상마다 이만큼까지.
PROBE_SECONDS = 3.0

STATUS_LABEL = {
    "ok": "정상",
    "pending": "적용 대기",
    "blocked": "멈춤",
    "failed": "실패",
    "broken": "정의 오류",
    "never": "아직 안 돌았음",
}


def secrets_env() -> Mapping[str, str]:
    """`token_env` 를 풀 자리 — 프로세스 환경 변수 › `RELAY_SECRETS_FILE` › `backend/.env`."""
    merged: dict[str, str] = {}
    settings = get_settings()
    for path in (BACKEND_DIR / ".env", settings.relay_secrets_file):
        if path is not None and Path(path).is_file():
            merged.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    merged.update(os.environ)
    return merged


def catalog(db: Session | None = None) -> list[Described]:
    """정의 전부. `db` 를 주면 **켜진 레시피 판**으로 계보 · 타입을 다시 센다(실제로 도는
    판)."""
    root = get_settings().relay_pipelines_dir
    found = [describe(path) for path in find(root)] if root.is_dir() else []
    if db is not None:
        from app.modules.relay import recipes

        for one in found:
            row = recipes.active(db, one.slug)
            if row is not None:
                recipes.with_recipe(one, row.body, f"판 {row.version}")
    return found


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


def recent_jobs(db: Session, slug: str, limit: int = 10) -> list[Job]:
    rows = db.scalars(
        select(Job)
        .where(Job.kind.in_((RUN_KIND, APPLY_KIND)), Job.params["slug"].as_string() == slug)
        .order_by(Job.created_at.desc())
        .limit(limit)
    ).all()
    return list(rows)


def status_of(found: Described, state: dict[str, Any] | None, last_job: Job | None) -> str:
    if found.problems:
        return "broken"
    history = (state or {}).get("history") or []
    last_event = history[-1] if history else {}
    if last_job is not None and last_job.status == "failed":
        finished = (last_job.finished_at or last_job.created_at).isoformat()
        if not last_event or str(last_event.get("at", "")) < finished:
            return "failed"
    if state is None:
        return "never"
    pending = state.get("pending")
    if pending:
        return "pending" if pending.get("ok") else "blocked"
    if last_event.get("event") == "blocked":
        return "blocked"
    return "ok"


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


def _probe(server: str, token: str, path: str) -> tuple[int, Any]:
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


def build_map(db: Session) -> RelayMap:
    env = secrets_env()
    nodes: dict[str, MapNode] = {}
    edges: list[MapEdge] = []
    problems: list[str] = []
    targets: dict[str, tuple[str, str]] = {}

    for found in catalog(db):
        state = read_state(found.slug)
        jobs = recent_jobs(db, found.slug, limit=1)
        status = status_of(found, state, jobs[0] if jobs else None)
        counts = _last_counts(state)
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
                    f"{found.target_server}/jobs?job={pending['job_id']}" if pending else ""
                ),
                "problems": found.problems,
                "counts": counts,
                "schedule": found.schedule_label,
            },
        )
        edges.append(MapEdge(source=system, target=endpoint))
        edges.append(MapEdge(source=endpoint, target=pipeline, status=status))
        if not found.target_server:
            continue
        target = _node_id("target", found.target_server)
        targets.setdefault(target, (found.target_server, env.get(found.target_token_env, "")))
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

    # 대상이 지금 하는 말 — 판 · slug(엉뚱한 곳에 붙지 않았나) · 그 너머(코어 현황의 수신자).
    for target_id, (server, token) in targets.items():
        node = nodes[target_id]
        status_code, health = _probe(server, "", "/api/health")
        if status_code != 200 or not isinstance(health, dict):
            node.status, node.status_label = "unreachable", "닿지 않음"
            problems.append(f"{node.label}({server})에 닿지 않습니다.")
            continue
        node.status, node.status_label = "ok", "연결됨"
        node.sub = f"{health.get('slug', '')} · {health.get('version', '')}"
        node.detail = {
            "slug": health.get("slug"),
            "version": health.get("version"),
            "app": health.get("app"),
        }
        if not token:
            continue
        status_code, core = _probe(server, token, "/api/ontology/core-status")
        if status_code != 200 or not isinstance(core, dict):
            continue
        for consumer in core.get("consumers") or []:
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
    found: Described, env: Mapping[str, str]
) -> tuple[dict[str, dict[str, Any]], str]:
    """대상 SP 의 정의 — `{타입: {칸: 정의}}`. 못 읽으면 빈 것과 까닭."""
    token = env.get(found.target_token_env, "")
    if not token:
        return (
            {},
            f"대상 토큰({found.target_token_env})을 못 풀어 대상의 정의와 견주지 못했습니다.",
        )
    status_code, schema = _probe(found.target_server, token, "/api/ontology/schema")
    if status_code != 200 or not isinstance(schema, dict):
        return {}, f"대상의 정의를 못 읽었습니다({status_code or '닿지 않음'})."
    out: dict[str, dict[str, Any]] = {}
    for one in schema.get("types") or []:
        out[str(one.get("slug"))] = {str(p.get("key")): p for p in one.get("properties") or []}
    return out, ""


def detail(db: Session, slug: str) -> PipelineDetail:
    found = by_slug(slug, db)
    state = read_state(slug)
    jobs = recent_jobs(db, slug)
    status = status_of(found, state, jobs[0] if jobs else None)
    schema, note = target_schema(found, secrets_env()) if found.target_server else ({}, "")
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
        if key in ("watermark", "applied_run", "applied_at", "pending", "incrementals")
    }
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
    )


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """**적용을 기다리는 계획 · 멈춘 · 실패한 파이프라인**을 홈이 말한다 — 지도를 안 열어도."""
    if not viewer.is_system_admin:
        return []
    counts = {"pending": 0, "blocked": 0, "failed": 0, "broken": 0}
    for found in catalog(db):
        jobs = recent_jobs(db, found.slug, limit=1)
        status = status_of(found, read_state(found.slug), jobs[0] if jobs else None)
        if status in counts:
            counts[status] += 1
    items = [
        extensions.MaintenanceItem(
            key="relay_pending",
            label="적용을 기다리는 중계 계획",
            count=counts["pending"],
            link="/relay/map",
        ),
        extensions.MaintenanceItem(
            key="relay_trouble",
            label="멈추거나 실패한 파이프라인",
            count=counts["blocked"] + counts["failed"] + counts["broken"],
            link="/relay/map",
            severity="warning",
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
    for found in catalog(db):
        if found.problems or not found.schedule:
            continue
        jobs = [one for one in recent_jobs(db, found.slug, limit=5) if one.kind == RUN_KIND]
        if any(one.status in ("queued", "running") for one in jobs):
            continue
        last = jobs[0].created_at.astimezone(moment.tzinfo) if jobs else None
        if due(found.schedule, last, moment):
            job_services.enqueue(
                db,
                kind=RUN_KIND,
                params={"slug": found.slug, "by": "timer"},
                user=None,
                workspace_id=None,
            )
            queued.append(found.slug)
    db.commit()
    return queued
