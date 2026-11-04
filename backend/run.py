"""서버 기동 — 개발에서도, 컨테이너 안에서도 이 파일 하나가 띄운다.

    python run.py                      개발 (reload, 포트 +1)
    APP_ENV=production python run.py   운영 (다중 워커)

**같은 진입점을 쓰는 이유**: SIF 의 `%runscript` 가 이것을 부른다. 개발과 운영이
다른 경로로 뜨면 "개발에서는 되는데 배포하면 안 되는" 차이가 그 틈에 쌓인다.

## 개발은 포트를 +1 한다

운영과 같은 포트를 쓰면 개발 백엔드를 내린 순간 프론트 프록시가 **같은 기계의
운영 설치본**에 그대로 붙는다. 서버가 죽은 것도 코드가 틀린 것도 아니어서 볼 곳이
없다. 그래서 운영이 N, 개발이 N+1 이다.

## reload 와 다중 워커는 같이 못 쓴다

uvicorn 이 둘을 함께 받으면 한쪽을 조용히 버린다. 여기서 갈라서 준다.

## 개발에서는 MCP 서버도 함께 띄운다

MCP 서버(`mcp_server/server.py`)는 별도 venv·별도 프로세스라 따로 띄워야 하는데, 매번
두 터미널에서 두 명령을 치는 것은 곧 안 하게 된다 — 그러면 Claude 가 「연결 실패」 만
보고 이유는 모른다. 그래서 개발 모드는 `mcp_server/venv` 가 있으면 자식 프로세스로 함께
띄우고(포트 +2, 백엔드는 이 프로세스의 개발 포트로), 이 프로세스가 끝날 때 같이 내린다.
`MCP_DEV=0` 이면 안 띄운다. **운영에서는 하지 않는다** — 거기는 systemd 유닛
(`<slug>-mcp`)이 따로 띄운다.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import uvicorn

from app.config import get_settings

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp_server"


def _start_mcp(api_port: int, mcp_port: int) -> subprocess.Popen[bytes] | None:
    """개발용 MCP 서버를 자식으로. 못 띄우는 이유는 **말하고** 건너뛴다 — 조용히 빠지면
    「Claude 가 안 붙는다」 를 여기서 찾을 사람이 없다."""
    if os.environ.get("MCP_DEV") == "0":
        return None
    python = MCP_DIR / "venv" / "bin" / "python"
    server = MCP_DIR / "server.py"
    if not python.exists() or not server.exists():
        print(
            f"MCP 서버는 안 띄웁니다 — {python} 이 없습니다. (cd mcp_server && "
            "python3 -m venv venv && ./venv/bin/pip install -r requirements.txt)",
            file=sys.stderr,
        )
        return None
    if _answering("127.0.0.1", mcp_port):
        print(
            f"MCP 서버는 안 띄웁니다 — {mcp_port} 포트에 이미 응답하는 것이 있습니다.",
            file=sys.stderr,
        )
        return None
    env = {
        **os.environ,
        "PLATFORM_API_BASE": f"http://127.0.0.1:{api_port}",
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(mcp_port),
    }
    child = subprocess.Popen([str(python), str(server)], cwd=MCP_DIR, env=env)
    print(f"MCP 서버: http://127.0.0.1:{mcp_port}/mcp → 백엔드 http://127.0.0.1:{api_port}")
    return child


def _start_worker() -> subprocess.Popen[bytes] | None:
    """개발용 작업 워커를 자식으로. **없으면 일괄 입력가 영영 「대기」 다** — 그리고
    그 사실은 화면의 「워커가 살아 있지 않습니다」 로만 드러난다. `WORKER_DEV=0` 이면 안
    띄운다."""
    if os.environ.get("WORKER_DEV") == "0":
        return None
    child = subprocess.Popen(
        [sys.executable, "-m", "app.worker"], cwd=Path(__file__).resolve().parent
    )
    print("작업 워커: 일괄 입력를 뒤에서 돌립니다 (python -m app.worker)")
    return child


def _stop(child: subprocess.Popen[bytes] | None) -> None:
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()


def _answering(host: str, port: int) -> bool:
    """그 포트에 이미 응답하는 것이 있나.

    **바인딩을 시도해 보는 것으로는 부족하다.** `SO_REUSEADDR` 때문에 TIME_WAIT
    소켓 위에도 바인딩이 성공한다. 실제로 붙어 본다.
    """
    target = "127.0.0.1" if host in ("0.0.0.0", "") else host
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex((target, port)) == 0


def main() -> None:
    settings = get_settings()
    development = settings.app_env == "development"
    port = settings.port + 1 if development else settings.port

    # **운영에서는 검사하지 않는다.** systemd 가 재시작할 때 옛 프로세스의 소켓이
    # 아직 닫히는 중일 수 있는데, 거기서 기동을 거부하면 서비스가 영영 안 올라온다.
    # 그 자리는 systemd 의 Restart 가 맡는다.
    if development and _answering(settings.host, port):
        raise SystemExit(
            f"{port} 포트에 이미 응답하는 서버가 있습니다. 그대로 띄우면 둘이 번갈아\n"
            f"답해서, 새로 만든 API 가 404 로 오는데 코드에는 있는 상태가 됩니다.\n"
            f"\n"
            f"  ss -ltnp 'sport = :{port}'\n"
            f"\n"
            f"으로 잡고 있는 프로세스를 찾아 내린 뒤 다시 돌리세요.\n"
            f"systemd 로 도는 것이면 그 출력에 유닛 이름이 함께 나옵니다."
        )

    if development:
        mcp = _start_mcp(port, settings.port + 2)
        worker = _start_worker()
        try:
            uvicorn.run("app.main:app", host=settings.host, port=port, reload=True)
        finally:
            _stop(mcp)
            _stop(worker)
    else:
        # **앱을 문자열로 넘긴다.** 다중 워커는 uvicorn 이 프로세스를 새로 띄워
        # 앱을 다시 import 하므로, 객체를 넘기면 그 프로세스가 그것을 못 만든다.
        uvicorn.run(
            "app.main:app",
            host=settings.host,
            port=port,
            workers=settings.uvicorn_workers,
            # 앞에 nginx 가 있으면(TRUST_PROXY) 그것이 준 클라이언트 주소 · 스킴을 믿는다.
            # 없으면 아무나 헤더를 위조할 수 있으니 안 믿는다.
            proxy_headers=settings.trust_proxy,
            forwarded_allow_ips="*" if settings.trust_proxy else None,
        )


if __name__ == "__main__":
    main()
