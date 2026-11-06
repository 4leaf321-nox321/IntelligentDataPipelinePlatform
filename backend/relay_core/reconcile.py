"""대조 — 대상 SP 에 **지금 실제로 있는 것**을 지난번 적용된 출력(기준)과 견준다(개발계획 4c).

relay 는 평소 자기 출력끼리만 견준다(`diff`) — 원천이 안 바뀐 줄은 다시 안 보낸다. 그래서
허브에서 누가 객체를 지우거나 사용 중지하거나, 허브를 백업에서 되돌리면 relay 는 모른 채
어긋나서 돈다. 대조가 그것을 찾는다(타입마다):

    빠짐       기준엔 있는데 대상에 없다
    안 씀      기준엔 살아 있는데 대상에선 사용 중지다
    다름       칸 값이 기준과 다르다(빈 칸은 「안 보냄」 이라 견주지 않는다)
    대상에만   대상엔 살아 있는데 기준엔 없다

**읽기만 한다.** 대상은 세 길 중 하나로 읽는다(`best_reader`) — 견주는 타입이 다 **코어로
열렸으면 코어 API**(커서로 2,000개씩 — 27만 객체 36초), 아니면 **묶음 내보내기**(작업 하나,
시스템 관리자 토큰), 둘 다 안 되면 **목록 API**(200개씩 처음부터 세어 넘겨 대상이 클수록
가파르게 느려진다 — 27만 객체 236초, 2026-11-06 실측). 느린 길로 읽었으면 보고서에 까닭을
적는다. 고치는 일은
**보정 계획**(`resync`)이다 — 어긋난 줄을 기준 그대로 다시 보내는 미리보기라, 평소 계획처럼
대상의 검토함에 뜨고 적용은 사람이 한다. 다시 보내는 객체에 닿는 기준의 선도 함께 싣는다(객체가
지워졌으면 선도 사라졌다). **대상에만 있는 것은 그 타입을 이 원천이 잠갔을 때만**
(`managed_by == source`) 무덤으로 싣는다 — 안 잠긴 타입이면 사람이 만든 것일 수 있다.

선은 아직 따로 견주지 않는다 — 대상 API 가 관계 종류마다의 선 목록을 주지 않는다.

참조 칸은 코어 · 내보내기가 상대의 **식별자**로 준다 — 그러면 대상 객체를 타입마다 흘려 견주고
다 쥐지 않는다. 목록 API 는 상대의 id 로 준다 — 같은 대조에서 받은 객체면 그 식별자로, 아니면
상대의 이름(`ref_labels`)으로 견주고, 둘 다 아니면 **확인 못 함**으로 센다(어긋남으로 세지
않는다).
"""

from __future__ import annotations

import json
import math
import urllib.parse
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from relay_core import sp
from relay_core.definition import Pipeline, Target
from relay_core.diff import write_batches
from relay_core.runner import Outcome, fold, pending_of, propose, settle_pending
from relay_core.state import Workdir, stamp
from relay_core.submit import SubmitError, Submitter

#: 대상 목록 한 쪽 — SP 의 목록 상한(`MAX_LIMIT`)과 같다.
PAGE = 200
#: 화면 · 이력에 싣는 본보기 — 타입 · 갈래마다 이만큼(파일에는 전부 남는다).
SAMPLE = 20
#: 마지막 보고서 — 작업 폴더의 이 파일.
REPORT = "reconcile.json"

#: 견주는 고정 칸. 나머지 고정 칸(id · aliases · status · renamed_from · previous_keys)은
#: relay 가 보내지 않거나 사람이 붙이는 것이다.
_FIXED = ("label", "description", "valid_from_year", "valid_to_year")
_NOT_COMPARED = {"key", "id", "aliases", "status", "renamed_from", "previous_keys"}
#: SP 묶음의 여러 값 구분자(`bulk.MULTI_SEP`).
_MULTI = ";"


class ReconcileError(RuntimeError):
    pass


class Reader(Protocol):
    """대상 읽기. 더 알 수 있으면 함께 준다(없으면 목록 API 처럼 다룬다): `via`(읽은 길) ·
    `fallback`(느린 길로 읽은 까닭) · `notice`(그 밖에 보고서에 알릴 것) · `keyed`(참조
    칸이 상대의 식별자로 온다 — 그러면 객체를 흘려 견주고 다 쥐지 않는다) · `blind`(그 길이
    주지 않는 고정 칸 — 확인 못 함으로 센다)."""

    def objects(self, type_slug: str) -> Iterable[dict[str, Any]] | None:
        """그 타입의 객체 전부(사용 중지 포함) — 대상에 타입이 없으면 None."""

    def managed_by(self) -> dict[str, str | None]:
        """타입마다 잠근 원천(`ObjectType.managed_by`)."""


def _short(failure: object) -> str:
    """SP 의 오류 글은 SQL 까지 싣는다 — 보고서에는 첫 줄 앞부분만."""
    text = str(failure).strip().splitlines()[0] if str(failure).strip() else ""
    return text if len(text) <= 200 else text[:200] + "…"


class TargetReader:
    """대상 SP 를 읽는다 — 목록 API 를 200개씩 쪽마다. **어디서나 되지만** 처음부터 세어 넘겨
    대상이 클수록 가파르게 느려진다 — 다른 길(코어 · 내보내기)이 안 될 때 쓴다."""

    via = "목록"
    keyed = False
    blind: frozenset[str] = frozenset()

    def __init__(self, target: Target) -> None:
        self.target = target
        self.fallback = ""

    def _get(self, path: str) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {self.target.token}", "X-Client": "datarelay"}
        url = f"{self.target.server.rstrip('/')}{path}"
        try:
            status, body = sp.pipeline._send(
                self.target.server, "GET", url, headers, None, who="대상 SP"
            )
        except sp.pipeline.Stop as stop:
            raise ReconcileError(str(stop)) from None
        return int(status), body

    def objects(self, type_slug: str) -> list[dict[str, Any]] | None:
        out: list[dict[str, Any]] = []
        quoted = urllib.parse.quote(type_slug)
        while True:
            status, body = self._get(f"/api/objects/{quoted}?limit={PAGE}&offset={len(out)}")
            if status == 404:
                return None
            if status != 200 or not isinstance(body, dict):
                raise ReconcileError(f"대상의 {type_slug} 목록을 못 읽었습니다({status}).")
            items = [one for one in body.get("items") or [] if isinstance(one, dict)]
            out.extend(items)
            if not items or len(out) >= int(body.get("total") or 0):
                return out

    def schema(self) -> dict[str, dict[str, Any]]:
        """타입마다 정의(`/api/ontology/schema` 의 types) — 못 읽으면 빈 것."""
        status, body = self._get("/api/ontology/schema")
        if status != 200 or not isinstance(body, dict):
            return {}
        return {
            str(one.get("slug")): one
            for one in body.get("types") or []
            if isinstance(one, dict) and one.get("slug")
        }

    def managed_by(self) -> dict[str, str | None]:
        return {slug: one.get("managed_by") for slug, one in self.schema().items()}

    def core_types(self) -> set[str]:
        """코어 API 로 열린 타입(`GET /api/core`) — 옛 SP 거나 못 읽으면 빈 것."""
        status, body = self._get("/api/core")
        if status != 200 or not isinstance(body, dict):
            return set()
        return {
            str(one.get("slug"))
            for one in body.get("types") or []
            if isinstance(one, dict) and one.get("slug")
        }


#: 코어 API 한 쪽 — SP 의 상한(`coreapi.services.MAX_LIMIT`).
CORE_PAGE = 2000


def _core_item(row: dict[str, Any]) -> dict[str, Any]:
    """코어 줄 → 목록 API 의 객체 모양. 별칭은 견주지 않는다(`properties` 에 섞여 온다)."""
    props = {k: v for k, v in (row.get("properties") or {}).items() if k != "aliases"}
    return {
        "key": row.get("key"),
        "label": row.get("label"),
        "status": row.get("status"),
        "properties": props,
        "ref_labels": {},
    }


class CoreReader:
    """**코어 API**(`/api/core/{타입}`)로 읽는다 — (변경 시각, id) 커서로 2,000개씩. 쪽마다
    같은 시간이 들어 대상이 커도 곧게 는다(27만 객체 32초 — 목록은 236초, 2026-11-06 실측).
    참조는 상대의 **식별자**로 온다. 코어로 **열린 타입**만 된다(타입의 「코어 공개」). 코어
    줄에는 설명 · 유효 연도가 없다 — 그 칸은 확인 못 함으로 센다."""

    via = "코어"
    keyed = True
    blind = frozenset({"description", "valid_from_year", "valid_to_year"})

    def __init__(self, lister: TargetReader, schema: dict[str, dict[str, Any]]) -> None:
        self.lister = lister
        self._schema = schema
        self.fallback = ""

    def objects(self, type_slug: str) -> Iterable[dict[str, Any]] | None:
        if type_slug not in self._schema:
            return None
        return self._pages(type_slug)

    def _pages(self, type_slug: str) -> Iterator[dict[str, Any]]:
        quoted = urllib.parse.quote(type_slug)
        cursor = ""
        while True:
            path = f"/api/core/{quoted}?limit={CORE_PAGE}"
            if cursor:
                path += f"&cursor={urllib.parse.quote(cursor)}"
            status, body = self.lister._get(path)
            if status != 200 or not isinstance(body, dict):
                raise ReconcileError(
                    f"대상의 {type_slug} 를 코어 API 로 못 읽었습니다({status})."
                )
            for row in body.get("items") or []:
                if isinstance(row, dict) and not row.get("deleted") and not row.get("hidden"):
                    yield _core_item(row)
            cursor = str(body.get("next") or "")
            if not cursor:
                return

    def managed_by(self) -> dict[str, str | None]:
        return {slug: one.get("managed_by") for slug, one in self._schema.items()}


#: 내보내기 줄에서 객체의 고정 칸 — 나머지는 그 타입의 속성이다(`bundles/export._row`).
_EXPORT_FIXED = (
    "key",
    "label",
    "status",
    "description",
    "valid_from_year",
    "valid_to_year",
    "aliases",
    "renamed_from",
    "previous_keys",
)


def _export_item(row: dict[str, Any]) -> dict[str, Any]:
    """내보내기 줄 → 목록 API 의 객체 모양(고정 칸 + `properties`). 참조는 이미 식별자다."""
    item: dict[str, Any] = {name: row[name] for name in _EXPORT_FIXED if name in row}
    item["properties"] = {k: v for k, v in row.items() if k not in _EXPORT_FIXED}
    item["ref_labels"] = {}
    return item


def export_groups(schema: dict[str, dict[str, Any]], slugs: Iterable[str]) -> list[str] | None:
    """그 타입들을 읽으려면 내보낼 사이드바 묶음 — 참조 칸이 가리키는 타입의 묶음까지(그래야
    참조가 식별자로 온다). 묶음이 없는 타입이 있으면 None(내보내기로는 못 읽는다)."""
    wanted = {slug for slug in slugs if slug in schema}
    for slug in list(wanted):
        for prop in schema[slug].get("properties") or []:
            target = prop.get("ref_type_slug") if isinstance(prop, dict) else None
            if prop.get("data_type") == "object_ref" and target and target in schema:
                wanted.add(str(target))
    groups: set[str] = set()
    for slug in wanted:
        group = schema[slug].get("nav_group_slug")
        if not group or schema[slug].get("kind_class") == "system":
            return None
        groups.add(str(group))
    return sorted(groups)


class ExportReader:
    """대상 SP 를 **묶음 내보내기 한 번**으로 읽는다 — 작업을 넣고 기다려 결과 파일을 받는다.
    시스템 관리자 토큰이어야 하고, 타입이 사이드바 묶음에 들어야 한다.

    내보내기를 못 쓰면(권한 · 묶음 밖 타입 · 결과 크기 상한 · 옛 SP) `fallback` 에 까닭을 적고
    목록 API(`TargetReader`)로 읽는다 — 대조가 멈추지는 않는다. **SP 의 내보내기는 객체가 6.5만
    개를 넘는 묶음에서 실패한다**(선을 고르는 질의에 id 를 다 넣어 매개변수 한도를 넘는다 —
    2026-11-06, SP v0.4.73). 그때도 목록으로 읽는다."""

    def __init__(self, target: Target, slugs: Iterable[str]) -> None:
        self.lister = TargetReader(target)
        self.target = target
        self._schema = self.lister.schema()
        self._items: dict[str, list[dict[str, Any]]] | None = None
        self.fallback = ""
        self.notice = ""
        groups = export_groups(self._schema, slugs)
        if not self._schema:
            self.fallback = "대상의 정의를 읽지 못했습니다"
        elif groups is None:
            self.fallback = "사이드바 묶음에 들지 않은 타입이 있습니다"
        elif groups:
            self._load(groups)

    def _load(self, groups: list[str]) -> None:
        server, token = self.target.server, self.target.token
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Client": "datarelay",
        }
        raw = json.dumps({"groups": groups, "allow_outside_refs": True}).encode("utf-8")
        url = f"{server.rstrip('/')}/api/bundles/export"
        try:
            status, answer = sp.pipeline._send(
                server, "POST", url, headers, raw, who="대상 SP"
            )
            if status != 202:
                sp.pipeline._refused("대상 SP", status, answer)
            job = sp.pipeline._wait_job(server, token, answer, who="대상 SP")
            envelope = sp.pipeline._get_json(
                server, token, f"/api/jobs/{job['id']}/download", who="대상 SP"
            )
        except sp.pipeline.Stop as stop:
            self.fallback = f"묶음 내보내기({', '.join(groups)})를 못 썼습니다: {_short(stop)}"
            return
        if not isinstance(envelope, dict):
            self.fallback = "묶음 내보내기의 결과를 읽을 수 없습니다"
            return
        exported = {
            str(one.get("slug"))
            for one in (envelope.get("ontology") or {}).get("types") or []
            if isinstance(one, dict)
        }
        items: dict[str, list[dict[str, Any]]] = {slug: [] for slug in exported}
        batches = envelope.get("objects") or []
        del envelope  # 줄을 객체 모양으로 옮기며 놓는다 — 큰 대상이면 봉투만 수백 MB 다
        while batches:
            batch = batches.pop()
            slug = str(batch.get("type_slug") or "")
            if slug in items:
                rows = batch.get("rows") or []
                items[slug].extend(_export_item(row) for row in rows if isinstance(row, dict))
        self._items = items

    @property
    def via(self) -> str:
        return "내보내기" if self._items is not None else "목록"

    @property
    def keyed(self) -> bool:
        return self._items is not None

    blind: frozenset[str] = frozenset()

    def objects(self, type_slug: str) -> list[dict[str, Any]] | None:
        if type_slug not in self._schema:
            return None
        if self._items is not None and type_slug in self._items:
            return self._items.pop(type_slug)
        return self.lister.objects(type_slug)

    def managed_by(self) -> dict[str, str | None]:
        return {slug: one.get("managed_by") for slug, one in self._schema.items()}


def best_reader(target: Target, slugs: Iterable[str]) -> Reader:
    """대상을 읽을 길 — 견주는 타입이 **다 코어로 열렸으면 코어 API**, 아니면 **묶음
    내보내기**, 그것도 안 되면 **목록 API**(까닭을 `fallback` 에). 한 대조는 한 길로 읽는다 —
    섞으면 참조를 푸는 법(식별자 · id)이 타입마다 달라진다."""
    lister = TargetReader(target)
    schema = lister.schema()
    present = [slug for slug in slugs if slug in schema]
    if not schema:
        lister.fallback = "대상의 정의를 읽지 못했습니다"
        return lister
    core = lister.core_types()
    if present and set(present) <= core:
        return CoreReader(lister, schema)
    exported = ExportReader(target, present)
    closed = ", ".join(sorted(set(present) - core))
    # 코어로 열기로 한 타입 하나를 빠뜨려도 대조는 돈다 — 무엇이 빠졌는지는 보고서가 말한다.
    if closed and exported.keyed:
        exported.notice = f"코어로 열리지 않은 타입({closed})이 있어 내보내기로 읽었습니다"
    elif closed:
        exported.fallback = f"코어로 열리지 않은 타입({closed}) · {exported.fallback}"
    return exported


@dataclass
class TypeDrift:
    rows: int = 0
    """기준의 줄."""
    missing: list[str] = field(default_factory=list)
    deprecated: list[str] = field(default_factory=list)
    changed: dict[str, list[str]] = field(default_factory=dict)
    """{식별자: 다른 칸}."""
    extra: list[str] = field(default_factory=list)
    unverified: int = 0
    """확인 못 한 참조 칸 — 상대를 이 대조에서 받지 않았고 이름도 다르다."""
    managed: bool = False
    """대상의 그 타입을 이 원천이 잠갔나 — 그래야 「대상에만」 을 보정 계획이 사용
    중지한다."""
    absent: bool = False
    """대상에 타입이 없다."""

    @property
    def drift(self) -> int:
        return len(self.missing) + len(self.deprecated) + len(self.changed) + len(self.extra)


@dataclass
class Report:
    at: str
    baseline: str
    source: str
    types: dict[str, TypeDrift] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    via: str = ""
    """대상을 어떻게 읽었나 — 내보내기 · 목록(내보내기를 못 써 쪽마다 읽었다)."""
    notes: list[str] = field(default_factory=list)
    """대조는 됐지만 알릴 것 — 내보내기를 못 쓴 까닭."""

    @property
    def drift(self) -> int:
        return sum(one.drift for one in self.types.values())

    def summary(self) -> str:
        if self.problems:
            return "대조하지 못했습니다 — " + " / ".join(self.problems)
        rows = sum(one.rows for one in self.types.values())
        if not self.drift:
            note = f"대상과 같습니다(객체 {rows}건)."
        else:
            parts = []
            for label, count in (
                ("빠짐", sum(len(one.missing) for one in self.types.values())),
                ("안 씀", sum(len(one.deprecated) for one in self.types.values())),
                ("다름", sum(len(one.changed) for one in self.types.values())),
                ("대상에만", sum(len(one.extra) for one in self.types.values())),
            ):
                if count:
                    parts.append(f"{label} {count}")
            note = f"대상과 어긋남 {self.drift}건({' · '.join(parts)}) — 기준 객체 {rows}건."
        unverified = sum(one.unverified for one in self.types.values())
        if unverified:
            note += f" 확인 못 한 참조 칸 {unverified}."
        for one in self.notes:
            note += f" {one}."
        return note

    def to_json(self, sample: int | None = None) -> dict[str, Any]:
        def cut(items: list[str]) -> list[str]:
            return items if sample is None else items[:sample]

        return {
            "at": self.at,
            "baseline": self.baseline,
            "source": self.source,
            "drift": self.drift,
            "summary": self.summary(),
            "problems": list(self.problems),
            "via": self.via,
            "notes": list(self.notes),
            "types": {
                slug: {
                    "rows": one.rows,
                    "missing": cut(one.missing),
                    "deprecated": cut(one.deprecated),
                    "changed": {key: one.changed[key] for key in cut(sorted(one.changed))},
                    "extra": cut(one.extra),
                    "counts": {
                        "missing": len(one.missing),
                        "deprecated": len(one.deprecated),
                        "changed": len(one.changed),
                        "extra": len(one.extra),
                    },
                    "unverified": one.unverified,
                    "managed": one.managed,
                    "absent": one.absent,
                }
                for slug, one in self.types.items()
            },
        }

    @classmethod
    def from_json(cls, body: dict[str, Any]) -> Report:
        types = {
            str(slug): TypeDrift(
                rows=int(one.get("rows") or 0),
                missing=list(one.get("missing") or []),
                deprecated=list(one.get("deprecated") or []),
                changed={str(k): list(v) for k, v in (one.get("changed") or {}).items()},
                extra=list(one.get("extra") or []),
                unverified=int(one.get("unverified") or 0),
                managed=bool(one.get("managed")),
                absent=bool(one.get("absent")),
            )
            for slug, one in (body.get("types") or {}).items()
        }
        return cls(
            at=str(body.get("at") or ""),
            baseline=str(body.get("baseline") or ""),
            source=str(body.get("source") or ""),
            types=types,
            problems=list(body.get("problems") or []),
            via=str(body.get("via") or ""),
            notes=list(body.get("notes") or []),
        )


# --- 값 견주기 ------------------------------------------------------------------------------


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _moment(value: Any) -> datetime | None:
    text = str(value).strip()
    if len(text) < 10 or text[4] != "-":
        return None
    try:
        found = datetime.fromisoformat(text.replace(" ", "T", 1))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=UTC)


def _scalar_same(expected: Any, actual: Any) -> bool:
    if str(expected).strip() == str(actual).strip():
        return True
    if isinstance(actual, bool) or str(actual).lower() in ("true", "false"):
        return str(expected).strip().lower() == str(actual).lower()
    left, right = _number(expected), _number(actual)
    if left is not None and right is not None:
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    then, now = _moment(expected), _moment(actual)
    return then is not None and now is not None and then == now


def _values(value: Any) -> list[Any]:
    if isinstance(value, list):
        return [one for one in value if not _empty(one)]
    if isinstance(value, str) and _MULTI in value:
        return [one.strip() for one in value.split(_MULTI) if one.strip()]
    return [value]


class _Refs:
    """대상의 id → (식별자, 이름) — 같은 대조에서 받은 객체 · 그 줄의 `ref_labels`."""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}

    def add(self, items: Iterable[dict[str, Any]]) -> None:
        for one in items:
            if one.get("id") and one.get("key"):
                self.keys[str(one["id"])] = str(one["key"])

    def same(self, expected: Any, actual: Any, labels: dict[str, str]) -> bool | None:
        """참조 칸이면 True · False, 확인 못 하면 None. 참조가 아니면 그냥 견준다."""
        wanted = [str(one).strip() for one in _values(expected)]
        have = _values(actual)
        if not any(str(one) in labels or str(one) in self.keys for one in have):
            return sorted(map(str, wanted)) == sorted(map(str, have)) or (
                len(wanted) == len(have) == 1 and _scalar_same(wanted[0], have[0])
            )
        names: list[set[str]] = []
        unknown = False
        for one in have:
            ident = str(one)
            options = {x for x in (self.keys.get(ident), labels.get(ident)) if x}
            if ident not in self.keys:
                unknown = True
            names.append(options)
        if len(wanted) == len(names) and all(
            any(want in options for options in names) for want in wanted
        ):
            return True
        return None if unknown else False


def _diff_row(
    row: dict[str, Any],
    item: dict[str, Any],
    refs: _Refs,
    blind: frozenset[str] = frozenset(),
) -> tuple[list[str], int]:
    """(다른 칸, 확인 못 한 칸). `blind` 는 읽은 길이 주지 않는 칸 — 확인 못 함으로 센다."""
    props = item.get("properties") or {}
    labels = {str(k): str(v) for k, v in (item.get("ref_labels") or {}).items()}
    differs: list[str] = []
    unverified = 0
    for column, expected in row.items():
        if column.startswith("_") or column in _NOT_COMPARED or _empty(expected):
            continue
        if column in blind:
            unverified += 1
            continue
        actual = item.get(column) if column in _FIXED else props.get(column)
        if _empty(actual):
            differs.append(column)
            continue
        verdict = refs.same(expected, actual, labels)
        if verdict is None:
            unverified += 1
        elif not verdict:
            differs.append(column)
    return differs, unverified


# --- 대조 · 보정 계획 -------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _baseline(work: Workdir, applied_run: str) -> Any:
    loaded = sp.pipeline.load(work.path(applied_run))
    if loaded.problems:
        raise ReconcileError(f"기준 출력을 못 읽었습니다({applied_run}): {loaded.problems}")
    return loaded


def _rows(batches: Iterable[Any]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for batch in batches:
        out.setdefault(batch.type_slug, []).extend(
            row for row in batch.rows if isinstance(row, dict)
        )
    return out


def compare(
    pipeline: Pipeline,
    work: Workdir,
    reader: Reader | None = None,
    submit: Submitter | None = None,
) -> Report:
    """대상과 기준을 견주고 보고서를 작업 폴더(`reconcile.json`) · 이력에 남긴다."""
    submit = submit or Submitter(pipeline.target)
    state = work.load_state()
    note = settle_pending(state, submit)
    before = state.applied_run
    # 나눠 보내는 계획이 일부만 들어갔으면 그 조각까지 기준에 접는다 — 안 그러면 들어간 것을
    # 「대상에만 있음」 · 「다름」 으로 센다.
    fold(work, state)
    if note or state.applied_run != before:
        work.save_state(state)
    report = Report(at=_now(), baseline=state.applied_run or "", source=pipeline.source)
    if not state.applied_run:
        report.problems.append("아직 적용한 것이 없습니다 — 견줄 기준이 없습니다.")
        return report
    baseline = _rows(_baseline(work, state.applied_run).objects)
    if reader is None:
        reader = best_reader(pipeline.target, baseline)
    report.via = str(getattr(reader, "via", ""))
    fallback = str(getattr(reader, "fallback", "") or "")
    if fallback:
        report.notes.append(f"목록 API 로 읽었습니다 — {fallback}")
    notice = str(getattr(reader, "notice", "") or "")
    if notice:
        report.notes.append(notice)
    keyed = bool(getattr(reader, "keyed", False))
    blind = frozenset(getattr(reader, "blind", frozenset()))
    managed = reader.managed_by()
    refs = _Refs()
    # 참조가 id 로 오면(목록 API) 다른 타입의 객체까지 받아 두어야 그 id 를 식별자로 푼다.
    # 식별자로 오면(코어 · 내보내기) 타입마다 **흘려** 견준다 — 대상 객체를 다 쥐지 않는다.
    held: dict[str, Iterable[dict[str, Any]] | None] = {}
    if not keyed:
        for slug in baseline:
            got = reader.objects(slug)
            held[slug] = list(got) if got is not None else None
            refs.add(held[slug] or [])
    for slug, rows in baseline.items():
        one = report.types.setdefault(slug, TypeDrift(rows=len(rows)))
        one.managed = bool(managed.get(slug)) and managed.get(slug) == pipeline.source
        items = held.pop(slug) if not keyed else reader.objects(slug)
        if items is None:
            one.absent = True
            one.missing = sorted(str(row.get("key")) for row in rows)
            report.problems.append(f"대상에 타입이 없습니다: {slug}")
            continue
        wanted = {str(row.get("key")): row for row in rows}
        seen: set[str] = set()
        for item in items:
            key = str(item.get("key") or "")
            if not key or key in seen:
                continue
            row = wanted.get(key)
            if row is None:
                if item.get("status") != "deprecated":
                    one.extra.append(key)
                continue
            seen.add(key)
            if item.get("status") == "deprecated":
                one.deprecated.append(key)
            differs, unverified = _diff_row(row, item, refs, blind)
            one.unverified += unverified
            if differs:
                one.changed[key] = differs
        one.missing = sorted(set(wanted) - seen)
        one.extra.sort()
        one.deprecated.sort()
    work.write_json(REPORT, report.to_json())
    state = work.load_state()
    state.history.append(
        {
            "at": report.at,
            "event": "reconciled",
            "drift": report.drift,
            "problems": list(report.problems),
            "summary": report.summary(),
        }
    )
    work.save_state(state)
    return report


def last_report(work: Workdir) -> Report | None:
    body = work.read_json(REPORT)
    return Report.from_json(body) if isinstance(body, dict) else None


def resync(
    pipeline: Pipeline,
    work: Workdir,
    reader: Reader | None = None,
    submit: Submitter | None = None,
) -> Outcome:
    """**보정 계획** — 지금 다시 견주고, 어긋난 줄을 기준 그대로 다시 보내는 미리보기를
    올린다.

    - 빠짐 · 다름은 기준의 줄 그대로, 안 씀은 `status: active` 를 더해 되살린다.
    - 대상에만 있는 것은 잠긴 타입에서만 무덤(사용 중지)으로.
    - 다시 보내는 객체에 닿는 기준의 선을 함께 — 대상에 이미 있으면 「그대로」 다.

    적용을 기다리는 계획이 있으면 올리지 않는다 — 그것을 덮으면 사람이 보던 계획이 사라진다."""
    submit = submit or Submitter(pipeline.target)
    state = work.load_state()
    settle_pending(state, submit)
    if state.pending is not None:
        return Outcome(
            "waiting",
            f"적용을 기다리는 계획({state.pending.job_id[:8]})이 있어 보정 계획을 생성하지 "
            "않았습니다 — 그 계획을 적용하거나 다시 실행한 뒤 대조하세요.",
        )
    report = compare(pipeline, work, reader, submit)
    if report.problems:
        return Outcome("blocked", report.summary())
    if not report.drift:
        return Outcome("nothing", report.summary())

    assert report.baseline  # compare 가 기준 없음을 problems 로 돌려준다
    loaded = _baseline(work, report.baseline)
    objects = _rows(loaded.objects)
    workspaces = {batch.type_slug: batch.workspace_slug for batch in loaded.objects}
    send: dict[str, list[dict[str, Any]]] = {}
    resent: dict[str, set[str]] = {}
    graves: list[dict[str, Any]] = []
    counts: dict[str, Any] = {"objects": {}, "relations": {}}
    for slug, drift in report.types.items():
        revive = set(drift.deprecated)
        keys = set(drift.missing) | set(drift.changed) | revive
        rows = [
            {**row, "status": "active"} if str(row.get("key")) in revive else row
            for row in objects.get(slug, [])
            if str(row.get("key")) in keys
        ]
        if rows:
            send[slug] = rows
            resent[slug] = {str(row.get("key")) for row in rows}
        gone = drift.extra if drift.managed else []
        graves.extend({"type_slug": slug, "key": key, "merged_into": None} for key in gone)
        counts["objects"][slug] = {
            "created": len(drift.missing),
            "changed": len(set(drift.changed) | revive),
            "unchanged": 0,
            "gone": len(gone),
        }
    if not send and not graves:
        return Outcome(
            "nothing",
            report.summary() + " 보정 계획에 실을 것이 없습니다 — 대상에만 있는 것은 "
            "잠기지 않은 타입이라 사람이 정합니다.",
        )
    every = set().union(*resent.values()) if resent else set()
    edges: dict[str, list[dict[str, Any]]] = {}
    for batch in loaded.relations:
        for row in batch.rows:
            if not isinstance(row, dict):
                continue
            if (
                str(row.get("src")) in resent.get(batch.type_slug, set())
                or str(row.get("dst")) in every
            ):
                edges.setdefault(batch.type_slug, []).append(row)
    for slug, rows in edges.items():
        counts["relations"][slug] = {
            "created": 0,
            "changed": len(rows),
            "unchanged": 0,
            "gone": 0,
        }

    when = stamp()
    folder = work.path(f"sends/{when}-resync")
    folder.mkdir(parents=True)
    order = write_batches(folder, sp.pipeline.OBJECTS_DIR, send, workspaces)
    edge_order = write_batches(folder, sp.pipeline.RELATIONS_DIR, edges, {})
    manifest = dict(loaded.manifest)
    manifest["objects_order"] = [
        slug for slug in loaded.manifest.get("objects_order", []) if slug in order
    ] or order
    manifest["relations_order"] = [
        slug for slug in loaded.manifest.get("relations_order", []) if slug in edge_order
    ] or edge_order
    manifest["source"] = pipeline.source
    manifest["title"] = f"{manifest.get('title') or pipeline.slug} — 대조의 보정 계획"
    (folder / sp.pipeline.MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if graves:
        (folder / sp.pipeline.TOMBSTONES).write_text(
            json.dumps({"objects": graves, "relations": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    run = work.path(report.baseline)
    if (run / sp.pipeline.ONTOLOGY).exists():
        (folder / sp.pipeline.ONTOLOGY).write_bytes((run / sp.pipeline.ONTOLOGY).read_bytes())

    try:
        plan, cut = propose(submit, folder, pipeline.target)
    except SubmitError as failure:
        return Outcome(
            "blocked", f"{report.summary()} 보정 계획을 생성하지 못했습니다: {failure}"
        )
    state = work.load_state()
    state.pending = pending_of(
        plan,
        cut,
        work,
        run=report.baseline,
        send=f"sends/{when}-resync",
        origin="reconcile",
        part_mb=pipeline.target.part_mb,
    )
    event: dict[str, Any] = {
        "at": _now(),
        "event": "planned",
        "origin": "reconcile",
        "job_id": plan.job_id,
        "ok": plan.ok,
        "counts": counts,
    }
    if cut.parts:
        event["parts"] = len(cut.parts)
    state.history.append(event)
    work.save_state(state)
    return Outcome(
        "planned" if plan.ok else "blocked",
        f"{report.summary()} 보정 계획: {state.pending.summary}",
        link=plan.link,
        counts=counts,
    )
