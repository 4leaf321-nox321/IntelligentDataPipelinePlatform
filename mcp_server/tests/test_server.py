"""MCP 서버 — **없는 도구가 뜻이다**(켜기 · 적용), 토큰을 그대로 넘긴다, 오류를 사람 말로."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def _ctx(token: str = "Bearer datarelay_pat_x") -> Any:
    request = SimpleNamespace(headers={"authorization": token})
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def test_켜기와_적용_도구는_없다() -> None:
    names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
    assert {"recipe_draft_save", "recipe_test", "source_sample", "target_schema"} <= names
    assert {
        "adapter_kinds",
        "source_discover",
        "pipeline_draft_save",
        "pipeline_test",
        "pipeline_reconcile",
        "pipeline_reconcile_report",
        "pipeline_trace",
    } <= names
    assert not {name for name in names if "activate" in name or "apply" in name}


def test_사용자의_토큰을_그대로_넘기고_mcp_로_표시한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"version": 3, "problems": []})

    monkeypatch.setattr(server, "_TRANSPORT", httpx.MockTransport(handler))
    got = asyncio.run(
        server.recipe_draft_save(
            _ctx(), "plm-model", '{"format": "sp-table/1", "types": []}', "초안"
        )
    )
    assert got == {"version": 3, "problems": []}
    assert seen[0].url.path == "/api/relay/pipelines/plm-model/recipes"
    assert seen[0].headers["Authorization"] == "Bearer datarelay_pat_x"
    assert seen[0].headers["X-Client"] == "mcp"


def test_백엔드의_거절은_코드와_말로(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"error": {"code": "DRL-RELAY-0008", "message": "아직 받은 것이 없습니다"}},
        )

    monkeypatch.setattr(server, "_TRANSPORT", httpx.MockTransport(handler))
    with pytest.raises(server.RelayError, match="409 DRL-RELAY-0008 아직 받은 것이 없습니다"):
        asyncio.run(server.recipe_test(_ctx(), "plm-model", 1))


def test_가이드는_절로_돌려준다() -> None:
    assert server.get_guide("start").startswith("## start")
    assert "sp-table/1" in server.get_guide("recipe")
    assert "되는 것" in server.get_guide("없음")
    assert "source_discover" in server.get_guide("adapter")
    assert "source" in server.get_guide("types")


def test_정의_초안과_탐색은_본문을_그대로_넘긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"ok": True})

    monkeypatch.setattr(server, "_TRANSPORT", httpx.MockTransport(handler))
    asyncio.run(server.source_discover(_ctx(), '{"kind": "file", "location": "/d/*.csv"}'))
    asyncio.run(server.pipeline_draft_save(_ctx(), "mes-lot", {"format": "relay-pipeline/1"}))
    asyncio.run(server.pipeline_test(_ctx(), "mes-lot", 2, recipe_version=1))
    assert [one.url.path for one in seen] == [
        "/api/relay/discover",
        "/api/relay/pipelines/mes-lot/definitions",
        "/api/relay/pipelines/mes-lot/definitions/2/test",
    ]
    assert (
        b'"location": "/d/*.csv"' in seen[0].content
        or b'"location":"/d/*.csv"' in seen[0].content
    )
    assert b'"recipe_version":1' in seen[2].content.replace(b" ", b"")
