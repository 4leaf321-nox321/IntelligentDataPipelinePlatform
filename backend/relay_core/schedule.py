"""일정 — 정의의 `schedule` 로 「지금 돌 차례인가」 를 가른다(타이머가 5분마다 묻는다).

    {"daily_at": "01:00"}      매일 그 시각(서버의 현지 시각) 뒤 첫 차례에 한 번
    {"every_minutes": 60}      마지막 실행에서 그만큼 지나면

일정이 없으면 화면 · 명령으로만 돈다. SP 연동 지침서의 확정(§12-A 1)은 「야간 1회 + 수동 실행」
이다.

**늦음**(`late`) — 일정대로라면 끝났어야 할 실행이 끝나지 않았다. 실패 · 멈춤은 실행이 끝나며
알리지만, 실행이 아예 안 끝나면(작업 워커가 멈춤 · 실행이 걸림 · 타이머가 안 돎) 끝날 때 알리는
길로는 아무 말도 없다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ScheduleError(ValueError):
    pass


def check(schedule: Any) -> dict[str, Any] | None:
    """모양을 본다 — 모르는 칸 · 틀린 시각은 거절한다(조용히 안 도는 일정이 가장 나쁘다)."""
    if schedule in (None, {}):
        return None
    if not isinstance(schedule, dict) or len(schedule) != 1:
        raise ScheduleError(
            'schedule 은 {"daily_at": "HH:MM"} 또는 {"every_minutes": N} 중 하나입니다.'
        )
    if "daily_at" in schedule:
        if not _HHMM.fullmatch(str(schedule["daily_at"])):
            raise ScheduleError(
                f"daily_at 은 HH:MM(24시)이어야 합니다: {schedule['daily_at']!r}"
            )
        return {"daily_at": str(schedule["daily_at"])}
    if "every_minutes" in schedule:
        minutes = schedule["every_minutes"]
        if not isinstance(minutes, int) or minutes < 5:
            raise ScheduleError(
                "every_minutes 는 5 이상의 정수입니다(타이머가 5분마다 확인합니다)."
            )
        return {"every_minutes": minutes}
    raise ScheduleError(f"모르는 일정입니다: {', '.join(schedule)}")


def label(schedule: dict[str, Any] | None) -> str:
    if not schedule:
        return "수동"
    if "daily_at" in schedule:
        return f"매일 {schedule['daily_at']}"
    return f"{schedule['every_minutes']}분마다"


def due(schedule: dict[str, Any] | None, last: datetime | None, now: datetime) -> bool:
    """`last`(마지막 실행을 넣은 때) · `now` 는 같은 시간대여야 한다."""
    if not schedule:
        return False
    if "every_minutes" in schedule:
        return last is None or now - last >= timedelta(minutes=int(schedule["every_minutes"]))
    hour, minute = (int(part) for part in str(schedule["daily_at"]).split(":"))
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=1)
    return last is None or last < slot


def next_slot(schedule: dict[str, Any], last: datetime) -> datetime:
    """`last`(마지막 실행을 넣은 때) 다음 차례 — `due` 가 참이 되는 첫 때."""
    if "every_minutes" in schedule:
        return last + timedelta(minutes=int(schedule["every_minutes"]))
    hour, minute = (int(part) for part in str(schedule["daily_at"]).split(":"))
    slot = last.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return slot if slot > last else slot + timedelta(days=1)


def _span(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes >= 48 * 60:
        return f"{minutes // (24 * 60)}일"
    if minutes >= 60:
        return f"{minutes // 60}시간"
    return f"{minutes}분"


def late(
    schedule: dict[str, Any] | None,
    *,
    last: datetime | None,
    waiting: datetime | None,
    now: datetime,
    grace: timedelta,
) -> str:
    """늦었나 — 까닭 한 줄, 아니면 빈 글. `last` 는 끝난(성공이든 실패든) 마지막 실행을 넣은
    때, `waiting` 은 아직 안 끝난 가장 오래된 실행을 넣은 때. 셋 다 같은 시간대.

    - 안 끝난 실행이 `grace` 넘게 대기 · 실행 중이다 — 작업 워커가 멈췄거나 걸렸다.
    - 안 끝난 실행이 없는데 다음 차례가 `grace` 넘게 지났다 — 넣는 쪽(일정 타이머)이 멈췄다.

    한 번도 끝난 적이 없고 기다리는 것도 없으면 늦음이 아니다(「실행 전」)."""
    if not schedule or grace <= timedelta(0):
        return ""
    if waiting is not None:
        if now - waiting > grace:
            return (
                f"실행 작업이 {_span(now - waiting)}째 끝나지 않았습니다 — 작업 워커가 "
                "멈췄거나 원천 · 대상이 응답하지 않습니다."
            )
        return ""
    if last is None:
        return ""
    expected = next_slot(schedule, last)
    if now - expected > grace:
        return (
            f"{expected:%m-%d %H:%M} 차례의 실행이 {_span(now - expected)} 지나도록 "
            "없습니다 — 일정 타이머가 멈췄는지 확인하세요."
        )
    return ""
