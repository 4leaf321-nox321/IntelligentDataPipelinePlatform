"""Kafka 원천을 실행에 — 미러에 넣은 뒤에만 확정하고(받기 점검에 걸리면 안 한다), 실행 기록에
메시지 수가 남는다. 지도의 이름 · 탐색의 울타리(브로커가 밖이면 거절).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from relay_core import runner
from relay_core.adapters import kafka
from relay_core.definition import load_body
from relay_core.state import Workdir
from tests.api.conftest import Signed
from tests.api.test_relay import _definition, _slug
from tests.api.test_relay_recipes import _pat
from tests.unit.test_relay_core import RECIPE
from tests.unit.test_relay_kafka import Broker
from tests.unit.test_relay_rest import FakeSubmitter

KAFKA = {
    "kind": "kafka",
    "bootstrap": "kafka1.corp:9093",
    "topic": "mes.lot",
    "group_id": "relay-mes-lot",
    "key": "LotId",
    "columns": {"MODEL_ID": "LotId", "MODEL_NAME": "Name", "KIND": "Kind", "BASE": "Base"},
    "deleted": "Gone",
    "timeout": 1,
    "auth": {
        "kind": "sasl",
        "mechanism": "SCRAM-SHA-512",
        "user": "svc_relay",
        "token_env": "MES_KAFKA_PASSWORD",
    },
}


def _lot(n: int, **over: object) -> dict[str, object]:
    return {"LotId": f"L{n}", "Name": f"로트 {n}", "Kind": "A", "Base": "", **over}


def test_실행은_미러에_넣은_뒤에만_확정한다(
    client: TestClient, registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broker = Broker()
    monkeypatch.setattr(kafka, "_default_consumer", broker.consumer)
    monkeypatch.setenv("MES_KAFKA_PASSWORD", "kafka-pw")
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    slug = _slug()
    _definition(registry, slug, source="mes", adapter=KAFKA)
    for n in range(1, 21):
        broker.send(n % 2, _lot(n))
    (registry / "recipe.table.json").write_text(json.dumps(RECIPE), encoding="utf-8")
    body = json.loads((registry / f"{slug}.json").read_text(encoding="utf-8"))
    pipeline = load_body(body, base=registry)
    work = Workdir(get_settings().relay_work_dir, slug)
    fake = FakeSubmitter()

    first = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert first.status == "planned" and "받음 20건" in first.message, first.message
    assert "Kafka 메시지 20건." in first.message
    assert broker.committed == {("relay-mes-lot", 0): 10, ("relay-mes-lot", 1): 10}
    assert broker.confs[0]["sasl.password"] == "kafka-pw"
    runner.apply(pipeline, work, fake)  # type: ignore[arg-type]

    # 받기 점검(대량 삭제)에 걸리면 확정하지 않는다 — 다음 실행이 다시 읽는다.
    for n in range(1, 16):
        broker.send(0, _lot(n, Gone=True))
    held = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert held.status == "blocked" and "대량 삭제" in held.message
    assert broker.committed[("relay-mes-lot", 0)] == 10
    off = dataclasses.replace(pipeline, intake=None)
    assert runner.run(off, work, fake).status == "planned"  # type: ignore[arg-type]
    assert broker.committed[("relay-mes-lot", 0)] == 25 and work.mirror_size() == 5


def test_지도의_이름과_탐색의_울타리(
    client: TestClient,
    admin: Signed,
    registry: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = Broker()
    broker.send(0, _lot(1))
    monkeypatch.setattr(kafka, "_default_consumer", broker.consumer)
    monkeypatch.setenv("MES_KAFKA_PASSWORD", "kafka-pw")
    slug = _slug()
    _definition(registry, slug, source="mes", adapter=KAFKA)
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert "Kafka mes.lot" in json.dumps(detail, ensure_ascii=False)

    mcp = _pat(client, admin)
    # 사용 중인 정의의 브로커는 울타리 안 — 탐색이 된다.
    inside = client.post("/api/relay/discover", json={"adapter": KAFKA}, headers=mcp)
    assert inside.status_code == 200, inside.text
    assert inside.json()["found"]["waiting"] == 1
    outside = client.post(
        "/api/relay/discover",
        json={"adapter": {**KAFKA, "bootstrap": "kafka1.corp:9093,evil.example:9092"}},
        headers=mcp,
    )
    assert outside.status_code == 403 and outside.json()["error"]["code"].endswith(
        "RELAY-0010"
    )
    kinds = client.get("/api/relay/adapter-kinds", headers=admin.headers).json()
    assert "kafka" in {one["kind"] for one in kinds["kinds"]}
