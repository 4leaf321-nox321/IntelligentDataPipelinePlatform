"""미러 → 실행 폴더 — **틀의 변환기(`sp_table.convert`)를 그대로** 부른다.

레시피는 지금 쓰는 `sp-table/1` 대응 파일이다. 행마다 AI 가 판단하지 않는다 — 같은 미러와 같은
레시피면 늘 같은 실행 폴더가 나온다(그래서 `diff` 가 맞는다).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from relay_core import sp
from relay_core.mirror import Mirror


@dataclass(frozen=True)
class Converted:
    run: Path
    clean: bool
    """미해결(`unresolved.json`)이 없나. 있으면 **제출하지 않는다** — 레시피를 고쳐 다시
    돌린다."""
    report: str


def convert(
    mirror: Mirror,
    recipe: Path,
    run: Path,
    *,
    work: Path,
    server: str = "",
    token: str = "",
) -> Converted:
    """`work` 에 원천 표(CSV)를 쓰고 `run` 에 실행 폴더를 만든다(`run` 은 없어야 한다).

    `server` · `token` 은 레시피의 참조 대조(`match`)가 대상 SP 의 식별자를 받을 때만 쓴다."""
    if len(mirror) == 0:
        raise ValueError("미러가 비어 있습니다 — 받은 행이 없습니다.")
    source = mirror.write_csv(work / f"{run.name}.source.csv")
    clean, report = sp.table.convert(recipe, source, run, server=server, token=token)
    return Converted(run=run, clean=bool(clean), report=str(report))
