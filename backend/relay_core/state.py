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
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from relay_core.adapters.base import Fetched
from relay_core.mirror import Applied, Merge, Mirror, write_csv
from relay_core.records import SourceRecord


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
    parts: list[str] = field(default_factory=list)
    """나눠 보내면 조각 폴더들(`sends/<때>/parts/001` …, `relay_core.parts`) — 비면 `send`
    하나다. `digest` 는 그때도 **전체** 묶음의 지문이다."""
    part: int = 0
    """지금 조각(0부터) — `job_id` 가 그 조각의 계획이고, 앞 조각들은 적용됐다. `job_id` 가
    비면 그 조각은 아직 미리 보지 않았다(「적용」 이 미리 보고 잇는다)."""
    folded: int = 0
    """적용된 조각 중 기준(`applied_run`)에 이미 접어 넣은 수(`parts.fold`)."""

    @property
    def total(self) -> int:
        return max(1, len(self.parts))


@dataclass
class State:
    watermark: str | None = None
    applied_run: str | None = None
    applied_at: str | None = None
    pending: Pending | None = None
    incrementals: int = 0
    """마지막 전량 뒤 증분을 몇 번 받았나 — `full_every` 가 그 수로 전량 날을 정한다."""
    history: list[dict[str, Any]] = field(default_factory=list)


#: 실행마다 생기는 이름 — `stamp()` 모양(보정 계획의 묶음은 `-resync`, 조각을 접은 기준은
#: `-partial` 이 붙는다 — 그 계획의 실행 시각 그대로). 정리는
#: **이 모양만** 지운다 — 시험의 임시 폴더(`tmp…`)나 사람이 둔 파일은 건드리지 않는다.
_RUN_NAME = re.compile(r"^(\d{8}T\d{12}Z)(-resync|-partial)?$")
#: 변환에 넣은 원천 표 · 레시피 사본, 나눠 변환 · 비교의 바구니(`<때>.spill` ·
#: `<때>.send.spill` — 끝나면 지우지만 도중에 죽으면 남는다).
_SOURCE_NAME = re.compile(
    r"^(\d{8}T\d{12}Z)\.(source\.csv|recipe\.table\.json|spill|send\.spill)$"
)
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
        # 원천 표(work/)는 실행 시각으로 — 조각을 접은 기준(`<때>-partial`)도 그 실행의
        # 것을 쓴다.
        stamps = {
            matched.group(1)
            for one in held
            if one and (matched := _RUN_NAME.match(one.rsplit("/", 1)[-1]))
        }
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
            # work/ 는 실행 하나에 파일이 여럿(원천 표 · 레시피) — 실행 시각으로 묶어 센다.
            recent = {when for _name, when in found} if folder == "work" else set()
            recent = set(sorted(recent)[-keep:])
            doomed = (
                [one for one in found if one[1] not in recent]
                if folder == "work"
                else found[:-keep]
            )
            for name, when in doomed:
                relative = f"{folder}/{name}"
                # 원천 표(work/)는 그 실행이 남으면 같이 남는다 — 기준이 무엇에서 나왔나.
                if relative in held or (folder == "work" and when in stamps):
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
        """미러를 메모리로 — 화면 · 레시피 시험처럼 미러를 쥐고 쓸 때. 실행은 들지 않는다
        (`merge_mirror` · `MirrorFile`)."""
        return Mirror(self.mirror_rows())

    def mirror_rows(self) -> Iterator[SourceRecord]:
        """`mirror.json` 을 **한 행씩** 키 차례로. 한 줄에 한 행인 모양(`_write_mirror`)이
        아니면(옛 판) 통째로 읽어 차례로 낸다."""
        file = self.root / "mirror.json"
        if not file.exists():
            return
        with file.open(encoding="utf-8") as handle:
            if handle.readline().strip() == "[" and _line_shaped(handle.readline()):
                handle.seek(0)
                handle.readline()
                for line in handle:
                    text = line.strip()
                    if not text or text == "]":
                        continue
                    yield _record(json.loads(text.rstrip(",")))
                return
        # 옛 판(0.2.2 까지)은 들여쓴 JSON 한 덩이였다 — 첫 줄은 같은 `[` 이고 둘째 줄이 `{` 다.
        yield from Mirror.from_rows(json.loads(file.read_text(encoding="utf-8"))).records()

    def mirror_size(self) -> int:
        """미러의 행 수 — 한 줄에 한 행인 모양이면 줄만 센다(읽어 풀지 않는다)."""
        file = self.root / "mirror.json"
        if not file.exists():
            return 0
        with file.open(encoding="utf-8") as handle:
            if handle.readline().strip() == "[" and _line_shaped(handle.readline()):
                handle.seek(0)
                return sum(1 for line in handle if line.strip() not in ("", "[", "]"))
        return sum(1 for _ in self.mirror_rows())

    def save_mirror(self, mirror: Mirror) -> None:
        self._write_mirror(mirror.records())

    def merge_mirror(self, fetched: Fetched) -> tuple[Applied, int]:
        """받은 것을 미러 파일에 흘려 반영한다 — `Mirror.apply` 와 같은 규칙 · 같은 수.
        메모리에는 받은 것만 든다. (반영한 수, 새 미러의 행 수)."""
        merge = Merge(fetched)
        self._write_mirror(merge.rows(self.mirror_rows()))
        return merge.applied, merge.count

    def _write_mirror(self, rows: Iterable[SourceRecord]) -> None:
        """**한 행씩 흘려 쓴다** — 미러 전체를 글 하나로 만들면(들여쓰기까지) 원천이 수십만
        행일 때 그 글만 수백 MB 다. 모양은 같은 JSON 배열(행마다 한 줄)이다. 임시 파일에 다
        쓴 뒤 바꿔 끼운다 — 읽던 옛 파일은 그때까지 그대로다."""
        target = self.root / "mirror.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        scratch = target.with_suffix(".json.tmp")
        with scratch.open("w", encoding="utf-8") as handle:
            handle.write("[")
            for index, one in enumerate(rows):
                row: dict[str, Any] = {"key": one.key, "values": dict(one.values)}
                row["updated_at"] = one.updated_at
                handle.write(",\n" if index else "\n")
                handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n]\n")
        os.replace(scratch, target)


def _line_shaped(line: str) -> bool:
    """한 줄에 한 행인 미러의 둘째 줄인가 — 행 하나(`{"key": …}`)이거나 빈 미러의 `]`."""
    text = line.strip().rstrip(",")
    if text == "]":
        return True
    try:
        return isinstance(json.loads(text), dict)
    except ValueError:
        return False


def _record(row: dict[str, Any]) -> SourceRecord:
    return SourceRecord(
        key=str(row["key"]),
        values={str(k): str(v) for k, v in dict(row.get("values") or {}).items()},
        updated_at=row.get("updated_at"),
    )


@dataclass
class MirrorFile:
    """파일에 있는 미러 — 메모리에 들지 않고 원천 표를 쓴다(`convert.write_source`)."""

    work: Workdir
    size: int

    def __len__(self) -> int:
        return self.size

    def write_csv(self, path: Path) -> Path:
        return write_csv(self.work.mirror_rows, path)
