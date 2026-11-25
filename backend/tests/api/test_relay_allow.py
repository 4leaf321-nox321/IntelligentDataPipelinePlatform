"""허용 주소 — 화면에서 재시작 없이 더하고(사람만), 대상 SP 주소는 원천 주소로 못 쓴다.
설정(`RELAY_TARGET_HOSTS`)의 대상 주소도 같다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from tests.api.conftest import Signed
from tests.api.test_relay_definitions import _code
from tests.api.test_relay_recipes import _pat

HUB = "hub.corp.example:8040"


@pytest.fixture(autouse=True)
def _scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """허용 주소 파일(`relay_work_dir` 의 부모)을 임시 폴더로 — 그대로 두면 개발 PC 의
    `.work/allowed-hosts.json` 에 쓰고, 시험이 도중에 실패하면 시험 주소가 개발 설정에 남는다
    (2026-11-25 — 정리 단계 전에 멈춘 실행이 남긴 주소를 다음 실행이 「비어 있지 않음」 으로
    읽었다)."""
    monkeypatch.setattr(get_settings(), "relay_work_dir", tmp_path / "work")


def _check_target(db: Session) -> int:
    """대상의 울타리를 지나는지 — 정의 초안의 시험 · 한 번에 초안이 지나는 길."""
    from app.modules.relay import guard, services
    from app.shared.errors import Forbidden

    try:
        guard.check_target(
            {"server": f"http://{HUB}", "token_env": "HUB_X_TOKEN"},
            guard.fence(services.catalog(db)),
        )
    except Forbidden:
        return 403
    return 200


def test_허용_주소는_사람만_화면에서_재시작_없이(
    client: TestClient, admin: Signed, db: Session
) -> None:
    mcp = _pat(client, admin)
    listed = client.get("/api/relay/allowed-hosts", headers=mcp)
    assert listed.status_code == 200 and listed.json()["target"]["screen"] == []
    assert _check_target(db) == 403

    refused = client.put("/api/relay/allowed-hosts", json={"target": [HUB]}, headers=mcp)
    assert refused.status_code == 403 and _code(refused).endswith("RELAY-0039")
    bad = client.put(
        "/api/relay/allowed-hosts",
        json={"target": ["https://hub.corp.example/api"]},
        headers=admin.headers,
    )
    assert bad.status_code == 422
    saved = client.put(
        "/api/relay/allowed-hosts", json={"target": [HUB.upper()]}, headers=admin.headers
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["target"]["screen"] == [HUB] and saved.json()["updated_by"]
    assert _check_target(db) == 200  # 재시작 없이 바로

    # 대상 주소는 원천 주소로 못 쓴다 — 원천의 비밀이 그쪽으로 나가지 않게.
    found = client.post(
        "/api/relay/discover",
        json={"adapter": {"kind": "rest", "base_url": f"http://{HUB}", "path": "/x"}},
        headers=mcp,
    )
    assert found.status_code == 403 and "[원천]" in found.text
    cleared = client.put("/api/relay/allowed-hosts", json={}, headers=admin.headers)
    assert cleared.json()["target"]["screen"] == []
    assert _check_target(db) == 403


def test_설정의_대상_주소(
    client: TestClient, admin: Signed, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "relay_target_hosts", HUB)
    assert _check_target(db) == 200
    listed = client.get("/api/relay/allowed-hosts", headers=admin.headers).json()
    assert HUB in listed["target"]["settings"] and HUB not in listed["source"]["settings"]
