"""`/api/relay` — 연결 지도 · 파이프라인 상세 · 실행(작업).

정의는 시험이 임시 폴더에 쓴다(지어낸 원천 · 대상). 대상 · 원천에 닿지 않는 것도 지도가 그리는
대로 본다 — 「못 닿음」 · 「실패」 가 그림에 나와야 사람이 안다.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.conftest import Signed, work_until

#: 아무도 안 듣는 자리 — 연결이 바로 거절된다(지도 · 실행이 「못 닿음」 을 그리는지 본다).
NOWHERE = "http://127.0.0.1:9"


def _definition(root: Path, slug: str, **over: Any) -> Path:
    body: dict[str, Any] = {
        "format": "relay-pipeline/1",
        "slug": slug,
        "source": "plm",
        "system": "PLM",
        "adapter": {
            "kind": "odata",
            "service_url": f"{NOWHERE}/odata/v4/Plm",
            "entity_set": "DevModels",
            "key": "ModelId",
            "retries": 1,
            "columns": {
                "MODEL_ID": "ModelId",
                "MODEL_NAME": "Name",
                "KIND": "KindCode",
                "BASE": "BaseModelId",
            },
        },
        "recipe": "recipe.table.json",
        "target": {
            "name": "자주검증 Datahub",
            "server": NOWHERE,
            "token_env": "RELAY_T_TOKEN",
        },
        **over,
    }
    path = root / f"{slug}.json"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


def _slug() -> str:
    return f"plm-{uuid.uuid4().hex[:6]}"


def test_지도는_정의에서_원천_API_파이프라인_대상_타입을_잇는다(
    client: TestClient, member: Signed, registry: Path
) -> None:
    slug = _slug()
    _definition(registry, slug)
    (registry / "draft.example.json").write_text(
        (registry / f"{slug}.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    got = client.get("/api/relay/map", headers=member.headers)
    assert got.status_code == 200, got.text
    body = got.json()
    kinds: dict[str, list[dict[str, Any]]] = {}
    for one in body["nodes"]:
        kinds.setdefault(one["kind"], []).append(one)
    assert [one["label"] for one in kinds["system"]] == ["PLM"]
    assert kinds["endpoint"][0]["label"].startswith("OData DevModels")
    (pipeline,) = kinds["pipeline"]  # 본보기(*.example.json)는 지도에 안 선다
    assert pipeline["label"] == slug and pipeline["status"] == "never"
    assert pipeline["link"] == f"/relay/pipelines/{slug}"
    (target,) = kinds["target"]
    assert target["status"] == "unreachable" and body["problems"]
    assert [one["label"] for one in kinds["type"]] == ["plm_model"]
    pairs = {(edge["source"], edge["target"]) for edge in body["edges"]}
    assert (pipeline["id"], target["id"]) in pairs and (
        target["id"],
        kinds["type"][0]["id"],
    ) in pairs


def test_깨진_정의도_지도에_선다(client: TestClient, member: Signed, registry: Path) -> None:
    slug = _slug()
    _definition(registry, slug, recipe="없는-레시피.json")
    nodes = client.get("/api/relay/map", headers=member.headers).json()["nodes"]
    (pipeline,) = [one for one in nodes if one["kind"] == "pipeline"]
    assert pipeline["status"] == "broken" and pipeline["detail"]["problems"]


def test_상세는_필드_계보와_비밀이_아닌_것만_보인다(
    client: TestClient, member: Signed, registry: Path
) -> None:
    slug = _slug()
    _definition(registry, slug)
    got = client.get(f"/api/relay/pipelines/{slug}", headers=member.headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["target_token_env"] == "RELAY_T_TOKEN"  # 이름만 — 값은 어디에도 안 나간다
    kind = next(one for one in body["lineage"] if one["target_field"] == "model_kind")
    assert kind["source_paths"] == ["KindCode"] and kind["transform"] == "값 대응 2개"
    assert "토큰" in body["target_schema_note"]
    missing = client.get("/api/relay/pipelines/none-such", headers=member.headers)
    assert missing.status_code == 404


def test_실행은_시스템_관리자만_작업으로_넣고_실패는_지도에_나온다(
    client: TestClient,
    member: Signed,
    admin: Signed,
    registry: Path,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _slug()
    _definition(registry, slug)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    refused = client.post(f"/api/relay/pipelines/{slug}/run", headers=member.headers)
    assert refused.status_code == 403, refused.text

    queued = client.post(f"/api/relay/pipelines/{slug}/run", headers=admin.headers)
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job_id"]
    work_until(db, uuid.UUID(job_id))
    job = client.get(f"/api/jobs/{job_id}", headers=admin.headers).json()
    assert job["kind"] == "relay_run" and job["status"] == "failed", job
    assert "닿지 않습니다" in (job["error"] or "") or "실패" in (job["error"] or "")

    nodes = client.get("/api/relay/map", headers=admin.headers).json()["nodes"]
    (pipeline,) = [one for one in nodes if one["kind"] == "pipeline"]
    assert pipeline["status"] == "failed"
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["jobs"][0]["id"] == job_id and detail["jobs"][0]["status"] == "failed"
    home = client.get("/api/server/maintenance", headers=admin.headers).json()
    assert any(one["key"] == "relay_trouble" and one["count"] >= 1 for one in home)


def test_타이머는_차례가_된_것만_한_번_넣는다(registry: Path, db: Session) -> None:
    from app.modules.relay import services

    scheduled, manual = _slug(), _slug()
    _definition(registry, scheduled, schedule={"every_minutes": 60})
    _definition(registry, manual)
    first = services.enqueue_due(db)
    assert scheduled in first and manual not in first
    # 이미 대기 중이면 또 넣지 않는다 — 원천이 느려 한 바퀴가 5분을 넘어도 겹치지 않게.
    assert scheduled not in services.enqueue_due(db)


def test_틀린_일정은_정의_오류로_지도에_선다(
    client: TestClient, member: Signed, registry: Path
) -> None:
    slug = _slug()
    _definition(registry, slug, schedule={"daily_at": "26:00"})
    nodes = client.get("/api/relay/map", headers=member.headers).json()["nodes"]
    (pipeline,) = [one for one in nodes if one["kind"] == "pipeline"]
    assert pipeline["status"] == "broken"
    assert any("daily_at" in one for one in pipeline["detail"]["problems"])
