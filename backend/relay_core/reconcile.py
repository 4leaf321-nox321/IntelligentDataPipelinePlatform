"""대조 — 대상 SP 에 **지금 실제로 있는 것**을 지난번 적용된 출력(기준)과 견준다(개발계획 4c).

relay 는 평소 자기 출력끼리만 견준다(`diff`) — 원천이 안 바뀐 줄은 다시 안 보낸다. 그래서
허브에서 누가 객체를 지우거나 사용 중지하거나, 허브를 백업에서 되돌리면 relay 는 모른 채
어긋나서 돈다. 대조가 그것을 찾는다(타입마다):

    빠짐       기준엔 있는데 대상에 없다
    안 씀      기준엔 살아 있는데 대상에선 사용 중지다
    다름       칸 값이 기준과 다르다(빈 칸은 「안 보냄」 이라 견주지 않는다)
    대상에만   대상엔 살아 있는데 기준엔 없다

**읽기만 한다**(`GET /api/objects/{타입}` — 객체를 읽을 수 있는 토큰이면 된다). 고치는 일은
**보정 계획**(`resync`)이다 — 어긋난 줄을 기준 그대로 다시 보내는 미리보기라, 평소 계획처럼
대상의 검토함에 뜨고 적용은 사람이 한다. 다시 보내는 객체에 닿는 기준의 선도 함께 싣는다(객체가
지워졌으면 선도 사라졌다). **대상에만 있는 것은 그 타입을 이 원천이 잠갔을 때만**
(`managed_by == source`) 무덤으로 싣는다 — 안 잠긴 타입이면 사람이 만든 것일 수 있다.

선은 아직 따로 견주지 않는다 — 대상 API 가 관계 종류마다의 선 목록을 주지 않는다.

참조 칸은 대상이 상대의 id 로 준다. 같은 대조에서 받은 객체면 그 식별자로, 아니면 상대의 이름
(`ref_labels`)으로 견주고, 둘 다 아니면 **확인 못 함**으로 센다(어긋남으로 세지 않는다).
"""

from __future__ import annotations

import json
import math
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from relay_core import sp
from relay_core.definition import Pipeline, Target
from relay_core.diff import write_batches
from relay_core.runner import Outcome, settle_pending
from relay_core.state import Pending, Workdir, stamp
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
    def objects(self, type_slug: str) -> list[dict[str, Any]] | None:
        """그 타입의 객체 전부(사용 중지 포함) — 대상에 타입이 없으면 None."""

    def managed_by(self) -> dict[str, str | None]:
        """타입마다 잠근 원천(`ObjectType.managed_by`)."""


class TargetReader:
    """대상 SP 를 읽는다 — 목록 API 를 쪽마다."""

    def __init__(self, target: Target) -> None:
        self.target = target

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

    def managed_by(self) -> dict[str, str | None]:
        status, body = self._get("/api/ontology/schema")
        if status != 200 or not isinstance(body, dict):
            return {}
        return {
            str(one.get("slug")): one.get("managed_by")
            for one in body.get("types") or []
            if isinstance(one, dict)
        }


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


def _diff_row(row: dict[str, Any], item: dict[str, Any], refs: _Refs) -> tuple[list[str], int]:
    """(다른 칸, 확인 못 한 칸)."""
    props = item.get("properties") or {}
    labels = {str(k): str(v) for k, v in (item.get("ref_labels") or {}).items()}
    differs: list[str] = []
    unverified = 0
    for column, expected in row.items():
        if column.startswith("_") or column in _NOT_COMPARED or _empty(expected):
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
    reader = reader or TargetReader(pipeline.target)
    state = work.load_state()
    note = settle_pending(state, submit)
    if note:
        work.save_state(state)
    report = Report(at=_now(), baseline=state.applied_run or "", source=pipeline.source)
    if not state.applied_run:
        report.problems.append("아직 적용한 것이 없습니다 — 견줄 기준이 없습니다.")
        return report
    baseline = _rows(_baseline(work, state.applied_run).objects)
    managed = reader.managed_by()
    refs = _Refs()
    fetched: dict[str, list[dict[str, Any]] | None] = {}
    for slug in baseline:
        fetched[slug] = reader.objects(slug)
        refs.add(fetched[slug] or [])
    for slug, rows in baseline.items():
        one = report.types.setdefault(slug, TypeDrift(rows=len(rows)))
        one.managed = bool(managed.get(slug)) and managed.get(slug) == pipeline.source
        items = fetched[slug]
        if items is None:
            one.absent = True
            one.missing = sorted(str(row.get("key")) for row in rows)
            report.problems.append(f"대상에 타입이 없습니다: {slug}")
            continue
        by_key = {str(item["key"]): item for item in items if item.get("key")}
        wanted = set()
        for row in rows:
            key = str(row.get("key"))
            wanted.add(key)
            item = by_key.get(key)
            if item is None:
                one.missing.append(key)
                continue
            if item.get("status") == "deprecated":
                one.deprecated.append(key)
            differs, unverified = _diff_row(row, item, refs)
            one.unverified += unverified
            if differs:
                one.changed[key] = differs
        one.extra = sorted(
            key
            for key, item in by_key.items()
            if key not in wanted and item.get("status") != "deprecated"
        )
        one.missing.sort()
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
            f"적용을 기다리는 계획({state.pending.job_id[:8]})이 있어 보정 계획을 올리지 "
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
        plan = submit.preview(folder)
    except SubmitError as failure:
        return Outcome("blocked", f"{report.summary()} 보정 계획을 못 올렸습니다: {failure}")
    state = work.load_state()
    state.pending = Pending(
        run=report.baseline,
        send=f"sends/{when}-resync",
        job_id=plan.job_id,
        ok=plan.ok,
        digest=plan.digest,
        summary=plan.summary,
        at=_now(),
        origin="reconcile",
    )
    state.history.append(
        {
            "at": _now(),
            "event": "planned",
            "origin": "reconcile",
            "job_id": plan.job_id,
            "ok": plan.ok,
            "counts": counts,
        }
    )
    work.save_state(state)
    return Outcome(
        "planned" if plan.ok else "blocked",
        f"{report.summary()} 보정 계획: {plan.summary}",
        link=plan.link,
        counts=counts,
    )
