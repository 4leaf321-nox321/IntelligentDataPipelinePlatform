"""보내 주는 원천(push) — 원천 시스템이 relay 로 **보내면** 받아 둔다. relay 가 가져갈 API 가
없고 원천이 webhook · 일괄 전송만 하는 경우.

    {"kind": "push", "key": "LotId", "columns": {"LOT_ID": "LotId", "QTY": "Qty"},
     "auth": {"kind": "bearer", "token_env": "MES_PUSH_TOKEN"}}

- 원천은 `POST /api/relay/inbound/<slug>` 에 머리글 `X-Relay-Token: <그 비밀>` 로 항목을
  보낸다 — JSON 목록, 또는 `rows_path` 의 자리에 목록이 든 객체(`{"items": [...]}`). 받은
  묶음은 그 파이프라인의 받을 함(작업 폴더의 `inbox/`)에 파일 하나로 쌓인다
  (`app/modules/relay/inbound.py`).
- 실행은 쌓인 묶음을 차례로 읽어 **받기**로 삼는다 — 증분이다(안 온 것은 그대로). 지운 것은
  삭제 표시(`deleted`)로 알려야 한다. 미러에 **반영한 뒤에만** 그 묶음을 지운다(`Fetched.done`)
  — 받기 점검이 멈추면 남아서 다음 실행이 다시 읽는다.
- 목록 펴기(`explode`) · 여러 칸 식별자는 REST 와 같다(`rest.records_of`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from relay_core.adapters.base import Fetched
from relay_core.adapters.rest import Auth, SourceError, check_shape, records_of, value_at
from relay_core.records import SourceRecord

#: 받을 함의 파일 이름 — 받은 차례(시각)로 서도록.
SUFFIX = ".json"


@dataclass(frozen=True)
class PushSource:
    key: str | Sequence[str]
    columns: Mapping[str, str]
    rows_path: str = ""
    """보낸 본문에서 목록이 있는 자리 — 비우면 본문 자체가 목록."""
    deleted: str | None = None
    updated_at: str | None = None
    explode: str = ""
    auth: Auth = field(default_factory=Auth)
    """원천이 보낼 때 실을 비밀 — `bearer` 와 `token_env`. 받는 쪽이 그 값과 견준다."""
    max_items: int = 50_000
    """한 번에 받는 항목의 상한."""


def items_of(body: Any, rows_path: str) -> list[Any]:
    """보낸 본문 → 항목들. 모양이 틀리면 `SourceError`."""
    items = value_at(body, rows_path) if rows_path else body
    if not isinstance(items, list):
        raise SourceError(f"전송한 본문의 {rows_path or '최상위'}에 목록이 없습니다.")
    return items


class PushAdapter:
    def __init__(self, source: PushSource, *, inbox: Path | None = None) -> None:
        self.source = source
        self.inbox = inbox

    def _files(self) -> list[Path]:
        if self.inbox is None:
            raise SourceError(
                "푸시 원천은 파이프라인의 수신함에서 읽습니다. 정의를 저장한 후 원천에서 "
                "데이터를 전송하고 정의 테스트나 실행으로 확인하세요."
            )
        if not self.inbox.is_dir():
            return []
        return sorted(one for one in self.inbox.iterdir() if one.name.endswith(SUFFIX))

    def _records(self, file: Path) -> list[SourceRecord]:
        source = self.source
        try:
            body = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as failure:
            raise SourceError(
                f"수신 데이터를 읽을 수 없습니다({file.name}): {failure}"
            ) from None
        out: list[SourceRecord] = []
        for item in items_of(body, ""):  # 받을 때 이미 목록만 남겼다(`inbound`)
            out += records_of(
                item,
                key=source.key,
                columns=source.columns,
                deleted=source.deleted,
                updated_at=source.updated_at,
                explode=source.explode,
            )
        return out

    def fetch(self, since: str | None, *, limit: int | None = None) -> Fetched:
        """쌓인 묶음 전부를 받은 차례로 — `since` 는 보지 않는다(받을 함이 곧 「그 뒤에 온
        것」 이다). `limit` 이면 앞 그만큼만(시험 — 지우지 않는다)."""
        check_shape(self.source.key, self.source.explode)
        files = self._files()
        records: list[SourceRecord] = []
        for file in files:
            records += self._records(file)
            if limit and len(records) >= limit:
                return Fetched(records=records[:limit], complete=False)
        if not files:
            return Fetched(records=[], watermark=since, complete=True, full=False)

        def done() -> None:
            for file in files:
                file.unlink(missing_ok=True)  # 이중화의 다른 워커가 먼저 지웠을 수 있다

        return Fetched(
            records=records,
            watermark=files[-1].name.removesuffix(SUFFIX),
            complete=True,
            full=False,
            done=done,
        )

    def probe(self) -> str:
        files = self._files()
        return f"수신함에 {len(files)}건이 대기 중입니다" + (
            f"(가장 오래된 파일 {files[0].name})."
            if files
            else ". 원천이 아직 데이터를 전송하지 않았습니다."
        )

    def discover(self) -> dict[str, Any]:
        from relay_core.adapters.explore import suggest

        files = self._files()
        if not files:
            raise SourceError(
                "아직 수신한 데이터가 없습니다. 원천이 데이터를 한 번 전송하면 탐색할 수 "
                "있습니다."
            )
        body = json.loads(files[-1].read_text(encoding="utf-8"))
        return suggest(items_of(body, "")[:5])
