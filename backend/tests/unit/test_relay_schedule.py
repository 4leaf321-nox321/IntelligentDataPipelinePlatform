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


GRACE = timedelta(hours=3)


def test_늦음_안_끝난_실행이_오래_기다리면() -> None:
    from relay_core.schedule import late

    daily = {"daily_at": "01:00"}
    queued = _at("2026-11-07T01:00:00")
    assert (
        late(daily, last=None, waiting=queued, now=_at("2026-11-07T03:59:00"), grace=GRACE)
        == ""
    )
    said = late(daily, last=None, waiting=queued, now=_at("2026-11-07T06:30:00"), grace=GRACE)
    assert "5시간째 끝나지 않았습니다" in said and "작업 워커" in said


def test_늦음_다음_차례가_지났는데_실행이_없으면() -> None:
    from relay_core.schedule import late, next_slot

    daily = {"daily_at": "01:00"}
    last = _at("2026-11-06T01:00:05")
    assert next_slot(daily, last) == _at("2026-11-07T01:00:00")
    assert next_slot({"every_minutes": 60}, last) == last + timedelta(hours=1)
    # 차례(11-07 01:00)에서 넉넉함(3시간) 안이면 아직이다.
    assert (
        late(daily, last=last, waiting=None, now=_at("2026-11-07T03:59:00"), grace=GRACE) == ""
    )
    said = late(daily, last=last, waiting=None, now=_at("2026-11-07T04:01:00"), grace=GRACE)
    assert "11-07 01:00 차례의 실행이 3시간 지나도록 없습니다" in said
    # 사람이 낮에 돌린 것도 끝난 실행이다 — 다음 차례는 그다음 날 01:00.
    noon = _at("2026-11-07T12:00:00")
    assert (
        late(daily, last=noon, waiting=None, now=_at("2026-11-08T03:00:00"), grace=GRACE) == ""
    )


def test_늦음이_아닌_것() -> None:
    from relay_core.schedule import late

    now = _at("2026-11-07T12:00:00")
    old = _at("2026-11-01T01:00:00")
    # 일정이 없음 · 끔(넉넉함 0) · 한 번도 끝난 적 없고 기다리는 것도 없음(실행 전).
    assert late(None, last=old, waiting=None, now=now, grace=GRACE) == ""
    assert (
        late({"daily_at": "01:00"}, last=old, waiting=None, now=now, grace=timedelta(0)) == ""
    )
    assert late({"daily_at": "01:00"}, last=None, waiting=None, now=now, grace=GRACE) == ""
    assert "5일" in late({"daily_at": "01:00"}, last=old, waiting=None, now=now, grace=GRACE)
