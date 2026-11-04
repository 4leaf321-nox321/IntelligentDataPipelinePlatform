"""테스트 공통 준비.

**개발 DB 를 건드리지 않는다.** 접속 정보는 개발 .env 에서 읽되 데이터베이스
이름만 `<이름>_test` 로 바꿔 쓴다 — 비밀번호를 두 곳에 적으면 한쪽만 고쳐지고,
그때 시험이 도는 곳이 어디인지 아무도 모른다.

**스위트를 두 번 동시에 띄우지 않는다.** 둘 다 같은 시험 DB 를 비우므로 서로의
데이터를 지운다.

**병렬(`-n`, pytest-xdist)이면 작업자마다 DB 를 따로 쓴다**(`<이름>_gw0_test` …) — 처음이면
만든다. 같은 DB 를 둘이 쓰면 맨 앞의 스키마 비우기가 서로의 표를 지운다. 첨부 · 로그
폴더도 작업자마다 임시 폴더로 — 앱 기동 검사가 같은 탐침 파일을 만들고 지워서 엇갈린다.

bcrypt 라운드를 낮춘다. 시험 하나가 계정을 만들고(해시) 로그인하므로(검증),
운영 라운드 그대로면 그 시간은 인증 로직이 아니라 **bcrypt 의 설계 목적**을 재는
데 쓰인다 — 시험이 보려는 것이 아니다. 검사하는 것은 그대로다: 같은 알고리즘,
같은 전처리, 같은 경로.
"""

from __future__ import annotations

import atexit
import fcntl
import os
import shutil
import tempfile

from sqlalchemy.engine import make_url

#: pytest-xdist 의 작업자 이름(`gw0` …) — 병렬이 아니면 빈 값.
WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")

os.environ.setdefault("APP_BCRYPT_ROUNDS", "4")
# 정제 도구 키트의 **이 PC 설정**(등록한 플랫폼 · 토큰)을 시험이 읽지 않게 — 없는 자리를
# 가리킨다. 필요한 시험은 제 임시 파일로 바꾼다(`SP_SETTINGS`).
os.environ["SP_SETTINGS"] = os.path.join(os.devnull, "sp-pipeline-settings.json")
# 설정을 처음 읽기 **전에** — 앱의 설정은 한 번 읽고 기억한다.
if WORKER:
    _scratch = tempfile.mkdtemp(prefix=f"sp-pytest-{WORKER}-")
    atexit.register(shutil.rmtree, _scratch, ignore_errors=True)
    os.environ["FILESTORE_DIR"] = os.path.join(_scratch, "filestore")
    os.environ["LOG_DIR"] = os.path.join(_scratch, "logs")


def _test_database_url() -> str:
    """개발 접속 정보에서 시험 DB 주소를 만든다.

    `APP_TEST_DATABASE_URL` 을 직접 주면 그것을 그대로 쓴다(CI 가 그렇게 한다).
    안 주면 .env 의 값에서 **데이터베이스 이름만** 바꾼다.
    """
    explicit = os.environ.get("APP_TEST_DATABASE_URL")
    if explicit:
        return explicit

    # .env 를 읽는 경로는 앱과 같아야 한다 — 여기서 따로 파싱하면 BOM 처리 같은
    # 사정이 갈린다.
    from app.config import Settings

    url = Settings().database_url
    base, _, name = url.rpartition("/")
    return f"{base}/{name}_test" if base else url


def _for_worker(url: str) -> str:
    """병렬이면 작업자의 DB 이름으로 — `<이름>_test` → `<이름>_gw0_test`(끝은 `_test` 그대로라
    아래의 개발 DB 가드가 같게 선다)."""
    if not WORKER:
        return url
    parsed = make_url(url)
    name = parsed.database or ""
    stem = name.removesuffix("_test")
    return parsed.set(database=f"{stem}_{WORKER}_test").render_as_string(hide_password=False)


# **시험 DB 주소는 주 프로세스가 한 번 정해 작업자에게 넘긴다**(`APP_TEST_DATABASE_URL`).
# 작업자는 주 프로세스가 이미 `<이름>_test` 로 바꾼 DATABASE_URL 을 물려받는다 — 거기서 다시
# 지으면 `_test` 가 두 번 붙었다(`<이름>_test_gw0_test`, 실측).
os.environ.setdefault("APP_TEST_DATABASE_URL", _test_database_url())
os.environ["DATABASE_URL"] = _for_worker(os.environ["APP_TEST_DATABASE_URL"])

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.all_models  # noqa: F401,E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402


def _ensure_database(name: str) -> None:
    """시험 DB 가 없으면 만든다 — 병렬 작업자의 DB 는 처음 돌 때 없다. 같은 접속 정보로
    관리 DB(`postgres`)에 붙어서. 있으면 아무것도 안 한다(만들 권한이 없어도 된다).

    **틀(`<이름>_template`)이 있으면 그것에서** — pgvector 확장은 postgres 계정만
    만들어(ADR 0050), 앱 계정이 만든 빈 DB 에는 없다. 개발 PC 는 `scripts/dev_pgvector.sh` 가
    확장을 든 틀을 만든다. CI 는 틀이 없고, superuser 라 표를 세울 때 스스로
    만든다(`vectors.ensure`)."""
    stem = (make_url(os.environ["APP_TEST_DATABASE_URL"]).database or "").removesuffix("_test")
    admin = create_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            known = {
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT datname FROM pg_database WHERE datname IN (:name, :template)"
                    ),
                    {"name": name, "template": f"{stem}_template"},
                )
            }
            if name not in known:
                template = (
                    f' TEMPLATE "{stem}_template"' if f"{stem}_template" in known else ""
                )
                connection.execute(text(f'CREATE DATABASE "{name}"{template}'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def schema() -> None:
    """표를 만들고 시작한다.

    마이그레이션을 돌리지 않는 이유: 시험이 보려는 것은 **지금 코드의 모델**이다.
    마이그레이션이 모델과 어긋났는지는 `alembic check` 가 본다.

    **스키마를 통째로 지운다.** `metadata.drop_all` 은 지금 모델이 아는 표만
    지우는데, 지난 판이 남긴 표가 옛 외래키로 그 표들을 붙들고 있으면 거기서
    막힌다 — 그리고 그 실패는 "테이블을 지울 수 없음" 이라는, 원인이 안 적힌
    말로 온다.
    """
    name = engine.url.database or ""
    if not name.endswith("_test"):
        # **여기서 막지 않으면 개발 DB 가 통째로 날아간다.** DATABASE_URL 을
        # 잘못 준 날 그 사실을 알려 줄 자리는 여기뿐이다.
        raise RuntimeError(f"시험 DB 가 아닙니다: {name}. 이름이 _test 로 끝나야 합니다.")

    _ensure_database(name)
    # **작업자끼리 차례로 지우고 세운다.** 스키마를 통째로 지우는 트랜잭션은 표 · 색인 · 제약 ·
    # 형식마다 잠금을 하나씩 쥐어(실측 600개 남짓) 작업자 8개가 함께 하면 Postgres 의 잠금 표
    # (기본 64 곱하기 접속 100)가 넘친다 — 「out of shared memory」 로 시험 전부가 시작도 못
    # 했다(2026-10-30, 관계 유효 기간의 제약 · 트리거가 더해진 뒤). 같은 PC 의 작업자끼리
    # 줄을 세운다.
    stem = make_url(os.environ["APP_TEST_DATABASE_URL"]).database or "test"
    queue = os.path.join(tempfile.gettempdir(), f"sp-pytest-schema-{stem}.lock")
    with open(queue, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _request_timing_in_foreground() -> Iterator[None]:
    """요청 시간 기록을 **따로 도는 스레드 없이** — 스레드가 같은 엔진으로 제 차례에 넣으면
    엔진의 문장 수를 세는 시험에 그 문장이 섞여 가끔 떨어졌다(2026-10-10). 시험마다 앞 시험의
    줄을 버리고, 기록을 보는 시험은 `writer.flush()` 로 직접 넣는다."""
    from app.shared.request_timing import writer

    writer.background = False
    writer.discard()
    yield
    writer.discard()


@pytest.fixture
def db() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    """진짜 앱을 부른다.

    접근 로그 미들웨어가 요청 처리 **밖에서** DB 를 쓰므로, 그 세션 공장도 시험
    DB 를 가리켜야 한다 — 안 그러면 접근 로그만 개발 DB 에 쌓인다.
    """
    fastapi_app.state.session_factory = SessionLocal
    with TestClient(fastapi_app) as test_client:
        yield test_client
