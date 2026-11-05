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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from relay_core.adapters.base import SourceAdapter
from relay_core.adapters.file import FileAdapter, FileSource
from relay_core.adapters.odata import ODataAdapter, ODataSource
from relay_core.adapters.rest import Auth, RestAdapter, RestSource, SourceError
from relay_core.adapters.xml import XmlAdapter, XmlSource, check_xml

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


@dataclass(frozen=True)
class Pipeline:
    slug: str
    source: str
    adapter: SourceAdapter
    recipe: Path
    target: Target
    path: Path


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
    "file": (FileSource, FileAdapter, ("location", "key")),
    "xml": (XmlSource, XmlAdapter, ("url", "rows_xpath", "key", "columns")),
}


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


def build_adapter(
    spec: dict[str, Any],
    env: Mapping[str, str],
    *,
    partial: bool = False,
    file_roots: Sequence[Path] | None = None,
) -> SourceAdapter:
    """어댑터 설정 → 어댑터 — 비밀(`auth.token_env`)은 `env` 에서 푼다.
    `file_roots` 를 주면 파일 어댑터가 그 폴더 밖을 읽지 않는다(앱이 준다)."""
    kind, body = check_adapter(spec, partial=partial)
    shape, adapter, _need = ADAPTERS[kind]
    names = {one.name for one in fields(shape)}
    if "auth" in names:
        body["auth"] = _auth(dict(body.pop("auth", None) or {}), env)
    if kind == "file":
        return FileAdapter(shape(**body), roots=file_roots)
    built: SourceAdapter = adapter(shape(**body))
    return built


def env_names(body: Any) -> list[str]:
    """정의가 푸는 환경 변수의 이름 전부(`token_env`) — 어디에 있든."""
    found: list[str] = []
    if isinstance(body, dict):
        for key, value in body.items():
            if key == "token_env" and isinstance(value, str) and value:
                found.append(value)
            else:
                found.extend(env_names(value))
    elif isinstance(body, list):
        for one in body:
            found.extend(env_names(one))
    return found


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
    from relay_core.autoapply import AutoApplyError, check

    try:
        check(body.get("auto_apply"))
    except AutoApplyError as failure:
        raise DefinitionError(str(failure)) from failure
    return slug, source


def load_body(
    body: dict[str, Any],
    *,
    base: Path | None,
    env: Mapping[str, str] | None = None,
    recipe: Path | None = None,
    path: Path | None = None,
    file_roots: Sequence[Path] | None = None,
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
    )
    if not target.token:
        raise DefinitionError("target 의 token_env 가 비었습니다.")
    return Pipeline(
        slug=slug,
        source=source,
        adapter=build_adapter(dict(body.get("adapter") or {}), env, file_roots=file_roots),
        recipe=recipe,
        target=target,
        path=path or Path(f"<판:{slug}>"),
    )


def load(path: Path, env: Mapping[str, str] | None = None) -> Pipeline:
    """정의 파일에서 — `load_body` 와 같고 레시피 자리는 파일 기준."""
    body = json.loads(path.read_text(encoding="utf-8"))
    return load_body(body, base=path.parent, env=env, path=path)
