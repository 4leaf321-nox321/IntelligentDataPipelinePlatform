"""작업의 종류 — **이름 하나가 처리 함수 하나다.**

새 종류는 여기 `register` 한 줄이다. 라우터 · 워커 · 화면은 종류를 모른다 — `needs_file` 과
`two_step` 만 보고, 나머지는 `run(work)` 가 한다. 결과(`dict`)는 그대로 `jobs.result` 에
들어가 화면이 그린다.

틀(StandardPlatform)에서 온 것은 일반 장치(`Work` · `Kind` · 등록부)와 도메인을
모르는 두 종류(고아 첨부 정리 · 부서 자료 이동)뿐이다. 객체 · 묶음 · 지표의 종류는
온톨로지와 함께 걷어냈다(ADR 0001). 파이프라인 실행의 종류는 `relay` 모듈이 여기에
`register` 한다.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, BinaryIO

from sqlalchemy.orm import Session

from app.modules.accounts.models import User
from app.modules.jobs import files
from app.modules.jobs.models import Job, JobFile
from app.shared.errors import Conflict, Forbidden, code

log = logging.getLogger(__name__)


@dataclass
class Work:
    db: Session
    """본 트랜잭션. 처리 함수는 커밋하지 않는다 — 워커가 끝에서 한 번 한다. 스스로 커밋하는
    것(일괄 입력 적용 · 동기화 · 지표)은 그 커밋마다 워커가 걸어 둔 검사(`services.hold`)가
    「아직 이 작업을 쥐었나」 를 같은 트랜잭션에서 보고, 아니면 `Lost` 로 커밋을 막는다."""
    job: Job
    user: User | None
    """시킨 사람. 타이머가 넣은 작업(`allow_system` 인 종류)은 None — 감사 기록의 actor 가
    「타이머」 가 된다."""
    params: dict[str, Any]
    input_file: JobFile | None
    progress: Callable[[str, int, int], None]
    """(단계, 처리한 수, 전체). 취소 요청이 있으면 **여기서 `Cancelled` 가 난다** — 단계
    사이에서만 멈추는 이유다."""
    still_mine: Callable[[Session], None]
    """처리 함수가 **따로 연 트랜잭션**을 커밋하기 바로 앞에, 그 트랜잭션의 세션으로 부른다 —
    「아직 이 작업을 쥐었나」 를 그 안에서 보고 아니면 `Lost`(묶음 가져오기의 바깥 트랜잭션).
    `db` 의 커밋에는 워커가 이미 걸어 두었다."""
    output_file_id: uuid.UUID | None = None
    """결과 파일을 만들었으면 `emit` 이 여기 적는다 — 워커가 끝에 작업 행에 옮긴다."""

    def input_bytes(self) -> bytes:
        """올린 파일의 내용을 **통째로** — 표 파일(일괄 입력 · 묶음)처럼 다 읽어야 푸는 것.
        그 종류의 상한(`job_file_max_bytes`, 100MB)이 메모리의 상한이다. 큰 파일(사진 zip)은
        `open_input` 으로 흘려 읽는다."""
        assert self.input_file is not None  # needs_file — 넣을 때 · 돌릴 때 이미 봤다
        return files.read_bytes(self.input_file)

    def open_input(self) -> AbstractContextManager[BinaryIO]:
        """올린 파일을 **파일로** 연다 — DB 에 든 작은 것이든 저장소(`_jobs/`)의 큰 것이든 같은
        모양이다(`files.open_file`). zip 은 이것을 그대로 연다(통째로 메모리에 안 올린다)."""
        assert self.input_file is not None
        return files.open_file(self.input_file)

    def emit(self, *, name: str, content_type: str, data: bytes) -> JobFile:
        """결과 파일 — 사람은 `GET /api/jobs/{id}/download` 로 받는다."""
        stored = files.store(
            self.db, name=name, content_type=content_type, data=data, output=True
        )
        self.output_file_id = stored.id
        return stored


@dataclass(frozen=True)
class Kind:
    name: str
    label: str
    needs_file: bool
    two_step: bool
    """계획 → 사람 확정 → 적용의 두 단계인가. 그러면 `params["apply"]` 와 지문을 쓴다."""
    run: Callable[[Work], dict[str, Any]]
    owns_workspace: bool = False
    """넣을 때 부서를 정하는 종류인가(객체 가져오기). 관계는 양끝의 것이라 부서가 없다."""
    allow_system: bool = False
    """시킨 사람 없이(타이머) 돌아도 되는 종류인가."""
    public: bool = False
    """일반 작업 API(`POST /api/jobs`)로 넣을 수 있나. **기본은 막힘** — 전용 경로가 있는
    종류(동기화 · 지표 · 묶음 · 내보내기 …)는 그 경로가 권한 · 범위를 본다. 일반 API 로 열면
    그 검사를 건너뛴다(2026-10-08 — 일반 멤버가 `bundle_export` 로 모든 부서의 객체를 받을 수
    있었다)."""
    admin_only: bool = False
    """시킨 사람이 있으면 **시스템 관리자여야** 하는 종류 — 워커가 돌리기 전에 한 번 더 본다
    (넣는 길이 몇이든)."""
    retries: bool = True
    """워커가 죽어 멎은 이 작업을 되살려 다시 돌리나(`services.recover_stale`). 워커를 죽인
    것이 그 작업 자체일 수 있는 계산(지식 그래프 지도 — 메모리를 다 써 강제 종료)은 다시 돌면
    또 죽는다. 그런 종류는 한 번에 `failed` 로 끝낸다(ADR 0049)."""
    stop_midway: bool = False
    """취소하면 **도는 질의까지** 끊나. 기본은 단계 사이(진행 보고)에서 본다 — 질의 하나가 몇십
    분인 계산은 단계 사이가 오지 않아 취소가 먹지 않았다(ADR 0049). 끊긴 질의는 롤백된다."""
    max_file_setting: str = "job_file_max_bytes"
    """올리는 파일의 상한을 읽는 **설정 이름** — 종류마다 다르다(2026-10-10). 표 파일(일괄
    입력 · 묶음)은 통째로 메모리에 올려 풀므로 100MB(`job_file_max_bytes`), 사진 zip 은 파일로
    열어 한 장씩 읽으므로 2GB(`job_photos_max_bytes`). 값은 설치가 `.env` 로 정한다."""


_registry: dict[str, Kind] = {}


def register(kind: Kind) -> None:
    _registry[kind.name] = kind


def get(name: str) -> Kind | None:
    return _registry.get(name)


def names() -> list[str]:
    return sorted(_registry)


def all_kinds() -> list[Kind]:
    return [_registry[name] for name in names()]


def _user(work: Work) -> User:
    if work.user is None:
        raise Conflict(code("JOBS", 14), "이 종류의 작업은 시킨 사람이 있어야 합니다.")
    return work.user


def filestore_gc(work: Work) -> dict[str, Any]:
    """첨부 저장소의 **고아 파일 정리**(`files/gc.py`) — 계획은 세기만, 적용이 지운다.

    시스템 관리자만(또는 시킨 사람 없는 워커). 적용은 계획을 믿지 않고 **다시 훑는다** — 그
    사이 올라오거나 지워진 것이 있다.
    """
    from app.modules.files import gc

    if work.user is not None and not work.user.is_system_admin:
        raise Forbidden(code("JOBS", 23), "첨부 저장소 정리는 시스템 관리자만 합니다.")
    found = gc.scan(work.db, progress=work.progress)
    size = _megabytes(found.orphan_bytes + found.temp_bytes)
    if not work.params.get("apply"):
        return {
            "applied": False,
            "ok": bool(found.orphans or found.temp),
            "orphans": len(found.orphans),
            "orphan_bytes": found.orphan_bytes,
            "temp": len(found.temp),
            "recent": found.recent,
            "scanned": found.scanned,
            "sample": [relative for relative, _ in found.orphans[: gc.SAMPLE]],
            "summary": (
                f"지울 것 — 고아 파일 {len(found.orphans):,}개 · 임시 파일 "
                f"{len(found.temp):,}개({size}) · 훑은 파일 {found.scanned:,}개 · 하루가 안 "
                f"지나 남길 것 {found.recent:,}개"
            ),
        }
    work.progress("지우기", 0, 0)
    done = gc.clean(work.db, found)
    return {
        "applied": True,
        "removed": done.files,
        "removed_bytes": done.bytes,
        "temp": done.temp,
        "kept": done.kept,
        "summary": (
            f"지움 — 고아 파일 {done.files:,}개({_megabytes(done.bytes)}) · 임시 파일 "
            f"{done.temp:,}개"
            + (f" · 다시 보니 쓰여 남김 {done.kept:,}개" if done.kept else "")
        ),
    }


def _megabytes(size: int) -> str:
    return f"{size / 1024 / 1024:,.1f}MB"


# --- 부서 통폐합 -----------------------------------------------------------------


def workspace_reassign(work: Work) -> dict[str, Any]:
    """부서 자료 이동(통폐합의 앞 단계) — 계획은 무엇이 몇 건 옮겨지나 · 무엇이 막히나만 세고,
    적용이 **이 작업의 트랜잭션 하나에서** 옮긴다(`workspaces/services.reassign`).

    **커밋하지 않는다** — 워커가 끝에서 한 번 하고, 그 커밋이 「아직 내 작업인가」 를 같은
    조건으로 본다(`services.run` 의 `_owned`). 나눠 커밋하면 실패 · 취소한 통폐합이 반쯤 옮긴
    부서를 남긴다 — 절반만 옮겨진 부서는 어디까지 됐는지 화면으로 알 수 없다. 대신 객체는
    덩어리마다 진행률을 적고(`work.progress`) 거기서 취소 · 빼앗김이 난다 — 커밋 전이라
    롤백이고 아무것도 안 옮겨진다.

    **시스템 관리자만**(`admin_only` — 워커가 돌리기 전에 다시 본다). 일반 작업 API 로는 못
    넣는다(`public` 아님) — 전용 경로(`POST /api/workspaces/{slug}/reassign`)가 부서 · 종류를
    넣는 순간에 본다.

    ⚠️ 옮길 종류는 `register_workspace_content` 로 등록된 것이다 — **워커도 앱과 같은 조립을
       거친다**(`app/worker.py` 의 `_assemble`). 안 거치면 워커에는 등록이 하나도 없어 「멤버」
       말고는 다 「옮길 수 없는 종류」 가 된다(시험은 앱을 띄운 프로세스에서 워커를 돌려 이것을
       못 본다).
    """
    from app.modules.workspaces import services as workspace_services

    slug = str(work.params.get("slug") or "")
    target = str(work.params.get("target_slug") or "")
    picked = [str(one) for one in work.params.get("kinds") or []]
    if not work.params.get("apply"):
        work.progress("계획", 0, 0)
        return workspace_services.plan_reassign(
            work.db, slug=slug, target_slug=target, kinds=picked
        )
    return workspace_services.reassign(
        work.db,
        slug=slug,
        target_slug=target,
        kinds=picked,
        actor=_user(work),
        progress=work.progress,
        fingerprint=work.params.get("fingerprint"),
    )


# `public` — 일반 작업 API 로 넣는 것은 고아 파일 정리(서버 화면)뿐이다. 나머지는 전용
# 경로로만.
# 부서 자료 이동은 전용 경로(`POST /api/workspaces/{slug}/reassign`, 시스템 관리자)로만.
register(
    Kind(
        "filestore_gc",
        "고아 첨부 파일 정리",
        False,
        True,
        filestore_gc,
        allow_system=True,
        admin_only=True,
        public=True,
    )
)
register(
    Kind(
        "workspace_reassign",
        "부서 자료 이동",
        False,
        True,
        workspace_reassign,
        admin_only=True,
    )
)
