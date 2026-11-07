"""바깥으로 나가는 연결의 설정 — **사내 사설 인증서(CA)** 와 프록시.

httpx 는 `SSL_CERT_FILE` 이 없으면 certifi 의 공개 CA 만 믿는다. 틀의 도구(`vendor/sp_pipeline`
— urllib)도 OpenSSL 의 기본 자리만 본다. 그래서 사내 CA 가 서명한 HTTPS 원천 · 대상에는
「CERTIFICATE_VERIFY_FAILED」 로 닿지 않는다 — 운영의 첫 연결에서 만날 일이다.

`RELAY_CA_FILE` 을 주면 뜰 때(`create_app` — 앱 · 워커 둘 다) **공개 CA 묶음에 사내 CA 를 더한
파일**을 작업 폴더에 쓰고 `SSL_CERT_FILE` 로 건다 — 둘 다 그것을 본다. 사내 CA 만 걸면 공개
인증서의 주소(사외 IdP 등)가 거꾸로 안 닿는다. 프록시는 `RELAY_PROXY` · `RELAY_NO_PROXY` 를
표준 환경 변수(`HTTPS_PROXY` · `NO_PROXY`)로 건다 — 대상 SP 같은 사내 주소는 `NO_PROXY` 에.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import certifi

from app.config import Settings

log = logging.getLogger(__name__)
BUNDLE = ".ca-bundle.pem"


def apply(settings: Settings) -> list[str]:
    """설정을 이 프로세스에 건다 — 한 일을 한 줄씩 돌려준다(서버 기동 로그)."""
    done: list[str] = []
    if settings.relay_ca_file:
        corp = Path(settings.relay_ca_file)
        if not corp.is_file():
            log.warning("RELAY_CA_FILE 이 없습니다 — 사내 CA 를 걸지 않았습니다: %s", corp)
        else:
            bundle = settings.relay_work_dir / BUNDLE
            body = Path(certifi.where()).read_bytes().rstrip() + b"\n" + corp.read_bytes()
            if not bundle.is_file() or bundle.read_bytes() != body:
                bundle.parent.mkdir(parents=True, exist_ok=True)
                scratch = bundle.with_suffix(".tmp")
                scratch.write_bytes(body)
                os.replace(scratch, bundle)
            os.environ["SSL_CERT_FILE"] = str(bundle)
            done.append(f"사내 CA 를 더한 인증서 묶음: {bundle}")
    if settings.relay_proxy:
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            os.environ[name] = settings.relay_proxy
        no_proxy = settings.relay_no_proxy
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = no_proxy
        done.append(f"프록시 {settings.relay_proxy} (거치지 않음: {no_proxy})")
    for line in done:
        log.info("바깥 연결: %s", line)
    return done
