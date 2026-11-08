"""화면 끝까지 시험(E2E) — 진짜 브라우저(Playwright · Chromium)로 화면을 누른다.

    backend/.venv/bin/python devtools/e2e/run.py             # 화면 빌드부터
    backend/.venv/bin/python devtools/e2e/run.py --no-build  # 빌드한 frontend/dist 로
    backend/.venv/bin/python devtools/e2e/run.py --keep      # 끝나도 시험 DB 를 남긴다

**개발 DB 와 개발 서버를 건드리지 않는다.** 모든 것을 따로 세우고 끝나면 내린다:

    DB          datarelay_e2e — backend/.env 의 DATABASE_URL 에서 이름만 바꾼다(매번 새로)
    relay API   127.0.0.1:8095 — 빌드한 화면을 함께 서빙
    샘플 PLM    127.0.0.1:8096 — 데이터는 .work/e2e/mock_plm.json
    정의 · 작업 .work/e2e/pipelines · .work/e2e/relay
    관리자      e2e-admin — 비밀번호는 매번 무작위, .work/e2e/admin.json(0600)에만

대상 SP 는 세우지 않는다 — 대상에 닿아야 하는 것(실행 · 적용 · 미해결 작업함)은
`devtools/README.md` 의 한 바퀴가 본다. 여기서는 화면의 길(로그인 · 지도 · 정의 편집 · 정의
시험의 원천 쪽 · 새 연결의 탐색 · 정의 저장)을 본다. 화면 사진은 `.work/e2e/shots/`.

브라우저는 `.work/e2e/venv` 의 Playwright(`requirements.txt`)다 — 없으면 만든다. Playwright 가
기대하는 Chromium 이 캐시(`~/.cache/ms-playwright`)에 없으면 캐시의 가장 새 headless shell 로
띄운다(`E2E_CHROME` 으로 직접 줄 수도 있다).

내릴 때는 **이 스크립트가 띄운 프로세스만**(Popen 이 쥔 PID) 끈다.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
FRONTEND = REPO / "frontend"
WORK = REPO / ".work" / "e2e"
HERE = Path(__file__).resolve().parent
DB = "datarelay_e2e"
API_PORT = 8095
MOCK_PORT = 8096
MOCK_TOKEN = "mock-plm-token"
ADMIN = "e2e-admin"

sys.path.insert(0, str(BACKEND))


def say(text: str) -> None:
    print(f"[e2e] {text}", flush=True)


def busy(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def database_url() -> tuple[str, str]:
    """개발 DB 주소에서 이름만 바꾼 것과 개발 DB 의 이름 — 비밀번호는 화면에 내지 않는다."""
    from sqlalchemy.engine import make_url

    from app.config import get_settings

    base = make_url(get_settings().database_url)
    if base.database == DB:
        raise SystemExit(
            "DATABASE_URL 이 이미 시험 DB 를 가리킵니다 — backend/.env 를 확인하세요."
        )
    return base.set(database=DB).render_as_string(hide_password=False), base.database or ""


def fresh_database(url: str, stem: str) -> None:
    """새로 — 개발 DB 에 `<이름>_template`(pgvector 를 깐 빈 DB)이 있으면 그것에서."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    admin = create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name"
                ),
                {"name": DB},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{DB}"'))
            template = f"{stem}_template" if stem else ""
            known = (
                template
                and connection.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": template}
                ).first()
            )
            connection.execute(
                text(f'CREATE DATABASE "{DB}"' + (f' TEMPLATE "{template}"' if known else ""))
            )
    finally:
        admin.dispose()


def drop_database(url: str) -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    admin = create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name"
                ),
                {"name": DB},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{DB}"'))
    finally:
        admin.dispose()


def private(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)


def browser_venv() -> Path:
    python = WORK / "venv" / "bin" / "python"
    if not python.exists():
        say("Playwright 환경을 만듭니다(.work/e2e/venv)")
        subprocess.run([sys.executable, "-m", "venv", str(WORK / "venv")], check=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-q", "-r", str(HERE / "requirements.txt")],
            check=True,
        )
    return python


#: 브라우저가 쓰는 시스템 라이브러리 — PC 에 없으면(sudo 없이는 apt 로 못 깐다) 패키지만
#: 내려받아 `.work/e2e/libroot` 에 풀고 브라우저에 `LD_LIBRARY_PATH` 로 준다(Ubuntu 24.04).
BROWSER_DEBS = ("libnspr4", "libnss3", "libasound2t64")
BROWSER_SONAMES = ("libnspr4.so", "libnss3.so", "libasound.so.2")


def browser_libs() -> str:
    known = subprocess.run(
        ["ldconfig", "-p"], capture_output=True, text=True, check=False
    ).stdout
    if all(name in known for name in BROWSER_SONAMES):
        return ""
    root = WORK / "libroot"
    lib = root / "usr" / "lib" / "x86_64-linux-gnu"
    if not all((lib / name).exists() for name in BROWSER_SONAMES):
        say(f"브라우저의 라이브러리를 .work/e2e/libroot 에 풉니다({' · '.join(BROWSER_DEBS)})")
        debs = WORK / "debs"
        debs.mkdir(exist_ok=True)
        subprocess.run(
            ["apt-get", "download", *BROWSER_DEBS],
            cwd=debs,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        for deb in sorted(debs.glob("*.deb")):
            subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
    return str(lib)


def wait_for(url: str, seconds: float = 40) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as reply:  # 127.0.0.1 만
                if reply.status < 500:
                    return
        except OSError:
            time.sleep(0.4)
    raise SystemExit(
        f"{url} 이 {seconds:.0f}초 안에 뜨지 않았습니다 — .work/e2e/*.log 를 보세요."
    )


def pipelines() -> Path:
    """샘플 PLM 의 정의(OData) 사본 — 주소만 시험의 샘플 PLM(8096)으로."""
    folder = WORK / "pipelines"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    body = json.loads((REPO / "devtools/mock_plm/pipeline.json").read_text(encoding="utf-8"))
    body["adapter"]["service_url"] = f"http://127.0.0.1:{MOCK_PORT}/odata/v4/plm"
    (folder / "pipeline.json").write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    shutil.copy(REPO / "devtools/mock_plm/recipe.table.json", folder / "recipe.table.json")
    return folder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-build", action="store_true", help="frontend/dist 를 그대로 쓴다")
    parser.add_argument("--keep", action="store_true", help="끝나도 시험 DB 를 남긴다")
    parser.add_argument("--headed", action="store_true", help="브라우저 창을 띄운다")
    args = parser.parse_args()

    for port in (API_PORT, MOCK_PORT):
        if busy(port):
            raise SystemExit(f"127.0.0.1:{port} 을 이미 누가 씁니다 — 비우고 다시 하세요.")
    WORK.mkdir(parents=True, exist_ok=True)
    python = browser_venv()
    if not args.no_build:
        say("화면을 빌드합니다(npm run build)")
        subprocess.run(
            ["npm", "run", "build"], cwd=FRONTEND, check=True, stdout=subprocess.DEVNULL
        )
    if not (FRONTEND / "dist" / "index.html").exists():
        raise SystemExit("frontend/dist 가 없습니다 — --no-build 없이 다시 하세요.")

    url, stem = database_url()
    say(f"시험 DB {DB} 를 새로 만듭니다")
    fresh_database(url, stem)
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "RELAY_PIPELINES_DIR": str(pipelines()),
        "RELAY_WORK_DIR": str(WORK / "relay"),
        "MOCK_PLM_TOKEN": MOCK_TOKEN,
        "MOCK_PLM_DATA": str(WORK / "mock_plm.json"),
        "RELAY_TARGET_TOKEN": "e2e-no-target",
    }
    shutil.rmtree(WORK / "relay", ignore_errors=True)
    (WORK / "mock_plm.json").unlink(missing_ok=True)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    password = secrets.token_urlsafe(18)
    # 뿌리 부서 · 관리자(seed_install), 그다음 강제 변경 없이(set_admin). 실패해도 명령 줄
    # (비밀번호)을 찍지 않는다.
    for script in ("scripts/seed_install.py", "scripts/set_admin.py"):
        done = subprocess.run(
            [sys.executable, script, "--email", ADMIN, "--password", password],
            cwd=BACKEND,
            env=env,
            check=False,
            capture_output=True,
        )
        if done.returncode:
            tail = done.stdout.decode(errors="replace").strip().splitlines()[-1:]
            raise SystemExit(f"{script} 가 실패했습니다: {' '.join(tail)}")
    private(WORK / "admin.json", json.dumps({"email": ADMIN, "password": password}))

    started: list[subprocess.Popen[bytes]] = []
    code = 1
    try:
        with (WORK / "mock.log").open("wb") as log:
            started.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "mock_plm.app:app",
                        "--app-dir",
                        str(REPO / "devtools"),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(MOCK_PORT),
                    ],
                    cwd=REPO,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
        with (WORK / "api.log").open("wb") as log:
            started.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "app.main:app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(API_PORT),
                    ],
                    cwd=BACKEND,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
        wait_for(f"http://127.0.0.1:{MOCK_PORT}/_mock/state")
        wait_for(f"http://127.0.0.1:{API_PORT}/api/health")
        say(f"API {API_PORT} · 샘플 PLM {MOCK_PORT} 가 떴습니다 — 브라우저로 누릅니다")
        shots = WORK / "shots"
        shutil.rmtree(shots, ignore_errors=True)
        shots.mkdir()
        scenario_env = {
            **os.environ,
            "E2E_BASE": f"http://127.0.0.1:{API_PORT}",
            "E2E_MOCK": f"http://127.0.0.1:{MOCK_PORT}",
            "E2E_ADMIN": str(WORK / "admin.json"),
            "E2E_SHOTS": str(shots),
            "E2E_HEADED": "1" if args.headed else "",
        }
        libs = browser_libs()
        if libs:
            scenario_env["LD_LIBRARY_PATH"] = ":".join(
                one for one in (libs, os.environ.get("LD_LIBRARY_PATH", "")) if one
            )
        code = subprocess.run(
            [str(python), str(HERE / "scenario.py")], env=scenario_env, check=False
        ).returncode
    finally:
        for one in reversed(started):
            one.terminate()
        for one in started:
            try:
                one.wait(timeout=10)
            except subprocess.TimeoutExpired:
                one.kill()
        if not args.keep:
            drop_database(url)
            say(f"시험 DB {DB} 를 지웠습니다")
    say(
        "통과"
        if code == 0
        else f"실패(종료 코드 {code}) — .work/e2e/shots · api.log 를 보세요"
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
