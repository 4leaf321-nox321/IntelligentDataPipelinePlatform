"""보내 주는 원천(push)의 받는 자리 — `POST /api/relay/inbound/{slug}`.

**로그인 없이** 원천 시스템이 부른다 — 그래서 차례가 중요하다:

1. 크기 상한(`MAX_BYTES`) — 읽기 전에 `Content-Length` 로, 읽은 뒤 실제 길이로.
2. 받을 파이프라인인가 — 사용 중인 정의이고 어댑터가 `push` 여야 한다. 아니면 **없는 것과
   같은 404**(무엇이 있는지 알려 주지 않는다).
3. 비밀 — 머리글 `X-Relay-Token` 을 그 정의의 `auth.token_env` 값과 상수 시간으로 견준다.
   `Authorization` 을 안 쓰는 까닭: 그 머리글은 사람 · 토큰(PAT)의 자리다.
4. 모양 · 건수 — JSON 목록(또는 `rows_path` 자리의 목록), `max_items` 이하.
5. 받을 함이 넘치지 않았나(`MAX_WAITING`) — 실행이 멈춰 쌓이기만 하면 디스크가 찬다.
6. 임시 파일에 다 쓰고 바꿔 끼운다 — 반쪽 묶음을 실행이 읽지 않게.

받은 묶음을 미러에 넣는 것은 실행이다(`relay_core.adapters.push`) — 받는 자리는 쌓기만 한다.
"""

from __future__ import annotations

import hmac
import json
import os
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.relay import services
from app.shared.errors import AppError, NotFound, code
from relay_core.adapters.push import SUFFIX, items_of
from relay_core.adapters.rest import SourceError
from relay_core.state import stamp

MAX_BYTES = 20 * 1024 * 1024
MAX_WAITING = 1000
HEADER = "X-Relay-Token"


def _nowhere(slug: str) -> NotFound:
    return NotFound(code("RELAY", 31), f"받을 곳이 없습니다: {slug}")


def check_size(length: str | None) -> None:
    if length and length.isdigit() and int(length) > MAX_BYTES:
        raise AppError(
            code("RELAY", 32),
            f"한 번에 {MAX_BYTES // (1024 * 1024)}MB 까지 받습니다.",
            status=413,
        )


def receive(db: Session, slug: str, token: str, raw: bytes) -> dict[str, Any]:
    check_size(str(len(raw)))
    try:
        found = services.by_slug(slug, db)
    except NotFound:
        raise _nowhere(slug) from None
    if found.draft or found.problems or found.adapter_kind != "push":
        raise _nowhere(slug)
    adapter = dict(found.body.get("adapter") or {})
    auth = dict(adapter.get("auth") or {})
    secret = services.secrets_env().get(str(auth.get("token_env") or ""), "")
    # 비밀이 비었으면 아무도 못 보낸다 — 빈 머리글과 같다고 받지 않는다.
    if not secret or not token or not hmac.compare_digest(token.encode(), secret.encode()):
        raise AppError(code("RELAY", 33), f"{HEADER} 가 맞지 않습니다.", status=401)
    try:
        body = json.loads(raw)
        items = items_of(body, str(adapter.get("rows_path") or ""))
    except (ValueError, SourceError) as failure:
        raise AppError(
            code("RELAY", 34), f"보낸 본문을 읽지 못했습니다: {failure}", status=422
        ) from None
    most = int(adapter.get("max_items") or 50_000)
    if len(items) > most:
        raise AppError(
            code("RELAY", 32),
            f"한 번에 {most:,}개까지 받습니다(보낸 것 {len(items):,}개).",
            status=413,
        )
    inbox = get_settings().relay_work_dir / slug / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    waiting = sum(1 for one in inbox.iterdir() if one.name.endswith(SUFFIX))
    if waiting >= MAX_WAITING:
        raise AppError(
            code("RELAY", 35),
            f"받을 함에 묶음이 {waiting}개 쌓였습니다 — 실행이 받아 가지 못하고 있습니다(실행 "
            "멈춤 · 늦음을 확인하세요).",
            status=429,
        )
    name = f"{stamp()}{SUFFIX}"
    scratch = inbox / f".{name}.tmp"
    scratch.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    os.replace(scratch, inbox / name)
    return {"accepted": len(items), "queued": name, "waiting": waiting + 1}
