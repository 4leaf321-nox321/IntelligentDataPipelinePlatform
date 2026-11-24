"""중계 일일 요약 — 매일 정한 시각(`RELAY_DIGEST_AT`, 기본 08:30)에 확인이 필요한 파이프라인을
알림 1건으로.

상태가 바뀔 때만 보내는 알림(`alerts.py`)은 오래 이어지는 문제를 다시 말하지 않는다. 밤에
실패를 알린 뒤 아무도 열지 않으면 그 파이프라인은 그대로 잊힌다. 일일 요약은 그것을 하루 한 번
다시 보인다.

- **확인 필요가 0건이면 보내지 않는다** — 매일 오는 '이상 없음'은 곧 읽히지 않고, 그러면 문제가
  있는 날의 요약도 읽히지 않는다.
- 5분 타이머(`scripts/relay_due.py`)가 부른다 — 그 시각이 지난 뒤 첫 차례에 한 번. 마지막으로
  보낸 시각은 공용 폴더의 `relay-digest.json` 에 두고, 이중화의 두 타이머는 자문 잠금으로 한
  대만 보낸다.
- 빈 값이면 미사용이다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.modules.notifications import services as notifications
from app.modules.relay import overview, webhook
from app.modules.relay.schemas import RelayOverview

FILE = "relay-digest.json"
LOCK = "datarelay:relay_digest"
#: 본문에 싣는 파이프라인 수 — 나머지는 건수로만. 알림 본문의 상한(`alerts.BODY_MAX`)을 넘지
#: 않게.
LISTED = 8
BODY_MAX = 500
LINK = "/relay/pipelines"


def _file() -> Path:
    return get_settings().relay_work_dir.parent / FILE


def _last() -> datetime | None:
    file = _file()
    if not file.is_file():
        return None
    try:
        return datetime.fromisoformat(
            str(json.loads(file.read_text(encoding="utf-8"))["sent_at"])
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def slot(now: datetime, at: str) -> datetime | None:
    """`now` 기준으로 가장 최근의 요약 시각 — `at` 이 비었거나 형식이 틀리면 None(미사용)."""
    try:
        hour, minute = (int(part) for part in at.split(":"))
    except ValueError:
        return None
    moment = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return moment if moment <= now else moment - timedelta(days=1)


def is_due(now: datetime, last: datetime | None, at: str) -> bool:
    latest = slot(now, at)
    if latest is None:
        return False
    return last is None or last.astimezone(now.tzinfo) < latest


def message(summary: RelayOverview) -> tuple[str, str]:
    """(제목, 본문) — 상태별 건수 한 줄과 급한 차례의 파이프라인."""
    total = len(summary.attention)
    title = f"중계 일일 요약 — 확인 필요 {total}건"
    order = [status for status in overview.ATTENTION if summary.counts.get(status)]
    line = " · ".join(f"{summary.labels.get(one, one)} {summary.counts[one]}" for one in order)
    drift_only = sum(1 for one in summary.attention if one.status not in overview.ATTENTION)
    if drift_only:
        line = " · ".join(part for part in (line, f"대상 불일치 {drift_only}") if part)
    rows = [
        f"- {one.slug}: {one.status_label}" + (f" ({one.reason})" if one.reason else "")
        for one in summary.attention[:LISTED]
    ]
    rest = total - len(rows)
    if rest > 0:
        rows.append(f"외 {rest}건은 파이프라인 목록에서 확인하세요.")
    return title, "\n".join([f"전체 {summary.total}개 중 {line}", *rows])[:BODY_MAX]


def send_if_due(db: Session, now: datetime | None = None) -> int | None:
    """차례가 되었으면 요약을 보낸다 — 보낸(또는 0건이라 건너뛴) 확인 필요 건수, 차례가 아니면
    None. **부르는 쪽이 커밋한다.**"""
    moment = now or datetime.now().astimezone()
    at = get_settings().relay_digest_at.strip()
    if not is_due(moment, _last(), at):
        return None
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"), {"k": LOCK}):
        return None
    if not is_due(moment, _last(), at):  # 잠금을 기다리는 동안 다른 서버가 보냈다
        return None
    summary = overview.build(db)
    if summary.attention:
        title, body = message(summary)
        notifications.notify_system_admins(
            db, kind=notifications.RELAY_DIGEST, title=title, body=body, link=LINK
        )
        webhook.send(kind=notifications.RELAY_DIGEST, title=title, body=body, link=LINK)
    file = _file()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"sent_at": moment.isoformat()}), encoding="utf-8")
    return len(summary.attention)
