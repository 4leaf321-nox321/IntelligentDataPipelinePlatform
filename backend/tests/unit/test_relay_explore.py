"""탐색의 단서 — 값을 모르고 부른 AI 가 정의를 고칠 수 있게. 칸의 후보(식별자 · 변경 시각 ·
삭제 표시 · 이름)와 REST 응답의 모양(목록 자리 · 쪽 넘김 · 전체 건수). 행 자리가 틀려도
탐색은 멈추지 않고 가장 그럴듯한 목록으로 칸을 보인다.
"""

from __future__ import annotations

from typing import Any

import httpx

from relay_core.adapters.explore import field_hints, shape
from tests.unit.test_relay_rest import _adapter

ROWS = [
    {
        "lotId": f"L{n}",
        "lotName": f"로트 {n}",
        "qty": n,
        "updatedAt": f"2026-11-0{n}T01:00:00Z",
        "isDeleted": "N",
        "plant": "P1",
    }
    for n in range(1, 4)
]


def test_칸의_후보는_이름과_값을_함께_본다() -> None:
    hints = field_hints(ROWS)
    assert hints["key"][0] == "lotId"
    assert "plant" not in hints["key"]  # 값이 겹친다
    assert hints["updated_at"] == ["updatedAt"] and hints["deleted"] == ["isDeleted"]
    assert hints["label"] == ["lotName"]
    assert field_hints([{"id": "1", "updated": "어제"}]) == {
        "key": [],  # 항목 하나로는 서로 다른지 모른다
        "updated_at": [],  # 날짜 꼴이 아니다
        "deleted": [],
        "label": [],
    }


def _reply(body: Any, headers: dict[str, str] | None = None) -> Any:
    return lambda request: httpx.Response(200, json=body, headers=headers or {})


def test_행_자리가_틀려도_목록을_찾아_칸을_보인다() -> None:
    body = {"result": {"data": ROWS, "totalCount": 120}, "status": "ok"}
    found = _adapter(_reply(body), key="lotId", rows_path="items").discover()
    assert [one["path"] for one in found["paths"]][:2] == ["lotId", "lotName"]
    assert found["hints"]["key"][0] == "lotId"
    rows = found["shape"]["rows_path"]
    assert rows["found"] is False and rows["candidates"][0]["path"] == "result.data"
    assert found["shape"]["total"] == {"path": "result.totalCount", "value": 120}
    assert found["shape"]["paging"]["guess"] == "page"
    assert any("result.data" in note for note in found["shape"]["notes"])


def test_쪽_넘김의_단서() -> None:
    nxt = shape({"items": ROWS, "next": "/api/lots?page=2"}, {}, "items", 5)
    assert nxt["paging"] == {
        "guess": "next_url",
        "next_path": "next",
        "evidence": "응답의 「next」 에 다음 쪽 주소가 있습니다.",
    }
    cursor = shape({"data": ROWS, "meta": {"nextCursor": "abc"}}, {}, "data", 5)
    assert (
        cursor["paging"]["guess"] == "cursor"
        and cursor["paging"]["next_path"] == "meta.nextCursor"
    )
    link = shape(ROWS, {"link": '<https://x/api?page=2>; rel="next"'}, "", 5)
    assert link["paging"]["guess"] == "link" and link["rows_path"]["found"] is True
    # 5건을 달라 했는데 9건 — 쪽 크기 질의가 안 듣는다.
    many = shape(ROWS * 3, {}, "", 5)
    assert many["paging"]["guess"] == "none"
    bare = shape(ROWS, {}, "items", 5)
    assert bare["rows_path"]["candidates"] == [{"path": "", "items": 3}]
    assert "응답 자체가 목록" in bare["notes"][0]
