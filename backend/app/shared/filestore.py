"""첨부 파일이 실제로 사는 곳 — **DB 에는 경로와 해시만 둔다.**

파일 내용을 DB 에 넣지 않는 이유: 덤프가 통째로 커져서 백업·복구 시간이 첨부 크기에
묶인다. 그리고 큰 바이너리는 DB 가 잘하는 일이 아니다.

## 내용으로 이름을 짓는다

저장 이름은 sha256 이고, 같은 내용은 한 번만 저장된다. 사람이 올린 이름은 DB 의
`original_name` 에 남는다.

    filestore/ab/cd/abcdef...  <- 앞 네 글자로 두 겹 나눈다

**한 폴더에 수만 개를 두지 않는다.** Windows 탐색기가 그 폴더를 못 열고, 백업
도구도 느려진다. 두 겹이면 파일 100만 개에서도 한 폴더가 수백 개다.

## 행을 지울 때 파일을 지우지 않는다

같은 내용을 여러 행이 가리킬 수 있어서, 행 하나를 지웠다고 파일을 지우면 다른
행이 가리키던 것이 사라진다. 정리는 「아무도 안 가리키는 것」 을 따로 훑는 일이다
(`files/gc.py` — 워커가 하루 한 번, 시스템 관리자가 작업으로).
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings

#: 한 번에 읽는 크기. 통째로 메모리에 올리지 않는다 — 큰 파일 몇 개가 동시에
#: 올라오면 그것만으로 프로세스가 죽는다.
CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Stored:
    """저장 결과. `size` 는 **우리가 실제로 쓴 바이트**다.

    브라우저가 보낸 Content-Length 를 믿지 않는다 — 그 값과 실제가 다를 수 있고,
    그때 DB 의 크기와 디스크의 크기가 어긋난다.
    """

    sha256: str
    size: int
    #: filestore 아래의 상대 경로. **절대경로를 DB 에 넣지 않는다** — 서버를 옮기면
    #: 그 값 전부가 틀린 것이 된다.
    relative_path: str
    #: 이미 있던 내용인가. 화면이 「같은 파일입니다」 를 말할 수 있다.
    deduplicated: bool


def root() -> Path:
    return get_settings().filestore_dir


def _path_for(digest: str) -> Path:
    return root() / digest[:2] / digest[2:4] / digest


def save(stream: BinaryIO) -> Stored:
    """스트림을 저장하고 (해시, 크기, 상대경로) 를 돌려준다.

    **임시 파일에 쓰고 나서 옮긴다.** 내용을 다 읽어야 이름(해시)이 정해지고,
    도중에 끊기면 반쪽 파일이 정상 이름으로 남으면 안 되기 때문이다 — 그 파일은
    해시가 맞지 않는데도 「있는 것」 으로 보인다.
    """
    base = root()
    staging = base / "_incoming"
    staging.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    size = 0
    # delete=False 로 만들고 직접 옮긴다. Windows 는 열려 있는 파일을 옮길 수 없다.
    import tempfile

    with tempfile.NamedTemporaryFile(dir=staging, delete=False) as temp:
        temp_path = Path(temp.name)
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
            temp.write(chunk)

    checksum = digest.hexdigest()
    final = _path_for(checksum)
    if final.exists():
        # 같은 내용이 이미 있다. **시각을 새로 한 뒤에** 새로 쓴 것을 버린다 — 고아 정리
        # (`files/gc.py`)가 하루 지난 고아로 골라 둔 파일이면 행이 생기기 전에 지우지 않게.
        # 시각을 못 바꿨으면(그 사이 정리가 지웠다) 새로 쓴 것을 그 자리로 옮긴다 — 먼저
        # 버리면 행이 없는 파일을 가리켰다(2026-10-08).
        try:
            os.utime(final)
        except OSError:
            pass
        else:
            temp_path.unlink(missing_ok=True)
            return Stored(
                checksum, size, str(final.relative_to(base)).replace("\\", "/"), True
            )

    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(temp_path), str(final))
    return Stored(checksum, size, str(final.relative_to(base)).replace("\\", "/"), False)


def resolve(relative_path: str) -> Path | None:
    """상대 경로를 실제 파일로. 없거나 **밖을 가리키면** None.

    `..` 이 섞인 값이 오면 filestore 바깥의 파일을 내보낼 수 있다. 그 값은 DB 에서
    오지만, DB 에 들어가는 경로를 만드는 곳이 늘어나면 언젠가 검사를 빠뜨린다 —
    그래서 **내보내는 자리에서** 한 번 더 본다.
    """
    base = root().resolve()
    target = (base / relative_path).resolve()
    if not target.is_file():
        return None
    if base not in target.parents:
        return None
    return target
