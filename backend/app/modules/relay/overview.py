"""운영 요약 — 상태별 건수 · 확인 필요 목록 · 대상 상태. MCP `relay_overview` 와 일일 요약
(`digest.py`)이 같이 쓴다.

**지도(`services.build_map`)와 같은 판정을 쓴다** — 요약이 따로 세면 지도와 다른 말을 하는 날이
온다. 지도 전체(노드 · 선)는 파이프라인이 늘면 AI 의 맥락을 넘치게 하고, 사람에게도 정상인
것까지 읽힌다. 요약은 확인이 필요한 것만 싣고 나머지는 건수로만 남긴다(2026-11-24).
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.modules.relay import services
from app.modules.relay.schemas import (
    OverviewItem,
    OverviewTarget,
    RelayMap,
    RelayOverview,
)

#: 사람이 확인해야 하는 상태 — 급한 차례. 정상 · 미실행 · 초안은 해당하지 않는다(화면
#: `board.ts` 와 같다).
ATTENTION = ("failed", "broken", "blocked", "late", "stopped", "pending")


def _reason(status: str, detail: dict[str, Any]) -> str:
    """확인이 필요한 사유 한 줄 — 상태 라벨 외에 무엇을 볼지."""
    from app.modules.relay import alerts

    reasons: list[str] = []
    if status == "failed" and detail.get("last_error"):
        reasons.append(str(detail["last_error"]))
    if detail.get("late"):
        reasons.append(str(detail["late"]))
    reasons.extend(str(one) for one in detail.get("held") or [])
    reasons.extend(str(one) for one in detail.get("problems") or [])
    if status == "pending":
        line = alerts.counts_line(detail.get("counts"))
        reasons.append(f"계획: {line}" if line else "계획 적용 대기")
    if detail.get("reconcile_status") == "problem":
        reasons.append("대조 실패")
    elif int(detail.get("drift") or 0):
        reasons.append(f"대상 불일치 {int(detail['drift'])}건")
    return " · ".join(reasons)


def _urgency(item: OverviewItem) -> tuple[int, str]:
    rank = ATTENTION.index(item.status) if item.status in ATTENTION else len(ATTENTION)
    return rank, item.slug


def build(db: Session, relay_map: RelayMap | None = None) -> RelayOverview:
    """요약 — `relay_map` 을 주면 그것으로(같은 요청에서 지도를 이미 만들었을 때)."""
    found = relay_map or services.build_map(db)
    by_id = {one.id: one for one in found.nodes}
    into: dict[str, list[str]] = {}
    out: dict[str, list[str]] = {}
    for edge in found.edges:
        into.setdefault(edge.target, []).append(edge.source)
        out.setdefault(edge.source, []).append(edge.target)

    def labels(ids: list[str], kind: str) -> list[str]:
        return [by_id[one].label for one in ids if one in by_id and by_id[one].kind == kind]

    pipelines = [one for one in found.nodes if one.kind == "pipeline"]
    counts = Counter(one.status or "never" for one in pipelines)
    attention: list[OverviewItem] = []
    for node in pipelines:
        detail = node.detail or {}
        drift = int(detail.get("drift") or 0) or detail.get("reconcile_status") == "problem"
        if node.status not in ATTENTION and not drift:
            continue
        endpoints = [one for one in into.get(node.id, []) if one in by_id]
        systems = sorted(
            {name for one in endpoints for name in labels(into.get(one, []), "system")}
        )
        attention.append(
            OverviewItem(
                slug=node.label,
                status=node.status,
                status_label=node.status_label,
                system=", ".join(systems),
                target=", ".join(labels(out.get(node.id, []), "target")),
                reason=_reason(node.status, detail),
                link=node.link,
                last_job_at=detail.get("last_job_at"),
            )
        )
    targets = [
        OverviewTarget(
            name=one.label,
            server=one.detail.get("server") or one.sub,
            status=one.status,
            status_label=one.status_label,
            version=one.detail.get("version"),
            checked_at=one.detail.get("checked_at"),
        )
        for one in found.nodes
        if one.kind == "target"
    ]
    return RelayOverview(
        generated_at=found.generated_at,
        total=len(pipelines),
        counts=dict(counts),
        labels={status: services.STATUS_LABEL.get(status, status) for status in counts},
        attention=sorted(attention, key=_urgency),
        targets=targets,
        problems=found.problems,
    )
