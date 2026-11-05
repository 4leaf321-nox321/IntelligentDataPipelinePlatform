"""백업 · 복구가 **중계 폴더**도 받는다 — 정의 파일과 작업 폴더의 이어 돌 몫, 덤프와 같은 시각.

운영자가 `pipelines/` 에 둔 정의 파일은 DB 에 없다. 작업 폴더의 기준 출력을 잃으면 다음 실행이
첫 적재처럼 전부 보내고 대조의 기준 · 대기 계획 · 자동 적용의 이력이 사라진다. 지난 실행
(`runs/` · `sends/`)은 기준 하나만 — 나머지는 쌓이기만 하는 기록이다.

DB 도구(pg_dump · psql · pg_restore)는 가짜다 — 여기서 보는 것은 스크립트가 무엇을 담고
되돌리나.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
from pathlib import Path

from tests.architecture.test_deploy import DEPLOY, _no_bash, _stub

BASELINE = "runs/20261105T010000Z"
OLD_RUN = "runs/20261104T010000Z"
ENV = "DATABASE_URL=postgresql+psycopg://relay:pw@127.0.0.1:5432/datarelay\n"


def _relay(root: Path) -> Path:
    relay = root / "relay"
    (relay / "pipelines").mkdir(parents=True)
    (relay / "pipelines" / "plm-model.json").write_text("{}", encoding="utf-8")
    (relay / "pipelines" / "plm-model.table.json").write_text("{}", encoding="utf-8")
    work = relay / "work" / "plm-model"
    for run in (BASELINE, OLD_RUN, "sends/20261105T010000Z"):
        (work / run).mkdir(parents=True)
        (work / run / "objects.json").write_text("[]", encoding="utf-8")
    state = {"watermark": "2026-11-05T00:00:00Z", "applied_run": BASELINE, "pending": None}
    (work / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    for name in ("mirror.json", "reconcile.json", "alerts.json"):
        (work / name).write_text("{}", encoding="utf-8")
    (work / "mirror.json.tmp").write_text("반쪽", encoding="utf-8")  # 쓰다 만 것
    (relay / "drop").mkdir()
    (relay / "drop" / "lots.csv").write_text("K\n", encoding="utf-8")  # 원천이 다시 준다
    return relay


def _tools(root: Path) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    # pg_dump --file=<덤프> 만 흉내 — 받을 곳에 무언가를 쓴다.
    _stub(
        bin_dir / "pg_dump",
        'for a in "$@"; do case "$a" in --file=*) echo dump > "${a#--file=}" ;; esac; done\n',
    )
    _stub(bin_dir / "pg_restore", "exit 0\n")
    # 표 개수만 답한다(복구 판정) — 나머지 질의는 빈 답(DB 없음 · 경로 칸 없음).
    _stub(bin_dir / "psql", 'case "$*" in *"count(*)"*) echo 3 ;; esac\n')
    return bin_dir


def _bash(bin_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shutil.which("bash") or "bash", *args],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
    )


@_no_bash
def test_백업은_정의와_작업_폴더의_이어_돌_몫을_받고_복구가_되돌린다(tmp_path: Path) -> None:
    install = tmp_path / "install"
    install.mkdir()
    (install / ".env").write_text(ENV, encoding="utf-8")
    relay = _relay(install)
    bin_dir = _tools(tmp_path)
    backup = tmp_path / "backup"

    done = _bash(bin_dir, str(DEPLOY / "backup.sh"), "-i", str(install), "-o", str(backup))
    assert done.returncode == 0, done.stderr
    (tarball,) = (backup / "relay").glob("relay-*.tar.gz")
    (dump,) = (backup / "db").glob("db-*.dump")
    assert tarball.name.removeprefix("relay-").removesuffix(".tar.gz") == dump.stem[3:]
    with tarfile.open(tarball) as opened:
        names = sorted(opened.getnames())
    work = "work/plm-model"
    assert names == [
        "pipelines/plm-model.json",
        "pipelines/plm-model.table.json",
        f"{work}/alerts.json",
        f"{work}/mirror.json",
        f"{work}/reconcile.json",
        f"{work}/{BASELINE}/objects.json",
        f"{work}/state.json",
    ]  # 지난 실행 · 보낸 묶음 · 쓰다 만 파일 · drop/ 은 안 담는다
    assert "중계 7 개" in done.stdout

    # 확인용 복구 — 중계 폴더는 건드리지 않고 묶음이 읽히는지만.
    check = _bash(bin_dir, str(DEPLOY / "restore.sh"), "-b", str(backup), "-d", "check_db")
    assert check.returncode == 0, check.stderr
    assert "중계 묶음이 읽힙니다(파일 7 개)" in check.stdout

    # 서버를 잃었다 — 새 자리에 되돌린다.
    shutil.rmtree(relay)
    fresh = tmp_path / "new" / "relay"
    restore = (str(DEPLOY / "restore.sh"), "-b", str(backup), "-d", "check_db")
    restored = _bash(bin_dir, *restore, "-R", str(fresh))
    assert restored.returncode == 0, restored.stderr
    state = json.loads((fresh / work / "state.json").read_text(encoding="utf-8"))
    assert (fresh / work / state["applied_run"] / "objects.json").is_file()
    assert (fresh / "pipelines" / "plm-model.json").is_file()


@_no_bash
def test_중계_묶음은_덤프와_같은_세대만_남는다(tmp_path: Path) -> None:
    install = tmp_path / "install"
    install.mkdir()
    (install / ".env").write_text(ENV, encoding="utf-8")
    _relay(install)
    bin_dir = _tools(tmp_path)
    backup = tmp_path / "backup"
    (backup / "relay").mkdir(parents=True)
    orphan = backup / "relay" / "relay-20200101-030000.tar.gz"  # 짝인 덤프가 이미 지워졌다
    orphan.write_bytes(b"")

    done = _bash(bin_dir, str(DEPLOY / "backup.sh"), "-i", str(install), "-o", str(backup))
    assert done.returncode == 0, done.stderr
    assert not orphan.exists()
    assert len(list((backup / "relay").glob("relay-*.tar.gz"))) == 1


def _no_createdb(root: Path, host: str = "127.0.0.1") -> tuple[Path, Path, Path]:
    """앱 계정에 CREATEDB 가 없는 운영 설치 — prepare 가 그렇게 만든다."""
    install = root / "install"
    install.mkdir()
    (install / ".env").write_text(ENV.replace("127.0.0.1", host), encoding="utf-8")
    bin_dir = _tools(root)
    _stub(
        bin_dir / "psql",
        'case "$*" in\n'
        '  *"CREATE DATABASE"*)\n'
        '    echo "ERROR:  permission denied to create database" >&2; exit 1 ;;\n'
        '  *"count(*)"*) echo 3 ;;\n'
        "esac\n",
    )
    calls = root / "sudo.log"
    _stub(bin_dir / "sudo", f'echo "$*" >> "{calls}"\n')
    backup = root / "backup"
    done = _bash(bin_dir, str(DEPLOY / "backup.sh"), "-i", str(install), "-o", str(backup))
    assert done.returncode == 0, done.stderr
    return bin_dir, backup, calls


@_no_bash
def test_앱_계정이_DB_를_못_만들면_postgres_계정으로_만들고_주인을_넘긴다(
    tmp_path: Path,
) -> None:
    """문서가 권하는 「확인용 DB 로 복원」 이 운영 설치에서 「permission denied to create
    database」 로 멈췄다(2026-11-06 리허설) — 앱 계정의 권한은 넓히지 않는다."""
    bin_dir, backup, calls = _no_createdb(tmp_path)
    done = _bash(bin_dir, str(DEPLOY / "restore.sh"), "-b", str(backup), "-d", "check_db")
    assert done.returncode == 0, done.stderr
    said = calls.read_text(encoding="utf-8")
    assert "-u postgres psql" in said and 'OWNER "relay"' in said and '"check_db"' in said


@_no_bash
def test_DB_가_다른_서버면_거기서_할_일을_말한다(tmp_path: Path) -> None:
    bin_dir, backup, calls = _no_createdb(tmp_path, host="10.0.0.11")
    done = _bash(bin_dir, str(DEPLOY / "restore.sh"), "-b", str(backup), "-d", "check_db")
    assert done.returncode != 0
    assert "sudo -u postgres createdb -O relay check_db" in done.stderr
    assert not calls.exists()  # 남의 서버 DB 를 이 서버의 postgres 로 만들지 않는다


@_no_bash
def test_백업_목록은_이상한_이름_밖을_가리키는_기준에_흔들리지_않는다(tmp_path: Path) -> None:
    """줄바꿈이 든 파일 이름이 목록을 쪼개 다른 파일을 끼우지 않고, 상태 파일의
    `applied_run` 이 `../..` 면 중계 폴더 밖을 담지 않는다(2026-11-06 보안 검토)."""
    install = tmp_path / "install"
    install.mkdir()
    (install / ".env").write_text(ENV, encoding="utf-8")
    relay = _relay(install)
    (relay / "pipelines" / "odd\n.env").write_text("{}", encoding="utf-8")
    outside = install / "secret"
    outside.mkdir()
    (outside / "key.txt").write_text("TOP-SECRET", encoding="utf-8")
    sneaky = relay / "work" / "sneaky"
    sneaky.mkdir(parents=True)
    state = {"applied_run": "../../../secret", "pending": None}
    (sneaky / "state.json").write_text(json.dumps(state), encoding="utf-8")
    bin_dir = _tools(tmp_path)
    backup = tmp_path / "backup"

    done = _bash(bin_dir, str(DEPLOY / "backup.sh"), "-i", str(install), "-o", str(backup))
    assert done.returncode == 0, done.stderr
    (tarball,) = (backup / "relay").glob("relay-*.tar.gz")
    with tarfile.open(tarball) as opened:
        names = opened.getnames()
    assert "pipelines/odd\n.env" in names and "pipelines/odd" not in names
    assert not any("secret" in one or ".." in one for one in names)
    assert "work/sneaky/state.json" in names


@_no_bash
def test_복구할_DB_이름은_SQL_에_끼울_수_있는_글자만(tmp_path: Path) -> None:
    install = tmp_path / "install"
    install.mkdir()
    (install / ".env").write_text(ENV, encoding="utf-8")
    _relay(install)
    bin_dir = _tools(tmp_path)
    backup = tmp_path / "backup"
    done = _bash(bin_dir, str(DEPLOY / "backup.sh"), "-i", str(install), "-o", str(backup))
    assert done.returncode == 0, done.stderr
    for bad in ('x" ENCODING', "x';--", "1abc", "a b"):
        got = _bash(bin_dir, str(DEPLOY / "restore.sh"), "-b", str(backup), "-d", bad)
        assert got.returncode != 0 and "DB 이름은" in got.stderr, (bad, got.stderr)
