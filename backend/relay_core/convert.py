"""미러 → 실행 폴더 — **틀의 변환기(`sp_table`)의 규칙 그대로**, 나눠서(`relay_core.table`).

레시피는 지금 쓰는 `sp-table/1` 대응 파일이다. 행마다 AI 가 판단하지 않는다 — 같은 미러와 같은
레시피면 늘 같은 실행 폴더가 나온다(그래서 `diff` 가 맞는다).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from relay_core import spill, table


@dataclass(frozen=True)
class Converted:
    run: Path
    clean: bool
    """미해결(`unresolved.json`)이 없나. 있으면 **제출하지 않는다** — 레시피를 고쳐 다시
    돌린다."""
    report: str


class Source(Protocol):
    """원천 표를 쓸 수 있는 미러 — 메모리의 `Mirror` 나 파일의 `state.MirrorFile`."""

    def __len__(self) -> int: ...

    def write_csv(self, path: Path) -> Path: ...


def write_source(mirror: Source, recipe: Path, run: Path, *, work: Path) -> Path:
    """`work` 에 원천 표(CSV)와 **그때의 레시피 사본**을 쓴다 — 변환(`transform`)의 입력.
    사본은 거꾸로 보기(`trace`)가 쓴다 — 기준을 만든 뒤 레시피 판이 바뀌면 지금 레시피로는 그
    값이 어디서 왔는지 틀리게 말한다."""
    if len(mirror) == 0:
        raise ValueError("미러가 비어 있습니다 — 받은 행이 없습니다.")
    source = mirror.write_csv(work / f"{run.name}.source.csv")
    (work / f"{run.name}.recipe.table.json").write_bytes(recipe.read_bytes())
    return source


def transform(
    source: Path,
    recipe: Path,
    run: Path,
    *,
    server: str = "",
    token: str = "",
    bucket_rows: int = spill.BUCKET_ROWS,
) -> Converted:
    """원천 표 → `run` 의 실행 폴더(`run` 은 없어야 한다). `server` · `token` 은 레시피의 참조
    대조(`match`)가 대상 SP 의 식별자를 받을 때만 쓴다.

    틀의 변환기와 같은 결과를 **나눠서** 만든다(`relay_core.table`) — 표를 메모리에 들지 않고,
    행이 `bucket_rows` 를 넘으면 타입마다 식별자의 해시로 바구니에 나눠 모은다. 바구니는
    원천 표 옆(`<때>.spill`)에 두고 끝나면 지운다."""
    clean, report = table.convert(
        recipe,
        source,
        run,
        server=server,
        token=token,
        bucket_rows=bucket_rows,
        scratch=source.with_name(f"{run.name}.spill"),
    )
    return Converted(run=run, clean=bool(clean), report=str(report))


def convert(
    mirror: Source,
    recipe: Path,
    run: Path,
    *,
    work: Path,
    server: str = "",
    token: str = "",
) -> Converted:
    """`write_source` 다음 `transform` — 레시피 시험 · 정의 초안 시험이 쓴다. 미러는 메모리의
    `Mirror` 도, 파일의 `state.MirrorFile` 도 된다."""
    source = write_source(mirror, recipe, run, work=work)
    return transform(source, recipe, run, server=server, token=token)
