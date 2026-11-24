"""사이드바 묶음 — 새 타입을 둘 묶음을 대상의 묶음에서 고르고(같은 원천의 타입이 든 묶음 ›
이름이 같은 묶음 › 새 묶음), 타입 정의 초안에 싣는다(있는 묶음은 slug, 새 묶음은 정의의
`groups`).
"""

from __future__ import annotations

from typing import Any

import pytest

from relay_core.typedraft import check_group, draft, suggest_group

GROUPS = [
    {"slug": "dev", "label": "개발"},
    {"slug": "mes", "label": "MES"},
    {"slug": "common", "label": "공통"},
]
TYPES: dict[str, dict[str, Any]] = {
    "plm_model": {"managed_by": "plm", "nav_group_slug": "dev"},
    "plm_task": {"managed_by": "plm", "nav_group_slug": "dev"},
    "plm_site": {"managed_by": "plm", "nav_group_slug": "common"},
    "mes_lot": {"managed_by": "mes", "nav_group_slug": ""},
}


def test_같은_원천의_타입이_든_묶음이_먼저() -> None:
    got = suggest_group(GROUPS, TYPES, "plm", "PLM")
    assert (got["slug"], got["new"]) == ("dev", False) and "2개" in got["why"]


def test_없으면_이름이_같은_묶음_그것도_없으면_새로() -> None:
    by_name = suggest_group(GROUPS, TYPES, "mes", "MES")
    assert (by_name["slug"], by_name["new"]) == ("mes", False)
    fresh = suggest_group(GROUPS, TYPES, "erp", "ERP")
    assert fresh == {
        "slug": "erp",
        "label": "ERP",
        "new": True,
        "why": "해당 그룹이 없어 원천 이름으로 새 그룹 생성",
    }
    assert suggest_group([], {}, "9x", "")["slug"] == "g_9x"


def test_타입_정의_초안이_묶음을_싣는다() -> None:
    columns = {"LOT_ID": ["L1"], "QTY": ["3"]}
    existing = draft(
        type_slug="erp_lot",
        label="로트",
        columns=columns,
        key_column="LOT_ID",
        nav_group="dev",
    )
    assert existing["ontology"]["types"][0]["nav_group_slug"] == "dev"
    assert "groups" not in existing["ontology"]
    fresh = draft(
        type_slug="erp_lot",
        label="로트",
        columns=columns,
        key_column="LOT_ID",
        nav_group={"slug": "erp", "label": "ERP", "icon": "factory"},
    )
    assert fresh["ontology"]["groups"] == [{"slug": "erp", "label": "ERP", "icon": "factory"}]
    assert fresh["ontology"]["types"][0]["nav_group_slug"] == "erp"
    plain = draft(type_slug="erp_lot", label="로트", columns=columns, key_column="LOT_ID")
    assert "nav_group_slug" not in plain["ontology"]["types"][0]


@pytest.mark.parametrize(
    ("group", "said"),
    [
        ({"slug": "Bad Slug", "label": "x"}, "slug"),
        ({"slug": "erp"}, "이름"),
        ({"slug": "erp", "label": "ERP", "audience": "boss"}, "audience"),
        ({"slug": "erp", "label": "ERP", "color": "red"}, "color"),
        ({"slug": "erp", "label": "ERP", "owner": "me"}, "지원하지 않는 항목"),
    ],
)
def test_새_묶음의_모양(group: dict[str, Any], said: str) -> None:
    with pytest.raises(ValueError, match=said):
        check_group(group)
