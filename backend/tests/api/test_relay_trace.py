"""거꾸로 보기를 API 로 — 로그인한 사람이 읽는다(원천 행 · 원래 값은 시스템 관리자만). 못
찾으면 404 와 까닭(`RELAY-0019`).

기준은 대조 시험과 같은 길로 실제 한 바퀴를 돌려 만든다(`_baseline`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.api.conftest import Signed
from tests.api.test_relay import _definition, _slug
from tests.api.test_relay_reconcile import _baseline


def _get(client: TestClient, who: Signed, slug: str, **query: Any) -> Any:
    return client.get(f"/api/relay/pipelines/{slug}/trace", params=query, headers=who.headers)


def test_값의_출처를_원천_행_규칙_관계까지_보인다(
    client: TestClient, admin: Signed, member: Signed, registry: Path, tmp_path: Path
) -> None:
    slug = _slug()
    _definition(registry, slug)
    _baseline(tmp_path, slug)

    got = _get(client, admin, slug, type_slug="plm_model", key="M2")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["found"] and body["basis"] == "기준 실행의 원천 표"
    assert body["recipe_basis"] == "기준 실행의 레시피" and body["rows_total"] == 1
    kind = next(one for one in body["fields"] if one["name"] == "model_kind")
    assert kind["value"] == "선행" and kind["columns"] == ["KIND"]
    assert kind["seen"] == [{"value": "B", "rows": [3]}]
    assert body["relations"] == [
        {"relation": "plm_derived_from", "direction": "out", "other": "M1"}
    ]
    assert body["reconcile"] is None  # 아직 대조하지 않았다
    assert body["rows"] and not body["rows_hidden"]

    # 관리자가 아니면 원천의 날것(레시피가 안 보내는 열까지)은 빼고 규칙 · 기준 값만
    plain = _get(client, member, slug, type_slug="plm_model", key="M2").json()
    assert plain["rows"] == [] and plain["rows_hidden"] and plain["rows_total"] == 1
    kind = next(one for one in plain["fields"] if one["name"] == "model_kind")
    assert kind["value"] == "선행" and kind["seen"] == [] and kind["columns"] == ["KIND"]


def test_못_찾으면_까닭을_말한다(
    client: TestClient, member: Signed, registry: Path, tmp_path: Path
) -> None:
    slug, fresh = _slug(), _slug()
    _definition(registry, slug)
    _definition(registry, fresh)
    _baseline(tmp_path, slug)

    unknown = _get(client, member, slug, type_slug="plm_model", key="M404")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"].endswith("RELAY-0019")
    assert "찾지 못했습니다" in unknown.json()["error"]["message"]
    never = _get(client, member, fresh, type_slug="plm_model", key="M1")
    assert never.status_code == 404
    assert "받은 적이 없습니다" in never.json()["error"]["message"]
    missing = _get(client, member, slug, type_slug="plm_model")
    assert missing.status_code == 422  # 식별자 없이는 묻지 않는다
