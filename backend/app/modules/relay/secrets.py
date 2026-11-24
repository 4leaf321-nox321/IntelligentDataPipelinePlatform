"""비밀 입력 — 원천 · 대상의 토큰 · 비밀번호를 **사람이 화면에서** 넣는다. 정의(AI 가
짓는다)에는 그 비밀이 든 환경 변수의 **이름**만 있다 — 사람은 값만 넣고, 나머지는 AI 가
고른다.

- 값은 중계 폴더의 비밀 함(`secrets.json`, 0600)에만 둔다. 플랫폼 설정(`.env` — DB 비밀번호 ·
  JWT 키)과 섞지 않는다. 운영자가 서버에서 관리하는 `.env` · `RELAY_SECRETS_FILE` 은 그대로
  읽힌다 — 같은 이름이면 화면에서 넣은 값이 먼저, 프로세스 환경 변수가 그보다 먼저다
  (`services.secrets_env`).
- **이름은 정의가 쓰는 것만**(정의 파일 · 사용 중인 판 · 초안) — 아무 이름이나 넣게 하면 이
  함이 플랫폼 설정을 덮는 길이 된다. 플랫폼 자신의 설정 이름은 거절한다(`guard.forbidden_env`).
- **값은 다시 보이지 않는다** — 목록은 「입력됨 · 서버 설정 · 비어 있음」 과 마지막 입력 시각 ·
  사람뿐. 감사 기록에도 이름만 남는다.
- **넣기 · 지우기는 사람만**(화면) — 토큰(MCP)으로 온 요청은 거절한다. 목록은 토큰으로도
  읽는다(값이 없다) — AI 가 「무엇이 비었나」 를 보고 사람에게 부탁한다.

JSON 으로 두는 까닭: dotenv 는 따옴표 안의 `${…}` 를 풀고, `\\` 로 끝나는 값을 읽지 못한다
(2026-11-17 실측) — 토큰이 그런 글자를 품으면 조용히 바뀐다.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.accounts.models import User
from app.modules.relay import guard
from app.modules.relay.models import RelayDefinition
from app.shared import audit
from app.shared.errors import AppError, Forbidden, code
from relay_core.definition import ENV_KEYS

#: 비밀 이름 — 환경 변수 꼴(대문자 · 숫자 · `_`).
NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
#: 값의 상한 — 토큰 · 비밀번호 · client secret 은 이보다 짧다.
VALUE_MAX = 8192

SOURCE, TARGET, CERT, PUSH = "원천 인증", "대상 토큰", "인증서 키 암호", "보내는 쪽 비밀"


def path() -> Path:
    return get_settings().relay_work_dir.parent / "secrets.json"


def _meta_path() -> Path:
    return path().with_name("secrets.meta.json")


def _wanted_path() -> Path:
    """정의를 짓기 전에 AI 가 부탁한 이름 — 탐색부터 비밀이 필요할 때(`request`)."""
    return path().with_name("secrets.wanted.json")


def _read(file: Path) -> dict[str, Any]:
    try:
        body = json.loads(file.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


def stored() -> dict[str, str]:
    """화면에서 넣은 값 — `services.secrets_env` 가 읽는다."""
    return {k: v for k, v in _read(path()).items() if isinstance(v, str)}


@contextmanager
def _locked() -> Iterator[None]:
    """이중화의 두 서버가 같은 중계 폴더를 쓴다 — 함을 고치는 동안 잠근다."""
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target.with_name("secrets.lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def _write(file: Path, body: dict[str, Any]) -> None:
    temp = file.with_name(f".{file.name}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(body, handle, ensure_ascii=False, indent=1, sort_keys=True)
    os.chmod(temp, 0o600)
    os.replace(temp, file)


def _role(where: list[str], adapter_kind: str) -> str:
    if where and where[0] == "target":
        return TARGET
    if where and where[-1] == "cert_password_env":
        return CERT
    return PUSH if adapter_kind == "push" else SOURCE


def _walk(body: Any, trail: list[str]) -> Iterator[tuple[list[str], str]]:
    if isinstance(body, dict):
        for key, value in body.items():
            if key in ENV_KEYS and isinstance(value, str) and value:
                yield [*trail, key], value
            else:
                yield from _walk(value, [*trail, key])
    elif isinstance(body, list):
        for one in body:
            yield from _walk(one, trail)


def uses(db: Session) -> dict[str, list[dict[str, str]]]:
    """비밀 이름 → 쓰는 곳(파이프라인 · 역할 · 판). 정의 파일 · 사용 중인 판 · 초안 모두."""
    from app.modules.relay import services

    found: dict[str, list[dict[str, str]]] = {}

    def add(slug: str, body: Any, state: str) -> None:
        kind = str(((body or {}).get("adapter") or {}).get("kind") or "")
        for where, name in _walk(body, []):
            entry = {"pipeline": slug, "role": _role(where, kind), "state": state}
            if entry not in found.setdefault(name, []):
                found[name].append(entry)

    for one in services.catalog(db):
        add(one.slug, one.body, "초안" if one.draft else "사용 중")
    for row in db.scalars(select(RelayDefinition).where(RelayDefinition.status == "draft")):
        add(row.pipeline_slug, row.body, f"초안 버전 {row.version}")
    for name, info in _read(_wanted_path()).items():
        if isinstance(info, dict) and name not in found:
            found[name] = [
                {
                    "pipeline": str(info.get("pipeline") or ""),
                    "role": str(info.get("role") or SOURCE),
                    "state": "요청됨(정의 전)",
                }
            ]
    return dict(sorted(found.items()))


def request(db: Session, user: User, pipeline: str, names: list[str], role: str) -> list[str]:
    """정의를 짓기 전에 필요한 비밀 이름을 **목록에 올린다**(값 없음) — AI(MCP)가 탐색 · 한
    번에 초안 전에 사람에게 입력을 부탁할 수 있게. 토큰도 된다(이름만 남긴다)."""
    bad = [one for one in names if not NAME.fullmatch(one)]
    if bad:
        raise AppError(
            code("RELAY", 37),
            f"인증 정보 이름 형식이 올바르지 않습니다: {', '.join(bad)}",
            status=422,
        )
    clash = sorted(one for one in names if one.upper() in guard.forbidden_env())
    if clash:
        raise Forbidden(
            code("RELAY", 13),
            f"{', '.join(clash)}는 이 플랫폼의 설정입니다. 원천, 대상의 인증 정보 이름으로 "
            "사용할 수 없습니다.",
        )
    with _locked():
        wanted = _read(_wanted_path())
        for one in names:
            wanted[one] = {
                "pipeline": pipeline,
                "role": role if role in (SOURCE, TARGET, CERT, PUSH) else SOURCE,
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "by": user.email,
            }
        _write(_wanted_path(), wanted)
    return names


def listing(db: Session) -> list[dict[str, Any]]:
    """정의가 쓰는 비밀마다 — 값 없이 상태만."""
    from app.modules.relay import services

    screen = stored()
    meta = _read(_meta_path())
    server = services.server_secrets()
    out = []
    for name, where in uses(db).items():
        if name in os.environ:
            status, note = (
                "server",
                "서버의 프로세스 환경 변수에 있습니다. 이 값을 사용합니다.",
            )
        elif name in screen:
            status, note = "screen", ""
            if name in server:
                note = "서버 설정(.env)에도 있습니다. 화면에서 입력한 값을 사용합니다."
        elif name in server:
            status, note = "server", "서버 설정(.env)에 있습니다."
        else:
            status, note = (
                "missing",
                "아직 입력하지 않았습니다. 이 인증 정보를 사용하는 원천, 대상에 연결할 수 "
                "없습니다.",
            )
        given = meta.get(name)
        info: dict[str, Any] = given if isinstance(given, dict) else {}
        out.append(
            {
                "name": name,
                "status": status,
                "note": note,
                "uses": where,
                "updated_at": info.get("at") if name in screen else None,
                "updated_by": info.get("by") if name in screen else None,
                "forbidden": name.upper() in guard.forbidden_env(),
            }
        )
    return out


def _human_only(request: Request) -> None:
    if getattr(request.state, "token_scopes", None) is not None:
        raise Forbidden(
            code("RELAY", 36),
            "인증 정보는 사람이 화면에서만 입력합니다. 토큰(MCP)으로는 입력하거나 삭제할 수 "
            "없습니다. 사람에게 [중계 › 인증 정보] 화면에서 입력을 요청하세요.",
        )


def _check_name(db: Session, name: str) -> None:
    if not NAME.fullmatch(name):
        raise AppError(
            code("RELAY", 37),
            "인증 정보 이름은 대문자, 숫자, _로 입력하세요(대문자로 시작, 64자 이내): "
            f"{name!r}",
            status=422,
        )
    if name.upper() in guard.forbidden_env():
        raise Forbidden(
            code("RELAY", 13),
            f"{name}는 이 플랫폼의 설정입니다. 화면에서 입력할 수 없습니다.",
        )
    if name not in uses(db):
        raise AppError(
            code("RELAY", 37),
            f"{name}를 사용하는 정의가 없습니다. 정의(초안 포함)에서 이 이름을 지정한 후 "
            "입력하세요.",
            status=422,
        )


def put(db: Session, user: User, request: Request, name: str, value: str) -> dict[str, Any]:
    _human_only(request)
    _check_name(db, name)
    if not value or len(value) > VALUE_MAX or "\n" in value or "\r" in value:
        raise AppError(
            code("RELAY", 37),
            f"값은 한 줄, {VALUE_MAX}자 이내여야 합니다. 값을 비우려면 삭제하세요.",
            status=422,
        )
    with _locked():
        body = stored()
        was = name in body
        body[name] = value
        _write(path(), body)
        meta = _read(_meta_path())
        meta[name] = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "by": user.email}
        _write(_meta_path(), meta)
    audit.record(
        db,
        action="relay.secret.set",
        actor=user,
        target_table="relay_secrets",
        target_id=None,
        target_label=name,
        changes={"name": name, "replaced": was},
    )
    db.commit()
    return {"name": name, "status": "screen"}


def delete(db: Session, user: User, request: Request, name: str) -> dict[str, Any]:
    _human_only(request)
    with _locked():
        body = stored()
        if name not in body:
            raise AppError(
                code("RELAY", 37), f"화면에서 입력한 {name}가 없습니다.", status=404
            )
        del body[name]
        _write(path(), body)
        meta = _read(_meta_path())
        meta.pop(name, None)
        _write(_meta_path(), meta)
    audit.record(
        db,
        action="relay.secret.delete",
        actor=user,
        target_table="relay_secrets",
        target_id=None,
        target_label=name,
        changes={"name": name},
    )
    db.commit()
    return {"name": name, "status": "deleted"}


def missing(db: Session, names: Mapping[str, Any] | list[str]) -> list[str]:
    """그 이름들 중 아직 아무 데도 없는 것 — 「한 번에 초안」 이 사람에게 부탁할 목록."""
    from app.modules.relay import services

    env = services.secrets_env()
    return [name for name in names if not env.get(name)]
