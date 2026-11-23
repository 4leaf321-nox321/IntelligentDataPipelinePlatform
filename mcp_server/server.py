"""datarelay MCP 서버 — AI 가 **레시피를 작성하고 시험하는** 자리(ADR 0001 §2).

    ./venv/bin/python server.py          # streamable-http, 기본 127.0.0.1:8082/mcp

- **사용자의 토큰으로** 동작한다 — 들어온 `Authorization` 을 백엔드(`DATARELAY_API`)로
  그대로 넘긴다. 서버에는 자격이 없다. 백엔드가 토큰 이름 · `X-Client: mcp` 를 감사 기록에
  남긴다.
- 도구는 다섯으로 갈린다: **보기**(지도 · 상세 · 원천 표본 · 대상 정의 · 레시피 · 정의) ·
  **원천 탐색**(어댑터 설정으로 원천에 닿아 칸을 본다 — 저장 안 함) · **작성**(레시피 ·
  정의의 초안 저장 · 시험 — 대상에 아무것도 안 보낸다) · **미리 보기 실행**(대상 SP 에
  계획을 생성한다, 적용은 아님) · **대조**(대상에 지금 있는 것과 견준다 — 읽기만, 보정 계획은
  미리 보기).
- **없는 도구가 뜻이다**: 레시피 판 사용 · 정의 판 사용 · 계획 적용. 사용은 백엔드가 토큰
  요청을 거절하고(`RELAY-0007` · `RELAY-0014`), 적용은 사람이 대상 SP 화면이나 datarelay
  화면에서 한다.
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
    "**설정 값은 AI 가 고른다** — 사람은 원천 주소 · 원천 이름 · 대상 타입을 말하고, "
    "비밀(토큰 · 비밀번호)을 화면에서 넣고, 시험이 깨끗한 판을 「사용」 · 계획을 「적용」 할 "
    "뿐이다. 처음에 get_guide('start') 를 읽어라. 새 원천은 get_guide('adapter') — API "
    "지침서가 있으면 source_spec 으로 읽고, pipeline_scaffold 로 정의 · 레시피 초안과 "
    "시험을 한 번에 만든 뒤 "
    "결과의 next 를 따른다. 비밀이 비었으면(secrets_needed · secrets_status) 사람에게 화면 › "
    "중계 › 비밀 에서 넣어 달라고 말한다 — 값은 대화로 받지 않는다. 정의에는 비밀의 이름만 "
    "적는다. 고를 값은 대상 정의(target_schema)의 값으로만 낸다. 짐작으로 채우지 않는다."
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


#: 경로에 들어가는 글자 — slug · 판 번호 · 작업 id 는 모두 이 안이다. 도구가 받은 값을
#: 경로에 끼우므로, `?` · `#` · `%` · `..` 를 허용하면
#: `pipeline_run(slug="plm-model/apply?")` 처럼 **없는 도구(적용)를 부르는 경로**가 된다 —
#: AI 는 원천 데이터에 심은 지시에 넘어갈 수 있다(2026-11-06 보안 검토).
_SAFE_PATH = re.compile(r"(/[A-Za-z0-9_-]+)+")


async def _call(
    ctx: Context | None,
    method: str,
    path: str,
    *,
    body: Any = None,
    params: Any = None,
    text: bool = False,
) -> Any:
    """백엔드 API 한 번. `text` 면 JSON 이 아닌 글(CSV)을 그대로 — 경로 끝 `.csv` 만."""
    suffix = ""
    if text and path.endswith(".csv"):
        path, suffix = path[: -len(".csv")], ".csv"
    if not _SAFE_PATH.fullmatch(path):
        raise RelayError(
            f"경로에 쓸 수 없는 글자가 있습니다: {path!r} — slug 는 영문 · 숫자 · - · _ 만 "
            "됩니다."
        )
    async with httpx.AsyncClient(base_url=API, timeout=120, transport=_TRANSPORT) as client:
        got = await client.request(
            method, f"/api{path}{suffix}", json=body, params=params, headers=_headers(ctx)
        )
    if text and got.status_code < 400:
        return got.text[:20000]
    try:
        answer = got.json()
    except ValueError:
        answer = {"text": got.text[:500]}
    if got.status_code >= 400:
        error = answer.get("error") if isinstance(answer, dict) else None
        message = (error or {}).get("message") or str(answer)[:500]
        # 요청 ID — 서버 로그에서 그 요청을 찾는 열쇠. 본문의 칸은 AI 에게 안 보이니 글에
        # 붙인다.
        rid = (error or {}).get("request_id") or got.headers.get("x-request-id") or ""
        tail = f" (요청 ID {rid})" if rid and rid != "-" and rid not in message else ""
        raise RelayError(
            f"{got.status_code} {(error or {}).get('code', '')} {message}{tail}".strip()
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
    """이 토큰의 사람 — 이름 · 시스템 관리자인가(레시피를 작성하려면 시스템 관리자여야
    한다)."""
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
    """파이프라인 상세 — 원천 · 대상 · 사용 중인 레시피(recipe_label) · 칸 대응(lineage) ·
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
    대상, 사이드바 묶음(`groups`)과 타입마다 든 묶음(`nav_group_of`)."""
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/target-schema")


@mcp.tool()
async def recipe_versions(ctx: Context, slug: str) -> Any:
    """레시피의 판들 — draft · active(사용 중) · retired · discarded(폐기). 사용 중인 판이
    없으면 정의 파일의 레시피를 사용한다."""
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/recipes")


@mcp.tool()
async def recipe_get(ctx: Context, slug: str, version: int | None = None) -> Any:
    """레시피 본문 — version 을 안 주면 정의 파일의 것(출발점)."""
    if version is None:
        return await _call(ctx, "GET", f"/relay/pipelines/{slug}/recipe-file")
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/recipes/{version}")


# --- 작성 · 시험 -------------------------------------------------------------------------


@mcp.tool()
async def recipe_draft_save(
    ctx: Context, slug: str, recipe: dict[str, Any] | str, note: str = ""
) -> Any:
    """레시피 초안을 저장한다 — 새 판 번호가 생긴다. **사용 중인 판은 그대로다.** recipe 는
    sp-table/1 JSON(객체 또는 그 글). 돌려준 problems(어댑터가 내지 않는 열 …)를 먼저
    수정한다."""
    body = json.loads(recipe) if isinstance(recipe, str) else recipe
    return await _call(
        ctx, "POST", f"/relay/pipelines/{slug}/recipes", body={"body": body, "note": note}
    )


@mcp.tool()
async def recipe_test(
    ctx: Context, slug: str, version: int, sample_rows: list[dict[str, Any]] | None = None
) -> Any:
    """그 판을 미러에 돌려 본다 — **대상에 아무것도 안 보낸다.** clean · rows(타입마다 행 수) ·
    unresolved(미해결) · would_send(사용하면 보낼 것 — 생성 · 변경 · 삭제) · report(값을
    가린 패턴). 받은 것이 없으면 `sample_rows`(원천이 보내는 항목의 모양 — 1,000개까지)로 —
    정의의 식별자 · 열로 펴서 바꿔 본다(그때 would_send 는 세지 않는다)."""
    body = {"sample_rows": sample_rows} if sample_rows is not None else None
    return await _call(
        ctx, "POST", f"/relay/pipelines/{slug}/recipes/{version}/test", body=body
    )


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
    """정의의 판들 — draft · active(사용 중) · retired · discarded(폐기). 사용 중인 판이 같은
    slug 의 정의 파일을 이긴다. 새 파이프라인이면 빈 목록이다."""
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
    새 파이프라인이 초안으로 선다(사람이 사용하기 전까지 실행되지 않는다). **사용 중인 판은
    그대로다.**
    `recipe` 칸은 적지 않는다(레시피는 recipe_draft_save). 비밀 값을 적으면 거절된다.
    돌려준 problems(울타리 밖 주소 · 다른 source · 등록 안 된 대상 …)를 먼저 읽는다."""
    body = json.loads(definition) if isinstance(definition, str) else definition
    return await _call(
        ctx, "POST", f"/relay/pipelines/{slug}/definitions", body={"body": body, "note": note}
    )


@mcp.tool()
async def pipeline_test(
    ctx: Context,
    slug: str,
    version: int,
    recipe_version: int | None = None,
    limit: int = 10,
    sample_rows: list[dict[str, Any]] | None = None,
) -> Any:
    """그 정의 판으로 원천을 **받아 보고**(전량 · 저장 안 함) · 대상 SP(닿나 · 토큰 · 레시피가
    만드는 타입이 있나 · 잠금 managed_by)를 보고 · 레시피로 바꿔 본다 — **대상에 아무것도 안
    보낸다.** 레시피는 recipe_version › 사용 중인 레시피 판 › 정의 파일의 것.
    source.always_empty(늘 빈 열) · target.types_missing · recipe.unresolved 를 읽고 수정한다.
    원천이 크면 앞 줄만 받는다(source.capped — 그때 「사용하면 보낼 것」 은 세지 않는다).
    **보내 주는 원천(push)** 은 사용 전에는 받은 것이 없다 — `sample_rows`(원천 담당에게 받은
    보낼 모양의 항목들)로 끝까지 시험하거나, 원천이 받는 자리에 `?trial=1` 로 시험 수신한 것을
    읽는다(source.inbox 가 「시험 수신함」)."""
    body: dict[str, Any] = {"recipe_version": recipe_version, "limit": limit}
    if sample_rows is not None:
        body["sample_rows"] = sample_rows
    return await _call(
        ctx, "POST", f"/relay/pipelines/{slug}/definitions/{version}/test", body=body
    )


@mcp.tool()
async def pipeline_draft_discard(ctx: Context, slug: str, version: int) -> Any:
    """정의 초안을 폐기한다 — **초안만**(사용 중인 · 지난 판은 폐기하지 못한다). 시험하다 만
    초안을 정리한다. 새 파이프라인의 초안을 다 폐기하면 지도에서 사라진다."""
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/definitions/{version}/discard")


@mcp.tool()
async def recipe_draft_discard(ctx: Context, slug: str, version: int) -> Any:
    """레시피 초안을 폐기한다 — **초안만**."""
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/recipes/{version}/discard")


# --- 미리 보기 실행 — 적용은 사람이 ----------------------------------------------------------


@mcp.tool()
async def pipeline_run(ctx: Context, slug: str, full: bool = False) -> Any:
    """원천을 받아 대상 SP 에 **미리 보기 계획**을 생성한다(작업). 적용하지 않는다 — 계획은
    사람이 대상 SP 화면이나 datarelay 화면에서 적용한다(자동 적용은 타이머의 실행에만).
    진행은 job_status 로. 결과가 「받기 점검에서 멈췄습니다」(대량 삭제 · 빈 칸)이면 원천 ·
    어댑터를 살펴 사람에게 알린다 — 「확인 후 진행」 은 사람이 화면에서만 한다. `full` 이면
    워터마크를 두고 전량을 다시 받는다(어댑터의 칸을 고친 뒤 — 안 온 것은 삭제로 읽힌다)."""
    body = {"full": True} if full else {}
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/run", body=body)


@mcp.tool()
async def pipeline_reconcile(ctx: Context, slug: str, plan: bool = False) -> Any:
    """대상 SP 에 **지금 있는 것**을 지난번 적용된 출력과 대조한다(작업 — 대상은 읽기만).
    빠짐 · 안 씀(사용 중지) · 다름 · 대상에만을 센다. `plan=True` 면 어긋난 줄을 다시 보내는
    **보정 계획**(미리 보기)까지 생성한다 — 적용은 사람이. 진행은 job_status, 결과는
    pipeline_reconcile_report."""
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/reconcile", body={"plan": plan})


@mcp.tool()
async def pipeline_reconcile_report(ctx: Context, slug: str) -> Any:
    """마지막 대조의 보고서 — 타입마다 빠짐 · 안 씀 · 다름(키 → 칸) · 대상에만의 식별자
    전부."""
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/reconcile")


@mcp.tool()
async def pipeline_trace(ctx: Context, slug: str, type_slug: str, key: str) -> Any:
    """거꾸로 보기 — 대상 객체 하나(`type_slug` · `key`)의 값이 어느 원천 행 · 칸 · 레시피
    규칙에서 왔나. 칸마다 기준 출력의 값 · 규칙 · 원천의 원래 값(행 번호), 원천 행들, 닿는
    관계, 마지막 대조에서의 상태(`reconcile.state` · 다른 칸). 대조가 「다름」 을 말하면
    이것으로 까닭을 가린다 — 레시피의 값 모양 탓이면 레시피를, 원천 탓이면 원천 담당을,
    대상에서 바뀐 것이면 보정 계획을. 읽기만 한다."""
    params = {"type_slug": type_slug, "key": key}
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/trace", params=params)


@mcp.tool()
async def pipeline_unresolved(ctx: Context, slug: str) -> Any:
    """미해결 작업함 — 미해결로 멈춘 실행의 **참조**(대상에서 못 찾은 원천 표기 — 칸 · 행
    수 · 대상의 후보)와 **값 갈림**(같은 식별자인데 행마다 값이 다름). 참조의 대응을
    제안하려면 후보 안에서만 골라 레시피의 `relay_aliases` 에 더한 초안을 `recipe_draft_save`
    로 저장한다(사용은 사람이 화면에서). 값 갈림은 레시피(모으는 칸)나 원천의 문제다 —
    `split_fields` 는 객체마다 갈리는 칸(모으는 칸 `join` · `collect` 로 바꿀 후보, 안내의
    `recipe`). 읽기만 한다."""
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/unresolved")


@mcp.tool()
async def pipeline_unresolved_draft(
    ctx: Context,
    slug: str,
    entries: list[dict[str, str]] | None = None,
    aggregates: list[dict[str, str]] | None = None,
    note: str = "",
) -> Any:
    """미해결 작업함의 「레시피 초안으로 저장」 — 지금 레시피에 **relay 사전**(`entries` —
    `[{"target": 대상 타입, "text": 원천 표기, "key": 대상 키}]`, key 는 pipeline_unresolved 의
    후보 안에서만)을 더하고, 값이 갈리는 칸을 **모으는 칸**으로 바꿔(`aggregates` —
    `[{"type_slug", "field", "mode": "join" | "collect", "separator": "; "}]`) 레시피 판 초안
    하나로 저장한다. 레시피 JSON 을 직접 고치지 않아도 된다. 사용은 사람이 화면에서.
    relay 사전은 임시 지름길이다 — 정본은 SP 의 별칭(alias_requests)."""
    body = {"entries": entries or [], "aggregates": aggregates or [], "note": note}
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/unresolved/draft", body=body)


@mcp.tool()
async def alias_requests(ctx: Context, slug: str) -> Any:
    """별칭 요청 목록(CSV 글) — 지금 레시피의 relay 사전을 대표 플랫폼에 미확인 별칭으로
    제안할 목록. 「대상이 이미 푸나」 가 예인 줄은 relay 사전에서 걷는다. 읽기만 한다."""
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/aliases.csv", text=True)


@mcp.tool()
async def pipeline_trend(ctx: Context, slug: str, limit: int = 30) -> Any:
    """실행 추이 — 실행마다 받은 행 · 미러 · 계획 건수 · 걸린 시간 · 멈춘 단계(최근 것부터
    `limit` 줄). 원천이 갑자기 줄거나 실패가 이어지는지 볼 때. 읽기만 한다."""
    return await _call(ctx, "GET", f"/relay/pipelines/{slug}/trend", params={"limit": limit})


@mcp.tool()
async def type_draft(
    ctx: Context,
    type_slug: str,
    key_column: str,
    columns: dict[str, list[str]],
    label: str = "",
    label_column: str = "",
    system: str = "",
    nav_group: str | dict[str, Any] | None = None,
) -> Any:
    """**SP 타입 정의 초안** — 대상에 받을 타입이 없을 때 원천 표본(`columns` — 열 → 보기
    값들, source_discover 의 paths · source_sample 로 만든다)으로 타입 정의와 그 레시피 첫 판을
    짓는다. 저장 · 전송하지 않는다 — type_plan 으로 대상에 계획을 올린다. `nav_group` 은
    사이드바 묶음 — 대상에 있는 묶음의 slug(target_schema 의 groups), 또는 새 묶음
    `{slug, label, icon}`(정의의 groups 로 함께 만든다)."""
    body = {
        "type_slug": type_slug,
        "label": label,
        "key_column": key_column,
        "label_column": label_column,
        "columns": columns,
        "system": system,
        "nav_group": nav_group,
    }
    return await _call(ctx, "POST", "/relay/type-draft", body=body)


@mcp.tool()
async def type_plan(
    ctx: Context, slug: str, ontology: dict[str, Any] | str, note: str = ""
) -> Any:
    """**대상에 타입 계획을 올린다** — SP 타입 정의 초안(`type_draft` · 한 번에 초안의
    `ontology`)을 그 파이프라인의 원천 이름(`source` — 그 원천의 것으로 잠긴다)과 함께 대상
    SP 의 **미리 보기 계획**으로. 대상 SP 의 MCP 를 따로 붙이지 않아도 된다. 적용은 사람이
    대상 SP 화면 「작업」 에서(`link`) — 적용 뒤 pipeline_test 로 types_missing 이 비었나
    본다. 대상 토큰에 ontology:write 범위가 없으면 거절된다 — 그 말을 사람에게 전한다.
    **사이드바 묶음**: 타입의 `nav_group_slug` 는 대상에 있는 묶음(target_schema 의
    groups)이거나 `ontology.groups` 로 함께 만드는 묶음이어야 한다. 이미 있는 타입을 다른
    묶음으로 옮기려면 `{"types": [{"slug": …, "nav_group_slug": …}]}` 만 — SP 는 보낸 칸만
    고친다."""
    body = {
        "ontology": json.loads(ontology) if isinstance(ontology, str) else ontology,
        "note": note,
    }
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/type-plan", body=body)


@mcp.tool()
async def source_spec(ctx: Context, url: str, auth: dict[str, Any] | None = None) -> Any:
    """원천의 **API 지침서**를 읽는다 — OpenAPI · Swagger(JSON · YAML), OData `$metadata`,
    WSDL, 문서 화면(HTML 이면 그 안의 지침서 주소 후보). 서버 주소 · 인증 방식(relay `auth`
    모양으로) · 목록 조회마다 질의 · 목록 자리(rows_path) · 항목의 칸 · 쪽 넘김 · 증분 질의의
    후보를 준다 — 이것으로 어댑터를 고르고 source_discover · pipeline_scaffold 로 확인한다.
    인증은 원천과 같은 모양(비밀은 token_env 이름만). 주소는 원천과 같은 울타리 안만."""
    body = {"url": url, "auth": auth}
    return await _call(ctx, "POST", "/relay/source-spec", body=body)


@mcp.tool()
async def pipeline_scaffold(
    ctx: Context,
    slug: str,
    adapter: dict[str, Any] | str,
    source: str,
    type_slug: str,
    key: str | list[str] | None = None,
    label: str = "",
    target_from: str = "",
    target: dict[str, Any] | None = None,
    columns: dict[str, str] | None = None,
    system: str = "",
    type_label: str = "",
    schedule: dict[str, Any] | None = None,
    note: str = "",
    split: bool = True,
    sample_rows: list[dict[str, Any]] | None = None,
    nav_group: str | dict[str, Any] | None = None,
) -> Any:
    """**한 번에 초안** — 원천의 주소 · 인증(`adapter`, 비밀은 token_env 이름만 — 모양은 비워도
    된다) · 원천 이름(`source` — plm · mes) · 대상 타입(`type_slug`)만으로: 탐색 → 행 자리 ·
    쪽 넘김 · 식별자 · 변경 시각 · 삭제 표시를 단서로 채운 **정의 판 초안** → 대상 타입의 칸에
    이름으로 잇는 **레시피 판 초안**(대상에 타입이 없으면 SP 타입 정의 초안) → **시험**.
    대상은 `target_from`(대상이 같은 파이프라인 slug) · `target` · 하나뿐인 대상 순.
    status: needs_secrets(사람에게 secrets_needed 를 화면에서 넣어 달라고 — 아무것도 저장하지
    않았다) · needs_key(candidates 중 식별자를 골라 key 로 다시) · discover_failed ·
    drafted · type_missing. **보내 주는 원천(push)** 은 받은 것이 없으니 `sample_rows`(보낼
    모양의 항목) 또는 `columns`({열: 자리})로 짓는다. **한 행을 여러 타입으로 나눈다**
    (`split`, 기본) — 대상의 정의를 따라, 같은 원천이 주인인 참조 타입(과제 · 프로젝트 …)은
    열 묶음(`task.*` · `TASK_*`)에서 객체로 만들어 key_of 로 잇고, 관계 종류는 맞는 열이
    하나면 선으로. recipe.types(타입마다 묶음 · 식별자 · 이름) · relations ·
    relation_candidates · skipped(다른 파이프라인이 채워 참조만)를 본다. 결과의 chosen(고른
    값과 까닭) · required_missing(꼭 받는데 빈 칸 — 먼저) · unmatched · references(참조 칸 —
    match 로) · trial 을 읽고 recipe_draft_save · pipeline_draft_save 로 고친 뒤 `next` 를
    따른다. 사용하지 않는다 — 사용은 사람이 화면에서. 대상에 타입이 없으면(`type_missing`)
    `nav_group`(사이드바 묶음 — 있는 묶음 slug 또는 새 묶음)을 따르고, 안 주면 대상의 묶음에서
    고른다(recipe.nav_group 에 고른 것과 까닭)."""
    body: dict[str, Any] = {
        "adapter": json.loads(adapter) if isinstance(adapter, str) else adapter,
        "source": source,
        "type_slug": type_slug,
        "key": key,
        "label": label,
        "target_from": target_from,
        "target": target,
        "columns": columns,
        "system": system,
        "type_label": type_label,
        "schedule": schedule,
        "note": note,
        "split": split,
        "sample_rows": sample_rows,
        "nav_group": nav_group,
    }
    return await _call(ctx, "POST", f"/relay/pipelines/{slug}/scaffold", body=body)


@mcp.tool()
async def secrets_status(ctx: Context) -> Any:
    """정의가 쓰는 비밀마다 상태 — `screen`(화면에서 넣음) · `server`(서버 설정) ·
    `missing`(비어 있음)과 쓰는 곳. **값은 없다.** missing 이면 사람에게 화면 › 중계 › 비밀
    에서 넣어 달라고 말한다(값을 대화로 받지 않는다). `missing` 에 비어 있는 이름, `link` 에
    사람에게 건넬 화면 자리."""
    got = await _call(ctx, "GET", "/relay/secrets")
    # 목록 그대로 내면 MCP 가 원소마다 따로 실어 「무엇이 비었나」 가 흩어진다 — 묶어서 낸다.
    listed = got if isinstance(got, list) else []
    missing = [one["name"] for one in listed if one.get("status") == "missing"]
    return {
        "secrets": listed,
        "missing": missing,
        "link": {name: f"/relay/secrets?name={name}" for name in missing},
    }


@mcp.tool()
async def allowed_hosts(ctx: Context) -> Any:
    """탐색 · 시험이 닿아도 되는 주소 — `source`(원천) · `target`(대상 SP)마다 서버 설정 ·
    화면에서 더한 것 · 사용 중인 정의의 것. 403(RELAY-0010)으로 막히면 이것을 보고
    **사람에게** 화면 › 중계 › 허용 주소 에 그 주소를 더해 달라고 말한다(바꾸는 도구는 없다 —
    재시작은 없다)."""
    return await _call(ctx, "GET", "/relay/allowed-hosts")


@mcp.tool()
async def secrets_request(
    ctx: Context, pipeline: str, names: list[str], role: str = "원천 인증"
) -> Any:
    """정의를 짓기 전에 필요한 비밀 **이름**을 비밀 목록에 올린다(값 없음) — 사람이 화면에서
    바로 넣을 수 있게. role: 원천 인증 · 대상 토큰 · 인증서 키 암호 · 보내는 쪽 비밀. 이름은
    대문자 · 숫자 · _ (예: MES_API_TOKEN). 플랫폼 자신의 설정 이름은 거절된다."""
    body = {"pipeline": pipeline, "names": names, "role": role}
    return await _call(ctx, "POST", "/relay/secrets/requests", body=body)


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
