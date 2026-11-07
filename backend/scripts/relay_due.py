"""타이머가 부른다(`<slug>-sync.timer`, 5분마다) — 차례가 된 파이프라인을 작업으로 넣고,
일정대로라면 끝났어야 할 실행이 끝나지 않은 파이프라인(늦음)을 알린다.

넣기만 한다. 도는 것은 작업 워커다 — 화면의 「지금 실행」 과 같은 길(`relay_run`)이다. 늦음은
**워커가 아니라 여기서** 본다 — 워커가 멈추면 끝나는 작업이 없어 끝날 때 알리는 길은 말이 없다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.all_models  # noqa: F401 — DB 를 만지는 스크립트는 반드시 이것을 읽는다
from app.database import SessionLocal
from app.modules.relay import alerts, services
from app.modules.relay import jobs as _jobs  # noqa: F401 — 작업 종류 등록


def main() -> int:
    with SessionLocal() as db:
        queued = services.enqueue_due(db)
        late = alerts.watch_late(db)
        db.commit()
    print("넣음: " + (", ".join(queued) if queued else "없음"))
    if late:
        print("늦음 갈래가 바뀜: " + ", ".join(late))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
