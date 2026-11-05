"""정의의 판 — AI(MCP)는 원천을 **탐색**하고 정의 **초안을 짓고 시험**한다, **켜는 것은 사람이
화면에서**. 탐색 · 시험은 서버가 바깥에 닿는 일이라 울타리(`guard`)를 먼저 지난다.

원천은 임시 폴더의 CSV(파일 어댑터), 대상은 아무도 안 듣는 자리다 — 시험은 대상에 아무것도
보내지 않으므로 「못 닿음」 이라고 말하는지만 본다.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from tests.api.conftest import Signed, work_until
from tests.api.test_relay import NOWHERE, _definition, _slug
from tests.api.test_relay_recipes import _draft, _pat
from tests.unit.test_relay_core import RECIPE


@pytest.fixture
def drop(tmp_path: Path, registry: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """읽어도 되는 폴더(`RELAY_FILE_ROOTS`)와 그 안의 원천 CSV."""
    folder = tmp_path / "drop"
    folder.mkdir()
    (folder / "models.csv").write_text(
        "MODEL_ID,MODEL_NAME,KIND,BASE,삭제\nM1,모델 1,A,,\nM2,모델 2,B,M1,\nM3,모델 3,A,,Y\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(get_settings(), "relay_file_roots", str(folder))
    return folder


def _body(slug: str, drop: Path, **over: Any) -> dict[str, Any]:
    return {
        "format": "relay-pipeline/1",
        "slug": slug,
        "source": "mes",
        "system": "MES",
        "adapter": {
            "kind": "file",
            "location": str(drop / "models.csv"),
            "key": "MODEL_ID",
            "deleted": "삭제",
        },
        "target": {"name": "제조 데이터허브", "server": NOWHERE, "token_env": "RELAY_T_TOKEN"},
        **over,
    }


def _save(client: TestClient, who: dict[str, str], slug: str, body: dict[str, Any]) -> Any:
    return client.post(
        f"/api/relay/pipelines/{slug}/definitions",
        json={"body": body, "note": "시험"},
        headers=who,
    )


def _code(answer: Any) -> str:
    return str(answer.json()["error"]["code"])


def test_종류는_코드의_설명을_보이고_비밀_칸은_없다(client: TestClient, admin: Signed) -> None:
    got = client.get("/api/relay/adapter-kinds", headers=admin.headers)
    assert got.status_code == 200, got.text
    body = got.json()
    kinds = {one["kind"]: one for one in body["kinds"]}
    assert set(kinds) == {"rest", "odata", "file", "xml", "sql"}
    assert kinds["odata"]["required"] == ["service_url", "entity_set", "key", "columns"]
    names = {f["name"] for one in kinds.values() for f in one["fields"]}
    assert not names & {"dsn", "token", "password", "secret"}
    sheet = next(f for f in kinds["file"]["fields"] if f["name"] == "sheet")
    assert sheet["default"] == "" and sheet["doc"]  # 칸 아래의 설명 글이 그대로 온다
    assert kinds["sql"]["disabled"] is True
    assert "token_env" in {one["name"] for one in body["auth"]}


def test_탐색은_울타리_안만_닿는다(client: TestClient, admin: Signed, drop: Path) -> None:
    mcp = _pat(client, admin)
    found = client.post(
        "/api/relay/discover",
        json={"adapter": {"kind": "file", "location": str(drop / "*.csv")}},
        headers=mcp,
    )
    assert found.status_code == 200, found.text
    assert found.json()["ok"] and "MODEL_NAME" in found.json()["found"]["columns"]

    def refused(adapter: dict[str, Any]) -> Any:
        return client.post("/api/relay/discover", json={"adapter": adapter}, headers=mcp)

    outside = refused({"kind": "file", "location": "/etc/passwd"})
    assert outside.status_code == 403 and _code(outside).endswith("RELAY-0011")
    far = refused({"kind": "rest", "base_url": "http://evil.test", "path": "/x"})
    assert far.status_code == 403 and _code(far).endswith("RELAY-0010")
    sql = refused({"kind": "sql", "dsn_env": "MES_DB", "table": "v_lot"})
    assert sql.status_code == 403 and _code(sql).endswith("RELAY-0012")
    own = refused(
        {
            "kind": "rest",
            "base_url": "http://127.0.0.1:9",
            "path": "/x",
            "auth": {"kind": "bearer", "token_env": "DATABASE_URL"},
        }
    )
    assert own.status_code == 403 and _code(own).endswith("RELAY-0013")
    leaked = refused({"kind": "rest", "base_url": "http://u:pw@127.0.0.1:9", "path": "/x"})
    assert leaked.status_code == 422


def test_새_파이프라인은_초안으로_서고_토큰은_시험까지만(
    client: TestClient,
    admin: Signed,
    drop: Path,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slug = _slug()
    mcp = _pat(client, admin)
    saved = _save(client, mcp, slug, _body(slug, drop, recipe="x.table.json"))
    assert saved.status_code == 201, saved.text
    assert saved.json()["version"] == 1
    assert any("recipe 칸은 뺐습니다" in one for one in saved.json()["problems"])

    nodes = client.get("/api/relay/map", headers=admin.headers).json()["nodes"]
    (pipeline,) = [one for one in nodes if one["kind"] == "pipeline" and one["label"] == slug]
    assert pipeline["status"] == "draft"
    run = client.post(f"/api/relay/pipelines/{slug}/run", headers=admin.headers)
    assert run.status_code == 409 and _code(run).endswith("RELAY-0015")

    tried = client.post(f"/api/relay/pipelines/{slug}/definitions/1/test", headers=mcp)
    assert tried.status_code == 200, tried.text
    result = tried.json()
    assert result["source"]["ok"] and result["source"]["rows"] == 3
    assert result["source"]["deleted"] == 1 and "always_empty" in result["source"]
    assert result["target"]["reachable"] is False
    assert "레시피가 아직 없습니다" in result["recipe"]["note"]

    # 새 파이프라인에도 레시피 초안을 지을 수 있다 — 정의 시험이 원천을 받아 그것으로 바꿔 본다
    assert _draft(client, mcp, slug, RECIPE).status_code == 201
    early = client.post(f"/api/relay/pipelines/{slug}/recipes/1/test", headers=mcp)
    assert early.status_code == 409 and "pipeline_test" in early.json()["error"]["message"]
    tried = client.post(
        f"/api/relay/pipelines/{slug}/definitions/1/test",
        json={"recipe_version": 1, "limit": 1},
        headers=mcp,
    ).json()
    assert len(tried["source"]["sample"]) == 1
    assert tried["recipe"]["clean"] and tried["recipe"]["rows"] == {"plm_model": 2}
    assert tried["recipe"]["would_send"]["objects"]["plm_model"]["created"] == 2

    refused = client.post(f"/api/relay/pipelines/{slug}/definitions/1/activate", headers=mcp)
    assert refused.status_code == 403 and _code(refused).endswith("RELAY-0014")

    on = client.post(
        f"/api/relay/pipelines/{slug}/definitions/1/activate", headers=admin.headers
    )
    assert on.status_code == 200 and on.json()["status"] == "active", on.text
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["definition_label"] == "판 1" and not detail["draft"]
    assert any(
        "레시피가 없습니다" in one for one in detail["problems"]
    )  # 레시피 판은 아직 꺼짐

    client.post(f"/api/relay/pipelines/{slug}/recipes/1/activate", headers=admin.headers)
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["problems"] == [] and detail["status"] == "never"
    assert detail["recipe_label"] == "판 1" and detail["types"] == ["plm_model"]

    # 켜진 판이 돈다 — 원천(파일)은 읽고, 대상에 못 닿아 실패한다(정의 · 레시피는 풀렸다)
    monkeypatch.setenv("RELAY_T_TOKEN", "x")
    queued = client.post(f"/api/relay/pipelines/{slug}/run", headers=admin.headers)
    assert queued.status_code == 202, queued.text
    job_id = queued.json()["job_id"]
    work_until(db, uuid.UUID(job_id))
    job = client.get(f"/api/jobs/{job_id}", headers=admin.headers).json()
    assert job["status"] == "failed" and "초안" not in (job["error"] or ""), job
    listed = client.get(f"/api/relay/pipelines/{slug}/definitions", headers=admin.headers)
    assert [(one["version"], one["status"]) for one in listed.json()] == [(1, "active")]
    assert listed.json()[0]["summary"].startswith("file models.csv → 제조 데이터허브")


def test_켜진_판이_같은_slug_의_정의_파일을_이긴다(
    client: TestClient, admin: Signed, registry: Path, drop: Path
) -> None:
    slug = _slug()
    _definition(registry, slug)
    body = _body(slug, drop, source="plm")
    saved = _save(client, admin.headers, slug, body)
    assert saved.status_code == 201, saved.text
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["definition_label"] == "파일" and detail["adapter_kind"] == "odata"

    client.post(f"/api/relay/pipelines/{slug}/definitions/1/activate", headers=admin.headers)
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["definition_label"] == "판 1" and detail["adapter_kind"] == "file"
    assert detail["recipe_label"] == "파일" and detail["types"] == ["plm_model"]
    assert detail["problems"] == []  # 레시피는 같은 slug 의 정의 파일 것을 빌린다

    other = _save(client, admin.headers, slug, _body(slug, drop))  # source mes — 잠금과 어긋남
    assert any("source" in one for one in other.json()["problems"])
    file = client.get(f"/api/relay/pipelines/{slug}/definition-file", headers=admin.headers)
    assert file.json()["body"]["adapter"]["kind"] == "odata"


def test_비밀_모르는_칸_다른_slug_는_거절한다(
    client: TestClient, admin: Signed, drop: Path
) -> None:
    slug = _slug()
    leaked = _body(slug, drop)
    leaked["adapter"]["auth"] = {"kind": "bearer", "token": "abc"}
    assert _save(client, admin.headers, slug, leaked).status_code == 422
    assert _save(client, admin.headers, slug, _body(slug, drop, extra=1)).status_code == 422
    assert _save(client, admin.headers, "plm-other", _body(slug, drop)).status_code == 422
    own = copy.deepcopy(_body(slug, drop))
    own["target"]["token_env"] = "JWT_SECRET"
    answer = _save(client, admin.headers, slug, own)
    assert answer.status_code == 403 and _code(answer).endswith("RELAY-0013")
    assert (
        client.get(f"/api/relay/pipelines/{slug}/definitions", headers=admin.headers).json()
        == []
    )


def test_울타리_밖_파일을_읽는_정의는_지도에_깨짐으로_선다(
    client: TestClient, admin: Signed, registry: Path, drop: Path
) -> None:
    slug = _slug()
    _definition(
        registry,
        slug,
        adapter={"kind": "file", "location": "/etc/hosts", "key": "MODEL_ID"},
    )
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["status"] == "broken"
    assert any("RELAY_FILE_ROOTS" in one for one in detail["problems"])


def test_같은_대상의_같은_타입을_다른_파이프라인이_채우면_시험이_말한다(
    client: TestClient, admin: Signed, registry: Path, drop: Path
) -> None:
    running = _slug()
    _definition(registry, running)  # 정의 파일 — plm_model 을 NOWHERE 에 넣는다
    slug = _slug()
    assert _save(client, admin.headers, slug, _body(slug, drop)).status_code == 201
    assert _draft(client, admin.headers, slug, RECIPE).status_code == 201
    tried = client.post(
        f"/api/relay/pipelines/{slug}/definitions/1/test",
        json={"recipe_version": 1},
        headers=admin.headers,
    ).json()
    assert any(running in one and "plm_model" in one for one in tried["problems"])


def test_초안은_토큰으로도_버리고_다_버리면_지도에서_빠진다(
    client: TestClient, admin: Signed, drop: Path
) -> None:
    slug = _slug()
    mcp = _pat(client, admin)
    assert _save(client, mcp, slug, _body(slug, drop)).status_code == 201
    assert _draft(client, mcp, slug, RECIPE).status_code == 201
    gone = client.post(f"/api/relay/pipelines/{slug}/recipes/1/discard", headers=mcp)
    assert gone.status_code == 200 and gone.json()["status"] == "discarded", gone.text
    gone = client.post(f"/api/relay/pipelines/{slug}/definitions/1/discard", headers=mcp)
    assert gone.status_code == 200 and gone.json()["status"] == "discarded", gone.text
    nodes = client.get("/api/relay/map", headers=admin.headers).json()["nodes"]
    assert not [one for one in nodes if one["kind"] == "pipeline" and one["label"] == slug]
    again = client.post(f"/api/relay/pipelines/{slug}/definitions/1/discard", headers=mcp)
    assert again.status_code == 409 and _code(again).endswith("RELAY-0017")
    for kind in ("definitions", "recipes"):  # 버린 초안은 사람도 다시 켜지 못한다
        revive = client.post(
            f"/api/relay/pipelines/{slug}/{kind}/1/activate", headers=admin.headers
        )
        assert revive.status_code in (404, 409), (kind, revive.text)

    # 켜진 판은 못 버린다
    assert _save(client, admin.headers, slug, _body(slug, drop)).json()["version"] == 2
    client.post(f"/api/relay/pipelines/{slug}/definitions/2/activate", headers=admin.headers)
    kept = client.post(f"/api/relay/pipelines/{slug}/definitions/2/discard", headers=mcp)
    assert kept.status_code == 409
    revive = client.post(
        f"/api/relay/pipelines/{slug}/recipes/1/activate", headers=admin.headers
    )
    assert revive.status_code == 409 and _code(revive).endswith("RELAY-0017")


def test_시험은_큰_원천의_앞만_받고_보낼_것은_세지_않는다(
    client: TestClient, admin: Signed, drop: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "relay_trial_rows", 1)
    slug = _slug()
    _save(client, admin.headers, slug, _body(slug, drop))
    _draft(client, admin.headers, slug, RECIPE)
    tried = client.post(
        f"/api/relay/pipelines/{slug}/definitions/1/test",
        json={"recipe_version": 1},
        headers=admin.headers,
    ).json()
    assert tried["source"]["rows"] == 1 and tried["source"]["capped"] == 1
    assert tried["recipe"]["rows"] == {"plm_model": 1} and "would_send" not in tried["recipe"]
    assert "앞부분만" in tried["recipe"]["against"]


def test_공유_폴더도_부를_수_있는_주소만(client: TestClient, admin: Signed) -> None:
    far = client.post(
        "/api/relay/discover",
        json={"adapter": {"kind": "file", "location": "smb://evilfs/share/x.csv"}},
        headers=admin.headers,
    )
    assert far.status_code == 403 and _code(far).endswith("RELAY-0010")


def test_자동_적용은_정의에_적고_사람이_켜야_서고_틀리면_거절한다(
    client: TestClient, admin: Signed, drop: Path
) -> None:
    slug = _slug()
    wrong = _save(
        client, admin.headers, slug, _body(slug, drop, auto_apply={"after_manual": 0})
    )
    assert wrong.status_code == 422 and "after_manual" in wrong.json()["error"]["message"]
    body = _body(slug, drop, auto_apply={"max_changes": 50})
    assert _save(client, admin.headers, slug, body).status_code == 201
    client.post(f"/api/relay/pipelines/{slug}/definitions/1/activate", headers=admin.headers)
    detail = client.get(f"/api/relay/pipelines/{slug}", headers=admin.headers).json()
    assert detail["auto_apply_label"].startswith("타이머 실행 · 사람 적용 3번 뒤 · 변경 50건")
