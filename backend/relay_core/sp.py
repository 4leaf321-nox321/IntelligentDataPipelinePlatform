"""틀(StandardPlatform)의 정제 도구를 부르는 **자리 하나**.

`vendor/sp_pipeline/` 은 SP 의 `pipeline/sp_pipeline.py` · `sp_table.py` 를 판을 고정해 옮긴
사본이다(`VERSION` 에 태그 · 커밋 · 해시). **고치지 않는다** — 고칠 것은 SP 에서 고치고
`scripts/sync_sp_pipeline.sh` 로 다시 가져온다. 두 도구는 SP 의 백엔드 시험이 실제 API 와 함께
지킨다(ADR 0001 「흡수하지 않는다」).

`sp_table` 이 `import sp_pipeline` 으로 짝을 찾으므로 그 폴더를 경로에 한 번 올린다. 둘 다 표준
라이브러리만 쓰고 타입 표시가 없어 여기서는 `Any` 로 받는다.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

#: 저장소 루트의 `vendor/sp_pipeline`.
VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "sp_pipeline"


def _modules() -> tuple[Any, Any]:
    if not (VENDOR / "sp_pipeline.py").exists():
        raise RuntimeError(
            f"SP 정제 도구가 없습니다: {VENDOR} — scripts/sync_sp_pipeline.sh 로 가져오세요."
        )
    where = str(VENDOR)
    if where not in sys.path:
        sys.path.insert(0, where)
    return importlib.import_module("sp_pipeline"), importlib.import_module("sp_table")


pipeline, table = _modules()

#: 대상 SP 의 작업이 「대기」 로 이만큼 이어져야 「워커가 꺼졌다」 고 멈춘다(초). 틀의 도구는
#: 120초다 — 사람이 손으로 돌릴 때 맞춘 값이다. 대상 SP 의 워커는 자기 일도 한다: 큰 첫 적재가
#: 들어가면 그래프 지도를 처음부터 그린다(13.7만 객체에 56초, 2026-11-06 실측 — 100만이면 몇
#: 분). 나눠 보내면 다음 조각의 미리 보기가 그 뒤에 줄을 선다. 중계는 사람 없이 기다리는
#: 작업이라 넉넉히 둔다. **틀의 파일은 고치지 않고** 그 값만 바꾼다.
QUEUED_LIMIT_SECONDS = 1800.0
pipeline.QUEUED_LIMIT_SECONDS = max(float(pipeline.QUEUED_LIMIT_SECONDS), QUEUED_LIMIT_SECONDS)


def version() -> dict[str, str]:
    """고정한 판 — `VERSION` 의 `키=값` 줄들."""
    out: dict[str, str] = {}
    for line in (VENDOR / "VERSION").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out
