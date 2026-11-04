"""첨부 저장소의 **고아 파일 정리** — 아무 첨부도 안 가리키는 파일을 지운다(ADR 0012).

filestore 는 내용 주소(sha256)라 같은 내용을 여러 첨부가 가리킬 수 있다. 그래서 첨부를 지울
때는 **행만** 지우고(`deleted_at`) 파일은 남겼다 — 그 사이 거절된 업로드 · 뗀 첨부 · 지운
타입의 파일이 디스크에 쌓이기만 했다. 여기서 따로 훑어 「살아 있는 첨부 행이 하나도 안
가리키는 파일」 만 지운다.

**지우는 것** — 둘뿐이다.
- 내용 주소 모양(`ab/cd/<sha256 64자>`)의 파일 중 살아 있는 첨부(원본 · 미리보기)가 안 가리키고
  **하루가 지난** 것. 하루를 두는 이유: 업로드는 파일을 먼저 쓰고 행을 나중에 넣는다 — 그
  사이의 파일은 아직 아무도 안 가리킨다.
- `_incoming/` 의 하루 지난 임시 파일(끊긴 업로드의 찌꺼기).

**안 지우는 것** — 그 밖의 모든 것. filestore 아래에 다른 것을 두는 설치가 있다(데이터 소스의
파일 폴더 `incoming/` 을 이 아래에 두라고 권한다). 모양이 다르면 손대지 않는다. **큰 작업
파일의 폴더(`_jobs/`)는 아예 안 들어간다**(2026-10-10) — 그 파일은 첨부가 아니라 작업의 것이고,
작업 파일의 만료(`jobs/files.purge_expired`)가 지운다. 여기서 「아무 첨부도 안 가리킨다」 로
지우면 계획을 본 뒤 적용을 기다리는 사진 zip 이 사라진다.

**지운 첨부를 되살리는 길은 없다** — 휴지통에 간 객체의 첨부는 행이 살아 있어(객체만 지운다)
그대로 남는다. 지우기 직전에 한 번 더 본다: 그 사이 같은 내용이 다시 올라와 행이 생겼거나
(`filestore.save` 가 같은 내용이면 파일 시각을 새로 한다) 시각이 새로워졌으면 남긴다.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.files.models import Attachment
from app.shared import filestore

#: 이보다 새 파일은 고아여도 안 지운다 — 업로드는 파일을 먼저 쓰고 행을 나중에 넣는다.
GRACE = timedelta(hours=24)
#: 내용 주소 모양 — 이것만 지울 수 있다.
CONTENT_PATH = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}$")
INCOMING = "_incoming"
#: 큰 작업 파일의 폴더(`jobs/files.JOBS_DIR`) — 첨부가 아니다. 이름만 맞춘다(files 가 jobs 를
#: import 하지 않게).
JOBS = "_jobs"
#: 결과에 싣는 예시 수.
SAMPLE = 20

Progress = Callable[[str, int, int], None] | None


@dataclass
class Found:
    """훑은 결과 — 지울 것과 그 크기."""

    orphans: list[tuple[str, int]] = field(default_factory=list)
    """(상대 경로, 바이트) — 아무 첨부도 안 가리키고 하루 지난 것."""
    temp: list[tuple[str, int]] = field(default_factory=list)
    """`_incoming/` 의 하루 지난 임시 파일."""
    recent: int = 0
    """고아지만 하루가 안 지나 남긴 것."""
    scanned: int = 0

    @property
    def orphan_bytes(self) -> int:
        return sum(size for _, size in self.orphans)

    @property
    def temp_bytes(self) -> int:
        return sum(size for _, size in self.temp)


def _referenced(db: Session) -> set[str]:
    """살아 있는 첨부가 가리키는 파일(원본 · 미리보기)."""
    out: set[str] = set()
    for original, thumb in db.execute(
        select(Attachment.relative_path, Attachment.thumb_path).where(
            Attachment.deleted_at.is_(None)
        )
    ):
        out.add(original)
        if thumb:
            out.add(thumb)
    return out


def scan(db: Session, *, now: datetime | None = None, progress: Progress = None) -> Found:
    """filestore 를 훑어 지울 것을 고른다 — **아무것도 안 지운다.**"""
    base = filestore.root()
    found = Found()
    if not base.is_dir():
        return found
    cutoff = (now or datetime.now(UTC)).timestamp() - GRACE.total_seconds()
    referenced = _referenced(db)
    tops = sorted(
        (entry for entry in os.scandir(base) if entry.is_dir(follow_symlinks=False)),
        key=lambda entry: entry.name,
    )
    for index, top in enumerate(tops):
        if progress is not None:
            progress("훑기", index, len(tops))
        if top.name == JOBS:
            continue
        if top.name == INCOMING:
            for entry in os.scandir(top.path):
                if entry.is_file(follow_symlinks=False) and entry.name.startswith("tmp"):
                    # 훑는 사이 사라질 수 있다 — 업로드가 끝나 옮겼거나, 같은 저장소를 쓰는
                    # 다른 서버의 정리가 지웠다. 그 파일 하나로 정리 전체가 멈추면 안 된다.
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if info.st_mtime < cutoff:
                        found.temp.append((f"{INCOMING}/{entry.name}", info.st_size))
            continue
        for path in Path(top.path).glob("*/*"):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(base).as_posix()
            if not CONTENT_PATH.match(relative):
                continue
            found.scanned += 1
            if relative in referenced:
                continue
            try:
                info = path.stat()
            except FileNotFoundError:
                continue  # 다른 서버의 정리가 방금 지웠다
            if info.st_mtime >= cutoff:
                found.recent += 1
                continue
            found.orphans.append((relative, info.st_size))
    return found


@dataclass
class Cleaned:
    files: int = 0
    bytes: int = 0
    temp: int = 0
    kept: int = 0
    """훑은 뒤 지우기 직전에 다시 보니 가리키는 행이 생겼거나 새로워져 남긴 것."""


def clean(db: Session, found: Found, *, now: datetime | None = None) -> Cleaned:
    """고른 것을 지운다 — **지우기 직전에 한 번 더 본다**(그 사이 같은 내용이 올라왔나)."""
    base = filestore.root()
    cutoff = (now or datetime.now(UTC)).timestamp() - GRACE.total_seconds()
    done = Cleaned()
    wanted = [relative for relative, _ in found.orphans]
    again: set[str] = set()
    for start in range(0, len(wanted), 1000):
        chunk = wanted[start : start + 1000]
        for original, thumb in db.execute(
            select(Attachment.relative_path, Attachment.thumb_path).where(
                Attachment.deleted_at.is_(None),
                or_(Attachment.relative_path.in_(chunk), Attachment.thumb_path.in_(chunk)),
            )
        ):
            again.add(original)
            if thumb:
                again.add(thumb)
    for relative, size in found.orphans:
        path = base / relative
        try:
            if relative in again or path.stat().st_mtime >= cutoff:
                done.kept += 1
                continue
            path.unlink()
        except FileNotFoundError:
            continue
        done.files += 1
        done.bytes += size
        for parent in (path.parent, path.parent.parent):
            try:
                parent.rmdir()  # 비었을 때만 지워진다
            except OSError:
                break
    for relative, _size in found.temp:
        try:
            (base / relative).unlink()
        except FileNotFoundError:
            continue
        done.temp += 1
    return done
