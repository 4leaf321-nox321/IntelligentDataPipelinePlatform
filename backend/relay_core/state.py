"""파이프라인의 상태 — 작업 폴더 하나(`<work>/<slug>/`).

    mirror.json    원천 키마다 최신 행
    state.json     워터마크 · 지난번 **적용된** 출력 · 대기 중인 계획
    runs/<때>/     그 실행의 전체 출력(레시피 변환 결과)
    sends/<때>/    대상에 보낸 것(바뀐 줄 · 무덤)
    work/          변환에 넣은 원천 표(CSV)

`runs/` · `sends/` · `work/` 는 실행마다 하나씩 는다 — 최근 몇 개와 상태가 가리키는 것만 남기고
정리한다(`Workdir.prune`).

1차는 파일이다 — 3단계에서 화면과 함께 DB 로 옮긴다. **쓸 때는 임시 파일에 쓰고 바꿔 끼운다** —
도중에 죽어도 반쯤 쓴 상태 파일이 남지 않는다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
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
    origin: str = "run"
    """그 계획이 어디서 왔나 — `run`(원천이 바뀜) · `reconcile`(대조의 보정 계획)."""


@dataclass
class State:
    watermark: str | None = None
    applied_run: str | None = None
    applied_at: str | None = None
    pending: Pending | None = None
    incrementals: int = 0
    """마지막 전량 뒤 증분을 몇 번 받았나 — `full_every` 가 그 수로 전량 날을 정한다."""
    history: list[dict[str, Any]] = field(default_factory=list)


#: 실행마다 생기는 이름 — `stamp()` 모양(보정 계획의 묶음은 `-resync` 가 붙는다). 정리는
#: **이 모양만** 지운다 — 시험의 임시 폴더(`tmp…`)나 사람이 둔 파일은 건드리지 않는다.
_RUN_NAME = re.compile(r"^(\d{8}T\d{12}Z)(-resync)?$")
_SOURCE_NAME = re.compile(r"^(\d{8}T\d{12}Z)\.source\.csv$")
#: 정리하는 곳 — (폴더, 이름 모양).
_PRUNED = (("runs", _RUN_NAME), ("sends", _RUN_NAME), ("work", _SOURCE_NAME))


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

    def prune(self, keep: int) -> list[str]:
        """지난 실행의 출력 · 보낸 묶음 · 변환 중간 표를 정리한다 — 폴더마다 **최근 `keep` 개와
        상태가 가리키는 것**(기준 출력, 대기 계획의 출력 · 묶음)만 남긴다. 지운 것의 상대
        경로를 돌려준다. `keep` 이 0 이면 안 지운다.

        최근 것을 남기는 까닭은 둘이다 — 「멈춤」 안내가 사람에게 가장 최근 실행의
        `unresolved.json` 을 열어 보라고 하고, 이중화의 다른 워커가 같은 파이프라인을 지금
        돌리는 중이면 그 폴더(가장 최근)를 만들고 있다."""
        if keep <= 0:
            return []
        state = self.load_state()
        held = {state.applied_run}
        if state.pending:
            held |= {state.pending.run, state.pending.send}
        removed: list[str] = []
        for folder, shape in _PRUNED:
            base = self.root / folder
            if not base.is_dir():
                continue
            found = sorted(
                (one.name, matched.group(1))
                for one in base.iterdir()
                if (matched := shape.match(one.name))
            )
            for name, when in found[:-keep]:
                relative = f"{folder}/{name}"
                # 원천 표(work/)는 그 실행이 남으면 같이 남는다 — 기준이 무엇에서 나왔나.
                if relative in held or (folder == "work" and f"runs/{when}" in held):
                    continue
                target = base / name
                try:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                except FileNotFoundError:
                    continue  # 다른 워커가 방금 지웠다
                removed.append(relative)
        return removed

    def read_json(self, name: str) -> Any:
        """작업 폴더의 JSON 하나(`reconcile.json` …) — 없으면 None."""
        file = self.root / name
        return json.loads(file.read_text(encoding="utf-8")) if file.exists() else None

    def write_json(self, name: str, body: Any) -> None:
        self._write(name, body)

    def load_mirror(self) -> Mirror:
        file = self.root / "mirror.json"
        if not file.exists():
            return Mirror()
        return Mirror.from_rows(json.loads(file.read_text(encoding="utf-8")))

    def save_mirror(self, mirror: Mirror) -> None:
        self._write("mirror.json", mirror.to_rows())
