"""AI(MCP)가 **서버를 시켜 바깥에 닿게 하는 길**의 울타리 — 원천 탐색 · 정의 초안 시험.

    주소   원천 · 대상의 host 가 `RELAY_SOURCE_HOSTS` 에 있거나, **켜진 정의가 이미 쓰는 것**
           (사람이 켠 것)이어야 한다. 비밀(`token_env`)을 실어 엉뚱한 곳에 보내지 못하게.
    파일   로컬 파일은 `RELAY_FILE_ROOTS` 안만 — 어댑터가 실제로 고른 파일(링크의 끝)을 다시
           본다(`FileAdapter.roots`).
    비밀   `token_env` 로 **이 플랫폼 자신의 설정**(DATABASE_URL · JWT_SECRET …)을
           가리키지 못한다 — 그 값이 원천 · 대상의 인증 머리글로 나간다.

켜진 정의가 도는 길(작업)은 사람이 켠 것이라 주소 울타리를 보지 않는다. 파일의 울타리는
도는 길에도 선다(`services.catalog` 가 문제로 적고 작업이 멈춘다).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from app.config import Settings, get_settings
from app.shared.errors import AppError, Forbidden, code
from relay_core.definition import SECRET_FIELDS, env_names
from relay_core.describe import Described

#: 서버가 바깥으로 닿는 자리의 앞머리 — 파일 원천은 공유 폴더(`smb://`)도 된다.
REMOTE = ("http://", "https://", "smb://")

#: 비밀처럼 보이는 값 — 주소 안의 비밀번호(`://u:p@`), 머리글 그대로(`Bearer …`).
_LOOKS_SECRET = re.compile(r"://[^/\s:@]+:[^/\s@]+@|^\s*(bearer|basic)\s+\S{8,}", re.I)


def source_url(kind: str, spec: dict[str, Any]) -> str:
    """어댑터가 닿는 주소 — 로컬 파일 · DB 면 빈 글."""
    where = {"rest": "base_url", "odata": "service_url", "xml": "url"}.get(kind)
    if where:
        return str(spec.get(where) or "")
    if kind == "file":
        location = str(spec.get("location") or "")
        return location if location.startswith(REMOTE) else ""
    return ""


def host_of(url: str) -> str:
    return urlsplit(url).netloc.rsplit("@", 1)[-1].lower()


def allowed_hosts(found: Iterable[Described]) -> set[str]:
    """설정의 주소 + **켜진 정의(파일 · 켜진 판)가 쓰는 주소** — 초안은 넣지 않는다."""
    out = set(get_settings().relay_source_host_list)
    for one in found:
        if one.draft:
            continue
        for url in (one.endpoint, one.target_server):
            if url.startswith(REMOTE):
                out.add(host_of(url))
    return out


def _host_ok(netloc: str, allowed: set[str]) -> bool:
    bare = netloc.split("]")[0] + "]" if netloc.startswith("[") else netloc.split(":")[0]
    return netloc in allowed or bare in allowed


def check_url(
    url: str, allowed: set[str], where: str, schemes: tuple[str, ...] = ("http", "https")
) -> None:
    parts = urlsplit(url)
    if parts.scheme not in schemes or not parts.netloc:
        raise AppError(
            code("RELAY", 9),
            f"{where}: {' · '.join(schemes)} 주소여야 합니다: {url!r}",
            status=422,
        )
    netloc = host_of(url)
    if not _host_ok(netloc, allowed):
        raise Forbidden(
            code("RELAY", 10),
            f"{where}: {netloc} 는 이 서버가 AI 요청으로 부를 수 있는 주소가 아닙니다. "
            "켜진 정의가 쓰는 주소이거나 RELAY_SOURCE_HOSTS 에 있어야 합니다 — 관리자에게 "
            "넣어 달라고 하세요.",
        )


def check_file(location: str) -> None:
    """로컬 파일 자리의 **고정된 앞부분**(glob 전)이 읽어도 되는 폴더 안인가. 진짜 울타리는
    어댑터가 고른 파일을 다시 보는 것이고, 이것은 일찍 까닭을 말하려는 것이다."""
    from pathlib import Path

    head = re.split(r"[*?\[]", location, maxsplit=1)[0]
    if not head or not Path(head).is_absolute():
        raise AppError(
            code("RELAY", 11),
            f"adapter.location 은 절대 경로여야 합니다: {location!r}",
            status=422,
        )
    real = Path(head).resolve()
    roots = get_settings().relay_file_root_paths
    if not any(real == root or real.is_relative_to(root) for root in roots):
        raise Forbidden(
            code("RELAY", 11),
            f"{location} 는 읽어도 되는 폴더(RELAY_FILE_ROOTS: "
            f"{', '.join(str(one) for one in roots)}) 밖입니다.",
        )


def check_source(kind: str, spec: dict[str, Any], allowed: set[str]) -> None:
    url = source_url(kind, spec)
    if url:
        schemes = ("http", "https", "smb") if kind == "file" else ("http", "https")
        check_url(url, allowed, "adapter", schemes)
    elif kind == "file":
        check_file(str(spec.get("location") or ""))


def check_target(target: dict[str, Any], allowed: set[str]) -> None:
    check_url(str(target.get("server") or ""), allowed, "target.server")


def forbidden_env() -> set[str]:
    """이 플랫폼 자신의 설정 이름 — 정의가 비밀 자리로 가리키면 안 된다."""
    return {name.upper() for name in Settings.model_fields}


def check_env(body: Any) -> None:
    bad = sorted(set(env_names(body)) & forbidden_env())
    if bad:
        raise Forbidden(
            code("RELAY", 13),
            f"{', '.join(bad)} 는 이 플랫폼 자신의 설정입니다 — 원천 · 대상의 비밀로 쓰지 "
            "않습니다. 그 시스템의 토큰을 담을 환경 변수를 따로 두세요.",
        )


def check_no_secrets(body: Any, where: str = "") -> None:
    """정의 어디에도 비밀이 없나 — 비밀 칸(`token` · `password` …) 이름, 비밀처럼 보이는 값."""
    if isinstance(body, dict):
        for key, value in body.items():
            here = f"{where}.{key}" if where else str(key)
            if key in SECRET_FIELDS:
                raise AppError(
                    code("RELAY", 9),
                    f"{here}: 비밀은 정의에 적지 않습니다 — 환경 변수 이름(token_env)을 "
                    "적으세요.",
                    status=422,
                )
            check_no_secrets(value, here)
    elif isinstance(body, list):
        for index, one in enumerate(body):
            check_no_secrets(one, f"{where}[{index}]")
    elif isinstance(body, str) and _LOOKS_SECRET.search(body):
        raise AppError(
            code("RELAY", 9),
            f"{where}: 비밀처럼 보이는 값입니다(주소 안의 비밀번호 · 인증 머리글) — 정의에는 "
            "환경 변수 이름만 적습니다.",
            status=422,
        )
