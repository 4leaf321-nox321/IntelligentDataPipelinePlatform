"""받을 수 있는 원천의 모양 — 목록 펴기 · 여러 칸 식별자 · 다음 쪽 주소 · Link 머리글 · POST
조회 · 목록 → 상세 · 호출 간격 · 인증(질의 문자열의 키 · oauth2 client credentials).

원천은 `httpx.MockTransport` — 시험이 응답을 정한다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from relay_core.adapters import rest
from relay_core.adapters.file import FileAdapter, FileSource
from relay_core.adapters.odata import ODataAdapter, ODataSource
from relay_core.adapters.rest import Auth, RestAdapter, RestSource, SourceError
from relay_core.definition import DefinitionError, check_adapter

Handler = Callable[[httpx.Request], httpx.Response]


def _rest(handler: Handler, **over: Any) -> RestAdapter:
    spec: dict[str, Any] = {
        "base_url": "http://plm.test",
        "path": "/api/orders",
        "key": "id",
        "columns": {"ORDER": "id"},
        "paging": "none",
        "updated_at": None,
        "deleted": None,
    }
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return RestAdapter(RestSource(**{**spec, **over}), client=client, sleep=lambda _s: None)


ORDERS = [
    {"id": "O1", "site": "P1", "lines": [{"no": 1, "item": "A"}, {"no": 2, "item": "B"}]},
    {"id": "O2", "site": "P2", "lines": [{"no": 1, "item": "C"}]},
    {"id": "O3", "site": "P1", "lines": []},
]


def test_목록_속_목록을_원소마다_행으로_펴고_여러_칸_식별자를_잇는다() -> None:
    adapter = _rest(
        lambda _r: httpx.Response(200, json={"items": ORDERS}),
        key=["id", "lines.no"],
        explode="lines",
        columns={"ORDER": "id", "SITE": "site", "LINE": "lines.no", "ITEM": "lines.item"},
    )
    got = adapter.fetch(None)
    assert [one.key for one in got.records] == ["O1|1", "O1|2", "O2|1"]  # 빈 목록은 행이 없다
    assert got.records[1].values == {"ORDER": "O1", "SITE": "P1", "LINE": "2", "ITEM": "B"}

    # 식별자가 원소를 가리지 않으면 한 항목의 원소들이 겹쳐 마지막 것만 남는다 — 멈춘다.
    wrong = _rest(lambda _r: httpx.Response(200, json={"items": ORDERS}), explode="lines")
    with pytest.raises(SourceError, match="식별자"):
        wrong.fetch(None)
    # 펼치지 않아도 여러 칸 식별자는 된다.
    plain = _rest(lambda _r: httpx.Response(200, json={"items": ORDERS}), key=["site", "id"])
    assert [one.key for one in plain.fetch(None).records] == ["P1|O1", "P2|O2", "P1|O3"]


def test_응답이_준_다음_쪽_주소를_따라가되_다른_곳은_따라가지_않는다() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "page=2" in str(request.url):
            return httpx.Response(200, json={"items": [ORDERS[1]], "next": None})
        # 상대 주소 — 원천 주소 기준으로 잇는다.
        return httpx.Response(200, json={"items": [ORDERS[0]], "next": "/api/orders?page=2"})

    got = _rest(handler, paging="next_url").fetch(None)
    assert [one.key for one in got.records] == ["O1", "O2"]
    assert seen[1] == "http://plm.test/api/orders?page=2"  # 받은 주소의 질의 그대로

    away = _rest(
        lambda _r: httpx.Response(200, json={"items": ORDERS, "next": "http://evil.test/x"}),
        paging="next_url",
    )
    with pytest.raises(SourceError, match="다른 곳"):
        away.fetch(None)


def test_Link_머리글의_다음_쪽을_따라간다() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(200, json=[ORDERS[2]])
        nxt = (
            '<http://plm.test/api/orders?cursor=c2>; rel="next", '
            '<http://plm.test/x>; rel="last"'
        )
        return httpx.Response(200, json=[ORDERS[0]], headers={"Link": nxt})

    got = _rest(handler, paging="link", rows_path="").fetch(None)
    assert [one.key for one in got.records] == ["O1", "O3"]


def test_POST_조회는_본문_틀에_쪽과_증분을_채운다() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        bodies.append(body)
        page = body["page"]
        items = ORDERS[(page - 1) * 2 : page * 2]
        return httpx.Response(200, json={"data": {"rows": items}})

    adapter = _rest(
        handler,
        method="POST",
        rows_path="data.rows",
        paging="page",
        page_size=2,
        body={"page": "{page}", "size": "{page_size}", "filter": "changed > {since}"},
    )
    got = adapter.fetch("2026-11-01")
    assert [one.key for one in got.records] == ["O1", "O2", "O3"]
    assert bodies[0] == {"page": 1, "size": 2, "filter": "changed > 2026-11-01"}
    assert bodies[1]["page"] == 2  # 덜 찬 쪽에서 끝


def test_목록에서_받은_항목마다_상세를_받아_붙인다() -> None:
    asked: list[str] = []
    details = {"O1": {"spec": {"weight": 3}}, "O2": {"spec": {"weight": 5}}}

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path.startswith("/api/orders/"):
            return httpx.Response(
                200, json={"data": details[request.url.path.rsplit("/", 1)[-1]]}
            )
        return httpx.Response(200, json={"items": ORDERS[:2]})

    adapter = _rest(
        handler,
        detail_path="/api/orders/{id}",
        detail_rows_path="data",
        columns={"ORDER": "id", "WEIGHT": "detail.spec.weight"},
    )
    got = adapter.fetch(None)
    assert [one.values["WEIGHT"] for one in got.records] == ["3", "5"]
    assert asked == ["/api/orders", "/api/orders/O1", "/api/orders/O2"]


def test_호출_사이를_띄운다() -> None:
    waits: list[float] = []
    now = [100.0]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"items": []}))
    )
    adapter = RestAdapter(
        RestSource(
            base_url="http://plm.test",
            path="/x",
            key="id",
            columns={},
            paging="none",
            min_interval=2.0,
        ),
        client=client,
        sleep=waits.append,
        clock=lambda: now[0],
    )
    adapter.fetch(None)
    adapter.fetch(None)  # 시계가 그대로면 2초를 기다려야 한다
    assert waits == [2.0]


def test_질의_문자열의_키를_싣고_오류_글에서_지운다() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.params.get("api_key")
        if key != "s3cret-key":
            return httpx.Response(401, text="no key")
        if request.url.params.get("fail"):
            return httpx.Response(400, text=f"bad request for api_key={key}")
        return httpx.Response(200, json={"items": ORDERS[:1]})

    auth = Auth(kind="query", param="api_key", token="s3cret-key")
    assert [one.key for one in _rest(handler, auth=auth).fetch(None).records] == ["O1"]
    with pytest.raises(SourceError) as caught:
        _rest(handler, auth=auth, params={"fail": "1"}).fetch(None)
    assert "s3cret-key" not in str(caught.value) and "***" in str(caught.value)


def test_oauth2_는_토큰을_받아_만료_전까지_다시_쓴다(monkeypatch: pytest.MonkeyPatch) -> None:
    rest._TOKENS.clear()
    issued: list[dict[str, str]] = []

    def post(url: str, *, data: dict[str, str], timeout: float) -> httpx.Response:
        issued.append(data)
        return httpx.Response(
            200,
            json={"access_token": f"tok-{len(issued)}", "expires_in": 3600},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(rest, "TOKEN_POST", post)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"items": []})

    auth = Auth(
        kind="oauth2", token="client-secret", user="relay", token_url="http://idp.test/token"
    )
    adapter = _rest(handler, auth=auth)
    adapter.fetch(None)
    adapter.fetch(None)
    assert seen == ["Bearer tok-1", "Bearer tok-1"] and len(issued) == 1
    assert issued[0] == {
        "grant_type": "client_credentials",
        "client_id": "relay",
        "client_secret": "client-secret",
    }
    # 만료되면 다시 받는다.
    key = ("http://idp.test/token", "relay", "")
    rest._TOKENS[key] = (rest._TOKENS[key][0], 0.0)
    adapter.fetch(None)
    assert seen[-1] == "Bearer tok-2"

    def refused(url: str, *, data: dict[str, str], timeout: float) -> httpx.Response:
        return httpx.Response(
            401, text="invalid client-secret", request=httpx.Request("POST", url)
        )

    rest._TOKENS.clear()
    monkeypatch.setattr(rest, "TOKEN_POST", refused)
    with pytest.raises(SourceError) as caught:
        adapter.fetch(None)
    assert "client-secret" not in str(caught.value)
    rest._TOKENS.clear()


def test_OData_와_파일도_펴고_여러_칸_식별자를_잇는다(tmp_path: Path) -> None:
    body = {
        "value": [
            {"Id": "O1", "Lines": [{"No": 1, "Item": "A"}, {"No": 2, "Item": "B"}]},
        ]
    }
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=body))
    )
    odata = ODataAdapter(
        ODataSource(
            service_url="http://plm.test/odata",
            entity_set="Orders",
            key=["Id", "Lines.No"],
            columns={"ID": "Id", "ITEM": "Lines.Item"},
            explode="Lines",
        ),
        client=client,
        sleep=lambda _s: None,
    )
    assert [one.key for one in odata.fetch(None).records] == ["O1|1", "O1|2"]

    path = tmp_path / "orders.json"
    path.write_text(json.dumps(ORDERS), encoding="utf-8")
    files = FileAdapter(
        FileSource(
            location=str(path),
            key=["id", "lines.no"],
            columns={"ITEM": "lines.item"},
            explode="lines",
        ),
        roots=[tmp_path],
    )
    got = files.fetch(None)
    assert [one.key for one in got.records] == ["O1|1", "O1|2", "O2|1"]
    assert got.records[2].values == {"ITEM": "C"}


def test_정의의_모양을_먼저_본다() -> None:
    base: dict[str, Any] = {
        "kind": "rest",
        "base_url": "http://plm.test",
        "path": "/x",
        "key": "id",
        "columns": {"ID": "id"},
    }

    def bad(**over: Any) -> str:
        with pytest.raises(DefinitionError) as caught:
            check_adapter({**base, **over})
        return str(caught.value)

    assert "모르는 칸" in bad(auth={"kind": "bearer", "token_env": "T", "realm": "x"})
    assert "token_url" in bad(auth={"kind": "oauth2", "token_env": "T"})
    assert "param" in bad(auth={"kind": "query", "token_env": "T", "param": ""})
    assert "kind" in bad(auth={"kind": "ntlm"})
    assert "GET · POST" in bad(method="DELETE")
    assert "body" in bad(method="POST")
    assert "paging" in bad(paging="scroll")
    assert "식별자" in bad(explode="lines")
    assert "min_interval" in bad(min_interval=600)
    assert "detail_path" in bad(detail_path="http://evil.test/x/{id}")
    assert "key" in bad(key=[])
    kind, _ = check_adapter(
        {
            **base,
            "key": ["id", "lines.no"],
            "explode": "lines",
            "paging": "next_url",
            "method": "POST",
            "body": {"page": "{page}"},
            "auth": {
                "kind": "oauth2",
                "token_env": "T",
                "user": "relay",
                "token_url": "https://idp.test/token",
                "scope": "read",
            },
        }
    )
    assert kind == "rest"


def test_oauth2_의_토큰_주소도_울타리_안이고_비밀이_묶인다() -> None:
    from app.modules.relay import guard
    from app.shared.errors import AppError

    spec = {
        "base_url": "http://plm.test",
        "auth": {
            "kind": "oauth2",
            "token_env": "PLM_SECRET",
            "token_url": "http://idp.test/t",
        },
    }
    fenced = guard.Fence(hosts=frozenset({"plm.test"}))
    with pytest.raises(AppError, match="token_url"):
        guard.check_source("rest", spec, fenced)
    # 토큰 주소도 울타리 안이면 된다 — 비밀이 다른 원천에 묶여 있으면 안 된다.
    opened = guard.Fence(hosts=frozenset({"plm.test", "idp.test"}))
    guard.check_source("rest", spec, opened)
    bound = guard.Fence(
        hosts=frozenset({"plm.test", "idp.test"}),
        bound={"PLM_SECRET": frozenset({("원천", "plm.test")})},
    )
    with pytest.raises(AppError, match="PLM_SECRET"):
        guard.check_source("rest", spec, bound)
