"""레시피의 판 — AI(MCP)는 초안을 짓고 시험한다, **켜는 것은 사람이 화면에서**(ADR 0001 §2)."""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.relay import recipes
from relay_core.mirror import Mirror
from relay_core.records import SourceRecord
from relay_core.state import Workdir
from tests.api.conftest import Signed
from tests.api.test_relay import _definition, _slug
from tests.unit.test_relay_core import RECIPE


def _pat(client: TestClient, who: Signed) -> dict[str, str]:
    made = client.post(
        "/api/auth/tokens",
        json={"name": f"mcp-{uuid.uuid4().hex[:6]}", "scopes": ["read", "relay:write"]},
        headers=who.headers,
    )
    assert made.status_code == 201, made.text
    return {"Authorization": f"Bearer {made.json()['token']}", "X-Client": "mcp"}


def _mirror(slug: str) -> None:
    rows = [
        SourceRecord(
            key=f"M{i}",
            values={"MODEL_ID": f"M{i}", "MODEL_NAME": f"모델 {i}", "KIND": "A", "BASE": ""},
        )
        for i in range(1, 4)
    ]
    Workdir(get_settings().relay_work_dir, slug).save_mirror(Mirror(rows))


def _draft(client: TestClient, who: dict[str, str], slug: str, body: dict[str, Any]) -> Any:
    return client.post(
        f"/api/relay/pipelines/{slug}/recipes",
        json={"body": body, "note": "시험"},
        headers=who,
    )


def test_토큰으로_초안을_짓고_시험하지만_켜지는_못한다(
    client: TestClient,
    admin: Signed,
    registry: Path,
) -> None:
    slug = _slug()
    _definition(registry, slug)
    mcp = _pat(client, admin)

    saved = _draft(client, mcp, slug, RECIPE)
    assert saved.status_code == 201, saved.text
    assert saved.json() == {"version": 1, "problems": []}

    early = client.post(f"/api/relay/pipelines/{slug}/recipes/1/test", headers=mcp)
    assert early.status_code == 409 and "먼저" in early.json()["error"]["message"]

    _mirror(slug)
    tried = client.post(f"/api/relay/pipelines/{slug}/recipes/1/test", headers=mcp)
    assert tried.status_code == 200, tried.text
    result = tried.json()
    assert result["clean"] and result["rows"] == {"plm_model": 3}
    assert result["would_send"]["objects"]["plm_model"]["created"] == 3
    assert result["against"].startswith("대상이 비었다고")

    refused = client.post(f"/api/relay/pipelines/{slug}/recipes/1/activate", headers=mcp)
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"].endswith("RELAY-0007")

    listed = client.get(f"/api/relay/pipelines/{slug}/recipes", headers=mcp).json()
    assert [(one["version"], one["status"], one["client"]) for one in listed] == [
        (1, "draft", "mcp")
    ]


def test_사람이_켜면_그_판이_돌고_앞의_판은_내려간다(
    client: TestClient,
    admin: Signed,
    registry: Path,
    db: Session,
) -> None:
    slug = _slug()
    _definition(registry, slug)
    _draft(client, admin.headers, slug, RECIPE)
    second = copy.deepcopy(RECIPE)
    second["types"][0]["fields"]["model_kind"]["map"]["C"] = "단종"
    second["relations"] = []
    _draft(client, admin.headers, slug, second)

    for version in (1, 2):
        done = client.post(
            f"/api/relay/pipelines/{slug}/recipes/{version}/activate", headers=admin.headers
        )
        assert done.status_code == 200 and done.json()["status"] == "active", done.text
    statuses = {
        one["version"]: one["status"]
        for one in client.get(
            f"/api/relay/pipelines/{slug}/recipes", headers=admin.headers
        ).json()
    }
    assert statuses == {1: "retired", 2: "active"}

    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["recipe_label"] == "판 2" and detail["relations"] == []
    kind = next(one for one in detail["lineage"] if one["target_field"] == "model_kind")
    assert kind["transform"] == "값 대응 3개"

    path = recipes.materialize(db, slug)
    assert path is not None and json.loads(path.read_text(encoding="utf-8")) == second


def test_어댑터가_안_내는_열을_읽는_초안은_저장하되_알린다(
    client: TestClient,
    admin: Signed,
    registry: Path,
) -> None:
    slug = _slug()
    _definition(registry, slug)
    body = copy.deepcopy(RECIPE)
    body["types"][0]["fields"]["region"] = {"column": "REGION"}
    saved = _draft(client, admin.headers, slug, body)
    assert saved.status_code == 201
    assert saved.json()["problems"] == ["어댑터가 내지 않는 열을 읽습니다: REGION"]
    wrong = _draft(client, admin.headers, slug, {"format": "x"})
    assert wrong.status_code == 422


def test_표본과_대상_정의는_시스템_관리자만(
    client: TestClient,
    admin: Signed,
    member: Signed,
    registry: Path,
) -> None:
    slug = _slug()
    _definition(registry, slug)
    _mirror(slug)
    assert (
        client.get(f"/api/relay/pipelines/{slug}/sample", headers=member.headers).status_code
        == 403
    )
    sample = client.get(
        f"/api/relay/pipelines/{slug}/sample?limit=2", headers=admin.headers
    ).json()
    assert sample["total"] == 3 and len(sample["rows"]) == 2
    assert sample["rows"][0]["MODEL_NAME"] == "모델 1"
    schema = client.get(
        f"/api/relay/pipelines/{slug}/target-schema", headers=admin.headers
    ).json()
    assert "토큰" in schema["note"]  # 시험 대상에는 토큰이 없다 — 그 까닭을 말한다


def test_레시피_판은_서버의_파일을_가리키지_못한다(
    client: TestClient, admin: Signed, registry: Path
) -> None:
    """DB 의 판은 작업 폴더에 옮겨 적혀 도므로 `@../../…` 가 서버의 아무 파일이나 읽고,
    `ontology` 는 묶음에 실려 대상으로 나간다(2026-11-06 보안 검토)."""
    slug = _slug()
    _definition(registry, slug)
    mcp = _pat(client, admin)
    keys = copy.deepcopy(RECIPE)
    field = keys["types"][0]["fields"]["model_kind"]
    field["match"] = {"keys": "@../../../../etc/passwd"}
    for body in (keys, {**RECIPE, "ontology": "../../../secret.json"}):
        got = _draft(client, mcp, slug, body)
        assert got.status_code == 422, got.text
        assert got.json()["error"]["code"].endswith("RELAY-0022")
    by_type = copy.deepcopy(RECIPE)
    by_type["types"][0]["fields"]["model_kind"]["match"] = {"keys": "plm_kind"}
    assert _draft(client, mcp, slug, by_type).status_code == 201  # 대상의 타입으로는 된다
