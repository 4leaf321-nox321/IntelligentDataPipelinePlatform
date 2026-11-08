"""받은 행이 디스크로 넘쳐도(`records.Records`) 미러에 넣은 결과 · 수 · 받기 점검의 재료가
메모리에서 한 것과 같다 — 같은 키가 여러 번 오면 받은 차례대로, 삭제 표시 · 전량의 부재
대조까지.
"""

from __future__ import annotations

import random
from pathlib import Path

from relay_core.adapters.base import Fetched
from relay_core.mirror import Merge
from relay_core.records import Records, SourceRecord, spill_to


def _row(rng: random.Random, n: int) -> SourceRecord:
    key = f"K{rng.randrange(n):04d}"
    if rng.random() < 0.1:
        return SourceRecord(key=key, deleted=True)
    return SourceRecord(
        key=key,
        values={"A": rng.choice(["", "x", "y"]), "B": str(rng.randrange(3))},
        updated_at=f"2026-11-0{rng.randrange(1, 9)}",
    )


def test_넘친_행으로_넣어도_미러와_수가_같다(tmp_path: Path) -> None:
    rng = random.Random(7)
    for turn in range(12):
        old = sorted(
            {
                one.key: one for one in (_row(rng, 300) for _ in range(250)) if not one.deleted
            }.values(),
            key=lambda one: one.key,
        )
        incoming = [_row(rng, 300) for _ in range(rng.randrange(1, 400))]
        full = turn % 3 == 0
        plain = Merge(Fetched(records=list(incoming), full=full))
        want = list(plain.rows(iter(old)))

        folder = tmp_path / f"spill-{turn}"
        with spill_to(folder):
            spilled = Records(keep=17)
            spilled.extend(incoming)
        assert spilled.spilled and len(spilled) == len(incoming)
        assert spilled.latest == max(one.updated_at for one in incoming if one.updated_at)
        merged = Merge(Fetched(records=spilled, full=full))
        got = list(merged.rows(iter(old)))
        assert got == want, turn
        assert merged.applied == plain.applied and merged.count == plain.count
        assert merged.tally == plain.tally
        spilled.close()
        assert not folder.exists()


def test_넘치기_전에는_받은_차례_넘친_뒤에는_키_차례(tmp_path: Path) -> None:
    rows = [SourceRecord(key=key) for key in ("C", "A", "B", "A")]
    small = Records(keep=100)
    small.extend(rows)
    assert [one.key for one in small] == ["C", "A", "B", "A"] and not small.spilled
    assert small[1].key == "A" and [one.key for one in small[:2]] == ["C", "A"]
    with spill_to(tmp_path / "s"):
        big = Records(keep=2)
        big.extend(rows)
    assert [one.key for one in big] == ["A", "A", "B", "C"]  # 같은 키는 받은 차례
    # 넘칠 자리가 없으면(정의 시험 · 시험) 메모리에 둔다.
    loose = Records(keep=2)
    loose.extend(rows)
    assert not loose.spilled and len(loose) == 4
