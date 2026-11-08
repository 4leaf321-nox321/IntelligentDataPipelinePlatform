"""보내 주는 원천(push) — 받는 자리의 차례(크기 · 받을 곳 · 비밀 · 모양 · 건수)와, 실행이 받을
함의 묶음을 **미러에 넣은 뒤에만** 지우는 것(받기 점검이 멈추면 남는다).
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from relay_core import runner
from relay_core.definition import load_body
from relay_core.state import Workdir
from tests.api.test_relay import _definition, _slug
from tests.unit.test_relay_core import RECIPE
from tests.unit.test_relay_rest import FakeSubmitter

PUSH = {
    "kind": "push",
    "key": "LotId",
    "columns": {"MODEL_ID": "LotId", "MODEL_NAME": "Name", "KIND": "Kind", "BASE": "Base"},
    "deleted": "Gone",
    "max_items": 50,
    "auth": {"kind": "bearer", "token_env": "MES_PUSH_TOKEN"},
}


def _push(registry: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    slug = _slug()
    _definition(registry, slug, source="mes", adapter=PUSH)
    monkeypatch.setenv("MES_PUSH_TOKEN", "push-s3cret-value")
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    return slug


def _lots(*numbers: int, **over: Any) -> list[dict[str, Any]]:
    return [
        {"LotId": f"L{n}", "Name": f"로트 {n}", "Kind": "A", "Base": "", **over}
        for n in numbers
    ]


def test_받는_자리는_비밀이_맞을_때만_받을_함에_쌓는다(
    client: TestClient, registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _push(registry, monkeypatch)
    url = f"/api/relay/inbound/{slug}"
    good = {"X-Relay-Token": "push-s3cret-value"}

    assert client.post(url, json=_lots(1)).status_code == 401  # 로그인 없이 — 비밀이 없다
    wrong = client.post(url, json=_lots(1), headers={"X-Relay-Token": "nope"})
    assert wrong.status_code == 401 and wrong.json()["error"]["code"].endswith("RELAY-0033")
    missing = client.post("/api/relay/inbound/none-such", json=_lots(1), headers=good)
    assert missing.status_code == 404
    other = _slug()
    _definition(registry, other)  # 보내 주는 원천이 아니다 — 없는 것과 같다
    assert (
        client.post(f"/api/relay/inbound/{other}", json=_lots(1), headers=good).status_code
        == 404
    )
    bad = client.post(
        url, content=b"{not json", headers={**good, "Content-Type": "application/json"}
    )
    assert bad.status_code == 422
    many = client.post(url, json=_lots(*range(60)), headers=good)
    assert many.status_code == 413

    got = client.post(url, json=_lots(1, 2), headers=good)
    assert got.status_code == 202, got.text
    assert got.json()["accepted"] == 2 and got.json()["waiting"] == 1
    inbox = get_settings().relay_work_dir / slug / "inbox"
    (one,) = list(inbox.glob("*.json"))
    assert json.loads(one.read_text(encoding="utf-8"))[0]["LotId"] == "L1"


def test_실행은_받을_함을_미러에_넣은_뒤에만_지운다(
    client: TestClient, registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = _push(registry, monkeypatch)
    url = f"/api/relay/inbound/{slug}"
    good = {"X-Relay-Token": "push-s3cret-value"}
    client.post(url, json=_lots(*range(1, 21)), headers=good)
    client.post(url, json=_lots(21), headers=good)

    root = get_settings().relay_work_dir
    body = json.loads((registry / f"{slug}.json").read_text(encoding="utf-8"))
    recipe = registry / "recipe.table.json"
    recipe.write_text(json.dumps(RECIPE), encoding="utf-8")
    pipeline = load_body(body, base=registry, inbox=root / slug / "inbox")
    work = Workdir(root, slug)
    fake = FakeSubmitter()
    first = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert first.status == "planned" and "받음 21건" in first.message, first.message
    assert list((root / slug / "inbox").glob("*.json")) == []  # 미러에 들어갔다 — 지운다
    runner.apply(pipeline, work, fake)  # type: ignore[arg-type]

    # 받기 점검에 걸리면(대량 삭제) 받을 함의 묶음은 남는다 — 다음 실행이 다시 읽는다.
    client.post(url, json=_lots(*range(1, 16), Gone=True), headers=good)
    held = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert held.status == "blocked" and "대량 삭제" in held.message
    assert len(list((root / slug / "inbox").glob("*.json"))) == 1
    off = dataclasses.replace(pipeline, intake=None)
    assert runner.run(off, work, fake).status == "planned"  # type: ignore[arg-type]
    assert list((root / slug / "inbox").glob("*.json")) == [] and work.mirror_size() == 6
