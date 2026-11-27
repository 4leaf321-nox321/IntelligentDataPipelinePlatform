"""일정 — 정의의 `schedule` 로 「지금 돌 차례인가」 를 가른다(타이머가 5분마다 묻는다).

    {"daily_at": "01:00"}      매일 그 시각(서버의 현지 시각) 뒤 첫 차례에 한 번
    {"every_minutes": 60}      마지막 실행에서 그만큼 지나면

일정이 없으면 화면 · 명령으로만 돈다. SP 연동 지침서의 확정(§12-A 1)은 「야간 1회 + 수동 실행」
이다.

**늦음**(`late`) — 일정대로라면 끝났어야 할 실행이 끝나지 않았다. 실패 · 멈춤은 실행이 끝나며
알리지만, 실행이 아예 안 끝나면(작업 워커가 멈춤 · 실행이 걸림 · 타이머가 안 돎) 끝날 때 알리는
길로는 아무 말도 없다.

**대조의 일정**(`reconcile`) — 대상과의 대조(`relay_core.reconcile`)를 언제 하나. 정의마다
둔다:

    {"weekdays": ["sun"], "at": "03:00"}     매주 그 요일(여럿이면 그 요일마다)
    {"every_days": 3}                        마지막 대조에서 사흘마다
    {"weekdays": ["sun"], "every_days": 14}  그 요일에만, 마지막 대조에서 14일이 지났으면(격주)
    false                                    타이머가 대조하지 않는다(화면 · MCP 에서만)

`at` 을 안 적으면 서버의 `RELAY_RECONCILE_AT`, `reconcile` 을 안 적으면 서버의
`RELAY_RECONCILE_DAYS` · `RELAY_RECONCILE_AT` 그대로다. 차례가 와도 **그 파이프라인의 실행 ·
적용이 끝난 뒤에** 넣는다(`app/modules/relay/services.reconcile_due`) — 적용 중인 대상을 읽으면
반쯤 들어간 것을 어긋남으로 센다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ScheduleError(ValueError):
    expected = True
    """원인을 문장으로 아는 실패 — 작업 틀이 예외 이름 · 스택 없이 그 문장만 보인다."""


def check(schedule: Any) -> dict[str, Any] | None:
    """모양을 본다 — 모르는 칸 · 틀린 시각은 거절한다(조용히 안 도는 일정이 가장 나쁘다)."""
    if schedule in (None, {}):
        return None
    if not isinstance(schedule, dict) or len(schedule) != 1:
        raise ScheduleError(
            'schedule은 {"daily_at": "HH:MM"} 또는 {"every_minutes": N} 중 하나여야 합니다.'
        )
    if "daily_at" in schedule:
        if not _HHMM.fullmatch(str(schedule["daily_at"])):
            raise ScheduleError(
                f"daily_at은 HH:MM(24시간) 형식이어야 합니다: {schedule['daily_at']!r}"
            )
        return {"daily_at": str(schedule["daily_at"])}
    if "every_minutes" in schedule:
        minutes = schedule["every_minutes"]
        if not isinstance(minutes, int) or minutes < 5:
            raise ScheduleError(
                "every_minutes는 5 이상의 정수여야 합니다(타이머가 5분마다 확인합니다)."
            )
        return {"every_minutes": minutes}
    raise ScheduleError(f"지원하지 않는 실행 주기입니다: {', '.join(schedule)}")


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
                f"실행 작업이 {_span(now - waiting)}째 완료되지 않았습니다. 작업 워커가 "
                "중단되었거나 원천 또는 대상이 응답하지 않습니다."
            )
        return ""
    if last is None:
        return ""
    expected = next_slot(schedule, last)
    if now - expected > grace:
        return (
            f"{expected:%m-%d %H:%M} 예정 실행이 {_span(now - expected)} 지나도록 시작되지 "
            "않았습니다. 실행 주기 타이머가 중단되었는지 확인하세요."
        )
    return ""


# --- 대조의 일정 ------------------------------------------------------------------------

#: 요일 — 정의에는 영어 세 글자(`sun`)나 한 글자(`일`)로. 차례는 `datetime.weekday()`.
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_KO_DAYS = "월화수목금토일"
#: 대조 간격의 상한(일).
EVERY_DAYS_MAX = 365
_RECONCILE_KEYS = ("weekdays", "every_days", "at")


@dataclass(frozen=True)
class Reconcile:
    """파이프라인의 대조 일정 — 정의의 `reconcile`(`check_reconcile`)."""

    weekdays: tuple[int, ...] = ()
    """이 요일에만(`datetime.weekday()` — 월 0 … 일 6). 비면 요일을 가리지 않는다."""
    every_days: int = 0
    """마지막 대조에서 적어도 이만큼(일). 0 이면 그 요일마다."""
    at: str = ""
    """그날 이 시각(HH:MM, 서버의 현지 시각) 뒤 첫 차례에. 비면 서버의 `RELAY_RECONCILE_AT`."""
    off: bool = False
    """`"reconcile": false` — 타이머가 대조하지 않는다."""

    def to_json(self) -> dict[str, Any] | bool:
        if self.off:
            return False
        out: dict[str, Any] = {}
        if self.weekdays:
            out["weekdays"] = [WEEKDAYS[one] for one in self.weekdays]
        if self.every_days:
            out["every_days"] = self.every_days
        if self.at:
            out["at"] = self.at
        return out


def _weekday(value: Any) -> int:
    text = str(value).strip().lower()
    if text in WEEKDAYS:
        return WEEKDAYS.index(text)
    if len(text) == 1 and text in _KO_DAYS:
        return _KO_DAYS.index(text)
    raise ScheduleError(
        f"reconcile.weekdays의 요일은 {', '.join(WEEKDAYS)} 또는 {', '.join(_KO_DAYS)} 중 "
        f"하나여야 합니다: {value!r}"
    )


def check_reconcile(spec: Any) -> Reconcile | None:
    """정의의 `reconcile` → 일정. 안 적었으면(`true` 도) None — 서버의 기본을 따른다. 모르는
    칸 · 틀린 요일 · 시각은 거절한다(조용히 안 도는 대조가 가장 나쁘다)."""
    if spec is None or spec is True or spec == {}:
        return None
    if spec is False:
        return Reconcile(off=True)
    if not isinstance(spec, dict):
        raise ScheduleError(
            'reconcile은 false 또는 {"weekdays": ["sun"], "every_days": N, "at": "HH:MM"} '
            "모양이어야 합니다."
        )
    unknown = sorted(set(spec) - set(_RECONCILE_KEYS))
    if unknown:
        raise ScheduleError(
            f"reconcile: 지원하지 않는 항목입니다: {', '.join(unknown)} "
            f"(허용 항목: {', '.join(_RECONCILE_KEYS)})"
        )
    given = spec.get("weekdays")
    names = [given] if isinstance(given, str) else given
    if given is not None and (not isinstance(names, list) or not names):
        raise ScheduleError('reconcile.weekdays는 요일 목록이어야 합니다(예: ["sun"]).')
    weekdays = tuple(sorted({_weekday(one) for one in names or []}))
    every = spec.get("every_days", 0)
    whole = isinstance(every, int) and not isinstance(every, bool)
    if "every_days" in spec and not (whole and 1 <= every <= EVERY_DAYS_MAX):
        raise ScheduleError(
            f"reconcile.every_days는 1 이상 {EVERY_DAYS_MAX} 이하의 정수여야 합니다: {every!r}"
        )
    if not weekdays and not every:
        raise ScheduleError(
            "reconcile에는 weekdays(요일) 또는 every_days(며칠마다) 중 하나가 필요합니다. "
            "서버의 기본 주기를 따르려면 reconcile을 적지 않습니다."
        )
    at = str(spec.get("at") or "")
    if "at" in spec and not _HHMM.fullmatch(at):
        raise ScheduleError(f"reconcile.at은 HH:MM(24시간) 형식이어야 합니다: {spec['at']!r}")
    return Reconcile(weekdays=weekdays, every_days=int(every), at=at)


def reconcile_rule(rule: Reconcile | None, *, days: int, at: str) -> Reconcile | None:
    """정의의 일정에 서버의 기본(`RELAY_RECONCILE_DAYS` · `_AT`)을 채운다. 대조하지 않으면
    None — 정의가 `false` 거나, 정의에 없고 서버가 0 일 때."""
    if rule is None:
        return Reconcile(every_days=days, at=at) if days > 0 else None
    if rule.off:
        return None
    return rule if rule.at else Reconcile(rule.weekdays, rule.every_days, at)


def reconcile_label(rule: Reconcile | None) -> str:
    """「매주 일요일 03:00」 · 「매주 월·목 03:00」 · 「7일마다 03:00」 · 「14일마다 · 일요일
    03:00」 · 「수동」."""
    if rule is None:
        return "수동"
    days = "·".join(_KO_DAYS[one] for one in rule.weekdays)
    if len(rule.weekdays) == 1:
        days += "요일"
    elif len(rule.weekdays) == len(WEEKDAYS):
        days = ""
    if rule.every_days > 1:
        return f"{rule.every_days}일마다{f' · {days}' if days else ''} {rule.at}"
    return f"매주 {days} {rule.at}" if days else f"매일 {rule.at}"


def reconcile_slot(rule: Reconcile, now: datetime) -> datetime:
    """`now` 와 같거나 앞선 마지막 차례 — 그 요일(있으면)의 `at`."""
    hour, minute = (int(part) for part in rule.at.split(":"))
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot > now:
        slot -= timedelta(days=1)
    while rule.weekdays and slot.weekday() not in rule.weekdays:
        slot -= timedelta(days=1)
    return slot


def reconcile_due(
    rule: Reconcile | None, *, last: datetime | None, first: datetime | None, now: datetime
) -> bool:
    """대조할 차례인가 — `last` 는 마지막으로 **한**(미루지 않은) 대조를 넣은 때, `first` 는 첫
    적용. 처음이면 첫 적용 뒤 처음 맞는 차례에(적용하고 5분 만에 견주지 않게). 셋 다 같은
    시간대여야 한다. 실행 · 적용이 도는지는 부르는 쪽이 본다."""
    if rule is None or not rule.at or first is None:
        return False
    slot = reconcile_slot(rule, now)
    if last is None:
        return first < slot
    return last < slot - timedelta(days=max(rule.every_days, 1) - 1)
