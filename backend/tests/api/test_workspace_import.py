"""부서 붙여 넣어 추가 — 다른 플랫폼의 내보내기를 그대로. 계획 먼저, 전부 아니면 무."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.api.conftest import Signed

# ReportArchive 의 내보내기 모양 그대로 — 모르는 열(kind·external_view_default…)이 섞여 있다.
RA_HEADER = (
    "slug\tname\tparent_slug\tparent_name\tdepth\tpath\tkind\tstatus\tdescription\tsort_order"
    "\texternal_view_default\tmember_count\tmanagers\tcreated_at"
)


def _rows(prefix: str) -> str:
    return "\n".join(
        [
            RA_HEADER,
            f"{prefix}-eng\t엔지니어링\t\t\t0\t엔지니어링\torg\tactive\t설계와 해석\t10"
            "\tinherit\t12\t김철수\t2025-01-01",
            f"{prefix}-cae\t해석팀\t{prefix}-eng\t엔지니어링\t1\t엔지니어링 / 해석팀"
            "\torg\tactive\t\t20\tinherit\t5\t\t2025-01-01",
            f"{prefix}-tf1\t경량화 TF\t\t\t0\t경량화 TF\ttf\tarchived\t끝난 TF\t30"
            "\tinherit\t3\t\t2025-01-01",
            f"u-{prefix}\t홍길동\t\t\t0\t홍길동\tpersonal\tactive\t\t0\t\t1\t\t2025-01-01",
        ]
    )


def _all(client: TestClient, admin: Signed) -> dict[str, dict[str, object]]:
    return {
        one["slug"]: one
        for one in client.get("/api/workspaces?all=true", headers=admin.headers).json()
    }


def test_붙여_넣으면_계획_먼저_그리고_상위까지(client: TestClient, admin: Signed) -> None:
    prefix = f"r{uuid.uuid4().hex[:5]}"
    planned = client.post(
        "/api/workspaces/import",
        json={"text": _rows(prefix), "apply": False},
        headers=admin.headers,
    )
    assert planned.status_code == 200, planned.text
    body = planned.json()
    assert body["applied"] is False
    assert body["counts"] == {"create": 3, "update": 0, "unchanged": 0, "skip": 1, "error": 0}
    assert body["rows"][3]["action"] == "skip" and "개인" in body["rows"][3]["message"]
    assert f"{prefix}-cae" not in _all(client, admin)

    done = client.post(
        "/api/workspaces/import",
        json={"text": _rows(prefix), "apply": True},
        headers=admin.headers,
    ).json()
    assert done["applied"] is True, done
    listed = _all(client, admin)
    assert listed[f"{prefix}-cae"]["parent_slug"] == f"{prefix}-eng"
    assert listed[f"{prefix}-eng"]["description"] == "설계와 해석"
    assert listed[f"{prefix}-tf1"]["is_active"] is False
    assert f"u-{prefix}" not in listed

    # 같은 표를 다시 넣으면 「그대로」. 이름을 고쳐 넣으면 「고침」.
    again = client.post(
        "/api/workspaces/import",
        json={"text": _rows(prefix), "apply": False},
        headers=admin.headers,
    ).json()
    assert again["counts"]["unchanged"] == 3
    renamed = _rows(prefix).replace("\t해석팀\t", "\tCAE팀\t")
    changed = client.post(
        "/api/workspaces/import",
        json={"text": renamed, "apply": True},
        headers=admin.headers,
    ).json()
    assert changed["counts"]["update"] == 1
    assert next(r for r in changed["rows"] if r["slug"] == f"{prefix}-cae")["changes"] == [
        "name"
    ]


def test_오류가_하나라도_있으면_아무것도_안_넣는다(client: TestClient, admin: Signed) -> None:
    prefix = f"r{uuid.uuid4().hex[:5]}"
    text = "\n".join(
        [
            "slug,name,parent_slug",
            f"{prefix}-a,A,",
            f"{prefix}-b,B,{prefix}-nope",  # 없는 상위
        ]
    )
    done = client.post(
        "/api/workspaces/import", json={"text": text, "apply": True}, headers=admin.headers
    ).json()
    assert done["applied"] is False and done["counts"]["error"] == 1
    assert "상위 부서" in done["rows"][1]["message"]
    assert f"{prefix}-a" not in _all(client, admin)


def test_시스템_관리자만(client: TestClient, member: Signed) -> None:
    denied = client.post(
        "/api/workspaces/import",
        json={"text": "slug,name\nx,y", "apply": False},
        headers=member.headers,
    )
    assert denied.status_code == 403
