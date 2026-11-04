"""원천의 행 한 줄 — 어댑터가 내고 미러가 쥔다."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceRecord:
    """원천의 **평평한** 행 하나(SP 연동 지침서 §3 의 모양).

    - `key` 는 원천의 **안 바뀌는** 식별자다. 이름으로 맞추지 않는다.
    - `values` 는 열 이름 → 글. 중첩은 어댑터가 펴서 온다(`owner.name` → `owner_name`).
    - `deleted` 면 원천에서 지운 것 — 미러에서 빠지고, 다음 출력 비교에서 무덤이 된다.
    """

    key: str
    values: Mapping[str, str] = field(default_factory=dict)
    updated_at: str | None = None
    deleted: bool = False
