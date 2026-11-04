"""두 서버가 같은 판단을 하지 않게 — **잠금은 연결 하나가 잡고, commit 을 거쳐도 놓인다.**"""

from __future__ import annotations

from sqlalchemy import text

from app.shared import singleton


def test_잠금은_한_연결만_잡고_놓으면_다음이_잡는다() -> None:
    with singleton.held("datasource-sync") as first:
        assert first is not None
        # 다른 서버(다른 연결)는 기다리지 않고 물러난다.
        with singleton.held("datasource-sync") as second:
            assert second is None
        # 다른 이름은 서로 막지 않는다.
        with singleton.held("webhook-dispatch") as other:
            assert other is not None
    with singleton.held("datasource-sync") as again:
        assert again is not None


def test_커밋을_거쳐도_같은_연결이라_잠금이_풀린다() -> None:
    """세션의 commit 은 연결을 풀에 돌려주므로, 보통의 세션이면 잠근 연결과 놓는 연결이
    달라져 잠금이 풀에 남는다(실측 — 웹훅이 두 번째부터 안 나갔다). 묶인 세션은 그렇지 않다."""
    with singleton.held("webhook-dispatch") as db:
        assert db is not None
        pid = db.scalar(text("SELECT pg_backend_pid()"))
        db.execute(text("SELECT 1"))
        db.commit()
        db.execute(text("SELECT 1"))
        db.commit()
        assert db.scalar(text("SELECT pg_backend_pid()")) == pid
    with singleton.held("webhook-dispatch") as free:
        assert free is not None
