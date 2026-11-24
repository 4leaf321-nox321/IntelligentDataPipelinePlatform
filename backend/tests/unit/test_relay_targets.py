"""대상 SP 상태의 저장(`targets.py`) · 일일 요약의 차례와 본문(`digest.py`) ·
웹훅(`webhook.py`).

DB 없이 본다 — 조회 함수는 가짜로 넘기고, 저장 자리는 임시 폴더다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.modules.relay import digest, targets, webhook
from app.modules.relay.schemas import OverviewItem, RelayOverview


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    settings = get_settings()
    monkeypatch.setattr(settings, "relay_work_dir", tmp_path / "work")
    monkeypatch.setattr(settings, "relay_target_fresh_seconds", 600)
    return tmp_path


class Probe:
    """대상 둘 — `a` 는 코어 현황까지 답하고, `b` 는 연결되지 않는다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, server: str, token: str, path: str) -> tuple[int, Any]:
        self.calls.append((server, path))
        if server == "http://b":
            return 0, None
        if path == "/api/health":
            return 200, {"slug": "hub", "version": "v0.4.101", "app": "Hub"}
        return 200, {"consumers": [{"name": "ra", "owner": "품질", "last_used_at": "x"}]}


def test_저장값이_새것이면_조회하지_않고_오래되면_그_대상만(work: Path) -> None:
    probe = Probe()
    first = targets.current({"http://a": "tok", "http://b": ""}, probe)
    assert first["http://a"]["reachable"] and first["http://a"]["version"] == "v0.4.101"
    assert first["http://a"]["consumers"] == [
        {"name": "ra", "owner": "품질", "last_used_at": "x"}
    ]
    assert not first["http://b"]["reachable"]
    saved = (work / targets.FILE).read_text(encoding="utf-8")
    assert "tok" not in saved  # 토큰은 저장하지 않는다

    probe.calls.clear()
    again = targets.current({"http://a": "tok", "http://b": ""}, probe)
    assert probe.calls == [] and again == first

    body = json.loads(saved)
    body["http://b"]["checked_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    (work / targets.FILE).write_text(json.dumps(body), encoding="utf-8")
    targets.current({"http://a": "tok", "http://b": ""}, probe)
    assert {server for server, _path in probe.calls} == {"http://b"}

    probe.calls.clear()
    targets.current({"http://a": "tok"}, probe, force=True)
    assert ("http://a", "/api/health") in probe.calls


def test_신선도_0이면_늘_직접_조회(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "relay_target_fresh_seconds", 0)
    probe = Probe()
    targets.current({"http://a": ""}, probe)
    targets.current({"http://a": ""}, probe)
    assert probe.calls == [("http://a", "/api/health")] * 2


def test_일일_요약의_차례() -> None:
    day = datetime(2026, 11, 24, 8, 0).astimezone()
    morning = day.replace(hour=8, minute=29)
    after = day.replace(hour=8, minute=31)
    yesterday = after - timedelta(days=1)
    assert not digest.is_due(morning, yesterday, "08:30")
    assert digest.is_due(after, yesterday, "08:30")
    assert not digest.is_due(after, after, "08:30")
    assert not digest.is_due(after, None, "")  # 빈 값이면 미사용


def test_일일_요약의_본문() -> None:
    items = [
        OverviewItem(slug=f"p{n}", status="failed", status_label="실패", reason="연결 불가")
        for n in range(digest.LISTED + 2)
    ]
    items.append(
        OverviewItem(slug="drift", status="ok", status_label="정상", reason="대상 불일치 3건")
    )
    summary = RelayOverview(
        generated_at="",
        total=20,
        counts={"failed": digest.LISTED + 2, "ok": 10},
        labels={"failed": "실패", "ok": "정상"},
        attention=items,
    )
    title, body = digest.message(summary)
    assert title == f"중계 일일 요약 — 확인 필요 {len(items)}건"
    assert body.splitlines()[0] == f"전체 20개 중 실패 {digest.LISTED + 2} · 대상 불일치 1"
    assert "- p0: 실패 (연결 불가)" in body
    assert "외 3건은 파이프라인 목록에서 확인하세요." in body
    assert len(body) <= digest.BODY_MAX


def test_웹훅은_설정했을_때만_보내고_주소를_로그에_남기지_않는다(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "relay_alert_webhook_url", "")
    assert webhook.send(kind="k", title="t") is False

    sent: list[dict[str, Any]] = []

    def post(url: str, json: dict[str, Any], timeout: float) -> httpx.Response:
        sent.append(json)
        return httpx.Response(200)

    monkeypatch.setattr(
        settings, "relay_alert_webhook_url", "https://chat.example/hook/SECRET"
    )
    monkeypatch.setattr(settings, "public_url", "https://relay.example")
    monkeypatch.setattr(httpx, "post", post)
    assert webhook.send(
        kind="relay.run.failed", title="p 실행 실패", body="연결 불가", link="/relay/x"
    )
    assert sent[0]["link"] == "https://relay.example/relay/x"
    assert sent[0]["text"].endswith("연결 불가\nhttps://relay.example/relay/x")

    def down(url: str, json: dict[str, Any], timeout: float) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", down)
    assert webhook.send(kind="k", title="t") is False
    assert "SECRET" not in caplog.text
