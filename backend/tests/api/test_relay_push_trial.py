"""보내 주는 원천을 **사용하기 전에 끝까지** 시험한다 — 견본 행 · 칸 목록 · 시험 수신
(`?trial=1`).
받은 행이 없으면 500 이 아니라 안내. SP 화면에서 적용한 계획은 화면을 열 때 알아챈다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.api.conftest import Signed
from tests.api.test_relay import NOWHERE, _definition, _slug
from tests.api.test_relay_ai_setup import SCHEMA, target_sp  # noqa: F401 — 준비물
from tests.api.test_relay_recipes import _pat

PUSH = {
    "kind": "push",
    "key": "model_id",
    "columns": {"MODEL_ID": "model_id", "MODEL_NAME": "model_name", "KIND": "kind"},
    "auth": {"kind": "bearer", "token_env": "MES_PUSH_TRIAL_TOKEN"},
}
TARGET = {"name": "제조 데이터허브", "server": NOWHERE, "token_env": "MFG_HUB_TOKEN"}
RECIPE = {
    "format": "sp-table/1",
    "types": [
        {
            "type_slug": "mes_model",
            "key": {"column": "MODEL_ID"},
            "label": {"column": "MODEL_NAME"},
            "fields": {"kind": {"column": "KIND"}},
        }
    ],
}
SAMPLE = [
    {"model_id": "M1", "model_name": "모델 1", "kind": "A"},
    {"model_id": "M2", "model_name": "모델 2", "kind": "B"},
]


def _draft(client: TestClient, who: dict[str, str], slug: str) -> tuple[int, int]:
    body = {
        "format": "relay-pipeline/1",
        "slug": slug,
        "source": "mes",
        "system": "MES",
        "adapter": PUSH,
        "target": TARGET,
    }
    made = client.post(
        f"/api/relay/pipelines/{slug}/definitions",
        json={"body": body, "note": "t"},
        headers=who,
    )
    assert made.status_code == 201, made.text
    recipe = client.post(
        f"/api/relay/pipelines/{slug}/recipes", json={"body": RECIPE, "note": "t"}, headers=who
    )
    assert recipe.status_code in (200, 201), recipe.text
    return made.json()["version"], recipe.json()["version"]


def test_받은_행이_없으면_500_이_아니라_안내(
    client: TestClient,
    admin: Signed,
    target_sp: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MFG_HUB_TOKEN", "x")
    monkeypatch.setenv("MES_PUSH_TRIAL_TOKEN", "push-trial-s3cret")
    slug = _slug()
    mcp = _pat(client, admin)
    version, recipe = _draft(client, mcp, slug)
    got = client.post(
        f"/api/relay/pipelines/{slug}/definitions/{version}/test",
        json={"recipe_version": recipe},
        headers=mcp,
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["source"]["rows"] == 0
    assert "sample_rows" in body["recipe"]["note"] and "?trial=1" in body["recipe"]["note"]


def test_견본_행으로_사용_전에_끝까지_시험한다(
    client: TestClient,
    admin: Signed,
    target_sp: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MFG_HUB_TOKEN", "x")  # 원천의 비밀 없이도 — 원천에 닿지 않는다
    slug = _slug()
    mcp = _pat(client, admin)
    version, recipe = _draft(client, mcp, slug)
    got = client.post(
        f"/api/relay/pipelines/{slug}/definitions/{version}/test",
        json={"recipe_version": recipe, "sample_rows": SAMPLE},
        headers=mcp,
    ).json()
    assert got["source"]["sample_rows"] and got["source"]["rows"] == 2
    assert got["recipe"]["rows"] == {"mes_model": 2} and got["recipe"]["clean"]
    assert "would_send" not in got["recipe"]  # 견본은 원천의 전부가 아니다
    tested = client.post(
        f"/api/relay/pipelines/{slug}/recipes/{recipe}/test",
        json={"sample_rows": SAMPLE},
        headers=mcp,
    ).json()
    assert tested["sample_rows"] and tested["rows"] == {"mes_model": 2}
    too_many = client.post(
        f"/api/relay/pipelines/{slug}/definitions/{version}/test",
        json={"sample_rows": [SAMPLE[0]] * 1001},
        headers=mcp,
    )
    assert too_many.status_code == 422


def test_시험_수신은_초안으로_받고_실행은_읽지_않는다(
    client: TestClient,
    admin: Signed,
    target_sp: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MFG_HUB_TOKEN", "x")
    monkeypatch.setenv("MES_PUSH_TRIAL_TOKEN", "push-trial-s3cret")
    slug = _slug()
    version, recipe = _draft(client, _pat(client, admin), slug)
    url = f"/api/relay/inbound/{slug}"
    good = {"X-Relay-Token": "push-trial-s3cret"}
    assert (
        client.post(url, json=SAMPLE, headers=good).status_code == 404
    )  # 초안 — 진짜는 아니다
    assert (
        client.post(f"{url}?trial=1", json=SAMPLE, headers={"X-Relay-Token": "no"}).status_code
        == 401
    )
    got = client.post(f"{url}?trial=1", json=SAMPLE, headers=good)
    assert got.status_code == 202 and got.json()["trial"], got.text
    root = get_settings().relay_work_dir / slug
    assert list((root / "trial-inbox").glob("*.json")) and not (root / "inbox").exists()

    tested = client.post(
        f"/api/relay/pipelines/{slug}/definitions/{version}/test",
        json={"recipe_version": recipe},
        headers=admin.headers,
    ).json()
    assert tested["source"]["rows"] == 2 and "테스트 수신함" in tested["source"]["inbox"]
    assert tested["recipe"]["rows"] == {"mes_model": 2}
    for _ in range(25):  # 최근 것만 남긴다
        client.post(f"{url}?trial=1", json=SAMPLE[:1], headers=good)
    assert len(list((root / "trial-inbox").glob("*.json"))) == 20


def test_한_번에_초안은_보내_주는_원천을_견본_행이나_칸_목록으로_짓는다(
    client: TestClient,
    admin: Signed,
    target_sp: None,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MFG_HUB_TOKEN", "x")
    monkeypatch.setenv("MES_PUSH_TRIAL_TOKEN", "push-trial-s3cret")
    mcp = _pat(client, admin)
    adapter = {"kind": "push", "auth": {"kind": "bearer", "token_env": "MES_PUSH_TRIAL_TOKEN"}}
    spec: dict[str, Any] = {
        "adapter": adapter,
        "source": "mes",
        "type_slug": "mes_model",
        "target": TARGET,
    }
    empty = client.post(
        f"/api/relay/pipelines/{_slug()}/scaffold", json=spec, headers=mcp
    ).json()
    assert empty["status"] == "discover_failed" and "sample_rows" in empty["next"][0]

    sampled = client.post(
        f"/api/relay/pipelines/{_slug()}/scaffold",
        json={**spec, "sample_rows": SAMPLE},
        headers=mcp,
    ).json()
    assert sampled["status"] == "drafted", sampled
    assert sampled["definition"]["body"]["adapter"]["key"] == "model_id"
    assert sampled["trial"]["source"]["rows"] == 2
    assert {m["property"] for m in sampled["recipe"]["matched"]} >= {"model_name", "kind"}

    listed = client.post(
        f"/api/relay/pipelines/{_slug()}/scaffold",
        json={**spec, "columns": {"MODEL_ID": "model_id", "MODEL_NAME": "model_name"}},
        headers=mcp,
    ).json()
    assert listed["status"] == "drafted", listed
    assert listed["definition"]["body"]["adapter"]["key"] == "model_id"


def test_SP_화면에서_적용한_계획은_화면을_열_때_알아챈다(
    client: TestClient,
    admin: Signed,
    registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.modules.relay import services

    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    work = get_settings().relay_work_dir / slug
    (work / "runs" / "20261118T000000Z").mkdir(parents=True)
    pending = {
        "run": "runs/20261118T000000Z",
        "send": "sends/20261118T000000Z",
        "job_id": "job-applied-on-sp",
        "ok": True,
        "digest": "d",
        "summary": "s",
        "at": "2026-11-18T00:00:00Z",
    }
    (work / "state.json").write_text(json.dumps({"pending": pending}), encoding="utf-8")
    asked: list[str] = []

    def probe(server: str, token: str, path: str) -> Any:
        if path.startswith("/api/jobs/"):
            asked.append(path)
        if path == "/api/jobs/job-applied-on-sp":
            return 200, {"id": "job-applied-on-sp", "applied_by": "someone"}
        return 404, None

    monkeypatch.setattr(services, "probe_http", probe)
    services._asked.clear()
    client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers)
    state = json.loads((work / "state.json").read_text(encoding="utf-8"))
    assert state["pending"] is None and state["applied_run"] == "runs/20261118T000000Z"
    assert any(one.get("event") == "seen_applied" for one in state["history"])
    assert asked == ["/api/jobs/job-applied-on-sp"]
    client.get("/api/relay/map", headers=admin.headers)
    assert asked == ["/api/jobs/job-applied-on-sp"]  # 대기 계획이 없다 — 다시 묻지 않는다
