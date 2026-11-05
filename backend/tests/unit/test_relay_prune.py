"""작업 폴더 정리 — 최근 N 개와 상태가 가리키는 것(기준 출력 · 대기 계획)만 남긴다.

실행마다 `runs/` · `sends/` · `work/` 가 하나씩 늘고 지우는 곳이 없었다(2026-11-05) — 한
시간마다 도는 파이프라인이면 운영 디스크가 꾸준히 찬다.
"""

from __future__ import annotations

from pathlib import Path

from relay_core.state import Pending, Workdir


def _at(i: int) -> str:
    return f"20261105T{i:012d}Z"  # `stamp()` 모양 — 날짜 · T · 12자리 · Z


def _folder(tmp_path: Path) -> Workdir:
    work = Workdir(tmp_path, "plm-model")
    for i in range(1, 7):
        (work.path(f"runs/{_at(i)}")).mkdir(parents=True)
        (work.path(f"runs/{_at(i)}/objects.json")).write_text("[]", encoding="utf-8")
        (work.path(f"sends/{_at(i)}")).mkdir(parents=True)
        work.path("work").mkdir(exist_ok=True)
        (work.path(f"work/{_at(i)}.source.csv")).write_text("K\n", encoding="utf-8")
    (work.path(f"sends/{_at(4)}-resync")).mkdir()
    (work.path("runs/notes.txt")).write_text("사람이 둔 것", encoding="utf-8")
    (work.path("runs/tmpabc")).mkdir()  # 시험의 임시 폴더 모양
    state = work.load_state()
    state.applied_run = f"runs/{_at(1)}"  # 오래된 기준 — 그래도 남는다
    state.pending = Pending(
        run=f"runs/{_at(3)}",
        send=f"sends/{_at(3)}",
        job_id="j",
        ok=True,
        digest="d",
        summary="",
        at="",
    )
    work.save_state(state)
    return work


def _names(work: Workdir, folder: str) -> list[str]:
    return sorted(one.name for one in work.path(folder).iterdir())


def test_최근_것과_상태가_가리키는_것만_남긴다(tmp_path: Path) -> None:
    work = _folder(tmp_path)
    removed = work.prune(2)

    assert _names(work, "runs") == [_at(1), _at(3), _at(5), _at(6), "notes.txt", "tmpabc"]
    assert _names(work, "sends") == [_at(3), _at(5), _at(6)]  # 보정 계획의 묶음도 이름 순으로
    # 원천 표는 남는 실행(기준 · 대기)을 따라 남는다.
    assert _names(work, "work") == [f"{_at(i)}.source.csv" for i in (1, 3, 5, 6)]
    assert f"sends/{_at(4)}-resync" in removed and f"runs/{_at(2)}" in removed
    assert work.prune(2) == []  # 두 번째는 할 일이 없다


def test_0_이면_안_지운다(tmp_path: Path) -> None:
    work = _folder(tmp_path)
    assert work.prune(0) == []
    assert len(_names(work, "runs")) == 8


def test_폴더가_아직_없으면_조용히_넘어간다(tmp_path: Path) -> None:
    assert Workdir(tmp_path, "new").prune(3) == []
