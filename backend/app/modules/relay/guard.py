"""AI(MCP)가 **서버를 시켜 바깥에 닿게 하는 길**의 울타리 — 원천 탐색 · 정의 초안 시험.

    주소   원천 · 대상의 host 가 `RELAY_SOURCE_HOSTS` 에 있거나, **켜진 정의가 이미 쓰는 것**
           (사람이 켠 것)이어야 한다. 비밀(`token_env`)을 실어 엉뚱한 곳에 보내지 못하게.
    파일   로컬 파일은 `RELAY_FILE_ROOTS` 안만 — 어댑터가 실제로 고른 파일(링크의 끝)을 다시
           본다(`FileAdapter.roots`).
    비밀   `token_env` 로 **이 플랫폼 자신의 설정**(DATABASE_URL · JWT_SECRET …)을
           가리키지 못한다 — 그 값이 원천 · 대상의 인증 머리글로 나간다.
    묶임   사용 중인 정의가 쓰는 비밀은 **그 역할 · 그 주소에 묶인다** — 대상 SP 의 토큰
           (JVHUB_TOKEN)을 원천 인증에 실어 SP 를 부르거나(적용도 POST 다), PLM 의 토큰을
           다른 주소로 보내지 못한다. 아직 아무 정의도 안 쓰는 새 비밀은 울타리 안 주소로만.

켜진 정의가 도는 길(작업)은 사람이 켠 것이라 주소 울타리를 보지 않는다. 파일의 울타리는
도는 길에도 선다(`services.catalog` 가 문제로 적고 작업이 멈춘다).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from app.config import Settings, get_settings
from app.shared.errors import AppError, Forbidden, code
from relay_core.definition import SECRET_FIELDS, env_names
from relay_core.describe import Described

#: 서버가 바깥으로 닿는 자리의 앞머리 — 파일 원천은 공유 폴더(`smb://`)도 된다.
REMOTE = ("http://", "https://", "smb://", "sftp://", "kafka://")

#: 비밀처럼 보이는 값 — 주소 안의 비밀번호(`://u:p@` · `://:p@`), 머리글 그대로
#: (`Bearer …`), 주소의 질의에 실은 키(`?access_token=…` · `&api_key=…`).
_LOOKS_SECRET = re.compile(
    r"://[^/\s:@]*:[^/\s@]+@"
    r"|^\s*(bearer|basic)\s+\S{8,}"
    r"|[?&](api[_-]?key|apikey|access[_-]?token|token|password|passwd|pwd|secret"
    r"|client[_-]?secret|sig|signature)=[^&\s]{4,}",
    re.I,
)
#: 비밀처럼 보이는 칸 이름 — `SECRET_FIELDS`(정확히 그 이름) 말고도 `api_key` · `client_secret`
#: · `access_token` · `X-Api-Key` … 이름만 담는 `…_env` 는 된다.
_SECRET_KEY = re.compile(
    r"(^|[_-])(password|passwd|pwd|secret|token|api[_-]?key|apikey|private[_-]?key"
    r"|credentials?|authorization|cookie)$",
    re.I,
)


def source_url(kind: str, spec: dict[str, Any]) -> str:
    """어댑터가 닿는 주소 — 로컬 파일 · DB 면 빈 글. Kafka 는 첫 브로커(전부는
    `kafka_urls`)."""
    where = {"rest": "base_url", "odata": "service_url", "xml": "url"}.get(kind)
    if where:
        return str(spec.get(where) or "")
    if kind == "file":
        location = str(spec.get("location") or "")
        return location if location.startswith(REMOTE) else ""
    if kind == "kafka":
        found = kafka_urls(spec)
        return found[0] if found else ""
    return ""


def kafka_urls(spec: dict[str, Any]) -> list[str]:
    """Kafka 가 닿는 곳 전부 — 브로커마다(`kafka://host:port`)와 스키마 레지스트리. 비밀이 다
    실려 간다(레지스트리의 비밀은 레지스트리로)."""
    from relay_core.adapters.kafka import endpoints

    found = endpoints(str(spec.get("bootstrap") or ""))
    registry = spec.get("schema_registry")
    if isinstance(registry, dict) and registry.get("url"):
        found.append(str(registry["url"]))
    return found


def host_of(url: str) -> str:
    return urlsplit(url).netloc.rsplit("@", 1)[-1].lower()


#: 비밀이 실리는 역할 — 원천의 인증(`adapter…token_env`) · 대상의 인증(`target.token_env`).
SOURCE, TARGET = "원천", "대상"


@dataclass(frozen=True)
class Fence:
    """AI 요청이 닿아도 되는 곳 — 주소와 비밀의 묶임. `fence(catalog)` 로 짓는다."""

    hosts: frozenset[str] = frozenset()
    """설정(`RELAY_SOURCE_HOSTS`)의 주소 + 사용 중인 정의(초안 아닌 것)가 쓰는 주소."""
    bound: Mapping[str, frozenset[tuple[str, str]]] = field(default_factory=dict)
    """사용 중인 정의가 쓰는 비밀의 이름 → 그 비밀이 실려 가는 (역할, host)."""
    targets: frozenset[str] = frozenset()
    """**대상 SP 로** 닿아도 되는 주소 — `hosts` 에 더해 `RELAY_TARGET_HOSTS` · 화면에서 더한
    대상 주소(`relay/allow.py`). 이것만 있는 주소는 원천 주소로는 안 된다."""


def token_url(spec: dict[str, Any]) -> str:
    """oauth2 의 토큰 주소 — client secret 이 실려 가는 또 하나의 곳."""
    raw = spec.get("auth")
    auth: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return str(auth.get("token_url") or "") if auth.get("kind") == "oauth2" else ""


def _uses(one: Described) -> list[tuple[str, str, str]]:
    """그 정의가 비밀을 싣는 곳 — (이름, 역할, host). oauth2 면 토큰 주소에도 실린다."""
    out: list[tuple[str, str, str]] = []
    adapter = dict(one.body.get("adapter") or {})
    if one.endpoint.startswith(REMOTE):
        out += [(name, SOURCE, host_of(one.endpoint)) for name in env_names(adapter)]
    if one.adapter_kind == "kafka":
        for url in kafka_urls(adapter):
            out += [(name, SOURCE, host_of(url)) for name in env_names(adapter)]
    issuer = token_url(adapter)
    if issuer.startswith(REMOTE):
        out += [(name, SOURCE, host_of(issuer)) for name in env_names(adapter)]
    if one.target_token_env and one.target_server.startswith(REMOTE):
        out.append((one.target_token_env, TARGET, host_of(one.target_server)))
    return out


def fence(found: Iterable[Described]) -> Fence:
    """설정의 주소 + **화면에서 더한 주소**(`relay/allow.py` — 재시작 없이) + **켜진 정의(파일
    · 켜진 판)** 가 쓰는 주소와 비밀 — 초안은 넣지 않는다. 대상 SP 만의 주소는 따로
    (`targets`)."""
    from app.modules.relay import allow

    settings = get_settings()
    screen = allow.stored()
    hosts = set(settings.relay_source_host_list) | set(screen["source"])
    targets = set(settings.relay_target_host_list) | set(screen["target"])
    bound: dict[str, set[tuple[str, str]]] = {}
    for one in found:
        if one.draft:
            continue
        adapter = dict(one.body.get("adapter") or {})
        issuer = token_url(adapter)
        more = kafka_urls(adapter) if one.adapter_kind == "kafka" else []
        for url in (one.endpoint, one.target_server, issuer, *more):
            if url.startswith(REMOTE):
                hosts.add(host_of(url))
        for name, role, host in _uses(one):
            bound.setdefault(name, set()).add((role, host))
    return Fence(
        frozenset(hosts),
        {k: frozenset(v) for k, v in bound.items()},
        frozenset(targets | hosts),
    )


def check_secret(name: str, role: str, url: str, fenced: Fence) -> None:
    """그 비밀을 그 역할로 그 주소에 실어도 되나 — 묶인 비밀은 묶인 곳만."""
    if not name or name not in fenced.bound:
        return
    host = host_of(url)
    if (role, host) in fenced.bound[name]:
        return
    where = " · ".join(f"{r} {h}" for r, h in sorted(fenced.bound[name]))
    raise Forbidden(
        code("RELAY", 21),
        f"{name} 은 사용 중인 정의가 {where} 에 쓰는 비밀입니다 — {role} {host} 에 실어 "
        "보낼 수 없습니다. 그 시스템의 토큰을 담을 환경 변수를 따로 두세요.",
    )


def _host_ok(netloc: str, allowed: Iterable[str]) -> bool:
    allowed = set(allowed)
    bare = netloc.split("]")[0] + "]" if netloc.startswith("[") else netloc.split(":")[0]
    return netloc in allowed or bare in allowed


def check_url(
    url: str,
    allowed: Iterable[str],
    where: str,
    schemes: tuple[str, ...] = ("http", "https"),
    *,
    target: bool = False,
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
        place = (
            "「대상 SP」(설정은 RELAY_TARGET_HOSTS)"
            if target
            else "「원천」(설정은 RELAY_SOURCE_HOSTS)"
        )
        raise Forbidden(
            code("RELAY", 10),
            f"{where}: {netloc} 는 이 서버가 AI 요청으로 호출할 수 있는 주소가 아닙니다 — "
            f"사용 중인 정의의 주소이거나 허용 주소여야 합니다. 사람에게 화면 › 중계 › 허용 "
            f"주소 의 {place} 에 더해 달라고 하세요(재시작 없음).",
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


def check_source(kind: str, spec: dict[str, Any], fenced: Fence) -> None:
    if kind == "kafka":
        # 브로커마다 · 레지스트리 — 하나라도 울타리 밖이면 거절한다(비밀이 거기로도 간다).
        for one in kafka_urls(spec):
            scheme = ("kafka",) if one.startswith("kafka://") else ("http", "https")
            check_url(one, fenced.hosts, "adapter", scheme)
            for name in env_names(spec):
                check_secret(name, SOURCE, one, fenced)
        url = ""
    else:
        url = source_url(kind, spec)
    if url:
        schemes = ("http", "https", "smb", "sftp") if kind == "file" else ("http", "https")
        check_url(url, fenced.hosts, "adapter", schemes)
        for name in env_names(spec):
            check_secret(name, SOURCE, url, fenced)
    elif kind == "file":
        check_file(str(spec.get("location") or ""))
    elif kind == "push":
        # 보내 주는 원천의 비밀은 원천이 relay 에 보낼 때 싣는 것 — 다른 정의가 이미 쓰는
        # 비밀(대상 SP 의 토큰 등)을 받는 열쇠로 쓰면 그 비밀을 아는 누구나 보낼 수 있다.
        for name in env_names(spec):
            if name in fenced.bound:
                raise Forbidden(
                    code("RELAY", 21),
                    f"{name} 은 사용 중인 정의가 쓰는 비밀입니다 — 보내 주는 원천의 비밀은 "
                    "따로 둡니다.",
                )
    # 토큰 주소는 따로 본다 — 원천 주소의 갈래(원격 · 로컬 파일)와 섞으면 한쪽이 빠진다
    # (smb 원천이 로컬 경로 검사에 걸렸다, 2026-11-08 보안 점검).
    issuer = token_url(spec)
    if issuer:
        # client secret 은 토큰 주소로 간다 — 그 주소도 울타리 안, 비밀이 묶인 곳이어야 한다.
        check_url(issuer, fenced.hosts, "adapter.auth.token_url")
        for name in env_names(spec):
            check_secret(name, SOURCE, issuer, fenced)


def check_target(target: dict[str, Any], fenced: Fence) -> None:
    server = str(target.get("server") or "")
    check_url(server, fenced.targets or fenced.hosts, "target.server", target=True)
    check_secret(str(target.get("token_env") or ""), TARGET, server, fenced)


#: 설정이 아니어도 이 프로세스가 스스로 거는 환경 변수 — 프록시 주소에 계정이 들 수 있고
#: (`network.apply` 가 `RELAY_PROXY` 를 옮겨 건다), 인증서 자리는 비밀은 아니어도 원천에 보낼
#: 값이 아니다(2026-11-08 보안 점검).
PROCESS_ENV = {
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
}


def forbidden_env() -> set[str]:
    """이 플랫폼 자신의 설정 이름 — 정의가 비밀 자리로 가리키면 안 된다. 대소문자를 가리지
    않는다(`https_proxy` 도 같다)."""
    return {name.upper() for name in Settings.model_fields} | PROCESS_ENV


def check_env(body: Any) -> None:
    bad = sorted({one for one in env_names(body) if one.upper() in forbidden_env()})
    if bad:
        raise Forbidden(
            code("RELAY", 13),
            f"{', '.join(bad)} 는 이 플랫폼 자신의 설정입니다 — 원천 · 대상의 비밀로 사용하지 "
            "않습니다. 그 시스템의 토큰을 담을 환경 변수를 따로 두세요.",
        )


#: 열 이름 → 자리처럼 **키가 원천의 열 이름**인 칸 — 열 이름이 `ACCESS_TOKEN` 일 수 있다.
_NAMES_ONLY = ("columns", "namespaces")


def check_no_secrets(body: Any, where: str = "") -> None:
    """정의 어디에도 비밀이 없나 — 비밀 칸(`token` · `password` …) 이름, 비밀처럼 보이는 값."""
    if isinstance(body, dict):
        for key, value in body.items():
            here = f"{where}.{key}" if where else str(key)
            named = _SECRET_KEY.search(str(key)) and not where.endswith(_NAMES_ONLY)
            if key in SECRET_FIELDS or named:
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
            f"{where}: 비밀처럼 보이는 값입니다(주소 안의 비밀번호 · 인증 머리글 · 질의의 "
            "키) — 정의에는 환경 변수 이름만 적습니다.",
            status=422,
        )
