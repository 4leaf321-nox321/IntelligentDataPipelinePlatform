"""새 연결 — 화면이 부르는 길 그대로: 정의 판 초안(새 slug) → 타입 정의 초안 → 레시피 판
초안.

정의 판에는 받기 점검(`intake`) · 계산 칸(`computed`) · 새 원천 모양(여러 칸 식별자 · 목록
펴기 · 다음 쪽 주소 · oauth2)을 적을 수 있어야 한다 — 허용 칸 목록(`definitions.TOP_KEYS`)에
빠지면 저장이 거절된다(받기 점검이 그랬다, 2026-11-07).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_relay import NOWHERE, _slug


def _body(slug: str) -> dict[str, Any]:
    return {
        "format": "relay-pipeline/1",
        "slug": slug,
        "source": "mes",
        "system": "MES",
        "adapter": {
            "kind": "rest",
            "base_url": f"{NOWHERE}/mes",
            "path": "/lots",
            "key": ["LotId", "steps.Seq"],
            "explode": "steps",
            "paging": "next_url",
            "columns": {"LOT_ID": "LotId", "STEP": "steps.Seq", "QTY_G": "steps.Grams"},
            "auth": {
                "kind": "oauth2",
                "token_env": "MES_CLIENT_SECRET",
                "user": "relay",
                "token_url": f"{NOWHERE}/idp/token",
            },
        },
        "target": {"name": "제조 데이터허브", "server": NOWHERE, "token_env": "RELAY_T_TOKEN"},
        "intake": {"delete_ratio": 0.1},
        "computed": {"QTY_KG": {"number": "QTY_G", "multiply": 0.001}},
    }


def test_새_연결은_정의_판_초안_타입_정의_초안_레시피_판_초안으로_선다(
    client: TestClient, admin: Signed, member: Signed, registry: Path
) -> None:
    slug = _slug()
    saved = client.post(
        f"/api/relay/pipelines/{slug}/definitions",
        json={"body": _body(slug), "note": "새 연결"},
        headers=admin.headers,
    )
    assert saved.status_code == 201, saved.text
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["draft"] is True and "10%" in detail["intake_label"]
    computed = [one for one in detail["lineage"] if one["columns"] == ["QTY_KG"]]
    assert computed == [] or "계산" in computed[0]["source_paths"][0]

    sample = {
        "LOT_ID": ["L1", "L2"],
        "STEP": ["1", "2"],
        "QTY_KG": ["1.25", "3"],
        "MADE_ON": ["2026-11-01", "2026-11-02"],
    }
    url = "/api/relay/type-draft"
    ask = {
        "type_slug": "mes_lot_step",
        "label": "로트 공정",
        "key_column": "LOT_ID",
        "columns": sample,
        "system": "MES",
    }
    assert client.post(url, json=ask, headers=member.headers).status_code == 403
    bad = client.post(url, json={**ask, "type_slug": "Bad"}, headers=admin.headers)
    assert bad.status_code == 422 and bad.json()["error"]["code"].endswith("RELAY-0029")
    got = client.post(url, json=ask, headers=admin.headers)
    assert got.status_code == 200, got.text
    kinds = {
        p["key"]: p["data_type"] for p in got.json()["ontology"]["types"][0]["properties"]
    }
    assert kinds == {"step": "number", "qty_kg": "number", "made_on": "date"}

    recipe = client.post(
        f"/api/relay/pipelines/{slug}/recipes",
        json={"body": got.json()["recipe"], "note": "새 연결 — 첫 판"},
        headers=admin.headers,
    )
    assert recipe.status_code == 201, recipe.text
    versions = client.get(f"/api/relay/pipelines/{slug}/recipes", headers=admin.headers).json()
    assert [one["status"] for one in versions] == ["draft"]


def test_정의의_새_칸이_틀리면_저장을_거절한다(
    client: TestClient, admin: Signed, registry: Path
) -> None:
    slug = _slug()
    body = _body(slug)
    body["adapter"]["explode"] = "steps"
    body["adapter"]["key"] = "LotId"  # 원소를 가리지 않는 식별자
    refused = client.post(
        f"/api/relay/pipelines/{slug}/definitions", json={"body": body}, headers=admin.headers
    )
    assert refused.status_code == 422 and "식별자" in refused.text
    body = _body(slug)
    body["computed"] = {"LOT_ID": {"template": "{LOT_ID}"}}
    refused = client.post(
        f"/api/relay/pipelines/{slug}/definitions", json={"body": body}, headers=admin.headers
    )
    assert refused.status_code == 422 and "이미 내는 열" in refused.text
