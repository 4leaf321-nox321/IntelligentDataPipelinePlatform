"""datarelay MCP 서버 — AI 가 **레시피를 짓고 시험하는** 자리(ADR 0001 §2).

    ./venv/bin/python server.py          # streamable-http, 기본 127.0.0.1:8082/mcp

- **사용자의 토큰으로** 동작한다 — 들어온 `Authorization` 을 백엔드(`DATARELAY_API`)로
  그대로 넘긴다. 서버에는 자격이 없다. 백엔드가 토큰 이름 · `X-Client: mcp` 를 감사 기록에
  남긴다.
- 도구는 셋으로 갈린다: **보기**(지도 · 상세 · 원천 표본 · 대상 정의 · 레시피) ·
  **짓기**(초안 저장 · 시험 — 대상에 아무것도 안 보낸다) · **미리 보기 실행**(대상 SP 에
  계획을 올린다, 적용은 아님).
- **없는 도구가 뜻이다**: 레시피 켜기 · 계획 적용 · 정의 바꾸기. 켜기는 백엔드가 토큰 요청을
  거절하고(`RELAY-0007`), 적용은 사람이 대상 SP 화면이나 datarelay 화면에서 한다.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP

#: 백엔드 — 운영 유닛은 `PLATFORM_API_BASE` 로 준다(틀의 유닛 그대로), 개발은 8081.
API = (
    os.environ.get("DATARELAY_API")
    or os.environ.get("PLATFORM_API_BASE")
    or "http://127.0.0.1:8081"
).rstrip("/")
GUIDE = Path(__file__).resolve().parent / "guide" / "GUIDE.md"
_JSON_RESPONSE = os.environ.get("MCP_JSON_RESPONSE", "") == "1"

INSTRUCTIONS = (
    "datarelay — 레거시 시스템(PLM · MES)의 데이터를 Standard Platform 에 넣는 중계 플랫폼. "
    "AI 는 레시피(sp-table/1)를 짓고 시험한다. 처음에 get_guide('start') 를 읽어라. "
    "레시피 켜기 · 계획 적용은 사람이 화면에서 한다 — 깨끗한 시험 결과를 보이고 사람에게 "
    "켜 달라고 말한다. 고를 값은 대상 정의(target_schema)의 값으로만 낸다. "
    "짐작으로 채우지 않는다."
)

mcp = FastMCP("datarelay", json_response=_JSON_RESPONSE, instructions=INSTRUCTIONS)

#: 시험이 갈아 끼우는 자리 — 가짜 백엔드를 붙인다. 운영에서는 None(실제 네트워크).
_TRANSPORT: httpx.AsyncBaseTransport | None = None


class RelayError(RuntimeError):
    pass


def _headers(ctx: Context | None) -> dict[str, str]:
    """들어온 MCP 요청의 `Authorization` 만 넘긴다 — 다른 머리는 믿지 않는다."""
    out = {"X-Client": "mcp"}
    req = getattr(getattr(ctx, "request_context", None), "request", None)
    if req is not None:
        value = req.headers.get("authorization")
        if value:
            out["Authorization"] = value
    return out


async def _call(
    ctx: Context | None, method: str, path: str, *, body: Any = None, params: Any = None
) -> Any:
    async with httpx.AsyncClient(base_url=API, timeout=120, transport=_TRANSPORT) as client:
        got = await client.request(
            method, f"/api{path}", json=body, params=params, headers=_headers(ctx)
        )
    try:
        answer = got.json()
    except ValueError:
        answer = {"text": got.text[:500]}
    if got.status_code >= 400:
        error = answer.get("error") if isinstance(answer, dict) else None
        message = (error or {}).get("message") or str(answer)[:500]
        raise RelayError(
            f"{got.status_code} {(error or {}).get('code', '')} {message}".strip()
        )
    return answer


# --- 보기 --------------------------------------------------------------------------------


@mcp.tool()
def get_guide(topic: str = "start") -> str:
    """사용 안내 — topic: start(차례 · 지킬 것) · recipe(sp-table/1 문법 전부)."""
    text = GUIDE.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## ", text)
    for part in parts[1:]:
        name, _, body = part.partition("\n")
        if name.strip() == topic:
            return f"## {name}\n{body}"
    return f"모르는 topic 입니다: {topic}. 되는 것: " + ", ".join(
        p.partition("\n")[0].strip() for p in parts[1:]
    )


@mcp.tool()
async def whoami(ctx: Context) -> Any:
    """이 토큰의 사람 — 이름 · 시스템 관리자인가(레시피를 지으려면 시스템 관리자여야 한다)."""
    me = await _call(ctx, "GET", "/auth/me")
    return {k: me.get(k) for k in ("email", "display_name", "is_system_admin")}


@mcp.tool()
async def relay_map(ctx: Context) -> Any:
    """연결 지도 — 원천 시스템 → API → 파이프라인 → 대상 SP → 타입. 파이프라인마다 상태를
    준다."""
    got = await _call(ctx, "GET", "/relay/map")
    pipelines = [n for n in got["nodes"] if n["kind"] == "pipeline"]
    return {
        "pipelines": [
            {
                "slug": n["label"],
                "status": n["status"],
                "status_label": n["status_label"],
                "sub": n["sub"],
            }
            for n in pipelines
        ],
        "targets": [
            {"label": n["label"], "status": n["status_label"], "sub": n["sub"]}
            for n in got["nodes"]
            if n["kind"] == "target"
        ],
        "problems": got.get("problems") or [],
    }


@mcp.tool()
async def pipeline_get(ctx: Context, slug: str) -> Any:
    """파이프라인 상세 — 원천 · 대상 · 지금 도는 레시피(recipe_label) · 칸 대응(lineage) ·
    상태 · 이력."""
    got = await _call(ctx, "GET", f"/relay/pipelines/{slug}")
    got["history"] = got.get("history", [])[:10]
    return got


@mcp.tool()
async def source_sample(ctx: Context, slug: str, limit: int = 20) -> Any:
    """원천의 표본 — 레시피가 읽는 **평평한 열** 그대로(마지막 실행이 받아 둔 미러에서)."""
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/sample", params={"limit": limit})


@mcp.tool()
async def target_schema(ctx: Context, slug: str) -> Any:
    """대상 SP 의 정의 — 레시피가 만드는 타입의 칸 · 자료형 · 고를 값(enum_options) · 참조
    대상."""
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/target-schema")


@mcp.tool()
async def recipe_versions(ctx: Context, slug: str) -> Any:
    """레시피의 판들 — draft · active · retired. 켜진 판이 없으면 정의 파일의 레시피가 돈다."""
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/recipes")


@mcp.tool()
async def recipe_get(ctx: Context, slug: str, version: int | None = None) -> Any:
    """레시피 본문 — version 을 안 주면 정의 파일의 것(출발점)."""
    if version is None:
        return await _call(ctx, "GET", f"/relay/pipelines/{slug}/recipe-file")
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/recipes/{version}")


# --- 짓기 · 시험 -------------------------------------------------------------------------


@mcp.tool()
async def recipe_draft_save(
    ctx: Context, slug: str, recipe: dict[str, Any] | str, note: str = ""
) -> Any:
    """레시피 초안을 저장한다 — 새 판 번호가 생긴다. **켜진 판은 그대로다.** recipe 는
    sp-table/1 JSON(객체 또는 그 글). 돌려준 problems(어댑터가 안 내는 열 …)를 먼저
    고친다."""
    body = json.loads(recipe) if isinstance(recipe, str) else recipe
    return await _call(
        ctx, "POST", f"/relay/pipelines/{slug}/recipes", body={"body": body, "note": note}
    )


@mcp.tool()
async def recipe_test(ctx: Context, slug: str, version: int) -> Any:
    """그 판을 미러에 돌려 본다 — **대상에 아무것도 안 보낸다.** clean · rows(타입마다 행 수) ·
    unresolved(미해결) · would_send(켜면 생성 · 고침 · 무덤) · report(값을 가린 패턴)."""
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/recipes/{version}/test")


# --- 미리 보기 실행 — 적용은 사람이 ----------------------------------------------------------


@mcp.tool()
async def pipeline_run(ctx: Context, slug: str) -> Any:
    """원천을 받아 대상 SP 에 **미리 보기 계획**을 올린다(작업). 적용하지 않는다 — 계획은
    사람이 대상 SP 화면이나 datarelay 화면에서 적용한다. 진행은 job_status 로."""
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/run", body={})


@mcp.tool()
async def job_status(ctx: Context, job_id: str) -> Any:
    """작업의 상태 · 결과 요약(summary)."""
    got = await _call(ctx, "GET", f"/jobs/{job_id}")
    return {k: got.get(k) for k in ("id", "kind", "status", "error", "result", "finished_at")}


# --- 기동 ---------------------------------------------------------------------------------
#
# **이 블록은 파일의 맨 끝이어야 한다** — `run` 이 블로킹이라 그 뒤의 `@mcp.tool()` 은 등록되지
# 않는다.
if __name__ == "__main__":
    mcp.settings.host = os.environ.get("MCP_HOST", "127.0.0.1")
    mcp.settings.port = int(os.environ.get("MCP_PORT", "8082"))
    if mcp.settings.host not in ("127.0.0.1", "localhost", "::1"):
        from mcp.server.transport_security import TransportSecuritySettings

        allowed = [
            h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()
        ]
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=bool(allowed),
            allowed_hosts=allowed,
            allowed_origins=[],
        )
    mcp.run(transport="streamable-http")
