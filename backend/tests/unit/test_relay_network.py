"""바깥 연결 — 사내 CA 를 공개 CA 묶음에 **더해** 걸고(사내 CA 만 걸면 공개 주소가 거꾸로 안
닿는다), 프록시를 표준 환경 변수로 건다. 진짜 TLS 로 닿는지는 사설 CA 로 띄운 서버로 본다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import certifi
import pytest

from app.config import get_settings
from app.modules.relay import network


def test_사내_CA_를_공개_묶음에_더해_SSL_CERT_FILE_로_건다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env: dict[str, Any] = {}
    monkeypatch.setattr(os, "environ", env)
    corp = tmp_path / "corp-ca.pem"
    corp.write_text("-----BEGIN CERTIFICATE-----\nCORP\n-----END CERTIFICATE-----\n")
    settings = get_settings().model_copy(
        update={
            "relay_ca_file": corp,
            "relay_work_dir": tmp_path / "work",
            "relay_proxy": "http://proxy.corp:8080",
            "relay_no_proxy": "localhost,hub.corp",
        }
    )
    done = network.apply(settings)
    bundle = Path(env["SSL_CERT_FILE"])
    body = bundle.read_text()
    assert body.startswith(Path(certifi.where()).read_text()[:200]) and body.endswith(
        "CORP\n-----END CERTIFICATE-----\n"
    )
    assert (
        env["HTTPS_PROXY"] == "http://proxy.corp:8080"
        and env["NO_PROXY"] == "localhost,hub.corp"
    )
    assert len(done) == 2

    # 없는 파일이면 걸지 않는다(로그에 남긴다).
    env.clear()
    missing = settings.model_copy(
        update={"relay_ca_file": tmp_path / "none.pem", "relay_proxy": ""}
    )
    assert network.apply(missing) == [] and "SSL_CERT_FILE" not in env
