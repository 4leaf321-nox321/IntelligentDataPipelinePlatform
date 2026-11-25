"""보낸 것 걷어내기 — 이 파이프라인이 대상에 넣은 객체를 **대상에서 거둔다**(시험으로 보낸 것 ·
잘못 보낸 것).

대상 SP 는 원천이 잠근 타입의 객체를 화면에서 못 지운다(정본은 원천 — SP `managed`). 그래서
거두는 일도 원천 쪽, 이 플랫폼이 시킨다. 평소의 삭제와 같은 길이다 — 기준(지난번 **적용된**
출력)에서 고른 객체와 그것에 닿는 선을 뺀 **새 기준**을 짓고, 둘을 견줘(`diff.delta`) 무덤만
든 묶음을 대상에 미리 보기로 올린다. 적용은 사람이(대상 SP 화면 또는 여기 「적용」).

- 고르는 것: 전부(`all`) · 타입(`types`) · 식별자(`keys` — 타입마다). 기준에 없는 타입 ·
  식별자는 거절한다 — 이 파이프라인이 넣지 않은 것은 거두지 않는다.
- 선: 고른 객체가 끝인 기준의 선도 뺀다. 출발 쪽은 선 파일의 타입이고, 도착 쪽 타입은 관계
  종류의 정의(`dst_type_slugs` — 기준의 ontology.json, 없으면 부르는 쪽이 준 대상의 정의)로
  안다. **모르는 관계 종류는 출발 쪽만 본다** — 같은 식별자의 남의 객체에 닿은 선을 잘못 끊지
  않게.
- 적용되면 새 기준이 `applied_run` 이 된다 — 대조가 거둔 것을 「빠짐」 으로 되살리지 않는다.
- 거둔 식별자는 작업 폴더의 `retracted.json` 에 남는다. 다시 켠 뒤 원천에 그대로 있어 다시
  보낼 때 실행이 그 줄에 `status: active` 를 싣는다(`revive`) — 대상은 같은 칸의 줄을
  「그대로」 로 보고 사용 중지를 풀지 않는다(진짜 SP 로 확인, 2026-11-24). 보정 계획이 「안
  씀」 을 되살리는 것과 같은 칸이다.
- **파이프라인을 멈춘다**(`state.stopped`) — 원천에 그 행이 그대로 있으면 다음 실행이 다시
  만든다. 멈춘 동안 실행 · 타이머가 돌지 않고, 사람이 화면에서 「다시 켜기」 해야 돈다
  (`resume`).
- 무덤의 할 일은 둘이다(`action`). **사용 중지**(기본 — 대상은 객체를 남겨 두고 그만 쓴다)와
  **지우기**(`delete` — 대상 SP 0.4.101 부터. 화면의 삭제와 같고 기록은 남는다. 가리키는 것이
  있으면 대상이 그 줄을 막는다. 그 전 판은 칸을 무시하고 사용 중지한다 — 그래서 판을 보고
  거절한다(`supports_delete`)). SP 에 요청한 것 — `docs/sp-요청-원천이-보낸-것-지우기.md`.
- **앞서 사용 중지로 걷어낸 것을 지우기**(`previous`) — 거둔 식별자 중 기준에 없고 대상에
  사용 중지로 남은 것을 대상에서 읽어 고른다. 기준은 그대로다(이미 빠져 있다).
- 선 무덤에 도착 쪽 타입(`dst_type`)을 싣는다 — 거둔 객체의 타입, 아니면 관계 종류의 도착
  타입이 하나일 때. 끝이 인터페이스인 종류(도착이 여러 타입)는 이것 없이 같은 식별자의 다른
  타입 선을 끊을 수 있다(SP `RelationTombstoneIn`).
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from relay_core import reconcile, sp
from relay_core.definition import Pipeline
from relay_core.diff import delta
from relay_core.diff import head as diff_head
from relay_core.runner import (
    Outcome,
    counts_of,
    origin_for,
    pending_of,
    propose,
    settle_pending,
)
from relay_core.state import Workdir, stamp
from relay_core.submit import SubmitError, Submitter

#: 거절 · 안내에 늘어놓는 식별자의 수.
SHOWN = 10
#: 대기 계획 · 이력의 `origin`.
ORIGIN = "retract"
#: 거둔 식별자(타입 → 식별자들) — 다시 보낼 때 되살린다(`revive`). 백업이 받는다(작업 폴더의
#: 맨 위 `*.json`).
LEDGER = "retracted.json"

#: 무덤의 할 일 — 사용 중지(기본) · 지우기.
ACTIONS = ("deprecate", "delete")
#: 지우기를 받는 대상 SP 판 — 그 전 판은 `action` 칸을 무시하고 사용 중지한다.
DELETE_SINCE = (0, 4, 101)
_ACTION_LABEL = {"deprecate": "사용 중지", "delete": "삭제"}


def supports_delete(version: str) -> bool:
    """대상 SP 판(`/api/health` 의 `version` — `v0.4.101`)이 지우기를 받나."""
    numbers = re.findall(r"\d+", version or "")[:3]
    return len(numbers) == 3 and tuple(int(one) for one in numbers) >= DELETE_SINCE


def after_apply(stopped: Mapping[str, Any] | None) -> str:
    """걷어내기 계획을 적용한 뒤의 한마디 — 작업 결과에 붙인다."""
    done = (
        "삭제했습니다(이력과 감사 기록은 남고, 되돌릴 수 없습니다)"
        if (stopped or {}).get("action") == "delete"
        else "사용 중지했습니다"
    )
    return (
        f"전송 데이터를 회수했습니다. 대상에서 해당 객체를 {done}. 파이프라인은 중지 "
        "상태입니다. 원천에 해당 행이 남아 있으면 다음 실행에서 다시 생성됩니다. 원천에서 "
        "삭제하거나 레시피를 수정한 후 화면에서 [재개]를 클릭하세요."
    )


class RetractError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strings(value: Any, what: str) -> frozenset[str]:
    if not isinstance(value, list) or not all(isinstance(one, str) and one for one in value):
        raise RetractError(f"{what}는 문자열 목록이어야 합니다.")
    return frozenset(value)


@dataclass(frozen=True)
class Scope:
    """무엇을 거두나 — 전부 · 타입 · 식별자(타입마다) · 앞서 사용 중지로 걷어낸 것. 하나는
    있어야 한다."""

    all: bool = False
    types: frozenset[str] = frozenset()
    keys: Mapping[str, frozenset[str]] = field(default_factory=dict)
    previous: bool = False

    @classmethod
    def of(cls, body: Mapping[str, Any]) -> Scope:
        if body.get("previous") is True:
            if body.get("all") is True or body.get("types") or body.get("keys"):
                raise RetractError(
                    "previous는 단독으로 사용합니다. all, types, keys와 함께 지정할 수 "
                    "없습니다."
                )
            return cls(previous=True)
        every = body.get("all") is True
        types = _strings(body.get("types") or [], "types")
        raw = body.get("keys") or {}
        if not isinstance(raw, dict):
            raise RetractError("keys는 {타입: [식별자, …]} 형식이어야 합니다.")
        keys = {str(slug): _strings(found, f"keys.{slug}") for slug, found in raw.items()}
        keys = {slug: found for slug, found in keys.items() if found}
        if every and (types or keys):
            raise RetractError(
                "all은 단독으로 사용합니다. types, keys와 함께 지정할 수 없습니다."
            )
        if not (every or types or keys):
            raise RetractError(
                "회수 범위를 지정하세요. all: true(전체), types: [타입], keys: {타입: "
                "[식별자]}, previous: true(사용 중지로 회수한 객체) 중 하나입니다."
            )
        return cls(all=every, types=types, keys=keys)

    def picks(self, slug: str, key: str) -> bool:
        return self.all or slug in self.types or key in self.keys.get(slug, ())

    def text(self) -> str:
        if self.previous:
            return "사용 중지로 회수한 객체"
        if self.all:
            return "전송 데이터 전체"
        parts = []
        if self.types:
            parts.append("타입 " + ", ".join(sorted(self.types)))
        if self.keys:
            count = sum(len(found) for found in self.keys.values())
            each = " · ".join(
                f"{slug} {len(found)}" for slug, found in sorted(self.keys.items())
            )
            parts.append(f"식별자 {count}개({each})")
        return " + ".join(parts)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.previous:
            out["previous"] = True
        if self.all:
            out["all"] = True
        if self.types:
            out["types"] = sorted(self.types)
        if self.keys:
            out["keys"] = {slug: sorted(found) for slug, found in sorted(self.keys.items())}
        return out


def check(scope: Scope, action: str) -> None:
    """고른 것과 할 일이 맞나 — API 가 넣기 전에, 엔진이 짓기 전에 본다."""
    if action not in ACTIONS:
        raise RetractError(f"action은 {' · '.join(ACTIONS)} 중 하나입니다.")
    if scope.previous and action != "delete":
        raise RetractError(
            "사용 중지로 회수한 객체는 이미 사용 중지 상태입니다. previous는 action: "
            "delete(삭제)와 함께만 지정할 수 있습니다."
        )


def stopped_message(stopped: Mapping[str, Any]) -> str:
    by = f" · {stopped['by']}" if stopped.get("by") else ""
    return (
        f"전송 데이터를 회수하고 파이프라인을 중지한 상태입니다({stopped.get('scope') or '—'} "
        f"· {stopped.get('at') or ''}{by}). 원천에서 해당 데이터를 삭제하거나 레시피를 수정한 "
        "후 화면에서 [재개]를 클릭하세요."
    )


def _remember(work: Workdir, gone: Mapping[str, set[str]]) -> None:
    held = work.read_json(LEDGER)
    book: dict[str, set[str]] = {
        str(slug): {str(one) for one in keys}
        for slug, keys in (held.items() if isinstance(held, dict) else [])
        if isinstance(keys, list)
    }
    for slug, keys in gone.items():
        book.setdefault(slug, set()).update(keys)
    work.write_json(LEDGER, {slug: sorted(keys) for slug, keys in sorted(book.items())})


def revive(work: Workdir, send: Path) -> int:
    """보낼 묶음(`sends/<때>`)에서 **거둔 적이 있는 식별자**의 줄에 `status: active` 를
    싣는다 — 실행이 미리 보기 전에 부른다. 대상에 사용 중지로 남은 그 객체를 되살린다(안
    실으면 대상은 같은 칸의 줄을 「그대로」 로 본다). 실은 줄 수를 돌려준다. 거둔 적이 없으면
    아무것도 안 한다."""
    held = work.read_json(LEDGER)
    if not isinstance(held, dict) or not held:
        return 0
    book = {
        str(slug): set(map(str, keys)) for slug, keys in held.items() if isinstance(keys, list)
    }
    count = 0
    folder = send / sp.pipeline.OBJECTS_DIR
    for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        body = json.loads(file.read_text(encoding="utf-8"))
        keys = book.get(str(body.get("type_slug") or file.stem))
        if not keys or not isinstance(body.get("rows"), list):
            continue
        touched = 0
        for row in body["rows"]:
            if (
                isinstance(row, dict)
                and str(row.get("key")) in keys
                and row.get("status") != "active"
            ):
                row["status"] = "active"
                touched += 1
        if touched:
            file.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
            count += touched
    return count


def _ends(run: Path, extra: Mapping[str, Iterable[str]] | None) -> dict[str, set[str]]:
    """관계 종류 → 도착 쪽 타입들 — 기준의 정의가 먼저, 없는 것은 대상의 정의(`extra`)."""
    out = {str(slug): {str(one) for one in found} for slug, found in (extra or {}).items()}
    body = sp.pipeline._read_json(run / sp.pipeline.ONTOLOGY, [])
    kinds = body.get("relation_types") if isinstance(body, dict) else None
    for one in kinds if isinstance(kinds, list) else []:
        if isinstance(one, dict) and one.get("slug") and one.get("dst_type_slugs"):
            out[str(one["slug"])] = {str(x) for x in one["dst_type_slugs"]}
    return out


@dataclass
class _Carved:
    objects: dict[str, int] = field(default_factory=dict)
    """타입마다 거둔 객체 수."""
    relations: int = 0
    seen_types: set[str] = field(default_factory=set)
    seen_keys: dict[str, set[str]] = field(default_factory=dict)
    """`keys` 로 고른 것 중 기준에서 찾은 것 — 못 찾은 것을 거절하려고."""
    blind: set[str] = field(default_factory=set)
    """도착 쪽 타입을 모르는 관계 종류 — 출발 쪽만 보았다."""
    kept_objects: list[str] = field(default_factory=list)
    kept_relations: list[str] = field(default_factory=list)
    gone: dict[str, set[str]] = field(default_factory=dict)
    """타입마다 거둔 식별자."""


def _rewrite(file: Path, out: Path, body: dict[str, Any], rows: list[Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / file.name).write_text(
        json.dumps({**body, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _carve(base: Path, out: Path, scope: Scope, ends: dict[str, set[str]]) -> _Carved:
    """기준(`base`)에서 고른 객체와 그것에 닿는 선을 뺀 새 기준을 `out` 에 — 파일 하나씩
    흘려 쓴다(기준이 수십만 줄이어도 메모리에 다 들지 않는다). 고른 것이 없는 파일은 그대로
    옮긴다."""
    carved = _Carved()
    gone = carved.gone
    problems: list[str] = []
    folder = base / sp.pipeline.OBJECTS_DIR
    for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        body = sp.pipeline._read_json(file, problems)
        if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
            raise RetractError(f"기준 출력의 {file.name} 파일을 읽을 수 없습니다: {problems}")
        slug = str(body.get("type_slug") or file.stem)
        carved.seen_types.add(slug)
        keep: list[Any] = []
        for row in body["rows"]:
            key = str(row.get("key")) if isinstance(row, dict) else ""
            if key and scope.picks(slug, key):
                gone.setdefault(slug, set()).add(key)
                carved.objects[slug] = carved.objects.get(slug, 0) + 1
                if key in scope.keys.get(slug, ()):
                    carved.seen_keys.setdefault(slug, set()).add(key)
            else:
                keep.append(row)
        if keep:
            _rewrite(file, out / sp.pipeline.OBJECTS_DIR, body, keep)
            carved.kept_objects.append(slug)
    folder = base / sp.pipeline.RELATIONS_DIR
    for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        body = sp.pipeline._read_json(file, problems)
        if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
            raise RetractError(f"기준 출력의 {file.name} 파일을 읽을 수 없습니다: {problems}")
        slug = str(body.get("type_slug") or file.stem)
        keep = []
        for row in body["rows"]:
            if not isinstance(row, dict):
                keep.append(row)
                continue
            relation = str(row.get("relation") or "")
            dst_types = ends.get(relation)
            if dst_types is None:
                carved.blind.add(relation)
            hit = str(row.get("src")) in gone.get(slug, ()) or any(
                str(row.get("dst")) in gone.get(one, ()) for one in dst_types or ()
            )
            if hit:
                carved.relations += 1
            else:
                keep.append(row)
        if keep:
            _rewrite(file, out / sp.pipeline.RELATIONS_DIR, body, keep)
            carved.kept_relations.append(slug)
    return carved


def _mark(
    send: Path, action: str, gone: Mapping[str, set[str]], ends: Mapping[str, set[str]]
) -> None:
    """무덤에 할 일(`action` — 지우기일 때만 싣는다. 기본이 사용 중지다)과 선의 도착 쪽
    타입(`dst_type`)을 싣는다."""
    file = send / sp.pipeline.TOMBSTONES
    if not file.exists():
        return
    body = json.loads(file.read_text(encoding="utf-8"))
    if action == "delete":
        for one in body.get("objects") or []:
            one["action"] = "delete"
    for one in body.get("relations") or []:
        kinds = ends.get(str(one.get("relation") or "")) or set()
        hit = sorted(kind for kind in kinds if str(one.get("dst")) in gone.get(kind, ()))
        if len(hit) == 1:
            one["dst_type"] = hit[0]
        elif len(kinds) == 1:
            one["dst_type"] = next(iter(kinds))
    file.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def _previous(
    pipeline: Pipeline,
    work: Workdir,
    base: Path,
    manifest: Mapping[str, Any],
    send: Path,
    reader: reconcile.Reader | None,
) -> dict[str, set[str]]:
    """앞서 사용 중지로 걷어낸 것 — 거둔 식별자(`LEDGER`) 중 기준에 없고(다시 보내지 않았다)
    **대상에 사용 중지로 남은 것**. 대상에서 읽어 고르고, 지우기 무덤만 든 묶음을 `send` 에
    짓는다. 고를 것이 없으면 빈 것(묶음을 안 짓는다)."""
    held = work.read_json(LEDGER)
    book = {
        str(slug): set(map(str, keys))
        for slug, keys in (held.items() if isinstance(held, dict) else [])
        if isinstance(keys, list) and keys
    }
    problems: list[str] = []
    folder = base / sp.pipeline.OBJECTS_DIR
    for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        body = sp.pipeline._read_json(file, problems)
        slug = (
            str((body or {}).get("type_slug") or file.stem) if isinstance(body, dict) else ""
        )
        if slug in book and isinstance(body, dict):
            book[slug] -= {
                str(row.get("key")) for row in body.get("rows") or [] if isinstance(row, dict)
            }
    book = {slug: keys for slug, keys in book.items() if keys}
    if not book:
        return {}
    try:
        reader = reader or reconcile.best_reader(pipeline.target, book)
        found: dict[str, set[str]] = {}
        for slug, keys in book.items():
            for one in reader.objects(slug) or []:
                key = str(one.get("key"))
                if key in keys and one.get("status") == "deprecated":
                    found.setdefault(slug, set()).add(key)
    except reconcile.ReconcileError as failure:
        raise RetractError(f"대상을 조회하지 못했습니다: {failure}") from None
    if not found:
        return {}
    send.mkdir(parents=True)
    head = {
        **manifest,
        "objects_order": [],
        "relations_order": [],
        "source": pipeline.source,
    }
    (send / sp.pipeline.MANIFEST).write_text(
        json.dumps(head, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    graves = [
        {"type_slug": slug, "key": key, "merged_into": None, "action": "delete"}
        for slug, keys in sorted(found.items())
        for key in sorted(keys)
    ]
    (send / sp.pipeline.TOMBSTONES).write_text(
        json.dumps({"objects": graves, "relations": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return found


def _refusal(scope: Scope, carved: _Carved) -> str:
    unknown = sorted(scope.types - carved.seen_types)
    if unknown:
        return (
            f"이 파이프라인이 대상에 적재하지 않은 타입입니다: {', '.join(unknown)}. 적재한 "
            f"타입: {', '.join(sorted(carved.seen_types)) or '없음'}."
        )
    missing = [
        f"{slug}/{key}"
        for slug, keys in sorted(scope.keys.items())
        for key in sorted(keys - carved.seen_keys.get(slug, set()))
    ]
    if missing:
        more = f" 외 {len(missing) - SHOWN}개" if len(missing) > SHOWN else ""
        return (
            "이 파이프라인의 기준 출력에 없는 식별자입니다: "
            f"{', '.join(missing[:SHOWN])}{more}. 다른 원천이나 사람이 생성한 객체는 회수하지 "
            "않습니다."
        )
    return ""


def retract(
    pipeline: Pipeline,
    work: Workdir,
    scope: Scope,
    *,
    by: str = "",
    action: str = "deprecate",
    ends: Mapping[str, Iterable[str]] | None = None,
    submit: Submitter | None = None,
    reader: reconcile.Reader | None = None,
) -> Outcome:
    """고른 것을 거두는 계획(미리 보기)을 대상에 올리고 파이프라인을 멈춘다. `action` 은
    무덤의 할 일(사용 중지 · 지우기 — 대상 판은 부르는 쪽이 본다, `supports_delete`). `ends` 는
    대상의 관계 종류마다 도착 쪽 타입(기준의 정의에 없는 것을 채운다). `reader` 는 앞서 걷어낸
    것을 대상에서 읽는 길(없으면 대조와 같은 길)."""
    check(scope, action)
    submit = submit or Submitter(pipeline.target)
    state = work.load_state()
    note = settle_pending(state, submit)
    if note:
        work.save_state(state)
    if state.pending is not None:
        return Outcome(
            "waiting",
            note
            + f"적용 대기 중인 계획({state.pending.job_id[:8]})이 있어 회수 계획을 생성하지 "
            "않았습니다. 계획을 적용하거나 다시 실행한 후 진행하세요.",
        )
    if not state.applied_run:
        return Outcome("nothing", note + "대상에 적용한 계획이 없어 회수할 데이터가 없습니다.")
    base = work.path(state.applied_run)
    manifest, problems = diff_head(base)
    if problems:
        raise RetractError(f"기준 출력을 읽을 수 없습니다({state.applied_run}): {problems}")

    when = stamp()
    run_name, send_name = f"runs/{when}-{ORIGIN}", f"sends/{when}-{ORIGIN}"
    out, send = work.path(run_name), work.path(send_name)
    if scope.previous:
        found_before = _previous(pipeline, work, base, manifest, send, reader)
        if not found_before:
            return Outcome(
                "nothing",
                note
                + "사용 중지로 회수한 객체 중 대상에 사용 중지 상태로 남은 객체가 없습니다.",
            )
        count = sum(len(keys) for keys in found_before.values())
        counts: dict[str, Any] = {
            "objects": {
                slug: {"created": 0, "changed": 0, "unchanged": 0, "gone": len(keys)}
                for slug, keys in found_before.items()
            },
            "relations": {},
        }
        # 기준은 그대로다 — 거둔 것은 이미 빠져 있다.
        return _plan(
            pipeline,
            work,
            state,
            submit,
            note,
            scope,
            by,
            action,
            run=state.applied_run,
            send_name=send_name,
            counts=counts,
            objects=count,
            relations=0,
            blind="",
        )
    out.mkdir(parents=True)
    try:
        carved = _carve(base, out, scope, _ends(base, ends))
        refused = _refusal(scope, carved)
        if refused:
            raise RetractError(refused)
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    if not carved.objects:
        shutil.rmtree(out, ignore_errors=True)
        return Outcome("nothing", note + "선택한 객체 중 대상에 남은 객체가 없습니다.")
    head = dict(manifest)
    for kind, kept in (("objects", carved.kept_objects), ("relations", carved.kept_relations)):
        head[f"{kind}_order"] = [
            one for one in manifest.get(f"{kind}_order") or [] if one in kept
        ]
    (out / sp.pipeline.MANIFEST).write_text(
        json.dumps(head, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name in (sp.pipeline.ONTOLOGY, sp.pipeline.UNRESOLVED):
        if (base / name).exists():
            shutil.copyfile(base / name, out / name)

    found = delta(base, out, send, source=pipeline.source)
    _mark(send, action, carved.gone, _ends(base, ends))
    _remember(work, carved.gone)
    blind = (
        f" 도착 타입을 알 수 없는 관계 종류({', '.join(sorted(carved.blind))})는 출발 객체 "
        "기준으로만 확인했습니다."
        if carved.blind
        else ""
    )
    return _plan(
        pipeline,
        work,
        state,
        submit,
        note,
        scope,
        by,
        action,
        run=run_name,
        send_name=send_name,
        counts=counts_of(found),
        objects=sum(carved.objects.values()),
        relations=carved.relations,
        blind=blind,
    )


def _plan(
    pipeline: Pipeline,
    work: Workdir,
    state: Any,
    submit: Submitter,
    note: str,
    scope: Scope,
    by: str,
    action: str,
    *,
    run: str,
    send_name: str,
    counts: dict[str, Any],
    objects: int,
    relations: int,
    blind: str,
) -> Outcome:
    """지은 묶음(`send_name`)을 미리 보기로 올리고, 오류가 없으면 대기 계획으로 두고 멈춘다.
    `run` 은 적용되면 기준이 될 출력이다."""
    send = work.path(send_name)
    head = json.loads((send / sp.pipeline.MANIFEST).read_text(encoding="utf-8"))
    head["title"] = f"{head.get('title') or pipeline.slug} — 전송 데이터 회수"
    (send / sp.pipeline.MANIFEST).write_text(
        json.dumps(head, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        plan, cut = propose(
            submit, send, pipeline.target, origin=origin_for(pipeline, send_name)
        )
    except SubmitError as failure:
        return Outcome("blocked", f"{note}회수 계획을 생성하지 못했습니다: {failure}")
    if not plan.ok:
        # 계획에 오류가 있으면 상태를 바꾸지 않는다 — 멈추지도, 대기 계획으로 두지도 않는다.
        state.history.append(
            {"at": _now(), "event": "blocked", "origin": ORIGIN, "job_id": plan.job_id}
        )
        work.save_state(state)
        return Outcome(
            "blocked",
            f"{note}회수 계획에 오류가 있습니다. 파이프라인은 중지하지 않았습니다. "
            f"{plan.summary}",
            link=plan.link,
            counts=counts,
        )
    state.pending = pending_of(
        plan,
        cut,
        work,
        run=run,
        send=send_name,
        origin=ORIGIN,
        part_mb=pipeline.target.part_mb,
    )
    label = _ACTION_LABEL[action]
    shown = f"{scope.text()} · {label}"
    state.stopped = {"at": _now(), "by": by, "why": ORIGIN, "scope": shown, "action": action}
    event: dict[str, Any] = {
        "at": _now(),
        "event": "planned",
        "origin": ORIGIN,
        "job_id": plan.job_id,
        "ok": plan.ok,
        "counts": counts,
        "scope": shown,
        "action": action,
    }
    if by:
        event["by"] = by
    if cut.parts:
        event["parts"] = len(cut.parts)
    state.history.append(event)
    work.save_state(state)
    edges = f" · 관계 {relations:,}건 해제" if relations else ""
    return Outcome(
        "planned",
        f"{note}회수 계획({shown})을 생성하고 파이프라인을 중지했습니다. 객체 {objects:,}개 "
        f"{label}{edges}. 대상 SP에서 계획을 확인하고 [적용]을 클릭하세요. [재개] 전까지 "
        f"실행과 타이머가 동작하지 않습니다.{blind}",
        link=plan.link,
        counts=counts,
    )


def resume(work: Workdir, applied: Callable[[str], bool | None], *, by: str = "") -> str:
    """멈춘 파이프라인을 다시 켠다 — 사람이 화면에서. 걷어내기 계획이 아직 대기 중이면:
    대상에서 적용됐으면 기준으로 올리고, 아니면 그 계획을 거둔다. `applied(job_id)` 가
    None 이면(대상에 닿지 않음) 켜지 않는다 — 적용됐는지 모른 채 계획을 거두면 대상에서 거둔
    것을 기준이 아직 들고 있게 된다."""
    state = work.load_state()
    if not state.stopped:
        raise RetractError("파이프라인이 중지 상태가 아닙니다.")
    note = ""
    pending = state.pending
    if pending is not None and pending.origin == ORIGIN:
        known = applied(pending.job_id) if pending.job_id else False
        if known is None:
            raise RetractError(
                "대상에 연결할 수 없어 회수 계획의 적용 여부를 확인할 수 없습니다. 대상에 "
                "연결된 후 재개하세요."
            )
        if pending.part > 0 or (known and pending.total > 1):
            raise RetractError(
                "회수 계획의 분할 번들 적용이 시작되었습니다. [적용]으로 모든 분할 번들을 "
                "적용한 후 재개하세요."
            )
        if known:
            note = settle_pending(state, _Applied())  # type: ignore[arg-type]
        else:
            state.history.append(
                {
                    "at": _now(),
                    "event": "retract_cancelled",
                    "job_id": pending.job_id,
                    **({"by": by} if by else {}),
                }
            )
            state.pending = None
            note = (
                f"적용하지 않은 회수 계획({pending.job_id[:8]})을 취소했습니다. 대상 SP의 "
                "[작업] 화면에 남은 해당 미리 보기는 적용하지 마세요. "
            )
    state.stopped = None
    state.history.append({"at": _now(), "event": "resumed", **({"by": by} if by else {})})
    work.save_state(state)
    return (
        note + "파이프라인을 재개했습니다. 다음 실행부터 동작합니다. 원천에 회수한 행이 남아 "
        "있으면 다음 계획에서 다시 생성됩니다."
    )


class _Applied:
    """적용됐다고 이미 안 계획을 기준으로 올릴 때 — `settle_pending` 의 묻기를 대신한다."""

    def applied(self, job_id: str) -> bool:
        return True
