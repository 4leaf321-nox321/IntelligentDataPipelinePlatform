"""미해결 작업함 — 멈춘 실행의 참조에 대상의 후보를 붙이고, 승인한 대응은 레시피 판 초안(relay
사전)이 되고, relay 사전은 대표 플랫폼에 보낼 별칭 요청 목록으로 내려받는다.

대상 SP 는 가짜다(`target_objects` · `_resolve_many`). 미해결은 틀의 모양 그대로 작업 폴더에
둔다 — 그 모양을 진짜 변환으로 보는 것은 `tests/unit/test_relay_inbox.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.modules.relay import unresolved
from relay_core.inbox import TargetObject, as_objects
from relay_core.llm import Llm
from relay_core.state import State, Workdir
from tests.api.conftest import Signed
from tests.api.test_relay import _definition, _slug
from tests.api.test_relay_recipes import _pat

MATCH = {"keys": "t_site", "on_missing": "unresolved"}
RECIPE: dict[str, Any] = {
    "format": "sp-table/1",
    "types": [
        {
            "type_slug": "t_model",
            "key": {"column": "MODEL_ID"},
            "label": {"column": "MODEL_NAME"},
            "fields": {"site": {"column": "KIND", "match": MATCH}},
        }
    ],
    "relations": [
        {
            "relation": "r_at",
            "type_slug": "t_model",
            "src": {"key_of": "t_model"},
            "dst": {"column": "KIND", "match": MATCH},
        }
    ],
}
RUN = "runs/20261107T010000000000Z"
UNRESOLVED = [
    {
        "what": "t_model 의 칸 site 값 '서울시험소'",
        "question": "코어에 없는(또는 여럿과 맞는) 식별자입니다 — 어느 것을 가리키나요?",
        "options": [],
        "_source": {"file": "x.csv", "rows": [3, 4]},
    },
    {
        "what": "관계 r_at 의 칸 dst 값 '서울시험소'",
        "question": "코어에 없는(또는 여럿과 맞는) 식별자입니다 — 어느 것을 가리키나요?",
        "options": [],
        "_source": {"file": "x.csv", "rows": [3, 4]},
    },
    {
        "what": "t_model 'M1' 의 칸 label",
        "question": "같은 식별자인데 행마다 값이 다릅니다 — 어느 값이 맞나요?",
        "options": ["모델 1", "모델 하나"],
        "_source": {"file": "x.csv", "rows": [[1], [6]]},
    },
]
SITES = [
    TargetObject("S-SEOUL", "서울 시험소"),
    TargetObject("S-BUSAN", "부산 시험소"),
    TargetObject("S-OLD", "서울시험소", status="deprecated"),
]


@pytest.fixture
def target(monkeypatch: pytest.MonkeyPatch) -> dict[str, set[str]]:
    """가짜 대상 — `t_site` 의 객체 셋. `resolved` 에 넣은 표기는 대상이 이미 푼다."""
    resolved: dict[str, set[str]] = {"t_site": set()}

    def objects(_found: Any, type_slug: str) -> list[TargetObject]:
        assert type_slug == "t_site"
        return SITES

    def resolve(_found: Any, type_slug: str, texts: list[str]) -> list[dict[str, Any]]:
        return [
            {"name": one, "match": "exact" if one in resolved[type_slug] else "none"}
            for one in texts
        ]

    monkeypatch.setattr(unresolved, "target_objects", objects)
    monkeypatch.setattr(unresolved, "_resolve_many", resolve)
    return resolved


def _pipeline(registry: Path) -> str:
    slug = _slug()
    (registry / "match.table.json").write_text(json.dumps(RECIPE), encoding="utf-8")
    _definition(registry, slug, recipe="match.table.json")
    return slug


def _blocked(slug: str) -> None:
    work = Workdir(get_settings().relay_work_dir, slug)
    work.save_state(
        State(history=[{"at": "2026-11-07T01:00:00Z", "event": "blocked", "run": RUN}])
    )
    folder = work.path(RUN)
    folder.mkdir(parents=True)
    (folder / "unresolved.json").write_text(
        json.dumps(UNRESOLVED, ensure_ascii=False), encoding="utf-8"
    )
    stamp = RUN.rsplit("/", 1)[-1]
    work.path("work").mkdir(exist_ok=True)
    work.path(f"work/{stamp}.recipe.table.json").write_text(json.dumps(RECIPE), "utf-8")


def test_작업함은_멈춘_실행의_참조에_대상의_후보를_붙이고_값_갈림은_따로(
    client: TestClient,
    admin: Signed,
    member: Signed,
    registry: Path,
    target: dict[str, set[str]],
) -> None:
    slug = _pipeline(registry)
    url = f"/api/relay/pipelines/{slug}/unresolved"
    assert client.get(url, headers=member.headers).status_code == 403  # 원천 표기가 나온다
    never = client.get(url, headers=admin.headers).json()
    assert never["references"] == [] and "실행한 적이 없습니다" in never["note"]

    _blocked(slug)
    got = client.get(url, headers=admin.headers)
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["run"] == RUN and body["llm"] is False
    assert body["recipe_label"] == "정의 파일의 레시피"
    (one,) = body["references"]  # 같은 대상 타입의 같은 표기는 한 줄
    assert one["target"] == "t_site" and one["text"] == "서울시험소"
    assert one["places"] == ["t_model.site", "관계 r_at.dst"] and one["columns"] == ["KIND"]
    assert one["rows"] == 2 and one["in_dictionary"] is None
    keys = [c["key"] for c in one["candidates"]]
    assert keys[0] == "S-SEOUL" and "S-OLD" not in keys  # 안 씀은 후보가 아니다
    (split,) = body["splits"]
    assert split["options"] == ["모델 1", "모델 하나"] and split["rows"] == 2
    assert body["split_fields"] == [{"type_slug": "t_model", "field": "label", "entities": 1}]

    # 실행이 지나가면 작업함은 빈다(까닭과 함께).
    Workdir(get_settings().relay_work_dir, slug).save_state(
        State(history=[{"at": "2026-11-07T02:00:00Z", "event": "nothing"}])
    )
    later = client.get(url, headers=admin.headers).json()
    assert later["references"] == [] and "멈춘 실행이 없습니다" in later["note"]


def test_승인한_대응은_레시피_판_초안이_되고_대상에_있는_키만_받는다(
    client: TestClient, admin: Signed, registry: Path, target: dict[str, set[str]]
) -> None:
    slug = _pipeline(registry)
    url = f"/api/relay/pipelines/{slug}/unresolved/draft"

    def post(entries: list[dict[str, str]], headers: dict[str, str]) -> httpx.Response:
        answer: httpx.Response = client.post(url, json={"entries": entries}, headers=headers)
        return answer

    wrong = post(
        [{"target": "t_site", "text": "서울시험소", "key": "S-NOWHERE"}], admin.headers
    )
    assert wrong.status_code == 422 and wrong.json()["error"]["code"].endswith("RELAY-0028")
    old = post([{"target": "t_site", "text": "서울시험소", "key": "S-OLD"}], admin.headers)
    assert old.status_code == 422  # 사용 중지된 키로는 안 보낸다
    other = post([{"target": "t_vendor", "text": "x", "key": "S-SEOUL"}], admin.headers)
    assert other.status_code == 422 and "참조 대조하지 않습니다" in other.text

    # 초안까지는 토큰(MCP)도 된다 — 사용은 화면의 사람만(레시피 판의 규칙 그대로).
    saved = post(
        [{"target": "t_site", "text": "서울시험소", "key": "S-SEOUL"}], _pat(client, admin)
    )
    assert saved.status_code == 201, saved.text
    version = saved.json()["version"]
    row = client.get(f"/api/relay/pipelines/{slug}/recipes/{version}", headers=admin.headers)
    body = row.json()
    assert body["status"] == "draft" and "미해결 작업함" in body["note"]
    assert body["body"]["relay_aliases"] == {"t_site": {"서울시험소": "S-SEOUL"}}
    assert body["body"]["types"] == RECIPE["types"]  # 나머지는 지금 레시피 그대로

    # 별칭 요청 목록은 **지금 도는** 레시피의 것 — 초안은 아직이다.
    csv_url = f"/api/relay/pipelines/{slug}/aliases.csv"
    empty = client.get(csv_url, headers=admin.headers)
    assert empty.status_code == 200 and empty.text.lstrip("﻿").count("\n") == 1
    activated = client.post(
        f"/api/relay/pipelines/{slug}/recipes/{version}/activate", headers=admin.headers
    )
    assert activated.status_code == 200, activated.text
    rows = client.get(csv_url, headers=admin.headers).text.lstrip("﻿").splitlines()
    assert rows[1].split(",") == [
        "t_site",
        "S-SEOUL",
        "서울 시험소",
        "서울시험소",
        "plm",
        slug,
        "",
    ]
    # 대표 플랫폼에 별칭이 등록돼 대상이 그 표기를 풀면 — relay 사전에서 지워도 된다.
    target["t_site"].add("서울시험소")
    rows = client.get(csv_url, headers=admin.headers).text.lstrip("﻿").splitlines()
    assert rows[1].endswith(",예")


def test_GLM_제안은_설정했을_때만_대상의_후보_안에서(
    client: TestClient,
    admin: Signed,
    registry: Path,
    target: dict[str, set[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _pipeline(registry)
    url = f"/api/relay/pipelines/{slug}/unresolved/suggest"
    ask = {"target": "t_site", "text": "서울시험소", "column": "KIND"}
    off = client.post(url, json=ask, headers=admin.headers)
    assert off.status_code == 409 and off.json()["error"]["code"].endswith("RELAY-0026")

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        reply = {"key": "S-SEOUL", "confidence": 0.95, "reason": "이름이 같습니다"}
        content = json.dumps(reply, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    fake = Llm(
        "http://glm.test/v1",
        "glm",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(unresolved, "_llm", lambda: fake)
    got = client.post(url, json=ask, headers=admin.headers)
    assert got.status_code == 200, got.text
    assert got.json() == {
        "key": "S-SEOUL",
        "label": "서울 시험소",
        "confidence": 0.95,
        "reason": "이름이 같습니다",
        "note": "",
    }
    offered = seen[0]["response_format"]["json_schema"]["schema"]["properties"]["key"]["enum"]
    assert "S-SEOUL" in offered and "S-OLD" not in offered
    assert as_objects([{"key": ""}]) == []


def test_값이_갈리는_칸은_모으는_칸으로_초안에_더한다(
    client: TestClient, admin: Signed, registry: Path, target: dict[str, set[str]]
) -> None:
    slug = _pipeline(registry)
    url = f"/api/relay/pipelines/{slug}/unresolved/draft"
    asked = {"aggregates": [{"type_slug": "t_model", "field": "label", "mode": "join"}]}
    saved = client.post(url, json=asked, headers=admin.headers)
    assert saved.status_code == 201, saved.text
    row = client.get(
        f"/api/relay/pipelines/{slug}/recipes/{saved.json()['version']}", headers=admin.headers
    ).json()
    assert row["body"]["types"][0]["label"] == {
        "join": {"column": "MODEL_NAME"},
        "separator": "; ",
    }
    assert "모으는 칸 1개" in row["note"]
    wrong = {"aggregates": [{"type_slug": "t_model", "field": "key", "mode": "join"}]}
    refused = client.post(url, json=wrong, headers=admin.headers)
    assert refused.status_code == 422 and "식별자" in refused.text
    assert client.post(url, json={}, headers=admin.headers).status_code == 422


def test_보안_점검_초안에는_대상_토큰을_싣지_않고_타입_이름을_가린다(
    client: TestClient, admin: Signed, registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-11-08 보안 점검 — 초안의 대상(사람이 아직 안 본 주소)에 토큰을 싣는 길, 대상 주소에
    들어가는 타입 이름, 프록시 같은 프로세스의 환경 변수, CSV 의 수식."""
    from app.modules.relay import unresolved as module

    sent: list[str] = []

    def spy(url: str, **_kwargs: Any) -> httpx.Response:
        sent.append(url)
        return httpx.Response(200, json={"items": [], "total": 0})

    monkeypatch.setattr(httpx, "get", spy)
    monkeypatch.setattr(httpx, "post", spy)
    monkeypatch.setattr(module, "_llm", lambda: object())

    # 초안 — 정의 판만 있고 사용한 적이 없다. 대상은 울타리 밖이다.
    slug = _slug()
    draft = {
        "format": "relay-pipeline/1",
        "slug": slug,
        "source": "plm",
        "adapter": {
            "kind": "rest",
            "base_url": "http://127.0.0.1:9",
            "path": "/x",
            "key": "id",
            "columns": {"ID": "id"},
        },
        "target": {"server": "http://evil.example", "token_env": "RELAY_T_TOKEN"},
    }
    saved = client.post(
        f"/api/relay/pipelines/{slug}/definitions", json={"body": draft}, headers=admin.headers
    )
    assert saved.status_code == 201, saved.text
    base = f"/api/relay/pipelines/{slug}"
    ask = {"target": "t_site", "text": "x", "column": "C"}
    refused = client.post(f"{base}/unresolved/suggest", json=ask, headers=admin.headers)
    assert refused.status_code == 409 and refused.json()["error"]["code"].endswith(
        "RELAY-0030"
    )
    entry = {"entries": [{"target": "t_site", "text": "x", "key": "S"}]}
    assert client.post(
        f"{base}/unresolved/draft", json=entry, headers=admin.headers
    ).status_code in (
        409,
        422,
    )
    assert client.get(f"{base}/aliases.csv", headers=admin.headers).status_code == 409
    inbox = client.get(f"{base}/unresolved", headers=admin.headers).json()
    assert "초안" in inbox["note"]
    assert sent == []  # 초안의 대상에는 아무것도 묻지 않았다

    # 사용 중인 파이프라인 — 레시피가 참조 대조하지 않는 타입 · 주소를 바꾸는 이름은 거절한다.
    active = _pipeline(registry)
    url = f"/api/relay/pipelines/{active}/unresolved/suggest"
    for target in ("t_vendor", "../../api/auth/me?x=", "T_SITE"):
        got = client.post(url, json={**ask, "target": target}, headers=admin.headers)
        assert got.status_code == 422, (target, got.text)
    assert sent == []


def test_보안_점검_프로세스의_환경_변수는_비밀_자리로_못_쓴다(
    client: TestClient, admin: Signed, registry: Path
) -> None:
    for name in ("HTTPS_PROXY", "https_proxy", "SSL_CERT_FILE"):
        slug = _slug()
        body = {
            "format": "relay-pipeline/1",
            "slug": slug,
            "source": "plm",
            "adapter": {
                "kind": "rest",
                "base_url": "http://127.0.0.1:9",
                "path": "/x",
                "key": "id",
                "columns": {"ID": "id"},
                "auth": {"kind": "bearer", "token_env": name},
            },
            "target": {"server": "http://127.0.0.1:9", "token_env": "RELAY_T_TOKEN"},
        }
        got = client.post(
            f"/api/relay/pipelines/{slug}/definitions",
            json={"body": body},
            headers=admin.headers,
        )
        assert got.status_code == 403 and got.json()["error"]["code"].endswith("RELAY-0013"), (
            name
        )


def test_보안_점검_별칭_요청_목록은_수식으로_읽히지_않는다() -> None:
    from app.modules.relay.unresolved import _cell

    assert [_cell(one) for one in ("=HYPERLINK(1)", "+1", "-2", "@x", "\tA", "보통")] == [
        "'=HYPERLINK(1)",
        "'+1",
        "'-2",
        "'@x",
        "'\tA",
        "보통",
    ]
