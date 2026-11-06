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


def write_source(mirror: Mirror, recipe: Path, run: Path, *, work: Path) -> Path:
    """`work` 에 원천 표(CSV)와 **그때의 레시피 사본**을 쓴다 — 변환(`transform`)의 입력.
    사본은 거꾸로 보기(`trace`)가 쓴다 — 기준을 만든 뒤 레시피 판이 바뀌면 지금 레시피로는 그
    값이 어디서 왔는지 틀리게 말한다."""
    if len(mirror) == 0:
        raise ValueError("미러가 비어 있습니다 — 받은 행이 없습니다.")
    source = mirror.write_csv(work / f"{run.name}.source.csv")
    (work / f"{run.name}.recipe.table.json").write_bytes(recipe.read_bytes())
    return source


def transform(
    source: Path, recipe: Path, run: Path, *, server: str = "", token: str = ""
) -> Converted:
    """원천 표 → `run` 의 실행 폴더(`run` 은 없어야 한다). `server` · `token` 은 레시피의 참조
    대조(`match`)가 대상 SP 의 식별자를 받을 때만 쓴다."""
    clean, report = sp.table.convert(recipe, source, run, server=server, token=token)
    return Converted(run=run, clean=bool(clean), report=str(report))


def convert(
    mirror: Mirror,
    recipe: Path,
    run: Path,
    *,
    work: Path,
    server: str = "",
    token: str = "",
) -> Converted:
    """`write_source` 다음 `transform` — 미러를 끝까지 쥐고 있어도 되는 곳(레시피 시험)에서.
    실행은 원천 표를 쓴 뒤 미러를 놓고 변환한다(`runner.run` — 큰 원천에서 메모리)."""
    source = write_source(mirror, recipe, run, work=work)
    return transform(source, recipe, run, server=server, token=token)
