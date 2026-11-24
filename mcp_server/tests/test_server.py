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
            json={"error": {"code": "DRL-RELAY-0008", "message": "수신 데이터가 없습니다."}},
        )

    monkeypatch.setattr(server, "_TRANSPORT", httpx.MockTransport(handler))
    with pytest.raises(server.RelayError, match="409 DRL-RELAY-0008 수신 데이터가 없습니다"):
        asyncio.run(server.recipe_test(_ctx(), "plm-model", 1))


def test_slug_로_다른_경로를_부르지_못한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`plm-model/apply?` 같은 slug 가 「없는 도구」(적용)의 경로가 되지 않게(2026-11-06 보안
    검토) — 백엔드에 닿기 전에 멈춘다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setattr(server, "_TRANSPORT", httpx.MockTransport(handler))
    for slug in ("plm-model/apply?", "../jobs/1/apply", "plm-model#", "plm model", "a%2Fb"):
        with pytest.raises(server.RelayError, match="사용할 수 없는 문자"):
            asyncio.run(server.recipe_test(_ctx(), slug, 1))
    assert seen == []
    asyncio.run(server.recipe_test(_ctx(), "plm-model", 1))
    assert seen[0].url.path == "/api/relay/pipelines/plm-model/recipes/1/test"


def test_가이드는_절로_돌려준다() -> None:
    assert server.get_guide("start").startswith("## start")
    assert "sp-table/1" in server.get_guide("recipe")
    assert "사용할 수 있는 topic" in server.get_guide("없음")
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


def test_값을_모르고도_쓰는_도구들은_제_길로_간다(monkeypatch: pytest.MonkeyPatch) -> None:
    """한 번에 초안 · 지침서 · 비밀 상태와 요청 · 미해결 초안 · 별칭 목록(CSV 글) · 추이 ·
    타입 정의 초안. 비밀을 **넣는** 도구는 없다 — 사람이 화면에서 넣는다."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith(".csv"):
            return httpx.Response(200, text="타입,표기,키\nt_site,서울시험소,S-SEOUL\n")
        if request.url.path.endswith("/secrets"):
            return httpx.Response(
                200,
                json=[
                    {"name": "MES_API_TOKEN", "status": "missing"},
                    {"name": "HUB_TOKEN", "status": "screen"},
                ],
            )
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(server, "_TRANSPORT", httpx.MockTransport(handler))
    ctx = _ctx()
    asyncio.run(server.source_spec(ctx, "https://mes/openapi.json"))
    asyncio.run(
        server.pipeline_scaffold(
            ctx, "mes-lots", '{"kind": "rest", "base_url": "https://mes"}', "mes", "mes_lot"
        )
    )
    status = asyncio.run(server.secrets_status(ctx))
    asyncio.run(server.secrets_request(ctx, "mes-lots", ["MES_API_TOKEN"]))
    asyncio.run(server.pipeline_unresolved_draft(ctx, "mes-lots", entries=[{"target": "t"}]))
    csv = asyncio.run(server.alias_requests(ctx, "mes-lots"))
    asyncio.run(server.pipeline_trend(ctx, "mes-lots", limit=5))
    asyncio.run(server.type_draft(ctx, "mes_lot", "LOT_ID", {"LOT_ID": ["L1"]}))
    asyncio.run(server.relay_overview(ctx))
    assert [(one.method, one.url.path) for one in seen] == [
        ("POST", "/api/relay/source-spec"),
        ("POST", "/api/relay/pipelines/mes-lots/scaffold"),
        ("GET", "/api/relay/secrets"),
        ("POST", "/api/relay/secrets/requests"),
        ("POST", "/api/relay/pipelines/mes-lots/unresolved/draft"),
        ("GET", "/api/relay/pipelines/mes-lots/aliases.csv"),
        ("GET", "/api/relay/pipelines/mes-lots/trend"),
        ("POST", "/api/relay/type-draft"),
        ("GET", "/api/relay/overview"),
    ]
    assert csv.startswith("타입,표기,키")
    # 목록을 그대로 내면 MCP 가 원소마다 따로 싣는다 — 「무엇이 비었나」 를 묶어서.
    assert status["missing"] == ["MES_API_TOKEN"] and len(status["secrets"]) == 2
    assert status["link"] == {"MES_API_TOKEN": "/relay/secrets?name=MES_API_TOKEN"}
    assert b'"base_url":"https://mes"' in seen[1].content.replace(b" ", b"")
    names = {tool.name for tool in server.mcp._tool_manager.list_tools()}
    assert not {
        name for name in names if "secret" in name and ("put" in name or "set" in name)
    }
    with pytest.raises(server.RelayError, match="사용할 수 없는 문자"):
        asyncio.run(server.alias_requests(ctx, "../jobs"))


def test_백엔드의_오류는_요청_ID_를_글에_붙인다(monkeypatch: pytest.MonkeyPatch) -> None:
    """서버 오류의 요청 ID 는 본문의 칸에 있다 — AI 가 보는 글에 붙여야 사람이 신고한다."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={
                "error": {
                    "code": "DRL-COMMON-0500",
                    "message": "서버 오류가 발생했습니다.",
                    "request_id": "abc123def456",
                }
            },
            headers={"X-Request-ID": "abc123def456"},
        )

    monkeypatch.setattr(server, "_TRANSPORT", httpx.MockTransport(handler))
    with pytest.raises(server.RelayError, match=r"요청 ID abc123def456"):
        asyncio.run(server.relay_map(_ctx()))


#: AI 가 읽고 사람에게 그대로 전하는 말에서 물러난 말(docs/용어.md 「바뀐 말」, 2026-11-24).
_RETIRED = (
    "「",
    "」",
    "걷어내",
    "다시 켜",
    "한 번에 초안",
    "지침서",
    "받기 점검",
    "작업함",
    "값 갈림",
    "사이드바 묶음",
    "쪽 넘김",
    "견본 행",
    "어긋남",
    "지금 실행",
)


def test_안내와_도구_설명은_화면과_같은_말을_쓴다() -> None:
    """안내문 · 도구 설명 · 가이드는 AI 가 사람에게 그대로 옮긴다 — 옛 말이 남으면 화면과
    다른 이름으로 안내한다."""
    texts = {"instructions": server.INSTRUCTIONS, "GUIDE.md": server.GUIDE.read_text("utf-8")}
    for tool in server.mcp._tool_manager.list_tools():
        texts[tool.name] = tool.description or ""
    found = {name: [word for word in _RETIRED if word in text] for name, text in texts.items()}
    assert {name: words for name, words in found.items() if words} == {}
