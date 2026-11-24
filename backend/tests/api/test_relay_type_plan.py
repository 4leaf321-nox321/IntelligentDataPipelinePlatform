"""대상에 타입 계획 올리기 — 정의 초안을 원천 이름(`source`)과 함께 미리 보기로(적용 안 함),
대상이 거절하면(ontology:write 없음) 그 말과 할 일을. 토큰(MCP)으로도 된다(미리 보기일 뿐).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from relay_core import sp
from tests.api.conftest import Signed
from tests.api.test_relay import _definition, _slug
from tests.api.test_relay_recipes import _pat

ONTOLOGY = {
    "types": [
        {
            "slug": "plm_lot",
            "label": "로트",
            "properties": [{"key": "qty", "label": "수량", "data_type": "number"}],
        }
    ]
}


def test_타입_계획은_원천_이름과_함께_미리_보기로_올린다(
    client: TestClient, admin: Signed, registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "t0k-target")
    sent: list[dict[str, Any]] = []

    def post(server: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
        sent.append({"server": server, "token": token, **body})
        return {"ontology": {"types": {"create": 1}}, "job_id": "job-type-1"}

    monkeypatch.setattr(sp.pipeline, "_post_bundle", post)
    got = client.post(
        f"/api/relay/pipelines/{slug}/type-plan",
        json={"ontology": ONTOLOGY, "note": "로트 타입"},
        headers=_pat(client, admin),
    )
    assert got.status_code == 200, got.text
    body = got.json()
    (one,) = sent
    assert one["source"] == "plm" and one["apply"] is False and one["ontology"] == ONTOLOGY
    assert one["origin"]["pipeline"] == slug and one["token"] == "t0k-target"
    assert body["job_id"] == "job-type-1" and "job-type-1" in body["link"]
    assert body["types"] == ["plm_lot"] and any("[적용]" in line for line in body["next"])

    def refuse(server: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
        raise sp.pipeline.Stop(
            "플랫폼이 거절했습니다(403): 이 토큰에는 ontology:write 범위가 없습니다 t0k-target"
        )

    monkeypatch.setattr(sp.pipeline, "_post_bundle", refuse)
    denied = client.post(
        f"/api/relay/pipelines/{slug}/type-plan",
        json={"ontology": ONTOLOGY},
        headers=admin.headers,
    )
    assert denied.status_code == 502 and "SP 관리자" in denied.text
    assert "t0k-target" not in denied.text
    empty = client.post(
        f"/api/relay/pipelines/{slug}/type-plan", json={"ontology": {}}, headers=admin.headers
    )
    assert empty.status_code == 422
