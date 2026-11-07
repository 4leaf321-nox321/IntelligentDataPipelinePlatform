"""계산 칸 — 어댑터가 낸 열로 **새 열**을 만든다(정의의 `computed`). 레시피(`sp-table/1`)는
틀의 것이라 고칠 수 없고, 그 형식에는 여러 열 잇기 · 숫자 · 단위 · 시각대 바꾸기가 없다.

    "computed": {
      "FULL_NAME": {"template": "{LAST} {FIRST}"},
      "WEIGHT_KG": {"number": "WEIGHT_G", "multiply": 0.001, "round": 3},
      "MEASURED_AT": {"datetime": "MEAS_TS", "format": "%Y%m%d%H%M%S",
                      "timezone": "Asia/Seoul"},
      "PART_NO": {"replace": "RAW_PART", "pattern": "^P-", "with": ""},
      "OWNER": {"first": ["OWNER_ID", "CREATOR_ID"]}
    }

- **변환할 때 미러 전체에** 건다(`convert.write_source`) — 받을 때 걸면 규칙을 바꾼 날 옛 행은
  옛 값으로 남아 비교가 섞인다. 미러에는 원천 그대로가 남는다.
- 차례대로 계산한다 — 뒤의 칸이 앞의 계산 칸을 읽어도 된다. 어댑터의 열 이름과 겹치면 안 된다.
- 못 바꾸는 값(숫자가 아님 · 날짜 모양이 아님)은 **빈 값**이 된다 — 짐작해서 채우지 않는다.
  레시피 시험의 보고서가 빈 칸을 센다.
- 시각(`datetime`)은 UTC 의 ISO(`2026-11-07T01:00:00Z`)로 낸다. 시각대가 없는 값은 `timezone`
  의 현지 시각으로 읽는다. `"output": "date"` 면 그 시각대의 날짜(`2026-11-07`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from relay_core.records import SourceRecord

OPS = ("template", "number", "datetime", "replace", "first")
_KEYS = {
    "template": {"template"},
    "number": {"number", "multiply", "add", "round"},
    "datetime": {"datetime", "format", "timezone", "output"},
    "replace": {"replace", "pattern", "with"},
    "first": {"first"},
}
_SLOT = re.compile(r"\{([^{}]+)\}")


class ComputeError(ValueError):
    pass


def check(spec: Any, columns: Iterable[str] = ()) -> dict[str, dict[str, Any]]:
    """정의의 `computed` — 모르는 계산 · 칸 · 틀린 정규식 · 시각대는 거절한다. 어댑터의 열과
    겹치는 이름도."""
    if spec is None:
        return {}
    if not isinstance(spec, dict):
        raise ComputeError("computed 는 {새 열: 계산} 모양이어야 합니다.")
    taken = set(columns)
    out: dict[str, dict[str, Any]] = {}
    for name, rule in spec.items():
        where = f"computed.{name}"
        if not str(name).strip():
            raise ComputeError("computed 의 새 열 이름이 비었습니다.")
        if name in taken:
            raise ComputeError(f"{where}: 어댑터가 이미 내는 열입니다 — 다른 이름을 쓰세요.")
        if not isinstance(rule, dict):
            raise ComputeError(f"{where} 는 {{계산: …}} 객체입니다.")
        ops = [one for one in OPS if one in rule]
        if len(ops) != 1:
            raise ComputeError(f"{where}: 계산은 {' · '.join(OPS)} 중 하나만 적습니다.")
        op = ops[0]
        unknown = sorted(set(rule) - _KEYS[op])
        if unknown:
            raise ComputeError(f"{where}: 모르는 칸입니다: {', '.join(unknown)}")
        if op == "first":
            if not isinstance(rule["first"], list) or not rule["first"]:
                raise ComputeError(f"{where}.first 는 열 이름의 목록입니다.")
        elif not isinstance(rule[op], str) or not rule[op].strip():
            raise ComputeError(f"{where}.{op} 이 비었습니다.")
        if op == "replace":
            try:
                re.compile(str(rule.get("pattern") or ""))
            except re.error as failure:
                raise ComputeError(
                    f"{where}.pattern 이 정규식이 아닙니다: {failure}"
                ) from None
        if op == "datetime":
            zone = str(rule.get("timezone") or "")
            if zone:
                try:
                    ZoneInfo(zone)
                except (ZoneInfoNotFoundError, ValueError):
                    raise ComputeError(f"{where}.timezone 을 모릅니다: {zone!r}") from None
            if rule.get("output", "utc") not in ("utc", "date"):
                raise ComputeError(f"{where}.output 은 utc · date 입니다.")
        if op == "number":
            for one in ("multiply", "add"):
                if one in rule and (
                    isinstance(rule[one], bool) or not isinstance(rule[one], int | float)
                ):
                    raise ComputeError(f"{where}.{one} 은 수입니다.")
            places = rule.get("round")
            if places is not None and (
                isinstance(places, bool)
                or not isinstance(places, int)
                or not 0 <= places <= 12
            ):
                raise ComputeError(f"{where}.round 는 0 ~ 12 의 정수입니다.")
        taken.add(str(name))
        out[str(name)] = dict(rule)
    return out


def inputs(rule: Mapping[str, Any]) -> list[str]:
    """그 계산이 읽는 열 — 칸 대응 표가 원천의 자리까지 잇는 데 쓴다."""
    if "template" in rule:
        return _SLOT.findall(str(rule["template"]))
    if "first" in rule:
        return [str(one) for one in rule["first"]]
    for op in ("number", "datetime", "replace"):
        if op in rule:
            return [str(rule[op])]
    return []


def describe(rule: Mapping[str, Any]) -> str:
    """칸 대응 표의 「변환」 한 줄."""
    if "template" in rule:
        return f"잇기 {rule['template']}"
    if "number" in rule:
        return f"숫자 * {rule.get('multiply', 1)} + {rule.get('add', 0)}"
    if "datetime" in rule:
        zone = rule.get("timezone") or "UTC"
        return f"시각({zone} → {'날짜' if rule.get('output') == 'date' else 'UTC'})"
    if "replace" in rule:
        return f"바꾸기 /{rule.get('pattern', '')}/"
    return "앞의 빈 값이 아닌 것"


def _number(text: str, rule: Mapping[str, Any]) -> str:
    cleaned = text.replace(",", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return ""
    value = value * float(rule.get("multiply", 1)) + float(rule.get("add", 0))
    places = rule.get("round")
    if places is not None:
        value = round(value, int(places))
    return str(int(value)) if value.is_integer() else repr(value)


def _datetime(text: str, rule: Mapping[str, Any]) -> str:
    raw = text.strip()
    if not raw:
        return ""
    try:
        if rule.get("format"):
            moment = datetime.strptime(raw, str(rule["format"]))
        else:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    zone = ZoneInfo(str(rule["timezone"])) if rule.get("timezone") else UTC
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    if rule.get("output") == "date":
        return moment.astimezone(zone).date().isoformat()
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def values_with(
    values: Mapping[str, str], rules: Mapping[str, Mapping[str, Any]]
) -> dict[str, str]:
    """행 하나에 계산 칸을 더한 값 — 차례대로(뒤의 계산이 앞의 것을 읽는다)."""
    out = dict(values)
    for name, rule in rules.items():
        if "template" in rule:
            used = _SLOT.findall(str(rule["template"]))
            filled = _SLOT.sub(
                lambda found: out.get(found.group(1), ""), str(rule["template"])
            )
            out[name] = filled.strip() if any(out.get(one) for one in used) else ""
        elif "number" in rule:
            out[name] = _number(out.get(str(rule["number"]), ""), rule)
        elif "datetime" in rule:
            out[name] = _datetime(out.get(str(rule["datetime"]), ""), rule)
        elif "replace" in rule:
            text = out.get(str(rule["replace"]), "")
            out[name] = re.sub(
                str(rule.get("pattern") or ""), str(rule.get("with") or ""), text
            )
        elif "first" in rule:
            out[name] = next((out[one] for one in rule["first"] if out.get(one)), "")
    return out


class Rows(Protocol):
    def __len__(self) -> int: ...

    def rows(self) -> Iterator[SourceRecord]: ...


@dataclass
class WithComputed:
    """미러(메모리 · 파일)에 계산 칸을 얹어 원천 표를 쓴다 — `convert.Source` 의 모양."""

    source: Rows
    rules: Mapping[str, Mapping[str, Any]]

    def __len__(self) -> int:
        return len(self.source)

    def _rows(self) -> Iterator[SourceRecord]:
        for one in self.source.rows():
            yield SourceRecord(
                key=one.key,
                values=values_with(one.values, self.rules),
                updated_at=one.updated_at,
                deleted=one.deleted,
            )

    def write_csv(self, path: Path) -> Path:
        from relay_core.mirror import write_csv

        return write_csv(self._rows, path)


def wrap(source: Any, rules: Mapping[str, Mapping[str, Any]] | None) -> Any:
    """계산 칸이 있으면 얹고, 없으면 그대로."""
    return WithComputed(source, rules) if rules else source
