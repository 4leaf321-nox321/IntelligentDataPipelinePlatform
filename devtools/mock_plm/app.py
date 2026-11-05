"""샘플 PLM API — 개발 PC 에 PLM 이 없어 relay 를 끝까지 돌려 보려고 만든 **가짜 원천**.

    GET  /api/v1/dev-models?page=&page_size=&updated_since=
                                  개발모델(과제 · 프로젝트 · 시험 주체가 중첩)
    GET  /odata/v4/plm/DevModels?$filter=&$top=&$skip=&$orderby=&$expand=
                                  같은 데이터를 OData v4 로(PascalCase · `@odata.nextLink`)
    POST /_mock/reset             처음 데이터로
    POST /_mock/scenario/change   바꾸기 · 지우기 · 새로 · 파생 끊기
    GET  /_mock/state             지금 데이터

진짜 PLM 의 모양을 흉내 낸 것은 셋이다: **한 행에 여러 타입**(모델 행 안에 과제 · 프로젝트),
**중첩 JSON**, **지운 행을 빼지 않고 `deleted` 로**(SP 연동 지침서 §3.2). 값 · 코드 체계는 모두
지어낸 것이다 — 진짜 열 이름 · 코드는 사내 정보라 저장소에 두지 않는다.

    MOCK_PLM_TOKEN   Bearer 토큰(기본 mock-plm-token)
    MOCK_PLM_DATA    데이터 파일(기본 .work/mock_plm/data.json)
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response

TOKEN = os.environ.get("MOCK_PLM_TOKEN", "mock-plm-token")
DATA = Path(os.environ.get("MOCK_PLM_DATA", ".work/mock_plm/data.json"))

PROJECTS = {
    "P-ALPHA": "알파 프로젝트",
    "P-BETA": "베타 프로젝트",
}
SITES = {
    "S-SEOUL": {"code": "S-SEOUL", "name": "서울 시험소", "region": "KR"},
    "S-AUSTIN": {"code": "S-AUSTIN", "name": "오스틴 시험소", "region": "US"},
}
TASKS = {
    "T-1001": ("P-ALPHA", "알파 1차 개발", "IP", "2026-08-01", "S-SEOUL"),
    "T-1002": ("P-ALPHA", "알파 2차 개발", "PL", "Skip", "S-AUSTIN"),
    "T-2001": ("P-BETA", "베타 선행 개발", "DN", "-", "S-SEOUL"),
}
#: (모델 id, 이름, 종류 코드, 지역, 개정, 기본 모델, 과제)
MODELS = [
    ("M-0001", "ALPHA-100", "01", "KR", "A1", None, "T-1001"),
    ("M-0002", "ALPHA-100 US", "03", "US", "A1", "M-0001", "T-1001"),
    ("M-0003", "ALPHA-100 EU", "03", "EU", "A2", "M-0001", "T-1001"),
    ("M-0004", "ALPHA-200", "01", "KR", "B1", None, "T-1002"),
    ("M-0005", "ALPHA-200 LITE", "03", "KR", "B1", "M-0004", "T-1002"),
    ("M-0006", "ALPHA-200 US", "03", "US", "B2", "M-0004", "T-1002"),
    ("M-0007", "BETA-X", "02", "KR", "X1", None, "T-2001"),
    ("M-0008", "BETA-X PRO", "03", "KR", "X1", "M-0007", "T-2001"),
    ("M-0009", "BETA-X US", "03", "US", "X2", "M-0007", "T-2001"),
    ("M-0010", "BETA-Y", "02", "EU", "Y1", None, "T-2001"),
]
SEEDED_AT = "2026-11-01T00:00:00Z"

app = FastAPI(title="Mock PLM (datarelay devtools)")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _task(code: str) -> dict[str, Any]:
    project, name, status, sra, site = TASKS[code]
    return {
        "code": code,
        "name": name,
        "status_cd": status,
        "sra_date": sra,
        "project": {"code": project, "name": PROJECTS[project]},
        "test_site": copy.deepcopy(SITES[site]),
    }


def _model(row: tuple[Any, ...], at: str) -> dict[str, Any]:
    model_id, name, kind, region, revision, base, task = row
    return {
        "model_id": model_id,
        "model_name": name,
        "kind_cd": kind,
        "region": region,
        "revision": revision,
        "base_model_id": base,
        "task": _task(task),
        "updated_at": at,
        "deleted": False,
    }


def seed() -> list[dict[str, Any]]:
    return [_model(row, SEEDED_AT) for row in MODELS]


def load() -> list[dict[str, Any]]:
    if not DATA.exists():
        save(seed())
    rows: list[dict[str, Any]] = json.loads(DATA.read_text(encoding="utf-8"))
    return rows


def save(rows: list[dict[str, Any]]) -> None:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _authorized(authorization: str | None) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="토큰이 없거나 틀렸습니다")


@app.get("/api/v1/dev-models")
def dev_models(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    updated_since: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """식별자 순으로 고정 정렬 — 쪽 넘김 중에 순서가 바뀌면 행이 빠지거나 겹친다(§3.2)."""
    _authorized(authorization)
    rows = sorted(load(), key=lambda one: one["model_id"])
    if updated_since:
        rows = [one for one in rows if one["updated_at"] > updated_since]
    start = (page - 1) * page_size
    return {
        "items": rows[start : start + page_size],
        "total": len(rows),
        "page": page,
        "page_size": page_size,
    }


@app.post("/_mock/reset")
def reset() -> dict[str, Any]:
    save(seed())
    return {"models": len(MODELS)}


@app.post("/_mock/scenario/change")
def scenario_change() -> dict[str, Any]:
    """하루치 변경 — 값 고침 하나 · 지움 하나 · 새것 하나 · 파생 끊김 하나 · 과제 상태 바뀜."""
    rows = {one["model_id"]: one for one in load()}
    at = _now()
    changed = rows["M-0003"]
    changed.update(model_name="ALPHA-100 EU2", revision="A3", updated_at=at)
    rows["M-0005"].update(deleted=True, updated_at=at)
    rows["M-0009"].update(base_model_id=None, updated_at=at)
    for one in rows.values():
        if one["task"]["code"] == "T-1002" and not one["deleted"]:
            one["task"]["status_cd"] = "IP"
            one["updated_at"] = at
    rows["M-0011"] = _model(("M-0011", "BETA-Z", "02", "KR", "Z1", None, "T-2001"), at)
    save(list(rows.values()))
    return {
        "at": at,
        "changed": ["M-0003", "M-0009", "T-1002"],
        "deleted": ["M-0005"],
        "added": ["M-0011"],
    }


@app.get("/_mock/state")
def state() -> list[dict[str, Any]]:
    return load()


#: OData 쪽의 서버 쪽 쪽 크기 — 클라이언트의 `$top` 이 더 커도 이만큼씩 주고 다음 주소를 준다.
ODATA_SERVER_PAGE = 3
_SINCE = re.compile(r"^UpdatedAt (gt|ge) (\S+)$")


def _odata(one: dict[str, Any]) -> dict[str, Any]:
    task = one["task"]
    return {
        "ModelId": one["model_id"],
        "ModelName": one["model_name"],
        "KindCode": one["kind_cd"],
        "Region": one["region"],
        "Revision": one["revision"],
        "BaseModelId": one["base_model_id"],
        "IsDeleted": one["deleted"],
        "UpdatedAt": one["updated_at"],
        "Task": {
            "Code": task["code"],
            "Name": task["name"],
            "StatusCode": task["status_cd"],
            "SraDate": task["sra_date"],
            "Project": {"Code": task["project"]["code"], "Name": task["project"]["name"]},
            "TestSite": {
                "Code": task["test_site"]["code"],
                "Name": task["test_site"]["name"],
                "Region": task["test_site"]["region"],
            },
        },
    }


#: 서비스 문서(CSDL) — `source_discover` 가 엔티티 셋 · 키 · 탐색 속성을 여기서 읽는다.
METADATA = """<?xml version="1.0" encoding="utf-8"?>
<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">
 <edmx:DataServices>
  <Schema Namespace="Plm" xmlns="http://docs.oasis-open.org/odata/ns/edm">
   <EntityType Name="DevModel">
    <Key><PropertyRef Name="ModelId"/></Key>
    <Property Name="ModelId" Type="Edm.String" Nullable="false"/>
    <Property Name="ModelName" Type="Edm.String"/>
    <Property Name="KindCode" Type="Edm.String"/>
    <Property Name="Region" Type="Edm.String"/>
    <Property Name="Revision" Type="Edm.String"/>
    <Property Name="BaseModelId" Type="Edm.String"/>
    <Property Name="IsDeleted" Type="Edm.Boolean"/>
    <Property Name="UpdatedAt" Type="Edm.DateTimeOffset"/>
    <NavigationProperty Name="Task" Type="Plm.Task"/>
   </EntityType>
   <EntityType Name="Task">
    <Key><PropertyRef Name="Code"/></Key>
    <Property Name="Code" Type="Edm.String" Nullable="false"/>
    <Property Name="Name" Type="Edm.String"/>
    <Property Name="StatusCode" Type="Edm.String"/>
    <Property Name="SraDate" Type="Edm.Date"/>
    <NavigationProperty Name="Project" Type="Plm.Project"/>
    <NavigationProperty Name="TestSite" Type="Plm.TestSite"/>
   </EntityType>
   <EntityType Name="Project">
    <Key><PropertyRef Name="Code"/></Key>
    <Property Name="Code" Type="Edm.String" Nullable="false"/>
    <Property Name="Name" Type="Edm.String"/>
   </EntityType>
   <EntityType Name="TestSite">
    <Key><PropertyRef Name="Code"/></Key>
    <Property Name="Code" Type="Edm.String" Nullable="false"/>
    <Property Name="Name" Type="Edm.String"/>
    <Property Name="Region" Type="Edm.String"/>
   </EntityType>
   <EntityContainer Name="Container">
    <EntitySet Name="DevModels" EntityType="Plm.DevModel"/>
   </EntityContainer>
  </Schema>
 </edmx:DataServices>
</edmx:Edmx>
"""


@app.get("/odata/v4/plm/$metadata")
def odata_metadata(authorization: str | None = Header(default=None)) -> Response:
    _authorized(authorization)
    return Response(content=METADATA, media_type="application/xml")


@app.get("/odata/v4/plm/DevModels")
def odata_dev_models(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """OData v4 의 흉내 — `$filter` 는 `UpdatedAt gt|ge <시각>` 하나만 안다. 서버가 쪽을 잘라
    `@odata.nextLink` 를 준다(진짜 OData 서버처럼)."""
    _authorized(authorization)
    query = request.query_params
    rows = sorted(load(), key=lambda one: one["model_id"])
    wanted = query.get("$filter", "")
    if wanted:
        found = _SINCE.match(wanted.strip())
        if not found:
            raise HTTPException(
                status_code=400, detail=f"이 흉내는 그 $filter 를 모릅니다: {wanted}"
            )
        op, at = found.groups()
        rows = [
            one
            for one in rows
            if (one["updated_at"] > at if op == "gt" else one["updated_at"] >= at)
        ]
    skip = int(query.get("$skip", "0"))
    top = min(int(query.get("$top", "100")), ODATA_SERVER_PAGE)
    page = [_odata(one) for one in rows[skip : skip + top]]
    body: dict[str, Any] = {"@odata.context": "$metadata#DevModels", "value": page}
    if skip + top < len(rows):
        rest = {k: v for k, v in query.items() if k != "$skip"}
        rest["$skip"] = str(skip + top)
        body["@odata.nextLink"] = str(request.url.replace_query_params(**rest))
    return body
