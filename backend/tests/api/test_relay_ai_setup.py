"""AI 가 값을 고르고 사람은 비밀만 넣는 길 — 한 번에 초안 · API 지침서 읽기 · 비밀 입력.

- 한 번에 초안: 비밀이 비었으면 아무것도 저장하지 않고 이름을 「요청됨」 으로 올린다. 넣으면
  탐색 → 정의 판 · 레시피 판 초안(칸 이름으로 잇기, 참조 칸은 남김) → 시험. 사용은 하지 않는다.
- 비밀: 값은 다시 보이지 않고, 넣기 · 지우기는 사람만(토큰 거절), 이름은 정의가 쓰는 것만 ·
  플랫폼 설정 이름은 거절. 화면 값이 서버 설정보다 먼저 쓰인다.
- 지침서: 원천과 같은 울타리.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from tests.api.conftest import Signed
from tests.api.test_relay import NOWHERE, _definition, _slug
from tests.api.test_relay_definitions import _code, drop  # noqa: F401 — 준비물
from tests.api.test_relay_recipes import _pat

SCHEMA = {
    "types": [
        {
            "slug": "mes_model",
            "managed_by": "mes",
            "properties": [
                {"key": "model_name", "label": "모델명", "data_type": "text"},
                {"key": "kind", "label": "종류", "data_type": "text"},
                {"key": "base", "label": "기반 모델", "data_type": "object_ref"},
                {"key": "made_on", "label": "생산일", "data_type": "date"},
            ],
        }
    ]
}


@pytest.fixture
def target_sp(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.relay import services

    def probe(server: str, token: str, path: str) -> Any:
        if path == "/api/health":
            return 200, {"status": "ok", "slug": "mfghub", "version": "v0.4.80"}
        if path == "/api/ontology/schema":
            return 200, SCHEMA
        return 404, None

    monkeypatch.setattr(services, "probe_http", probe)


def _spec(drop: Path, **over: Any) -> dict[str, Any]:  # noqa: F811
    return {
        "adapter": {"kind": "file", "location": str(drop / "models.csv")},
        "source": "mes",
        "type_slug": "mes_model",
        "target": {"name": "제조 데이터허브", "server": NOWHERE, "token_env": "MFG_HUB_TOKEN"},
        **over,
    }


def test_한_번에_초안은_비밀이_비면_저장하지_않고_넣으면_끝까지(
    client: TestClient,
    admin: Signed,
    drop: Path,  # noqa: F811
    target_sp: None,
    db: Session,
) -> None:
    slug = _slug()
    mcp = _pat(client, admin)
    first = client.post(f"/api/relay/pipelines/{slug}/scaffold", json=_spec(drop), headers=mcp)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "needs_secrets"
    assert first.json()["secrets_needed"] == ["MFG_HUB_TOKEN"]
    assert (
        client.get(f"/api/relay/pipelines/{slug}/definitions", headers=admin.headers).json()
        == []
    )

    listed = {one["name"]: one for one in client.get("/api/relay/secrets", headers=mcp).json()}
    assert listed["MFG_HUB_TOKEN"]["status"] == "missing"
    assert listed["MFG_HUB_TOKEN"]["uses"][0]["state"] == "요청됨(정의 전)"

    # 사람이 화면에서 넣는다 — 토큰(MCP)으로는 못 넣는다.
    refused = client.put(
        "/api/relay/secrets/MFG_HUB_TOKEN", json={"value": "hub-t0k"}, headers=mcp
    )
    assert refused.status_code == 403 and _code(refused).endswith("RELAY-0036")
    put = client.put(
        "/api/relay/secrets/MFG_HUB_TOKEN", json={"value": "hub-t0k"}, headers=admin.headers
    )
    assert put.status_code == 200, put.text

    done = client.post(f"/api/relay/pipelines/{slug}/scaffold", json=_spec(drop), headers=mcp)
    assert done.status_code == 200, done.text
    got = done.json()
    assert got["status"] == "drafted", got
    assert got["definition"]["chosen"]["key"]["value"] == "MODEL_ID"  # 값이 모두 다른 칸
    assert got["definition"]["body"]["adapter"]["key"] == "MODEL_ID"
    recipe = got["recipe"]
    assert {m["property"]: m["column"] for m in recipe["matched"]} == {
        "model_name": "MODEL_NAME",
        "kind": "KIND",
    }
    assert [one["property"] for one in recipe["references"]] == ["base"]  # 참조 칸은 남긴다
    assert [one["property"] for one in recipe["unmatched"]] == ["made_on"]
    trial = got["trial"]
    assert trial["source"]["ok"] and trial["source"]["rows"] == 3
    assert trial["target"]["token"] == "정상" and trial["target"]["types_missing"] == []
    assert any("「사용」" in one for one in got["next"])

    versions = client.get(
        f"/api/relay/pipelines/{slug}/definitions", headers=admin.headers
    ).json()
    assert [(one["version"], one["status"], one["client"]) for one in versions] == [
        (1, "draft", "mcp")
    ]
    listed = {one["name"]: one for one in client.get("/api/relay/secrets", headers=mcp).json()}
    assert listed["MFG_HUB_TOKEN"]["status"] == "screen"
    assert listed["MFG_HUB_TOKEN"]["updated_by"] and "value" not in listed["MFG_HUB_TOKEN"]
    assert "hub-t0k" not in json.dumps(listed)


def test_대상에_타입이_없으면_SP_타입_정의_초안을_짓는다(
    client: TestClient,
    admin: Signed,
    drop: Path,  # noqa: F811
    target_sp: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MFG_HUB_TOKEN", "x")
    slug = _slug()
    got = client.post(
        f"/api/relay/pipelines/{slug}/scaffold",
        json=_spec(drop, type_slug="mes_lot", key="MODEL_ID"),
        headers=_pat(client, admin),
    ).json()
    assert got["status"] == "type_missing"
    assert got["ontology"]["types"][0]["slug"] == "mes_lot"
    assert got["trial"]["target"]["types_missing"] == ["mes_lot"]
    assert any("type_plan" in one for one in got["next"])  # 대상에 타입 계획을 올린다


def test_비밀은_정의가_쓰는_이름만_사람만_값은_안_보이고_화면이_먼저(
    client: TestClient,
    admin: Signed,
    registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.relay import services

    slug = _slug()
    _definition(registry, slug)  # 대상 토큰 RELAY_T_TOKEN
    h = admin.headers
    unused = client.put("/api/relay/secrets/NOT_USED_TOKEN", json={"value": "v"}, headers=h)
    assert unused.status_code == 422 and "쓰는 정의가 없습니다" in unused.text
    platform = client.put("/api/relay/secrets/DATABASE_URL", json={"value": "v"}, headers=h)
    assert platform.status_code in (403, 422)
    asked = client.post(
        "/api/relay/secrets/requests",
        json={"pipeline": slug, "names": ["JWT_SECRET"]},
        headers=_pat(client, admin),
    )
    assert asked.status_code == 403 and _code(asked).endswith("RELAY-0013")
    bad = client.put("/api/relay/secrets/lower_case", json={"value": "v"}, headers=h)
    assert bad.status_code == 422
    multi = client.put("/api/relay/secrets/RELAY_T_TOKEN", json={"value": "a\nb"}, headers=h)
    assert multi.status_code == 422

    weird = "t0k'en\"${HOME}\\ #x"
    assert (
        client.put(
            "/api/relay/secrets/RELAY_T_TOKEN", json={"value": weird}, headers=h
        ).status_code
        == 200
    )
    assert (
        services.secrets_env()["RELAY_T_TOKEN"] == weird
    )  # 글자 그대로(풀거나 자르지 않는다)
    store = get_settings().relay_work_dir.parent / "secrets.json"
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    monkeypatch.setenv("RELAY_T_TOKEN", "from-process")
    assert services.secrets_env()["RELAY_T_TOKEN"] == "from-process"  # 프로세스 환경이 먼저
    listed = {one["name"]: one for one in client.get("/api/relay/secrets", headers=h).json()}
    assert listed["RELAY_T_TOKEN"]["status"] == "server"
    monkeypatch.delenv("RELAY_T_TOKEN")

    mcp = _pat(client, admin)
    assert client.delete("/api/relay/secrets/RELAY_T_TOKEN", headers=mcp).status_code == 403
    assert client.delete("/api/relay/secrets/RELAY_T_TOKEN", headers=h).status_code == 200
    assert "RELAY_T_TOKEN" not in services.secrets_env()


def test_지침서는_원천과_같은_울타리(
    client: TestClient, admin: Signed, registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp = _pat(client, admin)
    outside = client.post(
        "/api/relay/source-spec", json={"url": "http://10.9.9.9/openapi.json"}, headers=mcp
    )
    assert outside.status_code == 403 and _code(outside).endswith("RELAY-0010")
    _definition(registry, _slug())
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    stolen = client.post(
        "/api/relay/source-spec",
        json={
            "url": "http://127.0.0.1:8/openapi.json",
            "auth": {"kind": "bearer", "token_env": "RELAY_T_TOKEN"},
        },
        headers=mcp,
    )
    assert stolen.status_code == 403 and _code(stolen).endswith("RELAY-0021")
    nowhere = client.post(
        "/api/relay/source-spec", json={"url": "http://127.0.0.1:9/openapi.json"}, headers=mcp
    )
    assert nowhere.status_code == 200 and nowhere.json()["ok"] is False


SPLIT_SCHEMA = {
    "types": [
        {
            "slug": "mes_lot",
            "managed_by": "mes",
            "properties": [
                {"key": "qty", "label": "수량", "data_type": "number"},
                {
                    "key": "order",
                    "label": "작업지시",
                    "data_type": "object_ref",
                    "ref_type_slug": "mes_order",
                    "required": True,
                },
            ],
        },
        {
            "slug": "mes_order",
            "managed_by": "mes",
            "properties": [
                {
                    "key": "line",
                    "label": "라인",
                    "data_type": "object_ref",
                    "ref_type_slug": "mes_line",
                },
            ],
        },
        {"slug": "mes_line", "managed_by": "mes", "properties": []},
    ],
    "relation_types": [
        {
            "slug": "mes_split_from",
            "label": "분할",
            "src_type_slugs": ["mes_lot"],
            "dst_type_slugs": ["mes_lot"],
            "managed_by": "mes",
        }
    ],
}


def test_한_번에_초안은_한_행을_정의를_따라_여러_타입과_선으로_나눈다(
    client: TestClient,
    admin: Signed,
    drop: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.relay import services

    def probe(server: str, token: str, path: str) -> Any:
        if path == "/api/health":
            return 200, {"status": "ok", "slug": "mfghub", "version": "v0.4.80"}
        if path == "/api/ontology/schema":
            return 200, SPLIT_SCHEMA
        return 404, None

    monkeypatch.setattr(services, "probe_http", probe)
    monkeypatch.setenv("MFG_HUB_TOKEN", "x")
    (drop / "lots.csv").write_text(
        "LOT_ID,QTY,PARENT_LOT_ID,ORDER_NO,ORDER_NAME,LINE_CODE,LINE_NAME\n"
        "L1,3,,W1,지시 1,A,라인 A\n"
        "L2,4,L1,W1,지시 1,A,라인 A\n"
        "L3,5,,W2,지시 2,B,라인 B\n",
        encoding="utf-8",
    )
    spec = _spec(drop, adapter={"kind": "file", "location": str(drop / "lots.csv")})
    spec["type_slug"] = "mes_lot"
    spec["key"] = "LOT_ID"
    got = client.post(
        f"/api/relay/pipelines/{_slug()}/scaffold", json=spec, headers=_pat(client, admin)
    ).json()
    assert got["status"] == "drafted", got
    recipe = got["recipe"]
    made = {one["type_slug"]: (one["key"], one["label"]) for one in recipe["types"]}
    assert made == {
        "mes_lot": ("LOT_ID", "LOT_ID"),
        "mes_order": ("ORDER_NO", "ORDER_NAME"),
        "mes_line": ("LINE_CODE", "LINE_NAME"),
    }
    assert [(r["relation"], r["dst_column"]) for r in recipe["relations"]] == [
        ("mes_split_from", "PARENT_LOT_ID")
    ]
    assert recipe["required_missing"] == []
    rows = got["trial"]["recipe"]["rows"]
    assert rows == {"mes_line": 2, "mes_order": 2, "mes_lot": 3}  # 같은 식별자는 하나로
    assert any("타입 3개로 나눴다" in one for one in got["next"])

    alone = client.post(
        f"/api/relay/pipelines/{_slug()}/scaffold",
        json={**spec, "split": False},
        headers=_pat(client, admin),
    ).json()
    assert [one["type_slug"] for one in alone["recipe"].get("types", [])] in ([], ["mes_lot"])
    assert "relations" not in alone["recipe"] or not alone["recipe"]["relations"]
