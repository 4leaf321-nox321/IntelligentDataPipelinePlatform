"""끊긴 질문을 남긴다 — 답을 못 받고 끊긴 물음과 그 모양(틀의 ADR 0031, `CutQuestion`).

시간 상한(504)에 걸린 요청은 오류 처리기가(`errors.register_error_handlers`) 남긴다. 같은
모양이면 같은 물음이라 화면이 묶어 「몇 번 · 몇 명」 을 보인다(`server/performance.cut_stats`).

틀(StandardPlatform)의 같은 파일에서 **도메인을 모르는 부분만** 옮겼다 — 객체 목록의 모양 ·
지표 정의 초안 · SPARQL 거절은 온톨로지와 함께 걷어냈다(ADR 0001).

**따로 세션으로 넣는다** — 요청의 트랜잭션은 거절과 함께 버려진다. **넣다가 실패해도 응답은
그대로다** — 기록하려다 사람이 받을 안내를 잃으면 안 된다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request

from app.database import SessionLocal
from app.modules.audit.models import CutQuestion
from app.shared.access_log import USER_ID_SCOPE_KEY, route_template
from app.shared.request_context import get_request_id

log = logging.getLogger(__name__)

KIND_LABELS = {
    "timeout": "시간 상한",
    "too_big": "그래프가 큼",
    "records": "빠진 기록 타입",
}


def note(request: Request, kind: str, code: str, shape: dict[str, Any]) -> None:
    """한 줄 남긴다. 루프 위(async 처리기)에서는 `run_in_threadpool` 로 부른다."""
    factory = getattr(request.app.state, "session_factory", SessionLocal)
    try:
        with factory() as db:
            db.add(
                CutQuestion(
                    kind=kind,
                    code=code,
                    method=request.method,
                    route=route_template(request.scope, request.url.path),
                    shape=shape,
                    user_id=request.scope.get(USER_ID_SCOPE_KEY),
                    request_id=get_request_id() or None,
                )
            )
            db.commit()
    except Exception:
        log.warning("끊긴 질문을 남기지 못했습니다: %s %s", kind, code, exc_info=True)
