#!/usr/bin/env bash
# 배포 — 준비 · 설치 · 갱신 · 초기화 · 상태.
#
#   sudo ./deploy.sh prepare   최초 1회: apt 패키지, apptainer(공식 PPA), postgres, DB 역할·DB 생성
#   sudo ./deploy.sh install   SIF 배치 + .env 생성 + systemd + 마이그레이션 + 시드 + 기동
#   sudo ./deploy.sh update    SIF 교체 + 마이그레이션 + 재시작 (자료 그대로)
#   sudo ./deploy.sh reset     DB 통째로 초기화 (파괴적)
#   sudo ./deploy.sh status    서비스 상태 + /api/health
#   sudo ./deploy.sh           자동: 설치된 흔적이 없으면 install, 있으면 update
#   sudo ./deploy.sh remove    이 인스턴스를 서버에서 지운다 — 유닛 · DB · 설치 폴더 (공용 폴더는 남긴다)
#   sudo ./deploy.sh setup     **물어보고 알아서** — 이름 · 역할 · 상대 서버를 답하면 prepare → (DB 주/대기)
#                              → install 을 순서대로. 서버 두 대 첫 설치는 이것 하나면 된다.
#
# 서버 두 대 이중화(ha.sh · pg-ha.sh — 자세한 것은 README 「이중화」):
#   sudo ./deploy.sh db-primary | db-standby --from <IP> | db-promote | db-demote | db-status
#   sudo ./deploy.sh lb        메인 서버용 nginx 조각 · keepalived(DB VIP)를 env 에서 다시 만들고 반영
#
# **번들 하나로 여러 플랫폼(인스턴스)을 설치한다.** 어느 플랫폼인지는 `APP_SLUG` 가 정한다 —
# 처음 한 번 env 로 주면(`APP_SLUG=plmhub APP_NAME="PLM 기준정보" APP_PORT=8040`)
# /etc/platform-instances/<slug>.conf 에 남아 다음부터는 `sudo APP_SLUG=plmhub ./deploy.sh update` 로
# 충분하고, 이 서버에 인스턴스가 하나뿐이면 그것마저 생략된다. 안 주면 번들의 기본값
# (BUILD_INFO — 틀의 이름)으로 뜬다. slug 하나에서 DB · 유닛 · 경로 · 주소가 전부 나온다.
#
# 어디서 실행해도 된다. 짝이 되는 파일(app.sif · .env.example · 유닛 템플릿)을
# 자기 디렉터리에서 찾는다.

set -euo pipefail

err()  { echo "오류: $*" >&2; exit 1; }
info() { echo "==> $*"; }
warn() { echo "경고: $*" >&2; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ───────────────────────── 번들이 알려 주는 것 ─────────────────────────
[[ -f "$HERE/BUILD_INFO" ]] || err "BUILD_INFO 가 없습니다 ($HERE). 릴리스 번들 안에서 실행하세요."
bundle() { sed -n "s|^$1=||p" "$HERE/BUILD_INFO" | tail -n1; }

APP_NAME_DEFAULT="$(bundle app_name)"
APP_SLUG_DEFAULT="$(bundle app_slug)"
APP_PORT_DEFAULT="$(bundle port)"
VERSION="$(bundle version)"
[[ -n "$APP_SLUG_DEFAULT" && -n "$APP_NAME_DEFAULT" ]] || err "BUILD_INFO 에 app_name/app_slug 가 없습니다."

# ───────────────────────── 사전 확인 ─────────────────────────
# 'render' 는 아무것도 바꾸지 않는다 — root 없이 유닛 · nginx · keepalived 설정을 보여 준다.
RENDER_ONLY=0; [[ "${1:-}" =~ ^(render|units|-h|--help|help)$ ]] && RENDER_ONLY=1
[[ "${1:-}" == "setup" && "${2:-}" == "--plan" ]] && RENDER_ONLY=1
# 'hwax-request' 는 그 설치의 .env 와 설치 폴더만 만진다 — 둘 다 운영 계정의 것이라 그 계정으로는
# sudo 없이도 된다(시험도 그 길로 돈다).
OWN_FILES_ONLY=0; [[ "${1:-}" == "hwax-request" ]] && OWN_FILES_ONLY=1
[[ $EUID -eq 0 || $RENDER_ONLY -eq 1 || $OWN_FILES_ONLY -eq 1 ]] || err "root 로 실행하세요 (sudo)"

OPERATOR="${OPERATOR:-${SUDO_USER:-}}"
[[ -z "$OPERATOR" && $OWN_FILES_ONLY -eq 1 && $EUID -ne 0 ]] && OPERATOR="$(id -un)"
[[ -n "$OPERATOR" && "$OPERATOR" != "root" ]] \
    || err "운영 계정을 알 수 없습니다. 일반 사용자로 sudo 하거나 OPERATOR=<이름> 을 주세요."
[[ $RENDER_ONLY -eq 1 ]] || id "$OPERATOR" >/dev/null 2>&1 || err "그런 계정이 없습니다: $OPERATOR"

# ───────────────────────── 이 설치는 무슨 플랫폼인가 ─────────────────────────
# 우선순위: env > /etc/platform-instances/<slug>.conf(지난 설치가 남긴 것) > 번들 기본값.
ETC="${ETC:-}"
INSTANCES_DIR="$ETC/etc/platform-instances"
instance_conf_get() { [[ -f "$INSTANCES_DIR/$1.conf" ]] && sed -n "s|^$2=||p" "$INSTANCES_DIR/$1.conf" | tail -n1 || true; }

if [[ -z "${APP_SLUG:-}" ]]; then
    # slug 를 안 줬다 — 이 서버에 설치된 인스턴스가 하나면 그것, 여럿이면 물어본다.
    mapfile -t _known < <(ls "$INSTANCES_DIR"/*.conf 2>/dev/null | xargs -rn1 basename | sed 's/\.conf$//')
    if [[ ${#_known[@]} -eq 1 ]]; then
        APP_SLUG="${_known[0]}"
    elif [[ ${#_known[@]} -gt 1 ]]; then
        err "이 서버에 인스턴스가 여럿입니다: ${_known[*]} — APP_SLUG=<slug> 로 어느 것인지 주세요."
    else
        APP_SLUG="$APP_SLUG_DEFAULT"
    fi
fi
[[ "$APP_SLUG" =~ ^[a-z][a-z0-9]{0,31}$ ]] || err "APP_SLUG 는 소문자·숫자 한 덩어리 32자 이내여야 합니다: $APP_SLUG"
APP_NAME="${APP_NAME:-$(instance_conf_get "$APP_SLUG" APP_NAME)}"; APP_NAME="${APP_NAME:-$APP_NAME_DEFAULT}"
APP_TAGLINE="${APP_TAGLINE:-$(instance_conf_get "$APP_SLUG" APP_TAGLINE)}"
EXTENSIONS="${EXTENSIONS-$(instance_conf_get "$APP_SLUG" EXTENSIONS)}"
APP_PORT="${APP_PORT:-$(instance_conf_get "$APP_SLUG" APP_PORT)}"; APP_PORT="${APP_PORT:-$APP_PORT_DEFAULT}"

# **설치 경로·DB·유닛 이름이 전부 slug 에서 나온다.** 한 서버에 여러 플랫폼을
# 얹을 때 이것이 겹치면 서로를 덮어쓴다.
INSTALL_DIR="${INSTALL_DIR:-$(instance_conf_get "$APP_SLUG" INSTALL_DIR)}"
INSTALL_DIR="${INSTALL_DIR:-/home/$OPERATOR/apps/$APP_SLUG}"
PG_VERSION="${PG_VERSION:-16}"                          # 두 서버가 같아야 복제가 된다
DB_NAME="${DB_NAME:-$APP_SLUG}"
DB_USER="${DB_USER:-$APP_SLUG}"
# slug 에서 나오는 **유닛 이름들을 한 곳에서** 만든다.
#
# ⚠️ 두 벌로 적어 두었더니 설치 마법사(`cmd_setup`)가 답을 받아 다시 계산하는 목록에서
#    **워커만 빠졌다.** 그래서 한 서버에 두 번째 플랫폼을 깔 때 첫 플랫폼의 워커 유닛을
#    덮어썼다(실측) — 두 플랫폼이 한 워커를 공유하게 되고, 그 워커는 한쪽 DB 만 본다.
#    유닛이 하나 늘 때 고칠 자리가 둘이면 한쪽은 언젠가 빠진다.
derive_unit_names() {
    SERVICE_NAME="$APP_SLUG"
    SERVICE_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
    WORKER_SERVICE_NAME="${APP_SLUG}-worker"
    WORKER_SERVICE_UNIT="/etc/systemd/system/${WORKER_SERVICE_NAME}.service"
    SYNC_SERVICE_NAME="${APP_SLUG}-sync"
    SYNC_SERVICE_UNIT="/etc/systemd/system/${SYNC_SERVICE_NAME}.service"
    SYNC_TIMER_UNIT="/etc/systemd/system/${SYNC_SERVICE_NAME}.timer"
    MCP_SERVICE_NAME="${APP_SLUG}-mcp"
    MCP_SERVICE_UNIT="/etc/systemd/system/${MCP_SERVICE_NAME}.service"
    BACKUP_SERVICE_NAME="${APP_SLUG}-backup"
    BACKUP_SERVICE_UNIT="/etc/systemd/system/${BACKUP_SERVICE_NAME}.service"
    BACKUP_TIMER_UNIT="/etc/systemd/system/${BACKUP_SERVICE_NAME}.timer"
}
derive_unit_names

# sed 치환값에 들어가면 뜻을 갖는 글자(& | \)를 막는다 — 이름에 '&' 가 있으면 매치 전체가 들어간다.
sed_escape() { printf '%s' "$1" | sed -e 's/[&|\\]/\\&/g'; }

# 이 인스턴스의 설정을 남긴다 — 다음 배포가 기억한다.
instance_save() {
    mkdir -p "$INSTANCES_DIR"
    cat > "$INSTANCES_DIR/$APP_SLUG.conf" <<EOF
# $APP_NAME ($APP_SLUG) — deploy.sh 가 기억한다(env 로 덮으면 갱신). 설치 뒤 slug 는 바꾸지 않는다.
APP_NAME=$APP_NAME
APP_TAGLINE=$APP_TAGLINE
APP_PORT=$APP_PORT
MCP_PORT=${MCP_PORT:-}
EXTENSIONS=$EXTENSIONS
INSTALL_DIR=$INSTALL_DIR
DATA_DIR=${DATA_DIR:-}
BACKUP_HOST_DIR=${BACKUP_HOST_DIR:-}
EOF
    chmod 644 "$INSTANCES_DIR/$APP_SLUG.conf"
}

as_op() { sudo -u "$OPERATOR" "$@"; }

# ───────────────────────── 이중화 · 공용 스토리지 ─────────────────────────
# ha.sh 가 /etc/platform-ha.conf(호스트) 와 $INSTALL_DIR/deploy.conf(플랫폼) 를 읽어
# HA_ROLE · LB_MODE · PEER_IP · DB_VIP · PUBLIC_HOST · DATA_DIR (local 이면 WEB_VIP) 를 채운다(env 가 우선).
[[ -f "$HERE/ha.sh" ]] || err "ha.sh 가 $HERE 에 없습니다 (릴리스 번들에서 실행하세요)"
# shellcheck disable=SC1091
source "$HERE/ha.sh"
SELF_IP_GIVEN="${SELF_IP:-}"   # env 로 직접 준 것만 — setup 이 다시 잴 때 이것은 지킨다
ha_load

# **무엇이 공용이고 무엇이 로컬인가.** DATA_DIR(/data/<공통폴더>/<slug>)를 주면 첨부 · 백업 ·
# 인증서 · .env 는 거기(두 서버가 같이 본다), 코드(SIF) · 로그는 이 서버($INSTALL_DIR)다.
# 안 주면 지금까지처럼 전부 $INSTALL_DIR — 단독 서버.
if [[ -n "$DATA_DIR" ]]; then
    ENV_FILE="$DATA_DIR/.env"
    FILESTORE_HOST_DIR="$DATA_DIR/filestore"
    BACKUP_HOST_DIR="$DATA_DIR/backup"
    # 중계의 정의 · 미러 · 상태 — **공용**이다. 두 서버의 워커 어느 쪽이 돌려도 같은 상태를 본다.
    RELAY_HOST_DIR="$DATA_DIR/relay"
else
    ENV_FILE="$INSTALL_DIR/.env"
    FILESTORE_HOST_DIR="$INSTALL_DIR/filestore"
    BACKUP_HOST_DIR="${BACKUP_HOST_DIR:-}"
    RELAY_HOST_DIR="$INSTALL_DIR/relay"
fi
LOG_HOST_DIR="$INSTALL_DIR/logs"
# 앱이 DB 를 찾는 주소 — DB VIP 가 있으면 그것, 이중화인데 아직 없으면 주(master) 서버, 아니면 로컬.
if [[ -n "$DB_VIP" ]]; then DB_HOST="$DB_VIP"
elif [[ "$HA_ROLE" == "backup" ]]; then DB_HOST="$PEER_IP"
elif [[ "$HA_ROLE" == "master" ]]; then DB_HOST="$SELF_IP"
else DB_HOST="localhost"; fi
DB_HOST="${DB_HOST_OVERRIDE:-$DB_HOST}"

# 중계 타이머(`<slug>-sync`) — 몇 분마다 차례가 된 파이프라인을 작업으로 넣는다(`scripts/relay_due.py`).
# 돌리는 것은 작업 워커다. 앱과 같은 SIF. 이름은 `derive_unit_names` 가 만든다(위).
SYNC_ENABLED="${SYNC_ENABLED:-1}"                      # 0 으로 두면 타이머 안 설치

# 작업 워커 — 파이프라인 실행처럼 오래 걸리는 일을 요청 밖에서 돌린다. 앱과 같은 SIF.
# **없으면 파이프라인 실행이 영영 「대기」 다.** 두 서버에 하나씩 뜬다(docs/sp/작업-워커-설계.md).

# ───────────────────────── MCP 서버 (Claude 연동, 선택) ─────────────────────────
# 별도 venv + 별도 systemd 유닛. 백엔드 SIF 와 의존성이 충돌해 컨테이너에 못 넣는다.
# 유닛 이름은 `derive_unit_names` 가 만든다(위).
# 설치된 systemd 유닛에서 Environment=KEY=VALUE 값을 읽는다(없으면 빈 문자열).
# → 한 번 배포한 MCP 설정을 다음 배포가 자동으로 기억하게 하는 장치.
unit_env() {  # $1=key
    [[ -f "$MCP_SERVICE_UNIT" ]] || return 0
    sed -n "s|^Environment=$1=||p" "$MCP_SERVICE_UNIT" | tail -n1
}

MCP_ENABLED="${MCP_ENABLED:-1}"                        # 0 으로 두면 MCP 전체 건너뜀
# 우선순위: 명시한 env > 설치된 유닛에 저장된 값 > 기본값(번들의 BUILD_INFO).
# → 최초 한 번 'MCP_HOST=0.0.0.0 ./deploy.sh' 하면, 이후 './deploy.sh update' 가
#   매번 다시 지정하지 않아도 같은 값을 유지한다(되돌리려면 그때만 env 로 덮어쓰기).
# **앱 포트 +2** — 인스턴스마다 앱 포트가 다르므로 번들의 mcp_port 는 기본 포트일 때만 맞는다.
MCP_PORT_DEFAULT="$(bundle mcp_port)"
[[ "$APP_PORT" == "$APP_PORT_DEFAULT" && -n "$MCP_PORT_DEFAULT" ]] || MCP_PORT_DEFAULT=$((APP_PORT + 2))
MCP_HOST="${MCP_HOST:-$(unit_env MCP_HOST)}"
# 이중화면 상대 서버의 nginx 도 이 MCP 에 붙어야 한다 — 로컬에만 열면 절반의 요청이 502 다.
[[ -n "$HA_ROLE" ]] && MCP_HOST="${MCP_HOST:-0.0.0.0}"; MCP_HOST="${MCP_HOST:-127.0.0.1}"
MCP_PORT="${MCP_PORT:-$(unit_env MCP_PORT)}";  MCP_PORT="${MCP_PORT:-$MCP_PORT_DEFAULT}"
MCP_API_BASE="${MCP_API_BASE:-$(unit_env PLATFORM_API_BASE)}"
MCP_API_BASE="${MCP_API_BASE:-http://127.0.0.1:$APP_PORT}"
# DNS rebinding 보호 허용 Host(쉼표구분). 비우면 server.py 가 비-localhost 바인딩 시
# 보호를 끈다(사내망). 외부 노출 시 도메인/IP 지정 권장. 이 값도 위처럼 기억된다.
MCP_ALLOWED_HOSTS="${MCP_ALLOWED_HOSTS:-$(unit_env MCP_ALLOWED_HOSTS)}"
# 우리 nginx(LB_MODE=local)는 Host 를 그대로 넘기므로 공개 호스트명이 허용 목록에 있어야 한다.
# 메인 서버의 nginx 가 무엇을 넘기는지는 모르므로 그때는 비워 둔다(0.0.0.0 이면 보호를 끈다 — 사내망).
[[ "$HA_ROLE" != "" && "$LB_MODE" == "local" && -z "$MCP_ALLOWED_HOSTS" ]] && MCP_ALLOWED_HOSTS="$PUBLIC_HOST,$WEB_VIP,$SELF_IP,$PEER_IP"

# ───────────────────────── 포트가 겹치지 않나 ─────────────────────────
# 한 서버에 쌍둥이를 여럿 얹는다. 앱 포트는 사람이 정하고 **MCP 는 앱 포트 +2 로 딸려 온다** —
# 그래서 두 번째 플랫폼이 앞 플랫폼의 MCP 포트를 앱 포트로 고르면(8040 · 8042) 조용히 겹친다.
# 늦게 뜨는 쪽이 「주소가 이미 사용 중」 으로 죽고, 그 사실은 systemd 로그에만 남는다.
#
# **물어보지 않고 막는다** — 이미 설치된 인스턴스의 기록(그리고 그 MCP 유닛)을 보고 겹치면
# 멈춘다. 10 씩 벌리라는 안내만으로는 몇 달 뒤 다른 사람이 그 규칙을 모른다.
other_mcp_port() {  # $1=slug — 그 인스턴스의 MCP 포트(유닛 > 기록 > 앱 포트 +2)
    local slug="$1" unit="/etc/systemd/system/${slug}-mcp.service" got=""
    [[ -f "$unit" ]] && got="$(sed -n 's|^Environment=MCP_PORT=||p' "$unit" | tail -n1)"
    [[ -z "$got" ]] && got="$(instance_conf_get "$slug" MCP_PORT)"
    local app; app="$(instance_conf_get "$slug" APP_PORT)"
    [[ -z "$got" && -n "$app" ]] && got=$((app + 2))
    printf '%s' "$got"
}

# 이 서버에 이미 있는 인스턴스들 **다음 10 자리** — 설치 마법사가 기본값으로 보여 준다.
free_port() {
    local file slug taken=0 got="$APP_PORT_DEFAULT"
    for file in "$INSTANCES_DIR"/*.conf; do
        [[ -f "$file" ]] || continue
        slug="$(basename "$file" .conf)"
        [[ "$slug" == "$APP_SLUG" ]] && continue
        taken="$(instance_conf_get "$slug" APP_PORT)"
        [[ -n "$taken" && "$taken" -ge "$got" ]] && got=$((taken + 10))
    done
    printf '%s' "$got"
}

# 화면에 찍을 호스트 — 바인딩이 `0.0.0.0` 이면 그것은 주소가 아니다.
mcp_shown_host() {
    case "$MCP_HOST" in
        0.0.0.0|"::"|"") printf '%s' "${SELF_IP:-${PUBLIC_HOST:-이-서버}}" ;;
        *) printf '%s' "$MCP_HOST" ;;
    esac
}

check_port_clash() {
    local file slug oapp omcp mine
    for file in "$INSTANCES_DIR"/*.conf; do
        [[ -f "$file" ]] || continue
        slug="$(basename "$file" .conf)"
        [[ "$slug" == "$APP_SLUG" ]] && continue
        oapp="$(instance_conf_get "$slug" APP_PORT)"
        omcp="$(other_mcp_port "$slug")"
        for mine in "$APP_PORT:앱" "$MCP_PORT:MCP"; do
            local port="${mine%%:*}" what="${mine##*:}"
            if [[ -n "$oapp" && "$port" == "$oapp" ]] || [[ -n "$omcp" && "$port" == "$omcp" ]]; then
                err "$APP_SLUG 의 $what 포트 $port 가 이미 설치된 $slug 와 겹칩니다" \
                    "($slug: 앱 ${oapp:-?} · MCP ${omcp:-?})." \
                    "MCP 는 앱 포트 +2 로 딸려 옵니다 — 앱 포트를 10 이상 벌려 주세요:" \
                    "sudo APP_SLUG=$APP_SLUG APP_PORT=<다른 포트> ./deploy.sh update"
            fi
        done
    done
    [[ "$APP_PORT" == "$MCP_PORT" ]] && err "앱 포트와 MCP 포트가 같습니다($APP_PORT) — MCP_PORT 를 다시 주세요."
    return 0
}
check_port_clash

# ───────────────────────── 조각들 ─────────────────────────
# apptainer 는 **우분투 기본 저장소에 없다** — 공식 PPA 에만 있다. `apt-get install apptainer`
# 를 바로 부르면 새 서버에서 「Unable to locate package」 로 멈추고, set -e 라 그 뒤의 준비가
# 통째로 안 된다. CI(`.github/actions/apptainer`)가 까는 길과 같은 길로 간다.
APPTAINER_PPA="ppa:apptainer/ppa"
APPTAINER_DOCS="https://apptainer.org/docs/admin/main/installation.html"
APPTAINER_DEBS="https://github.com/apptainer/apptainer/releases"
OS_RELEASE="${OS_RELEASE:-/etc/os-release}"

# **source 하지 않는다** — os-release 의 VERSION 이 번들 버전(VERSION)을 덮는다.
os_id() { sed -n 's/^ID=//p' "$OS_RELEASE" 2>/dev/null | tr -d '"'; }

ensure_apptainer() {
    if command -v apptainer >/dev/null 2>&1; then
        info "apptainer 가 이미 있습니다: $(apptainer --version 2>/dev/null || echo '?')"
        return 0
    fi
    # **번들에 동봉한 .deb 가 1순위.** 폐쇄망 서버는 PPA 에 못 닿아 사람이 .deb 를 구해 와야 했다
    # (첫 서버 설치 때 실제로 겪었다). build_bundle.sh 가 빌드 머신(PPA 있음 · 운영과 같은
    # 우분투 24.04/amd64)에서 apptainer 와 최소 서버에 없을 수 있는 의존성을 받아 둔다.
    local debs="${HERE:-.}/apptainer_debs"
    if compgen -G "$debs/*.deb" >/dev/null; then
        info "apptainer 설치 (번들 동봉 .deb)"
        # apt 가 닿으면 나머지 의존성까지 함께 풀고, 완전 차단이면 dpkg 로 바로.
        if apt-get install -y "$debs"/*.deb 2>/dev/null \
            || { dpkg -i "$debs"/*.deb 2>/dev/null && { apt-get install -y -f 2>/dev/null || true; }; }; then
            if command -v apptainer >/dev/null 2>&1; then
                info "apptainer 설치: $(apptainer --version 2>/dev/null || echo '?')"
                return 0
            fi
        fi
        warn "동봉 .deb 로는 설치되지 않았습니다 — 저장소 · PPA 를 차례로 시도합니다."
    fi
    # 이미 닿는 저장소(사내 미러 등)에 있으면 그것을 쓴다 — PPA 를 굳이 더하지 않는다.
    if apt-cache policy apptainer 2>/dev/null | grep -q 'Candidate: [0-9]'; then
        info "apptainer 설치 (이미 닿는 저장소에서)"
        apt-get install -y --no-install-recommends apptainer
        return 0
    fi
    if [[ "$(os_id)" != "ubuntu" ]]; then
        err "apt 에서 apptainer 를 찾을 수 없고 우분투가 아니라 PPA 를 쓸 수 없습니다. $APPTAINER_DOCS 대로 먼저 설치하고 'sudo ./deploy.sh prepare' 를 다시 돌리세요 — 이미 만든 DB·폴더는 그대로 둡니다."
    fi
    info "apptainer 공식 PPA 추가: $APPTAINER_PPA"
    if ! { apt-get install -y --no-install-recommends software-properties-common \
            && add-apt-repository -y "$APPTAINER_PPA" \
            && apt-get update; }; then
        err "PPA 를 추가하지 못했습니다 — 서버가 ppa.launchpadcontent.net 에 닿지 않는 것 같습니다(폐쇄망·프록시). 이 번들에는 apptainer_debs/ 가 없습니다 — 새 릴리스 번들(.deb 동봉)을 쓰거나, 닿는 PC 에서 apptainer .deb 를 받아($APPTAINER_DEBS) 옮기고 'sudo apt install ./apptainer_*.deb' 로 설치한 뒤 'sudo ./deploy.sh prepare' 를 다시 돌리세요 — 이미 만든 DB·폴더는 그대로 둡니다."
    fi
    apt-get install -y --no-install-recommends apptainer \
        || err "PPA 를 더했는데 apptainer 를 설치하지 못했습니다 — 위의 apt 출력을 확인하세요."
    info "apptainer 설치: $(apptainer --version 2>/dev/null || echo '?')"
}

ensure_dirs() {
    info "설치 폴더 준비: $INSTALL_DIR$( [[ -n "$DATA_DIR" ]] && echo " · 공용 $DATA_DIR" )"
    # **운영 데이터는 SIF 밖이다.** 이미지가 통째로 교체돼도 살아남아야 한다.
    as_op mkdir -p "$INSTALL_DIR" "$LOG_HOST_DIR"
    if [[ -n "$DATA_DIR" ]]; then
        [[ -d "$(dirname "$DATA_DIR")" ]] || err "공용 폴더가 없습니다: $(dirname "$DATA_DIR") — /data 가 마운트됐는지 확인하세요"
        mkdir -p "$DATA_DIR" "$FILESTORE_HOST_DIR" "$BACKUP_HOST_DIR" "$DATA_DIR/tls" "$DATA_DIR/db" \
            "$RELAY_HOST_DIR/pipelines" "$RELAY_HOST_DIR/work" "$RELAY_HOST_DIR/drop"
        chown "$OPERATOR:$OPERATOR" "$DATA_DIR" "$FILESTORE_HOST_DIR" "$BACKUP_HOST_DIR"
        chown -R "$OPERATOR:$OPERATOR" "$RELAY_HOST_DIR"
    else
        as_op mkdir -p "$FILESTORE_HOST_DIR" "$RELAY_HOST_DIR/pipelines" "$RELAY_HOST_DIR/work" \
            "$RELAY_HOST_DIR/drop"
    fi
    instance_save
    ha_save
}

generate_env_if_missing() {
    # **있으면 손대지 않는다.** 여기에 JWT 비밀키가 있어서, 덮으면 전원이 다시
    # 로그인한다. 그리고 그것은 배포가 할 일이 아니다.
    [[ -f "$ENV_FILE" ]] && { info ".env 가 이미 있습니다($ENV_FILE) — 그대로 둡니다."; return 0; }
    [[ -f "$HERE/.env.example" ]] || err ".env.example 이 스크립트 옆에 없습니다 ($HERE)"
    # 비밀번호는 **주 DB** 에서 돌린다. 대기 서버(읽기 전용)에서는 할 수 없다 — 이중화의 두 번째
    # 서버는 공용 폴더의 .env 를 그대로 쓰므로 여기 올 일이 없다. 왔다면 순서가 틀린 것이다.
    if pg_in_recovery; then
        if [[ -n "$DATA_DIR" ]]; then
            err "이 서버의 DB 는 대기입니다. .env 는 주 서버에서 install 할 때 $ENV_FILE 에 만들어집니다 — 주 서버에서 먼저 install"
        else
            err "이 서버의 DB 는 대기입니다. 공용 폴더(DATA_DIR)가 없으니 주 서버의 .env 를 그대로 복사해 두세요:
  scp <주 서버>:$INSTALL_DIR/.env $ENV_FILE   (JWT 비밀 · DB 주소가 두 서버에서 같아야 합니다)"
        fi
    fi

    local pw secret
    pw="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    secret="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"

    info ".env 생성 (난수 비밀키 + DB 비밀번호)"
    # **DB 비밀번호를 여기서 돌린다.** 그래야 .env 와 Postgres 역할이 반드시
    # 같은 값을 갖는다 — 따로 두면 언제 어긋났는지 알 방법이 없다.
    sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
ALTER USER "$DB_USER" WITH PASSWORD '$pw';
SQL

    sed -e "s|REPLACE_WITH_RANDOM_48_CHAR_SECRET|$secret|" \
        -e "s|REPLACE_DB_USER|$DB_USER|" \
        -e "s|REPLACE_DB_PASSWORD|$pw|" \
        -e "s|REPLACE_DB_NAME|$DB_NAME|" \
        -e "s|@localhost:5432/|@$DB_HOST:5432/|" \
        -e "s|^PORT=.*|PORT=$APP_PORT|" \
        -e "s|^APP_SLUG=.*|APP_SLUG=$APP_SLUG|" \
        -e "s|^APP_NAME=.*|APP_NAME=$(sed_escape "$APP_NAME")|" \
        -e "s|^APP_TAGLINE=.*|APP_TAGLINE=$(sed_escape "$APP_TAGLINE")|" \
        -e "s|^EXTENSIONS=.*|EXTENSIONS=$EXTENSIONS|" \
        "$HERE/.env.example" > "$ENV_FILE"
    if [[ -n "$BACKUP_HOST_DIR" ]]; then
        # 컨테이너 안에서 보이는 경로다 — 유닛이 $BACKUP_HOST_DIR 를 /data/backup 에 건다.
        sed -i -e "s|^# BACKUP_DIR=.*|BACKUP_DIR=/data/backup|" "$ENV_FILE"
    fi
    if [[ -n "$HA_ROLE" ]]; then
        # 프록시 뒤 · 경로 접두어. nginx 가 /<slug>/ 를 떼고 넘기고, 앱은 화면 · 쿠키 · API 주소를
        # 이 접두어 아래로 맞춘다. 쿠키의 Secure 는 요청이 https 인지 보고 앱이 스스로 붙인다 —
        # 여기서 강제로 켜면 메인 서버 없이 IP 로 확인하는 동안 새로고침마다 로그인 화면이다.
        cat >> "$ENV_FILE" <<EOF

# --- 이중화 · 프록시 뒤 (deploy.sh 가 넣었다) ---
# https://$PUBLIC_HOST/$APP_SLUG/ — nginx 가 접두어를 떼고 넘긴다. 화면 · 쿠키 · API 가 이 아래로 맞춰진다.
PUBLIC_PATH=/$APP_SLUG
# X-Forwarded-* 를 믿는다 — 프록시(nginx) 뒤에서만 켠다.
TRUST_PROXY=true
EOF
    fi
    chown "$OPERATOR:$OPERATOR" "$ENV_FILE"
    # 비밀키가 들어 있다. 남이 읽을 이유가 없다.
    chmod 600 "$ENV_FILE"
    warn "$ENV_FILE 를 만들었습니다 — 공개 전에 CORS·백업 경로를 확인하세요."
}

# 이름 · 설명은 배포로 바꿀 수 있다 — `sudo APP_NAME=… ./deploy.sh update`.
# **확장은 화면에서 켠다**(시스템 관리자 › 서버 › 「확장 모듈」). 여기 EXTENSIONS 는
# 아직 아무도 켜고 끈 적 없는 확장의 기본값일 뿐이다.
# **slug 만은 못 바꾼다** — DB · 쿠키 · 토큰 · 유닛 이름이 전부 거기서 나왔다.
sync_env_identity() {
    [[ -f "$ENV_FILE" ]] || return 0
    local current; current="$(sed -n 's|^APP_SLUG=||p' "$ENV_FILE" | tail -n1)"
    if [[ -n "$current" && "$current" != "$APP_SLUG" ]]; then
        err ".env 의 APP_SLUG 는 $current 인데 지금은 $APP_SLUG 입니다 — slug 는 설치 뒤 바꿀 수 없습니다 (다른 인스턴스면 APP_SLUG=$current 로)"
    fi
    local key value
    for key in APP_SLUG APP_NAME APP_TAGLINE EXTENSIONS; do
        value="${!key}"
        if grep -q "^$key=" "$ENV_FILE"; then
            sed -i "s|^$key=.*|$key=$(sed_escape "$value")|" "$ENV_FILE"
        else
            printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
        fi
    done
}

# ───────────────────────── HWAX 포털에 붙이기 (ADR 0028) ─────────────────────────
# `sudo PORTAL_JWKS_URL=… PORTAL_LAUNCH_URL=… ./deploy.sh update` — **env 로 준 것만 `.env` 에 적는다.**
# 안 주면 `.env` 그대로다(손으로 고친 값을 다음 update 가 지우지 않는다). 빈 값을 주면(`PORTAL_JWKS_URL=`)
# 비운다 — 끄는 길이다. 설치 기록(/etc/platform-instances)에 두지 않는 까닭: `.env` 는 update 가 덮지 않고
# 이중화의 두 서버가 함께 쓰는 한 벌(DATA_DIR)인데 기록은 서버마다라, 기록에서 옮겨 적으면 한쪽 서버의
# update 가 그 서버의 기록으로 다른 쪽이 적은 값을 덮는다.
# 게이트웨이 위임의 허용 주소(`HEAX_SSO_ALLOWED_IPS`)도 같은 길이다. **비밀(`HEAX_SSO_SECRET`)은 env 로 받지
# 않는다** — 아래 info 가 값을 찍고 셸 기록에 남는다. 비밀은 `hwax-request` 가 만들어 `.env` 에만 적는다.
PORTAL_ENV_KEYS=(PORTAL_JWKS_URL PORTAL_LAUNCH_URL PORTAL_AUDIENCE PORTAL_ISSUER HEAX_SSO_ALLOWED_IPS)

# $1=키 $2=값 — 활성 줄이 있으면 바꾸고, 주석 선언(`# KEY=`, 예시 파일의 것)이 있으면 그 줄을 살리고, 없으면
# 덧붙인다(끝에 개행이 없는 파일이면 먼저 개행 — 안 그러면 앞 줄과 한 줄이 된다).
env_file_set() {
    local key="$1" value
    value="$(sed_escape "$2")"
    if grep -q "^$key=" "$ENV_FILE"; then
        sed -i "s|^$key=.*|$key=$value|" "$ENV_FILE"
    elif grep -qE "^#[[:space:]]*$key=" "$ENV_FILE"; then
        sed -i -E "0,/^#[[:space:]]*$key=.*/s||$key=$value|" "$ENV_FILE"
    else
        if [[ -s "$ENV_FILE" && -n "$(tail -c1 "$ENV_FILE")" ]]; then printf '\n' >> "$ENV_FILE"; fi
        printf '%s=%s\n' "$key" "$2" >> "$ENV_FILE"
    fi
}

sync_env_portal() {
    [[ -f "$ENV_FILE" ]] || return 0
    local key given=0
    for key in "${PORTAL_ENV_KEYS[@]}"; do
        [[ -v "$key" ]] || continue
        env_file_set "$key" "${!key}"
        given=1
        info ".env: $key=${!key:-(비움)}"
    done
    [[ $given -eq 1 ]] || return 0
    [[ -n "$(sed -n 's|^PORTAL_JWKS_URL=||p' "$ENV_FILE" | tail -n1)" ]] || return 0
    # 포털 nginx 는 /<id>/ 를 벗겨 넘긴다 — 접두어가 없으면 로그인 뒤 돌아갈 곳이 포털의 첫 화면이 되고
    # 쿠키 path 도 어긋난다. 이미 적힌 값은 건드리지 않는다(포털 id 를 slug 와 다르게 지은 설치).
    if ! grep -q '^PUBLIC_PATH=' "$ENV_FILE"; then
        env_file_set PUBLIC_PATH "/$APP_SLUG"
        info ".env: PUBLIC_PATH=/$APP_SLUG — 포털의 id 가 slug 와 다르면 .env 에서 /<id> 로 고친다"
    fi
    if ! grep -q '^TRUST_PROXY=' "$ENV_FILE"; then
        env_file_set TRUST_PROXY true
        info ".env: TRUST_PROXY=true — 포털 nginx 뒤라 https 판정(쿠키 Secure) · 접속 주소를 그쪽에서 받는다"
    fi
}

# ───────────────────────── HWAX 포털 연동 요청서 (ADR 0028) ─────────────────────────
# `sudo APP_SLUG=<slug> ./deploy.sh hwax-request` — 요청서 틀(저장소 `docs/[참고] HWAX포탈_연동_요청서.md`, 번들에서는
# 이 옆의 `HWAX-연동요청서.template.md`)을 **이 설치의 값**(slug · 이름 · 포트 · 접두)으로 채워
# `$INSTALL_DIR/HWAX-연동요청서.md` 에 쓴다. 같은 코드의 인스턴스가 여럿이라(같은 서버쌍에 함께 뜬다) 손으로
# 고쳐 쓰면 한 장에 다른 인스턴스의 포트가 남는다 — 그 한 줄이 포털의 라우트가 된다.
#
# 게이트웨이 위임 비밀(`HEAX_SSO_SECRET`)이 `.env` 에 없으면 여기서 만든다(있으면 그대로 — 이미 포털에 전한 값을
# 바꾸면 그 순간 위임이 전부 401 이다). **비밀과 내부 주소는 요청서에 쓰지 않는다** — 그 파일은 메일 · 메신저로
# 돈다. 둘 다 담당에게 따로 전한다(화면에 읽는 길만 찍는다).
HWAX_REQUEST_TEMPLATE="HWAX-연동요청서.template.md"

hwax_request_template() {
    local bundled="$HERE/$HWAX_REQUEST_TEMPLATE" repo="$HERE/../docs/[참고] HWAX포탈_연동_요청서.md"
    if [[ -f "$bundled" ]]; then printf '%s' "$bundled"
    elif [[ -f "$repo" ]]; then printf '%s' "$repo"
    else err "요청서 틀이 없습니다 — $bundled (릴리스 번들에서 실행하세요)"; fi
}

env_file_get() { sed -n "s|^$1=||p" "$ENV_FILE" | tail -n1; }

cmd_hwax_request() {
    [[ -f "$ENV_FILE" ]] || err ".env 가 없습니다($ENV_FILE) — 이 인스턴스를 먼저 install 하세요"
    [[ -d "$INSTALL_DIR" ]] || err "설치 폴더가 없습니다($INSTALL_DIR) — 이 인스턴스를 먼저 install 하세요"
    local template out tmp prefix tagline made=0
    template="$(hwax_request_template)"
    out="$INSTALL_DIR/HWAX-연동요청서.md"
    # 게이트웨이의 env 접두 — 환경변수 이름이라 대문자 · 밑줄(slug 에는 하이픈이 없지만 규칙대로).
    prefix="$(printf '%s' "$APP_SLUG" | tr 'a-z-' 'A-Z_')"
    tagline="${APP_TAGLINE:-(한 줄 소개 — 포털 담당과 정한다)}"

    if [[ -z "$(env_file_get HEAX_SSO_SECRET)" ]]; then
        local secret
        secret="$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(32))')"
        [[ "$secret" =~ ^[0-9a-f]{64}$ ]] || err "위임 비밀을 만들지 못했습니다(openssl · python3)"
        env_file_set HEAX_SSO_SECRET "$secret"
        unset secret
        made=1
    fi

    tmp="$out.tmp"
    # 틀 설명 상자는 뺀다 · 연달아 빈 줄은 하나로(`cat -s`).
    sed -e '/<!-- 틀: 시작 -->/,/<!-- 틀: 끝 -->/d' \
        -e "s|{{ID}}|$APP_SLUG|g" \
        -e "s|{{NAME}}|$(sed_escape "$APP_NAME")|g" \
        -e "s|{{TAGLINE}}|$(sed_escape "$tagline")|g" \
        -e "s|{{APP_PORT}}|$APP_PORT|g" \
        -e "s|{{MCP_PORT}}|$MCP_PORT|g" \
        -e "s|{{PREFIX}}|$prefix|g" \
        -e "s|{{DATE}}|$(date +%F)|g" \
        -e "s|{{VERSION}}|$(sed_escape "$VERSION")|g" \
        "$template" | cat -s > "$tmp"
    if grep -n '{{[A-Z_]*}}' "$tmp" >&2; then
        rm -f "$tmp"
        err "요청서 틀에 채우지 못한 자리가 있습니다(위) — 틀과 deploy.sh 의 자리 목록이 어긋났습니다"
    fi
    mv "$tmp" "$out"
    chmod 644 "$out"
    [[ $EUID -eq 0 ]] && { chown "$OPERATOR:$OPERATOR" "$out" 2>/dev/null || true; }

    info "요청서: $out"
    echo "    $APP_NAME ($APP_SLUG) · 앱 :$APP_PORT · MCP :$MCP_PORT · 게이트웨이 접두 $prefix — 포털 담당에게 이 파일을 보냅니다."
    echo
    echo "  따로 전할 것(파일에 없다):"
    case "$HA_ROLE" in
        master) echo "    · 서버 주소 — A(주, 이 서버) ${SELF_IP:-?} · B(대기) ${PEER_IP:-?}" ;;
        backup) echo "    · 서버 주소 — A(주) ${PEER_IP:-?} · B(대기, 이 서버) ${SELF_IP:-?}" ;;
        *)      echo "    · 서버 주소 — 이 서버 ${SELF_IP:-?} (단독)" ;;
    esac
    echo "    · 위임 비밀 — sudo grep '^HEAX_SSO_SECRET=' $ENV_FILE   (안전한 길로 — 메신저 평문 말고)"
    if [[ $made -eq 1 ]]; then
        echo
        warn "위임 비밀을 새로 만들어 $ENV_FILE 에 적었습니다 — 앱이 읽게 다시 띄우세요:"
        echo "    sudo APP_SLUG=$APP_SLUG ./deploy.sh restart${HA_ROLE:+   (이중화면 다른 서버에서도)}"
    fi
    # 게이트웨이가 우리 MCP 를 부르려면 — 바인딩 · Host 검사.
    if [[ "$MCP_ENABLED" != "1" ]]; then
        warn "MCP 가 꺼져 있습니다(MCP_ENABLED=$MCP_ENABLED) — 게이트웨이가 붙을 MCP 가 없습니다."
    elif [[ "$MCP_HOST" == "127.0.0.1" || "$MCP_HOST" == "localhost" || "$MCP_HOST" == "::1" ]]; then
        warn "MCP 가 이 서버 안에만 열려 있습니다(MCP_HOST=$MCP_HOST) — 게이트웨이가 못 닿습니다:"
        echo "    sudo APP_SLUG=$APP_SLUG MCP_HOST=0.0.0.0 ./deploy.sh update"
    fi
    if [[ -n "$MCP_ALLOWED_HOSTS" ]]; then
        warn "MCP_ALLOWED_HOSTS 가 있습니다 — 게이트웨이가 부르는 주소(포트까지, 예: <A 주소>:$MCP_PORT)가 그 안에 있어야 합니다(없으면 421)."
        echo "    지금: $MCP_ALLOWED_HOSTS"
    fi
    if [[ -z "$(env_file_get PORTAL_JWKS_URL)" ]]; then
        echo
        info "포털 담당에게 JWKS 주소(요청서 4-2)를 받으면:"
        echo "    sudo APP_SLUG=$APP_SLUG PORTAL_JWKS_URL=<받은 주소> PORTAL_LAUNCH_URL=https://hwax.sec.samsung.net/launch/$APP_SLUG ./deploy.sh update"
    fi
    local path; path="$(env_file_get PUBLIC_PATH)"
    if [[ -n "$path" && "$path" != "/$APP_SLUG" ]]; then
        warn "PUBLIC_PATH=$path 가 slug 와 다릅니다 — 요청서는 포털 id 를 slug($APP_SLUG)로 적습니다(이름은 하나로 — 요청서 2절)."
    fi
}

place_sif() {
    [[ -f "$HERE/app.sif" ]] || err "app.sif 가 $HERE 에 없습니다 (릴리스 번들에서 실행하세요)"
    info "app.sif 배치 → $INSTALL_DIR/app.sif"
    install -o "$OPERATOR" -g "$OPERATOR" -m 644 "$HERE/app.sif" "$INSTALL_DIR/app.sif"
}

# 컨테이너 안에서 한 번 실행. **웹 서비스와 같은 SIF·같은 .env 를 쓴다** —
# 다른 파이썬으로 마이그레이션을 돌리면 그 둘이 다른 스키마를 볼 수 있다.
in_container() {
    local backup_bind=()
    [[ -n "$BACKUP_HOST_DIR" ]] && backup_bind=(--bind "$BACKUP_HOST_DIR:/data/backup")
    as_op apptainer exec \
        --bind "$ENV_FILE:/opt/app/backend/.env:ro" \
        --bind "$FILESTORE_HOST_DIR:/data/filestore" \
        --bind "$RELAY_HOST_DIR:/data/relay" \
        --bind "$LOG_HOST_DIR:/data/logs" \
        ${backup_bind[@]+"${backup_bind[@]}"} \
        "$INSTALL_DIR/app.sif" "$@"
}

run_migrations() {
    info "마이그레이션 적용"
    in_container sh -c 'cd /opt/app/backend && /opt/app/venv/bin/python -m alembic upgrade head'
}

# **임시 파일 상한을 앱이 요청마다 걸 수 있게 한다**(ADR 0048). 정렬 · 해시가 디스크로 넘어간 임시 파일에
# 상한이 없으면 무거운 질의 하나가 디스크를 다 채워 DB 가 멈춘다. 그 설정(temp_file_limit)은 superuser 만
# 바꾸므로 앱 계정에 권한을 준다(PostgreSQL 15 부터). 권한이 없으면 앱은 상한 없이 돈다(서버 설정을 따른다) —
# 그래서 실패해도 멈추지 않는다. 대기 서버는 건너뛴다(주에서 준 것이 복제로 넘어온다). 멱등하다.
grant_temp_limit() {
    pg_in_recovery && return 0
    local version
    version="$(sudo -u postgres psql -X -tAqc 'SHOW server_version_num' 2>/dev/null)" || version=""
    if [[ -z "$version" ]]; then
        warn "이 서버의 PostgreSQL 에 붙지 못해 임시 파일 상한 권한을 건너뜁니다(DB 가 다른 서버면 그 서버에서: GRANT SET ON PARAMETER temp_file_limit TO \"$DB_USER\")"
        return 0
    fi
    if (( version < 150000 )); then
        warn "PostgreSQL 15 아래라 앱이 임시 파일 상한을 걸 수 없습니다 — 서버 전체 값은 sudo ./deploy.sh pg-tune 이 권합니다"
        return 0
    fi
    if sudo -u postgres psql -X -qc "GRANT SET ON PARAMETER temp_file_limit TO \"$DB_USER\"" >/dev/null; then
        info "임시 파일 상한 권한: $DB_USER (읽기 요청마다 READ_TEMP_LIMIT, 기본 5GB)"
    else
        warn "임시 파일 상한 권한을 주지 못했습니다 — 앱은 상한 없이 돕니다"
    fi
}

run_seed() {
    # 뿌리 부서와 관리자 계정. **이것 없이는 아무도 로그인할 수 없다** —
    # 가입은 승인이 필요한데 승인할 사람이 없기 때문이다. 멱등하다.
    info "설치 시드 (이미 있으면 건드리지 않음)"
    in_container sh -c 'cd /opt/app/backend && /opt/app/venv/bin/python scripts/seed_install.py'
}

# 유닛 템플릿의 자리표시 — 세 유닛(앱 · 동기화 · 백업)이 같은 경로를 본다.
# 백업 폴더가 없으면 그 --bind 줄을 통째로 뺀다(apptainer 는 없는 원본을 거부한다).
render_unit_paths() {  # $1=template
    # 줄 끝의 '\' 가 다음 줄로 잇는다 — 빈 줄로 두면 거기서 명령이 끊기므로 줄을 아예 지운다.
    local backup_sed
    if [[ -n "$BACKUP_HOST_DIR" ]]; then
        backup_sed="s|^@@BACKUP_BIND@@.*|    --bind $BACKUP_HOST_DIR:/data/backup \\\\|"
    else
        backup_sed='/^@@BACKUP_BIND@@/d'
    fi
    sed -e "s|@@USER@@|$OPERATOR|g" \
        -e "s|@@INSTALL_DIR@@|$INSTALL_DIR|g" \
        -e "s|@@APP_NAME@@|$APP_NAME|g" \
        -e "s|@@APP_SLUG@@|$APP_SLUG|g" \
        -e "s|@@ENV_FILE@@|$ENV_FILE|g" \
        -e "s|@@FILESTORE_DIR@@|$FILESTORE_HOST_DIR|g" \
        -e "s|@@RELAY_DIR@@|$RELAY_HOST_DIR|g" \
        -e "s|@@LOG_DIR@@|$LOG_HOST_DIR|g" \
        -e "s|@@BACKUP_DIR@@|${BACKUP_HOST_DIR:-}|g" \
        -e "$backup_sed" \
        "$1"
}

render_service_unit() {
    [[ -f "$HERE/app.service.template" ]] || err "app.service.template 이 $HERE 에 없습니다"
    info "systemd 유닛 렌더 → $SERVICE_UNIT"
    render_unit_paths "$HERE/app.service.template" > "$SERVICE_UNIT"
    chmod 644 "$SERVICE_UNIT"
    systemctl daemon-reload
}

# ── MCP 서버 (선택) — 별도 venv + systemd. 실패는 전부 비치명적(백엔드 배포 무관). ──
render_mcp_service_unit() {
    [[ -f "$HERE/mcp.service.template" ]] \
        || { warn "mcp.service.template 없음 — MCP 유닛 건너뜀"; return 1; }
    info "MCP systemd 유닛 렌더 → $MCP_SERVICE_UNIT"
    sed -e "s|@@USER@@|$OPERATOR|g" \
        -e "s|@@INSTALL_DIR@@|$INSTALL_DIR|g" \
        -e "s|@@APP_NAME@@|$APP_NAME|g" \
        -e "s|@@APP_SLUG@@|$APP_SLUG|g" \
        -e "s|@@API_BASE@@|$MCP_API_BASE|g" \
        -e "s|@@MCP_HOST@@|$MCP_HOST|g" \
        -e "s|@@MCP_PORT@@|$MCP_PORT|g" \
        -e "s|@@MCP_ALLOWED_HOSTS@@|$MCP_ALLOWED_HOSTS|g" \
        -e "s|@@APP_SLUG@@|$APP_SLUG|g" \
        "$HERE/mcp.service.template" > "$MCP_SERVICE_UNIT"
    chmod 644 "$MCP_SERVICE_UNIT"
    systemctl daemon-reload
}

setup_mcp() {
    [[ "$MCP_ENABLED" == "1" ]] || { info "MCP 비활성(MCP_ENABLED=0) — 건너뜀"; return 0; }
    [[ -d "$HERE/mcp_server" ]] || { warn "번들에 mcp_server/ 없음 — MCP 건너뜀"; return 0; }

    info "MCP 서버 설치 (별도 venv + systemd)"
    local md="$INSTALL_DIR/mcp_server"

    # 1) 소스 배치
    as_op mkdir -p "$md"
    install -o "$OPERATOR" -g "$OPERATOR" -m 644 "$HERE/mcp_server/server.py" "$md/server.py"
    # 사용 가이드 — get_guide 가 읽는 본문. server.py 옆 guide/ 에 있어야 한다.
    # 이게 빠지면 get_guide 가 "가이드를 읽을 수 없습니다" 를 돌려주고, AI 는
    # 도구 설명만으로 일하게 된다(치명적이진 않지만 품질이 떨어진다).
    if [[ -d "$HERE/mcp_server/guide" ]]; then
        as_op mkdir -p "$md/guide"
        as_op cp -r "$HERE/mcp_server/guide/." "$md/guide/"
    fi
    install -o "$OPERATOR" -g "$OPERATOR" -m 644 "$HERE/mcp_server/requirements.txt" "$md/requirements.txt"
    [[ -f "$HERE/mcp_server/README.md" ]] \
        && install -o "$OPERATOR" -g "$OPERATOR" -m 644 "$HERE/mcp_server/README.md" "$md/README.md" || true

    # 2) venv 없으면 생성
    if [[ ! -x "$md/venv/bin/python" ]]; then
        info "MCP venv 생성: $md/venv"
        if ! as_op python3 -m venv "$md/venv"; then
            warn "python3 -m venv 실패('python3-venv' 설치 필요?) — MCP 미설치(백엔드 영향 없음)"; return 0
        fi
    fi

    # 3) 의존성 설치 — 동봉 휠 우선(오프라인), 없으면 온라인 시도
    local pip="$md/venv/bin/pip"
    if [[ -d "$HERE/mcp_server/wheels" ]]; then
        as_op rm -rf "$md/wheels"
        as_op cp -r "$HERE/mcp_server/wheels" "$md/wheels"   # 재실행 안전(중첩 방지)
        info "MCP 의존성 설치 (오프라인 휠)"
        if ! as_op "$pip" install -q --no-index --find-links "$md/wheels" -r "$md/requirements.txt"; then
            warn "오프라인 휠 설치 실패 — MCP 미설치. 수동: cd $md && ./venv/bin/pip install --no-index --find-links wheels -r requirements.txt"; return 0
        fi
    else
        info "MCP 의존성 설치 (온라인 pip — 휠 미동봉)"
        if ! as_op "$pip" install -q -r "$md/requirements.txt"; then
            warn "pip 설치 실패(폐쇄망?). 백엔드 배포는 정상. 수동 설치 후 'systemctl restart $MCP_SERVICE_NAME'"; return 0
        fi
    fi

    # 4) 유닛 렌더 + 기동
    render_mcp_service_unit || return 0
    systemctl enable "$MCP_SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl restart "$MCP_SERVICE_NAME" \
        || warn "MCP 서비스 기동 실패 — 'journalctl -u $MCP_SERVICE_NAME' 확인"
    # **사람이 칠 주소로 찍는다.** `0.0.0.0` 은 주소가 아니다 — 그대로 찍으면 그것을 복사해
    # 등록하고 안 붙는다. 그리고 앱 주소와 **다른 포트**라는 것을 여기서 한 번 말해 준다.
    info "MCP 서버: http://$(mcp_shown_host):$MCP_PORT/mcp  → 백엔드 $MCP_API_BASE"
    info "  (앱은 $APP_PORT, MCP 는 +2 다. 접두어(/$APP_SLUG)는 메인 서버 nginx 뒤에서만 붙는다)"
}

# ── 작업 워커 — 같은 SIF 재사용. **치명적이다**: 없으면 파이프라인 실행이 안 돈다. ──
setup_worker() {
    [[ -f "$HERE/worker.service.template" ]] || err "worker.service.template 없음 — 번들이 깨졌습니다"
    local had_unit=0
    [[ -f "$WORKER_SERVICE_UNIT" ]] && had_unit=1
    info "작업 워커 렌더 → $WORKER_SERVICE_UNIT"
    render_unit_paths "$HERE/worker.service.template" > "$WORKER_SERVICE_UNIT"
    chmod 644 "$WORKER_SERVICE_UNIT"
    systemctl daemon-reload
    # **운영자가 꺼 둔(disable) 워커는 켜지 않는다**(ADR 0049). update 가 다시 켜면 끈 까닭(지도 계산이
    # 서버를 버겁게 했다 등)이 그대로 되살아난다(운영, 2026-11-02). 처음 설치(유닛이 없던 때)만 켠다.
    if (( had_unit )) && [[ "$(systemctl is-enabled "$WORKER_SERVICE_NAME" 2>/dev/null || true)" == "disabled" ]]; then
        warn "작업 워커($WORKER_SERVICE_NAME)는 꺼 둔 상태라 켜지 않습니다 — 다시 켜기: sudo systemctl enable --now $WORKER_SERVICE_NAME"
        return 0
    fi
    systemctl enable "$WORKER_SERVICE_NAME" >/dev/null 2>&1 || true
    systemctl restart "$WORKER_SERVICE_NAME" \
        || warn "작업 워커 기동 실패 — 'journalctl -u $WORKER_SERVICE_NAME' 확인. 파이프라인 실행이 「대기」 에 머뭅니다"
}

# ── 동기화 타이머 — 같은 SIF 재사용, 별도 venv 없음. 비치명적. ──
setup_sync_timer() {
    [[ "$SYNC_ENABLED" == "1" ]] || { info "동기화 타이머 비활성(SYNC_ENABLED=0) — 건너뜀"; return 0; }
    [[ -f "$HERE/sync.service.template" && -f "$HERE/sync.timer.template" ]] \
        || { warn "sync.*.template 없음 — 동기화 타이머 건너뜀"; return 0; }
    info "동기화 타이머 렌더 → $SYNC_TIMER_UNIT"
    render_unit_paths "$HERE/sync.service.template" > "$SYNC_SERVICE_UNIT"
    sed -e "s|@@APP_NAME@@|$APP_NAME|g" "$HERE/sync.timer.template" > "$SYNC_TIMER_UNIT"
    chmod 644 "$SYNC_SERVICE_UNIT" "$SYNC_TIMER_UNIT"
    systemctl daemon-reload
    systemctl enable --now "${SYNC_SERVICE_NAME}.timer" >/dev/null 2>&1 \
        || warn "동기화 타이머 기동 실패 — 'systemctl status ${SYNC_SERVICE_NAME}.timer' 확인"
    info "동기화 타이머: 5분마다 차례가 된 파이프라인을 작업으로 넣습니다 — 돌리는 것은 $WORKER_SERVICE_NAME (journalctl -u $SYNC_SERVICE_NAME)"
}

# ── 백업 타이머 — 백업 폴더를 알 때만. 이중화면 두 서버 모두 걸리고, backup.sh 가 「오늘
# 것이 이미 있으면」 건너뛴다(한 대가 죽어도 다른 대가 받는다). ──
setup_backup_timer() {
    [[ -n "$BACKUP_HOST_DIR" ]] || return 0
    [[ -f "$HERE/backup.service.template" && -f "$HERE/backup.timer.template" ]] \
        || { warn "backup.*.template 없음 — 백업 타이머 건너뜀"; return 0; }
    install -o "$OPERATOR" -g "$OPERATOR" -m 755 "$HERE/backup.sh" "$INSTALL_DIR/backup.sh"
    install -o "$OPERATOR" -g "$OPERATOR" -m 755 "$HERE/restore.sh" "$INSTALL_DIR/restore.sh"
    # 두 서버가 같은 시각에 같은 덤프를 받지 않게 — 대기(backup) 서버는 30분 뒤.
    local at="03:00"; [[ "$HA_ROLE" == "backup" ]] && at="03:30"
    info "백업 타이머 렌더 → $BACKUP_TIMER_UNIT (매일 $at → $BACKUP_HOST_DIR)"
    render_unit_paths "$HERE/backup.service.template" > "$BACKUP_SERVICE_UNIT"
    sed -e "s|@@APP_NAME@@|$APP_NAME|g" -e "s|@@AT@@|$at|g" "$HERE/backup.timer.template" > "$BACKUP_TIMER_UNIT"
    chmod 644 "$BACKUP_SERVICE_UNIT" "$BACKUP_TIMER_UNIT"
    systemctl daemon-reload
    systemctl enable --now "${BACKUP_SERVICE_NAME}.timer" >/dev/null 2>&1 \
        || warn "백업 타이머 기동 실패 — 'systemctl status ${BACKUP_SERVICE_NAME}.timer' 확인"
}

health_check() {
    # 기동 직후에는 아직 안 뜬다. 몇 초 기다려 준다.
    local url="http://127.0.0.1:$APP_PORT/api/health"
    for _ in $(seq 1 10); do
        if curl -fsS --max-time 2 "$url" 2>/dev/null; then echo; return 0; fi
        sleep 1
    done
    warn "$url 이 응답하지 않습니다 — journalctl -u $SERVICE_NAME -n 50"
    return 1
}

# ───────────────────────── 명령 ─────────────────────────
cmd_prepare() {
    info "OS 패키지 설치 (postgresql-$PG_VERSION, python3-venv$( [[ -n "$HA_ROLE" ]] && echo ', keepalived' )$( [[ "$LB_MODE" == "local" ]] && echo ', nginx' ))"
    # python3-venv: MCP 서버가 별도 venv 로 돈다. 없으면 install 때 MCP 만 조용히
    # 건너뛰어지고, 그 사실은 Claude 를 붙이는 날에야 드러난다.
    # apptainer 는 여기 없다 — 기본 저장소에 없어서, 맨 끝 `ensure_apptainer` 가 따로 깐다.
    # **PostgreSQL 은 버전을 박아 깐다.** 두 서버의 버전이 다르면 복제가 안 된다 — 「postgresql」
    # 메타패키지는 OS 가 주는 것을 깔아 서버마다 달라질 수 있다.
    # keepalived 는 DB VIP 를 나중에 받아도 바로 켤 수 있게 이중화면 늘 깐다. nginx 는 우리가 LB 일 때만.
    local ha_pkgs=(); [[ -n "$HA_ROLE" ]] && ha_pkgs=(keepalived)
    [[ "$LB_MODE" == "local" ]] && ha_pkgs+=(nginx openssl)
    apt-get update
    apt-get install -y --no-install-recommends \
        "postgresql-$PG_VERSION" "postgresql-client-$PG_VERSION" postgresql-contrib \
        ca-certificates curl python3 python3-venv rsync ${ha_pkgs[@]+"${ha_pkgs[@]}"}

    info "postgresql 기동"
    systemctl enable postgresql
    systemctl start postgresql || warn "postgresql 이 뜨지 않았습니다 — 대기 서버라면 db-standby 뒤에 뜹니다"

    ensure_dirs

    if pg_in_recovery; then
        info "이 서버의 DB 는 대기(복제본) — 역할 · 데이터베이스는 주에서 만든 것이 넘어온다. 건너뜁니다."
        ensure_apptainer
        cat <<MSG

[OK] 대기 서버 준비 완료.  ($APP_NAME · 주 $PEER_IP)
  다음: sudo ./deploy.sh install
MSG
        return 0
    fi

    info "DB 역할 확인: $DB_USER"
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
        # 비밀번호는 .env 를 만들 때 돌린다 — 여기 값은 어디에도 안 쓰인다.
        sudo -u postgres psql -c "CREATE USER \"$DB_USER\" WITH PASSWORD 'placeholder-rotated-on-env-creation';"
        info "DB 역할 생성: $DB_USER"
    else
        info "DB 역할이 이미 있습니다: $DB_USER"
    fi

    info "데이터베이스 확인: $DB_NAME"
    if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
        sudo -u postgres psql -c "CREATE DATABASE \"$DB_NAME\" OWNER \"$DB_USER\" ENCODING 'UTF8';"
        info "데이터베이스 생성: $DB_NAME"
    else
        info "데이터베이스가 이미 있습니다: $DB_NAME"
    fi
    grant_temp_limit

    # **맨 끝에 둔다.** PPA 에 닿지 않는 서버에서 여기서 멈춰도 DB·폴더는 이미 서 있고,
    # apptainer 만 따로 깐 뒤 prepare 를 다시 돌리면 된다(앞 단계는 멱등하다).
    ensure_apptainer

    cat <<MSG

[OK] 서버 준비 완료.  ($APP_NAME · DB $DB_NAME · 포트 $APP_PORT$( [[ -n "$HA_ROLE" ]] && echo " · 이중화 $HA_ROLE" ))
  다음: sudo ./deploy.sh install$( [[ "$HA_ROLE" == "master" ]] && printf '\n  이중화: sudo ./deploy.sh db-primary   (그 뒤 상대 서버에서 prepare → db-standby → install)' )
MSG
}

cmd_install() {
    [[ -f "$HERE/app.sif" ]] || err "app.sif 가 없습니다 — 릴리스 번들을 풀고 그 안에서 실행하세요"
    info "$APP_NAME ($APP_SLUG) $VERSION — 계정 $OPERATOR · 설치 경로 $INSTALL_DIR · 포트 $APP_PORT · 확장 기본값 ${EXTENSIONS:-없음}"

    ensure_dirs
    generate_env_if_missing
    sync_env_identity
    sync_env_portal
    place_sif
    run_migrations
    run_seed
    render_service_unit

    systemctl enable "$SERVICE_NAME"
    systemctl restart "$SERVICE_NAME"
    health_check || true

    setup_worker
    setup_mcp || warn "MCP 설정 건너뜀(비치명적)"
    setup_sync_timer || warn "동기화 타이머 건너뜀(비치명적)"
    setup_backup_timer || warn "백업 타이머 건너뜀(비치명적)"
    setup_lb

    cat <<MSG

[OK] 설치 완료.
  로그   : sudo journalctl -u $SERVICE_NAME -f
  워커   : sudo systemctl status $WORKER_SERVICE_NAME   (파이프라인 실행이 여기서 돈다)
  접속   : $( [[ -n "$HA_ROLE" ]] && echo "https://$PUBLIC_HOST/$APP_SLUG/  (직접: http://$SELF_IP:$APP_PORT/)" || echo "http://<서버주소>:$APP_PORT/" )
  자료   : 첨부 $FILESTORE_HOST_DIR · 중계 $RELAY_HOST_DIR(정의는 pipelines/) · 설정 $ENV_FILE · 로그 $LOG_HOST_DIR$( [[ -n "$BACKUP_HOST_DIR" ]] && echo " · 백업 $BACKUP_HOST_DIR" )
  MCP    : sudo systemctl status $MCP_SERVICE_NAME   (Claude 연동, 선택)

  위에 찍힌 관리자 임시 비밀번호는 **다시 표시되지 않습니다.**
  첫 로그인에서 변경이 강제됩니다.
MSG
}

cmd_update() {
    [[ -f "$HERE/app.sif" ]]     || err "app.sif 가 $HERE 에 없습니다"
    [[ -f "$ENV_FILE" ]] || err "$ENV_FILE 가 없습니다 — 먼저 install 하세요"
    info "$APP_NAME ($APP_SLUG) $VERSION — 포트 $APP_PORT · 확장 기본값 ${EXTENSIONS:-없음}"
    ensure_dirs
    sync_env_identity
    sync_env_portal

    info "$SERVICE_NAME 중지"
    systemctl stop "$SERVICE_NAME" || true
    # 워커도 함께 — 옛 코드가 새 마이그레이션 위에서 작업을 집으면 안 된다. 하던 작업은
    # 끝까지 기다린다(TimeoutStopSec). 못 끝낸 것은 다른 워커가 되살린다.
    systemctl stop "$WORKER_SERVICE_NAME" 2>/dev/null || true

    if [[ -f "$INSTALL_DIR/app.sif" ]]; then
        # **직전 이미지를 남긴다.** 롤백은 이 파일을 되돌리는 것뿐이다.
        info "직전 SIF 보관 → app.sif.prev"
        mv "$INSTALL_DIR/app.sif" "$INSTALL_DIR/app.sif.prev"
    fi
    place_sif
    run_migrations
    # 앱이 뜨기 전에 — 앱은 기동 뒤 처음 읽을 때 한 번 걸어 보고 그 답을 기억한다.
    grant_temp_limit
    # 템플릿(경로·하드닝)이 바뀌었을 수 있으므로 다시 렌더한다.
    render_service_unit

    systemctl start "$SERVICE_NAME"
    health_check || true

    setup_worker
    # MCP 도 함께 갱신(소스 교체 + 유닛 재렌더 + 재기동). 비치명적.
    setup_mcp || warn "MCP 설정 건너뜀(비치명적)"
    setup_sync_timer || warn "동기화 타이머 건너뜀(비치명적)"
    setup_backup_timer || warn "백업 타이머 건너뜀(비치명적)"
    setup_lb

    cat <<MSG

[OK] 갱신 완료. ($VERSION)

  **파일만 되돌아간다 — 마이그레이션은 취소되지 않는다.**
  롤백:
    sudo systemctl stop $SERVICE_NAME $WORKER_SERVICE_NAME
    sudo mv $INSTALL_DIR/app.sif.prev $INSTALL_DIR/app.sif
    sudo systemctl start $SERVICE_NAME $WORKER_SERVICE_NAME
MSG
}

cmd_reset() {
    cat <<MSG

  ⚠ 초기화 — 다음이 **전부 사라집니다**:
      DB       : $DB_NAME 을 지우고 빈 상태로 다시 만듭니다
      첨부     : $FILESTORE_HOST_DIR/* 를 지웁니다
  .env · DB 역할 · systemd 유닛은 그대로 둡니다.

MSG
    read -r -p "정말 진행하려면 '$DB_NAME' 을 그대로 입력하세요: " confirm
    [[ "$confirm" == "$DB_NAME" ]] || err "취소했습니다. 아무것도 바뀌지 않았습니다."

    pg_in_recovery && err "이 서버의 DB 는 대기입니다 — 초기화는 주 서버에서."
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    info "데이터베이스 재생성: $DB_NAME"
    sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DROP DATABASE IF EXISTS "$DB_NAME";
CREATE DATABASE "$DB_NAME" OWNER "$DB_USER" ENCODING 'UTF8';
SQL
    info "첨부 삭제"
    as_op find "$FILESTORE_HOST_DIR" -mindepth 1 -delete 2>/dev/null || true

    run_migrations
    run_seed
    systemctl start "$SERVICE_NAME"
    health_check || true
    echo
    echo "[OK] 초기화 완료. 위에 찍힌 관리자 임시 비밀번호로 로그인하세요."
}

# ── 껐다 켜기 — **`.env` 를 고친 뒤 쓰는 자리.** ─────────────────────────────────
#
# `.env` 는 프로세스가 **뜰 때 한 번만** 읽는다. 파일만 고치면 도는 것은 옛 값을 그대로
# 쓰고, 그 사실은 아무 데도 안 뜬다 — 사람은 고쳤는데 왜 그대로냐를 한참 찾는다.
#
# 유닛 이름(`<slug>` · `<slug>-worker` · `<slug>-mcp`)을 사람이 외울 이유가 없다. 스크립트는
# 이미 slug 를 안다.
units_of() {
    # `$ETC` 는 시험이 쓰는 접두어다(render 와 같은 방식) — 운영에서는 비어 있어 실제 경로다.
    local list=("$SERVICE_NAME")
    [[ -f "$ETC$WORKER_SERVICE_UNIT" ]] && list+=("$WORKER_SERVICE_NAME")
    [[ -f "$ETC$MCP_SERVICE_UNIT" ]] && list+=("$MCP_SERVICE_NAME")
    printf '%s\n' "${list[@]}"
}

cmd_units() {
    # **무엇을 껐다 켜는지 먼저 보여 준다.** root 없이도 볼 수 있어야 한다 — 「이 서버에 뭐가
    # 깔려 있지」 는 고치기 전에 묻는 물음이다.
    units_of
}

cmd_service() {  # $1 = start|stop|restart
    local action="$1" unit one
    mapfile -t unit < <(units_of)
    if [[ "$action" != "stop" ]] && [[ "$(systemctl is-enabled "$WORKER_SERVICE_NAME" 2>/dev/null || true)" == "disabled" ]]; then
        # 꺼 둔(disable) 워커는 띄우지 않는다(ADR 0049 — update 와 같은 규칙).
        local kept=()
        for one in "${unit[@]}"; do [[ "$one" == "$WORKER_SERVICE_NAME" ]] || kept+=("$one"); done
        unit=("${kept[@]}")
        warn "작업 워커($WORKER_SERVICE_NAME)는 꺼 둔 상태라 띄우지 않습니다 — 다시 켜기: sudo systemctl enable --now $WORKER_SERVICE_NAME"
    fi
    info "$action: ${unit[*]}"
    systemctl "$action" "${unit[@]}"
    [[ "$action" == "stop" ]] && return 0
    health_check || warn "앱이 아직 응답하지 않습니다 — journalctl -u $SERVICE_NAME -n 50"
    return 0
}

cmd_status() {
    echo "== $APP_NAME ($APP_SLUG) · 확장 기본값 ${EXTENSIONS:-없음} =="
    echo "  설치 경로 : $INSTALL_DIR$( [[ -n "$DATA_DIR" ]] && echo "  · 공용 $DATA_DIR" )"
    echo "  DB        : $DB_NAME @ $DB_HOST"
    echo "  포트      : $APP_PORT"
    [[ -f "$INSTALL_DIR/app.sif" ]] && echo "  SIF       : $(stat -c '%y' "$INSTALL_DIR/app.sif")"
    echo
    systemctl --no-pager --lines=10 status "$SERVICE_NAME" || true
    echo
    echo "== /api/health =="
    health_check || true
    echo
    echo "== 작업 워커 ($WORKER_SERVICE_NAME) =="
    if [[ -f "$WORKER_SERVICE_UNIT" ]]; then
        systemctl --no-pager --lines=3 status "$WORKER_SERVICE_NAME" || true
    else
        echo "  없음 — 파이프라인 실행이 「대기」 에 머뭅니다. sudo ./deploy.sh update"
    fi
    if [[ -f "$MCP_SERVICE_UNIT" ]]; then
        echo
        echo "== MCP ($MCP_SERVICE_NAME · http://$(mcp_shown_host):$MCP_PORT/mcp) =="
        systemctl --no-pager --lines=5 status "$MCP_SERVICE_NAME" || true
    fi
    if [[ -f "$SYNC_TIMER_UNIT" ]]; then
        echo
        echo "== 동기화 타이머 ($SYNC_SERVICE_NAME.timer) =="
        systemctl --no-pager list-timers "${SYNC_SERVICE_NAME}.timer" || true
    fi
    if [[ -f "$BACKUP_TIMER_UNIT" ]]; then
        echo
        echo "== 백업 타이머 ($BACKUP_SERVICE_NAME.timer → $BACKUP_HOST_DIR) =="
        systemctl --no-pager list-timers "${BACKUP_SERVICE_NAME}.timer" || true
        [[ -f "$BACKUP_HOST_DIR/LAST_BACKUP.txt" ]] && head -n1 "$BACKUP_HOST_DIR/LAST_BACKUP.txt"
    fi
    echo
    ha_status
}

# ───────────────────────── setup — 물어보고 알아서 ─────────────────────────
# 운영자가 env 이름과 순서를 외우지 않게. 답한 값은 instance · ha 설정 파일에 남아 다음 배포가
# 기억한다. 주(A)는 끝에 B 로 넘길 파일(.env · 복제 비밀번호)을 handoff/ 에 모아 두고, 대기(B)는
# 그것을 A 에서 scp 로 받아 제자리에 둔다 — 사람이 파일을 손으로 옮기지 않는다.
ask() {  # $1=변수 $2=질문 $3=기본값
    local answer
    if [[ -n "$3" ]]; then read -r -p "$2 [$3]: " answer; else read -r -p "$2: " answer; fi
    printf -v "$1" '%s' "${answer:-$3}"
}
cmd_setup() {
    local plan_only=0; [[ "${1:-}" == "--plan" ]] && plan_only=1
    [[ -t 0 || $plan_only -eq 1 ]] || err "setup 은 물어보며 진행합니다 — 터미널에서 직접(원격이면 ssh -t) 실행하세요."
    echo
    echo "== $APP_NAME_DEFAULT 설치 안내 ($VERSION) — 물음에 답하면 나머지는 이 스크립트가 합니다. 비우면 [ ] 안의 값 =="
    echo
    local role
    ask role "이 서버는 주(A) 입니까, 대기(B) 입니까? (A/B, 서버 한 대뿐이면 1)" "A"
    case "${role^^}" in A) HA_ROLE=master ;; B) HA_ROLE=backup ;; 1) HA_ROLE="" ;; *) err "A, B, 1 중 하나로 답하세요." ;; esac
    ask APP_SLUG "플랫폼 이름 — 기계용, 소문자·숫자만 (예: plmhub). 설치 뒤엔 못 바꿉니다" "${APP_SLUG:-}"
    [[ "$APP_SLUG" =~ ^[a-z][a-z0-9]{0,31}$ ]] || err "소문자·숫자 한 덩어리 32자 이내여야 합니다: $APP_SLUG"
    if [[ "$HA_ROLE" == backup ]]; then
        # 이름 · 포트 · 확장은 A 의 .env 에 이미 있다 — 다시 묻지 않고 받아온 것을 쓴다.
        APP_NAME="$(instance_conf_get "$APP_SLUG" APP_NAME)"; APP_NAME="${APP_NAME:-$APP_NAME_DEFAULT}"
        APP_PORT="$(instance_conf_get "$APP_SLUG" APP_PORT)"; APP_PORT="${APP_PORT:-$APP_PORT_DEFAULT}"
        EXTENSIONS="$(instance_conf_get "$APP_SLUG" EXTENSIONS)"
    else
        ask APP_NAME "화면에 보일 이름 (예: PLM 기준정보)" "$(instance_conf_get "$APP_SLUG" APP_NAME)"
        APP_NAME="${APP_NAME:-$APP_NAME_DEFAULT}"
        # **빈 자리를 미리 골라 준다** — MCP 가 앱 포트 +2 를 쓰므로 겹치면 뒤에서 멈춘다.
        local port_default; port_default="$(instance_conf_get "$APP_SLUG" APP_PORT)"
        [[ -n "$port_default" ]] || port_default="$(free_port)"
        ask APP_PORT "앱 포트 — 10 씩 벌립니다(MCP 가 +2 를 씁니다)" "$port_default"
        APP_PORT="${APP_PORT:-$APP_PORT_DEFAULT}"
        ask EXTENSIONS "확장 모듈 기본값, 쉼표로 (화면에서 켤 수 있으니 그냥 Enter)" "$(instance_conf_get "$APP_SLUG" EXTENSIONS)"
    fi
    local peer_account=""
    if [[ -n "$HA_ROLE" ]]; then
        ask PEER_IP "상대 서버의 IP ($( [[ "$HA_ROLE" == master ]] && echo 'B' || echo 'A' ))" "$PEER_IP"
        [[ -n "$PEER_IP" ]] || err "상대 서버 IP 가 필요합니다."
        ask PUBLIC_HOST "사용자가 브라우저에 치는 호스트명" "${PUBLIC_HOST:-hwax.sec.samsung.net}"
        ask DATA_DIR "공용 스토리지 폴더 (예: /data/$APP_SLUG — 아직 없으면 그냥 Enter)" "$DATA_DIR"
        if [[ -z "$DATA_DIR" && "$HA_ROLE" == backup ]]; then
            ask peer_account "A 서버의 계정 이름 — .env 와 복제 비밀번호를 거기서 받아옵니다" "$OPERATOR"
            ask SSH_PORT "A 서버의 ssh 포트" "${SSH_PORT:-22}"
        fi
    fi
    # 답한 값으로 파생값을 다시 계산한다. **유닛 이름은 그 함수가 만든다** — 여기 손으로
    # 적었더니 워커가 빠져 두 번째 플랫폼이 첫 플랫폼의 워커를 덮어썼다(실측).
    INSTALL_DIR="/home/$OPERATOR/apps/$APP_SLUG"; DB_NAME="$APP_SLUG"; DB_USER="$APP_SLUG"
    derive_unit_names
    MCP_PORT=$((APP_PORT + 2)); MCP_API_BASE="http://127.0.0.1:$APP_PORT"
    [[ -n "$HA_ROLE" ]] && MCP_HOST="0.0.0.0"
    SELF_IP="${SELF_IP_GIVEN:-}"   # 상대 IP 를 이제 아니, 그쪽으로 나가는 내 주소를 다시 잰다
    ha_load
    if [[ -n "$DATA_DIR" ]]; then ENV_FILE="$DATA_DIR/.env"; FILESTORE_HOST_DIR="$DATA_DIR/filestore"; BACKUP_HOST_DIR="$DATA_DIR/backup"; RELAY_HOST_DIR="$DATA_DIR/relay"
    else ENV_FILE="$INSTALL_DIR/.env"; FILESTORE_HOST_DIR="$INSTALL_DIR/filestore"; BACKUP_HOST_DIR=""; RELAY_HOST_DIR="$INSTALL_DIR/relay"; fi
    LOG_HOST_DIR="$INSTALL_DIR/logs"
    if [[ -n "$DB_VIP" ]]; then DB_HOST="$DB_VIP"; elif [[ "$HA_ROLE" == backup ]]; then DB_HOST="$PEER_IP"; elif [[ "$HA_ROLE" == master ]]; then DB_HOST="$SELF_IP"; else DB_HOST="localhost"; fi

    cat <<PLAN

── 할 일 ──────────────────────────────────────────────
  플랫폼   : $( [[ "$HA_ROLE" == backup ]] && echo "($APP_SLUG) — 이름 · 포트 · 확장은 A 의 설정을 그대로 받습니다" || echo "$APP_NAME ($APP_SLUG) · 포트 $APP_PORT · 확장 기본값 ${EXTENSIONS:-없음}" )
  이 서버  : $( case "$HA_ROLE" in master) echo "주(A) $SELF_IP — 상대 B $PEER_IP";; backup) echo "대기(B) $SELF_IP — 주 A $PEER_IP";; *) echo "단독";; esac )
  DB       : 이름 $DB_NAME · 앱이 붙는 곳 $DB_HOST
  파일     : 설치 $INSTALL_DIR$( [[ -n "$DATA_DIR" ]] && echo " · 공용 $DATA_DIR" )
  순서     : 1) 패키지 · DB 역할 (prepare)$( case "$HA_ROLE" in master) echo "  2) PostgreSQL 주 (db-primary)  3) 앱 (install)  4) B 에 넘길 파일 모으기";; backup) echo "  2) A 에서 .env · 복제 비밀번호 받기  3) PostgreSQL 대기 (db-standby)  4) 앱 (install)";; *) echo "  2) 앱 (install)";; esac )
  기록     : $INSTALL_DIR/setup.log (임시 관리자 비밀번호도 여기 남습니다)
  유닛     : $SERVICE_NAME · $WORKER_SERVICE_NAME · $MCP_SERVICE_NAME · $SYNC_SERVICE_NAME.timer · $BACKUP_SERVICE_NAME.timer
             (한 서버에 두 번째 플랫폼이면 **이 이름들이 앞 플랫폼과 달라야** 합니다 — slug 에서 나옵니다)
───────────────────────────────────────────────────────
PLAN
    [[ $plan_only -eq 1 ]] && return 0
    local go; read -r -p "진행할까요? (y/N): " go; [[ "${go,,}" == "y" ]] || err "취소했습니다. 아무것도 바뀌지 않았습니다."

    as_op mkdir -p "$INSTALL_DIR"
    # **A 에서 받아오는 것은 로그 기록(tee) 전에.** 비밀번호를 묻는 scp 가 tee 뒤에서 터미널을
    # 읽으면 셸이 작업을 정지시킨다(실측 — 「[2]+ Stopped」). 이미 제자리에 있으면 안 받는다.
    if [[ "$HA_ROLE" == backup && -z "$DATA_DIR" ]]; then
        if [[ -f "$ENV_FILE" && -f /etc/pg-ha.replpass ]]; then
            info ".env 와 복제 비밀번호가 이미 있습니다 — A 에서 다시 받지 않습니다"
        else
            local handoff="$INSTALL_DIR/handoff"
            info "A($PEER_IP) 에서 .env · 복제 비밀번호 받기 — $peer_account 계정의 비밀번호를 물으면 입력하세요"
            as_op mkdir -p "$handoff"
            as_op scp -q -P "${SSH_PORT:-22}" "$peer_account@$PEER_IP:apps/$APP_SLUG/handoff/pg-ha.replpass" \
                "$peer_account@$PEER_IP:apps/$APP_SLUG/handoff/.env" "$handoff/" \
                || err "A 에서 받지 못했습니다. A 에서 setup 이 끝났는지, 계정 · IP · ssh 포트가 맞는지 확인하세요."
            install -o root -g postgres -m 640 "$handoff/pg-ha.replpass" /etc/pg-ha.replpass
            install -o "$OPERATOR" -g "$OPERATOR" -m 600 "$handoff/.env" "$ENV_FILE"
            rm -rf "$handoff"
        fi
    fi
    local log="$INSTALL_DIR/setup.log"
    # 화면에 찍히는 것을 전부 파일에도 — 임시 비밀번호가 「한 번만」 찍히는 문제를 여기서 푼다.
    # **파일은 tee 보다 먼저 만든다.** tee 는 뒤늦게 뜨므로 그 뒤에 chown 하면 「없는 파일」 이다(실측).
    touch "$log"; chown "$OPERATOR:$OPERATOR" "$log"; chmod 600 "$log"
    exec > >(tee -a "$log") 2>&1
    echo "[$(date -Is)] setup 시작 — $APP_NAME ($APP_SLUG) $HA_ROLE"

    cmd_prepare
    case "$HA_ROLE" in
        master)
            cmd_db primary
            cmd_install
            # B 로 넘길 것 — 운영 계정이 읽을 수 있게 모아 둔다(B 의 setup 이 scp 로 집어 간다).
            local handoff="$INSTALL_DIR/handoff"
            mkdir -p "$handoff"
            install -o "$OPERATOR" -g "$OPERATOR" -m 600 /etc/pg-ha.replpass "$handoff/pg-ha.replpass"
            install -o "$OPERATOR" -g "$OPERATOR" -m 600 "$ENV_FILE" "$handoff/.env"
            chown "$OPERATOR:$OPERATOR" "$handoff"; chmod 700 "$handoff"
            cat <<MSG

[OK] 주(A) 설치 끝. 다음은 **B 서버에서**  sudo ./deploy.sh setup  — 물음에 B 라고 답하면
     A($SELF_IP) 의 $handoff/ 에서 .env 와 복제 비밀번호를 받아 갑니다(A 계정의 비밀번호를 한 번 묻습니다).
     임시 관리자 비밀번호는 위에 찍혔고 $log 에도 있습니다.
MSG
            ;;
        backup)
            [[ -f "$ENV_FILE" ]] || err "$ENV_FILE 가 없습니다 — A 에서 setup 이 끝났나요?"
            # A 의 .env 가 정한 이름 · 포트 · 확장을 그대로 — 두 서버는 같은 인스턴스다.
            local v
            v="$(sed -n 's|^APP_NAME=||p' "$ENV_FILE" | tail -n1)";    [[ -n "$v" ]] && APP_NAME="$v"
            v="$(sed -n 's|^APP_TAGLINE=||p' "$ENV_FILE" | tail -n1)"; APP_TAGLINE="$v"
            v="$(sed -n 's|^EXTENSIONS=||p' "$ENV_FILE" | tail -n1)";  EXTENSIONS="$v"
            v="$(sed -n 's|^PORT=||p' "$ENV_FILE" | tail -n1)";        [[ -n "$v" ]] && APP_PORT="$v"
            MCP_PORT=$((APP_PORT + 2)); MCP_API_BASE="http://127.0.0.1:$APP_PORT"
            info "A 의 설정을 받았습니다 — $APP_NAME · 포트 $APP_PORT · 확장 기본값 ${EXTENSIONS:-없음}"
            cmd_db standby
            cmd_install
            cat <<MSG

[OK] 대기(B) 설치 끝. 양쪽에서  sudo ./deploy.sh status  로 확인하세요 —
     이 서버는 「역할: 대기 · streaming ← $PEER_IP」 여야 합니다.
MSG
            ;;
        *)
            cmd_install
            ;;
    esac
    echo "[$(date -Is)] setup 끝"
}

# ───────────────────────── remove — 인스턴스 지우기 ─────────────────────────
# 리허설로 올린 것을 치우거나 플랫폼을 내릴 때. **공용 폴더(DATA_DIR)는 안 지운다** — 첨부와 백업은
# 사람이 보고 지우는 것이다. 이름을 그대로 쳐야 진행된다(reset 과 같은 무늬).
cmd_remove() {
    cat <<MSG

  ⚠ 인스턴스 삭제 — $APP_NAME ($APP_SLUG). 다음이 **전부 사라집니다**:
      유닛     : $SERVICE_NAME · $WORKER_SERVICE_NAME · $MCP_SERVICE_NAME · $SYNC_SERVICE_NAME.timer · $BACKUP_SERVICE_NAME.timer
      DB       : $DB_NAME 과 역할 $DB_USER (이 서버의 PostgreSQL 이 주일 때)
      설치 폴더: $INSTALL_DIR (SIF · .env · 로그$( [[ -z "$DATA_DIR" ]] && echo ' · 첨부' ))
      기록     : $INSTANCES_DIR/$APP_SLUG.conf
  남기는 것: $( [[ -n "$DATA_DIR" ]] && echo "공용 폴더 $DATA_DIR (첨부 · 백업 · .env)" || echo "없음" ). 호스트 설정(/etc/platform-ha.conf · pg-ha)도 그대로.

MSG
    read -r -p "정말 진행하려면 '$APP_SLUG' 을 그대로 입력하세요: " confirm
    [[ "$confirm" == "$APP_SLUG" ]] || err "취소했습니다. 아무것도 바뀌지 않았습니다."

    local unit
    for unit in "$SERVICE_NAME.service" "$WORKER_SERVICE_NAME.service" "$MCP_SERVICE_NAME.service" "$SYNC_SERVICE_NAME.timer" \
                "$SYNC_SERVICE_NAME.service" "$BACKUP_SERVICE_NAME.timer" "$BACKUP_SERVICE_NAME.service"; do
        systemctl disable --now "$unit" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/$unit"
    done
    systemctl daemon-reload
    info "유닛 삭제"
    if pg_in_recovery; then
        warn "이 서버의 DB 는 대기라 DB 는 여기서 못 지웁니다 — 주 서버에서 remove 하면 복제로 함께 사라집니다."
    else
        sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DROP DATABASE IF EXISTS "$DB_NAME";
-- 임시 파일 상한 권한(grant_temp_limit)이 남아 있으면 역할을 못 지운다(PostgreSQL 15+).
DO \$\$ BEGIN
    IF current_setting('server_version_num')::int >= 150000
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER') THEN
        EXECUTE 'REVOKE SET ON PARAMETER temp_file_limit FROM "$DB_USER"';
    END IF;
END \$\$;
DROP ROLE IF EXISTS "$DB_USER";
SQL
        info "DB · 역할 삭제: $DB_NAME"
    fi
    rm -rf "$INSTALL_DIR"
    rm -f "$INSTANCES_DIR/$APP_SLUG.conf"
    [[ -n "$LB_MODE" && "$LB_MODE" == "local" ]] && rm -f "/etc/nginx/platforms.d/$APP_SLUG.conf" "/etc/nginx/conf.d/${APP_SLUG}-upstream.conf" && { nginx -t >/dev/null 2>&1 && systemctl reload nginx || true; }
    echo
    echo "[OK] $APP_SLUG 를 지웠습니다.$( [[ -n "$DATA_DIR" ]] && echo " 공용 폴더 $DATA_DIR 는 남아 있습니다." )"
}

# 아무것도 바꾸지 않고 결과만 — ETC=<폴더> 를 주면 거기 쓰고, 없으면 임시 폴더에 쓴 뒤 화면에 보여 준다.
cmd_render() {
    local show=0
    if [[ -z "${ETC:-}" ]]; then ETC="$(mktemp -d)"; show=1; fi
    local tpl unit
    mkdir -p "$ETC/etc/systemd/system"
    # 설정 파일도 그 아래에 — 실제 배포가 남길 것과 같은 모양을 본다.
    instance_save; ha_save
    for tpl in app.service worker.service sync.service backup.service; do
        [[ -f "$HERE/$tpl.template" ]] || continue
        case "$tpl" in
            app.service)     unit="$SERVICE_NAME.service" ;;
            worker.service)  unit="$WORKER_SERVICE_NAME.service" ;;
            sync.service)    unit="$SYNC_SERVICE_NAME.service" ;;
            *)               unit="$BACKUP_SERVICE_NAME.service" ;;
        esac
        render_unit_paths "$HERE/$tpl.template" > "$ETC/etc/systemd/system/$unit"
    done
    render_lb
    if [[ $show -eq 1 ]]; then
        find "$ETC" -type f | sort | while read -r f; do echo "### ${f#"$ETC"}"; cat "$f"; echo; done
    else
        echo "렌더 결과: $ETC"
    fi
}

cmd_auto() {
    if [[ -f "$INSTALL_DIR/app.sif" && -f "$ENV_FILE" && -f "$SERVICE_UNIT" ]]; then
        info "기존 설치를 찾았습니다 → update"
        cmd_update
    else
        info "새 설치 → install"
        cmd_install
    fi
}

usage() {
    cat <<MSG
$APP_NAME 배포 스크립트 ($VERSION)

  sudo ./deploy.sh [setup|prepare|install|update|restart|reset|status]

  setup     **처음이면 이것.** 물음에 답하면 prepare → (DB 주/대기) → install 을 알아서
  prepare   최초 1회: apt 패키지, apptainer(공식 PPA), postgres, DB 역할·DB
  install   SIF + .env + systemd, 마이그레이션, 시드, 기동
  update    SIF 교체 + 마이그레이션 + 재시작 (자료 그대로)
  reset     DB·첨부 초기화 (파괴적)
  remove    이 인스턴스를 지운다 — 유닛 · DB · 설치 폴더 (공용 폴더는 남김, 파괴적)
  status    서비스 상태 + health (+ 이중화 · DB 주/대기)
  restart   앱 · 작업 워커 · MCP 를 함께 재시작 — **.env 를 고쳤으면 이것**
  start|stop  같은 묶음을 켜고 끈다
  units     그 묶음에 무엇이 들어 있는지만 본다 (root 없이)
  (없으면)  자동: 처음이면 install, 아니면 update

  이중화(서버 두 대) — README 「이중화」:
  db-primary              이 서버의 PostgreSQL 을 주로 (복제 계정 · 감시 · 원복 잠금)
  db-standby [--from IP]  이 서버의 PostgreSQL 을 대기로 (데이터는 주에서 새로 받는다)
  db-promote              대기를 주로 (장애)      db-demote  주를 곱게 내림 (계획 전환)
  db-status               역할 · 복제 지연 · VIP
  lb                      메인 서버용 nginx 조각 · keepalived(DB VIP)를 지금 설정으로 다시 만들고 반영

  pg-tune [--apply]       PostgreSQL 서버 설정을 이 서버의 메모리 · CPU 로 맞춘다(기본은 보여 주기만 —
                          ADR 0031). 공유 버퍼 등은 재시작해야 바뀐다. 이중화면 두 서버 다. --help 로 옵션

지금 설정 (env 로 덮을 수 있음):
  APP_SLUG    = $APP_SLUG   (APP_NAME="$APP_NAME" EXTENSIONS=${EXTENSIONS:-없음}) — 다른 인스턴스는 APP_SLUG=<slug>
  OPERATOR    = $OPERATOR
  INSTALL_DIR = $INSTALL_DIR
  DATA_DIR    = ${DATA_DIR:-(없음 — 단독 서버, 전부 INSTALL_DIR)}
  DB_NAME     = $DB_NAME @ $DB_HOST
  DB_USER     = $DB_USER
  APP_PORT    = $APP_PORT
  MCP_ENABLED = $MCP_ENABLED   (MCP_HOST=$MCP_HOST MCP_PORT=$MCP_PORT MCP_API_BASE=$MCP_API_BASE)
  HA_ROLE     = ${HA_ROLE:-(없음)}   PEER_IP=$PEER_IP DB_VIP=${DB_VIP:-(없음)} PUBLIC_HOST=$PUBLIC_HOST LB_MODE=$LB_MODE${WEB_VIP:+ WEB_VIP=$WEB_VIP}

  HWAX 포털에 붙이기 — sudo PORTAL_JWKS_URL=<포털 JWKS> PORTAL_LAUNCH_URL=<포털>/launch/<id> ./deploy.sh update
    (준 것만 .env 에 적는다 · 빈 값이면 비운다 · PUBLIC_PATH/TRUST_PROXY 가 없으면 /<slug> · true 를 더한다)
  hwax-request  포털 담당에게 보낼 연동 요청서를 이 설치의 값으로 채워 $INSTALL_DIR/HWAX-연동요청서.md 에
                (게이트웨이 위임 비밀이 .env 에 없으면 만든다 — 비밀 · 내부 주소는 파일에 안 쓴다)
MSG
}

case "${1:-}" in
    setup)          shift; cmd_setup "$@" ;;
    remove)         cmd_remove ;;
    prepare)        cmd_prepare ;;
    install)        cmd_install ;;
    update)         cmd_update  ;;
    reset)          cmd_reset   ;;
    status)         cmd_status  ;;
    units)          cmd_units ;;
    restart)        cmd_service restart ;;
    start)          cmd_service start   ;;
    stop)           cmd_service stop    ;;
    db-primary)     shift; ensure_dirs; cmd_db primary "$@" ;;
    db-standby)     shift; [[ "${1:-}" == "--from" ]] && shift; ensure_dirs; cmd_db standby "${1:-}" ;;
    db-promote)     cmd_db promote ;;
    db-demote)      cmd_db demote ;;
    db-status)      cmd_db status ;;
    lb)             ensure_dirs; setup_lb ;;
    pg-tune)        shift; exec "$HERE/pg-tune.sh" "$@" ;;
    render)         cmd_render ;;
    hwax-request)   cmd_hwax_request ;;
    ""|auto)        cmd_auto    ;;
    -h|--help|help) usage       ;;
    *)              usage; exit 1 ;;
esac
