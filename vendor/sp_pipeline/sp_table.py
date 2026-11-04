#!/usr/bin/env python3
"""표(CSV) → 실행 폴더 — **규칙으로** 옮기는 정제.

    python sp_table.py <대응.json> <원천.csv> runs/2026-09-13-plm-models

한 행에 여러 타입이 섞인 표(프로젝트 · 과제 · 모델이 한 줄에)를 **대응 파일**대로 타입마다
나눠 실행 폴더를 만든다. 그 뒤는 여느 실행과 같다 — `sp_pipeline.py validate · preview ·
apply`.

- 행마다 AI 가 판단하지 않는다. 같은 원천과 같은 대응이면 **늘 같은 결과**가 나온다.
- 같은 식별자가 여러 행에 나오면 한 객체로 모은다. **행마다 값이 다르면 짐작하지 않고**
  `unresolved.json` 에 올린다 — 그 칸이 사실은 윗단계가 아니라 행의 속성이라는 신호다.
- 이름 안에 뜻이 박힌 코드(`모델_지역_버전_사업자`)는 **해석기**로 조각을 칸에 나눈다.
  못 나눈 행은 넣되 조각 칸을 비우고 보고서에 올린다.
- 보고서(`table-report.txt`)는 값을 **가린 패턴**(영문 A · 숫자 9 · 한글 가)으로만 적는다 —
  사내 데이터를 들고 나오지 않고도 결과를 옮겨 규칙을 고칠 수 있다.

대응 파일의 모양은 같은 폴더의 `AGENTS.md` 가 정본이다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import sp_pipeline as pipeline

FORMAT = "sp-table/1"
REPORT = "table-report.txt"
ENCODINGS = ("utf-8-sig", "cp949")
"""엑셀이 저장한 CSV 는 대개 둘 중 하나다 — BOM 붙은 UTF-8, 또는 한글 윈도의 CP949."""
DATE_RE = re.compile(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})$")
BLANK = "<blank>"
DATE = "<date>"
TOP = 10
BOUNDARIES = "_-/. "
"""참조 대조에서 「앞부분」 을 가르는 자리 — 코드가 끊기는 글자."""
MATCH_OUTCOMES = (
    "그대로",
    "대소문자만 다름",
    "앞부분이 하나",
    "별칭·이름으로",
    "여러 개",
    "없음",
)
ON_MISSING = ("blank", "unresolved", "keep")
AGGREGATES = ("collect", "join")
"""**여러 행의 값을 하나로 모으는 칸.** 같은 식별자가 여러 행에 나올 때, 값이 갈리는 것이
오류가 아니라 **그것이 답인** 칸이 있다 — 별칭 여럿, 담당자 여럿, 줄마다 다른 메모.
`collect` 는 목록으로, `join` 은 한 글로 잇는다."""

Stop = pipeline.Stop


def mask(value: str) -> str:
    """값을 모양만 남긴다 — `SM-F907N_KOR` → `AA-A999A_AAA`."""
    out: list[str] = []
    for char in value:
        if "가" <= char <= "힣" or "ㄱ" <= char <= "ㆎ":
            out.append("가")
        elif char.isdigit():
            out.append("9")
        elif char.isalpha():
            out.append("A")
        else:
            out.append(char)
    return "".join(out)


def _top(counter: Counter[str], limit: int = TOP) -> str:
    shown = " · ".join(f"{name} {count}" for name, count in counter.most_common(limit))
    rest = len(counter) - limit
    return shown + (f" · 외 {rest}종" if rest > 0 else "")


# --------------------------------------------------------------------------
# 원천 읽기
# --------------------------------------------------------------------------


@dataclass
class Table:
    name: str
    encoding: str
    header: list[str]
    rows: list[tuple[int, dict[str, str]]]
    """(시트의 행 번호 — 머리글이 1, 값) — 출처(`_source.row`)에 그대로 쓴다."""
    blank_rows: int
    sha256: str


def read_table(path: Path, *, encoding: str = "auto", delimiter: str = ",") -> Table:
    try:
        raw = path.read_bytes()
    except OSError as failure:
        raise Stop(f"원천을 읽을 수 없습니다: {path} ({failure})") from failure
    tried = ENCODINGS if encoding == "auto" else (encoding,)
    for name in tried:
        try:
            text = raw.decode(name)
        except UnicodeDecodeError:
            continue
        break
    else:
        raise Stop(
            f"{path.name}: 글자를 읽을 수 없습니다({', '.join(tried)}) — "
            "대응 파일의 source.encoding 에 인코딩을 적으세요"
        )
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = [one.strip() for one in next(reader, [])]
    if not any(header):
        raise Stop(f"{path.name}: 첫 줄(머리글)이 비어 있습니다")
    doubled = sorted(name for name, count in Counter(header).items() if count > 1 and name)
    if doubled:
        raise Stop(f"{path.name}: 머리글에 같은 이름이 둘 이상입니다 — {', '.join(doubled)}")
    rows: list[tuple[int, dict[str, str]]] = []
    blank = 0
    for number, record in enumerate(reader, start=2):
        if not any(cell.strip() for cell in record):
            blank += 1
            continue
        if len(record) > len(header) and any(cell.strip() for cell in record[len(header) :]):
            raise Stop(f"{path.name} {number}행: 머리글보다 칸이 많습니다({len(record)}칸)")
        rows.append(
            (
                number,
                {
                    name: (record[i] if i < len(record) else "")
                    for i, name in enumerate(header)
                },
            )
        )
    return Table(
        name=path.name,
        encoding=name,
        header=header,
        rows=rows,
        blank_rows=blank,
        sha256=hashlib.sha256(raw).hexdigest(),
    )


# --------------------------------------------------------------------------
# 해석기 — 이름 안에 박힌 조각을 칸으로
# --------------------------------------------------------------------------


@dataclass
class Slot:
    name: str
    optional: bool = False
    values: set[str] | None = None
    pattern: re.Pattern[str] | None = None
    kinds: list[tuple[str, re.Pattern[str]]] = field(default_factory=list)
    rare_below: int | None = None
    """해석기의 `rare_below` 를 이 조각만 바꾼다 — 기본 모델코드처럼 원래 종류가 많은
    조각은 0."""

    def match(self, token: str) -> tuple[bool, str | None]:
        if self.values is not None and token not in self.values:
            return False, None
        if self.pattern is not None and not self.pattern.search(token):
            return False, None
        if not self.kinds:
            return True, None
        # 종류는 **적은 차례대로** 본다 — 먼저 맞는 것이 그 조각의 종류다.
        for kind, pattern in self.kinds:
            if pattern.search(token):
                return True, kind
        return False, None


@dataclass
class Parsed:
    slots: dict[str, tuple[str, str | None]]
    """조각 이름 → (값, 종류)."""
    failure: str = ""
    """비어 있으면 읽힌 것. `못 나눔` · `두 갈래` 는 해석 실패, `검증:` 은 규칙 위반."""


@dataclass
class ParserStats:
    total: int = 0
    ok: int = 0
    failures: dict[str, Counter[str]] = field(default_factory=dict)
    kinds: dict[str, Counter[str]] = field(default_factory=dict)
    values: dict[str, Counter[str]] = field(default_factory=dict)
    present: Counter[str] = field(default_factory=Counter)


class Parser:
    def __init__(
        self,
        name: str,
        spec: dict[str, Any],
        dictionaries: dict[str, set[str]],
        header: list[str],
    ) -> None:
        self.name = name
        self.column = _column(spec, header, where=f"parsers.{name}")
        self.separator = str(spec.get("separator") or "_")
        self.rare_below = int(spec.get("rare_below") or 0)
        self.slots: list[Slot] = []
        for index, one in enumerate(spec.get("slots") or [], start=1):
            where = f"parsers.{name}.slots[{index}]"
            if not isinstance(one, dict) or not one.get("name"):
                raise Stop(f"대응 파일: {where} 에 name 이 없습니다")
            values: set[str] | None = None
            if "values" in one:
                values = {str(value) for value in one["values"]}
            if "dictionary" in one:
                wanted = str(one["dictionary"])
                if wanted not in dictionaries:
                    raise Stop(
                        f"대응 파일: {where} 의 사전 {wanted!r} 가 dictionaries 에 없습니다"
                    )
                values = (values or set()) | dictionaries[wanted]
            self.slots.append(
                Slot(
                    name=str(one["name"]),
                    optional=bool(one.get("optional")),
                    rare_below=int(one["rare_below"]) if "rare_below" in one else None,
                    values=values,
                    pattern=_regex(one.get("pattern"), where),
                    kinds=[
                        (str(kind), _compiled(pattern, where))
                        for kind, pattern in (one.get("kinds") or {}).items()
                    ],
                )
            )
        if not self.slots:
            raise Stop(f"대응 파일: parsers.{name} 에 slots 가 없습니다")
        self.slot_names = {slot.name for slot in self.slots}
        self.checks: list[dict[str, Any]] = []
        for index, check in enumerate(spec.get("checks") or [], start=1):
            where = f"parsers.{name}.checks[{index}]"
            slot = str(check.get("slot") or check.get("require") or "")
            if slot not in self.slot_names:
                raise Stop(f"대응 파일: {where} 의 조각 {slot!r} 가 slots 에 없습니다")
            if "suffix_of" in check:
                _column({"column": check["suffix_of"]}, header, where=where)
            elif "require" in check:
                _column({"column": check.get("when_column")}, header, where=where)
            else:
                raise Stop(f"대응 파일: {where} 는 suffix_of 나 require 중 하나여야 합니다")
            self.checks.append(check)
        self.stats = ParserStats()
        self._cache: dict[int, Parsed] = {}

    def _assign(self, tokens: list[str], at: int, slot: int) -> list[dict[str, Any]]:
        if slot == len(self.slots):
            return [{}] if at == len(tokens) else []
        current = self.slots[slot]
        found: list[dict[str, Any]] = []
        if current.optional:
            found.extend(self._assign(tokens, at, slot + 1))
        if at < len(tokens):
            ok, kind = current.match(tokens[at])
            if ok:
                for rest in self._assign(tokens, at + 1, slot + 1):
                    found.append({current.name: (tokens[at], kind), **rest})
        return found

    def parse(self, number: int, row: dict[str, str]) -> Parsed:
        if number in self._cache:
            return self._cache[number]
        text = row[self.column].strip()
        parsed = self._parse(text, row)
        self._cache[number] = parsed
        self._count(text, parsed)
        return parsed

    def _parse(self, text: str, row: dict[str, str]) -> Parsed:
        if not text:
            return Parsed({}, "비어 있음")
        tokens = text.split(self.separator)
        ways = self._assign(tokens, 0, 0)
        if not ways:
            return Parsed({}, f"못 나눔(조각 {len(tokens)}개)")
        if len(ways) > 1:
            return Parsed({}, "두 갈래로 읽힘")
        slots = ways[0]
        for check in self.checks:
            if "suffix_of" in check:
                name = str(check["slot"])
                if name not in slots:
                    continue
                joiner = str(check.get("joiner", self.separator))
                other = row[str(check["suffix_of"])].strip()
                if not other.endswith(f"{joiner}{slots[name][0]}"):
                    return Parsed({}, f"검증: {name} 이(가) {check['suffix_of']} 의 끝과 다름")
            else:
                name = str(check["require"])
                when = row[str(check["when_column"])].strip()
                if when in {str(one) for one in check.get("in") or []} and name not in slots:
                    return Parsed(
                        {}, f"검증: {check['when_column']} 이(가) {when} 인데 {name} 없음"
                    )
        return Parsed(slots)

    def _count(self, text: str, parsed: Parsed) -> None:
        stats = self.stats
        stats.total += 1
        if parsed.failure:
            stats.failures.setdefault(parsed.failure, Counter())[mask(text)] += 1
            return
        stats.ok += 1
        for name, (value, kind) in parsed.slots.items():
            stats.present[name] += 1
            stats.values.setdefault(name, Counter())[value] += 1
            if kind is not None:
                stats.kinds.setdefault(name, Counter())[kind] += 1

    def report(self) -> list[str]:
        stats = self.stats
        failed = sum(sum(one.values()) for one in stats.failures.values())
        lines = [
            f"[해석 {self.name}] {self.column} {stats.total}행"
            f" · 읽음 {stats.ok} · 못 읽음 {failed}"
        ]
        for reason, patterns in sorted(stats.failures.items()):
            lines.append(f"  {reason} {sum(patterns.values())}: {_top(patterns)}")
        for slot in self.slots:
            values = stats.values.get(slot.name, Counter())
            head = f"  조각 {slot.name}: 있음 {stats.present[slot.name]}"
            if slot.optional:
                head += f" · 없음 {stats.ok - stats.present[slot.name]}"
            head += f" · 값 {len(values)}종"
            if slot.name in stats.kinds:
                head += f" — {_top(stats.kinds[slot.name])}"
            lines.append(head)
            below = self.rare_below if slot.rare_below is None else slot.rare_below
            if below:
                rare = Counter(
                    {value: count for value, count in values.items() if count < below}
                )
                if rare:
                    shapes: Counter[str] = Counter()
                    for value, count in rare.items():
                        shapes[mask(value)] += count
                    lines.append(
                        f"    {below}행 미만인 값 {len(rare)}종"
                        f"({sum(rare.values())}행): {_top(shapes)}"
                    )
        return lines


def _compiled(pattern: Any, where: str) -> re.Pattern[str]:
    try:
        return re.compile(str(pattern))
    except re.error as failure:
        raise Stop(
            f"대응 파일: {where} 의 정규식 {pattern!r} 가 틀렸습니다 ({failure})"
        ) from None


def _regex(pattern: Any, where: str) -> re.Pattern[str] | None:
    return None if pattern in (None, "") else _compiled(pattern, where)


# --------------------------------------------------------------------------
# 대응 — 칸 하나를 행에서 어떻게 얻나
# --------------------------------------------------------------------------


def _column(spec: dict[str, Any], header: list[str], *, where: str) -> str:
    name = str(spec.get("column") or "").strip()
    if not name:
        raise Stop(f"대응 파일: {where} 에 column 이 없습니다")
    if name not in header:
        raise Stop(
            f"대응 파일: {where} 의 열 {name!r} 가 원천에 없습니다"
            f" — 원천의 열: {', '.join(header)}"
        )
    return name


@dataclass
class FieldStats:
    problems: dict[str, Counter[str]] = field(default_factory=dict)


class KeyMatch:
    """원천이 적은 코드를 플랫폼(코어)의 식별자와 맞춘다 — 그대로, 대소문자만 다른 것, 그리고
    허락하면 **앞부분이 하나뿐인** 것(`SM-A1` → `SM-A1_KOR_SKT`). 둘 이상이면 고르지 않는다."""

    def __init__(self, keys: set[str]) -> None:
        self.exact = set(keys)
        self.upper: dict[str, set[str]] = defaultdict(set)
        self.fronts: dict[str, set[str]] = defaultdict(set)
        self.resolved: dict[str, str | None] = {}
        """플랫폼이 풀어 준 것 — 글자 → 식별자(못 풀었으면 None).

        ⚠️ 식별자만 보면 「앤시스」 라고 적힌 원천이 통째로 안 맞는다. 별칭과 이름으로도
           찾아야 하는데, **그 판정은 플랫폼의 것**이다(식별자 → 별칭 → 이름 차례와 겹침
           규칙). 도구가 따로 맞추면 「도구는 된다는데 넣으면 안 되는」 상태가 생긴다."""

        for key in keys:
            self.upper[key.upper()].add(key)
            for index, char in enumerate(key):
                if index and char in BOUNDARIES:
                    self.fronts[key[:index].upper()].add(key)

    def find(self, text: str, *, prefix: bool) -> tuple[str | None, str]:
        if text in self.exact:
            return text, "그대로"
        found = self.upper.get(text.upper(), set())
        if len(found) == 1:
            return next(iter(found)), "대소문자만 다름"
        if len(found) > 1:
            return None, "여러 개"
        if prefix:
            found = self.fronts.get(text.upper(), set())
            if len(found) == 1:
                return next(iter(found)), "앞부분이 하나"
            if len(found) > 1:
                return None, "여러 개"
        picked = self.resolved.get(text)
        if picked:
            return picked, "별칭·이름으로"
        return None, "없음"


@dataclass
class MatchStats:
    target: str
    outcomes: Counter[str] = field(default_factory=Counter)
    misses: dict[str, Counter[str]] = field(default_factory=dict)
    """못 맞춘 까닭(여러 개 · 없음) → 가린 패턴."""


class Mapping:
    def __init__(
        self,
        spec: dict[str, Any],
        table: Table,
        keys: Callable[[str], set[str]] | None = None,
    ) -> None:
        if spec.get("format") != FORMAT:
            raise Stop(f"대응 파일: format 이 {FORMAT} 가 아닙니다 ({spec.get('format')!r})")
        self.spec = spec
        self.table = table
        dictionaries = {
            str(name): {str(value) for value in values}
            for name, values in (spec.get("dictionaries") or {}).items()
        }
        self.parsers = {
            str(name): Parser(str(name), one, dictionaries, table.header)
            for name, one in (spec.get("parsers") or {}).items()
        }
        self.keys = keys
        self.matchers: dict[str, KeyMatch] = {}
        self.match_stats: dict[tuple[str, str], MatchStats] = {}
        self.match_unresolved: dict[tuple[str, str, str], list[int]] = {}
        self.types: list[dict[str, Any]] = list(spec.get("types") or [])
        self.relations: list[dict[str, Any]] = list(spec.get("relations") or [])
        if not self.types and not self.relations:
            # **선만 담은 대응 파일도 있다** — 객체는 이미 들어가 있고 이 표는 그것들을 잇기만
            # 할 때. 그때 끝점은 `column`(식별자 · 이름)으로 적는다(`key_of` 는 타입이 있어야
            # 한다).
            raise Stop("대응 파일: types 나 relations 중 하나는 있어야 합니다")
        self.by_slug: dict[str, dict[str, Any]] = {}
        for index, one in enumerate(self.types, start=1):
            slug = str(one.get("type_slug") or "")
            if not slug or "key" not in one:
                raise Stop(f"대응 파일: types[{index}] 에 type_slug · key 가 있어야 합니다")
            if slug in self.by_slug:
                raise Stop(f"대응 파일: 타입 {slug} 이(가) 두 번 나옵니다")
            self.by_slug[slug] = one
        for one in self.types:
            slug = one["type_slug"]
            specs = {"key": one["key"], "label": one.get("label") or one["key"]}
            specs.update(one.get("fields") or {})
            for name, value in specs.items():
                if name in ("key", "label") and isinstance(value, dict) and "match" in value:
                    raise Stop(
                        f"대응 파일: {slug}.{name} 에는 match 를 쓰지 않습니다"
                        " — 참조 칸에 쓴다"
                    )
                self._check(value, where=f"{slug}.{name}")
        for index, one in enumerate(self.relations, start=1):
            where = f"relations[{index}]"
            for name in ("relation", "type_slug", "src", "dst"):
                if name not in one:
                    raise Stop(f"대응 파일: {where} 에 {name} 이(가) 있어야 합니다")
            if self.types and str(one["type_slug"]) not in self.by_slug:
                raise Stop(
                    f"대응 파일: {where} 의 type_slug {one['type_slug']!r} 가 "
                    "types 에 없습니다"
                )
            slug = str(one["relation"])
            for name in ("src", "dst"):
                self._check(one[name], where=f"{where}.{name}")
            if "evidence_note" in one:
                self._check(one["evidence_note"], where=f"{where}.evidence_note")
            for key, value in (one.get("properties") or {}).items():
                self._check(value, where=f"{where}.properties.{key}")
        self.stats: dict[tuple[str, str], FieldStats] = {}

    def _check(self, spec: Any, *, where: str) -> None:
        if not isinstance(spec, dict):
            raise Stop(f"대응 파일: {where} 는 {{...}} 여야 합니다")
        for mode in AGGREGATES:
            if mode in spec:
                if len(spec) > (2 if mode == "join" else 1) or (
                    mode == "join" and set(spec) - {"join", "separator"}
                ):
                    raise Stop(
                        f"대응 파일: {where} 의 {mode} 안에 값 뽑기를 적습니다 — "
                        f'{{"{mode}": {{"column": "열"}}}}'
                    )
                self._check(spec[mode], where=f"{where}.{mode}")
                return
        if "column" in spec:
            _column(spec, self.table.header, where=where)
            if "match" in spec:
                self._check_match(spec["match"], where=where)
        elif "key_of" in spec:
            if spec["key_of"] not in self.by_slug:
                raise Stop(
                    f"대응 파일: {where} 의 key_of {spec['key_of']!r} 가 types 에 없습니다"
                )
        elif "parser" in spec:
            parser = self.parsers.get(str(spec["parser"]))
            if parser is None:
                raise Stop(
                    f"대응 파일: {where} 의 해석기 {spec['parser']!r} 가 parsers 에 없습니다"
                )
            if spec.get("slot") not in parser.slot_names:
                raise Stop(
                    f"대응 파일: {where} 의 조각 {spec.get('slot')!r} 가 해석기에 없습니다"
                )
        elif "value" not in spec:
            raise Stop(
                f"대응 파일: {where} 는 column · key_of · parser · value 중"
                " 하나가 있어야 합니다"
            )

    def _check_match(self, rule: Any, *, where: str) -> None:
        if not isinstance(rule, dict) or not str(rule.get("keys") or "").strip():
            raise Stop(
                f"대응 파일: {where} 의 match 에는 keys(타입 slug 또는 @파일)가 있어야 합니다"
            )
        if rule.get("on_missing", "blank") not in ON_MISSING:
            raise Stop(f"대응 파일: {where} 의 on_missing 은 {' · '.join(ON_MISSING)} 중 하나")
        if self.keys is None:
            raise Stop(
                f"대응 파일: {where} 가 참조 대조(match)를 씁니다 — "
                "SP_SERVER · SP_TOKEN 을 두거나 keys 를 @파일로 적으세요"
            )
        target = str(rule["keys"]).strip()
        if target not in self.matchers:
            self.matchers[target] = KeyMatch(self.keys(target))

    def _match(
        self, rule: dict[str, Any], text: str, number: int, *, at: tuple[str, str]
    ) -> Any:
        target = str(rule["keys"]).strip()
        key, outcome = self.matchers[target].find(text, prefix=bool(rule.get("prefix")))
        stats = self.match_stats.setdefault(at, MatchStats(target))
        stats.outcomes[outcome] += 1
        if key is not None:
            return key
        stats.misses.setdefault(outcome, Counter())[mask(text)] += 1
        on_missing = rule.get("on_missing", "blank")
        if on_missing == "keep":
            return text
        if on_missing == "unresolved":
            self.match_unresolved.setdefault((at[0], at[1], text), []).append(number)
        # **못 맞춘 참조는 비워 넣는다** — 그대로 보내면 묶음 전체가 막히고, 짐작하면 엉뚱한
        # 것에 붙는다. 보고서가 몇 행 · 어떤 모양인지 말한다.
        return None

    def value(
        self, spec: dict[str, Any], number: int, row: dict[str, str], *, at: tuple[str, str]
    ) -> Any:
        for mode in AGGREGATES:
            if mode in spec:
                # 한 행의 값만 읽는다 — 행들을 하나로 만드는 것은 `gather` 의 몫이다.
                return self.value(spec[mode], number, row, at=at)
        if "value" in spec:
            return spec["value"]
        if "key_of" in spec:
            target = self.by_slug[spec["key_of"]]
            return self.value(target["key"], number, row, at=(target["type_slug"], "key"))
        if "parser" in spec:
            parsed = self.parsers[str(spec["parser"])].parse(number, row)
            got = parsed.slots.get(str(spec["slot"]))
            if got is None:
                return None
            return got[1] if spec.get("part") == "kind" else got[0]

        raw = row[str(spec["column"])]
        text = raw.strip()
        if spec.get("upper"):
            text = text.upper()
        blanks = {str(one) for one in spec.get("blank") or []}
        if not text or text in blanks:
            mapping = spec.get("map") or {}
            return mapping.get(BLANK) if BLANK in mapping else None
        if "map" in spec:
            mapping = spec["map"]
            if text in mapping:
                return mapping[text]
            if DATE in mapping and _as_date(text) is not None:
                return mapping[DATE]
            self._problem(at, "대응 없는 값", text)
            return None
        if spec.get("date"):
            iso = _as_date(text)
            if iso is None:
                self._problem(at, "날짜가 아닌 값", text)
            return iso
        if "match" in spec:
            return self._match(spec["match"], text, number, at=at)
        return text

    def _problem(self, at: tuple[str, str], what: str, text: str) -> None:
        stats = self.stats.setdefault(at, FieldStats())
        stats.problems.setdefault(what, Counter())[mask(text)] += 1


def _as_date(text: str) -> str | None:
    found = DATE_RE.match(text)
    if not found:
        return None
    try:
        return date(*(int(part) for part in found.groups())).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# 모으기 — 같은 식별자는 한 객체로, 값이 갈리면 미해결로
# --------------------------------------------------------------------------


@dataclass
class Gathered:
    key: str
    first_row: int
    rows: int = 0
    values: dict[str, dict[str, tuple[Any, list[int]]]] = field(default_factory=dict)
    """칸 → (값의 지문 → (값, 그 값이 나온 행들))."""


@dataclass
class TypeResult:
    slug: str
    workspace_slug: str | None
    objects: list[dict[str, Any]]
    blank_keys: int
    conflicts: Counter[str]
    unresolved: list[dict[str, Any]]


def _aggregate_of(spec: Any) -> tuple[str, str] | None:
    """모으는 칸인가 — `(모드, 잇는 글자)`. 아니면 None."""
    if not isinstance(spec, dict):
        return None
    for mode in AGGREGATES:
        if mode in spec:
            return mode, str(spec.get("separator") or "; ")
    return None


def gather(mapping: Mapping, one: dict[str, Any]) -> TypeResult:
    slug = str(one["type_slug"])
    specs: dict[str, Any] = {"label": one.get("label") or one["key"]}
    specs.update(one.get("fields") or {})
    aggregates = {
        name: found for name, spec in specs.items() if (found := _aggregate_of(spec))
    }
    found: dict[str, Gathered] = {}
    blank_keys = 0
    for number, row in mapping.table.rows:
        key = mapping.value(one["key"], number, row, at=(slug, "key"))
        if key in (None, ""):
            blank_keys += 1
            continue
        key = str(key)
        entry = found.get(key)
        if entry is None:
            entry = found[key] = Gathered(key=key, first_row=number)
        entry.rows += 1
        for name, spec in specs.items():
            value = mapping.value(spec, number, row, at=(slug, name))
            fingerprint = json.dumps(value, ensure_ascii=False, sort_keys=True)
            seen = entry.values.setdefault(name, {})
            if fingerprint in seen:
                seen[fingerprint][1].append(number)
            else:
                seen[fingerprint] = (value, [number])

    objects: list[dict[str, Any]] = []
    conflicts: Counter[str] = Counter()
    unresolved: list[dict[str, Any]] = []
    for entry in found.values():
        out: dict[str, Any] = {"key": entry.key}
        for name, seen in entry.values.items():
            if name in aggregates:
                # **갈리는 것이 답인 칸이다** — 별칭 여럿, 담당 여럿. 순서는 나온 차례,
                # 같은 값은 한 번만.
                mode, separator = aggregates[name]
                values = [value for value, _ in seen.values() if value not in (None, "")]
                if not values:
                    out[name] = None
                elif mode == "collect":
                    out[name] = values
                else:
                    out[name] = separator.join(str(value) for value in values)
                continue
            if len(seen) == 1:
                out[name] = next(iter(seen.values()))[0]
                continue
            # **짐작하지 않는다.** 칸을 비워 두면(안 보냄) 플랫폼 값이 그대로 남고, 사람이
            # 답하기 전에는 미해결이 넣기를 막는다.
            conflicts[name] += 1
            unresolved.append(
                {
                    "what": f"{slug} {entry.key!r} 의 칸 {name}",
                    "question": "같은 식별자인데 행마다 값이 다릅니다 — 어느 값이 맞나요?"
                    " (늘 갈린다면 이 칸은 이 타입이 아니라 행 쪽 타입의 칸입니다)",
                    "options": [value for value, _ in seen.values()],
                    "_source": {
                        "file": mapping.table.name,
                        "rows": [rows[:10] for _, rows in seen.values()],
                    },
                }
            )
        out["_source"] = {"file": mapping.table.name, "row": entry.first_row}
        if entry.rows > 1:
            out["_note"] = f"{entry.rows}행에서 모음"
        objects.append(out)
    return TypeResult(
        slug=slug,
        workspace_slug=one.get("workspace_slug") or mapping.spec.get("workspace_slug"),
        objects=objects,
        blank_keys=blank_keys,
        conflicts=conflicts,
        unresolved=unresolved,
    )


# --------------------------------------------------------------------------
# 명령
# --------------------------------------------------------------------------


@dataclass
class EdgeResult:
    """관계 종류 하나의 결과 — 출발 타입마다 한 파일로 나간다."""

    relation: str
    type_slug: str
    rows: list[dict[str, Any]]
    blank_ends: int
    """출발점이나 도착점이 빈 행 — 그 행은 선을 만들지 않는다."""
    conflicts: int
    unresolved: list[dict[str, Any]]


def gather_edges(mapping: Mapping, one: dict[str, Any]) -> EdgeResult:
    """표 한 장에서 선을 모은다 — **같은 세 끝은 한 줄로.**

    한 행에 여러 타입이 섞인 표에서는 같은 선이 수십 행에 되풀이된다(모델이 여러 줄이면
    그 과제로 가는 선도 여러 줄). 세 끝(출발 · 종류 · 도착)이 같으면 한 줄로 모으고,
    **붙는 값이 행마다 다르면 짐작하지 않고** 미해결에 올린다 — 객체 칸과 같은 규칙이다.
    """
    slug = str(one["relation"])
    type_slug = str(one["type_slug"])
    at = (f"관계 {slug}", "")
    note_spec = one.get("evidence_note")
    props: dict[str, Any] = one.get("properties") or {}
    found: dict[tuple[str, str], dict[str, dict[str, tuple[Any, list[int]]]]] = {}
    order: list[tuple[str, str]] = []
    blank_ends = 0
    for number, row in mapping.table.rows:
        src = mapping.value(one["src"], number, row, at=(f"관계 {slug}", "src"))
        dst = mapping.value(one["dst"], number, row, at=(f"관계 {slug}", "dst"))
        if src in (None, "") or dst in (None, ""):
            blank_ends += 1
            continue
        ends = (str(src), str(dst))
        values = found.get(ends)
        if values is None:
            values = found[ends] = {}
            order.append(ends)
        specs = dict(props)
        if note_spec is not None:
            specs["evidence_note"] = note_spec
        for name, spec in specs.items():
            value = mapping.value(spec, number, row, at=(f"관계 {slug}", name))
            fingerprint = json.dumps(value, ensure_ascii=False, sort_keys=True)
            seen = values.setdefault(name, {})
            if fingerprint in seen:
                seen[fingerprint][1].append(number)
            else:
                seen[fingerprint] = (value, [number])

    rows: list[dict[str, Any]] = []
    conflicts = 0
    unresolved: list[dict[str, Any]] = []
    for ends in order:
        src, dst = ends
        out: dict[str, Any] = {"src": src, "relation": slug, "dst": dst}
        properties: dict[str, Any] = {}
        for name, seen in found[ends].items():
            if len(seen) > 1:
                conflicts += 1
                unresolved.append(
                    {
                        "what": f"관계 {src} -{slug}-> {dst} 의 칸 {name}",
                        "question": "같은 선인데 행마다 값이 다릅니다 — 어느 값이 맞나요?",
                        "options": [
                            {"value": value, "rows": numbers[:10]}
                            for value, numbers in seen.values()
                        ],
                        "_source": {
                            "file": mapping.table.name,
                            "rows": sorted(
                                number for _, numbers in seen.values() for number in numbers
                            )[:20],
                        },
                    }
                )
                continue
            value = next(iter(seen.values()))[0]
            if value in (None, ""):
                continue
            if name == "evidence_note":
                out["evidence_note"] = str(value)
            else:
                properties[name] = value
        if properties:
            out["properties"] = properties
        rows.append(out)
    assert at
    return EdgeResult(
        relation=slug,
        type_slug=type_slug,
        rows=rows,
        blank_ends=blank_ends,
        conflicts=conflicts,
        unresolved=unresolved,
    )


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except OSError as failure:
        raise Stop(f"대응 파일을 읽을 수 없습니다: {path} ({failure})") from failure
    except ValueError as failure:
        raise Stop(f"대응 파일이 JSON 이 아닙니다: {path} ({failure})") from failure
    if not isinstance(body, dict):
        raise Stop(f"대응 파일은 {{...}} 여야 합니다: {path}")
    return body


def _mapping_notes(mapping: Mapping, mapping_path: Path, digest: str) -> str:
    lines = [f"sp_table 로 옮김 — 대응 {mapping_path.name}(sha256 {digest[:12]})"]
    for one in mapping.types:
        specs: dict[str, Any] = {"key": one["key"], "label": one.get("label") or one["key"]}
        specs.update(one.get("fields") or {})
        parts = []
        for name, spec in specs.items():
            if "column" in spec:
                source = spec["column"]
            elif "key_of" in spec:
                source = f"{spec['key_of']} 의 식별자"
            elif "parser" in spec:
                part = " 종류" if spec.get("part") == "kind" else ""
                source = f"{spec['parser']}.{spec['slot']}{part}"
            else:
                source = "고정값"
            parts.append(f"{name} ← {source}")
        lines.append(f"{one['type_slug']}: " + " · ".join(parts))
    return "\n".join(lines)


def convert(
    mapping_path: Path, source_path: Path, run: Path, *, server: str = "", token: str = ""
) -> tuple[bool, str]:
    """실행 폴더를 만든다. (미해결이 없나, 보고서). 참조 대조(`match`)가 있으면 식별자를
    플랫폼(`server` · `token`)이나 `@파일`에서 받는다."""
    spec = _load_mapping(mapping_path)
    source = spec.get("source") or {}
    table = read_table(
        source_path,
        encoding=str(source.get("encoding") or "auto"),
        delimiter=str(source.get("delimiter") or ","),
    )
    if not table.rows:
        raise Stop(f"{table.name}: 값이 있는 행이 없습니다")
    cache: dict[str, set[str]] = {}

    def keys(target: str) -> set[str]:
        if target not in cache:
            if target.startswith("@"):
                cache[target] = pipeline.read_keys_file(
                    (mapping_path.parent / target[1:]).resolve()
                )
            elif not server or not token:
                raise Stop(
                    f"참조 대조({target})에 플랫폼 식별자가 필요합니다 — SP_SERVER · SP_TOKEN"
                )
            else:
                cache[target] = pipeline.fetch_keys(server, token, target)
        return cache[target]

    mapping = Mapping(spec, table, keys=keys)
    _ask_platform(mapping, server, token)
    ontology: dict[str, Any] | None = None
    if spec.get("ontology"):
        ontology_path = (mapping_path.parent / str(spec["ontology"])).resolve()
        loaded = _load_mapping(ontology_path)
        ontology = loaded

    results = [gather(mapping, one) for one in mapping.types]
    edges = [gather_edges(mapping, one) for one in mapping.relations]

    pipeline.cmd_init(run, title=str(spec.get("title") or table.name))
    manifest_path = run / pipeline.MANIFEST
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mapping_digest = hashlib.sha256(mapping_path.read_bytes()).hexdigest()
    manifest["sources"] = [
        {
            "name": table.name,
            "sha256": table.sha256,
            "rows": len(table.rows),
            "encoding": table.encoding,
        }
    ]
    manifest["objects_order"] = [result.slug for result in results]
    if edges:
        manifest["relations_order"] = [one.relation for one in edges]
    manifest["notes"] = _mapping_notes(mapping, mapping_path, mapping_digest)
    pipeline._write(manifest_path, manifest)
    if ontology is not None:
        pipeline._write(run / pipeline.ONTOLOGY, ontology)

    unresolved: list[dict[str, Any]] = []
    for result in results:
        chunks = [
            result.objects[at : at + pipeline.MAX_ROWS]
            for at in range(0, len(result.objects), pipeline.MAX_ROWS)
        ]
        for index, chunk in enumerate(chunks, start=1):
            name = result.slug if len(chunks) == 1 else f"{result.slug}-{index:03d}"
            pipeline._write(
                run / pipeline.OBJECTS_DIR / f"{name}.json",
                {
                    "type_slug": result.slug,
                    "workspace_slug": result.workspace_slug,
                    "rows": chunk,
                },
            )
        unresolved.extend(result.unresolved)
    for result_edges in edges:
        chunks = [
            result_edges.rows[at : at + pipeline.MAX_ROWS]
            for at in range(0, len(result_edges.rows), pipeline.MAX_ROWS)
        ]
        for index, chunk in enumerate(chunks, start=1):
            name = (
                result_edges.relation
                if len(chunks) == 1
                else f"{result_edges.relation}-{index:03d}"
            )
            pipeline._write(
                run / pipeline.RELATIONS_DIR / f"{name}.json",
                {"type_slug": result_edges.type_slug, "rows": chunk},
            )
        unresolved.extend(result_edges.unresolved)
    for (slug, name, text), numbers in mapping.match_unresolved.items():
        unresolved.append(
            {
                "what": f"{slug} 의 칸 {name} 값 {text!r}",
                "question": "코어에 없는(또는 여럿과 맞는) 식별자입니다 — "
                "어느 것을 가리키나요? 맞는 것이 없으면 비워 넣습니다",
                "options": [],
                "_source": {"file": table.name, "rows": numbers[:20]},
            }
        )
    pipeline._write(run / pipeline.UNRESOLVED, unresolved)

    text = _report(mapping, mapping_path, results, run, len(unresolved), edges)
    (run / REPORT).write_text(text + "\n", encoding="utf-8")
    return not unresolved, text


def _ask_platform(mapping: Mapping, server: str, token: str) -> None:
    """식별자로 못 맞춘 값을 **플랫폼에 한 번에 물어** 별칭 · 이름으로도 푼다.

    ⚠️ 판정은 플랫폼의 것을 쓴다(`resolve-many`) — 식별자 → 별칭 → 이름 차례와 겹침 규칙이
       거기 있고, 도구가 따로 맞추면 「도구는 된다는데 넣으면 안 되는」 상태가 생긴다.
       주소 · 토큰이 없으면(그리고 `@파일` 로 맞출 때는) 예전처럼 식별자만 본다.
    """
    if not server or not token or not mapping.matchers:
        return
    for spec in _match_columns(mapping) + _match_ends(mapping):
        target = str(spec["keys"]).strip()
        matcher = mapping.matchers.get(target)
        if matcher is None or target.startswith("@"):
            continue
        left: list[str] = []
        for _number, row in mapping.table.rows:
            text = str(row.get(str(spec["column"])) or "").strip()
            if spec.get("upper"):
                text = text.upper()
            if not text or text in matcher.exact or text in matcher.resolved:
                continue
            found, _outcome = matcher.find(text, prefix=bool(spec.get("prefix")))
            if found is None:
                left.append(text)
        if not left:
            continue
        answered = pipeline.resolve_names(server, token, target, sorted(set(left)))
        if answered is None:
            continue
        for text, verdict in answered.items():
            picked = (verdict or {}).get("object") if verdict.get("match") == "exact" else None
            matcher.resolved[text] = (
                str(picked["key"]) if picked and picked.get("key") else None
            )


def _match_ends(mapping: Mapping) -> list[dict[str, Any]]:
    """**관계 끝점**의 참조 대조 — `relations[].src` · `dst` 에 적은 것.

    ⚠️ 예전에는 별칭 · 이름 풀이가 객체 칸에만 걸렸다. 그래서 끝점에 그룹 표기(「앤시스」)가
       적혀 있으면 「없음」 으로 비워졌고, **선이 조용히 빠졌다** — 객체는 들어가 있어서
       사람은 다 들어간 줄 안다.
    """
    out: list[dict[str, Any]] = []
    for one in mapping.relations:
        for name in ("src", "dst"):
            spec = one.get(name)
            if isinstance(spec, dict) and "match" in spec and "column" in spec:
                out.append(
                    {**spec["match"], "column": spec["column"], "upper": spec.get("upper")}
                )
    return out


def _match_columns(mapping: Mapping) -> list[dict[str, Any]]:
    """참조 대조(`match`)를 쓰는 칸들 — 대조 규칙에 그 칸(`column` · `upper`)을 붙여서."""
    out: list[dict[str, Any]] = []
    for one in mapping.types:
        for spec in (one.get("fields") or {}).values():
            inner = spec
            for mode in AGGREGATES:
                if isinstance(inner, dict) and mode in inner:
                    inner = inner[mode]
            if isinstance(inner, dict) and "match" in inner:
                out.append(
                    {**inner["match"], "column": inner["column"], "upper": inner.get("upper")}
                )
    return out


def brief(report: str) -> str:
    """보고서를 **말로 옮길 수 있는 몇 줄**로 — 파일을 들고 나올 수 없는 자리에서 사람이 읽어
    전하거나 받아 적는다. 수만 있고 값은 없다(가린 패턴도 상위 2개까지)."""
    lines: list[str] = []
    for line in report.splitlines():
        if line.startswith("[") and "]" in line:
            head = line.split("]", 1)[0].lstrip("[")
            body = line.split("]", 1)[1].strip()
            lines.append(f"{head}: {body}")
        elif line.startswith("  ") and (
            any(word in line for word in ("못 나눔", "검증:", "두 갈래"))
            or line.strip().startswith(("여러 개 ", "없음 "))
        ):
            what, _, rest = line.strip().partition(":")
            top = " · ".join(rest.strip().split(" · ")[:2])
            lines.append(f"  {what}: {top}")
        elif line.startswith("  칸 ") or line.strip().startswith("미해결"):
            lines.append("  " + line.strip())
    return "\n".join(lines)


def _report(
    mapping: Mapping,
    mapping_path: Path,
    results: list[TypeResult],
    run: Path,
    unresolved: int,
    edges: list[EdgeResult] | None = None,
) -> str:
    table = mapping.table
    lines = [
        f"원천: {len(table.rows)}행(빈 행 {table.blank_rows})"
        f" · {table.encoding} · 열 {len(table.header)}",
        f"대응: {mapping_path.name}",
        "",
    ]
    for result in results:
        head = (
            f"[{result.slug}] 객체 {len(result.objects)} · 식별자가 빈 행 {result.blank_keys}"
            f" · 값이 갈린 칸 {sum(result.conflicts.values())}"
        )
        if result.conflicts:
            head += (
                " (" + " · ".join(f"{k} {v}" for k, v in result.conflicts.most_common()) + ")"
            )
        lines.append(head)
        for (slug, name), stats in sorted(mapping.stats.items()):
            if slug != result.slug:
                continue
            for what, patterns in sorted(stats.problems.items()):
                lines.append(
                    f"  칸 {name} — {what} {sum(patterns.values())}: {_top(patterns)}"
                )
    if mapping.match_stats:
        lines.append("")
    for one in edges or []:
        lines.append(
            f"[관계 {one.relation}] 선 {len(one.rows)} · 끝이 빈 행 {one.blank_ends}"
            f" · 값이 갈린 선 {one.conflicts} → {one.type_slug}"
        )
        for (where, field_name), field_stats in sorted(mapping.stats.items()):
            if where != f"관계 {one.relation}":
                continue
            for what, patterns in sorted(field_stats.problems.items()):
                lines.append(
                    f"  칸 {field_name} — {what} {sum(patterns.values())}: {_top(patterns)}"
                )
    if edges:
        lines.append("")
    for (slug, name), stats in sorted(mapping.match_stats.items()):
        total = sum(stats.outcomes.values())
        parts = " · ".join(
            f"{label} {stats.outcomes[label]}"
            for label in MATCH_OUTCOMES
            if stats.outcomes[label]
        )
        lines.append(f"[참조 대조] {slug}.{name} → {stats.target} {total}행 — {parts}")
        for outcome, patterns in stats.misses.items():
            lines.append(f"  {outcome} {sum(patterns.values())}: {_top(patterns)}")
    for parser in mapping.parsers.values():
        lines.append("")
        lines.extend(parser.report())
    lines.append("")
    if unresolved:
        lines.append(f"미해결 {unresolved}건 → {pipeline.UNRESOLVED} — 답을 정한 뒤 비우세요")
    lines.append(f"다음: python sp_pipeline.py validate {run}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sp_table", description=__doc__.split("\n")[0])
    parser.add_argument("mapping", type=Path, help="대응 파일(JSON)")
    parser.add_argument("source", type=Path, help="원천 표(CSV)")
    parser.add_argument("run", type=Path, help="만들 실행 폴더 — 없는 폴더여야 한다")
    args = parser.parse_args(argv)
    try:
        ok, text = convert(
            args.mapping,
            args.source,
            args.run,
            server=os.environ.get("SP_SERVER", ""),
            token=os.environ.get("SP_TOKEN", ""),
        )
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        return 2
    print(text)
    print("\n== 말로 전할 요약 ==\n" + brief(text))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
