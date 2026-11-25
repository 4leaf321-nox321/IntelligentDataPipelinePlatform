"""타이머가 부른다(`<slug>-sync.timer`, 5분마다) — 대상 SP 상태를 조회해 두고(`targets.py`),
차례가 된 파이프라인을 작업으로 넣고,
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
from app.modules.relay import alerts, digest, services, targets
from app.modules.relay import jobs as _jobs  # noqa: F401 — 작업 종류 등록


def main() -> int:
    with SessionLocal() as db:
        # 대상 SP 상태를 조회해 둔다 — 지도 · 목록 · 요약은 이 저장값을 읽는다(`targets.py`).
        # 조회는 트랜잭션 밖의 HTTP 라 먼저, 따로 한다.
        checked = targets.refresh(targets.known(db), services.probe_http)
        db.rollback()
        # SP 화면에서 적용한 계획을 알아챈다 — 지도 · 홈은 묻지 않고 이 결과를 읽는다.
        settled = services.settle_all(db)
        queued = services.enqueue_due(db)
        late = alerts.watch_late(db)
        summary = digest.send_if_due(db)
        db.commit()
    down = [server for server, one in checked.items() if not one.get("reachable")]
    print(f"대상 조회: {len(checked)}개" + (f"(연결 불가: {', '.join(down)})" if down else ""))
    if settled:
        print("적용 확인: " + ", ".join(settled))
    print("작업 추가: " + (", ".join(queued) if queued else "없음"))
    if late:
        print("지연 상태 변경: " + ", ".join(late))
    if summary is not None:
        print(f"일일 요약: 확인 필요 {summary}건" + ("" if summary else "(전송하지 않음)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
