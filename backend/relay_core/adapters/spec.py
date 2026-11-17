"""API 지침서 읽기 — 원천이 내놓은 **기계가 읽는 지침서**를 받아, 정의를 고를 때 필요한 것만
추린다(MCP `source_spec`). 사람이 칸 이름 · 쪽 넘김 · 인증 방식을 몰라도 AI 가 이것을 읽고
어댑터 설정을 고른다.

- OpenAPI 3 · Swagger 2(JSON · YAML) — 서버 주소, 인증 방식(→ relay `auth` 의 모양), 목록을
  주는 조회(GET · 검색형 POST)마다 질의 · 목록 자리 · 항목의 칸, 쪽 넘김 · 증분 질의의 후보
- OData `$metadata`(CSDL) — 엔티티 셋마다 식별자 · 속성 · 탐색 속성, 변경 시각 · 삭제 칸의 후보
- WSDL — 서비스 주소 · 작업(SOAP 조회의 `envelope` · `soap_action` 을 지을 재료)
- HTML(사람이 보는 문서 화면) — 그 안의 지침서 파일 주소 후보만

받기만 한다 — 저장 · 전송하지 않는다. 크기 · 개수에 상한이 있다(`MAX_BYTES` · `OPS_MAX`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urljoin

import httpx
from lxml import etree

from relay_core.adapters.rest import Auth, SourceError, scrub

#: 받을 지침서의 상한 — 이보다 크면 멈춘다.
MAX_BYTES = 5 * 1024 * 1024
#: 추려 보일 조회 · 항목 칸의 상한.
OPS_MAX = 60
FIELDS_MAX = 40

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
_PAGE = re.compile(r"^(page|pageno|pagenum|pagenumber|pageindex|p)$", re.I)
_SIZE = re.compile(
    r"^(page_?size|size|limit|per_?page|rows|count|top|\$top|max_?results)$", re.I
)
_OFFSET = re.compile(r"^(offset|skip|start|\$skip|from)$", re.I)
_CURSOR = re.compile(r"^(cursor|after|page_?token|next_?token|continuation(_?token)?)$", re.I)
_SINCE = re.compile(
    r"(since|updated|modified|changed|last_?update|from_?date|start_?date|after)", re.I
)


def fetch(
    url: str, auth: Auth, *, timeout: float = 30.0, client: httpx.Client | None = None
) -> tuple[bytes, str]:
    """지침서를 받는다 — (본문, Content-Type). 인증은 원천과 같은 모양(`Auth`)."""
    sent = {
        "Accept": "application/json, application/yaml, application/xml, */*;q=0.5",
        **auth.headers(),
    }
    secrets = [*auth.secrets(), *auth.query().values()]
    own = client is None
    http = client or httpx.Client(
        timeout=timeout, follow_redirects=False, **auth.client_options()
    )
    try:
        target = (
            httpx.URL(url).copy_merge_params(auth.query()) if auth.query() else httpx.URL(url)
        )
        with http.stream(
            "GET", target, headers=sent, auth=auth.http_auth() or httpx.USE_CLIENT_DEFAULT
        ) as got:
            if got.status_code != 200:
                raise SourceError(
                    scrub(
                        f"지침서를 받지 못했습니다: {got.status_code} ({url})", sent, secrets
                    )
                )
            raw = bytearray()
            for chunk in got.iter_bytes():
                raw += chunk
                if len(raw) > MAX_BYTES:
                    raise SourceError(
                        f"지침서가 {MAX_BYTES // (1024 * 1024)}MB 를 넘습니다 — 받지 않습니다."
                    )
            return bytes(raw), got.headers.get("content-type", "")
    except httpx.HTTPError as failure:
        raise SourceError(scrub(f"지침서에 닿지 않습니다: {failure}", sent, secrets)) from None
    finally:
        if own:
            http.close()


def summarize(raw: bytes, content_type: str, url: str) -> dict[str, Any]:
    """받은 지침서 → 종류와 추린 내용. 모르는 모양이면 무엇인지만 말한다."""
    text = raw.decode("utf-8", errors="replace").lstrip("﻿").strip()
    if text.startswith("<"):
        if re.match(r"(?is)^(<!doctype html|<html)", text):
            return _html(text, url)
        try:
            root = etree.fromstring(raw, parser=_PARSER)
        except etree.XMLSyntaxError as failure:
            return {"kind": "unknown", "note": f"XML 로 읽지 못했습니다: {failure}"}
        tag = etree.QName(root).localname.lower()
        if tag == "edmx":
            return _odata(root)
        if tag in ("definitions", "description"):
            return _wsdl(root)
        return {
            "kind": "xml",
            "root": etree.QName(root).localname,
            "note": "OData · WSDL 이 아닌 XML 입니다.",
        }
    doc = _load(text)
    if isinstance(doc, dict) and ("openapi" in doc or "swagger" in doc):
        return _openapi(doc, url)
    if doc is None:
        return {"kind": "unknown", "note": "JSON · YAML · XML 로 읽지 못했습니다."}
    return {
        "kind": "data",
        "note": "지침서가 아니라 데이터로 보입니다 — 원천 주소면 source_discover 로 보세요.",
        "top": sorted(doc)[:30] if isinstance(doc, dict) else f"목록 {len(doc)}개",
    }


def _load(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        import yaml

        return yaml.safe_load(text)
    except Exception:
        return None


# --- OpenAPI · Swagger ---------------------------------------------------------------------


def _resolve(doc: dict[str, Any], node: Any, depth: int = 0) -> Any:
    """`$ref`(문서 안의 것만)를 풀어 준다 — 깊이 6까지."""
    while isinstance(node, dict) and "$ref" in node and depth < 6:
        ref = str(node["$ref"])
        if not ref.startswith("#/"):
            return {}
        current: Any = doc
        for part in ref[2:].split("/"):
            current = (
                current.get(part.replace("~1", "/").replace("~0", "~"))
                if isinstance(current, dict)
                else None
            )
        node = current or {}
        depth += 1
    return node if isinstance(node, dict) else {}


def _merged(doc: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """`allOf` 를 한 객체로 — 속성만 모은다."""
    schema = _resolve(doc, schema)
    if "allOf" in schema:
        props: dict[str, Any] = {}
        for part in schema["allOf"]:
            props.update(_merged(doc, part).get("properties") or {})
        return {"type": "object", "properties": props}
    return schema


def _fields(doc: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, Any]]:
    schema = _merged(doc, schema)
    out = []
    for name, prop in list((schema.get("properties") or {}).items())[:FIELDS_MAX]:
        prop = _merged(doc, prop)
        kind = prop.get("type") or ("object" if prop.get("properties") else "")
        one: dict[str, Any] = {"name": name, "type": kind}
        if prop.get("format"):
            one["format"] = prop["format"]
        if kind == "array":
            items = _merged(doc, prop.get("items") or {})
            one["items"] = items.get("type") or ("object" if items.get("properties") else "")
        if prop.get("description"):
            one["description"] = str(prop["description"])[:80]
        if prop.get("enum"):
            one["enum"] = list(prop["enum"])[:10]
        out.append(one)
    return out


def _list_of(
    doc: dict[str, Any], schema: dict[str, Any]
) -> tuple[str, list[dict[str, Any]]] | None:
    """응답의 목록 자리와 항목의 칸 — 응답이 목록이면 `""`, 객체 안의 목록이면 그 칸
    (두 겹까지)."""
    schema = _merged(doc, schema)
    if schema.get("type") == "array":
        return "", _fields(doc, schema.get("items") or {})
    for name, prop in (schema.get("properties") or {}).items():
        prop = _merged(doc, prop)
        if prop.get("type") == "array":
            item = _merged(doc, prop.get("items") or {})
            if item.get("properties") or item.get("allOf"):
                return str(name), _fields(doc, item)
        if prop.get("type") == "object" or prop.get("properties"):
            inner = (
                _list_of(doc, {"properties": prop.get("properties") or {}, "type": "object"})
                if name
                else None
            )
            if inner and inner[0] != "":
                return f"{name}.{inner[0]}", inner[1]
    return None


def _auth_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """지침서의 인증 방식 → relay `auth` 의 모양(값은 비운다 — 비밀은 사람이 화면에서)."""
    schemes = (
        (doc.get("components") or {}).get("securitySchemes")
        or doc.get("securityDefinitions")
        or {}
    )
    out = []
    for name, raw in schemes.items():
        one = _resolve(doc, raw)
        kind = str(one.get("type") or "")
        auth: dict[str, Any] = {}
        if kind == "apiKey":
            where = one.get("in")
            auth = (
                {"kind": "query", "param": one.get("name")}
                if where == "query"
                else {"kind": "header", "header": one.get("name")}
            )
        elif kind == "http":
            scheme = str(one.get("scheme") or "").lower()
            auth = (
                {"kind": "bearer"}
                if scheme == "bearer"
                else {"kind": "basic"}
                if scheme == "basic"
                else {}
            )
        elif kind == "basic":
            auth = {"kind": "basic"}
        elif kind == "oauth2":
            flows = one.get("flows") or {}
            flow = flows.get("clientCredentials") or {}
            token = flow.get("tokenUrl") or (
                one.get("tokenUrl") if one.get("flow") == "application" else ""
            )
            scopes = sorted((flow.get("scopes") or one.get("scopes") or {}).keys())
            if token:
                auth = {
                    "kind": "oauth2",
                    "token_url": token,
                    **({"scope": " ".join(scopes)} if scopes else {}),
                }
        out.append(
            {
                "name": name,
                "type": kind,
                "relay_auth": auth or None,
                "note": ""
                if auth
                else "relay 가 지원하지 않는 방식이거나 정보가 모자랍니다 — 원천 담당 확인",
            }
        )
    return out


def _params(doc: dict[str, Any], *lists: Iterable[Any]) -> list[dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for items in lists:
        for raw in items or []:
            one = _resolve(doc, raw)
            if not one.get("name"):
                continue
            schema = _resolve(doc, one.get("schema") or {})
            out[(str(one.get("in")), str(one["name"]))] = {
                "name": one["name"],
                "in": one.get("in"),
                "required": bool(one.get("required")),
                "type": schema.get("type") or one.get("type") or "",
                **(
                    {"description": str(one["description"])[:80]}
                    if one.get("description")
                    else {}
                ),
            }
    return list(out.values())


def _paging(params: list[dict[str, Any]]) -> dict[str, Any]:
    """질의 이름으로 쪽 넘김 · 증분의 후보를 고른다 — relay 정의의 칸 이름 그대로."""
    query = [str(p["name"]) for p in params if p.get("in") == "query"]
    hint: dict[str, Any] = {}
    page = next((n for n in query if _PAGE.match(n)), None)
    size = next((n for n in query if _SIZE.match(n)), None)
    offset = next((n for n in query if _OFFSET.match(n)), None)
    cursor = next((n for n in query if _CURSOR.match(n)), None)
    if cursor:
        hint = {"paging": "cursor", "cursor_param": cursor}
    elif page:
        hint = {"paging": "page", "page_param": page}
    elif offset:
        hint = {"paging": "offset", "offset_param": offset}
    if size and hint:
        hint["size_param"] = size
    since = [n for n in query if _SINCE.search(n) and n not in (offset, cursor)]
    if since:
        hint["since_param"] = since[0]
    return hint


def _openapi(doc: dict[str, Any], url: str) -> dict[str, Any]:
    info = doc.get("info") or {}
    if doc.get("openapi"):
        # OpenAPI 3 — 없으면 「/」, 상대 주소면 **지침서가 있는 자리**에 견준다(명세).
        # FastAPI 처럼 servers 를 안 적는 원천이 흔하다(2026-11-17 MCP 점검 — 샘플 PLM).
        given = [one for one in doc.get("servers") or [] if isinstance(one, dict)]
        given = given or [{"url": "/"}]
        servers = [urljoin(url, str(one.get("url") or "/")).rstrip("/") for one in given]
    else:
        schemes = doc.get("schemes") or ["https"]
        servers = (
            [f"{schemes[0]}://{doc.get('host', '')}{doc.get('basePath', '')}"]
            if doc.get("host")
            else []
        )
    ops = []
    for path, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in ("get", "post"):
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            params = _params(doc, item.get("parameters") or [], op.get("parameters") or [])
            ok = (
                (op.get("responses") or {}).get("200")
                or (op.get("responses") or {}).get(200)
                or {}
            )
            ok = _resolve(doc, ok)
            schema = (
                ok.get("schema")
                or ((ok.get("content") or {}).get("application/json") or {}).get("schema")
                or {}
            )
            listed = _list_of(doc, schema) if schema else None
            if method == "post" and not listed:
                continue  # 쓰는 POST 는 원천이 아니다 — 목록을 주는 검색형만
            entry: dict[str, Any] = {
                "method": method.upper(),
                "path": path,
                "summary": str(op.get("summary") or op.get("operationId") or "")[:100],
                "params": params,
            }
            # 쪽 넘김 · 증분은 질의 이름에서 — 응답 모양을 안 적은 지침서(`dict` 를 내는
            # FastAPI 따위)에도 있다. 칸의 후보는 목록의 항목을 알 때만.
            hints = _paging(params)
            if listed:
                entry["rows_path"] = listed[0]
                entry["fields"] = listed[1]
                hints.update(_field_guess(listed[1]))
            if hints:
                entry["hints"] = hints
            ops.append(entry)
    ops.sort(
        key=lambda one: (0 if "rows_path" in one else 1, one["method"] != "GET", one["path"])
    )
    return {
        "kind": "openapi",
        "version": str(doc.get("openapi") or doc.get("swagger")),
        "title": str(info.get("title") or ""),
        "servers": servers,
        "auth": _auth_of(doc),
        "operations": ops[:OPS_MAX],
        "operations_total": len(ops),
        "note": "목록을 주는 조회(rows_path 가 있는 것)가 앞이다. base_url 은 servers 중 "
        "하나, "
        "path 는 조회의 path. hints 는 이름으로 고른 후보 — source_discover 로 확인한다.",
    }


def _field_guess(fields: list[dict[str, Any]]) -> dict[str, Any]:
    names = [str(f["name"]) for f in fields]
    low = {n: re.sub(r"[^0-9a-z]", "", n.lower()) for n in names}
    key = next((n for n in names if low[n] == "id"), None) or next(
        (n for n in names if low[n].endswith(("id", "code", "key", "no"))), None
    )
    updated = next(
        (
            str(f["name"])
            for f in fields
            if f.get("format") in ("date-time", "date")
            and re.search(r"(updat|modif|chang)", low[str(f["name"])])
        ),
        None,
    )
    deleted = next(
        (
            str(f["name"])
            for f in fields
            if f.get("type") == "boolean" and re.search(r"(delet|remov)", low[str(f["name"])])
        ),
        None,
    )
    out: dict[str, Any] = {}
    if key:
        out["key"] = key
    if updated:
        out["updated_at"] = updated
    if deleted:
        out["deleted"] = deleted
    return out


# --- OData · WSDL · HTML ------------------------------------------------------------------


def _odata(root: Any) -> dict[str, Any]:
    types: dict[str, Any] = {}
    for one in root.xpath("//*[local-name()='EntityType']"):
        props = [
            {"name": p.get("Name"), "type": p.get("Type")}
            for p in one.xpath("./*[local-name()='Property']")
        ]
        modified = [
            p["name"]
            for p in props
            if "DateTime" in str(p["type"])
            and re.search(r"(?i)(modif|updat|chang)", str(p["name"]))
        ]
        deleted = [
            p["name"]
            for p in props
            if str(p["type"]).endswith("Boolean")
            and re.search(r"(?i)(delet|remov)", str(p["name"]))
        ]
        types[str(one.get("Name"))] = {
            "key": [k.get("Name") for k in one.xpath(".//*[local-name()='PropertyRef']")],
            "properties": props[:FIELDS_MAX],
            "navigation": [
                n.get("Name") for n in one.xpath("./*[local-name()='NavigationProperty']")
            ],
            "hints": {
                **({"modified_field": modified[0]} if modified else {}),
                **({"deleted": deleted[0]} if deleted else {}),
            },
        }
    sets = []
    for one in root.xpath("//*[local-name()='EntitySet']")[:OPS_MAX]:
        type_name = str(one.get("EntityType") or "").rsplit(".", 1)[-1]
        sets.append(
            {
                "entity_set": one.get("Name"),
                "entity_type": type_name,
                **types.get(type_name, {}),
            }
        )
    return {
        "kind": "odata",
        "version": str(root.get("Version") or ""),
        "entity_sets": sets,
        "note": "어댑터는 odata — service_url 은 $metadata 앞의 주소, entity_set 은 위의 "
        "이름. "
        "탐색 속성은 expand 로 함께 받는다.",
    }


def _wsdl(root: Any) -> dict[str, Any]:
    address = [
        str(a.get("location"))
        for a in root.xpath("//*[local-name()='address']")
        if a.get("location")
    ]
    actions = {
        str(op.getparent().get("name")): str(op.get("soapAction") or "")
        for op in root.xpath(
            "//*[local-name()='binding']/*[local-name()='operation']/*[local-name()='operation']"
        )
        if op.getparent() is not None
    }
    ops = []
    for op in root.xpath(
        "//*[local-name()='portType' or local-name()='interface']/*[local-name()='operation']"
    )[:OPS_MAX]:
        name = str(op.get("name"))
        given = op.xpath("./*[local-name()='input']")
        ops.append(
            {
                "operation": name,
                "input": str(given[0].get("message") or given[0].get("element") or "")
                if given
                else "",
                "soap_action": actions.get(name, ""),
            }
        )
    return {
        "kind": "wsdl",
        "service": [str(s.get("name")) for s in root.xpath("//*[local-name()='service']")],
        "addresses": address,
        "operations": ops,
        "note": "어댑터는 xml — url 은 addresses 중 하나, envelope 에 조회 작업의 SOAP "
        "본문({since} 자리), "
        "soap_action 은 위의 값. 행 자리(rows_xpath)는 응답을 source_discover 로 보고 정한다.",
    }


def _html(text: str, url: str) -> dict[str, Any]:
    found = []
    for href in re.findall(r"""(?:href|src|url)\s*[=:]\s*["']([^"'#]+)["']""", text, re.I):
        if re.search(
            r"(?i)(openapi|swagger|api-docs|\.ya?ml$|\.json$|\$metadata|\?wsdl)", href
        ):
            full = urljoin(url, href)
            if full not in found:
                found.append(full)
    return {
        "kind": "html",
        "candidates": found[:20],
        "note": "사람이 보는 문서 화면입니다 — candidates 의 지침서 파일 주소를 다시 "
        "source_spec 으로 "
        "읽으세요. 없으면 원천 담당에게 OpenAPI(JSON · YAML) 주소나 파일을 받으세요.",
    }
