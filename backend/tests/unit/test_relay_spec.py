"""API 지침서 읽기 — OpenAPI 3(JSON) · Swagger 2(YAML) · OData `$metadata` · WSDL · 문서 화면을
받아, 어댑터 설정을 고를 재료만 추린다. 인증 방식은 relay `auth` 의 모양으로(값은 비운다).
"""

from __future__ import annotations

import json

import httpx

from relay_core.adapters.rest import Auth
from relay_core.adapters.spec import fetch, summarize

OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "MES Lot API", "version": "2.1"},
    "servers": [{"url": "https://mes.corp.local/api/v2"}],
    "components": {
        "securitySchemes": {
            "key": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
            "cc": {
                "type": "oauth2",
                "flows": {
                    "clientCredentials": {
                        "tokenUrl": "https://idp/token",
                        "scopes": {"lot.read": ""},
                    }
                },
            },
        },
        "schemas": {
            "Lot": {
                "type": "object",
                "properties": {
                    "lotId": {"type": "string"},
                    "qty": {"type": "integer"},
                    "updatedAt": {"type": "string", "format": "date-time"},
                    "isDeleted": {"type": "boolean"},
                },
            },
            "LotPage": {
                "type": "object",
                "properties": {
                    "data": {"type": "array", "items": {"$ref": "#/components/schemas/Lot"}},
                    "total": {"type": "integer"},
                },
            },
        },
    },
    "paths": {
        "/lots": {
            "get": {
                "summary": "로트 목록",
                "parameters": [
                    {"name": "pageNo", "in": "query", "schema": {"type": "integer"}},
                    {"name": "pageSize", "in": "query", "schema": {"type": "integer"}},
                    {"name": "modifiedSince", "in": "query", "schema": {"type": "string"}},
                ],
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/LotPage"}
                            }
                        }
                    }
                },
            },
            "post": {"summary": "로트 생성", "responses": {"201": {}}},
        },
        "/lots/{lotId}": {
            "get": {
                "parameters": [{"name": "lotId", "in": "path", "required": True}],
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Lot"}
                            }
                        }
                    }
                },
            }
        },
    },
}


def test_OpenAPI_는_목록_조회_인증_쪽_넘김_증분을_추린다() -> None:
    got = summarize(
        json.dumps(OPENAPI).encode(), "application/json", "https://mes/openapi.json"
    )
    assert got["kind"] == "openapi" and got["servers"] == ["https://mes.corp.local/api/v2"]
    first = got["operations"][0]
    assert (first["method"], first["path"], first["rows_path"]) == ("GET", "/lots", "data")
    assert [f["name"] for f in first["fields"]] == ["lotId", "qty", "updatedAt", "isDeleted"]
    assert first["hints"] == {
        "paging": "page",
        "page_param": "pageNo",
        "size_param": "pageSize",
        "since_param": "modifiedSince",
        "key": "lotId",
        "updated_at": "updatedAt",
        "deleted": "isDeleted",
    }
    assert all(op["method"] == "GET" for op in got["operations"])  # 쓰는 POST 는 뺀다
    auth = {one["name"]: one["relay_auth"] for one in got["auth"]}
    assert auth["key"] == {"kind": "header", "header": "X-API-Key"}
    assert auth["cc"] == {
        "kind": "oauth2",
        "token_url": "https://idp/token",
        "scope": "lot.read",
    }


def test_응답_모양을_안_적어도_질의로_쪽_넘김_증분을_고르고_servers_는_지침서_자리로() -> None:
    """FastAPI 의 `dict[str, Any]` 응답 — 항목의 칸은 모르지만 질의 이름은 있다
    (2026-11-17 점검)."""
    doc = {
        "openapi": "3.1.0",
        "info": {"title": "Mock PLM"},
        "paths": {
            "/api/v1/dev-models": {
                "get": {
                    "parameters": [
                        {"name": "page", "in": "query"},
                        {"name": "page_size", "in": "query"},
                        {"name": "updated_since", "in": "query"},
                    ],
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "additionalProperties": True}
                                }
                            }
                        }
                    },
                }
            }
        },
    }
    got = summarize(
        json.dumps(doc).encode(), "application/json", "http://plm:8096/openapi.json"
    )
    assert got["servers"] == ["http://plm:8096"]
    (op,) = got["operations"]
    assert "rows_path" not in op
    assert op["hints"] == {
        "paging": "page",
        "page_param": "page",
        "size_param": "page_size",
        "since_param": "updated_since",
    }
    relative = {**doc, "servers": [{"url": "/api/v2"}]}
    got = summarize(
        json.dumps(relative).encode(), "application/json", "https://mes/docs/x.json"
    )
    assert got["servers"] == ["https://mes/api/v2"]


SWAGGER_YAML = """
swagger: "2.0"
info: {title: PLM, version: "1"}
host: plm.corp.local
basePath: /rest
schemes: [https]
securityDefinitions:
  q: {type: apiKey, in: query, name: api_key}
paths:
  /models:
    get:
      parameters:
        - {name: offset, in: query, type: integer}
        - {name: limit, in: query, type: integer}
      responses:
        "200":
          schema:
            type: array
            items:
              type: object
              properties:
                id: {type: string}
                name: {type: string}
"""


def test_Swagger_YAML_도_읽는다() -> None:
    got = summarize(SWAGGER_YAML.encode(), "application/yaml", "https://plm/swagger.yaml")
    assert got["kind"] == "openapi" and got["servers"] == ["https://plm.corp.local/rest"]
    (op,) = got["operations"]
    assert op["rows_path"] == "" and op["hints"]["paging"] == "offset"
    assert op["hints"]["size_param"] == "limit" and op["hints"]["key"] == "id"
    assert got["auth"][0]["relay_auth"] == {"kind": "query", "param": "api_key"}


def test_OData_WSDL_문서_화면() -> None:
    metadata = b"""<?xml version="1.0"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
 <edmx:DataServices><Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="PLM">
  <EntityType Name="DevModel"><Key><PropertyRef Name="ModelId"/></Key>
   <Property Name="ModelId" Type="Edm.String"/>
   <Property Name="UpdatedAt" Type="Edm.DateTimeOffset"/>
   <Property Name="IsDeleted" Type="Edm.Boolean"/>
   <NavigationProperty Name="Task" Type="PLM.Task"/>
  </EntityType>
  <EntityContainer Name="C">
   <EntitySet Name="DevModels" EntityType="PLM.DevModel"/>
  </EntityContainer>
 </Schema></edmx:DataServices></edmx:Edmx>"""
    od = summarize(metadata, "application/xml", "https://plm/odata/$metadata")
    (one,) = od["entity_sets"]
    assert (od["kind"], one["entity_set"], one["key"]) == ("odata", "DevModels", ["ModelId"])
    assert one["hints"] == {"modified_field": "UpdatedAt", "deleted": "IsDeleted"}
    assert one["navigation"] == ["Task"]

    wsdl = b"""<definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
      xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/" name="LotService">
      <portType name="P">
        <operation name="GetLots"><input message="tns:GetLotsIn"/></operation>
      </portType>
      <binding name="B" type="tns:P">
        <operation name="GetLots"><soap:operation soapAction="urn:GetLots"/></operation>
      </binding>
      <service name="LotService">
        <port name="p"><soap:address location="https://mes/soap"/></port>
      </service>
    </definitions>"""
    ws = summarize(wsdl, "text/xml", "https://mes/soap?wsdl")
    assert ws["kind"] == "wsdl" and ws["addresses"] == ["https://mes/soap"]
    assert ws["operations"] == [
        {"operation": "GetLots", "input": "tns:GetLotsIn", "soap_action": "urn:GetLots"}
    ]

    page = (
        b'<!doctype html><html><a href="/v3/api-docs.yaml">spec</a>'
        b'<script>url: "swagger.json"</script></html>'
    )
    html = summarize(page, "text/html", "https://mes/docs/")
    assert html["kind"] == "html"
    assert html["candidates"] == [
        "https://mes/v3/api-docs.yaml",
        "https://mes/docs/swagger.json",
    ]

    # 외부 개체(XXE)는 풀지 않는다.
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
    assert "root:" not in json.dumps(summarize(evil, "text/xml", "https://x"))


def test_받기는_인증을_싣고_크기를_잘라_멈추며_비밀을_글에서_지운다() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * (6 * 1024 * 1024))
        if request.url.path == "/no":
            return httpx.Response(403, text="denied")
        return httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    auth = Auth(kind="query", param="api_key", token="s3cret-key")
    raw, kind = fetch("https://mes/openapi.json", auth, client=client)
    assert raw == b"{}" and kind == "application/json"
    assert seen[0].url.params["api_key"] == "s3cret-key"
    for path, said in (("/big", "넘습니다"), ("/no", "403")):
        try:
            fetch(f"https://mes{path}", auth, client=client)
        except Exception as failure:
            assert said in str(failure) and "s3cret-key" not in str(failure)
        else:
            raise AssertionError("멈춰야 한다")
