"""일정 — 「지금 돌 차례인가」. 타이머는 5분마다 묻고, 넣는 것은 차례일 때 한 번이다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from relay_core.schedule import ScheduleError, check, due, label

KST = timezone(timedelta(hours=9))


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=KST)


def test_모양을_본다() -> None:
    assert check(None) is None and label(None) == "수동"
    assert check({"daily_at": "01:00"}) == {"daily_at": "01:00"}
    assert label({"every_minutes": 60}) == "60분마다"
    for wrong in (
        {"daily_at": "25:00"},
        {"every_minutes": 1},
        {"weekly": "mon"},
        {"a": 1, "b": 2},
    ):
        with pytest.raises(ScheduleError):
            check(wrong)


def test_매일_그_시각_뒤_첫_차례에_한_번() -> None:
    daily = {"daily_at": "01:00"}
    assert due(daily, None, _at("2026-11-05T00:30:00"))  # 처음이면 바로
    assert not due(
        daily, _at("2026-11-05T01:02:00"), _at("2026-11-05T09:00:00")
    )  # 오늘 돌았다
    assert due(daily, _at("2026-11-04T01:03:00"), _at("2026-11-05T01:05:00"))  # 오늘 차례
    assert not due(daily, _at("2026-11-04T01:03:00"), _at("2026-11-05T00:55:00"))  # 아직 전


def test_간격() -> None:
    every = {"every_minutes": 60}
    assert not due(every, _at("2026-11-05T01:00:00"), _at("2026-11-05T01:59:00"))
    assert due(every, _at("2026-11-05T01:00:00"), _at("2026-11-05T02:00:00"))
