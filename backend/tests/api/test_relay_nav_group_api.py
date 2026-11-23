"""사이드바 묶음을 한 번에 초안 · 타입 계획에서 — 대상의 묶음에서 고르고, 없는 묶음을 가리키면
보내기 전에 거절하고, 새 묶음은 계획에 함께 만든다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from relay_core import sp
from tests.api.conftest import Signed
from tests.api.test_relay import _definition, _slug
from tests.api.test_relay_ai_setup import _spec
from tests.api.test_relay_definitions import drop  # noqa: F401 — 준비물
from tests.api.test_relay_recipes import _pat

SCHEMA = {
    "groups": [
        {
            "slug": "mfg",
            "label": "제조",
            "icon": "factory",
            "sort_order": 10,
            "is_active": True,
        },
        {"slug": "old", "label": "옛 묶음", "is_active": False},
    ],
    "types": [
        {"slug": "mes_order", "managed_by": "mes", "nav_group_slug": "mfg", "properties": []},
        {"slug": "plm_model", "managed_by": "plm", "nav_group_slug": "", "properties": []},
    ],
    "relation_types": [],
}


@pytest.fixture
def target(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from app.modules.relay import services

    def probe(server: str, token: str, path: str) -> Any:
        if path == "/api/health":
            return 200, {"status": "ok", "slug": "mfghub", "version": "v0.4.96"}
        if path == "/api/ontology/schema":
            return 200, SCHEMA
        return 404, None

    monkeypatch.setattr(services, "probe_http", probe)
    sent: list[dict[str, Any]] = []

    def post(server: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        return {"ontology": {"changes": []}, "job_id": "job-nav-1"}

    monkeypatch.setattr(sp.pipeline, "_post_bundle", post)
    return sent


def test_한_번에_초안은_같은_원천의_묶음을_고른다(
    client: TestClient,
    admin: Signed,
    drop: Path,  # noqa: F811
    target: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MFG_HUB_TOKEN", "x")
    mcp = _pat(client, admin)
    got = client.post(
        f"/api/relay/pipelines/{_slug()}/scaffold",
        json=_spec(drop, type_slug="mes_lot", key="MODEL_ID"),
        headers=mcp,
    ).json()
    assert got["status"] == "type_missing", got
    assert got["ontology"]["types"][0]["nav_group_slug"] == "mfg"
    assert (
        got["recipe"]["nav_group"]["slug"] == "mfg"
        and "mes" in got["recipe"]["nav_group"]["why"]
    )

    given = client.post(
        f"/api/relay/pipelines/{_slug()}/scaffold",
        json=_spec(
            drop,
            type_slug="mes_lot",
            key="MODEL_ID",
            nav_group={"slug": "lots", "label": "로트", "icon": "box"},
        ),
        headers=mcp,
    ).json()
    assert given["ontology"]["groups"] == [{"slug": "lots", "label": "로트", "icon": "box"}]
    assert given["ontology"]["types"][0]["nav_group_slug"] == "lots"


def test_타입_계획은_없는_묶음을_보내기_전에_거절하고_새_묶음은_함께(
    client: TestClient,
    admin: Signed,
    registry: Path,
    target: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    one = {"slug": "plm_lot", "label": "로트", "properties": []}
    nowhere = client.post(
        f"/api/relay/pipelines/{slug}/type-plan",
        json={"ontology": {"types": [{**one, "nav_group_slug": "old"}]}},  # 꺼진 묶음
        headers=admin.headers,
    )
    assert nowhere.status_code == 422 and "mfg" in nowhere.text and not target
    made = client.post(
        f"/api/relay/pipelines/{slug}/type-plan",
        json={
            "ontology": {
                "groups": [{"slug": "plm", "label": "PLM"}],
                "types": [{**one, "nav_group_slug": "plm"}],
            }
        },
        headers=admin.headers,
    )
    assert made.status_code == 200, made.text
    assert made.json()["groups_created"] == ["plm"]
    assert made.json()["nav_group_of"] == {"plm_lot": "plm"}
    assert target[-1]["ontology"]["groups"] == [{"slug": "plm", "label": "PLM"}]
    # 이미 있는 타입을 다른 묶음으로 — 보낸 칸만(SP 는 안 보낸 칸을 그대로 둔다).
    moved = client.post(
        f"/api/relay/pipelines/{slug}/type-plan",
        json={"ontology": {"types": [{"slug": "plm_model", "nav_group_slug": "mfg"}]}},
        headers=admin.headers,
    )
    assert moved.status_code == 200 and moved.json()["groups_created"] == []
    bad = client.post(
        f"/api/relay/pipelines/{slug}/type-plan",
        json={"ontology": {"groups": [{"slug": "x y", "label": "X"}], "types": [one]}},
        headers=admin.headers,
    )
    assert bad.status_code == 422
