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
