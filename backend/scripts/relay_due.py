"""타이머가 부른다(`<slug>-sync.timer`, 5분마다) — 차례가 된 파이프라인을 작업으로 넣는다.

넣기만 한다. 도는 것은 작업 워커다 — 화면의 「지금 실행」 과 같은 길(`relay_run`)이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.all_models  # noqa: F401 — DB 를 만지는 스크립트는 반드시 이것을 읽는다
from app.database import SessionLocal
from app.modules.relay import jobs as _jobs  # noqa: F401 — 작업 종류 등록
from app.modules.relay import services


def main() -> int:
    with SessionLocal() as db:
        queued = services.enqueue_due(db)
    print("넣음: " + (", ".join(queued) if queued else "없음"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
