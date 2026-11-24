"""중계 알림의 외부 전달 — 사내 메신저의 수신 웹훅(선택, 기본 미사용).

시스템 관리자 알림은 플랫폼의 알림함에만 쌓여, 화면을 열지 않으면 알 수 없다. 서버 `.env` 에
`RELAY_ALERT_WEBHOOK_URL` 을 두면 같은 알림을 그 주소로도 보낸다. 본문은 JSON `{"text",
"title", "body", "link", "kind"}` 이다 — Slack · Mattermost · 많은 사내 메신저의 수신 웹훅이
`text` 를 읽는다.

**주소는 서버 설정으로만 둔다.** 수신 웹훅 주소에는 대개 토큰이 들어 있어 정의 · 화면에 두면 그
값이 기록에 남는다. 화면은 설정 여부만 표시한다.

보내기는 최선 노력이다. 실패해도 플랫폼 알림은 그대로 남고, 실패는 로그에만 남긴다. 작업의
끝에서 부르므로 오래 기다리지 않는다(`TIMEOUT_SECONDS`).
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 3.0


def configured() -> bool:
    return bool(get_settings().relay_alert_webhook_url.strip())


def _absolute(link: str | None) -> str:
    """화면 경로(`/relay/pipelines/x`)를 설치 주소로 — `PUBLIC_URL` 이 없으면 경로 그대로."""
    if not link:
        return ""
    base = get_settings().public_url.rstrip("/")
    return f"{base}{link}" if base and link.startswith("/") else link


def send(*, kind: str, title: str, body: str | None = None, link: str | None = None) -> bool:
    """알림 하나를 웹훅으로 — 설정하지 않았으면 아무것도 하지 않는다. 보냈으면 참."""
    url = get_settings().relay_alert_webhook_url.strip()
    if not url:
        return False
    target = _absolute(link)
    text = "\n".join(
        part for part in (f"[{get_settings().app_name}] {title}", body, target) if part
    )
    payload = {"text": text, "title": title, "body": body or "", "link": target, "kind": kind}
    try:
        answer = httpx.post(url, json=payload, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as error:
        log.warning("중계 알림 웹훅 전송 실패: %s", type(error).__name__)
        return False
    if answer.status_code >= 400:
        log.warning("중계 알림 웹훅 전송 실패: HTTP %s", answer.status_code)
        return False
    return True
