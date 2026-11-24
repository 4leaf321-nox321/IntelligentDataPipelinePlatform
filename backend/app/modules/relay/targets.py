"""대상 SP 상태의 저장 — 타이머가 조회해 두고, 연결 지도 · 파이프라인 목록 · 요약은 저장값을
읽는다.

지도를 그릴 때마다 대상마다 `/api/health` · 코어 현황을 요청 안에서 차례로 조회하면, 대상이
늘수록 첫 화면이 느려지고 연결되지 않는 대상 하나가 그 타임아웃만큼 화면 전체를
늦춘다(2026-11-24 검토). 그래서 5분 타이머(`scripts/relay_due.py`)가 조회해
`target-status.json` 에 두고 화면은 그것을 읽는다.

저장값이 없거나 `RELAY_TARGET_FRESH_SECONDS` 보다 오래됐으면(타이머가 없는 개발 PC · 멈춘
타이머) 그 대상만 직접 조회한다 — 대상끼리 병렬로. 0 이면 늘 직접 조회한다(옛 동작).

**토큰은 저장하지 않는다** — 코어 현황에서는 수신자의 이름 · 담당 · 마지막 수신 시각만 남긴다.
파일은 이중화의 공용 폴더(`relay_work_dir` 의 부모 — 인증 정보 · 허용 주소와 같은 자리)에 둔다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings

FILE = "target-status.json"
#: 한 번에 조회하는 대상 수 — 대상마다 3초(`services.PROBE_SECONDS`)가 걸려도 화면이 그 합만큼
#: 기다리지 않게.
WORKERS = 8

#: (주소, 토큰, 경로) → (상태 코드, 본문). 시험이 `services.probe_http` 를 바꿔 끼우므로 부르는
#: 쪽이 그 함수를 넘긴다.
Probe = Callable[[str, str, str], tuple[int, Any]]


def _file() -> Path:
    return get_settings().relay_work_dir.parent / FILE


def load() -> dict[str, dict[str, Any]]:
    """저장된 상태 — `{주소: 항목}`. 없거나 읽지 못하면 빈 것(그러면 직접 조회한다)."""
    file = _file()
    if not file.is_file():
        return {}
    try:
        body = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(body, dict):
        return {}
    return {str(key): one for key, one in body.items() if isinstance(one, dict)}


def _save(entries: Mapping[str, dict[str, Any]]) -> None:
    file = _file()
    file.parent.mkdir(parents=True, exist_ok=True)
    # 웹 프로세스와 타이머가 함께 쓸 수 있다 — 임시 파일 이름을 프로세스마다 달리해 바꿔
    # 끼운다.
    scratch = file.with_name(f"{file.name}.{os.getpid()}.tmp")
    scratch.write_text(
        json.dumps(dict(entries), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(scratch, file)


def check(server: str, token: str, probe: Probe) -> dict[str, Any]:
    """대상 하나를 지금 조회한다 — 상태 확인과, 토큰이 있으면 코어 현황의 수신자."""
    entry: dict[str, Any] = {"checked_at": datetime.now(UTC).isoformat(), "reachable": False}
    status_code, health = probe(server, "", "/api/health")
    if status_code != 200 or not isinstance(health, dict):
        return entry
    entry.update(
        reachable=True,
        slug=health.get("slug"),
        version=health.get("version"),
        app=health.get("app"),
        consumers=[],
    )
    if not token:
        return entry
    status_code, core = probe(server, token, "/api/ontology/core-status")
    if status_code != 200 or not isinstance(core, dict):
        return entry
    entry["consumers"] = [
        {
            "name": str(one.get("name") or ""),
            "owner": one.get("owner"),
            "last_used_at": one.get("last_used_at"),
        }
        for one in core.get("consumers") or []
        if isinstance(one, dict) and one.get("name")
    ]
    return entry


def refresh(targets: Mapping[str, str], probe: Probe) -> dict[str, dict[str, Any]]:
    """주어진 대상(`{주소: 토큰}`)을 지금 조회해 저장하고 돌려준다 — 병렬로."""
    if not targets:
        return {}
    items = list(targets.items())
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(items))) as pool:
        found = list(pool.map(lambda item: check(item[0], item[1], probe), items))
    checked = {server: entry for (server, _token), entry in zip(items, found, strict=True)}
    _save({**load(), **checked})
    return checked


def _stale(entry: dict[str, Any] | None, now: datetime, limit: timedelta) -> bool:
    if not entry or not limit:
        return True
    try:
        at = datetime.fromisoformat(str(entry.get("checked_at")))
    except ValueError:
        return True
    return now - at > limit


def current(
    targets: Mapping[str, str], probe: Probe, *, force: bool = False
) -> dict[str, dict[str, Any]]:
    """화면이 읽는 상태 — 저장값이 새것이면 그대로, 없거나 오래됐거나 `force` 면 그 대상만
    조회한다."""
    saved = load()
    limit = timedelta(seconds=max(get_settings().relay_target_fresh_seconds, 0))
    now = datetime.now(UTC)
    stale = {
        server: token
        for server, token in targets.items()
        if force or _stale(saved.get(server), now, limit)
    }
    checked = refresh(stale, probe)
    return {server: checked.get(server) or saved[server] for server in targets}


def known(db: Session) -> dict[str, str]:
    """조회할 대상 — 사용 중인 정의의 대상 SP 주소와 그 토큰(`{주소: 토큰}`). 초안의 대상은
    조회하지 않는다(사람이 아직 확인하지 않은 주소다)."""
    from app.modules.relay import services

    env = services.secrets_env()
    found: dict[str, str] = {}
    for one in services.catalog(db):
        if one.draft or not one.target_server:
            continue
        found.setdefault(one.target_server, env.get(one.target_token_env, ""))
    return found
