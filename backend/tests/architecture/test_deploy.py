"""배포 자산이 지키는 것들.

여기서 잡는 것은 전부 **돌려 보기 전에는 안 보인다.** 그리고 돌려 보는 자리는
대개 운영 서버다.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"
#: 개발자용 스크립트(밀기 전 점검 등). 배포 자산은 아니지만 **같은 함정을 만난다** —
#: Windows 체크아웃의 CRLF, 실행 비트, 엄격 모드.
TOOLS = REPO / "scripts"


def _scripts() -> list[Path]:
    found: list[Path] = []
    for folder in (DEPLOY, TOOLS):
        if folder.exists():
            found += folder.glob("*.sh")
    return sorted(found)


def test_셸_스크립트는_실행_가능하고_shebang_이_있다() -> None:
    """**tar 는 실행 비트를 보존한다.** 번들을 푼 서버에서 `./deploy.sh` 가
    「Permission denied」 로 막히면, 그것은 배포를 시작하기도 전의 벽이다.
    """
    for path in _scripts():
        first = path.read_bytes().split(b"\n", 1)[0]
        assert first.startswith(b"#!"), f"{path.name} 에 shebang 이 없습니다"
        assert b"bash" in first, f"{path.name} 은 bash 로 실행돼야 합니다: {first!r}"
        if os.name != "nt":  # pragma: no cover - Windows 체크아웃에서는 못 본다
            mode = path.stat().st_mode
            assert mode & stat.S_IXUSR, f"{path.name} 에 실행 비트가 없습니다"


def test_셸_스크립트는_엄격_모드로_돈다() -> None:
    """`set -euo pipefail` 이 없으면 **중간 단계가 실패해도 다음 줄이 그냥 돈다.**

    배포에서 그것은 「마이그레이션은 실패했는데 서비스는 새 코드로 재시작된」
    상태를 만들고, 화면은 500 을 내면서 원인을 말하지 않는다.
    """
    for path in _scripts():
        head = path.read_text(encoding="utf-8")[:2000]
        assert "set -euo pipefail" in head, f"{path.name} 에 set -euo pipefail 이 없습니다"


def test_배포_스크립트가_번들에_함께_담긴다() -> None:
    """**서버가 tar 하나만 받는 환경이어도 그다음 배포가 돌아야 한다.**

    빠뜨리면 첫 배포에 저장소를 클론하는 수밖에 없고, 폐쇄망에는 그 길이 없다.
    """
    builder = DEPLOY / "build_bundle.sh"
    if not builder.exists():  # pragma: no cover - 배포 자산을 뺀 설치
        return
    text = builder.read_text(encoding="utf-8")
    # 백업·복구가 여기 있는 이유: README 가 번들 안에서 `./backup.sh` 를 시킨다.
    # 없으면 그 사실은 **백업이 필요해진 날**에야 드러나고, SSH 로만 닿는 서버에서
    # 「저장소에서 마저 가져오기」 는 성립하지 않는다.
    for name in (
        "deploy.sh",
        "backup.sh",
        "restore.sh",
        "app.service.template",
        ".env.production.example",
        # MCP 서버는 SIF 에 못 들어간다(의존성 충돌). 소스와 유닛 템플릿, 그리고
        # get_guide 가 읽는 guide/ 가 번들에 있어야 운영 호스트에서 설 수 있다.
        "mcp.service.template",
        "sync.service.template",
        "sync.timer.template",
        # 지표 타이머(ADR 0013) — 밤마다 세어 두는 것이 번들에 없으면 지표가 조용히 낡는다.
        "metrics.service.template",
        "metrics.timer.template",
        "mcp_server/server.py",
        "mcp_server/requirements.txt",
        "mcp_server/guide",
        # HWAX 포털 연동 요청서의 틀 — `deploy.sh hwax-request` 가 서버에서 채운다(ADR 0028).
        "HWAX-연동요청서.template.md",
        # PostgreSQL 서버 설정 맞추기 — `deploy.sh pg-tune` 이 부른다(ADR 0031).
        "pg-tune.sh",
    ):
        assert name in text, f"{name} 이 번들에 안 담깁니다 (build_bundle.sh)"


def test_번들이_약속한_파일이_실제로_담긴다() -> None:
    """**README 가 번들 안에서 시키는 명령은 번들 안에 있어야 한다.**

    `README_OPERATOR.md` 는 번들에 함께 들어가 「`ls` 하면 이것들이 보인다」 고
    적는다. 그 목록과 `build_bundle.sh` 가 실제로 담는 것이 어긋나면, 운영자는
    **없는 스크립트를 치게 된다** — 그리고 그 자리는 대개 서버에 SSH 로 붙어 있는
    자리다.
    """
    builder = DEPLOY / "build_bundle.sh"
    readme = DEPLOY / "README_OPERATOR.md"
    if not builder.exists() or not readme.exists():  # pragma: no cover
        return

    built = builder.read_text(encoding="utf-8")
    promised = readme.read_text(encoding="utf-8").split("---", 1)[0]

    for name in (
        "deploy.sh",
        "backup.sh",
        "restore.sh",
        "app.sif",
        "BUILD_INFO",
        "mcp.service.template",
        "mcp_server",
    ):
        if name in promised:
            assert name in built or name == "app.sif", (
                f"README 는 번들에 {name} 이 있다고 적는데 build_bundle.sh 는 안 담습니다"
            )


def test_셸_스크립트에_CRLF_가_없다() -> None:
    """**줄바꿈 하나가 배포를 시작도 못 하게 만든다.**

    Windows 의 Git 은 `core.autocrlf=true` 가 기본이라, 그 기계에서 클론하면 셸
    스크립트가 CRLF 로 체크아웃된다. 그 파일이 리눅스 서버에 닿으면:

        -bash: ./deploy.sh: /usr/bin/env bash^M: bad interpreter: No such file or directory

    파일은 있고, 실행 비트도 있고, 눈으로 보면 멀쩡하다. `.gitattributes` 가
    막지만 **막혔는지 확인하는 자리**가 따로 있어야 한다 — 설정은 언젠가 빠진다.
    """
    for path in _scripts():
        assert b"\r\n" not in path.read_bytes(), (
            f"{path.name} 에 CRLF 가 있습니다. .gitattributes 를 확인하고 "
            "`dos2unix` 또는 재체크아웃 하세요 — 서버에서 bad interpreter 로 죽습니다"
        )


def test_줄바꿈을_저장소가_정한다() -> None:
    """`.gitattributes` 가 없으면 줄바꿈이 **클론한 사람의 설정**에서 정해진다.

    그러면 같은 커밋이 기계마다 다른 파일로 체크아웃되고, 그 차이는 리뷰에
    안 보인다.
    """
    attrs = REPO / ".gitattributes"
    assert attrs.exists(), ".gitattributes 가 없습니다 (Windows 클론에서 CRLF 가 섞입니다)"
    text = attrs.read_text(encoding="utf-8")
    assert "eol=lf" in text, ".gitattributes 가 LF 를 강제하지 않습니다"
    assert "*.sh" in text, ".gitattributes 가 셸 스크립트를 짚지 않습니다"


def test_태그를_밀면_번들이_나온다() -> None:
    """**번들을 만들 자리가 저장소 안에 있어야 한다.**

    `build_bundle.sh` 는 리눅스 · apptainer · npm 을 요구한다. 저장소를 받는
    기계가 Windows 라면 그 기계에서는 만들 수 없다 — Apptainer 는 Windows
    네이티브 빌드가 없다. 그 자리가 비어 있으면 **배포 경로가 통째로 끊긴다.**
    """
    release = REPO / ".github" / "workflows" / "release.yml"
    if not (REPO / ".github").exists():  # pragma: no cover - CI 자산을 뺀 포크
        return
    assert release.exists(), "태그 릴리스 워크플로가 없습니다 (.github/workflows/release.yml)"

    text = release.read_text(encoding="utf-8")
    assert "build_bundle.sh" in text, "릴리스가 번들을 안 만듭니다"
    assert "gh release create" in text, "릴리스가 산출물을 안 올립니다"
    # **검증을 통과해야 나간다.** 안 그러면 아무도 안 돌려 본 코드가 tar 로 나가고,
    # 깨졌다는 사실은 운영 서버에서 드러난다. 검증은 둘 중 하나다 — 같은 커밋에서 이미
    # 통과한 CI 를 쓰거나(`ci-passed`), 그것이 없으면 verify 가 전부 돌린다.
    assert "needs: [ci-passed, verify]" in text, "릴리스가 검증을 안 기다립니다"
    assert "if: needs.ci-passed.outputs.passed != 'true'" in text, (
        "verify 를 건너뛰는 조건이 바뀌었습니다 — 같은 커밋의 CI 가 통과했을 때만 건너뛴다"
    )
    # 건너뛴 verify 를 통과로 치는 것은 **그 커밋의 CI 가 통과로 끝났을 때만**이다.
    assert "needs.ci-passed.result == 'success'" in text
    assert "(needs.verify.result == 'success' || needs.verify.result == 'skipped')" in text
    assert "head_sha=${GITHUB_SHA}" in text and '" completed/success "' in text, (
        "다른 커밋이나 끝나지 않은 CI 를 통과로 읽습니다"
    )
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "workflow_call" in ci, "ci.yml 을 릴리스에서 불러 쓸 수 없습니다"


def test_번들이_자기가_무슨_플랫폼인지_말한다() -> None:
    """`deploy.sh` 가 DB 이름·포트·유닛 이름을 BUILD_INFO 에서 읽는다.

    배포 스크립트에 제품 이름을 박아 두면 포크할 때 바꿀 자리가 하나 더 늘고,
    **안 바꾸면 두 플랫폼이 같은 DB 와 같은 systemd 유닛을 쓴다** — 나중 배포가
    앞의 것을 그대로 지우고, 그 사실은 아무 데도 안 적힌다.
    """
    builder = DEPLOY / "build_bundle.sh"
    installer = DEPLOY / "deploy.sh"
    if not builder.exists() or not installer.exists():  # pragma: no cover
        return

    written = builder.read_text(encoding="utf-8")
    for key in ("app_name=", "app_slug=", "port=", "mcp_port=", "version="):
        assert key in written, f"build_bundle.sh 가 BUILD_INFO 에 {key} 를 안 씁니다"

    read = installer.read_text(encoding="utf-8")
    for key in ("app_name", "app_slug", "port", "mcp_port"):
        assert f"bundle {key}" in read, f"deploy.sh 가 {key} 를 안 읽습니다"


def test_MCP_유닛이_서버를_venv_로_띄운다() -> None:
    """MCP 서버는 **한 파일**이고 `venv/bin/python server.py` 로 그 자리에서 뜬다.

    `-m` 으로 패키지처럼 부르면 저장소 루트가 필요한데, 설치 폴더에는 그것이
    없다 — `ModuleNotFoundError` 가 나고, 그 오류는 무엇이 빠졌는지 말해 주지
    않는다. 유닛 템플릿과 deploy.sh 가 같은 자리를 가리키는지 여기서 본다.
    """
    unit = DEPLOY / "mcp.service.template"
    installer = DEPLOY / "deploy.sh"
    if not unit.exists() or not installer.exists():  # pragma: no cover
        return
    text = unit.read_text(encoding="utf-8")
    assert "mcp_server/venv/bin/python" in text and "mcp_server/server.py" in text
    assert "-m mcp_server" not in text
    for key in ("PLATFORM_API_BASE", "MCP_HOST", "MCP_PORT", "MCP_ALLOWED_HOSTS"):
        assert f"Environment={key}=" in text, f"유닛 템플릿에 {key} 가 없습니다"
    read = installer.read_text(encoding="utf-8")
    assert "mcp.service.template" in read and "setup_mcp" in read


def test_배포_자산에_플랫폼_이름을_박지_않는다() -> None:
    """포크할 때 바꿀 자리를 늘리지 않는다.

    문서(`README_OPERATOR.md`)는 예외다 — 거기서는 `<slug>` 자리표시자를 쓴다.
    검사하는 것은 **동작에 쓰이는 스크립트와 유닛 템플릿**이다.
    """
    from app.branding import DEFAULT_APP_NAME, DEFAULT_APP_SLUG

    targets = [*_scripts(), *DEPLOY.glob("*.template")]
    offenders = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        if DEFAULT_APP_SLUG in text or DEFAULT_APP_NAME in text:
            offenders.append(path.name)
    assert not offenders, (
        "배포 자산에 플랫폼 이름이 박혔습니다(포크하면 두 플랫폼이 겹칩니다): "
        + ", ".join(offenders)
    )


def test_bind_mount_대상이_이미지에_있다() -> None:
    """**마운트 대상은 이미지 안에 미리 있어야 한다.**

    없으면 마운트가 조용히 엉뚱한 데 붙거나 실패하고, 그 사실은 **파일을 쓰는
    순간**에야 드러난다 — 그리고 그 오류는 그 기능을 쓰는 사람에게만 보인다.
    """
    definition = DEPLOY / "apptainer.def"
    unit = DEPLOY / "app.service.template"
    if not definition.exists() or not unit.exists():  # pragma: no cover
        return

    def_text = definition.read_text(encoding="utf-8")
    unit_text = unit.read_text(encoding="utf-8")

    # 유닛이 --bind 로 넣는 컨테이너 쪽 경로를 모은다.
    wanted = set()
    for line in unit_text.splitlines():
        if "--bind" not in line:
            continue
        spec = line.split("--bind", 1)[1].strip().rstrip("\\").strip()
        parts = spec.split(":")
        if len(parts) >= 2:
            wanted.add(parts[1])

    for target in sorted(wanted):
        if target.endswith(".env"):
            # 파일 마운트는 이미지 안의 파일을 덮으므로 mkdir 대상이 아니다.
            continue
        assert "mkdir -p" in def_text and target in def_text, (
            f"{target} 이 apptainer.def 의 %post 에서 안 만들어집니다"
        )


# --- apptainer 를 까는 길 ------------------------------------------------------------
#
# apptainer 는 우분투 기본 저장소에 없다. `prepare` 가 PPA 없이 `apt-get install apptainer`
# 를 부르던 v0.1.0 은 새 서버에서 「Unable to locate package」 로 멈췄다 — 그리고 그것은
# **운영 서버에 SSH 로 붙어 있는 자리**에서만 드러난다. 그래서 그 길을 가짜 명령으로 돌려 본다.


def _piece(text: str, name: str) -> str:
    """deploy.sh 에서 함수 하나를 꺼낸다 — 스크립트 전체는 root·BUILD_INFO 를 요구한다."""
    one_line = re.search(rf"^{name}\(\)\s*\{{[^\n]*\}}$", text, re.M)
    if one_line:
        return one_line.group(0)
    # 여는 괄호 뒤의 주석(`cmd_service() {  # $1 = …`)도 받는다.
    block = re.search(rf"^{name}\(\)\s*\{{[^\n]*\n.*?^\}}$", text, re.M | re.S)
    assert block, f"deploy.sh 에 {name} 이 없습니다"
    return block.group(0)


def _stub(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _ensure_apptainer(
    tmp_path: Path,
    *,
    installed: bool = False,
    candidate: str = "(none)",
    os_id: str = "ubuntu",
    ppa_reachable: bool = True,
    bundled: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    constants = [
        line
        for line in text.splitlines()
        if line.startswith(("APPTAINER_PPA=", "APPTAINER_DOCS=", "APPTAINER_DEBS="))
    ]
    pieces = [
        _piece(text, name) for name in ("err", "info", "warn", "os_id", "ensure_apptainer")
    ]

    bin_dir = tmp_path / "bin"
    tools = tmp_path / "tools"
    bin_dir.mkdir()
    tools.mkdir()
    # **진짜 apptainer 가 PATH 에 섞이면 안 된다**(이 PC·CI 에는 깔려 있다).
    # 쓰는 도구만 옮긴다.
    for tool in ("sed", "grep", "tr", "chmod", "cat"):
        found = shutil.which(tool)
        assert found, f"{tool} 이 없습니다"
        (tools / tool).symlink_to(found)
    log = tmp_path / "calls.log"
    fake_apptainer = "#!/bin/sh\necho apptainer version 1.5.3\n"
    if installed:
        _stub(bin_dir / "apptainer", "echo apptainer version 1.5.3\n")
    _stub(
        bin_dir / "apt-cache",
        f'printf "apptainer:\\n  Installed: (none)\\n  Candidate: {candidate}\\n"\n',
    )
    # apt-get install ... apptainer 가 불리면 「깔린」 것으로 만든다.
    _stub(
        bin_dir / "apt-get",
        f'echo "apt-get $*" >> "{log}"\n'
        f'case "$*" in *" apptainer"*|*apptainer_*.deb*) '
        f"printf '{fake_apptainer}' > \"{bin_dir}/apptainer\"; "
        f'chmod +x "{bin_dir}/apptainer";; esac\n',
    )
    # 번들에 동봉한 .deb — 있으면 ensure_apptainer 가 이것을 1순위로 깐다.
    here = tmp_path / "bundle"
    here.mkdir()
    if bundled:
        (here / "apptainer_debs").mkdir()
        (here / "apptainer_debs" / "apptainer_1.5.3-1~noble_amd64.deb").write_bytes(b"deb")
    _stub(
        bin_dir / "add-apt-repository",
        f'echo "add-apt-repository $*" >> "{log}"\nexit {0 if ppa_reachable else 1}\n',
    )
    os_release = tmp_path / "os-release"
    os_release.write_text(
        f'NAME="Test"\nID={os_id}\nVERSION="99 (os-release)"\n', encoding="utf-8"
    )

    script = "set -euo pipefail\n" + "\n".join([*constants, *pieces]) + "\nensure_apptainer\n"
    done = subprocess.run(
        # PATH 를 가짜 명령 자리로 좁히므로 bash 자신은 절대 경로로 부른다.
        [shutil.which("bash") or "bash", "-c", script],
        env={"PATH": f"{bin_dir}:{tools}", "OS_RELEASE": str(os_release), "HERE": str(here)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, calls


_no_bash = pytest.mark.skipif(
    shutil.which("bash") is None or os.name == "nt", reason="bash 가 있는 리눅스에서만"
)


def test_prepare_는_apptainer_를_기본_저장소에서_바로_찾지_않는다() -> None:
    """패키지 목록에 apptainer 를 넣으면 새 우분투 서버에서 apt 가 통째로 실패한다."""
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    prepare = _piece(text, "cmd_prepare")
    installs = [line for line in prepare.splitlines() if "postgresql-contrib" in line]
    assert installs and all("apptainer" not in line for line in installs)
    assert "ensure_apptainer" in prepare
    assert "ppa:apptainer/ppa" in text


@_no_bash
def test_이미_깔려_있으면_아무것도_안_한다(tmp_path: Path) -> None:
    done, calls = _ensure_apptainer(tmp_path, installed=True)
    assert done.returncode == 0, done.stderr
    assert calls == []


@_no_bash
def test_번들에_deb_가_있으면_그것을_먼저_깐다(tmp_path: Path) -> None:
    """폐쇄망 — PPA 도 저장소도 안 닿는다. 사람이 .deb 를 구해 오지 않게 번들이 들고 간다."""
    done, calls = _ensure_apptainer(tmp_path, bundled=True, ppa_reachable=False)
    assert done.returncode == 0, done.stderr
    assert any("install" in one and "apptainer_debs/" in one for one in calls)
    assert not any(one.startswith("add-apt-repository") for one in calls)
    assert "apptainer version" in done.stdout


def test_번들이_apptainer_deb_를_동봉한다() -> None:
    """build_bundle.sh 가 빌드 머신에서 받아 넣고, deploy.sh 가 그 폴더를 본다."""
    builder = (DEPLOY / "build_bundle.sh").read_text(encoding="utf-8")
    installer = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    assert "apt-get download apptainer" in builder and "apptainer_debs" in builder
    assert "apptainer_debs" in _piece(installer, "ensure_apptainer")


@_no_bash
def test_우분투면_공식_PPA_를_더해_깐다(tmp_path: Path) -> None:
    done, calls = _ensure_apptainer(tmp_path)
    assert done.returncode == 0, done.stderr
    ppa = next(i for i, one in enumerate(calls) if one.startswith("add-apt-repository"))
    assert "ppa:apptainer/ppa" in calls[ppa]
    # PPA 를 더한 **뒤에** 깐다.
    assert any("install" in one and " apptainer" in one for one in calls[ppa + 1 :])
    assert "apptainer version" in done.stdout


@_no_bash
def test_닿는_저장소에_있으면_PPA_를_더하지_않는다(tmp_path: Path) -> None:
    """사내 미러에 이미 있는 것을 굳이 바깥 PPA 로 받으러 가지 않는다."""
    done, calls = _ensure_apptainer(tmp_path, candidate="1.5.3-1~noble")
    assert done.returncode == 0, done.stderr
    assert not any(one.startswith("add-apt-repository") for one in calls)
    assert any("install" in one and " apptainer" in one for one in calls)


@_no_bash
def test_PPA_에_닿지_않으면_할_일을_말하고_멈춘다(tmp_path: Path) -> None:
    """폐쇄망 — **조용히 넘어가지 않고**, 무엇을 먼저 깔아야 하는지 말한다."""
    done, calls = _ensure_apptainer(tmp_path, ppa_reachable=False)
    assert done.returncode != 0
    assert ".deb" in done.stderr and "prepare" in done.stderr
    assert not any("install" in one and " apptainer" in one for one in calls)


@_no_bash
def test_우분투가_아니면_PPA_를_쓰지_않고_설치_안내를_준다(tmp_path: Path) -> None:
    done, calls = _ensure_apptainer(tmp_path, os_id="debian")
    assert done.returncode != 0
    assert "apptainer.org" in done.stderr
    assert not any(one.startswith("add-apt-repository") for one in calls)


# --- 임시 파일 상한 권한(ADR 0048) ----------------------------------------------------
#
# 앱이 읽기 요청마다 `temp_file_limit` 를 걸려면 앱 계정에 권한이 있어야 한다(superuser 만
# 바꾸는 설정, PostgreSQL 15 부터 GRANT). 못 주는 서버(대기 · 15 아래 · DB 가 다른 곳)에서
# update 가 멈추면 안 된다 — 앱은 권한이 없으면 상한 없이 돈다.


def _grant_temp_limit(
    tmp_path: Path, *, recovery: bool = False, version: str | None = "160004"
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    ha = (DEPLOY / "ha.sh").read_text(encoding="utf-8")
    pieces = [_piece(text, name) for name in ("err", "info", "warn", "grant_temp_limit")]
    pieces.append(_piece(ha, "pg_in_recovery"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    answer_version = f"echo {version}" if version else "exit 2"
    _stub(
        bin_dir / "sudo",
        f'echo "$*" >> "{log}"\n'
        'case "$*" in\n'
        f"  *pg_is_in_recovery*) echo {'t' if recovery else 'f'} ;;\n"
        f"  *server_version_num*) {answer_version} ;;\n"
        "esac\n",
    )
    script = "set -euo pipefail\nDB_USER=plmhub\n" + "\n".join(pieces) + "\ngrant_temp_limit\n"
    done = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, calls


@_no_bash
def test_주_DB_면_앱_계정에_임시_파일_상한_권한을_준다(tmp_path: Path) -> None:
    done, calls = _grant_temp_limit(tmp_path)
    assert done.returncode == 0, done.stderr
    assert any('GRANT SET ON PARAMETER temp_file_limit TO "plmhub"' in one for one in calls)


@_no_bash
@pytest.mark.parametrize(
    ("recovery", "version", "said"),
    [(True, "160004", ""), (False, "140011", "15 아래"), (False, None, "붙지 못해")],
)
def test_권한을_못_주는_서버에서도_멈추지_않는다(
    tmp_path: Path, recovery: bool, version: str | None, said: str
) -> None:
    """대기 서버(주에서 준 것이 복제로 온다) · 15 아래 · DB 가 다른 서버."""
    done, calls = _grant_temp_limit(tmp_path, recovery=recovery, version=version)
    assert done.returncode == 0, done.stderr
    assert not any("GRANT" in one for one in calls)
    assert said in done.stderr


def test_update_와_prepare_가_권한을_주고_remove_는_거둔_뒤_역할을_지운다() -> None:
    """권한이 남은 역할은 DROP ROLE 이 거절한다 — remove 가 거기서 멈춘다."""
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    update = _piece(text, "cmd_update")
    assert update.index("run_migrations") < update.index("grant_temp_limit")
    assert update.index("grant_temp_limit") < update.index('systemctl start "$SERVICE_NAME"')
    assert "grant_temp_limit" in _piece(text, "cmd_prepare")
    remove = text[text.index('REVOKE SET ON PARAMETER temp_file_limit FROM "$DB_USER"') :]
    assert "DROP ROLE IF EXISTS" in remove[:400]


# --- 꺼 둔 워커(ADR 0049) ---------------------------------------------------------------
#
# 운영에서 지도 계산이 워커를 되풀이해 죽여 워커를 껐더니, update 가 다시 켰다(2026-11-02).


def _setup_worker(
    tmp_path: Path, *, had_unit: bool, state: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    pieces = [_piece(text, name) for name in ("err", "info", "warn", "setup_worker")]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    here = tmp_path / "bundle"
    here.mkdir()
    (here / "worker.service.template").write_text("[Unit]\n", encoding="utf-8")
    unit = tmp_path / "plm-worker.service"
    if had_unit:
        unit.write_text("[Unit]\n", encoding="utf-8")
    log = tmp_path / "calls.log"
    _stub(
        bin_dir / "systemctl",
        f'echo "systemctl $*" >> "{log}"\n'
        f'case "$1" in is-enabled) echo {state}; [ {state} = enabled ] ;; esac\n',
    )
    script = (
        "set -euo pipefail\n"
        f'HERE="{here}"\nWORKER_SERVICE_UNIT="{unit}"\nWORKER_SERVICE_NAME=plm-worker\n'
        'render_unit_paths() { cat "$1"; }\n' + "\n".join(pieces) + "\nsetup_worker\n"
    )
    done = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, calls


@_no_bash
def test_update_는_꺼_둔_워커를_켜지_않는다(tmp_path: Path) -> None:
    done, calls = _setup_worker(tmp_path, had_unit=True, state="disabled")
    assert done.returncode == 0, done.stderr
    assert not any(one.startswith(("systemctl enable", "systemctl restart")) for one in calls)
    assert "꺼 둔 상태라 켜지 않습니다" in done.stderr


@_no_bash
@pytest.mark.parametrize(("had_unit", "state"), [(True, "enabled"), (False, "disabled")])
def test_켜_둔_워커와_처음_설치는_켜고_다시_띄운다(
    tmp_path: Path, had_unit: bool, state: str
) -> None:
    """처음 설치는 유닛이 없어 `is-enabled` 가 disabled 라고 답한다 — 그것은 끈 것이 아니다."""
    done, calls = _setup_worker(tmp_path, had_unit=had_unit, state=state)
    assert done.returncode == 0, done.stderr
    assert "systemctl enable plm-worker" in calls
    assert "systemctl restart plm-worker" in calls


@_no_bash
@pytest.mark.parametrize(
    ("action", "state", "worker"),
    [
        ("restart", "disabled", False),
        ("start", "disabled", False),
        ("restart", "enabled", True),
        ("stop", "disabled", True),
    ],
)
def test_restart_도_꺼_둔_워커는_띄우지_않는다(
    tmp_path: Path, action: str, state: str, worker: bool
) -> None:
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    pieces = [
        _piece(text, name)
        for name in ("err", "info", "warn", "units_of", "cmd_service", "health_check")
    ]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "worker.service").write_text("", encoding="utf-8")
    log = tmp_path / "calls.log"
    _stub(
        bin_dir / "systemctl",
        f'echo "systemctl $*" >> "{log}"\n'
        f'case "$1" in is-enabled) echo {state}; [ {state} = enabled ] ;; esac\n',
    )
    _stub(bin_dir / "curl", "exit 0\n")
    script = (
        "set -euo pipefail\n"
        f'ETC="{etc}"\nSERVICE_NAME=plm\nWORKER_SERVICE_NAME=plm-worker\n'
        "WORKER_SERVICE_UNIT=/worker.service\nMCP_SERVICE_UNIT=/mcp.service\nAPP_PORT=1\n"
        "sleep() { :; }\n" + "\n".join(pieces) + f"\ncmd_service {action}\n"
    )
    done = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    calls = log.read_text(encoding="utf-8").splitlines()
    acted = [one for one in calls if one.startswith(f"systemctl {action}")]
    assert acted and ("plm-worker" in acted[0]) is worker, acted


def test_pg_tune_은_보여_주기만이_기본이고_바꾸면_재시작할_것을_말한다() -> None:
    """서버 전체 설정을 바꾸는 스크립트다 — **물어보지 않고 바꾸지 않는다**(`--apply` 일 때만
    ALTER SYSTEM), PostgreSQL 을 스스로 재시작하지 않는다(사람이 한가한 때). 개발 PC 는
    `--url` 로 같은 것을 쓴다."""
    script = (DEPLOY / "pg-tune.sh").read_text(encoding="utf-8")
    assert "APPLY=0" in script and "if (( ! APPLY )); then" in script
    assert "ALTER SYSTEM SET" in script and "pg_reload_conf()" in script
    assert "pending_restart" in script
    # 재시작은 말로만 — 스크립트가 직접 부르지 않는다.
    lines = [line.strip() for line in script.splitlines()]
    assert not any(line.startswith(("systemctl", "service ", "pg_ctl")) for line in lines)
    assert "--url" in script
    deploy = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    assert 'pg-tune)        shift; exec "$HERE/pg-tune.sh" "$@" ;;' in deploy


# --- 뜻으로 찾기의 바탕(ADR 0050) — pgvector · Ollama ---------------------------------------
#
# pgvector 는 superuser 만 만든다 — 배포가 마이그레이션 앞에서 postgres 계정으로 만든다. 대기
# 서버는 만들 수 없고(이중화의 갱신은 B 먼저), 폐쇄망은 번들의 .deb 로 깐다. Ollama · 모델은
# 서버에 하나다. **이 판은 갖추기만 한다** — 쓰는 표가 아직 없어, 어느 것도 못 갖춰도 갱신은
# 끝난다(운영의 갱신은 사람이 아니라 스크립트가 돌린다 — 묻거나 멈추면 거기서 선다).


def _vector_sql(text: str) -> str:
    found = re.search(r'^VECTOR_SQL=".*?"$', text, re.M | re.S)
    assert found, "deploy.sh 에 VECTOR_SQL 이 없습니다"
    return found.group(0)


def _ensure_vector(
    tmp_path: Path,
    *,
    package: bool = True,
    bundled: bool = False,
    apt_ok: bool = True,
    recovery: bool = False,
    primary_has: bool = True,
    version: str | None = "160004",
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    ha = (DEPLOY / "ha.sh").read_text(encoding="utf-8")
    pieces = [
        _piece(text, name)
        for name in (
            "err",
            "info",
            "warn",
            "apt_quiet",
            "ensure_vector_package",
            "ensure_vector",
        )
    ]
    pieces.append(_piece(ha, "pg_in_recovery"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    share = tmp_path / "share"
    control = share / "16" / "extension" / "vector.control"
    control.parent.mkdir(parents=True)
    if package:
        control.write_text("", encoding="utf-8")
    here = tmp_path / "bundle"
    (here / "pgvector_debs").mkdir(parents=True)
    if bundled:
        (here / "pgvector_debs" / "postgresql-16-pgvector_0.6.0-1_amd64.deb").write_bytes(
            b"deb"
        )
    log = tmp_path / "calls.log"
    answer_version = f"echo {version}" if version else "exit 2"
    has = "echo 0.6.0" if primary_has or not recovery else ":"
    _stub(
        bin_dir / "sudo",
        # SQL 이 여러 줄이라 한 줄로 적는다.
        f'echo "sudo $*" | tr "\\n" " " >> "{log}"; echo >> "{log}"\n'
        'case "$*" in\n'
        f"  *pg_is_in_recovery*) echo {'t' if recovery else 'f'} ;;\n"
        f"  *server_version_num*) {answer_version} ;;\n"
        f"  *extversion*) {has} ;;\n"
        "esac\n",
    )
    # 저장소 — 닿으면 깔린다. 번들의 .deb 는 apt 가 못 닿아도 dpkg 가 깐다.
    _stub(
        bin_dir / "apt-get",
        # 묻지 않게 불렸는지도 적는다.
        f'echo "apt-get $DEBIAN_FRONTEND:$NEEDRESTART_SUSPEND $*" >> "{log}"\n'
        + (f'touch "{control}"\n' if apt_ok else "exit 100\n"),
    )
    _stub(bin_dir / "dpkg", f'echo "dpkg $*" >> "{log}"\ntouch "{control}"\n')
    script = (
        "set -euo pipefail\nDB_NAME=plmhub\n"
        f'HERE="{here}"\nPG_SHARE="{share}"\n{_vector_sql(text)}\n'
        + "\n".join(pieces)
        + "\nensure_vector\n"
    )
    done = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, calls


@_no_bash
def test_주_DB_에_pgvector_를_extensions_스키마로_만든다(tmp_path: Path) -> None:
    done, calls = _ensure_vector(tmp_path)
    assert done.returncode == 0, done.stderr
    made = [one for one in calls if "CREATE EXTENSION vector SCHEMA extensions" in one]
    assert made and "-d plmhub" in made[0]
    assert "GRANT USAGE ON SCHEMA extensions TO PUBLIC" in made[0]
    assert not any(one.startswith("apt-get") for one in calls)
    assert "pgvector: 0.6.0" in done.stdout


@_no_bash
def test_폐쇄망이면_번들의_deb_로_깐다(tmp_path: Path) -> None:
    done, calls = _ensure_vector(tmp_path, package=False, bundled=True, apt_ok=False)
    assert done.returncode == 0, done.stderr
    assert any(
        one.startswith("dpkg -i") and "pgvector_debs/postgresql-16-pgvector_" in one
        for one in calls
    )


@_no_bash
def test_apt_는_묻지_않고_다른_서비스를_다시_띄우지_않는다(tmp_path: Path) -> None:
    """우분투 24.04 의 needrestart 는 패키지를 깔면 「어느 서비스를 다시 띄울까」 를 묻는다 —
    스크립트로 도는 갱신이 거기서 선다. 고르는 쪽(자동)은 그 서버의 PostgreSQL 을 다시 띄울 수
    있다."""
    done, calls = _ensure_vector(tmp_path, package=False)
    assert done.returncode == 0, done.stderr
    installs = [one for one in calls if one.startswith("apt-get")]
    assert installs and all(one.startswith("apt-get noninteractive:1 ") for one in installs)
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    for name in ("ensure_vector_package", "ensure_ollama"):
        body = _piece(text, name)
        assert "apt_quiet install" in body and "apt-get install" not in body, name


@_no_bash
def test_번들도_저장소도_없으면_할_일을_말하고_실패를_알린다(tmp_path: Path) -> None:
    """알리기만 한다 — 갱신은 `ensure_vector || warn` 로 이어 간다."""
    done, calls = _ensure_vector(tmp_path, package=False, apt_ok=False)
    assert done.returncode != 0
    assert "apt-get download postgresql-16-pgvector" in done.stderr
    assert "sudo ./deploy.sh pgvector" in done.stderr
    assert not any("CREATE EXTENSION" in one for one in calls)


@_no_bash
def test_대기_서버는_패키지만_깔고_확장은_주의_갱신에_맡긴다(
    tmp_path: Path,
) -> None:
    """이중화의 갱신은 B(대기) 먼저다 — 대기는 확장을 못 만든다. 주에 아직 없어도 B 의 갱신은
    지나가고, A 의 갱신이 만들어 복제로 온다. 스크립트가 돌리는 갱신에 사람 손이 끼지
    않는다."""
    for primary_has, said in ((True, "복제로 왔다"), (False, "주 서버의 갱신이 만들어")):
        (tmp_path / str(primary_has)).mkdir()
        done, calls = _ensure_vector(
            tmp_path / str(primary_has), package=False, recovery=True, primary_has=primary_has
        )
        assert done.returncode == 0, done.stderr
        assert any(one.startswith("apt-get") and "pgvector" in one for one in calls)
        assert not any("CREATE EXTENSION vector" in one for one in calls)
        assert said in done.stdout


@_no_bash
def test_PostgreSQL_에_못_붙으면_거기서_할_일을_말한다(tmp_path: Path) -> None:
    """DB 가 다른 서버 — 그 서버에서 칠 한 줄을 준다."""
    done, calls = _ensure_vector(tmp_path, version=None)
    assert done.returncode != 0
    assert "CREATE EXTENSION IF NOT EXISTS vector SCHEMA extensions" in done.stderr
    assert not any(one.startswith(("apt-get", "dpkg")) for one in calls)


def test_갱신은_pgvector_를_못_갖춰도_멈추지_않는다() -> None:
    """이 판에는 확장을 쓰는 표가 없다 — 운영의 갱신은 스크립트가 돌리므로 멈추면 거기서 선다.
    마이그레이션 앞 · update 는 서비스를 내리기 전(패키지를 받는 동안 서비스가 서 있다)."""
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    update = _piece(text, "cmd_update")
    assert update.index("ensure_vector") < update.index('systemctl stop "$SERVICE_NAME"')
    for command in ("cmd_install", "cmd_reset"):
        body = _piece(text, command)
        assert body.index("ensure_vector") < body.index("run_migrations"), command
    for command in ("cmd_install", "cmd_update", "cmd_reset", "cmd_prepare"):
        body = _piece(text, command)
        assert body.count('ensure_vector || warn "') == 1, command
    prepare = _piece(text, "cmd_prepare")
    standby = prepare[: prepare.index("        ensure_apptainer\n")]
    assert 'ensure_vector_package "$PG_VERSION" || true' in standby
    # 따로 부를 때는 실패가 실패다.
    assert '    pgvector)       ensure_vector || err "' in text
    assert not list((REPO / "backend" / "migrations" / "versions").glob("*pgvector*"))


def test_번들이_pgvector_deb_를_우분투_판으로_동봉한다() -> None:
    builder = (DEPLOY / "build_bundle.sh").read_text(encoding="utf-8")
    assert "pgvector_debs" in builder
    # PGDG 만 뺀다 — 러너는 우분투 저장소를 미러 목록으로 가리켜 주소로는 못 고른다.
    assert "apt-cache madison" in builder and "$3 !~ /pgdg/" in builder
    assert "pgvector_debs" in _piece(
        (DEPLOY / "deploy.sh").read_text(encoding="utf-8"), "ensure_vector_package"
    )


def test_번들은_서버와_같은_우분투에서_만들고_deb_가_빠지면_멈춘다() -> None:
    """`ubuntu-latest` 가 26.04 로 바뀌자 서버(24.04)용 .deb 를 못 받아, 빌드는 경고만 하고
    .deb 없는 번들이 나갔다(v0.4.69 · v0.4.70) — 폐쇄망 서버는 그것으로 apptainer · pgvector 를
    깐다."""
    release = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    # 일 하나씩(들여쓰기 두 칸의 `<이름>:` 줄에서 자른다).
    jobs = re.split(r"\n  (?=[\w-]+:\s*\n)", release)
    build = next(job for job in jobs if "./deploy/build_bundle.sh" in job)
    assert "runs-on: ubuntu-24.04" in build
    assert "apptainer_debs/apptainer_" in build
    assert "pgvector_debs/postgresql-16-pgvector_" in build


def test_복구는_덤프의_pgvector_를_먼저_만든다() -> None:
    """앱 계정의 pg_restore 는 확장을 못 만든다 — 그것을 쓰는 표가 안 돌아온 채 끝난다."""
    restore = (DEPLOY / "restore.sh").read_text(encoding="utf-8")
    made = restore.index("CREATE EXTENSION IF NOT EXISTS vector SCHEMA extensions")
    assert made < restore.index("pg_restore --host")
    assert "EXTENSION - vector" in restore


def _setup_embedding(
    tmp_path: Path,
    *,
    env: str = "",
    installed: bool = False,
    has_model: bool = False,
    bundle: bool = False,
    online: bool = True,
    dims: int = 1024,
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    pieces = [
        _piece(text, name)
        for name in (
            "err",
            "info",
            "warn",
            "env_file_get",
            "setup_embedding",
            "ollama_up",
            "ensure_ollama",
            "ollama_models_dir",
            "ensure_ollama_model",
            "check_embedding",
        )
    ]
    version = re.search(r"^OLLAMA_VERSION=.*$", text, re.M)
    assert version
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    unit = tmp_path / "ollama.service"
    home = tmp_path / "ollama-home"
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "calls.log"
    env_file = tmp_path / ".env"
    env_file.write_text(env, encoding="utf-8")
    if installed:
        (prefix / "bin").mkdir()
        _stub(prefix / "bin" / "ollama", "exit 0\n")
        unit.write_text("[Service]\n", encoding="utf-8")
    if has_model:
        (state / "model").write_text("", encoding="utf-8")
    # 받는 Ollama — bin/ollama 하나든 묶음.
    payload = tmp_path / "payload"
    (payload / "bin").mkdir(parents=True)
    _stub(payload / "bin" / "ollama", "exit 0\n")
    fake_tar = tmp_path / "ollama.tar"
    subprocess.run(["tar", "-C", str(payload), "-cf", str(fake_tar), "bin"], check=True)
    ai = tmp_path / "ai"
    if bundle:
        (ai / "models" / "manifests" / "registry.ollama.ai" / "library" / "bge-m3").mkdir(
            parents=True
        )
        (
            ai
            / "models"
            / "manifests"
            / "registry.ollama.ai"
            / "library"
            / "bge-m3"
            / "latest"
        ).write_text("{}", encoding="utf-8")
        (ai / "models" / "blobs").mkdir()
        (ai / "models" / "blobs" / "sha256-abc").write_text("w", encoding="utf-8")
        subprocess.run(
            ["tar", "-C", str(payload), "-czf", str(ai / "ollama-linux-amd64.tgz"), "bin"],
            check=True,
        )
    _stub(
        bin_dir / "curl",
        'url=""; out=""; prev=""\n'
        'for a in "$@"; do\n'
        '  [ "$prev" = "-o" ] && out="$a"\n'
        '  case "$a" in http*) url="$a";; esac\n'
        '  prev="$a"\n'
        "done\n"
        f'echo "curl $url" >> "{log}"\n'
        'case "$url" in\n'
        "  */api/version) exit 0 ;;\n"
        f'  */api/show) [ -f "{state}/model" ] ;;\n'
        f'  */api/pull) touch "{state}/model" ;;\n'
        f"  */api/embed) echo '{{\"embeddings\": [[{', '.join(['0.1'] * dims)}]]}}' ;;\n"
        f'  https://github.com/*) {"" if online else "exit 22;"} cp "{fake_tar}" "$out" ;;\n'
        "esac\n",
    )
    _stub(bin_dir / "zstd", 'cat "$2"\n')
    _stub(
        bin_dir / "systemctl",
        f'echo "systemctl $*" >> "{log}"\n'
        f'case "$1" in cat) [ -f "{unit}" ] ;; show) echo "Environment=" ;; esac\n',
    )
    _stub(
        bin_dir / "getent",
        f'case "$1" in passwd) echo "ollama:x:1:1::{home}:/bin/false" ;; esac\n',
    )
    _stub(bin_dir / "install", 'for last; do :; done; mkdir -p "$last"\n')
    for tool in ("chown", "useradd", "usermod", "apt-get"):
        _stub(bin_dir / tool, f'echo "{tool} $*" >> "{log}"\n')
    script = (
        "set -euo pipefail\n"
        f'ENV_FILE="{env_file}"\nOLLAMA_PREFIX="{prefix}"\nOLLAMA_UNIT="{unit}"\n'
        f'AI_BUNDLE="{ai}"\nEMBEDDING_DIM=1024\n{version.group(0)}\n'
        "sleep() { :; }\nid() { return 0; }\n" + "\n".join(pieces) + "\nsetup_embedding\n"
    )
    done = subprocess.run(
        [shutil.which("bash") or "bash", "-c", script],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, calls, home / ".ollama" / "models"


@_no_bash
def test_임베딩을_끈_설치는_Ollama_를_깔지_않는다(tmp_path: Path) -> None:
    done, calls, _ = _setup_embedding(tmp_path, env="EMBEDDING_BACKEND=off\n")
    assert done.returncode == 0, done.stderr
    assert calls == [] and "끔" in done.stdout


@_no_bash
def test_다른_서버의_Ollama_를_쓰면_여기_깔지_않는다(tmp_path: Path) -> None:
    done, calls, _ = _setup_embedding(tmp_path, env="OLLAMA_BASE_URL=http://10.0.0.5:11434\n")
    assert done.returncode == 0, done.stderr
    assert calls == [] and "다른 서버" in done.stdout


@_no_bash
def test_없으면_박은_판을_받아_띄우고_모델을_받아_한_번_바꿔_본다(tmp_path: Path) -> None:
    done, calls, _ = _setup_embedding(tmp_path)
    assert done.returncode == 0, done.stderr
    assert (
        "curl https://github.com/ollama/ollama/releases/download/v0.30.10/"
        "ollama-linux-amd64.tar.zst" in calls
    )
    unit = (tmp_path / "ollama.service").read_text(encoding="utf-8")
    assert f"ExecStart={tmp_path}/prefix/bin/ollama serve" in unit
    # 이 서버 안에서만 듣는다.
    assert "Environment=OLLAMA_HOST=127.0.0.1:11434" in unit
    assert "systemctl enable --now ollama" in calls
    assert "curl http://127.0.0.1:11434/api/pull" in calls
    assert calls.index("curl http://127.0.0.1:11434/api/pull") < calls.index(
        "curl http://127.0.0.1:11434/api/embed"
    )
    assert "1024차원" in done.stdout


@_no_bash
def test_이미_있는_Ollama_는_판도_유닛도_건드리지_않는다(tmp_path: Path) -> None:
    """서버에 하나 — 다른 제품(보고서 보관함 같은)이 깐 것을 함께 쓴다."""
    done, calls, _ = _setup_embedding(tmp_path, installed=True, has_model=True)
    assert done.returncode == 0, done.stderr
    assert not any("github.com" in one or "/api/pull" in one for one in calls)
    assert (tmp_path / "ollama.service").read_text(encoding="utf-8") == "[Service]\n"


@_no_bash
def test_폐쇄망이면_임베딩_꾸러미에서_Ollama_와_모델을_깐다(tmp_path: Path) -> None:
    done, calls, store = _setup_embedding(tmp_path, bundle=True, online=False)
    assert not any("github.com" in one or "/api/pull" in one for one in calls)
    assert (tmp_path / "prefix" / "bin" / "ollama").exists()
    assert (store / "blobs" / "sha256-abc").exists()
    assert (
        store / "manifests" / "registry.ollama.ai" / "library" / "bge-m3" / "latest"
    ).exists()
    # 가짜 Ollama 는 복사한 모델을 못 본다 — 그래서 끝의 확인에서 멈춘다. 받으러 가지는 않는다.
    assert "Ollama 에 안 보입니다" in done.stderr


@_no_bash
def test_못_받거나_차원이_다르면_실패로_말한다(tmp_path: Path) -> None:
    """배포는 계속한다(`setup_embedding || warn`) — 서버 화면이 다시 말한다."""
    (tmp_path / "offline").mkdir()
    done, _, _ = _setup_embedding(tmp_path / "offline", online=False)
    assert done.returncode != 0 and "build_ai_bundle.sh" in done.stderr
    (tmp_path / "other").mkdir()
    done, _, _ = _setup_embedding(tmp_path / "other", installed=True, has_model=True, dims=768)
    assert done.returncode != 0 and "768" in done.stderr


def test_임베딩은_앱이_뜬_뒤에_돌고_실패해도_배포를_멈추지_않는다() -> None:
    text = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    for command in ("cmd_install", "cmd_update"):
        body = _piece(text, command)
        assert body.index("setup_embedding") > body.index("health_check"), command
        assert 'setup_embedding || warn "' in body, command
    assert "    ollama)" in text


def test_임베딩_꾸러미는_deploy_sh_의_Ollama_판을_쓴다() -> None:
    """두 곳에 적으면 인터넷으로 까는 서버와 꾸러미로 까는 서버의 판이 갈린다."""
    builder = (DEPLOY / "build_ai_bundle.sh").read_text(encoding="utf-8")
    deploy = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")
    pattern = re.search(r"sed -n '(s/\^OLLAMA_VERSION=.*?)' deploy/deploy\.sh", builder)
    assert pattern, "build_ai_bundle.sh 가 deploy.sh 에서 판을 읽지 않습니다"
    picked = subprocess.run(
        ["sed", "-n", pattern.group(1)],
        input=deploy,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert picked and f'OLLAMA_VERSION="${{OLLAMA_VERSION:-{picked}}}"' in deploy
