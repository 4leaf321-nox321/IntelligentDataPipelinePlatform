"""파이프라인의 상태 — 작업 폴더 하나(`<work>/<slug>/`).

    mirror.json    원천 키마다 최신 행
    state.json     워터마크 · 지난번 **적용된** 출력 · 대기 중인 계획
    runs/<때>/     그 실행의 전체 출력(레시피 변환 결과)
    sends/<때>/    대상에 보낸 것(바뀐 줄 · 무덤)
    work/          변환에 넣은 원천 표(CSV)

1차는 파일이다 — 3단계에서 화면과 함께 DB 로 옮긴다. **쓸 때는 임시 파일에 쓰고 바꿔 끼운다** —
도중에 죽어도 반쯤 쓴 상태 파일이 남지 않는다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from relay_core.mirror import Mirror


@dataclass
class Pending:
    run: str
    """그 계획의 전체 출력(`runs/…`) — 적용되면 이것이 다음 비교의 기준이 된다."""
    send: str
    job_id: str
    ok: bool
    digest: str
    summary: str
    at: str


@dataclass
class State:
    watermark: str | None = None
    applied_run: str | None = None
    applied_at: str | None = None
    pending: Pending | None = None
    incrementals: int = 0
    """마지막 전량 뒤 증분을 몇 번 받았나 — `full_every` 가 그 수로 전량 날을 정한다."""
    history: list[dict[str, Any]] = field(default_factory=list)


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


class Workdir:
    def __init__(self, root: Path, slug: str) -> None:
        self.root = root / slug
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, relative: str) -> Path:
        return self.root / relative

    def _write(self, name: str, body: Any) -> None:
        target = self.root / name
        scratch = target.with_suffix(target.suffix + ".tmp")
        scratch.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(scratch, target)

    def load_state(self) -> State:
        file = self.root / "state.json"
        if not file.exists():
            return State()
        raw = json.loads(file.read_text(encoding="utf-8"))
        pending = raw.get("pending")
        return State(
            watermark=raw.get("watermark"),
            applied_run=raw.get("applied_run"),
            applied_at=raw.get("applied_at"),
            pending=Pending(**pending) if pending else None,
            incrementals=int(raw.get("incrementals") or 0),
            history=list(raw.get("history") or []),
        )

    def save_state(self, state: State) -> None:
        body = asdict(state)
        body["history"] = body["history"][-50:]
        self._write("state.json", body)

    def load_mirror(self) -> Mirror:
        file = self.root / "mirror.json"
        if not file.exists():
            return Mirror()
        return Mirror.from_rows(json.loads(file.read_text(encoding="utf-8")))

    def save_mirror(self, mirror: Mirror) -> None:
        self._write("mirror.json", mirror.to_rows())
