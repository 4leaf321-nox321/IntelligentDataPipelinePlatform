"""datarelay MCP 서버 — AI 가 **레시피를 짓고 시험하는** 자리(ADR 0001 §2).

    ./venv/bin/python server.py          # streamable-http, 기본 127.0.0.1:8082/mcp

- **사용자의 토큰으로** 동작한다 — 들어온 `Authorization` 을 백엔드(`DATARELAY_API`)로
  그대로 넘긴다. 서버에는 자격이 없다. 백엔드가 토큰 이름 · `X-Client: mcp` 를 감사 기록에
  남긴다.
- 도구는 다섯으로 갈린다: **보기**(지도 · 상세 · 원천 표본 · 대상 정의 · 레시피 · 정의) ·
  **원천 탐색**(어댑터 설정으로 원천에 닿아 칸을 본다 — 저장 안 함) · **짓기**(레시피 ·
  정의의 초안 저장 · 시험 — 대상에 아무것도 안 보낸다) · **미리 보기 실행**(대상 SP 에
  계획을 올린다, 적용은 아님) · **대조**(대상에 지금 있는 것과 견준다 — 읽기만, 보정 계획은
  미리보기).
- **없는 도구가 뜻이다**: 레시피 켜기 · 정의 켜기 · 계획 적용. 켜기는 백엔드가 토큰 요청을
  거절하고(`RELAY-0007` · `RELAY-0014`), 적용은 사람이 대상 SP 화면이나 datarelay 화면에서
  한다.
- 탐색 · 정의 시험은 서버가 바깥에 닿는 일이다 — 백엔드의 울타리(부를 수 있는 주소 · 읽을 수
  있는 폴더 · DB 꺼짐 · 플랫폼 자신의 비밀 이름)를 지난다.
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
    "AI 는 레시피(sp-table/1)와 파이프라인 정의(relay-pipeline/1 — 원천 어댑터 · 대상)를 "
    "짓고 시험한다. 처음에 get_guide('start') 를 읽어라 — 새 원천을 붙이면 "
    "get_guide('adapter'). 켜기 · 계획 적용은 사람이 화면에서 한다 — 깨끗한 시험 결과를 "
    "보이고 사람에게 켜 달라고 말한다. 비밀은 정의에 적지 않고 환경 변수 이름만 적는다. "
    "고를 값은 대상 정의(target_schema)의 값으로만 낸다. "
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
    """사용 안내 — topic: start(차례 · 지킬 것) · recipe(sp-table/1 문법 전부) ·
    adapter(새 원천 붙이기 — 탐색 · 정의 초안 · 시험) · types(대상에 타입이 없을 때) ·
    reconcile(대상과의 대조 — 어긋남 읽기 · 보정 계획)."""
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


# --- 원천 · 정의 — 새 원천을 붙인다(어댑터 설정) -------------------------------------------


@mcp.tool()
async def adapter_kinds(ctx: Context) -> Any:
    """어댑터 종류(rest · odata · file · xml)마다 — 설명, 꼭 적을 칸(required), 모든 칸의
    기본값 · 설명, auth 의 칸. 비밀 칸은 없다 — 비밀은 환경 변수의 이름(token_env)만 적는다."""
    return await _call(ctx, "GET", "/relay/adapter-kinds")


@mcp.tool()
async def source_discover(ctx: Context, adapter: dict[str, Any] | str) -> Any:
    """어댑터 설정으로 원천에 닿아 본다 — **저장하지 않는다.** 식별자(key) · 열(columns)은 아직
    몰라도 된다. 돌려주는 found: 칸의 자리(paths)와 보기 값, 열 이름 제안(columns —
    그대로 어댑터의 columns 로 쓸 수 있다), OData 면 엔티티 셋 목록(entity_sets), DB 면 표의
    열. 부를 수 있는 주소 · 폴더 밖이면 403 이 까닭을 말한다 — 사람에게 전한다."""
    body = json.loads(adapter) if isinstance(adapter, str) else adapter
    return await _call(ctx, "POST", "/relay/discover", body={"adapter": body})


@mcp.tool()
async def pipeline_definitions(ctx: Context, slug: str) -> Any:
    """정의의 판들 — draft · active · retired. 켜진 판이 같은 slug 의 정의 파일을 이긴다. 새
    파이프라인이면 빈 목록이다."""
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/definitions")


@mcp.tool()
async def pipeline_definition_get(ctx: Context, slug: str, version: int | None = None) -> Any:
    """정의 본문 — version 을 안 주면 같은 slug 의 정의 파일(출발점, 없으면 body 가 null)."""
    if version is None:
        return await _call(ctx, "GET", f"/relay/pipelines/{slug}/definition-file")
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/definitions/{version}")


@mcp.tool()
async def pipeline_draft_save(
    ctx: Context, slug: str, definition: dict[str, Any] | str, note: str = ""
) -> Any:
    """파이프라인 정의(relay-pipeline/1) 초안을 저장한다 — 새 판 번호가 생긴다. 새 slug 면
    새 파이프라인이 초안으로 선다(켜기 전까지 돌지 않는다). **켜진 판은 그대로다.**
    `recipe` 칸은 적지 않는다(레시피는 recipe_draft_save). 비밀 값을 적으면 거절된다.
    돌려준 problems(울타리 밖 주소 · 다른 source · 등록 안 된 대상 …)를 먼저 읽는다."""
    body = json.loads(definition) if isinstance(definition, str) else definition
    return await _call(
        ctx, "POST", f"/relay/pipelines/{slug}/definitions", body={"body": body, "note": note}
    )


@mcp.tool()
async def pipeline_test(
    ctx: Context, slug: str, version: int, recipe_version: int | None = None, limit: int = 10
) -> Any:
    """그 정의 판으로 원천을 **받아 보고**(전량 · 저장 안 함) · 대상 SP(닿나 · 토큰 · 레시피가
    만드는 타입이 있나 · 잠금 managed_by)를 보고 · 레시피로 바꿔 본다 — **대상에 아무것도 안
    보낸다.** 레시피는 recipe_version › 켜진 레시피 판 › 정의 파일의 것. source.always_empty
    (늘 빈 열) · target.types_missing · recipe.unresolved 를 읽고 고친다. 원천이 크면 앞
    줄만 받는다(source.capped — 그때 「켜면 보낼 것」 은 세지 않는다)."""
    return await _call(
        ctx,
        "POST",
        f"/relay/pipelines/{slug}/definitions/{version}/test",
        body={"recipe_version": recipe_version, "limit": limit},
    )


@mcp.tool()
async def pipeline_draft_discard(ctx: Context, slug: str, version: int) -> Any:
    """정의 초안을 버린다 — **초안만**(켜진 · 지난 판은 못 버린다). 시험하다 만 초안을
    치운다. 새 파이프라인의 초안을 다 버리면 지도에서 빠진다."""
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/definitions/{version}/discard")


@mcp.tool()
async def recipe_draft_discard(ctx: Context, slug: str, version: int) -> Any:
    """레시피 초안을 버린다 — **초안만**."""
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/recipes/{version}/discard")


# --- 미리 보기 실행 — 적용은 사람이 ----------------------------------------------------------


@mcp.tool()
async def pipeline_run(ctx: Context, slug: str) -> Any:
    """원천을 받아 대상 SP 에 **미리 보기 계획**을 올린다(작업). 적용하지 않는다 — 계획은
    사람이 대상 SP 화면이나 datarelay 화면에서 적용한다(자동 적용은 타이머의 실행에만).
    진행은 job_status 로."""
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/run", body={})


@mcp.tool()
async def pipeline_reconcile(ctx: Context, slug: str, plan: bool = False) -> Any:
    """대상 SP 에 **지금 있는 것**을 지난번 적용된 출력과 대조한다(작업 — 대상은 읽기만).
    빠짐 · 안 씀(사용 중지) · 다름 · 대상에만을 센다. `plan=True` 면 어긋난 줄을 다시 보내는
    **보정 계획**(미리보기)까지 올린다 — 적용은 사람이. 진행은 job_status, 결과는
    pipeline_reconcile_report."""
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/reconcile", body={"plan": plan})


@mcp.tool()
async def pipeline_reconcile_report(ctx: Context, slug: str) -> Any:
    """마지막 대조의 보고서 — 타입마다 빠짐 · 안 씀 · 다름(키 → 칸) · 대상에만의 식별자
    전부."""
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/reconcile")


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
