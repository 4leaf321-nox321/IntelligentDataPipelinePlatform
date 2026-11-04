#!/usr/bin/env python3
"""StandardPlatform 정제 파이프라인 — AI 결과물을 검토받고 넣는 길.

원천 데이터를 AI(Claude Code · Gemini CLI)가 곧바로 플랫폼에 넣으면
그룹마다 판단이 달라지고, 다시 넣을 때마다 같은 것이 둘씩 생기고,
무엇이 왜 들어갔는지 남지 않는다. 그래서 AI 는 **실행 폴더(run)** 에
결과물 묶음을 만들고, 사람은 이 도구로 검증 · 미리 보기 · 적재한다.

    python sp_pipeline.py init     runs/2026-09-13-parts
    python sp_pipeline.py validate runs/2026-09-13-parts
    python sp_pipeline.py preview  runs/2026-09-13-parts
    python sp_pipeline.py apply    runs/2026-09-13-parts

서버 주소와 토큰은 `SP_SERVER` · `SP_TOKEN` 환경 변수(또는 `--server`
· `--token`). 토큰은 플랫폼 「내 정보」 에서 발급한 개인 토큰이다.

표준 라이브러리만 쓴다 — 받는 사람 PC 에 무엇을 더 깔게 하지 않는다.
실행 폴더의 모양은 같은 폴더의 `AGENTS.md` 가 정본이다.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORMAT = "sp-bundle/1"
MANIFEST = "bundle.json"
ONTOLOGY = "ontology.json"
OBJECTS_DIR = "objects"
RELATIONS_DIR = "relations"
TOMBSTONES = "tombstones.json"
UNRESOLVED = "unresolved.json"
PREVIEW = "preview.json"
APPLIED = "applied.json"

#: 감사 기록에 남는 통로 이름 — 사람이 화면에서 넣은 것과 가른다.
CLIENT = "sp-pipeline"

# --- 이 PC 가 겨누는 플랫폼들 -----------------------------------------------------
#
# **한 PC 가 여러 플랫폼에 넣는다**(허브 · 쌍둥이 여럿). 그래서 주소 · 토큰을 한 벌로 두지
# 않고 **이름을 붙여 여럿** 둔다. 설치(`sp_setup.py`)를 플랫폼마다 한 번씩 돌리면 더해진다 —
# 앞에 등록한 것은 남는다.
#
# **작업 폴더가 자기 플랫폼을 기억한다**(`work.json` 의 `platform`). 검증 · 미리 보기는 그
# 플랫폼으로, 적용은 **미리 본 그 플랫폼으로만** 간다. 둘 이상인데 정해지지 않았으면 짐작하지
# 않고 묻는다 — 엉뚱한 플랫폼에 넣은 수만 줄은 되돌리기 전까지 그곳의 데이터다.
#
# 설정 파일은 키트 폴더가 아니라 **사용자 설정 폴더**에 둔다. 키트를 새 판으로 다시 풀어도
# 등록한 플랫폼이 남아야 하고, 사람이 명령 창에서 치는 적용 · 되돌리기도 같은 곳을 읽어야
# 한다(예전에는 주소 · 토큰을 AI 클라이언트의 MCP 설정에만 넣어서, 명령 창의 적용이 맨 끝에서
# 「서버와 토큰이 필요합니다」 로 멈췄다).
#
#     {"work_root": "D:\\온톨로지작업",
#      "platforms": {"rootdesign": {"server": "http://…:3030/rootdesign", "token": "spt_…"}},
#      "hub": {"server": "…", "token": "…"}}

#: 플랫폼 이름 — 그 설치의 slug 를 쓴다(화면의 설치 명령이 채워 준다).
PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


def settings_path() -> Path:
    """사용자 설정 폴더의 `sp-pipeline/settings.json` — `SP_SETTINGS` 로 바꿀 수 있다(시험)."""
    explicit = os.environ.get("SP_SETTINGS", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "sp-pipeline" / "settings.json"


def load_settings() -> dict[str, Any]:
    """없거나 읽을 수 없으면 빈 것 — 「무엇이 없다」 는 부르는 쪽이 말한다."""
    try:
        loaded = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_settings(body: dict[str, Any]) -> Path:
    """토큰이 평문이다 — Claude Desktop 설정에 두던 것과 같은 값 · 같은 PC 다. 이 사용자만
    읽게 둔다(POSIX. Windows 는 사용자 폴더의 권한을 따른다)."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path


def platforms() -> dict[str, dict[str, str]]:
    """등록한 플랫폼들 — `{이름: {server, token}}`."""
    found = load_settings().get("platforms")
    if not isinstance(found, dict):
        return {}
    return {
        str(name): {
            "server": str(one.get("server") or ""),
            "token": str(one.get("token") or ""),
        }
        for name, one in found.items()
        if isinstance(one, dict)
    }


def setting(name: str) -> str:
    """환경 변수 → 설정 파일 차례로 — 작업 폴더 뿌리(`SP_WORK_ROOT`)와 허브(`SP_HUB_*`).

    **환경이 이긴다** — 창에서 잠깐 다른 곳을 볼 때. 플랫폼 주소 · 토큰은 여기가 아니라
    `target()` 이 정한다(이름으로 고른다).
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value
    settings = load_settings()
    hub = settings.get("hub") if isinstance(settings.get("hub"), dict) else {}
    found = {
        "SP_WORK_ROOT": settings.get("work_root"),
        "SP_HUB_SERVER": hub.get("server"),
        "SP_HUB_TOKEN": hub.get("token"),
    }.get(name)
    return str(found or "").strip()


@dataclass(frozen=True)
class Target:
    """넣을 곳 — 이름 · 주소 · 토큰. 이름이 비면 환경 변수로 준 것(옛 설치)이다."""

    name: str
    server: str
    token: str

    @property
    def shown(self) -> str:
        return f"{self.name} ({self.server})" if self.name else self.server


def _listing(known: dict[str, dict[str, str]]) -> str:
    return " · ".join(f"{name}({one['server']})" for name, one in sorted(known.items()))


def target(platform: str = "") -> Target:
    """**어느 플랫폼으로 가나** — 이름을 주면 그것, 아니면 하나뿐일 때만 그것.

    1. 이름이 있으면 등록한 것 중에서 — 없으면 무엇이 등록돼 있는지와 더하는 법을 말한다.
    2. `SP_SERVER` · `SP_TOKEN` 환경 변수 — 옛 설치(MCP 설정의 env)와 한 번만 다른 곳을 볼 때.
    3. 등록한 것이 **하나뿐이면** 그것.
    4. 여럿인데 이름이 없으면 **짐작하지 않고 멈춘다.**
    """
    known = platforms()
    if platform:
        found = known.get(platform)
        if found is None:
            raise Stop(
                f"이 PC 에 등록되지 않은 플랫폼입니다: {platform} — "
                f"등록된 것: {_listing(known) or '없음'}. 그 플랫폼 화면 「내 정보」 의 "
                "정제 도구 키트에 있는 설치 명령을 이 PC 에서 한 번 실행하면 더해집니다."
            )
        if not found["server"] or not found["token"]:
            raise Stop(f"플랫폼 {platform} 의 주소나 토큰이 비어 있습니다 — 설치 명령을 다시")
        return Target(platform, found["server"], found["token"])
    server = os.environ.get("SP_SERVER", "").strip()
    token = os.environ.get("SP_TOKEN", "").strip()
    if server and token:
        return Target("", server, token)
    if len(known) == 1:
        return target(next(iter(known)))
    if not known:
        raise Stop(
            "이 PC 에 등록된 플랫폼이 없습니다 — 플랫폼 화면 「내 정보」 의 정제 도구 키트에 "
            "있는 설치 명령을 실행하세요"
        )
    raise Stop(
        f"플랫폼이 여럿입니다 — {_listing(known)}. 어느 곳인지 정해야 합니다: 작업 폴더는 "
        "`work_platform`(또는 만들 때 platform=), 명령이면 --platform"
    )


def work_platform(run: Path) -> str:
    """실행 폴더가 든 **작업 폴더의 플랫폼** — `runs/<실행>` 의 두 단계 위 `work.json`.

    작업 폴더 밖의 실행(명령으로 바로 만든 것)이면 빈 값이다.
    """
    if run.parent.name != "runs":
        return ""
    body = _read_json(run.parent.parent / "work.json", [])
    return str(body.get("platform") or "") if isinstance(body, dict) else ""


#: 플랫폼이 한 번에 받는 행 수(파일로 넣기와 같다).
MAX_ROWS = 5000
RELATION_FIELDS = ("src", "relation", "dst", "evidence_note", "properties")
"""관계 한 줄의 칸. `properties` 는 **그 관계 종류의 속성**이다(인과 관계의 근거 건수처럼) —
모양은 플랫폼이 관계 종류의 정의로 본다."""
#: 이보다 낮은 확신도의 행은 넣지 않는다 — 모델링 규약 5장. unresolved.json 으로 간다.
LOW_CONFIDENCE = 0.7
ONTOLOGY_KEYS = {"groups", "interfaces", "types", "relation_types"}

#: 대량 적재(백필)에 켜는 칸들 — `bundle.json` 의 `backfill` 에 적거나 `--backfill` 로 켠다.
#:
#: 넷 다 기본값이 아닌 이유: 평소 적재에서는 기본값이 맞다(전부 아니면 무 · 줄마다 기록).
#: 수만 줄에서는 그 기본값이 발목을 잡는다 — 미리 보기가 적용을 두 번 돌고, 웹훅이 수만 번
#: 나가고, 감사 로그가 뒤덮이고, 한 줄이 전체를 막는다.
BACKFILL = {
    "preview": "plan",
    "events": "summary",
    "audit": "summary",
    "missing_refs": "blank",
}
#: 묶음에 실을 수 있는 칸과 그 값 — 실행 폴더가 적은 것을 여기서 검사한다.
BUNDLE_OPTIONS = {
    "preview": ("full", "plan"),
    "events": ("each", "summary"),
    "audit": ("each", "summary"),
    "missing_refs": ("error", "blank"),
    # **사람이 화면에서 고친 칸**을 비켜 갈지 덮을지. 기본(안 적으면)은 비켜 가는 것이고,
    # 백필에서도 그대로다 — 수만 줄을 넣는다고 사람의 수정을 되돌릴 이유는 없다.
    "human_edits": ("keep", "overwrite"),
}

#: (method, url, headers, body) -> (status, json). 시험이 갈아 끼운다.
Sender = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Any]]


def _urllib_send(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float = 600,
) -> tuple[int, Any]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as failure:
        raw = failure.read()
        try:
            return failure.code, json.loads(raw or b"null")
        except ValueError:
            text = raw.decode("utf-8", errors="replace")
            return failure.code, {"error": {"message": text}}


SEND: Sender = _urllib_send
#: 작업을 기다리는 사이의 잠. 시험이 no-op 으로 바꾼다.
WAIT: Callable[[float], None] = time.sleep
POLL_SECONDS = 1.5
#: 「대기」 가 이만큼 이어지면 멈추고 말한다 — **집을 워커가 없다는 뜻이다.** 말없이 계속
#: 기다리면 사람은 제 묶음이 잘못된 줄 알고 몇 번을 다시 만든다. 도는 중(running)이면 안 센다.
QUEUED_LIMIT_SECONDS = 120.0


# --------------------------------------------------------------------------
# 실행 폴더 읽기
# --------------------------------------------------------------------------


@dataclass
class Batch:
    type_slug: str
    file: str
    rows: list[Any]
    workspace_slug: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    """묶음에 붙은 **그 밖의 것** — `aliases_mode`(별칭을 더할지 맞출지) · `mode`(관계를
    더할지 맞출지). 허브가 적어 보낸 것을 **그대로 전해야** 한다: 여기서 떨어뜨리면 허브에서
    뺀 별칭과 끊은 선이 받는 쪽에 남아 둘이 갈린다."""


@dataclass
class Run:
    path: Path
    manifest: dict[str, Any] = field(default_factory=dict)
    ontology: dict[str, Any] | None = None
    objects: list[Batch] = field(default_factory=list)
    relations: list[Batch] = field(default_factory=list)
    tombstones: dict[str, Any] | None = None
    """허브에서 **사라진 것** — 지운 객체와 끊긴 선. 받는 쪽이 사용 중지 · 합치기 · 끊기를
    계획에 올린다."""
    unresolved: list[Any] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    """파일을 읽다가 난 문제 — 모양을 검사하기도 전에 막힌 것."""


def _read_json(path: Path, problems: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as failure:
        problems.append(f"{path.name}: JSON 을 읽을 수 없습니다 ({failure})")
        return None


def _batches(run: Run, folder: str) -> list[Batch]:
    base = run.path / folder
    if not base.is_dir():
        return []
    found: dict[str, Batch] = {}
    for file in sorted(base.glob("*.json")):
        body = _read_json(file, run.problems)
        if body is None:
            continue
        if not isinstance(body, dict) or not isinstance(body.get("rows"), list):
            run.problems.append(f'{folder}/{file.name}: {{"rows": [...]}} 모양이어야 합니다')
            continue
        slug = str(body.get("type_slug") or file.stem)
        found[file.name] = Batch(
            type_slug=slug,
            file=f"{folder}/{file.name}",
            rows=body["rows"],
            workspace_slug=body.get("workspace_slug"),
            extra={
                key: body[key] for key in ("aliases_mode", "mode") if body.get(key) is not None
            },
        )
    # **적는 차례대로 넣는다.** 참조하는 타입은 참조되는 타입 뒤에 와야 한다.
    order = [str(one) for one in run.manifest.get(f"{folder}_order") or []]
    ranked = sorted(
        found.values(),
        key=lambda one: order.index(one.type_slug) if one.type_slug in order else len(order),
    )
    return ranked


def load(path: Path) -> Run:
    run = Run(path=path)
    manifest = _read_json(path / MANIFEST, run.problems)
    run.manifest = manifest if isinstance(manifest, dict) else {}
    if manifest is None and not (path / MANIFEST).exists():
        run.problems.append(f"{MANIFEST} 가 없습니다 — 먼저 init 하세요")
    ontology = _read_json(path / ONTOLOGY, run.problems)
    if isinstance(ontology, dict) and any(ontology.get(key) for key in ONTOLOGY_KEYS):
        run.ontology = ontology
    elif ontology is not None and not isinstance(ontology, dict):
        run.problems.append(f"{ONTOLOGY}: 객체({{...}})여야 합니다")
    run.objects = _batches(run, OBJECTS_DIR)
    run.relations = _batches(run, RELATIONS_DIR)
    graves = _read_json(path / TOMBSTONES, run.problems)
    if isinstance(graves, dict) and (graves.get("objects") or graves.get("relations")):
        run.tombstones = graves
    elif graves is not None and not isinstance(graves, dict):
        run.problems.append(f"{TOMBSTONES}: 객체({{...}})여야 합니다")
    unresolved = _read_json(path / UNRESOLVED, run.problems)
    run.unresolved = unresolved if isinstance(unresolved, list) else []
    return run


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """`_` 로 시작하는 칸(출처 · 확신도 · 메모)은 **플랫폼에 안 보낸다.**

    출처는 행 옆에 두어야 AI 가 빠뜨리지 않고, 사람이 행을 보며 확인한다. 그런데
    플랫폼은 모르는 열을 거절한다 — 그래서 보내기 전에 여기서 뗀다.
    """
    return {key: value for key, value in row.items() if not str(key).startswith("_")}


def payload(run: Run) -> dict[str, Any]:
    """플랫폼 `POST /api/bundles/import` 에 보내는 몸통(`apply` 제외).

    허브에서 받은 실행(`bundle.json` 의 `source`)이면 그 이름을 싣는다 — 플랫폼은 그 허브가
    관리하는 정의 · 객체만 이 길로 고치게 한다."""
    body = {
        "ontology": run.ontology,
        "objects": [
            {
                "type_slug": one.type_slug,
                "workspace_slug": one.workspace_slug,
                "rows": [_clean(row) for row in one.rows if isinstance(row, dict)],
                **one.extra,
            }
            for one in run.objects
        ],
        "relations": [
            {
                "type_slug": one.type_slug,
                "rows": [_clean(row) for row in one.rows if isinstance(row, dict)],
                **one.extra,
            }
            for one in run.relations
        ],
    }
    if run.tombstones:
        body["tombstones"] = run.tombstones
    source = str(run.manifest.get("source") or "").strip()
    if source:
        body["source"] = source
    # **백필 칸을 그대로 싣는다** — 실행 폴더가 정한다(`bundle.json`). 여기서 떨어뜨리면
    # 그 넷은 API 로 직접 부르는 쪽만 쓸 수 있고, 도구로 넣는 백필은 기본값으로 돈다.
    body.update(bundle_options(run.manifest))
    return body


def bundle_options(manifest: dict[str, Any]) -> dict[str, str]:
    """`bundle.json` 이 정한 묶음 칸 — `backfill: true` 면 넷을 한꺼번에 켠다."""
    out: dict[str, str] = {}
    if manifest.get("backfill"):
        out.update(BACKFILL)
    for name, allowed in BUNDLE_OPTIONS.items():
        value = manifest.get(name)
        if value is None:
            continue
        if str(value) not in allowed:
            raise Stop(
                f"{MANIFEST}: {name} 은 {' · '.join(allowed)} 중 하나입니다 ({value!r})"
            )
        out[name] = str(value)
    return out


def digest(body: dict[str, Any]) -> str:
    """보낼 몸통의 지문. **미리 본 것과 적용하는 것이 같은지** 이것으로 가른다."""
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------
# 검증 — 서버에 보내기 전에 잡을 수 있는 것
# --------------------------------------------------------------------------


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate(run: Run, *, allow_unresolved: bool = False) -> Report:
    report = Report(errors=list(run.problems))
    if run.manifest:
        try:
            bundle_options(run.manifest)
        except Stop as bad:
            report.errors.append(str(bad))
    if run.manifest and run.manifest.get("format") != FORMAT:
        report.errors.append(
            f"{MANIFEST}: format 이 {FORMAT} 가 아닙니다 ({run.manifest.get('format')!r})"
        )

    if run.ontology is not None:
        unknown = sorted(set(run.ontology) - ONTOLOGY_KEYS)
        if unknown:
            report.errors.append(f"{ONTOLOGY}: 모르는 항목 {', '.join(unknown)}")
        for index, one in enumerate(run.ontology.get("types") or [], start=1):
            if not isinstance(one, dict) or not one.get("slug") or not one.get("label"):
                report.errors.append(f"{ONTOLOGY}: types[{index}] 에 slug · label 이 없습니다")

    if run.ontology is None and not run.objects and not run.relations:
        report.errors.append(
            "묶음이 비어 있습니다 — 정의 · 객체 · 관계 중 하나는 있어야 합니다"
        )

    received = bool(str(run.manifest.get("source") or "").strip())
    keys: dict[str, dict[str, str]] = {}
    for batch in run.objects:
        _check_rows(batch, report, max_rows=MAX_ROWS, received=received)
        if not batch.workspace_slug and not received:
            report.warnings.append(
                f"{batch.file}: workspace_slug 이 없어 **전역**으로 들어갑니다"
                " (시스템 관리자만)"
            )
        seen = keys.setdefault(batch.type_slug, {})
        for index, row in enumerate(batch.rows, start=1):
            if not isinstance(row, dict):
                continue
            if not row.get("label") and not row.get("key"):
                report.errors.append(f"{batch.file} {index}행: label 도 key 도 없습니다")
            key = str(row.get("key") or "").strip()
            if key:
                where = f"{batch.file} {index}행"
                if key in seen:
                    report.errors.append(
                        f"{where}: 식별자 {key!r} 가 겹칩니다 (먼저 {seen[key]})"
                    )
                else:
                    seen[key] = where

    for batch in run.relations:
        _check_rows(batch, report, max_rows=MAX_ROWS, received=received)
        no_evidence = 0
        for index, row in enumerate(batch.rows, start=1):
            if not isinstance(row, dict):
                continue
            missing = [name for name in ("src", "relation", "dst") if not row.get(name)]
            if missing:
                report.errors.append(
                    f"{batch.file} {index}행: {', '.join(missing)} 가 없습니다"
                )
            extra = sorted(
                key
                for key in row
                if not str(key).startswith("_") and key not in RELATION_FIELDS
            )
            if extra:
                report.errors.append(
                    f"{batch.file} {index}행: 관계 행에 없는 칸 {', '.join(extra)}"
                    f" (쓸 수 있는 것: {', '.join(RELATION_FIELDS)})"
                )
            if "properties" in row and not isinstance(row["properties"], dict):
                report.errors.append(
                    f"{batch.file} {index}행: properties 는 {{키: 값}} 이어야 합니다"
                )
            if not row.get("evidence_note"):
                no_evidence += 1
        if no_evidence and not received:
            report.warnings.append(
                f"{batch.file}: 근거(evidence_note)가 없는 관계 {no_evidence}줄"
                " — 왜 이었는지 남지 않습니다"
            )

    if run.unresolved:
        message = f"{UNRESOLVED}: 미해결 {len(run.unresolved)}건 — 사람이 판단한 뒤 비우세요"
        (report.warnings if allow_unresolved else report.errors).append(message)
    return report


def _rows(indexes: list[int], limit: int = 5) -> str:
    shown = ", ".join(str(one) for one in indexes[:limit])
    return shown + (f" 외 {len(indexes) - limit}" if len(indexes) > limit else "")


def _check_rows(
    batch: Batch, report: Report, *, max_rows: int, received: bool = False
) -> None:
    if len(batch.rows) > max_rows:
        report.errors.append(
            f"{batch.file}: {len(batch.rows)}행 — 한 파일에 {max_rows}행까지입니다. 나누세요"
        )
    not_dict = sum(1 for row in batch.rows if not isinstance(row, dict))
    if not_dict:
        report.errors.append(f"{batch.file}: 객체({{...}})가 아닌 행 {not_dict}개")
    rows = [row for row in batch.rows if isinstance(row, dict)]
    weak = [
        index
        for index, row in enumerate(rows, start=1)
        if isinstance(row.get("_confidence"), int | float)
        and row["_confidence"] < LOW_CONFIDENCE
    ]
    if weak:
        report.errors.append(
            f"{batch.file}: 확신도 {LOW_CONFIDENCE} 미만인 행 {len(weak)}개({_rows(weak)}행)"
            " — 넣지 말고 unresolved.json 으로 옮기세요"
        )
    if received:
        # 허브에서 받은 행은 원천이 허브다 — 출처 · 인용을 행마다 요구하지 않는다.
        return
    # 문서(쪽 · 슬라이드)에서 뽑은 행은 원문 인용이 있어야 검토하는 사람이 원문을 안 연다.
    unquoted = [
        index
        for index, row in enumerate(rows, start=1)
        if isinstance(row.get("_source"), dict)
        and ("page" in row["_source"] or "slide" in row["_source"])
        and not row["_source"].get("quote")
    ]
    if unquoted:
        report.warnings.append(
            f"{batch.file}: 문서에서 뽑았는데 원문 인용(_source.quote)이 없는 행"
            f" {len(unquoted)}개({_rows(unquoted)}행) — 검토할 때 원문을 열어야 합니다"
        )
    no_source = sum(
        1 for row in batch.rows if isinstance(row, dict) and not row.get("_source")
    )
    if no_source:
        report.warnings.append(
            f"{batch.file}: 출처(_source)가 없는 행 {no_source}개 — 어디서 왔는지 모릅니다"
        )


# --------------------------------------------------------------------------
# 플랫폼에 보내기
# --------------------------------------------------------------------------


class Stop(Exception):
    """사람에게 말하고 멈출 일."""


def _post_bundle(server: str, token: str, body: dict[str, Any]) -> dict[str, Any]:
    """묶음을 보내고 **작업이 끝날 때까지 기다린다.**

    플랫폼은 묶음을 요청 안에서 처리하지 않는다 — 1만 객체에 44초라 클라이언트가 먼저 끊었다.
    202 로 작업이 오고, 여기서 그 작업을 보다가 끝난 결과(옛 응답과 같은 모양)를 돌려준다.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # 감사 기록에 「이 도구가 넣었다」 가 남는다.
        "X-Client": CLIENT,
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    url = f"{server.rstrip('/')}/api/bundles/import"
    status, answer = _send(server, "POST", url, headers, raw, who="플랫폼")
    if status != 202:
        _refused("플랫폼", status, answer)
    job = _wait_job(server, token, answer)
    result = job.get("result")
    if not isinstance(result, dict):
        raise Stop(f"플랫폼 응답을 읽을 수 없습니다: {job!r}")
    # 작업 번호를 함께 — 미리 본 계획을 그 플랫폼 화면 「작업」 에서 열어 적용하는 길이다.
    return {**result, "job_id": str(job.get("id") or "")}


def _send(
    server: str,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    who: str,
) -> tuple[int, Any]:
    try:
        return SEND(method, url, headers, body)
    except (urllib.error.URLError, OSError) as failure:
        # **닿지 않은 것과 거절당한 것을 가른다.** 트레이스백을 보여 주면 사람은 무엇을
        # 고칠지(주소 · 서버가 떠 있나 · 사내망) 모른다.
        reason = getattr(failure, "reason", failure)
        raise Stop(
            f"{who}에 닿지 않습니다: {server} ({reason}) — 주소와 서버가 떠 있는지 확인하세요"
        ) from failure


def _refused(who: str, status: int, answer: Any) -> None:
    error = answer.get("error", {}) if isinstance(answer, dict) else {}
    raise Stop(
        f"{who}이 거절했습니다 ({status}): [{error.get('code', '?')}] "
        f"{error.get('message', answer)}"
    )


def _wait_job(server: str, token: str, job: Any, *, who: str = "플랫폼") -> dict[str, Any]:
    """작업이 끝날 때까지 본다. 실패 · 취소면 그 이유로 멈춘다 — 「끝났다」 로 얼버무리지
    않는다."""
    if not isinstance(job, dict) or not job.get("id"):
        raise Stop(f"{who}이 작업을 돌려주지 않았습니다: {job!r}")
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    url = f"{server.rstrip('/')}/api/jobs/{job['id']}"
    shown = ""
    queued_for = 0.0
    while True:
        status, current = _send(server, "GET", url, headers, None, who=who)
        if status != 200:
            _refused(who, status, current)
        if not isinstance(current, dict):
            raise Stop(f"{who} 응답을 읽을 수 없습니다: {current!r}")
        state = str(current.get("status"))
        progress = current.get("progress") or {}
        line = (
            f"{progress.get('stage', '')} {progress.get('done', 0)}/{progress.get('total', 0)}"
        )
        if line != shown and state == "running":
            print(f"  … {line}", file=sys.stderr)
            shown = line
        if state == "done":
            return current
        if state in ("failed", "cancelled"):
            raise Stop(
                f"{who}의 작업이 {'실패했습니다' if state == 'failed' else '취소됐습니다'}: "
                f"{current.get('error') or ''}"
            )
        queued_for = queued_for + POLL_SECONDS if state == "queued" else 0.0
        if queued_for >= QUEUED_LIMIT_SECONDS:
            raise Stop(
                f"{who}의 작업(<{job['id']}>)을 {int(queued_for)}초 동안 아무도 집어 가지 "
                "않았습니다 — 작업 워커가 꺼져 있는 것 같습니다. 운영자에게 "
                "'systemctl status <slug>-worker' 를 확인해 달라고 하세요. "
                "묶음은 서버에 남아 있으니 워커가 살아나면 그대로 이어집니다."
            )
        WAIT(POLL_SECONDS)


def _get_json(server: str, token: str, path: str, *, who: str = "허브") -> Any:
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    status, answer = _send(
        server, "GET", f"{server.rstrip('/')}{path}", headers, None, who=who
    )
    if status != 200:
        _refused(who, status, answer)
    return answer


#: 상태를 **묻기만** 할 때 기다리는 시간 — 적재(600초)만큼 매달리면 작업 상태 하나 보는 데 몇
#: 분이 걸린다. 못 닿으면 「모른다」 로 넘어간다.
PROBE_SECONDS = 5.0


def plan_applied(server: str, token: str, job_id: str) -> bool | None:
    """그 계획을 **화면(또는 어디서든) 적용했나** — 플랫폼의 `applied_by` 로. 못 닿으면 None.

    키트가 미리 본 계획을 사람은 그 플랫폼 화면 「작업」 에서 적용한다. 키트는 그것을
    모르므로, 안 물으면 작업 상태가 계속 「적용 전 — 적용하라」 고 조르고, 사람은 같은 것을
    또 넣으려 한다.
    """
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    url = f"{server.rstrip('/')}/api/jobs/{job_id}"
    try:
        if SEND is _urllib_send:
            status, answer = _urllib_send("GET", url, headers, None, timeout=PROBE_SECONDS)
        else:
            status, answer = SEND("GET", url, headers, None)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if status != 200 or not isinstance(answer, dict):
        return None
    return bool(answer.get("applied_by"))


def jobs_link(server: str, job_id: str) -> str:
    """그 플랫폼 화면의 「작업」 — **이 계획을 펼친 채로** 연다(`?job=`).

    적용은 사람이 거기서 「적용」 을 누른다. 명령 창보다 쉽고, **그 플랫폼의 화면에서** 누르니
    엉뚱한 곳에 넣을 일이 없다. 계획을 본 사람(이 토큰의 주인)만 적용할 수 있다.
    """
    return f"{server.rstrip('/')}/jobs?job={job_id}"


def command_line(*args: str) -> str:
    """사람이 명령 창에 붙여 넣는 이 도구의 명령 — **지금 이것을 돌리는 파이썬으로.**

    PC 에 파이썬이 없을 수 있다 — Windows 키트는 제 파이썬(`python/`)을 들고 다니고 PATH 에
    안 올린다. `python …` 으로 주면 그 PC 에서는 「python 을 찾을 수 없다」 로 멈춘다.
    `-X utf8` 은 MCP 서버의 PYTHONUTF8 과 같은 것 — 명령 창에는 그 환경 변수가 없다.
    """
    return f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" ' + " ".join(args)


def platform_schema(server: str, token: str, types: list[str] | None = None) -> dict[str, Any]:
    """플랫폼의 **지금 정의** — 정의 초안을 잡기 전에 읽는다(무엇이 이미 있나).

    **키트가 직접 읽는다** — 그래서 정제만 하는 사람은 서버 MCP 를 따로 붙이지 않아도 된다
    (Node.js · 설정 JSON 손편집이 빠진다). `types` 를 주면 그 타입들과, 그 타입에 닿는 관계
    종류만 — 타입이 백 개면 전부는 길다.
    """
    schema = _get_json(server, token, "/api/ontology/schema", who="플랫폼")
    if not isinstance(schema, dict) or not types:
        return schema if isinstance(schema, dict) else {}
    wanted = set(types)
    picked = [one for one in schema.get("types") or [] if one.get("slug") in wanted]
    missing = sorted(wanted - {str(one.get("slug")) for one in picked})
    relations = [
        one
        for one in schema.get("relation_types") or []
        if wanted & set(one.get("src_type_slugs") or [])
        | wanted & set(one.get("dst_type_slugs") or [])
    ]
    out = {
        "groups": schema.get("groups") or [],
        "types": picked,
        "relation_types": relations,
        "all_type_slugs": [one.get("slug") for one in schema.get("types") or []],
    }
    if missing:
        out["missing"] = missing
    return out


def _export_bundle(hub: str, hub_token: str, groups: list[str]) -> Any:
    """허브의 내보내기도 작업이다 — 넣고, 기다리고, 결과 파일을 받는다.

    **묶음을 여럿 받는다.** 코어를 축별로 나눠 둔 설치에서 하나씩 받으면, 축끼리 가리키는
    참조 때문에 어느 쪽도 못 내보낸다(허브가 거절한다).
    """
    headers = {
        "Authorization": f"Bearer {hub_token}",
        "Content-Type": "application/json",
        "X-Client": CLIENT,
    }
    raw = json.dumps({"groups": groups}, ensure_ascii=False).encode("utf-8")
    status, answer = _send(
        hub, "POST", f"{hub.rstrip('/')}/api/bundles/export", headers, raw, who="허브"
    )
    if status != 202:
        _refused("허브", status, answer)
    job = _wait_job(hub, hub_token, answer, who="허브")
    return _get_json(hub, hub_token, f"/api/jobs/{job['id']}/download")


def resolve_names(
    server: str, token: str, type_slug: str, names: list[str]
) -> dict[str, dict[str, Any]] | None:
    """이름 여럿을 **플랫폼의 판정으로** 푼다 — `POST /objects/{타입}/resolve-many`.

    도구가 스스로 맞추지 않는 이유: 식별자 → 별칭 → 이름 순서와 겹침 규칙은 플랫폼의 것이고,
    두 벌로 두면 「도구는 된다는데 넣으면 안 되는」 상태가 생긴다.
    """
    out: dict[str, dict[str, Any]] = {}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Client": CLIENT,
    }
    unique = list(dict.fromkeys(one for one in names if one))
    url = f"{server.rstrip('/')}/api/objects/{urllib.parse.quote(type_slug)}/resolve-many"
    for at in range(0, len(unique), 500):
        raw = json.dumps({"names": unique[at : at + 500]}, ensure_ascii=False).encode("utf-8")
        status, body = _send(server, "POST", url, headers, raw, who="플랫폼")
        if status == 404:
            # **그 타입이 플랫폼에 아직 없다.** 같은 묶음의 정의가 만들 것일 수 있으므로
            # 여기서 멈추지 않는다 — 부르는 쪽이 판단한다.
            return None
        if status != 200:
            _refused("플랫폼", status, body)
        for one in (body or {}).get("items") or []:
            out[str(one.get("name"))] = one
    return out


def fetch_keys(server: str, token: str, type_slug: str) -> set[str]:
    """플랫폼에서 그 타입의 식별자를 전부 받는다(쪽마다) — 원천의 코드가 코어에 붙나를
    볼 때."""
    keys: set[str] = set()
    offset = 0
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    while True:
        url = (
            f"{server.rstrip('/')}/api/objects/{urllib.parse.quote(type_slug)}"
            f"?limit=500&offset={offset}"
        )
        try:
            status, body = SEND("GET", url, headers, None)
        except (urllib.error.URLError, OSError) as failure:
            reason = getattr(failure, "reason", failure)
            raise Stop(
                f"플랫폼에 닿지 않습니다: {server} ({reason}) — "
                "주소(SP_SERVER)와 서버가 떠 있는지 확인하세요"
            ) from failure
        if status != 200 or not isinstance(body, dict):
            error = body.get("error", {}) if isinstance(body, dict) else {}
            raise Stop(
                f"코어 식별자를 받지 못했습니다({type_slug}, {status}): "
                f"{error.get('message', body)}"
            )
        items = body.get("items") or []
        keys.update(str(one["key"]) for one in items if one.get("key"))
        offset += len(items)
        if not items or offset >= int(body.get("total") or 0):
            return keys


def read_keys_file(path: Path) -> set[str]:
    """식별자 파일 — 한 줄에 하나. 허브에 닿지 않는 PC 에서 코어 식별자를 옮겨 올 때."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as failure:
        raise Stop(f"식별자 파일을 읽을 수 없습니다: {path} ({failure})") from failure
    return {line.strip() for line in lines if line.strip()}


def summarize(result: dict[str, Any], *, limit: int = 20) -> str:
    """사람이 읽는 요약 — 오류는 **행 번호와 이유**까지."""
    lines: list[str] = []
    state = (
        "적용함" if result.get("applied") else ("괜찮음" if result.get("ok") else "오류 있음")
    )
    lines.append(f"결과: {state}")
    ontology = result.get("ontology")
    if ontology:
        changed = [c for c in ontology.get("changes", []) if c.get("action") != "unchanged"]
        lines.append(f"정의: 바뀌는 것 {len(changed)}건")
        for change in changed[:limit]:
            lines.append(f"  {change.get('action')} {change.get('kind')} {change.get('slug')}")
        for warning in ontology.get("warnings", []):
            lines.append(f"  경고: {warning}")
        for error in ontology.get("errors", []):
            lines.append(f"  오류: {error}")
    for name, label in (("objects", "객체"), ("relations", "관계")):
        for batch in result.get(name, []):
            plan = batch.get("plan") or {}
            counts = " · ".join(
                f"{k} {v}" for k, v in sorted((plan.get("counts") or {}).items())
            )
            lines.append(f"{label} {batch.get('type_slug')}: {counts or '-'}")
            if batch.get("error"):
                lines.append(f"  오류: {batch['error']}")
            for error in plan.get("errors", []):
                lines.append(f"  오류: {error}")
            bad = [row for row in plan.get("rows", []) if row.get("action") == "error"]
            for row in bad[:limit]:
                lines.append(
                    f"  {row.get('row')}행 {row.get('label') or ''}: {row.get('message')}"
                )
            if len(bad) > limit:
                lines.append(f"  … 외 {len(bad) - limit}행")
            # **끊을 선은 따로 센다** — 수에만 섞어 두면 「무엇이 끊기나」 를 보려고 사람이
            # 표를 펴야 하는데, 그 자리에서 사람이 보는 것은 이 요약뿐이다.
            cut = [row for row in plan.get("rows", []) if row.get("action") == "unlink"]
            for row in cut[:limit]:
                lines.append(f"  끊음: {row.get('label') or ''} — {row.get('message') or ''}")
            if len(cut) > limit:
                lines.append(f"  … 외 {len(cut) - limit}줄 끊음")
            # 오류가 아닌 **말**(별칭이 빠졌다 · 같은 파일 앞줄이 먼저 쓴다 …)도 보인다.
            notes = [
                row
                for row in plan.get("rows", [])
                if row.get("message") and row.get("action") not in ("error", "unlink")
            ]
            for row in notes[:limit]:
                where = f"{row.get('row')}행 {row.get('label') or ''}"
                lines.append(f"  알림: {where}: {row.get('message')}")
            if len(notes) > limit:
                lines.append(f"  … 외 {len(notes) - limit}행 알림")
    graves = result.get("tombstones") or {}
    if graves.get("rows"):
        counts = " · ".join(
            f"{k} {v}" for k, v in sorted((graves.get("counts") or {}).items())
        )
        lines.append(f"사라진 것: {counts or '-'}")
        for row in graves["rows"][:limit]:
            lines.append(
                f"  {row.get('action')} {row.get('label') or ''}: {row.get('message') or ''}"
            )
        if len(graves["rows"]) > limit:
            lines.append(f"  … 외 {len(graves['rows']) - limit}줄")
    for error in result.get("errors", []):
        lines.append(f"오류: {error}")
    return "\n".join(lines)


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write(path: Path, body: Any) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# 명령
# --------------------------------------------------------------------------


def cmd_init(path: Path, *, title: str = "", backfill: bool = False) -> str:
    if (path / MANIFEST).exists():
        raise Stop(f"이미 실행 폴더입니다: {path} — 새 실행은 새 폴더로")
    (path / OBJECTS_DIR).mkdir(parents=True, exist_ok=True)
    (path / RELATIONS_DIR).mkdir(parents=True, exist_ok=True)
    _write(
        path / MANIFEST,
        {
            "format": FORMAT,
            "title": title,
            "created_at": _stamp(),
            "sources": [],
            "objects_order": [],
            "relations_order": [],
            "notes": "",
            # **대량 적재인가.** 켜면 넷이 함께 켜진다(`BACKFILL`) — 미리 보기는 계획만,
            # 웹훅 · 감사는 묶음 한 건, 못 찾은 참조는 그 칸만 비운다.
            "backfill": backfill,
        },
    )
    _write(path / ONTOLOGY, {"groups": [], "types": [], "relation_types": []})
    _write(path / UNRESOLVED, [])
    return f"실행 폴더를 만들었습니다: {path}"


def cmd_pull(
    path: Path,
    *,
    hub: str,
    hub_token: str,
    group: str | list[str],
    source: str = "hub",
) -> str:
    """허브가 내보낸 묶음을 **새 실행 폴더로** 받는다(사이드바 묶음 여럿도 한 번에).

    받은 것도 여느 실행과 같다 — `validate` → `preview`(받는 플랫폼) → 사람이 확인 → `apply`.
    `bundle.json` 의 `source` 가 실려 가므로, 받는 플랫폼은 그 타입들을 허브 관리로 둔다.

    **허브가 적어 보낸 것을 떨어뜨리지 않는다** — 별칭·관계의 「맞춤」(`aliases_mode` ·
    `mode`)과 사라진 것(`tombstones`). 떨어뜨리면 허브에서 뺀 별칭 · 끊은 선 · 지운 객체가
    받는 쪽에 남고, 두 설치는 그때부터 조용히 갈린다.
    """
    wanted = [group] if isinstance(group, str) else list(group)
    groups = [one.strip() for one in wanted if one and one.strip()]
    if not groups:
        raise Stop("받을 사이드바 묶음(--group)이 필요합니다 — PLM 기준정보면 plm")
    body = _export_bundle(hub, hub_token, groups)
    if not isinstance(body, dict) or body.get("format") != FORMAT:
        raise Stop(f"허브의 응답이 묶음이 아닙니다: {str(body)[:200]}")
    cmd_init(path, title=f"허브에서 받기 — {', '.join(groups)}")
    manifest = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    manifest["source"] = source
    manifest["sources"] = [
        {
            "name": hub,
            "group": ",".join(groups),
            "exported_at": body.get("exported_at"),
            "counts": body.get("counts") or {},
        }
    ]
    batches = body.get("objects") or []
    manifest["objects_order"] = list(dict.fromkeys(str(one["type_slug"]) for one in batches))
    manifest["notes"] = "\n".join(str(one) for one in body.get("warnings") or [])
    _write(path / MANIFEST, manifest)
    _write(path / ONTOLOGY, body.get("ontology") or {})
    for index, batch in enumerate(batches, start=1):
        _write(path / OBJECTS_DIR / f"{index:03d}-{batch['type_slug']}.json", batch)
    for index, batch in enumerate(body.get("relations") or [], start=1):
        _write(path / RELATIONS_DIR / f"{index:03d}-{batch['type_slug']}.json", batch)
    graves = body.get("tombstones") or {}
    if graves.get("objects") or graves.get("relations"):
        _write(path / TOMBSTONES, graves)
    counts = body.get("counts") or {}
    lines = [
        f"받았습니다: {path}",
        f"허브 {hub} · 묶음 {', '.join(groups)} · 내보낸 때 {body.get('exported_at')}",
        (
            "타입 {types} · 관계 종류 {relation_types} · 객체 {objects} · 관계 {relations}"
            " · 사라진 것 {tombstones}"
        ).format(
            **{
                key: counts.get(key, 0)
                for key in ("types", "relation_types", "objects", "relations", "tombstones")
            }
        ),
        *[f"경고: {one}" for one in body.get("warnings") or []],
        f"다음: python sp_pipeline.py validate {path}"
        " → preview → 확인 → apply (받는 플랫폼에)",
    ]
    return "\n".join(lines)


def check_endpoints(run: Run, server: str, token: str) -> Report:
    """**끝점이 풀리나** — 플랫폼에 물어 미리 본다(검증의 선택 단계).

    예전에는 이것을 `preview` 에서야 알았다. 미리 보기는 계획을 세우느라 몇 분이 걸리고,
    그 몇 분 뒤에 「이름이 여럿과 맞는다」 를 듣는다. 물어서 아는 것은 먼저 묻는다.

    보는 것 넷:
    - 관계의 출발점(`src`)이 그 타입에서 **하나로 정해지나**
    - 관계의 **도착점(`dst`)** 도 — 관계 종류가 허락한 타입에서(여럿이면 그 중 하나에서).
      예전에는 출발점만 봐서, 도착점이 없는 파일이 미리 보기까지 가서야 막혔다
    - 객체 행이 가리키는 참조 칸(실행 폴더의 `ontology.json` 이 말하는 `object_ref`)이 풀리나
    - **별칭으로 풀린 것**은 경고로 — 다른 객체의 이름과 같은 별칭이 있으면 조용히 그쪽에
      붙는다(그 사실은 아무 데도 안 적힌다)
    """
    report = Report()
    # 이 실행의 정의가 만드는 타입 · 인터페이스 — 플랫폼에 아직 없어도 된다.
    defined: set[str] = {
        str(one.get("slug"))
        for key in ("types", "interfaces")
        for one in (((run.ontology or {}).get(key) or []) if run.ontology else [])
        if isinstance(one, dict)
    }
    ref_of: dict[str, dict[str, str]] = {}
    for one in ((run.ontology or {}).get("types") or []) if run.ontology else []:
        if not isinstance(one, dict):
            continue
        fields = {
            str(prop.get("key")): str(prop.get("ref_type_slug") or "")
            for prop in (one.get("properties") or [])
            if isinstance(prop, dict) and prop.get("data_type") == "object_ref"
        }
        if fields:
            ref_of[str(one.get("slug"))] = fields

    # 관계 종류 → 도착 타입들. 실행 폴더의 정의가 먼저고, 없으면 플랫폼에 묻는다.
    dst_types: dict[str, list[str]] = {
        str(one.get("slug")): [str(x) for x in (one.get("dst_type_slugs") or [])]
        for one in (((run.ontology or {}).get("relation_types") or []) if run.ontology else [])
        if isinstance(one, dict)
    }
    missing_kinds = {
        str(row.get("relation"))
        for batch in run.relations
        for row in batch.rows
        if isinstance(row, dict)
        and row.get("relation")
        and row.get("relation") not in dst_types
    }
    if missing_kinds:
        for one in _get_json(server, token, "/api/ontology/relation-types") or []:
            if isinstance(one, dict) and str(one.get("slug")) in missing_kinds:
                dst_types[str(one["slug"])] = [
                    str(x) for x in (one.get("dst_type_slugs") or [])
                ]

    asked: dict[str, set[str]] = {}
    # 도착점은 **여러 타입 중 하나**에 있으면 된다 — 그 판정은 아래에서 따로 한다.
    either: list[tuple[str, list[str]]] = []
    for batch in run.relations:
        for row in batch.rows:
            if not isinstance(row, dict):
                continue
            if row.get("src"):
                asked.setdefault(batch.type_slug, set()).add(str(row["src"]))
            ends = dst_types.get(str(row.get("relation") or ""))
            if row.get("dst") and ends:
                if len(ends) == 1:
                    asked.setdefault(ends[0], set()).add(str(row["dst"]))
                else:
                    either.append((str(row["dst"]), ends))
    for batch in run.objects:
        for key, target in (ref_of.get(batch.type_slug) or {}).items():
            if not target:
                continue
            for row in batch.rows:
                if not isinstance(row, dict):
                    continue
                value = row.get(key)
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, str) and item.strip():
                        asked.setdefault(target, set()).add(item.strip())
    if not asked:
        return report

    # **이 실행이 만드는 것은 아직 없어도 된다** — 같은 묶음의 객체 단계가 넣는다.
    making: dict[str, set[str]] = {}
    for batch in run.objects:
        for row in batch.rows:
            if not isinstance(row, dict):
                continue
            for name in ("key", "label"):
                if row.get(name):
                    making.setdefault(batch.type_slug, set()).add(str(row[name]))

    for text, ends in either:
        for one in ends:
            asked.setdefault(one, set()).add(text)

    # 끝에 **인터페이스**가 적혔으면 그것을 구현한 타입이 만드는 것도 「이 실행이 만드는 것」
    # 이다. 구현은 실행 폴더의 정의가 먼저고, 플랫폼에 이미 있는 것은 물어서 안다.
    members: dict[str, set[str]] = {}
    for one in ((run.ontology or {}).get("types") or []) if run.ontology else []:
        if isinstance(one, dict):
            for name in one.get("interface_slugs") or []:
                members.setdefault(str(name), set()).add(str(one.get("slug")))
    if set(asked) - defined - {batch.type_slug for batch in run.objects}:
        with contextlib.suppress(Stop):
            for one in _get_json(server, token, "/api/ontology/interfaces") or []:
                if isinstance(one, dict):
                    members.setdefault(str(one.get("slug")), set()).update(
                        str(x) for x in one.get("implementers") or []
                    )

    settled: dict[str, set[str]] = {}
    for type_slug, names in sorted(asked.items()):
        made_here = set(making.get(type_slug) or set())
        for member in members.get(type_slug) or ():
            made_here |= making.get(member) or set()
        left = sorted(names - made_here)
        if not left:
            continue
        found = resolve_names(server, token, type_slug, left)
        if found is None:
            where = report.warnings if type_slug in defined else report.errors
            where.append(
                f"{type_slug}: 이 타입이 플랫폼에 아직 없어 끝점을 못 봤습니다 "
                f"({len(left)}건) — 이 묶음의 정의가 만드는 타입이면 정상입니다"
            )
            continue
        missing = [one for one in left if (found.get(one) or {}).get("match") == "none"]
        several = [one for one in left if (found.get(one) or {}).get("match") == "candidates"]
        by_alias = [
            one
            for one in left
            if ((found.get(one) or {}).get("object") or {}).get("matched_by") == "alias"
        ]
        settled[type_slug] = {
            one for one in left if (found.get(one) or {}).get("match") == "exact"
        }
        # 도착 타입이 여럿인 관계는 **그 중 하나**에서 풀리면 된다 — 나머지에서 「없다」 가
        # 나오는 것은 당연하므로 여기서 빼고 아래에서 함께 본다.
        forgiving = {one for one, ends in either if type_slug in ends}
        missing = [one for one in missing if one not in forgiving]
        several = [one for one in several if one not in forgiving]
        if missing:
            report.errors.append(
                f"{type_slug}: 가리키는 것이 플랫폼에 없습니다 {len(missing)}건 — "
                + ", ".join(missing[:10])
                + (" …" if len(missing) > 10 else "")
            )
        if several:
            report.errors.append(
                f"{type_slug}: 이름이 여럿과 맞습니다 {len(several)}건(그때 플랫폼은 "
                "고르지 않는다) — " + ", ".join(several[:10])
            )
        if by_alias:
            report.warnings.append(
                f"{type_slug}: 별칭으로 풀린 것 {len(by_alias)}건 — 다른 객체의 이름과 같은 "
                "별칭이면 그쪽에 붙습니다: " + ", ".join(by_alias[:10])
            )

    # 도착 타입이 여럿인 줄 — **어느 타입에서도** 안 풀린 것만 오류다.
    homeless = sorted(
        {
            text
            for text, ends in either
            if not any(
                text in settled.get(one, set()) or text in (making.get(one) or set())
                for one in ends
            )
        }
    )
    if homeless:
        report.errors.append(
            f"관계의 도착점을 플랫폼에서 찾지 못했습니다 {len(homeless)}건 — "
            + ", ".join(homeless[:10])
            + (" …" if len(homeless) > 10 else "")
        )
    return report


def cmd_validate(
    path: Path,
    *,
    allow_unresolved: bool = False,
    server: str = "",
    token: str = "",
) -> tuple[bool, str]:
    run = load(path)
    report = validate(run, allow_unresolved=allow_unresolved)
    asked = ""
    if server and token:
        remote = check_endpoints(run, server, token)
        report.errors.extend(remote.errors)
        report.warnings.extend(remote.warnings)
        asked = " · 끝점 확인함"
    lines = [f"오류: {one}" for one in report.errors] + [
        f"경고: {one}" for one in report.warnings
    ]
    total = sum(len(one.rows) for one in run.objects)
    links = sum(len(one.rows) for one in run.relations)
    head = (
        f"정의 {'있음' if run.ontology else '없음'} · 객체 {total}행({len(run.objects)}파일)"
        f" · 관계 {links}줄{asked} · 오류 {len(report.errors)} · 경고 {len(report.warnings)}"
    )
    return not report.errors, "\n".join([head, *lines])


def preview_platform(run: Path) -> str:
    """미리 본 플랫폼의 이름 — **적용은 미리 본 곳으로만** 간다."""
    seen = _read_json(run / PREVIEW, [])
    return str(seen.get("platform") or "") if isinstance(seen, dict) else ""


def cmd_preview(
    path: Path, *, server: str, token: str, platform: str = ""
) -> tuple[bool, str]:
    run = load(path)
    report = validate(run)
    if report.errors:
        raise Stop(
            "검증을 먼저 통과하세요:\n" + "\n".join(f"오류: {e}" for e in report.errors)
        )
    body = payload(run)
    result = _post_bundle(server, token, {**body, "apply": False})
    _write(
        path / PREVIEW,
        {
            "at": _stamp(),
            "server": server,
            "platform": platform,
            "digest": digest(body),
            "result": result,
        },
    )
    return bool(result.get("ok")), summarize(result)


def cmd_apply(path: Path, *, server: str, token: str) -> tuple[bool, str]:
    run = load(path)
    report = validate(run)
    if report.errors:
        raise Stop(
            "검증을 먼저 통과하세요:\n" + "\n".join(f"오류: {e}" for e in report.errors)
        )
    body = payload(run)
    seen = _read_json(path / PREVIEW, [])
    # **미리 본 것만 넣는다.** 미리 본 뒤 파일을 고쳤으면 사람이 본 적 없는 것이 들어간다.
    if not isinstance(seen, dict):
        raise Stop("먼저 preview 로 미리 보고 확인하세요")
    if seen.get("digest") != digest(body):
        raise Stop("미리 본 뒤에 묶음이 바뀌었습니다 — preview 를 다시 하고 확인하세요")
    if seen.get("server") != server:
        raise Stop(
            f"미리 본 서버({seen.get('server')})와 다른 서버입니다 — preview 를 다시 하세요"
        )
    if not (seen.get("result") or {}).get("ok"):
        raise Stop("미리 보기에서 오류가 있었습니다 — 고치고 preview 를 다시 하세요")
    result = _post_bundle(server, token, {**body, "apply": True})
    _write(
        path / APPLIED,
        {"at": _stamp(), "server": server, "digest": digest(body), "result": result},
    )
    return bool(result.get("applied")), summarize(result)


def cmd_runs(*, server: str, token: str, limit: int = 20) -> tuple[bool, str]:
    """넣은 판들 — **되돌릴 번호를 여기서 찾는다.**"""
    headers = {"Authorization": f"Bearer {token}", "X-Client": CLIENT}
    url = f"{server.rstrip('/')}/api/bundles/runs?limit={max(1, min(limit, 100))}"
    status, answer = _send(server, "GET", url, headers, None, who="플랫폼")
    if status != 200:
        _refused("플랫폼", status, answer)
    if not isinstance(answer, list) or not answer:
        return True, "넣은 판이 없습니다."
    lines = ["판 번호                               넣은 때        누가      무엇"]
    for one in answer:
        at = str(one.get("at") or "")[:16].replace("T", " ")
        mark = "" if one.get("undoable") else "  (되돌릴 수 없음)"
        lines.append(
            f"{one.get('id')}  {at}  {str(one.get('actor') or '')[:8]:8}  "
            f"{str(one.get('label') or '')[:40]}{mark}"
        )
    return True, "\n".join(lines)


def cmd_undo(run_id: str, *, server: str, token: str, apply: bool = False) -> tuple[bool, str]:
    """그 판을 되돌린다 — 기본은 계획이다(아무것도 안 바뀐다).

    **계획을 사람이 읽은 뒤** `--apply` 로 되돌린다. 그 사이 남이 고친 줄은 되돌리지 않고
    이유가 줄에 적혀 온다 — 남의 변경을 조용히 덮지 않는다.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Client": CLIENT,
    }
    url = (
        f"{server.rstrip('/')}/api/bundles/runs/{run_id}/undo"
        f"?apply={'true' if apply else 'false'}"
    )
    status, answer = _send(server, "POST", url, headers, b"", who="플랫폼")
    if status != 202:
        _refused("플랫폼", status, answer)
    job = _wait_job(server, token, answer)
    result = job.get("result")
    if not isinstance(result, dict):
        raise Stop(f"플랫폼 응답을 읽을 수 없습니다: {job!r}")
    counts = result.get("counts") or {}
    did = "되돌렸습니다" if result.get("applied") else "계획입니다 — 아무것도 안 바뀌었습니다"
    head = (
        f"{did}"
        f" · 지움 {counts.get('delete', 0)} · 되돌림 {counts.get('update', 0)}"
        f" · 다시 이음 {counts.get('create', 0)} · 건너뜀 {counts.get('unchanged', 0)}"
        f" · 오류 {counts.get('error', 0)}"
    )
    skipped = [
        f"  건너뜀: {one.get('label')} — {one.get('message')}"
        for one in (result.get("rows") or [])
        if one.get("action") == "unchanged" and one.get("message")
    ][:20]
    errors = [f"  오류: {one}" for one in (result.get("errors") or [])]
    return bool(result.get("ok")), "\n".join([head, *errors, *skipped])


def _chosen(args: argparse.Namespace, platform: str) -> Target:
    """명령 줄의 `--server` 가 있으면 그것(등록 없이 한 번만), 아니면 이름으로 고른다."""
    if args.server:
        token = args.token or os.environ.get("SP_TOKEN", "").strip()
        if not token:
            raise Stop("--server 를 줬으면 --token 도 줍니다(또는 SP_TOKEN)")
        return Target("", args.server, token)
    return target(args.platform or platform)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sp_pipeline", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="빈 실행 폴더를 만든다")
    init.add_argument("run", type=Path)
    init.add_argument("--title", default="")
    init.add_argument(
        "--backfill",
        action="store_true",
        help="대량 적재 — 미리 보기는 계획만 · 웹훅과 감사는 묶음 한 건 · 못 찾은 참조는 비움",
    )

    check = sub.add_parser("validate", help="서버에 보내기 전에 모양을 본다")
    check.add_argument("run", type=Path)
    check.add_argument("--allow-unresolved", action="store_true")
    # **주소와 토큰이 있으면 끝점을 미리 묻는다.** 없으면 모양만 본다(예전과 같다).
    check.add_argument(
        "--platform", default="", help="등록한 플랫폼 이름(작업 폴더의 것이 기본)"
    )
    check.add_argument("--server", default="", help="한 번만 다른 곳 — 등록 없이")
    check.add_argument("--token", default="")

    pull = sub.add_parser("pull", help="허브가 내보낸 묶음을 새 실행 폴더로 받는다")
    pull.add_argument("run", type=Path)
    pull.add_argument(
        "--group",
        required=True,
        action="append",
        help="사이드바 묶음 slug — PLM 기준정보면 plm. **여러 번 적어도 된다**",
    )
    pull.add_argument("--hub", default=setting("SP_HUB_SERVER"))
    pull.add_argument("--hub-token", default=setting("SP_HUB_TOKEN"))
    pull.add_argument("--source", default="hub")

    runs = sub.add_parser("runs", help="넣은 판들 — 되돌릴 번호를 찾는다")
    runs.add_argument("--platform", default="", help="등록한 플랫폼 이름(하나뿐이면 생략)")
    runs.add_argument("--server", default="")
    runs.add_argument("--token", default="")
    runs.add_argument("--limit", type=int, default=20)

    undo = sub.add_parser("undo", help="넣은 판 하나를 통째로 되돌린다(기본은 계획)")
    undo.add_argument("run_id", help="`runs` 가 보여 준 판 번호")
    undo.add_argument("--platform", default="", help="등록한 플랫폼 이름(하나뿐이면 생략)")
    undo.add_argument("--server", default="")
    undo.add_argument("--token", default="")
    undo.add_argument(
        "--apply", action="store_true", help="계획을 읽은 뒤 — 이것 없이는 아무것도 안 바뀐다"
    )

    for name, what in (
        ("preview", "아무것도 저장하지 않고 미리 본다"),
        ("apply", "미리 본 것을 넣는다"),
    ):
        one = sub.add_parser(name, help=what)
        one.add_argument("run", type=Path)
        one.add_argument(
            "--platform",
            default="",
            help="등록한 플랫폼 이름 — 미리 보기는 작업 폴더의 것, 적용은 미리 본 곳이 기본",
        )
        one.add_argument("--server", default="", help="한 번만 다른 곳 — 등록 없이")
        one.add_argument("--token", default="")
        one.add_argument(
            "--backfill",
            action="store_true",
            help="이 실행을 대량 적재로 — `bundle.json` 에 적어 둔다(다음부터도 그렇게 간다)",
        )

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(cmd_init(args.run, title=args.title, backfill=args.backfill))
            return 0
        if args.command == "validate":
            # 플랫폼이 정해지면 끝점까지 묻고, 아니면 모양만 본다 — 그 사실을 말한다.
            asked: Target | None = None
            note = ""
            try:
                asked = _chosen(args, work_platform(args.run))
            except Stop as why:
                note = f"\n(끝점은 묻지 않았습니다 — {why})"
            ok, text = cmd_validate(
                args.run,
                allow_unresolved=args.allow_unresolved,
                server=asked.server if asked else "",
                token=asked.token if asked else "",
            )
            print(text + note)
            return 0 if ok else 1
        if args.command == "pull":
            if not args.hub or not args.hub_token:
                raise Stop(
                    "허브 주소와 토큰이 필요합니다 — SP_HUB_SERVER · SP_HUB_TOKEN 또는 "
                    "--hub · --hub-token"
                )
            print(
                cmd_pull(
                    args.run,
                    hub=args.hub,
                    hub_token=args.hub_token,
                    group=args.group,
                    source=args.source,
                )
            )
            return 0
        if args.command == "runs":
            chosen = _chosen(args, "")
            print(f"플랫폼: {chosen.shown}")
            ok, text = cmd_runs(server=chosen.server, token=chosen.token, limit=args.limit)
            print(text)
            return 0 if ok else 1
        if args.command == "undo":
            chosen = _chosen(args, "")
            print(f"플랫폼: {chosen.shown}")
            ok, text = cmd_undo(
                args.run_id, server=chosen.server, token=chosen.token, apply=args.apply
            )
            print(text)
            return 0 if ok else 1
        if args.backfill:
            # **실행 폴더에 적어 둔다** — 미리 보기와 적용이 같은 칸으로 가야 한다(미리 본
            # 것과 넣는 것이 다르면 미리 본 뜻이 없다).
            mark = args.run / MANIFEST
            body = _read_json(mark, [])
            if not isinstance(body, dict):
                raise Stop(f"{MANIFEST} 을 읽을 수 없습니다: {args.run}")
            body["backfill"] = True
            _write(mark, body)
        if args.command == "preview":
            chosen = _chosen(args, work_platform(args.run))
            print(f"미리 보는 곳: {chosen.shown}")
            ok, text = cmd_preview(
                args.run, server=chosen.server, token=chosen.token, platform=chosen.name
            )
        else:
            # **적용은 미리 본 곳으로.** 그 이름을 실행 폴더가 기억한다.
            chosen = _chosen(args, preview_platform(args.run) or work_platform(args.run))
            print(f"넣는 곳: {chosen.shown}")
            ok, text = cmd_apply(args.run, server=chosen.server, token=chosen.token)
        print(text)
        return 0 if ok else 1
    except Stop as stop:
        print(str(stop), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
