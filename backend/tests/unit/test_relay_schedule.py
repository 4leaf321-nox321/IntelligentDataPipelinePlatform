"""일정 — 「지금 돌 차례인가」. 타이머는 5분마다 묻고, 넣는 것은 차례일 때 한 번이다."""

from __future__ import annotations

import re
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
    assert "5시간째 완료되지 않았습니다" in said and "작업 워커" in said


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
    assert "11-07 01:00 예정 실행이 3시간 지나도록 시작되지 않았습니다" in said
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


# --- 대조의 일정 -------------------------------------------------------------------------

from relay_core.schedule import (  # noqa: E402 — 이 아래의 시험만 쓴다
    Reconcile,
    check_reconcile,
    reconcile_due,
    reconcile_label,
    reconcile_rule,
)


def _rule(spec: object, days: int = 7, at: str = "03:00") -> Reconcile | None:
    return reconcile_rule(check_reconcile(spec), days=days, at=at)


def test_대조_일정의_모양을_본다() -> None:
    # 안 적었으면 서버의 기본을 따른다 — `true` · 빈 것도 같다.
    assert check_reconcile(None) is None and check_reconcile(True) is None
    assert check_reconcile({}) is None
    assert check_reconcile(False) == Reconcile(off=True)
    assert check_reconcile({"weekdays": ["sun", "목"], "at": "04:30"}) == Reconcile(
        weekdays=(3, 6), at="04:30"
    )
    assert check_reconcile({"weekdays": "SUN"}) == Reconcile(weekdays=(6,))
    assert check_reconcile({"every_days": 14, "weekdays": ["일"]}).to_json() == {  # type: ignore[union-attr]
        "weekdays": ["sun"],
        "every_days": 14,
    }
    assert Reconcile(off=True).to_json() is False
    for wrong, said in (
        ({"weekly": "sun"}, "지원하지 않는 항목"),
        ({"weekdays": ["sunday"]}, "요일은"),
        ({"weekdays": []}, "요일 목록"),
        ({"every_days": 0}, "1 이상 365 이하"),
        ({"every_days": True}, "1 이상 365 이하"),
        ({"every_days": 400}, "1 이상 365 이하"),
        ({"at": "04:00"}, "weekdays(요일) 또는 every_days"),
        ({"weekdays": ["sun"], "at": "4시"}, "HH:MM"),
        ("weekly", "false 또는"),
    ):
        with pytest.raises(ScheduleError, match=re.escape(said)):
            check_reconcile(wrong)


def test_대조_일정에_서버의_기본을_채우고_이름을_붙인다() -> None:
    assert _rule(None) == Reconcile(every_days=7, at="03:00")
    assert _rule(None, days=0) is None  # 서버가 끔 · 정의도 안 적음
    assert _rule(False) is None and _rule({"every_days": 3}, days=0) is not None
    assert _rule({"weekdays": ["sun"]}, at="02:00") == Reconcile(weekdays=(6,), at="02:00")
    assert reconcile_label(_rule(None)) == "7일마다 03:00"
    assert reconcile_label(_rule({"weekdays": ["sun"]})) == "매주 일요일 03:00"
    assert reconcile_label(_rule({"weekdays": ["mon", "thu"], "at": "01:10"})) == (
        "매주 월·목 01:10"
    )
    assert reconcile_label(_rule({"every_days": 1})) == "매일 03:00"
    assert reconcile_label(_rule({"weekdays": ["sun"], "every_days": 14})) == (
        "14일마다 · 일요일 03:00"
    )
    assert reconcile_label(_rule({"weekdays": WEEK})) == "매일 03:00"
    assert reconcile_label(_rule(False)) == "수동"


WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def test_매주_그_요일_그_시각_뒤_첫_차례에() -> None:
    sunday = _rule({"weekdays": ["sun"]})
    first = _at("2026-11-20T10:00:00")  # 금요일에 처음 적용

    def due(last: str | None, now: str) -> bool:
        return reconcile_due(
            sunday, last=_at(last) if last else None, first=first, now=_at(now)
        )

    assert due(None, "2026-11-22T03:05:00")  # 첫 적용 뒤 처음 맞는 일요일 03:00
    assert not due(None, "2026-11-22T02:55:00")  # 그날 03:00 전
    assert due(None, "2026-11-27T12:00:00")  # 그 일요일을 놓쳤으면 그 뒤 첫 틱에
    assert not due("2026-11-22T03:05:00", "2026-11-28T03:05:00")  # 토요일 — 차례가 아니다
    assert due("2026-11-22T03:05:00", "2026-11-29T03:05:00")  # 다음 일요일
    # 일요일 내내 미뤄졌으면(실행 · 적용이 길었다) 월요일에라도 넣는다 — 차례를 놓치지 않는다.
    assert due("2026-11-22T03:05:00", "2026-11-30T01:00:00")
    assert not due("2026-11-29T09:00:00", "2026-11-30T01:00:00")


def test_며칠마다_격주는_요일과_간격을_함께() -> None:
    first = _at("2026-11-01T00:00:00")
    biweekly = _rule({"weekdays": ["sun"], "every_days": 14})
    last = _at("2026-11-15T03:01:00")  # 일요일
    assert not reconcile_due(biweekly, last=last, first=first, now=_at("2026-11-22T03:05:00"))
    assert reconcile_due(biweekly, last=last, first=first, now=_at("2026-11-29T03:05:00"))
    three = _rule({"every_days": 3, "at": "05:00"})
    last = _at("2026-11-20T05:02:00")
    assert not reconcile_due(three, last=last, first=first, now=_at("2026-11-22T06:00:00"))
    assert reconcile_due(three, last=last, first=first, now=_at("2026-11-23T05:00:00"))
    # 대조하지 않는 일정 · 적용한 적이 없으면 아니다.
    assert not reconcile_due(None, last=None, first=first, now=_at("2026-11-23T05:00:00"))
    assert not reconcile_due(three, last=None, first=None, now=_at("2026-11-23T05:00:00"))
