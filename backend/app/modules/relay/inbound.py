"""보내 주는 원천(push)의 받는 자리 — `POST /api/relay/inbound/{slug}`.

**로그인 없이** 원천 시스템이 부른다 — 그래서 차례가 중요하다:

1. 크기 상한(`MAX_BYTES`) — 읽기 전에 `Content-Length` 로, 읽은 뒤 실제 길이로.
2. 받을 파이프라인인가 — 사용 중인 정의이고 어댑터가 `push` 여야 한다. 아니면 **없는 것과
   같은 404**(무엇이 있는지 알려 주지 않는다).
3. 비밀 — 머리글 `X-Relay-Token` 을 그 정의의 `auth.token_env` 값과 상수 시간으로 견준다.
   `Authorization` 을 안 쓰는 까닭: 그 머리글은 사람 · 토큰(PAT)의 자리다.
4. 모양 · 건수 — JSON 목록(또는 `rows_path` 자리의 목록), 또는 **Parquet 파일 하나**(앞뒤의
   `PAR1` 로 알아본다 — 값은 JSON 꼴로 바꿔 쌓는다, `adapters.parquet`), `max_items` 이하.
5. 받을 함이 넘치지 않았나(`MAX_WAITING`) — 실행이 멈춰 쌓이기만 하면 디스크가 찬다.
6. 임시 파일에 다 쓰고 바꿔 끼운다 — 반쪽 묶음을 실행이 읽지 않게.

받은 묶음을 미러에 넣는 것은 실행이다(`relay_core.adapters.push`) — 받는 자리는 쌓기만 한다.

**시험 수신**(`?trial=1`) — 사용하기 전에 원천이 진짜로 한 번 보내 보는 길. 가장 늦은 **초안**
판의 정의(없으면 사용 중인 판)로 같은 차례를 지나 **시험 수신함**(`trial-inbox/`, 최근
`TRIAL_KEEP` 개)에 둔다. 실행은 시험 수신함을 읽지 않는다 — 정의 시험 · 탐색 · 한 번에 초안만
(받을 함이 비었을 때).
"""

from __future__ import annotations

import hmac
import json
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.relay import services
from app.modules.relay.models import RelayDefinition
from app.shared.errors import AppError, NotFound, code
from relay_core.adapters import parquet
from relay_core.adapters.push import SUFFIX, items_of
from relay_core.adapters.rest import SourceError
from relay_core.state import stamp

MAX_BYTES = 20 * 1024 * 1024
MAX_WAITING = 1000
#: 시험 수신함에 남길 묶음 — 넘으면 오래된 것부터 지운다(시험은 최근 것만 본다).
TRIAL_KEEP = 20
HEADER = "X-Relay-Token"


def _nowhere(slug: str) -> NotFound:
    return NotFound(code("RELAY", 31), f"수신할 파이프라인이 없습니다: {slug}")


def check_size(length: str | None) -> None:
    if length and length.isdigit() and int(length) > MAX_BYTES:
        raise AppError(
            code("RELAY", 32),
            f"한 번에 최대 {MAX_BYTES // (1024 * 1024)}MB까지 수신합니다.",
            status=413,
        )


def _draft_body(db: Session, slug: str) -> dict[str, Any] | None:
    """가장 늦은 초안 판의 정의 — 시험 수신이 그 비밀 · 모양으로 받는다."""
    row = db.scalars(
        select(RelayDefinition)
        .where(RelayDefinition.pipeline_slug == slug, RelayDefinition.status == "draft")
        .order_by(RelayDefinition.version.desc())
        .limit(1)
    ).first()
    return dict(row.body) if row is not None else None


def receive(
    db: Session, slug: str, token: str, raw: bytes, *, trial: bool = False
) -> dict[str, Any]:
    check_size(str(len(raw)))
    body = _draft_body(db, slug) if trial else None
    if body is None:
        try:
            found = services.by_slug(slug, db)
        except NotFound:
            raise _nowhere(slug) from None
        if not trial and (found.draft or found.problems or found.adapter_kind != "push"):
            raise _nowhere(slug)
        body = found.body
    adapter = dict(body.get("adapter") or {})
    if str(adapter.get("kind") or "") != "push":
        raise _nowhere(slug)
    auth = dict(adapter.get("auth") or {})
    secret = services.secrets_env().get(str(auth.get("token_env") or ""), "")
    # 비밀이 비었으면 아무도 못 보낸다 — 빈 머리글과 같다고 받지 않는다.
    if not secret or not token or not hmac.compare_digest(token.encode(), secret.encode()):
        raise AppError(code("RELAY", 33), f"{HEADER}가 올바르지 않습니다.", status=401)
    most = int(adapter.get("max_items") or 50_000)
    try:
        if parquet.looks_like(raw):
            # 하나 더 읽어 넘친 것을 안다 — 다 펴지 않는다.
            items: list[Any] = list(parquet.rows(raw, limit=most + 1))
        else:
            items = items_of(json.loads(raw), str(adapter.get("rows_path") or ""))
    except (ValueError, SourceError) as failure:
        raise AppError(
            code("RELAY", 34), f"요청 본문을 읽을 수 없습니다: {failure}", status=422
        ) from None
    if len(items) > most:
        raise AppError(
            code("RELAY", 32),
            f"한 번에 최대 {most:,}건까지 수신합니다(요청 {len(items):,}건"
            + (" 이상" if parquet.looks_like(raw) else "")
            + ").",
            status=413,
        )
    if trial:
        return _keep_trial(slug, items)
    inbox = get_settings().relay_work_dir / slug / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    waiting = sum(1 for one in inbox.iterdir() if one.name.endswith(SUFFIX))
    if waiting >= MAX_WAITING:
        raise AppError(
            code("RELAY", 35),
            f"수신함에 처리되지 않은 수신 데이터가 {waiting}건 쌓였습니다. 실행이 수신 "
            "데이터를 처리하지 못하고 있습니다. 실행 중단, 지연 여부를 확인하세요.",
            status=429,
        )
    name = f"{stamp()}{SUFFIX}"
    scratch = inbox / f".{name}.tmp"
    scratch.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    os.replace(scratch, inbox / name)
    return {"accepted": len(items), "queued": name, "waiting": waiting + 1}


def _keep_trial(slug: str, items: list[Any]) -> dict[str, Any]:
    """시험 수신함에 — 실행은 읽지 않는다. 최근 `TRIAL_KEEP` 개만 남긴다."""
    from app.modules.relay.definitions import trial_inbox

    folder = trial_inbox(slug)
    folder.mkdir(parents=True, exist_ok=True)
    kept = sorted(one for one in folder.iterdir() if one.name.endswith(SUFFIX))
    for old in kept[: max(0, len(kept) - TRIAL_KEEP + 1)]:
        old.unlink(missing_ok=True)
    name = f"{stamp()}{SUFFIX}"
    scratch = folder / f".{name}.tmp"
    scratch.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    os.replace(scratch, folder / name)
    return {
        "accepted": len(items),
        "queued": name,
        "trial": True,
        "note": "테스트 수신 데이터입니다. 실행에는 사용하지 않으며 정의 테스트와 일괄 초안 "
        "생성에만 사용합니다.",
    }
