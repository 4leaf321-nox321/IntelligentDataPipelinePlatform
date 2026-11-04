"""워커는 **앱과 같은 조립**으로 돈다 — 따로 띄운 프로세스에서 본다(2026-10-10).

시험은 앱을 띄운 프로세스 안에서 워커(`process_one`)를 돌린다 — 그 프로세스에는 앱이 건 등록이
이미 있어서, 운영의 워커(`python -m app.worker`, `app.main` 을 안 거친다)에 등록이 **하나도
없다**는 것을 못 봤다. 그래서 사진 일괄 업로드의 적용은 운영에서 첨부 자리를 몰라 객체의 부서
대신 전역으로 판정했고, 부서 자료 이동은 멤버 말고는 옮길 종류를 몰랐다. 여기서는 새 프로세스를
띄워 본다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]

_PROBE = """
import json
from app import worker
from app.shared import events, extensions
before = len(extensions._contents)
worker._assemble()
print(json.dumps({
    "before": before,
    "contents": sorted(one.__module__ for one in extensions._contents),
    "owners": sorted(extensions._attachment_owners),
    "listeners": sorted(f"{one.__module__}.{one.__name__}" for one in events._listeners),
    "summary_only": sorted(f"{one.__module__}.{one.__name__}" for one in events._summary_only),
}))
"""


def test_워커는_앱과_같은_등록으로_돈다(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "FILESTORE_DIR": str(tmp_path / "filestore"),
        "LOG_DIR": str(tmp_path / "logs"),
        "EXTENSIONS": "",
    }
    ran = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr[-2000:]
    found = json.loads(ran.stdout.strip().splitlines()[-1])
    # 조립 전에는 비어 있다 — 이것이 운영 워커가 처음에 놓였던 자리다.
    assert found["before"] == 0
    # 부서 통폐합(작업)이 옮길 종류 — 워커가 모르면 「멤버」 말고는 다 옮길 수 없다.
    assert "app.modules.files.services" in found["contents"]
    # 첨부 자리 — 틀에서는 객체 · 게시글이 열었다. 이 플랫폼에는 아직 붙일 자리가 없다.
    assert found["owners"] == []
    # 변경 이벤트를 듣는 곳 — 틀의 웹훅 · 지켜보기는 온톨로지와 함께 걷어냈다(ADR 0001).
    assert found["listeners"] == []
    assert found["summary_only"] == []
