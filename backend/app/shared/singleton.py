"""한 번에 한 곳만 — 앱이 두 서버에서 돌 때 **프로세스 밖의 판단**을 DB 에 맡긴다.

앱은 무상태에 가깝지만, 스스로 「지금 할 일이 있나」 를 보고 움직이는 자리가 둘 있다 —
데이터 소스 동기화 타이머와 웹훅 발송. 두 서버가 같은 시각에 같은 판단을 하면 같은 원천이
두 번 들어가고 같은 웹훅이 두 번 간다. 조정은 앱끼리가 아니라 **DB 의 자문 잠금**(advisory
lock)으로 한다 — 앱은 서로의 존재를 몰라도 된다.

잠금은 **연결**에 붙는다. 그 연결이 끊기면 저절로 풀려 잡은 쪽이 죽어도 남지 않는다. 대신
세션의 `commit()` 은 연결을 풀에 돌려주고 다음 문장은 **다른 연결**로 나가므로, 잠금을 잡은
연결과 놓는 연결이 달라져 잠금이 풀에 남는다(실측 — 웹훅이 두 번째부터 안 나갔다). 그래서
잠는 동안은 연결 하나를 손에 쥐고, 그 연결에 묶인 세션만 쓴다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine

#: 이름마다 잠금 번호가 정해진다 — 다른 플랫폼(다른 DB)과는 겹칠 일이 없다.
_NAMES = {"datasource-sync": 1, "webhook-dispatch": 2}


def _key(name: str) -> int:
    if name in _NAMES:
        return _NAMES[name]
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:4], "big")


@contextmanager
def held(name: str) -> Iterator[Session | None]:
    """잠금을 잡으면 그 연결에 묶인 세션을, 다른 곳이 쥐고 있으면 **기다리지 않고** None 을.

    세션의 `commit()` 을 몇 번 해도 연결은 바뀌지 않는다. 블록을 나가면 잠금을 놓고
    연결을 돌려준다 — 예외로 나가도 같다."""
    with engine.connect() as connection:
        got = bool(
            connection.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": _key(name)})
        )
        # 잠금 문장이 연 트랜잭션을 닫아 둔다 — 열린 채 세션을 묶으면 세션의 commit 이 그
        # 바깥 트랜잭션에 합류해 실제로는 저장되지 않는다.
        connection.commit()
        if not got:
            yield None
            return
        session = SessionLocal(bind=connection)
        try:
            yield session
        finally:
            session.close()
            connection.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _key(name)})
            connection.commit()
