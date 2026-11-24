"""받기 점검 — 받은 것을 미러에 **반영하기 전에** 두 가지를 본다. 걸리면 실행을 멈춘다.

    대량 삭제   이번 받기로 미러의 많은 행이 한꺼번에 삭제된다 — 전량을 받는 날 원천이
                일부만 주면(쪽 넘기기가 도중에 끊김 · 권한이 바뀌어 일부만 보임 · 빈 목록)
                「이번에 안 온 것」 이 다 삭제로 읽힌다.
    빈 칸       미러에서 늘 값이 있던 칸이 받은 행 **모두**에서 비었다 — 원천에서 칸 이름이
                바뀌면 어댑터는 오류 없이 빈 값을 낸다(`rest.value_at` 은 없는 자리를 None
                으로 준다). 미러의 열은 옛 행까지 합친 것이라 틀의 「열이 원천에 없습니다」
                에도 안 걸리고, 그 칸을 비우는 계획이 정상처럼 올라간다 — 자동 적용이 켜져
                있으면 그대로 들어간다.

정의의 `intake` — 없으면 아래 기본값으로 켜져 있다:

    "intake": {"delete_ratio": 0.05, "delete_rows": 10, "blank_rows": 3}
    "intake": false        점검하지 않는다

- 대량 삭제: 삭제가 `delete_rows` 건을 넘고 **동시에** 미러의 `delete_ratio` 를 넘으면.
- 빈 칸: 미러의 절반 넘는 행에 값이 있던 칸이, 받은 행 모두에서 비었고, 그 때문에 값을 잃는
  기존 행이 `blank_rows` 건 이상이면. 새로 생긴 행만 비어 있는 것(아직 안 채운 칸)은 아니다.

**멈추면 아무것도 반영하지 않는다** — 미러 · 워터마크 · 증분 횟수가 그대로라 다음 실행이
같은 것을 다시 받는다. 원천 · 어댑터를 고치면 그 실행이 저절로 지나가고, 맞는 변경이면
사람이 화면에서 「확인 후 진행」 을 클릭한다(`Accept` — 확인한 건수 · 칸까지만 지나간다).
토큰(MCP)으로는 확인할 수 없다 — 계획의 적용과 같다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class IntakeError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    delete_ratio: float = 0.05
    """미러의 이 비율을 넘게 삭제하면(그리고 `delete_rows` 건을 넘으면) 멈춘다."""
    delete_rows: int = 10
    """이 건수 이하의 삭제는 비율과 상관없이 지나간다 — 작은 원천(기준 코드 몇십 개)에서 몇 건
    삭제한 것이 비율로는 크게 보인다."""
    blank_rows: int = 3
    """빈 칸으로 값을 잃는 기존 행이 이만큼 되면 멈춘다."""


#: 「늘 값이 있던 칸」 — 미러의 이 비율 넘는 행에 값이 있었다. 대개 비어 있는 칸(체크아웃한
#: 사람 · 반려 사유)은 받은 행 모두에서 비어도 이상하지 않다.
FILLED = 0.5

_KEYS = {"delete_ratio", "delete_rows", "blank_rows"}


def check(spec: Any) -> Policy | None:
    """정의의 `intake` → 기준. `false` 면 None(점검 안 함), 없으면 기본값. 틀리면
    `IntakeError`."""
    if spec is False:
        return None
    if spec is None or spec is True:
        return Policy()
    if not isinstance(spec, dict):
        raise IntakeError("intake는 false 또는 기준 객체여야 합니다.")
    unknown = sorted(set(spec) - _KEYS)
    if unknown:
        raise IntakeError(f"intake: 지원하지 않는 항목입니다: {', '.join(unknown)}")
    ratio = spec.get("delete_ratio", Policy.delete_ratio)
    rows = spec.get("delete_rows", Policy.delete_rows)
    blank = spec.get("blank_rows", Policy.blank_rows)
    if isinstance(ratio, bool) or not isinstance(ratio, int | float) or not 0 < ratio <= 1:
        raise IntakeError(
            "intake.delete_ratio는 0보다 크고 1 이하인 수여야 합니다(0.05 = 5%)."
        )
    if isinstance(rows, bool) or not isinstance(rows, int) or rows < 0:
        raise IntakeError("intake.delete_rows는 0 이상의 정수여야 합니다.")
    if isinstance(blank, bool) or not isinstance(blank, int) or blank < 1:
        raise IntakeError("intake.blank_rows는 1 이상의 정수여야 합니다.")
    return Policy(delete_ratio=float(ratio), delete_rows=rows, blank_rows=blank)


def label(policy: Policy | None) -> str:
    if policy is None:
        return "미사용"
    return (
        f"삭제 {policy.delete_rows}건 · 미러의 {policy.delete_ratio:.0%} 초과 · 빈 필드 "
        f"{policy.blank_rows}건 이상 시 중단"
    )


@dataclass
class Tally:
    """미러에 반영하며 센 것(`mirror.Merge`) — 점검의 재료."""

    before: int = 0
    """반영 전 미러의 행 수."""
    removed: int = 0
    old_filled: Counter[str] = field(default_factory=Counter)
    """반영 전 미러에서 칸마다 값이 있던 행 수."""
    received: int = 0
    """받은 행(삭제 표시 빼고)."""
    filled: Counter[str] = field(default_factory=Counter)
    """받은 행에서 칸마다 값이 있던 행 수."""
    lost: Counter[str] = field(default_factory=Counter)
    """값이 있던 기존 행이 이번에 그 칸을 잃은 수(빈 값 · 칸이 없음)."""


@dataclass(frozen=True)
class Blank:
    column: str
    lost: int
    """값을 잃는 기존 행 — 앞선 실행에서 이미 잃은 것(`carried`)까지."""
    filled: int
    """반영 전 미러에서 값이 있던 행."""
    carried: int = 0
    """앞선 실행들이 그 칸을 이미 비운 기존 행 — 그 실행들에서도 받은 행 모두에서 비었다."""


@dataclass(frozen=True)
class Findings:
    before: int = 0
    deleted: int = 0
    """기준을 넘은 삭제 — 넘지 않았으면 0."""
    blanks: tuple[Blank, ...] = ()
    received: int = 0

    @property
    def held(self) -> bool:
        return bool(self.deleted or self.blanks)

    def to_json(self) -> dict[str, Any]:
        return {
            "before": self.before,
            "deleted": self.deleted,
            "received": self.received,
            "blanks": [
                {
                    "column": one.column,
                    "lost": one.lost,
                    "filled": one.filled,
                    "carried": one.carried,
                }
                for one in self.blanks
            ],
        }

    def reasons(self) -> list[str]:
        """화면 · 알림에 쓰는 까닭 — 한 줄씩."""
        out: list[str] = []
        if self.deleted:
            out.append(
                f"대량 삭제: 이번 수신으로 미러 {self.before:,}건 중 "
                f"{self.deleted:,}건({self.deleted / self.before:.1%})이 삭제됩니다. 원천이 "
                "데이터를 일부만 제공하지 않았는지(페이지 처리, 권한, 빈 응답) 확인하세요."
            )
        for one in self.blanks:
            earlier = (
                f"(이전 실행에서 이미 빈 값이 된 {one.carried:,}건 포함)"
                if one.carried
                else ""
            )
            out.append(
                f"빈 필드 {one.column}: 수신 행 {self.received:,}건(삭제 표시 제외) 모두에서 "
                f"값이 비어 있습니다. 값이 있던 {one.lost:,}건{earlier}이 빈 값이 됩니다. "
                f"반영 전 미러에서 값이 있던 행은 {one.filled:,}건입니다. 원천의 필드 이름이 "
                "변경되지 않았는지 어댑터의 columns를 확인하세요."
            )
        return out


@dataclass(frozen=True)
class Accept:
    """사람이 「확인 후 진행」 으로 지나가게 한 것 — 확인한 건수 · 칸**까지만**. 그사이 원천이
    더 나빠졌으면(삭제가 늘었거나 다른 칸이 비었으면) 다시 멈춘다."""

    deleted: int = 0
    columns: tuple[str, ...] = ()
    by: str = ""

    @classmethod
    def of(cls, held: dict[str, Any] | None, by: str = "") -> Accept | None:
        """상태에 적힌 멈춤(`State.held`)에서 — 없으면 None."""
        if not held:
            return None
        found = held.get("findings") or {}
        return cls(
            deleted=int(found.get("deleted") or 0),
            columns=tuple(str(one.get("column")) for one in found.get("blanks") or []),
            by=by,
        )

    def covers(self, found: Findings) -> bool:
        if found.deleted > self.deleted:
            return False
        return all(one.column in self.columns for one in found.blanks)


def carry(before: Mapping[str, int], tally: Tally) -> dict[str, int]:
    """**실행을 넘어** 빈 칸을 센다 — 한 번에 두세 행씩 바뀌는 원천은 한 실행만으로는 기준
    (`blank_rows`)에 안 걸리고, 칸 이름이 바뀐 뒤로 밤마다 몇 행씩 조용히 비워 간다. 받은 행
    모두에서 빈 칸은 잃은 수를 더해 두고, 받은 행 하나라도 값이 있으면 지운다(칸은 살아
    있다)."""
    out = dict(before)
    for column in set(tally.old_filled) | set(out):
        if tally.filled[column]:
            out.pop(column, None)
        elif tally.lost[column]:
            out[column] = out.get(column, 0) + tally.lost[column]
    return out


def inspect(
    policy: Policy | None, tally: Tally, carried: Mapping[str, int] | None = None
) -> Findings:
    """기준을 넘은 것만 — 안 넘었으면 빈 `Findings`. `carried` 는 앞선 실행들이 이미 비운
    수(`carry`) — 이번에도 값을 잃는 칸에만 더한다."""
    carried = carried or {}
    if policy is None:
        return Findings()
    deleted = 0
    if (
        tally.before
        and tally.removed > policy.delete_rows
        and tally.removed / tally.before > policy.delete_ratio
    ):
        deleted = tally.removed
    blanks: list[Blank] = []
    if tally.received and tally.before:
        for column, filled in sorted(tally.old_filled.items()):
            if filled / tally.before <= FILLED or tally.filled[column]:
                continue
            if not tally.lost[column]:
                continue  # 이번엔 값을 잃는 행이 없다 — 증거가 없는 실행에서 멈추지 않는다
            earlier = int(carried.get(column, 0))
            if tally.lost[column] + earlier >= policy.blank_rows:
                blanks.append(
                    Blank(
                        column=column,
                        lost=tally.lost[column] + earlier,
                        filled=filled,
                        carried=earlier,
                    )
                )
    return Findings(
        before=tally.before, deleted=deleted, blanks=tuple(blanks), received=tally.received
    )
