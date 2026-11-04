"""작업 파일의 저장 계층 — **여기 하나만 바꾸면 저장 위치가 바뀐다.**

작은 것은 DB(`job_files.data`), 큰 것(`job_file_inline_max_bytes`, 기본 32MB 초과)은 공용
저장소의 작업 폴더(`FILESTORE_DIR/_jobs/<id>`)에 두고 행에는 경로만 둔다(2026-10-10,
`docs/sp/작업-워커-설계.md` 3장). 공용 저장소는 첨부가 이미 쓰는 곳이라 이중화의 두 서버가 같은
경로로 마운트한다 — 어느 서버에서 올려도 어느 워커가 집어도 읽는다. 개발 PC(서버 한 대)는 그냥
로컬 폴더다.

## 흘려 받는다

올린 파일은 1MB 씩 읽어 해시를 내며 옮긴다(`store_stream`). 예전에는 `upload.file.read()` 로
통째로 읽어, 100MB 파일이면 요청 하나가 그만큼 메모리를 들었다 — 사진 zip 은 이제 2GB 까지다.
문턱 아래에서는 메모리에 모으고(DB 에 넣으려면 어차피 바이트가 있어야 한다), 넘는 순간 작업
폴더의 임시 파일(`<id>.part`)로 갈아탄다. 다 받으면 이름을 바꾼다(`<id>`) — 받다 끊긴 것은
`.part` 로 남고 정리가 하루 뒤 지운다. 상한은 받는 **도중에** 본다(다 받고 재지 않는다).

## 지우기

행은 만료(`job_file_ttl_days`)에 지우고, 그 행의 파일은 **커밋한 뒤에** 지운다 — 먼저 지웠는데
커밋이 무르면 행이 없는 파일이 아니라 파일이 없는 행이 남는다. 행 없이 남은 파일(받다 끊긴 것 ·
넣다 롤백된 것)도 하루가 지나면 지운다(`sweep_orphans`). 첨부의 고아 정리(`files/gc.py`)는 이
폴더를 안 본다 — 작업 파일의 수명은 작업이 정한다.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.jobs.models import Job, JobFile
from app.shared import filestore
from app.shared.errors import AppError, code

#: 공용 저장소 아래 작업 파일의 폴더 — 첨부(내용 주소 `ab/cd/<sha256>`)와 모양이 달라 첨부의
#: 고아 정리가 손대지 않는다(그래도 `files/gc.py` 가 이 이름을 따로 건너뛴다).
JOBS_DIR = "_jobs"
#: 받는 도중의 파일 — 다 받으면 이 꼬리를 뗀다.
PART = ".part"
#: 한 번에 읽고 쓰는 크기.
CHUNK = 1024 * 1024
#: 행 없는 파일을 이만큼 지나야 지운다 — 받는 중이거나 막 받아 커밋을 기다리는 것을 안 지운다.
GRACE = timedelta(hours=24)
_MB = 1024 * 1024


def jobs_root() -> Path:
    return filestore.root() / JOBS_DIR


def cap_of(setting: str) -> int:
    """종류의 상한 — 설정 이름(`Kind.max_file_setting`)으로 읽는다."""
    return int(getattr(get_settings(), setting))


def too_big(
    size: int, cap: int, *, output: bool = False, label: str | None = None
) -> AppError:
    """상한을 넘은 파일 — 413. `size` 는 받은 데까지(받는 도중에 멈추면 그만큼)."""
    advice = "조건으로 좁혀 나눠 내보내세요" if output else "나눠 업로드하세요"
    what = f"{label}은(는) " if label else ""
    return AppError(
        code("JOBS", 1),
        f"파일이 너무 큽니다({size // _MB:,}MB). {what}한 번에 {cap // _MB:,}MB 까지 — "
        f"{advice}.",
        status=413,
        details={"size_bytes": size, "max_bytes": cap},
    )


def store(
    db: Session,
    *,
    name: str,
    content_type: str,
    data: bytes,
    output: bool = False,
    cap: int | None = None,
) -> JobFile:
    """바이트로 가진 것을 넣는다 — 작업이 만든 결과 파일(`output`, 상한
    `job_output_max_bytes`)과 서버가 만든 입력(묶음의 JSON). 큰 것은 공용 저장소로 간다."""
    settings = get_settings()
    limit = cap
    if limit is None:
        limit = settings.job_output_max_bytes if output else settings.job_file_max_bytes
    if len(data) > limit:
        raise too_big(len(data), limit, output=output)
    return store_stream(
        db,
        name=name,
        content_type=content_type,
        stream=io.BytesIO(data),
        cap=limit,
        output=output,
    )


def store_stream(
    db: Session,
    *,
    name: str,
    content_type: str,
    stream: BinaryIO,
    cap: int,
    output: bool = False,
    label: str | None = None,
) -> JobFile:
    """**흘려 받아** 넣는다 — 한 번에 `CHUNK` 씩. 문턱(`job_file_inline_max_bytes`)을 넘으면
    공용 저장소의 작업 폴더에, 아니면 DB 에. 상한(`cap`)을 넘는 순간 멈추고 413 — 쓰던 파일은
    지운다. 행은 flush 까지(커밋은 부르는 쪽)."""
    inline = get_settings().job_file_inline_max_bytes
    file_id = uuid.uuid4()
    digest = hashlib.sha256()
    size = 0
    held: list[bytes] = []
    part: Path | None = None
    spill: BinaryIO | None = None
    try:
        while chunk := stream.read(CHUNK):
            size += len(chunk)
            if size > cap:
                raise too_big(size, cap, output=output, label=label)
            digest.update(chunk)
            if spill is None and size > inline:
                part = jobs_root() / f"{file_id}{PART}"
                part.parent.mkdir(parents=True, exist_ok=True)
                spill = part.open("wb")
                for one in held:
                    spill.write(one)
                held = []
            if spill is not None:
                spill.write(chunk)
            else:
                held.append(chunk)
        if spill is not None:
            spill.close()
            spill = None
    except BaseException:
        if spill is not None:
            spill.close()
        if part is not None:
            part.unlink(missing_ok=True)
        raise
    if size == 0:
        raise AppError(code("JOBS", 2), "빈 파일입니다.", status=422)
    relative: str | None = None
    if part is not None:
        os.replace(part, jobs_root() / str(file_id))
        relative = f"{JOBS_DIR}/{file_id}"
    row = JobFile(
        id=file_id,
        name=name[:255],
        content_type=content_type or "application/octet-stream",
        size_bytes=size,
        sha256=digest.hexdigest(),
        data=None if relative else b"".join(held),
        path=relative,
        expires_at=datetime.now(UTC) + timedelta(days=get_settings().job_file_ttl_days),
    )
    db.add(row)
    try:
        db.flush()
    except BaseException:
        if relative:
            _unlink(relative)
        raise
    return row


def _resolve(relative: str) -> Path:
    """행의 경로를 실제 파일로 — **작업 폴더 밖은 안 연다**(DB 의 값이라도 `..` 를 믿지
    않는다)."""
    base = jobs_root().resolve()
    target = (filestore.root() / relative).resolve()
    if base not in target.parents:
        raise AppError(
            code("JOBS", 29), f"작업 파일의 경로가 이상합니다: {relative}", status=409
        )
    return target


def local_path(row: JobFile) -> Path | None:
    """저장소에 놓인 파일이면 그 경로(없으면 409), DB 에 든 것이면 None."""
    if row.path is None:
        return None
    target = _resolve(row.path)
    if not target.is_file():
        raise _missing(row)
    return target


def _missing(row: JobFile) -> AppError:
    return AppError(
        code("JOBS", 30),
        f"작업 파일 「{row.name}」 이(가) 저장소에 없습니다({row.path}). 서버 두 대가 같은 "
        "저장소(FILESTORE_DIR)를 쓰는지 확인하세요 — 지워졌다면 다시 업로드하세요.",
        status=409,
    )


@contextlib.contextmanager
def open_file(row: JobFile) -> Iterator[BinaryIO]:
    """작업 파일을 **파일로** 연다 — DB 에 든 것은 그 바이트를, 저장소의 것은 그 파일을. 처리는
    어디에 있든 같은 모양으로 읽는다(zip 은 이것을 그대로 연다)."""
    path = local_path(row)
    if path is None:
        yield io.BytesIO(row.data or b"")
        return
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        raise _missing(row) from None
    with handle:
        yield handle


def read_bytes(row: JobFile) -> bytes:
    """작업 파일을 통째로 — 표 파일처럼 다 읽어야 푸는 것만(그 종류의 상한이 메모리의 상한)."""
    if row.path is None:
        return row.data or b""
    with open_file(row) as handle:
        return handle.read()


def _unlink(relative: str) -> bool:
    try:
        _resolve(relative).unlink()
    except FileNotFoundError:
        return False  # 다른 서버의 정리가 방금 지웠다
    except AppError:
        return False
    return True


def purge_expired(db: Session) -> int:
    """기한 지난 파일을 지운다. 작업 행의 참조는 SET NULL — 작업 기록은 남고 파일만 없다.

    저장소의 파일은 **커밋한 뒤에** 지운다(먼저 지웠는데 무르면 파일이 없는 행이 남는다). 행
    없이 남은 파일도 함께 치운다(`sweep_orphans`). 돌려주는 것은 지운 행 수 + 치운 파일 수."""
    now = datetime.now(UTC)
    gone = db.execute(
        delete(JobFile).where(JobFile.expires_at < now).returning(JobFile.id, JobFile.path)
    ).all()
    db.commit()
    for _id, relative in gone:
        if relative:
            _unlink(relative)
    return len(gone) + sweep_orphans(db)


def sweep_orphans(db: Session, *, now: float | None = None) -> int:
    """작업 폴더에서 **행이 안 가리키고 하루가 지난** 파일을 지운다 — 받다 끊긴 `.part`, 받은
    뒤 넣다 롤백된 것. 하루를 두는 이유: 받는 중이거나 막 받아 커밋을 기다리는 파일은 아직 아무
    행도 안 가리킨다(받는 동안은 시각이 계속 새로워진다). 서버 두 대의 정리가 같은 폴더를 함께
    훑어도 된다 — 먼저 지운 쪽이 이기고 늦은 쪽은 건너뛴다."""
    root = jobs_root()
    if not root.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - GRACE.total_seconds()
    known = set(db.scalars(select(JobFile.path).where(JobFile.path.is_not(None))))
    removed = 0
    for entry in os.scandir(root):
        if not entry.is_file(follow_symlinks=False):
            continue
        relative = f"{JOBS_DIR}/{entry.name}"
        if relative in known:
            continue
        try:
            if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                continue
            os.unlink(entry.path)
        except FileNotFoundError:
            continue
        removed += 1
    return removed


def purge_old_jobs(db: Session) -> int:
    """기한 지난 **끝난** 작업 기록을 지운다.

    도는 것은 아무리 오래돼도 안 지운다 — 멎은 작업은 워커가 되살릴 몫이고, 여기서 지우면
    그 일이 있었다는 사실까지 사라진다. 적용 작업이 가리키던 계획 작업이 먼저 지워져도
    `parent_id` 는 SET NULL 이라 남는다.
    """
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().job_ttl_days)
    gone = db.execute(
        delete(Job)
        .where(Job.status.in_(("done", "failed", "cancelled")), Job.finished_at < cutoff)
        .returning(Job.id)
    )
    count = len(gone.all())
    db.commit()
    return count
