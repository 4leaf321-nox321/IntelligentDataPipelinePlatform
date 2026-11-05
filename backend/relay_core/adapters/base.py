"""어댑터의 약속 — **이 모양만 내면 그 뒤는 원천을 모른다.**

PLM 에서 직접 받든, 중간 시스템(데이터레이크 · DB 뷰 · 파일 내보내기)을 거치든 어댑터 하나만
바뀐다. 레시피부터 뒤(변환 · 비교 · 제출)는 그대로다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from relay_core.records import SourceRecord


@dataclass
class Fetched:
    records: list[SourceRecord] = field(default_factory=list)
    watermark: str | None = None
    """다음에 `since` 로 되돌려 줄 값 — **원천이 준 값을 그대로.** 이쪽 시계로 만들면 그 사이
    행이 샌다(SP 연동 지침서 §4.2 「지킬 것」 1)."""
    complete: bool = True
    """마지막 쪽까지 받았나. 아니면 워터마크를 저장하지 않고, 부재 대조(이번에 안 온 것을 지운
    것으로 보기)도 하지 않는다 — 덜 받은 채 대조하면 아직 안 받은 것이 지워진다."""
    full: bool = False
    """전량을 받았나(`since` 없이). 그때만 「이번에 안 온 것」 을 지운 것으로 본다."""


class Adapter(Protocol):
    def fetch(self, since: str | None) -> Fetched: ...


class SourceAdapter(Adapter, Protocol):
    """어댑터 넷(REST · OData · 파일 · XML · SQL)이 다 지키는 것 — 받기 · 닿나 · 탐색.

    `source` 는 그 어댑터의 설정(실행기가 `full_every` · `deleted` 를 본다)."""

    source: Any

    def fetch(self, since: str | None, *, limit: int | None = None) -> Fetched:
        """`limit` 이면 그만큼만 받고 멈춘다 — 정의 시험이 큰 원천을 끝까지 받지 않게.
        덜 받은 것이라 `complete=False`(부재 대조 · 워터마크 저장을 하지 않는다)."""
        ...

    def probe(self) -> str: ...

    def discover(self) -> dict[str, Any]: ...
