"""일정 — 정의의 `schedule` 로 「지금 돌 차례인가」 를 가른다(타이머가 5분마다 묻는다).

    {"daily_at": "01:00"}      매일 그 시각(서버의 현지 시각) 뒤 첫 차례에 한 번
    {"every_minutes": 60}      마지막 실행에서 그만큼 지나면

일정이 없으면 화면 · 명령으로만 돈다. SP 연동 지침서의 확정(§12-A 1)은 「야간 1회 + 수동 실행」
이다.
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
            'schedule 은 {"daily_at": "HH:MM"} 또는 {"every_minutes": N} 하나다.'
        )
    if "daily_at" in schedule:
        if not _HHMM.match(str(schedule["daily_at"])):
            raise ScheduleError(
                f"daily_at 은 HH:MM(24시)이어야 합니다: {schedule['daily_at']!r}"
            )
        return {"daily_at": str(schedule["daily_at"])}
    if "every_minutes" in schedule:
        minutes = schedule["every_minutes"]
        if not isinstance(minutes, int) or minutes < 5:
            raise ScheduleError("every_minutes 는 5 이상의 정수다(타이머가 5분마다 본다).")
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
