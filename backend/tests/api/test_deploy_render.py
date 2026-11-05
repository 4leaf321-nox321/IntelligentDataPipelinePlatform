"""배포 스크립트가 만드는 유닛 · nginx · keepalived 설정 — **root 없이 렌더만** 해서 본다.

서버 두 대에 올리는 설정은 서버에 가서야 틀린 것이 드러난다(그리고 그때는 SSH 로 붙어 있는
자리다). `deploy.sh render` 가 아무것도 바꾸지 않고 결과만 쓰므로, 여기서 모양을 잠근다."""

from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DEPLOY = REPO / "deploy"
REQUEST_TEMPLATE = REPO / "docs" / "[참고] HWAX포탈_연동_요청서.md"

# 기본 — 메인 서버(포탈 + nginx)가 로드밸런서. A · B 에는 앱 · MCP · DB 만.
HA_ENV = {
    "HA_ROLE": "master",
    "PEER_IP": "10.0.0.2",
    "SELF_IP": "10.0.0.1",
    "DB_VIP": "10.0.0.11",
    "PUBLIC_HOST": "portal.example.local",
    "VRRP_IFACE": "eth0",
    "DATA_DIR": "/data/common/testplatform",
}
# 메인 서버가 없을 때 — A · B 자체에 nginx + 웹 VIP.
LOCAL_ENV = {**HA_ENV, "LB_MODE": "local", "WEB_VIP": "10.0.0.10"}


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """릴리스 번들의 모양 — build_bundle.sh 가 담는 것 중 렌더에 필요한 것만."""
    stage = tmp_path / "bundle"
    stage.mkdir()
    for name in ["deploy.sh", "ha.sh", "pg-ha.sh", "backup.sh", "restore.sh"]:
        shutil.copy(DEPLOY / name, stage / name)
    for tpl in DEPLOY.glob("*.template"):
        shutil.copy(tpl, stage / tpl.name)
    # HWAX 연동 요청서의 틀 — build_bundle.sh 가 이 이름으로 담는다(이름이 어긋나면
    # hwax-request 가 못 찾는다).
    shutil.copy(REQUEST_TEMPLATE, stage / "HWAX-연동요청서.template.md")
    (stage / "BUILD_INFO").write_text(
        "app_name=Test Platform\napp_slug=testplatform\nport=8040\nmcp_port=8042\nversion=t\n",
        encoding="utf-8",
    )
    return stage


def read(etc: Path, rel: str) -> str:
    return (etc / "etc" / rel).read_text(encoding="utf-8")


def render(bundle: Path, etc: Path, **env: str) -> subprocess.CompletedProcess[str]:
    full = {
        **os.environ,
        "OPERATOR": "ops",
        "INSTALL_DIR": "/opt/testplatform",
        "ETC": str(etc),
        **env,
    }
    return subprocess.run(
        ["bash", str(bundle / "deploy.sh"), "render"],
        cwd=bundle,
        env=full,
        capture_output=True,
        text=True,
        check=True,
    )


def test_단독_서버는_설치_폴더_하나에_전부(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, INSTALL_DIR="/home/ops/apps/testplatform")
    unit = (etc / "etc/systemd/system/testplatform.service").read_text(encoding="utf-8")
    assert "--bind /home/ops/apps/testplatform/.env:/opt/app/backend/.env:ro" in unit
    assert "--bind /home/ops/apps/testplatform/filestore:/data/filestore" in unit
    assert "--bind /home/ops/apps/testplatform/logs:/data/logs" in unit
    # 백업 폴더를 모르면 그 --bind 줄이 **아예 없다** — 빈 줄이면 ExecStart 가 거기서 끊긴다.
    assert ":/data/backup" not in unit
    assert "@@" not in unit
    # 로컬 PostgreSQL 에 묶지 않는다 — 이중화에서는 DB 가 상대 서버에 있다.
    assert not [x for x in unit.splitlines() if x.startswith("Requires=")]
    assert not (etc / "etc/nginx").exists()
    assert not (etc / "etc/keepalived").exists()
    # 단독 서버에도 워커는 선다 — 가져오기가 도는 곳이다.
    worker = read(etc, "systemd/system/testplatform-worker.service")
    assert "--bind /home/ops/apps/testplatform/.env:/opt/app/backend/.env:ro" in worker


def test_이중화는_공용_폴더와_로컬을_가른다(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, **HA_ENV)
    unit = (etc / "etc/systemd/system/testplatform.service").read_text(encoding="utf-8")
    assert "--bind /data/common/testplatform/.env:/opt/app/backend/.env:ro" in unit
    assert "--bind /data/common/testplatform/filestore:/data/filestore" in unit
    assert "--bind /opt/testplatform/logs:/data/logs" in unit
    assert "--bind /data/common/testplatform/backup:/data/backup \\" in unit
    lines = unit.splitlines()
    exec_start = lines.index(next(x for x in lines if x.startswith("ExecStart=")))
    # ExecStart 부터 app.sif 까지 빈 줄 없이 이어진다.
    tail = lines[exec_start:]
    end = tail.index(next(x for x in tail if x.strip().endswith("app.sif")))
    assert all(x.strip() for x in tail[: end + 1])

    # **워커가 유닛으로 서지 않으면 일괄 입력이 영영 대기다.** 앱과 같은 SIF · 같은 .env.
    worker = read(etc, "systemd/system/testplatform-worker.service")
    assert "--bind /data/common/testplatform/.env:/opt/app/backend/.env:ro" in worker
    assert "python -m app.worker" in worker
    assert "@@" not in worker
    # 중계의 정의 · 작업 폴더도 **공용**이다 — 두 서버의 워커 어느 쪽이 돌려도 같은 것을 본다.
    assert "--bind /data/common/testplatform/relay:/data/relay" in worker
    assert "--bind /data/common/testplatform/relay:/data/relay" in unit

    sync = read(etc, "systemd/system/testplatform-sync.service")
    assert "--bind /data/common/testplatform/.env" in sync
    # 타이머는 차례가 된 파이프라인을 작업으로 넣는다(`scripts/relay_due.py`).
    assert (
        "scripts/relay_due.py" in sync
        and "--bind /data/common/testplatform/relay:/data/relay" in sync
    )
    # 지표 타이머(틀의 ADR 0013)는 배포에서 걷어냈다(ADR 0001) — 그 유닛은 생기지 않는다.
    assert not (etc / "etc" / "systemd/system/testplatform-metrics.service").exists()
    backup = read(etc, "systemd/system/testplatform-backup.service")
    assert (
        "backup.sh -e /data/common/testplatform/.env -f /data/common/testplatform/filestore"
        " -o /data/common/testplatform/backup" in backup
    )


def _assets_try_other_server(conf: str) -> None:
    """화면 조각은 **한 서버에 없으면(404) 다른 서버에서** — 이중화를 한 대씩 올리는 동안 두
    서버의 판이 달라, 새 판에서 받은 화면이 조각을 옛 판 서버에 물으면 404 가 난다. 앱
    location 은 404 를 넘기지 않는다(POST 가 두 번 가면 안 된다) — 조각 location 만 따로."""
    at = conf.index("location /testplatform/assets/ {")
    block = conf[at : conf.index("}", at)]
    assert "proxy_pass http://testplatform_app/assets/;" in block
    assert "proxy_next_upstream error timeout http_404 http_502 http_503;" in block
    app = conf[conf.index("location /testplatform/ {") :]
    app = app[: app.index("}")]
    assert "http_404" not in app


def _photo_zip_streams(conf: str) -> None:
    """사진 zip(2GB, `JOB_PHOTOS_MAX_BYTES`)의 경로만 본문 상한을 넓히고 흘려 넘긴다 — 다른
    경로는 512m 그대로(아무 경로나 2GB 를 받으면 앱의 임시 폴더에 먼저 쌓인다, 2026-10-10)."""
    at = conf.index("location ~ ^/testplatform/api/objects/[^/]+/photos/import$ {")
    block = conf[at : conf.index("}", at)]
    assert "client_max_body_size 2100m;" in block
    assert "proxy_request_buffering off;" in block
    # 정규식 location 은 주소를 못 붙인다 — 접두어째 넘긴다(앱이 둘 다 받는다).
    assert "proxy_pass http://testplatform_app;" in block
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in block
    app = conf[conf.index("location /testplatform/ {") :]
    assert "client_max_body_size 2100m;" not in app[: app.index("}")]


def test_메인_서버에_넘길_nginx_조각(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, **HA_ENV)
    # A · B 에는 nginx 가 없다 — 메인 서버 쪽에 넣을 조각만.
    assert not (etc / "etc/nginx").exists()
    snippet = (etc / "main-server-nginx.conf").read_text(encoding="utf-8")
    assert "server 10.0.0.1:8040" in snippet and "server 10.0.0.2:8040" in snippet
    assert "server 10.0.0.1:8042" in snippet and "server 10.0.0.2:8042" in snippet
    # 접두어를 벗기는 것(끝의 '/')과 https 를 알리는 것 — 이 둘이 없으면 앱이 못 맞춘다.
    assert "location /testplatform/ {" in snippet
    assert "proxy_pass http://testplatform_app/;" in snippet
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in snippet
    assert "location /testplatform/mcp {" in snippet
    assert "proxy_pass http://testplatform_mcp/mcp;" in snippet
    assert "proxy_buffering off;" in snippet
    _assets_try_other_server(snippet)
    _photo_zip_streams(snippet)
    # keepalived 는 DB VIP 만.
    conf = read(etc, "keepalived/keepalived.conf")
    assert "VI_DB" in conf and "VI_WEB" not in conf and "chk_nginx" not in conf
    dropin = read(etc, "systemd/system/keepalived.service.d/platform-ha.conf")
    assert "After=postgresql.service\n" in dropin


def test_로컬_LB_는_접두어를_떼고_두_앱에_나눈다(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, **LOCAL_ENV)
    upstream = read(etc, "nginx/conf.d/testplatform-upstream.conf")
    assert "server 10.0.0.1:8040" in upstream and "server 10.0.0.2:8040" in upstream
    assert "server 10.0.0.1:8042" in upstream and "server 10.0.0.2:8042" in upstream

    site = (etc / "etc/nginx/platforms.d/testplatform.conf").read_text(encoding="utf-8")
    # 접두어를 떼고 넘긴다 — 앱은 PUBLIC_PATH 로 화면 · 쿠키만 맞춘다(FastAPI root_path).
    assert "location /testplatform/ {" in site
    assert "proxy_pass http://testplatform_app/;" in site
    assert "location /testplatform/mcp {" in site
    assert "proxy_pass http://testplatform_mcp/mcp;" in site
    assert "proxy_buffering off;" in site
    assert "X-Forwarded-Proto $scheme" in site
    _assets_try_other_server(site)
    _photo_zip_streams(site)

    host = (etc / "etc/nginx/sites-available/platform-ha").read_text(encoding="utf-8")
    assert "server_name portal.example.local 10.0.0.10 10.0.0.1 10.0.0.2 _;" in host
    assert "return 301 https://$host$request_uri;" in host
    assert "include /etc/nginx/platforms.d/*.conf;" in host
    assert "listen 443 ssl http2 default_server;" in host
    assert (etc / "etc/nginx/sites-enabled/platform-ha").is_symlink()


def test_keepalived_는_주_DB_가_항상_이긴다(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, **LOCAL_ENV)
    conf = (etc / "etc/keepalived/keepalived.conf").read_text(encoding="utf-8")
    assert "virtual_router_id 51" in conf and "virtual_ipaddress { 10.0.0.10 }" in conf
    assert "virtual_router_id 52" in conf and "virtual_ipaddress { 10.0.0.11 }" in conf
    assert "unicast_src_ip 10.0.0.1" in conf and "unicast_peer { 10.0.0.2 }" in conf
    # 웹 VIP 는 master 역할이 150, DB VIP 는 101 + 주(check-primary) 50.
    assert "priority 150" in conf and "priority 101" in conf
    assert 'script "/usr/local/sbin/pg-ha check-primary"' in conf and "weight 50" in conf
    assert 'notify_master "/usr/local/sbin/pg-ha on-master"' in conf
    # keepalived 는 DB · nginx 뒤에 뜬다 — 재부팅이 승격이 되지 않게.
    dropin = etc / "etc/systemd/system/keepalived.service.d/platform-ha.conf"
    assert "After=postgresql.service nginx.service" in dropin.read_text(encoding="utf-8")

    other = tmp_path / "etc-b"
    swapped = {**LOCAL_ENV, "HA_ROLE": "backup", "SELF_IP": "10.0.0.2", "PEER_IP": "10.0.0.1"}
    render(bundle, other, **swapped)
    conf_b = read(other, "keepalived/keepalived.conf")
    assert "state BACKUP" in conf_b and "priority 100" in conf_b
    assert "priority 150" not in conf_b


def test_DB_VIP_가_없으면_자동_승격_인스턴스가_없다(bundle: Path, tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    render(bundle, etc, **{**LOCAL_ENV, "DB_VIP": ""})
    conf = (etc / "etc/keepalived/keepalived.conf").read_text(encoding="utf-8")
    assert "VI_WEB" in conf and "VI_DB" not in conf and "on-master" not in conf
    # 메인 서버가 LB 이고 DB VIP 도 없으면 keepalived 자체가 없다.
    plain = tmp_path / "etc-plain"
    render(bundle, plain, **{**HA_ENV, "DB_VIP": ""})
    assert not (plain / "etc/keepalived").exists()
    assert (plain / "main-server-nginx.conf").exists()


def test_스크립트_문법(bundle: Path) -> None:
    for name in ["deploy.sh", "ha.sh", "pg-ha.sh", "backup.sh", "restore.sh"]:
        subprocess.run(["bash", "-n", str(bundle / name)], check=True)


def test_번들_하나로_인스턴스_여럿(bundle: Path, tmp_path: Path) -> None:
    """slug · 이름 · 포트 · 확장은 번들이 아니라 설치가 준다. 같은 번들에서 두 인스턴스를
    렌더하면 유닛 · 경로 · 포트 · 메인 서버 조각이 전부 갈리고, 준 값은 인스턴스 파일에 남아
    다음 배포가 기억한다."""
    etc = tmp_path / "etc"
    one = {**HA_ENV, "APP_SLUG": "plmhub", "APP_NAME": "PLM & 기준정보", "APP_PORT": "8050"}
    render(bundle, etc, **one, EXTENSIONS="sample")
    two = {**HA_ENV, "APP_SLUG": "simtools", "APP_NAME": "시뮬레이션", "APP_PORT": "8060"}
    render(bundle, etc, **{**two, "DATA_DIR": "/data/simtools"})

    units = etc / "etc/systemd/system"
    assert (units / "plmhub.service").exists() and (units / "simtools.service").exists()
    assert "/opt/testplatform/app.sif" in read(etc, "systemd/system/plmhub.service")
    snippet = (etc / "main-server-nginx.conf").read_text(encoding="utf-8")
    # 마지막에 렌더한 인스턴스의 조각 — 포트 +2 가 MCP.
    assert "server 10.0.0.1:8060" in snippet and "server 10.0.0.1:8062" in snippet
    assert "location /simtools/ {" in snippet

    saved = read(etc, "platform-instances/plmhub.conf")
    assert "APP_NAME=PLM & 기준정보" in saved and "APP_PORT=8050" in saved
    assert "EXTENSIONS=sample" in saved
    assert "DATA_DIR=/data/simtools" in read(etc, "platform-instances/simtools.conf")

    # 두 인스턴스가 있으면 slug 없이는 못 고른다.
    with pytest.raises(subprocess.CalledProcessError) as failed:
        render(bundle, etc)
    assert "APP_SLUG=<slug>" in failed.value.stderr


def test_쌍둥이를_한_서버에_얹을_때_포트가_겹치면_멈춘다(bundle: Path, tmp_path: Path) -> None:
    """**MCP 포트는 앱 포트 +2 로 딸려 온다.**

    그래서 두 번째 플랫폼이 앞 플랫폼의 MCP 포트를 앱 포트로 고르면(8050 · 8052) 조용히
    겹치고, 늦게 뜨는 쪽이 「주소가 이미 사용 중」 으로 죽는다 — 그 사실은 systemd 로그에만
    남는다. 10 씩 벌리라는 안내만으로는 몇 달 뒤 다른 사람이 그 규칙을 모른다.
    """
    etc = tmp_path / "etc"
    render(bundle, etc, **{**HA_ENV, "APP_SLUG": "plmhub", "APP_PORT": "8050"})
    assert "MCP_PORT=8052" in read(etc, "platform-instances/plmhub.conf")

    # 앞 플랫폼의 MCP 포트를 앱 포트로 고른다.
    with pytest.raises(subprocess.CalledProcessError) as clash:
        render(bundle, etc, **{**HA_ENV, "APP_SLUG": "simtools", "APP_PORT": "8052"})
    assert "plmhub" in clash.value.stderr and "겹칩니다" in clash.value.stderr

    # 거꾸로도 — 내 MCP 포트가 앞 플랫폼의 앱 포트다.
    with pytest.raises(subprocess.CalledProcessError) as other:
        render(bundle, etc, **{**HA_ENV, "APP_SLUG": "simtools", "APP_PORT": "8048"})
    assert "겹칩니다" in other.value.stderr

    # 10 벌리면 된다.
    render(bundle, etc, **{**HA_ENV, "APP_SLUG": "simtools", "APP_PORT": "8060"})
    assert "MCP_PORT=8062" in read(etc, "platform-instances/simtools.conf")


def test_setup_은_물어보고_계획을_보여준다(bundle: Path, tmp_path: Path) -> None:
    """운영자가 env 이름과 순서를 외우지 않게 — 답한 것으로 계획을 만들고, --plan 은
    거기서 멈춘다."""
    etc = tmp_path / "etc"
    env = {**os.environ, "OPERATOR": "ops", "ETC": str(etc), "SELF_IP": "10.0.0.1"}
    answers = "A\nplmhub\nPLM 기준정보\n8050\nsample\n10.0.0.2\n\n\n"
    out = subprocess.run(
        ["bash", str(bundle / "deploy.sh"), "setup", "--plan"],
        cwd=bundle,
        env=env,
        input=answers,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # 확장은 화면에서 켠다 — 설치가 받는 값은 **기본값**이라 그렇게 적힌다.
    assert "PLM 기준정보 (plmhub) · 포트 8050 · 확장 기본값 sample" in out
    # **유닛 이름이 전부 답한 slug 에서 나온다.** 파생 목록이 두 벌이던 때 설치 마법사 쪽에
    # 워커만 빠져 있었고, 그래서 한 서버의 두 번째 플랫폼이 **첫 플랫폼의 워커 유닛을
    # 덮어썼다**(실측). 두 플랫폼이 한 워커를 공유하면 그 워커는 한쪽 DB 만 본다.
    for unit in ("plmhub", "plmhub-worker", "plmhub-mcp", "plmhub-sync", "plmhub-backup"):
        assert unit in out, unit
    # 번들의 기본 slug(이 시험의 BUILD_INFO 는 `testplatform`)가 남아 있으면 안 된다 —
    # 그것이 앞 플랫폼의 유닛을 덮어쓰던 바로 그 자리다.
    assert "testplatform-worker" not in out, "번들 기본 slug 가 남았다"
    assert "주(A) 10.0.0.1 — 상대 B 10.0.0.2" in out
    assert "db-primary" in out and "B 에 넘길 파일" in out
    # 대기는 이름 · 포트를 다시 묻지 않는다 — A 의 .env 를 그대로 받는다.
    answers_b = "B\nplmhub\n10.0.0.1\n\n\nops\n\n"
    out_b = subprocess.run(
        ["bash", str(bundle / "deploy.sh"), "setup", "--plan"],
        cwd=bundle,
        env={**env, "SELF_IP": "10.0.0.2"},
        input=answers_b,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "이름 · 포트 · 확장은 A 의 설정을 그대로" in out_b
    assert "대기(B) 10.0.0.2 — 주 A 10.0.0.1" in out_b and "db-standby" in out_b
    # 이상한 slug 는 계획 전에 막는다.
    bad = subprocess.run(
        ["bash", str(bundle / "deploy.sh"), "setup", "--plan"],
        cwd=bundle,
        env=env,
        input="A\nPLM Hub\n",
        capture_output=True,
        text=True,
    )
    assert bad.returncode != 0 and "소문자" in bad.stderr


def test_도움말이_재시작을_말한다(bundle: Path) -> None:
    """**`.env` 는 프로세스가 뜰 때 한 번만 읽는다.** 고치는 법만 알려 주고 다시 띄우는 법을
    안 알려 주면, 사람은 고쳤는데 왜 그대로냐를 한참 찾는다."""
    got = subprocess.run(
        ["bash", str(bundle / "deploy.sh"), "--help"],
        cwd=bundle,
        env={**os.environ, "OPERATOR": "ops", "INSTALL_DIR": "/opt/testplatform"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert "restart" in got.stdout
    assert ".env" in got.stdout


def test_재시작은_유닛_이름을_스스로_안다(bundle: Path, tmp_path: Path) -> None:
    """유닛 이름(`<slug>` · `<slug>-worker` · `<slug>-mcp`)을 사람이 외울 이유가 없다 —
    스크립트가 이미 slug 를 안다. 안 깐 것(여기서는 MCP)은 빼고 부른다.

    `units` 로 본다: `restart` 는 root 가 있어야 하지만 **무엇을 껐다 켜는지**는 고치기 전에
    묻는 물음이라 root 없이 볼 수 있어야 하고, 둘은 같은 목록을 쓴다.
    """
    etc = tmp_path / "etc"
    (etc / "etc/systemd/system").mkdir(parents=True)
    (etc / "etc/systemd/system/testplatform-worker.service").write_text("", encoding="utf-8")

    got = subprocess.run(
        ["bash", str(bundle / "deploy.sh"), "units"],
        cwd=bundle,
        env={
            **os.environ,
            "OPERATOR": "ops",
            "INSTALL_DIR": "/opt/testplatform",
            "ETC": str(etc),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert got.stdout.split() == ["testplatform", "testplatform-worker"]


def _sync_portal(env_file: Path, **env: str) -> str:
    """`sync_env_portal`(HWAX 포털에 붙이기, ADR 0028)만 떼어 돌린다 — install · update 는 root
    와 SIF 가 있어야 해서. 떼어 내는 것은 그 함수와 그것이 부르는 둘(`sed_escape` ·
    `env_file_set`)."""
    script = (DEPLOY / "deploy.sh").read_text(encoding="utf-8").splitlines()
    pieces: list[str] = [next(line for line in script if line.startswith("PORTAL_ENV_KEYS="))]
    for name in ("sed_escape", "env_file_set", "sync_env_portal"):
        start = next(i for i, line in enumerate(script) if line.startswith(f"{name}() {{"))
        end = start if script[start].rstrip().endswith("}") else script.index("}", start)
        pieces.extend(script[start : end + 1])
    program = "\n".join(
        ["set -euo pipefail", 'info() { echo "==> $*"; }', *pieces, "sync_env_portal"]
    )
    got = subprocess.run(
        ["bash", "-c", program],
        env={
            "PATH": os.environ["PATH"],
            "ENV_FILE": str(env_file),
            "APP_SLUG": "testplatform",
            **env,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return got.stdout


def _values(env_file: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            found.setdefault(key, []).append(value)
    return found


def test_포털_설정은_준_것만_env_에_적고_접두어를_더한다(tmp_path: Path) -> None:
    """`sudo PORTAL_JWKS_URL=… PORTAL_LAUNCH_URL=… ./deploy.sh update` — 예시의 주석 선언을
    살려 그 자리에 적고, 포털 nginx 가 /<id>/ 를 벗겨 넘기므로 접두어 · 프록시 믿기를
    더한다."""
    env_file = tmp_path / ".env"
    shutil.copy(DEPLOY / ".env.production.example", env_file)
    jwks = "http://portal.example:8088/.well-known/jwks.json"
    launch = "https://portal.example/launch/testplatform"
    _sync_portal(env_file, PORTAL_JWKS_URL=jwks, PORTAL_LAUNCH_URL=launch)
    values = _values(env_file)
    assert values["PORTAL_JWKS_URL"] == [jwks]
    assert values["PORTAL_LAUNCH_URL"] == [launch]
    assert values["PUBLIC_PATH"] == ["/testplatform"]
    assert values["TRUST_PROXY"] == ["true"]
    assert "PORTAL_AUDIENCE" not in values  # 안 준 것은 안 적는다(비우면 slug)

    # 두 번 돌려도 한 줄씩이다.
    _sync_portal(env_file, PORTAL_JWKS_URL=jwks)
    assert _values(env_file)["PORTAL_JWKS_URL"] == [jwks]


def test_포털_설정을_안_주면_env_를_건드리지_않는다(tmp_path: Path) -> None:
    """손으로 고친 `.env` 를 다음 update 가 지우지 않는다 — 접두어도 포털 id 에 맞춰 고친
    그대로."""
    env_file = tmp_path / ".env"
    before = (
        "APP_SLUG=testplatform\nPORTAL_JWKS_URL=http://hand/.well-known/jwks.json\n"
        "PUBLIC_PATH=/hwaxapp"
    )
    env_file.write_text(before, encoding="utf-8")
    _sync_portal(env_file)
    assert env_file.read_text(encoding="utf-8") == before

    # 하나만 바꿔도 손으로 적은 접두어는 그대로다(끝 개행이 없던 파일에 이어 붙이지 않는다).
    _sync_portal(env_file, PORTAL_LAUNCH_URL="https://portal.example/launch/hwaxapp")
    values = _values(env_file)
    assert values["PUBLIC_PATH"] == ["/hwaxapp"]
    assert values["PORTAL_LAUNCH_URL"] == ["https://portal.example/launch/hwaxapp"]
    assert values["TRUST_PROXY"] == ["true"]


def test_빈_값을_주면_포털_로그인을_끈다(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("PORTAL_JWKS_URL=http://old/.well-known/jwks.json\n", encoding="utf-8")
    _sync_portal(env_file, PORTAL_JWKS_URL="")
    values = _values(env_file)
    assert values["PORTAL_JWKS_URL"] == [""]
    assert "PUBLIC_PATH" not in values  # 끄는 길에 접두어를 더하지 않는다


# --- HWAX 포털 연동 요청서(`deploy.sh hwax-request`, ADR 0028) ---------------------------


def _hwax_request(
    bundle: Path, tmp_path: Path, slug: str, *, port: int, **env: str
) -> tuple[str, Path, Path]:
    """그 인스턴스의 요청서를 만든다 — (화면에 찍힌 것, 요청서, .env). 운영 계정으로 root
    없이."""
    install = tmp_path / slug
    install.mkdir(exist_ok=True)
    env_file = Path(env.get("DATA_DIR", str(install))) / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    if not env_file.exists():
        # 설치가 만든 .env 의 모양 — 예시의 주석 선언(`# HEAX_SSO_SECRET=`)이 그 자리에 있다.
        shutil.copy(DEPLOY / ".env.production.example", env_file)
    got = subprocess.run(
        ["bash", str(bundle / "deploy.sh"), "hwax-request"],
        cwd=bundle,
        env={
            **os.environ,
            "OPERATOR": getpass.getuser(),
            "INSTALL_DIR": str(install),
            "ETC": str(tmp_path / "etc"),
            "APP_SLUG": slug,
            "APP_PORT": str(port),
            **env,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return got.stdout + got.stderr, install / "HWAX-연동요청서.md", env_file


def _secret(env_file: Path) -> str:
    return _values(env_file)["HEAX_SSO_SECRET"][-1]


def test_요청서는_인스턴스의_값으로_채우고_자리를_안_남긴다(
    bundle: Path, tmp_path: Path
) -> None:
    """같은 코드의 인스턴스 둘 — slug · 이름 · 포트 · 접두가 각자의 것으로 들어가고, 틀의 자리
    (`{{…}}`)와 틀 설명 상자는 남지 않는다. 손으로 고쳐 쓰면 한 장에 옆 인스턴스의 포트가
    남는다."""
    shown, first, _ = _hwax_request(
        bundle, tmp_path, "jajuverify", port=8050, APP_NAME="자주검증 Datahub"
    )
    _, second, _ = _hwax_request(
        bundle,
        tmp_path,
        "failuretrace",
        port=8060,
        APP_NAME="Failure Trace",
        APP_TAGLINE="불량 & 원인 | 추적",
    )
    for doc, slug, prefix, app, mcp, other in (
        (first, "jajuverify", "JAJUVERIFY", 8050, 8052, "failuretrace"),
        (second, "failuretrace", "FAILURETRACE", 8060, 8062, "jajuverify"),
    ):
        text = doc.read_text(encoding="utf-8")
        assert "{{" not in text and "}}" not in text, re.findall(r"\{\{\w*\}\}", text)
        assert "<!-- 틀" not in text and "이 파일은 틀" not in text
        assert f"{slug}=http://<A 주소>:{app}/" in text  # 화면 라우트(끝 / = STRIP)
        assert f'"url": "http://<A 주소>:{mcp}/mcp"' in text  # 게이트웨이 백엔드
        assert f"{slug}:{prefix}" in text and f"{prefix}_SSO_SECRET" in text
        assert f"{prefix}_SSO_URL=http://<A 주소>:{app}/api/auth/sso" in text
        assert f"audience: {slug}" in text and f"url: /{slug}/api/auth/portal-callback" in text
        assert f"gateway: [{slug}]" in text
        assert other not in text and str(app + 10) not in text
    assert "name: 자주검증 Datahub" in first.read_text(encoding="utf-8")
    # 이름 · 소개에 sed 가 뜻으로 읽는 글자(& |)가 있어도 그대로 들어간다.
    assert "tagline: 불량 & 원인 | 추적" in second.read_text(encoding="utf-8")
    assert str(first) in shown


def test_위임_비밀은_env_에만_있고_요청서와_화면에_없다(bundle: Path, tmp_path: Path) -> None:
    """비밀이 없으면 만들어 `.env` 에 적는다(한 번만 — 이미 포털에 전한 값을 바꾸면 위임이 전부
    401 이다). 요청서는 메일 · 메신저로 돈다 — 비밀도 내부 주소도 거기 없다."""
    ha = {
        "HA_ROLE": "master",
        "PEER_IP": "10.20.30.2",
        "SELF_IP": "10.20.30.1",
        "DB_VIP": "10.20.30.11",
        "PUBLIC_HOST": "portal.example.local",
        "VRRP_IFACE": "eth0",
        "DATA_DIR": str(tmp_path / "data" / "jajuverify"),
    }
    shown, doc, env_file = _hwax_request(bundle, tmp_path, "jajuverify", port=8050, **ha)
    secret = _secret(env_file)
    assert re.fullmatch(r"[0-9a-f]{64}", secret)
    # 예시의 주석 선언을 살려 그 자리에 적었다 — 한 줄뿐이다.
    assert _values(env_file)["HEAX_SSO_SECRET"] == [secret]
    assert "restart" in shown  # 새 비밀은 앱이 다시 떠야 읽는다
    text = doc.read_text(encoding="utf-8")
    assert secret not in text and secret not in shown
    # 내부 주소는 파일에 없다(화면에는 따로 전할 것으로 찍힌다). 남는 것은 포털 박스 안의
    # 루프백뿐.
    for address in ("10.20.30.1", "10.20.30.2", "10.20.30.11"):
        assert address not in text
        assert address in shown or address == "10.20.30.11"
    assert set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)) <= {"127.0.0.1"}

    # 다시 만들어도 비밀은 그대로다.
    shown, _, _ = _hwax_request(bundle, tmp_path, "jajuverify", port=8050, **ha)
    assert _secret(env_file) == secret and _values(env_file)["HEAX_SSO_SECRET"] == [secret]
    assert "새로 만들어" not in shown


def test_요청서의_위험_도구는_틀과_같다(bundle: Path, tmp_path: Path) -> None:
    """포털 담당이 `_INVOKE_DENY_EXACT` 에 넣을 목록이 채운 요청서에도 그대로 실린다(틀과
    서버의 목록이 같은지는 MCP 시험이 본다)."""
    _, doc, _ = _hwax_request(bundle, tmp_path, "jajuverify", port=8050)
    text = doc.read_text(encoding="utf-8")
    block = text[text.index("<!-- 위험 도구: 시작 -->") : text.index("<!-- 위험 도구: 끝 -->")]
    assert {"objects_delete", "relation_remove", "job_cancel", "ontology_delete"} <= set(
        re.findall(r'"([a-z_]+)"', block)
    )


def test_허용_IP_는_env_로_받고_비밀은_받지_않는다(tmp_path: Path) -> None:
    """`sudo HEAX_SSO_ALLOWED_IPS=… ./deploy.sh update` 는 적는다. 비밀은 env 로 줘도 적지
    않는다 — `info` 가 값을 화면에 찍고 셸 기록에 남는다."""
    env_file = tmp_path / ".env"
    shutil.copy(DEPLOY / ".env.production.example", env_file)
    shown = _sync_portal(
        env_file, HEAX_SSO_ALLOWED_IPS="10.1.2.3,10.1.3.0/24", HEAX_SSO_SECRET="x" * 64
    )
    values = _values(env_file)
    assert values["HEAX_SSO_ALLOWED_IPS"] == ["10.1.2.3,10.1.3.0/24"]
    assert "HEAX_SSO_SECRET" not in values and "x" * 64 not in shown
