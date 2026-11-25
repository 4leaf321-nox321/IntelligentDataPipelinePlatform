"""허용 주소 — AI(MCP)의 탐색 · 시험이 닿아도 되는 곳을 **화면에서, 재시작 없이** 더한다.

울타리(`guard.fence`)는 세 곳을 합친다:

- 서버 설정 — `RELAY_SOURCE_HOSTS`(원천 · 대상 둘 다 — 예전부터의 뜻) · `RELAY_TARGET_HOSTS`
  (대상 SP 로만). 고치려면 `.env` 와 재시작.
- **화면에서 더한 것** — 이 모듈(중계 폴더의 `allowed-hosts.json`). 원천 주소 · 대상 SP 주소를
  따로 둔다 — 대상 주소는 원천 주소로 못 쓴다(원천의 비밀이 대상 쪽으로 나가지 않게).
- 사용 중인 정의가 쓰는 주소 — 사람이 켠 것이라 따로 적지 않는다.

넣기 · 빼기는 **사람만**(화면 — 토큰(MCP)은 거절, 울타리를 AI 가 넓히면 울타리가 아니다).
목록은 토큰으로도 읽는다 — AI 가 「어디가 막혔나」 를 보고 사람에게 부탁한다. 바꾼 것은 감사에
남는다(더한 것 · 뺀 것).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.shared import audit
from app.shared.errors import AppError, code

#: `host` · `host:port` · `[v6]` · `[v6]:port` — 앞에 `http://` · 뒤에 경로는 받지 않는다.
HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?|\[[0-9a-f:.]+\])(?::\d{1,5})?$")
#: 목록 하나의 상한.
MAX_HOSTS = 200
KINDS = ("source", "target")


def path() -> Path:
    return get_settings().relay_work_dir.parent / "allowed-hosts.json"


def _read() -> dict[str, Any]:
    try:
        body = json.loads(path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def stored() -> dict[str, list[str]]:
    """화면에서 더한 주소 — `{"source": [...], "target": [...]}`."""
    body = _read()
    return {
        kind: [str(one).lower() for one in body.get(kind) or [] if isinstance(one, str)]
        for kind in KINDS
    }


@contextmanager
def _locked() -> Iterator[None]:
    """이중화의 두 서버가 같은 중계 폴더를 쓴다 — 고치는 동안 잠근다."""
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target.with_name("allowed-hosts.lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _clean(kind: str, given: list[str]) -> list[str]:
    out: list[str] = []
    bad: list[str] = []
    for one in given:
        host = str(one).strip().lower()
        if not host:
            continue
        if not HOST.fullmatch(host):
            bad.append(str(one))
        elif host not in out:
            out.append(host)
    if bad:
        raise AppError(
            code("RELAY", 39),
            "주소는 host 또는 host:port 형식으로 입력하세요(http://와 경로 제외): "
            + ", ".join(bad[:5]),
            status=422,
        )
    if len(out) > MAX_HOSTS:
        raise AppError(
            code("RELAY", 39), f"{kind} 주소는 최대 {MAX_HOSTS}개입니다.", status=422
        )
    return out


def status(db: Session) -> dict[str, Any]:
    """종류마다 — 서버 설정 · 화면에서 더한 것 · 사용 중인 정의가 쓰는 것."""
    from app.modules.relay import guard, services

    settings = get_settings()
    screen = stored()
    used_source: set[str] = set()
    used_target: set[str] = set()
    for one in services.catalog(db):
        if one.draft:
            continue
        adapter = dict(one.body.get("adapter") or {})
        urls = [one.endpoint, guard.token_url(adapter)]
        if one.adapter_kind == "kafka":
            urls += guard.kafka_urls(adapter)
        used_source |= {guard.host_of(url) for url in urls if url.startswith(guard.REMOTE)}
        if one.target_server.startswith(guard.REMOTE):
            used_target.add(guard.host_of(one.target_server))
    meta = _read()
    return {
        "source": {
            "settings": list(settings.relay_source_host_list),
            "screen": screen["source"],
            "definitions": sorted(used_source),
        },
        "target": {
            "settings": sorted(
                {*settings.relay_target_host_list, *settings.relay_source_host_list}
            ),
            "screen": screen["target"],
            "definitions": sorted(used_target),
        },
        "updated_at": meta.get("updated_at"),
        "updated_by": meta.get("updated_by"),
    }


def put(
    db: Session,
    user: User,
    request: Request,
    source: list[str],
    target: list[str],
    *,
    approval: str | None = None,
) -> dict[str, Any]:
    """화면의 사람이나, 대화에서 승인받은 토큰(`approval.py`)만 바꾼다 — AI 가 자기 탐색 범위를
    스스로 넓히지 않게."""
    from app.modules.relay import approval as approving

    approved = approving.check(
        request, user, what="허용 주소 변경", refused=39, approval=approval
    )
    wanted = {"source": _clean("원천", source), "target": _clean("대상", target)}
    with _locked():
        before = stored()
        body = {
            **wanted,
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "updated_by": user.email,
        }
        file = path()
        scratch = file.with_name(f".{file.name}.tmp")
        scratch.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(scratch, file)
    changes = {
        kind: {
            "added": sorted(set(wanted[kind]) - set(before[kind])),
            "removed": sorted(set(before[kind]) - set(wanted[kind])),
        }
        for kind in KINDS
    }
    audit.record(
        db,
        action="relay.allowed_hosts.set",
        actor=user,
        target_table="relay_allowed_hosts",
        target_id=None,
        target_label="허용 주소",
        changes=changes,
    )
    approving.record(
        db, user, approved, action="allowed_hosts", target="허용 주소", hosts=changes
    )
    db.commit()
    return status(db)
