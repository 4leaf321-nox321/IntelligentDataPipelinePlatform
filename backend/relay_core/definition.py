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
      "target": {"name": "jvhub", "server": "http://…:8040", "token_env": "JVHUB_TOKEN"}
    }

**비밀은 정의에 적지 않는다** — `token_env` 로 환경 변수의 이름만 적는다. 정의 · 레시피는 열
이름과 코드 체계가 사내 정보라 저장소 밖에 둔다(ADR 0001 §5). `recipe` 는 정의 파일 기준 상대
경로다.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from relay_core.adapters.odata import ODataAdapter, ODataSource
from relay_core.adapters.rest import Auth, RestAdapter, RestSource

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
    token: str


@dataclass(frozen=True)
class Pipeline:
    slug: str
    source: str
    adapter: RestAdapter | ODataAdapter
    recipe: Path
    target: Target
    path: Path


def _secret(spec: dict[str, Any], where: str, env: Mapping[str, str]) -> str:
    """`token_env` 의 값. 정의에 `token` 을 그대로 적었으면 거절한다 — 정의 파일이 퍼지는 날
    비밀도 같이 퍼진다."""
    if "token" in spec:
        raise DefinitionError(f"{where}: 비밀은 정의에 적지 않습니다 — token_env 를 쓰세요.")
    name = str(spec.get("token_env") or "")
    if not name:
        return ""
    value = env.get(name, "")
    if not value:
        raise DefinitionError(f"{where}: 환경 변수 {name} 가 비어 있습니다.")
    return value


def _auth(spec: dict[str, Any], env: Mapping[str, str]) -> Auth:
    return Auth(
        kind=str(spec.get("kind") or "none"),
        token=_secret(spec, "adapter.auth", env),
        header=str(spec.get("header") or "Authorization"),
        user=str(spec.get("user") or ""),
    )


#: 어댑터 종류 → (원천 설정의 모양, 어댑터, 꼭 있어야 하는 칸).
ADAPTERS: dict[str, tuple[type[Any], type[Any], tuple[str, ...]]] = {
    "rest": (RestSource, RestAdapter, ("base_url", "path", "key", "columns")),
    "odata": (ODataSource, ODataAdapter, ("service_url", "entity_set", "key", "columns")),
}


def check_adapter(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """종류와 그 칸들 — **모르는 키는 거절한다**(조용히 무시하면 적은 사람은 적었다고
    믿는다)."""
    body = dict(spec)
    kind = str(body.pop("kind", "rest"))
    if kind not in ADAPTERS:
        raise DefinitionError(f"모르는 어댑터입니다: {kind} (되는 것: {', '.join(ADAPTERS)})")
    shape, _adapter, need = ADAPTERS[kind]
    known = {one.name for one in fields(shape)}
    unknown = sorted(set(body) - known)
    if unknown:
        raise DefinitionError(f"adapter: 모르는 칸입니다: {', '.join(unknown)}")
    for one in need:
        if not body.get(one):
            raise DefinitionError(f"adapter.{one} 가 필요합니다.")
    return kind, body


def _adapter(spec: dict[str, Any], env: Mapping[str, str]) -> RestAdapter | ODataAdapter:
    kind, body = check_adapter(spec)
    shape, adapter, _need = ADAPTERS[kind]
    auth = _auth(dict(body.pop("auth", None) or {}), env)
    built: RestAdapter | ODataAdapter = adapter(shape(**body, auth=auth))
    return built


def load(path: Path, env: Mapping[str, str] | None = None) -> Pipeline:
    """달릴 수 있게 읽는다 — 비밀(`token_env`)까지 푼다. `env` 를 안 주면 프로세스의 환경
    변수."""
    env = os.environ if env is None else env
    body = json.loads(path.read_text(encoding="utf-8"))
    if body.get("format") != FORMAT:
        raise DefinitionError(f"{path}: format 이 {FORMAT} 이어야 합니다.")
    slug = str(body.get("slug") or "")
    if not _SLUG.match(slug):
        raise DefinitionError(
            f"slug 는 소문자로 시작하는 소문자 · 숫자 · '-' 40자 이내: {slug!r}"
        )
    source = str(body.get("source") or "")
    if not _SOURCE.match(source):
        raise DefinitionError(f"source(원천 이름 — plm · mes …)가 맞지 않습니다: {source!r}")
    recipe = (path.parent / str(body.get("recipe") or "")).resolve()
    if not recipe.is_file():
        raise DefinitionError(f"레시피가 없습니다: {recipe}")
    target_spec = dict(body.get("target") or {})
    target = Target(
        name=str(target_spec.get("name") or ""),
        server=str(target_spec.get("server") or "").rstrip("/"),
        token=_secret(target_spec, "target", env),
    )
    if not target.server or not target.token:
        raise DefinitionError("target 의 server · token_env 가 필요합니다.")
    return Pipeline(
        slug=slug,
        source=source,
        adapter=_adapter(dict(body.get("adapter") or {}), env),
        recipe=recipe,
        target=target,
        path=path,
    )
