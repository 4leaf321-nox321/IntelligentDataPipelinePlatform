"""백업 신선도 — **서버가 자기 백업을 볼 수 있는가.**

백업은 작업 스케줄러가 돌린다. 아무도 안 보면 조용히 죽고, 그 사실은 복구가
필요한 날에야 드러난다.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import get_settings
from app.shared import ops


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """`get_settings` 는 캐시된다 — 시험이 값을 바꾸려면 비워야 한다."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _with_backup_dir(monkeypatch: pytest.MonkeyPatch, path: Path | None) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "backup_dir", path)


def test_설정이_없으면_그렇다고_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """**「백업이 없다」 와 「설정이 없다」 는 다른 일이다** — 같게 말하면 사람은
    안 해도 되는 일을 하러 간다."""
    _with_backup_dir(monkeypatch, None)
    status = ops.backup_status()
    assert status.configured is False
    assert status.stale is True  # 모르는 것을 괜찮다고 하지 않는다
    assert status.problem is not None
    assert "BACKUP_DIR" in status.problem


def test_폴더가_없으면_한_번도_안_돈_것이다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _with_backup_dir(monkeypatch, tmp_path / "없는폴더")
    status = ops.backup_status()
    assert status.configured is True
    assert status.stale is True
    assert status.problem is not None and "한 번도" in status.problem


def test_덤프가_없으면_한_번도_안_돈_것이다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "db").mkdir()
    _with_backup_dir(monkeypatch, tmp_path)
    status = ops.backup_status()
    assert status.stale is True
    assert status.last_at is None


def test_최근_덤프가_있으면_신선하다(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """**이름이 아니라 확장자로 본다** — 배치가 바뀌어도 덤프는 덤프다."""
    nested = tmp_path / "db"
    nested.mkdir()
    (nested / "db-20260910-030000.dump").write_bytes(b"x")
    _with_backup_dir(monkeypatch, tmp_path)

    status = ops.backup_status()
    assert status.stale is False
    assert status.problem is None
    assert status.last_at is not None
    assert status.age_hours is not None and status.age_hours < 1


def test_오래되면_남은_일에_올라간다(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """**홈이 말해 주지 않으면 아무도 모른다.** 관리 화면에 들어가야만 보이는
    값은 안 읽힌다."""
    nested = tmp_path / "db"
    nested.mkdir()
    dump = nested / "db-old.dump"
    dump.write_bytes(b"x")
    old = (datetime.now(UTC) - timedelta(hours=ops.STALE_HOURS + 5)).timestamp()
    import os

    os.utime(dump, (old, old))
    _with_backup_dir(monkeypatch, tmp_path)

    status = ops.backup_status()
    assert status.stale is True
    assert status.problem is not None and "시간 전" in status.problem

    class _Admin:
        is_system_admin = True

    class _Member:
        is_system_admin = False

    items = ops.maintenance(None, _Admin())  # type: ignore[arg-type]
    assert [one.key for one in items] == ["backup_stale"]
    assert items[0].severity == "warning"

    # 처리할 수 없는 사람에게는 안 뜬다 — 못 지우는 줄은 곧 안 읽힌다.
    assert ops.maintenance(None, _Member()) == []  # type: ignore[arg-type]
