"""파이프라인 정의 — **원천 + 레시피 + 대상 + `source`** 한 장(`relay-pipeline/1`).

    {
      "format": "relay-pipeline/1",
      "slug": "plm-model",
      "source": "plm",
      "adapter": {"kind": "rest", "base_url": "...", "path": "/api/models", "key": "model_id",
                  "columns": {"MODEL_ID": "model_id", "TASK_CODE": "task.code"},
                  "since_param": "updated_since",
                  "auth": {"kind": "bearer", "token_env": "PLM_TOKEN"}},
      "recipe": "plm-models.table.json",
      "target": {"name": "jvhub", "server": "http://…:8040", "token_env": "JVHUB_TOKEN"},
      "intake": {"delete_ratio": 0.05, "delete_rows": 10, "blank_rows": 3}
    }

**비밀은 정의에 적지 않는다** — `token_env` 로 환경 변수의 이름만 적는다. 정의 · 레시피는 열
이름과 코드 체계가 사내 정보라 저장소 밖에 둔다(ADR 0001 §5). `recipe` 는 정의 파일 기준 상대
경로다.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from relay_core.adapters.base import SourceAdapter
from relay_core.adapters.file import FileAdapter, FileSource
from relay_core.adapters.kafka import (
    MECHANISMS,
    KafkaAdapter,
    KafkaSource,
    SchemaRegistry,
    check_kafka,
)
from relay_core.adapters.odata import ODataAdapter, ODataSource
from relay_core.adapters.push import PushAdapter, PushSource
from relay_core.adapters.rest import (
    AUTH_KINDS,
    PAGINGS,
    Auth,
    RestAdapter,
    RestSource,
    SourceError,
    check_shape,
)
from relay_core.adapters.xml import XmlAdapter, XmlSource, check_xml
from relay_core.compute import ComputeError
from relay_core.compute import check as check_computed
from relay_core.intake import IntakeError
from relay_core.intake import Policy as IntakePolicy
from relay_core.intake import check as check_intake

FORMAT = "relay-pipeline/1"
_SLUG = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
#: 원천 이름 — SP 의 `source` 규칙(소문자 · 숫자 · `-` · `_`, 40자).
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class DefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    name: str
    server: str
    token: str = field(repr=False)
    part_mb: int = 8
    """조각 상한(MB) — 보낼 묶음이 이보다 크면 나눠 보낸다(`relay_core.parts`). 대상 SP 가 한
    번에 받는 크기(`job_file_max_bytes`, 기본 100MB)보다 작아야 한다."""


@dataclass(frozen=True)
class Pipeline:
    slug: str
    source: str
    adapter: SourceAdapter
    recipe: Path
    target: Target
    path: Path
    intake: IntakePolicy | None = field(default_factory=IntakePolicy)
    """받기 점검(`relay_core.intake`) — 정의의 `intake`. None 이면 점검하지 않는다."""
    computed: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    """계산 칸(`relay_core.compute`) — 변환할 때 미러 전체에 얹는다."""
    origin: Mapping[str, str] = field(default_factory=dict)
    """대상 SP 에 실을 **원천 기록의 바탕** — `{system, url, trace}`(SP ADR 0087). 엔진은
    웹 · 설정을 모르므로 부르는 쪽(작업 워커 — `app/modules/relay/jobs.py`)이 이 설치의
    이름 · 화면 주소로 채운다. 엔진이 파이프라인 이름 · 실행 번호를 더해 묶음의 `origin`
    으로 보낸다(`runner.origin_for`). 비면 싣지 않는다(CLI)."""


def _secret(spec: dict[str, Any], where: str, env: Mapping[str, str]) -> str:
    """`token_env` 의 값. 정의에 `token` 을 그대로 적었으면 거절한다 — 정의 파일이 퍼지는 날
    비밀도 같이 퍼진다."""
    if "token" in spec:
        raise DefinitionError(f"{where}: 비밀은 정의에 적지 않습니다 — token_env 를 적으세요.")
    name = str(spec.get("token_env") or "")
    if not name:
        return ""
    value = env.get(name, "")
    if not value:
        raise DefinitionError(
            f"{where}: 비밀 {name} 가 비어 있습니다 — 화면 › 중계 › 비밀 에서 값을 넣거나 "
            "서버 설정(.env)에 둡니다."
        )
    return value


#: 정의에 적는 인증의 칸 — 비밀은 `token_env`(환경 변수의 이름)로만.
AUTH_KEYS = {
    "kind",
    "token_env",
    "header",
    "user",
    "param",
    "token_url",
    "scope",
    "cert",
    "cert_password_env",
    "mechanism",
}
#: 비밀의 **이름**을 담는 칸 — 값은 서버의 `.env` 에(`env_names` · 울타리가 이 이름들을 본다).
ENV_KEYS = ("token_env", "cert_password_env")
#: 클라이언트 인증서는 이 모양의 **파일 이름**만 — 경로를 받으면 서버의 아무 파일이나 가리킨다.
_CERT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def check_auth(spec: Any) -> dict[str, Any]:
    """인증의 모양 — 모르는 칸 · 모르는 방식은 거절한다(적은 사람은 적었다고 믿는다)."""
    if spec is None:
        return {}
    if not isinstance(spec, dict):
        raise DefinitionError("adapter.auth 는 JSON 객체여야 합니다.")
    unknown = sorted(set(spec) - AUTH_KEYS - {"token"})
    if unknown:
        raise DefinitionError(f"adapter.auth: 모르는 칸입니다: {', '.join(unknown)}")
    kind = str(spec.get("kind") or "none")
    if kind not in AUTH_KINDS:
        raise DefinitionError(
            f"adapter.auth.kind 는 {' · '.join(AUTH_KINDS)} 중 하나입니다: {kind!r}"
        )
    if kind == "oauth2" and not (spec.get("token_url") and spec.get("user")):
        raise DefinitionError(
            "oauth2 에는 auth.token_url(토큰 주소)과 auth.user(client_id)가 필요합니다 — "
            "client secret 은 auth.token_env 의 환경 변수에."
        )
    if kind == "oauth2" and not str(spec.get("token_url")).startswith(("http://", "https://")):
        raise DefinitionError("auth.token_url 은 http(s) 주소여야 합니다.")
    if kind == "query" and not str(spec.get("param") or "").strip():
        raise DefinitionError("query 에는 auth.param(키를 실을 질의 이름)이 필요합니다.")
    if kind == "ntlm" and not str(spec.get("user") or "").strip():
        raise DefinitionError("ntlm 에는 auth.user(계정 — CORP\\svc_relay)가 필요합니다.")
    if kind == "sasl":
        if str(spec.get("mechanism") or "") not in MECHANISMS:
            raise DefinitionError(
                f"sasl 에는 auth.mechanism({' · '.join(MECHANISMS)})이 필요합니다."
            )
        if not str(spec.get("user") or "").strip() or not spec.get("token_env"):
            raise DefinitionError(
                "sasl 에는 auth.user(계정)와 auth.token_env(비밀번호의 이름)가 필요합니다."
            )
    elif spec.get("mechanism"):
        raise DefinitionError("auth.mechanism 은 sasl(Kafka)에만 적습니다.")
    cert = str(spec.get("cert") or "")
    if cert and (not _CERT_NAME.fullmatch(cert) or ".." in cert):
        raise DefinitionError(
            "auth.cert 는 서버의 인증서 폴더(RELAY_CERT_DIR) 안의 파일 이름만 적습니다 — "
            f"경로가 아니라: {cert!r}"
        )
    return spec


def build_auth(
    spec: dict[str, Any] | None, env: Mapping[str, str], cert_root: Path | None = None
) -> Auth:
    """정의의 `auth` 칸 → 실을 인증(비밀은 `env` 에서). 원천이 아닌 곳(API 지침서)에도 쓴다."""
    return _auth(dict(spec or {}), env, cert_root)


def _auth(spec: dict[str, Any], env: Mapping[str, str], cert_root: Path | None = None) -> Auth:
    check_auth(spec)
    cert = ""
    if spec.get("cert"):
        if cert_root is None:
            raise DefinitionError("클라이언트 인증서를 둘 폴더(RELAY_CERT_DIR)가 없습니다.")
        found = (cert_root / str(spec["cert"])).resolve()
        if not found.is_relative_to(cert_root.resolve()) or not found.is_file():
            raise DefinitionError(
                f"클라이언트 인증서가 인증서 폴더에 없습니다: {spec['cert']} "
                f"({cert_root}) — 운영자가 그 폴더에 PEM(인증서 + 키)을 둡니다."
            )
        cert = str(found)
    password = ""
    if spec.get("cert_password_env"):
        password = env.get(str(spec["cert_password_env"]), "")
        if not password:
            raise DefinitionError(
                f"비밀 {spec['cert_password_env']} 가 비어 있습니다 — 화면 › 중계 › 비밀 에서 "
                "값을 넣거나 서버 설정(.env)에 둡니다."
            )
    return Auth(
        kind=str(spec.get("kind") or "none"),
        token=_secret(spec, "adapter.auth", env),
        header=str(spec.get("header") or "Authorization"),
        user=str(spec.get("user") or ""),
        param=str(spec.get("param") or "api_key"),
        token_url=str(spec.get("token_url") or ""),
        scope=str(spec.get("scope") or ""),
        cert=cert,
        cert_password=password,
        mechanism=str(spec.get("mechanism") or ""),
    )


#: 어댑터 종류 → (원천 설정의 모양, 어댑터, 꼭 있어야 하는 칸).
ADAPTERS: dict[str, tuple[type[Any], type[Any], tuple[str, ...]]] = {
    "rest": (RestSource, RestAdapter, ("base_url", "path", "key", "columns")),
    "odata": (ODataSource, ODataAdapter, ("service_url", "entity_set", "key", "columns")),
    "file": (FileSource, FileAdapter, ("location", "key")),
    "xml": (XmlSource, XmlAdapter, ("url", "rows_xpath", "key", "columns")),
    "push": (PushSource, PushAdapter, ("key", "columns")),
    "kafka": (KafkaSource, KafkaAdapter, ("bootstrap", "topic", "group_id", "key", "columns")),
}

#: 어댑터 종류마다 쓸 수 있는 인증 — HTTP 의 머리글 인증을 Kafka 에 적거나 그 반대면 조용히
#: 안 실린다(적은 사람은 실렸다고 믿는다).
KAFKA_AUTH = ("none", "sasl", "oauth2")


def column_default(kind: str) -> Any:
    """그 어댑터가 `columns` 를 안 적었을 때 — 파일 · DB 는 `"*"`(머리글 그대로)."""
    shape = ADAPTERS.get(kind, (None, None, ()))[0]
    for one in fields(shape) if shape is not None else ():
        if one.name == "columns":
            return one.default if isinstance(one.default, str) else {}
    return {}


#: 정의에 적으면 안 되는 칸 — 비밀은 환경 변수의 **이름**으로만(`token_env`).
SECRET_FIELDS = {"dsn", "password", "token", "secret"}


#: 숫자 칸의 범위 — 한 번 묻는 데 몇 분씩 묶이거나 끝없이 다시 묻지 않게(탐색 · 시험은 화면
#: 요청 안에서 돈다).
BOUNDS: dict[str, tuple[int, int]] = {
    "timeout": (1, 300),
    "retries": (1, 10),
    "page_size": (1, 10_000),
    "max_pages": (1, 100_000),
}


#: 탐색(`partial`)에서는 없어도 되는 칸 — 무엇이 있는지 보려고 부르는 것이라 아직 모른다.
DISCOVER_DEFAULTS: dict[str, Any] = {
    "key": "",
    "columns": {},
    "rows_xpath": "/*/*",
    "entity_set": "",
    "topic": "",
    "group_id": "",
}


def check_adapter(
    spec: dict[str, Any], *, partial: bool = False
) -> tuple[str, dict[str, Any]]:
    """종류와 그 칸들 — **모르는 키는 거절한다**(조용히 무시하면 적은 사람은 적었다고
    믿는다). `partial` 이면 탐색용 — 식별자 · 열처럼 아직 모르는 칸은 비워도 된다."""
    body = dict(spec)
    kind = str(body.pop("kind", "rest"))
    if kind not in ADAPTERS:
        raise DefinitionError(f"모르는 어댑터입니다: {kind} (되는 것: {', '.join(ADAPTERS)})")
    shape, _adapter, need = ADAPTERS[kind]
    leaked = sorted(SECRET_FIELDS & set(body))
    if leaked:
        raise DefinitionError(
            f"adapter: 비밀({', '.join(leaked)})은 정의에 적지 않습니다 — 환경 변수 이름"
            "(`auth.token_env`)을 적으세요."
        )
    known = {one.name for one in fields(shape)} - SECRET_FIELDS
    unknown = sorted(set(body) - known)
    if unknown:
        raise DefinitionError(f"adapter: 모르는 칸입니다: {', '.join(unknown)}")
    for one in need:
        if partial and one in DISCOVER_DEFAULTS:
            body.setdefault(one, DISCOVER_DEFAULTS[one])
        elif not body.get(one):
            raise DefinitionError(f"adapter.{one} 가 필요합니다.")
    for name, (low, high) in BOUNDS.items():
        value = body.get(name, low)
        if isinstance(value, bool) or not (
            isinstance(value, int | float) and low <= value <= high
        ):
            raise DefinitionError(f"adapter.{name} 은 {low} 이상 {high} 이하여야 합니다.")
    check_auth(body.get("auth"))
    auth_kind = str((body.get("auth") or {}).get("kind") or "none")
    if kind == "kafka":
        if auth_kind not in KAFKA_AUTH:
            raise DefinitionError(
                f"Kafka 의 auth.kind 는 {' · '.join(KAFKA_AUTH)} 입니다(클라이언트 인증서는 "
                f"auth.cert 를 함께): {auth_kind!r}"
            )
        problems = check_kafka(body)
        if problems:
            raise DefinitionError(problems[0])
        registry = body.get("schema_registry")
        if registry is not None:
            if not isinstance(registry, dict) or not str(registry.get("url") or "").startswith(
                ("http://", "https://")
            ):
                raise DefinitionError(
                    'adapter.schema_registry 는 {"url": "http(s)://…", "auth": {…}} 입니다.'
                )
            unknown = sorted(set(registry) - {"url", "auth"})
            if unknown:
                raise DefinitionError(
                    f"adapter.schema_registry: 모르는 칸입니다: {', '.join(unknown)}"
                )
            check_auth(registry.get("auth"))
    elif auth_kind == "sasl":
        raise DefinitionError("auth.kind sasl 은 Kafka 원천에만 씁니다.")
    _check_shape(kind, body, partial=partial)
    if kind == "xml":
        try:
            check_xml(
                str(body.get("method") or "GET"),
                str(body.get("soap_action") or ""),
                str(body.get("envelope") or ""),
                str(body.get("content_type") or "text/xml"),
            )
        except SourceError as failure:
            raise DefinitionError(f"adapter: {failure}") from None
    return kind, body


def _check_shape(kind: str, body: dict[str, Any], *, partial: bool) -> None:
    """식별자 · 목록 펴기 · 쪽 넘김 · 요청 방식의 모양."""
    key = body.get("key")
    if isinstance(key, list):
        if not key or not all(isinstance(one, str) and one.strip() for one in key):
            raise DefinitionError("adapter.key 가 목록이면 비지 않은 자리들이어야 합니다.")
    elif key is not None and not isinstance(key, str):
        raise DefinitionError("adapter.key 는 자리 하나(글) 또는 자리들의 목록입니다.")
    explode = str(body.get("explode") or "")
    if explode and not partial:
        try:
            check_shape(key or "", explode)
        except SourceError as failure:
            raise DefinitionError(f"adapter: {failure}") from None
    if kind != "rest":
        return
    paging = str(body.get("paging") or "page")
    if paging not in PAGINGS:
        raise DefinitionError(
            f"adapter.paging 은 {' · '.join(PAGINGS)} 중 하나입니다: {paging!r}"
        )
    method = str(body.get("method") or "GET").upper()
    if method not in ("GET", "POST"):
        raise DefinitionError(f"adapter.method 는 GET · POST 만 됩니다(읽기만): {method!r}")
    if method == "POST" and not isinstance(body.get("body"), dict):
        raise DefinitionError(
            "method POST 에는 body(본문 틀, JSON 객체)가 있어야 합니다 — 조회 본문 없이 "
            "POST 하면 남의 API 를 부르는 길이 된다."
        )
    workers = body.get("detail_workers", 1)
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 16:
        raise DefinitionError("adapter.detail_workers 는 1 이상 16 이하의 정수입니다.")
    interval = body.get("min_interval", 0)
    if (
        isinstance(interval, bool)
        or not isinstance(interval, int | float)
        or not (0 <= interval <= 60)
    ):
        raise DefinitionError("adapter.min_interval 은 0 이상 60 이하(초)입니다.")
    detail = str(body.get("detail_path") or "")
    if detail.startswith(("http://", "https://", "//")):
        raise DefinitionError(
            "adapter.detail_path 는 원천 주소(base_url) 아래의 경로입니다 — 다른 곳을 적지 "
            "않습니다."
        )


def build_adapter(
    spec: dict[str, Any],
    env: Mapping[str, str],
    *,
    partial: bool = False,
    file_roots: Sequence[Path] | None = None,
    cert_root: Path | None = None,
    inbox: Path | None = None,
) -> SourceAdapter:
    """어댑터 설정 → 어댑터 — 비밀(`auth.token_env`)은 `env` 에서 푼다.
    `file_roots` 를 주면 파일 어댑터가 그 폴더 밖을 읽지 않는다(앱이 준다). `cert_root` 는
    클라이언트 인증서(`auth.cert`)를 찾는 폴더."""
    kind, body = check_adapter(spec, partial=partial)
    shape, adapter, _need = ADAPTERS[kind]
    names = {one.name for one in fields(shape)}
    if "auth" in names:
        body["auth"] = _auth(dict(body.pop("auth", None) or {}), env, cert_root)
    if kind == "file":
        return FileAdapter(shape(**body), roots=file_roots)
    if kind == "kafka":
        registry = body.pop("schema_registry", None)
        if registry:
            body["schema_registry"] = SchemaRegistry(
                url=str(registry["url"]),
                auth=_auth(dict(registry.get("auth") or {}), env, cert_root),
            )
        return KafkaAdapter(shape(**body))
    if kind == "push":
        if body["auth"].kind != "bearer" or not body["auth"].token:
            raise DefinitionError(
                "보내 주는 원천(push)은 auth.kind bearer 와 auth.token_env(원천이 보낼 때 "
                "실을 비밀)가 있어야 합니다."
            )
        return PushAdapter(shape(**body), inbox=inbox)
    built: SourceAdapter = adapter(shape(**body))
    return built


def env_names(body: Any) -> list[str]:
    """정의가 푸는 환경 변수의 이름 전부(`token_env` · `cert_password_env`) — 어디에 있든."""
    found: list[str] = []
    if isinstance(body, dict):
        for key, value in body.items():
            if key in ENV_KEYS and isinstance(value, str) and value:
                found.append(value)
            else:
                found.extend(env_names(value))
    elif isinstance(body, list):
        for one in body:
            found.extend(env_names(one))
    return found


#: 조각 상한의 범위(MB) — 대상 SP 의 기본 상한이 100MB, 이중화 nginx 가 512MB 다.
PART_MB = (1, 512)


def part_mb(target: dict[str, Any]) -> int:
    """`target.part_mb` — 없으면 8. 정수 · 범위만."""
    value = target.get("part_mb", 8)
    low, high = PART_MB
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise DefinitionError(
            f"target.part_mb(조각 상한 MB)는 {low} 이상 {high} 이하의 정수여야 합니다 — "
            f"대상 SP 가 한 번에 받는 크기보다 작게: {value!r}"
        )
    return value


def check_body(body: dict[str, Any]) -> tuple[str, str]:
    """정의의 머리 — (slug, source). 비밀 없이 볼 수 있는 것만 본다(초안 저장 · 지도)."""
    if body.get("format") != FORMAT:
        raise DefinitionError(f"format 이 {FORMAT} 이어야 합니다.")
    slug = str(body.get("slug") or "")
    if not _SLUG.fullmatch(slug):
        raise DefinitionError(
            f"slug 는 소문자로 시작하는 소문자 · 숫자 · '-' 40자 이내: {slug!r}"
        )
    source = str(body.get("source") or "")
    if not _SOURCE.fullmatch(source):
        raise DefinitionError(f"source(원천 이름 — plm · mes …)가 맞지 않습니다: {source!r}")
    check_adapter(dict(body.get("adapter") or {}))
    target = dict(body.get("target") or {})
    if "token" in target:
        raise DefinitionError("target: 비밀은 정의에 적지 않습니다 — token_env 를 적으세요.")
    if not target.get("server") or not target.get("token_env"):
        raise DefinitionError("target 의 server · token_env 가 필요합니다.")
    part_mb(target)
    from relay_core.autoapply import AutoApplyError, check

    try:
        check(body.get("auto_apply"))
    except AutoApplyError as failure:
        raise DefinitionError(str(failure)) from failure
    intake(body)
    computed(body)
    return slug, source


def computed(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """정의의 `computed` — 어댑터가 내는 열과 겹치면 안 된다(파일의 `"*"` 면 모른다)."""
    adapter = dict(body.get("adapter") or {})
    columns = adapter.get("columns")
    names = list(columns) if isinstance(columns, dict) else []
    try:
        return check_computed(body.get("computed"), names)
    except ComputeError as failure:
        raise DefinitionError(str(failure)) from failure


def intake(body: dict[str, Any]) -> IntakePolicy | None:
    """정의의 `intake` — 없으면 기본값(켜짐), `false` 면 None."""
    try:
        return check_intake(body.get("intake"))
    except IntakeError as failure:
        raise DefinitionError(str(failure)) from failure


def load_body(
    body: dict[str, Any],
    *,
    base: Path | None,
    env: Mapping[str, str] | None = None,
    recipe: Path | None = None,
    path: Path | None = None,
    file_roots: Sequence[Path] | None = None,
    cert_root: Path | None = None,
    inbox: Path | None = None,
) -> Pipeline:
    """달릴 수 있게 — 비밀(`token_env`)까지 푼다. 레시피는 `recipe` 가 이기고,
    없으면 정의의 `recipe`(그 자리는 `base` 기준). DB 의 정의(판)는 `base` 가 없다 — 켜진
    레시피 판을 준다."""
    env = os.environ if env is None else env
    slug, source = check_body(body)
    if recipe is None:
        if base is None or not body.get("recipe"):
            raise DefinitionError(
                "레시피가 없습니다 — 레시피 판을 작성해 사용하거나 정의에 recipe 를 적으세요."
            )
        recipe = (base / str(body["recipe"])).resolve()
    if not recipe.is_file():
        raise DefinitionError(f"레시피가 없습니다: {recipe}")
    target_spec = dict(body.get("target") or {})
    target = Target(
        name=str(target_spec.get("name") or ""),
        server=str(target_spec.get("server") or "").rstrip("/"),
        token=_secret(target_spec, "target", env),
        part_mb=part_mb(target_spec),
    )
    if not target.token:
        raise DefinitionError("target 의 token_env 가 비었습니다.")
    return Pipeline(
        slug=slug,
        source=source,
        adapter=build_adapter(
            dict(body.get("adapter") or {}),
            env,
            file_roots=file_roots,
            cert_root=cert_root,
            inbox=inbox,
        ),
        recipe=recipe,
        target=target,
        path=path or Path(f"<판:{slug}>"),
        intake=intake(body),
        computed=computed(body),
    )


def load(path: Path, env: Mapping[str, str] | None = None) -> Pipeline:
    """정의 파일에서 — `load_body` 와 같고 레시피 자리는 파일 기준."""
    body = json.loads(path.read_text(encoding="utf-8"))
    return load_body(body, base=path.parent, env=env, path=path)
