#!/usr/bin/env bash
# pg-ha — 서버 두 대의 PostgreSQL 주/대기. **호스트 하나에 하나**, 플랫폼마다가 아니다.
#
# 한 서버의 PostgreSQL 클러스터(16/main)는 그 서버의 모든 플랫폼이 함께 쓴다. 그래서 주/대기 ·
# 승격 · 감시는 플랫폼(slug)이 아니라 호스트의 일이고, deploy.sh 는 이 도구를 /usr/local/sbin/pg-ha
# 로 깔아 두고 부른다.
#
#   pg-ha primary --peer <상대 IP> [--vip <DB VIP>]    이 서버를 주로 (복제 계정 · pg_hba · conf.d)
#   pg-ha standby --from <주 IP>                       이 서버를 대기로 (기존 데이터는 옆으로, pg_basebackup)
#   pg-ha promote                                      대기를 주로 (장애 때 — 옛 주는 나중에 standby 로 재구성)
#   pg-ha demote                                       주를 곱게 멈추고 「주 아님」 표시 (계획 전환의 첫 단계)
#   pg-ha status                                       역할 · 복제 상태 · VIP
#   pg-ha check | check-primary | on-master | guard    keepalived · systemd 가 부르는 훅
#
# 설정: /etc/pg-ha.conf (PEER_IP · DB_VIP · PG_VERSION · PG_CLUSTER · REPL_USER)
# 복제 비밀번호: /etc/pg-ha.replpass (root:postgres 640) — **두 서버가 같은 값**이어야 한다.
#   primary 가 만들고, standby 는 --replpass-from <파일> 로 받거나 같은 자리에 미리 둔다.
#
# **역할은 데이터 폴더가 말한다** — standby.signal 이 있으면 대기, 없으면 주. 표시 파일
# (/var/lib/pg-ha/demoted)은 「옛 주가 다시 주로 뜨는 것」 을 막는 잠금일 뿐이다.

set -euo pipefail

err()  { echo "오류: $*" >&2; exit 1; }
info() { echo "==> $*"; }
warn() { echo "경고: $*" >&2; }

CONF=/etc/pg-ha.conf
REPLPASS_FILE=/etc/pg-ha.replpass
STATE_DIR=/var/lib/pg-ha
DEMOTED="$STATE_DIR/demoted"

PG_VERSION="${PG_VERSION:-16}"
PG_CLUSTER="${PG_CLUSTER:-main}"
PEER_IP="${PEER_IP:-}"
DB_VIP="${DB_VIP:-}"
REPL_USER="${REPL_USER:-pgha_repl}"
# shellcheck disable=SC1090
[[ -f "$CONF" ]] && source "$CONF"

DATA_DIR="/var/lib/postgresql/$PG_VERSION/$PG_CLUSTER"
CONF_DIR="/etc/postgresql/$PG_VERSION/$PG_CLUSTER"
UNIT="postgresql@${PG_VERSION}-${PG_CLUSTER}.service"
SLOT="pgha_$(hostname -s | tr -c 'a-zA-Z0-9\n' '_')"

need_root() { [[ $EUID -eq 0 ]] || err "root 로 실행하세요 (sudo)"; }
as_pg() { sudo -u postgres "$@"; }
psql_local() { as_pg psql -X -v ON_ERROR_STOP=1 -tAq "$@"; }

save_conf() {
    cat > "$CONF" <<EOF
# pg-ha 가 쓴다 — 손으로 고쳐도 되지만 primary/standby 를 다시 돌리면 덮인다.
PEER_IP=$PEER_IP
DB_VIP=$DB_VIP
PG_VERSION=$PG_VERSION
PG_CLUSTER=$PG_CLUSTER
REPL_USER=$REPL_USER
EOF
    chmod 644 "$CONF"
}

cluster_running() { pg_lsclusters -h 2>/dev/null | awk -v v="$PG_VERSION" -v c="$PG_CLUSTER" '$1==v && $2==c {print $4}' | grep -q '^online'; }
in_recovery() { [[ "$(psql_local -c 'SELECT pg_is_in_recovery()' 2>/dev/null || echo x)" == "t" ]]; }
is_primary()  { [[ "$(psql_local -c 'SELECT pg_is_in_recovery()' 2>/dev/null || echo x)" == "f" ]]; }
holds_vip()   { [[ -n "$DB_VIP" ]] && ip -o addr show 2>/dev/null | grep -q " inet $DB_VIP/"; }
self_ip() {
    # 상대에게 갈 때 쓰는 내 주소 — 그것이 상대의 pg_hba 에 적힐 주소다.
    [[ -n "$PEER_IP" ]] || { hostname -I | awk '{print $1}'; return; }
    ip -o route get "$PEER_IP" 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n1
}

# 상대의 상태 — 복제 계정으로 postgres DB 에 붙어 묻는다. 못 붙으면 빈 값.
peer_in_recovery() {
    [[ -n "$PEER_IP" && -f "$REPLPASS_FILE" ]] || return 0
    PGPASSWORD="$(cat "$REPLPASS_FILE")" PGCONNECT_TIMEOUT=3 \
        psql -X -tAq -h "$PEER_IP" -U "$REPL_USER" -d postgres -c 'SELECT pg_is_in_recovery()' 2>/dev/null || true
}

# ── 클러스터 설정 — 주와 대기가 **같은 설정**을 갖는다(대기가 승격되면 그대로 주다) ──
configure_cluster() {
    local self; self="$(self_ip)"
    mkdir -p "$CONF_DIR/conf.d"
    cat > "$CONF_DIR/conf.d/pg-ha.conf" <<EOF
# pg-ha 가 쓴다. 주와 대기가 같다.
listen_addresses = '*'
wal_level = replica
max_wal_senders = 10
max_replication_slots = 10
hot_standby = on
# 대기가 오래 죽어 있어도 주의 디스크가 WAL 로 차지 않게 — 이 이상 밀리면 슬롯을 버리고
# 대기는 basebackup 부터 다시 한다(pg-ha standby --from).
max_slot_wal_keep_size = 2GB
wal_keep_size = 512MB
EOF
    chown postgres:postgres "$CONF_DIR/conf.d/pg-ha.conf"

    local hba="$CONF_DIR/pg_hba.conf"
    sed -i '/^# pg-ha begin$/,/^# pg-ha end$/d' "$hba"
    {
        echo "# pg-ha begin"
        echo "# 두 서버 사이 — 복제와 상태 확인(복제 계정), 앱(각 플랫폼 계정)."
        [[ -n "$PEER_IP" ]] && {
            echo "host    replication     $REPL_USER      $PEER_IP/32     scram-sha-256"
            echo "host    postgres        $REPL_USER      $PEER_IP/32     scram-sha-256"
            echo "host    all             all             $PEER_IP/32     scram-sha-256"
        }
        [[ -n "$self" ]] && echo "host    all             all             $self/32     scram-sha-256"
        echo "# pg-ha end"
    } >> "$hba"
}

ensure_replpass() {
    if [[ ! -f "$REPLPASS_FILE" ]]; then
        python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$REPLPASS_FILE"
        chown root:postgres "$REPLPASS_FILE"; chmod 640 "$REPLPASS_FILE"
        info "복제 비밀번호 생성: $REPLPASS_FILE — **대기 서버에도 같은 파일**이 있어야 한다"
    fi
    # **pg_basebackup -R 은 비밀번호를 적지 않는다**(passfile 만 가리킨다). 대기의 walreceiver 가
    # 주에 붙으려면 postgres 계정의 .pgpass 에 있어야 한다 — 없으면 대기가 「인증 실패」 로
    # 조용히 못 따라가고, 그 사실은 승격하는 날에야 드러난다.
    local pgpass=/var/lib/postgresql/.pgpass
    printf '*:5432:*:%s:%s\n' "$REPL_USER" "$(cat "$REPLPASS_FILE")" > "$pgpass"
    chown postgres:postgres "$pgpass"; chmod 600 "$pgpass"
}

# ── 훅 — keepalived 와 systemd 가 부른다. 출력은 journal 로 ──────────────────────────
hook_check() { pg_isready -q -h 127.0.0.1 -p 5432 -t 2; }

hook_check_primary() {
    # 주이고 **가짜 주가 아닐 때만** 0. 가짜 주 = 나는 주라고 알지만 DB VIP 는 남이 쥐고
    # 있고 거기서 DB 가 응답한다(= 상대가 승격됐다). 그때 VIP 를 되찾으면 갈라진 두 주가 된다.
    is_primary || return 1
    if [[ -n "$DB_VIP" ]] && ! holds_vip && pg_isready -q -h "$DB_VIP" -p 5432 -t 2; then
        return 1
    fi
    return 0
}

hook_on_master() {
    # DB VIP 가 이 서버로 왔다. 대기였다면 그것은 주가 죽었다는 뜻 — 승격한다.
    if in_recovery; then
        logger -t pg-ha "DB VIP 를 받았고 대기 상태 — 승격합니다"
        cmd_promote
        logger -t pg-ha "승격 완료. 옛 주는 'pg-ha standby --from $(self_ip)' 로 재구성해야 합니다"
    fi
}

hook_guard() {
    # PostgreSQL 이 뜨기 직전(systemd ExecStartPre). **옛 주가 다시 주로 뜨는 것**을 막는다.
    [[ -f "$DATA_DIR/standby.signal" ]] && exit 0
    [[ -d "$DATA_DIR" ]] || exit 0
    if [[ -f "$DEMOTED" ]]; then
        echo "pg-ha: 이 서버는 주에서 내려왔습니다($DEMOTED). 'pg-ha standby --from <주 IP>' 로 대기로 만드세요." >&2
        exit 1
    fi
    if [[ -n "$DB_VIP" ]] && ! holds_vip && pg_isready -q -h "$DB_VIP" -p 5432 -t 3; then
        echo "pg-ha: DB VIP $DB_VIP 에서 다른 서버의 DB 가 서비스 중입니다 — 이 서버를 주로 띄우면 두 주가 됩니다. 'pg-ha standby --from <주 IP>'." >&2
        exit 1
    fi
    if [[ "$(peer_in_recovery)" == "f" ]]; then
        echo "pg-ha: 상대($PEER_IP)가 주입니다 — 이 서버를 주로 띄우면 두 주가 됩니다. 'pg-ha standby --from $PEER_IP'." >&2
        exit 1
    fi
    exit 0
}

install_guard() {
    mkdir -p "/etc/systemd/system/$UNIT.d" "$STATE_DIR"
    cat > "/etc/systemd/system/$UNIT.d/pg-ha.conf" <<EOF
# pg-ha — 옛 주가 부팅해도 주로 뜨지 못하게. '+' 는 root 로 돈다(유닛은 postgres 로 돈다).
[Service]
ExecStartPre=+/usr/local/sbin/pg-ha guard
EOF
    systemctl daemon-reload
}

# ── 명령 ────────────────────────────────────────────────────────────────────────────
cmd_primary() {
    need_root
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --peer) PEER_IP="$2"; shift 2 ;;
            --vip)  DB_VIP="$2";  shift 2 ;;
            *) err "모르는 인자: $1" ;;
        esac
    done
    [[ -n "$PEER_IP" ]] || err "--peer <상대 서버 IP> 가 필요합니다"
    [[ -d "$DATA_DIR" ]] || err "클러스터가 없습니다: $DATA_DIR (postgresql-$PG_VERSION 이 깔렸나요?)"
    [[ -f "$DATA_DIR/standby.signal" ]] && err "이 서버는 대기입니다. 주로 만들려면 'pg-ha promote'."
    save_conf
    ensure_replpass
    configure_cluster
    install_guard
    rm -f "$DEMOTED"
    if cluster_running; then
        info "설정 다시 읽기 (listen_addresses 는 재시작이 필요하면 재시작)"
        local listen; listen="$(psql_local -c "SHOW listen_addresses")"
        if [[ "$listen" != "*" ]]; then systemctl restart "$UNIT"; else psql_local -c "SELECT pg_reload_conf()" >/dev/null; fi
    else
        systemctl start "$UNIT"
    fi
    local pw; pw="$(cat "$REPLPASS_FILE")"
    if [[ "$(psql_local -c "SELECT 1 FROM pg_roles WHERE rolname='$REPL_USER'")" == "1" ]]; then
        psql_local -c "ALTER ROLE \"$REPL_USER\" WITH REPLICATION LOGIN PASSWORD '$pw'" >/dev/null
    else
        psql_local -c "CREATE ROLE \"$REPL_USER\" WITH REPLICATION LOGIN PASSWORD '$pw'" >/dev/null
    fi
    info "주 준비 완료 — 상대 $PEER_IP · DB VIP ${DB_VIP:-(없음)} · 복제 계정 $REPL_USER"
    echo "  다음: 대기 서버에 $REPLPASS_FILE 를 같은 내용으로 두고  sudo pg-ha standby --from $(self_ip)"
}

cmd_standby() {
    need_root
    local from="" passfrom=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --from) from="$2"; shift 2 ;;
            --vip)  DB_VIP="$2"; shift 2 ;;
            --replpass-from) passfrom="$2"; shift 2 ;;
            *) err "모르는 인자: $1" ;;
        esac
    done
    [[ -n "$from" ]] || err "--from <주 서버 IP> 가 필요합니다"
    PEER_IP="$from"
    if [[ -n "$passfrom" ]]; then
        install -o root -g postgres -m 640 "$passfrom" "$REPLPASS_FILE"
    fi
    [[ -f "$REPLPASS_FILE" ]] || err "복제 비밀번호가 없습니다: $REPLPASS_FILE — 주 서버의 같은 파일을 가져오세요(--replpass-from <파일>)"
    ensure_replpass
    local pw; pw="$(cat "$REPLPASS_FILE")"

    info "주 $from 확인"
    [[ "$(PGPASSWORD="$pw" PGCONNECT_TIMEOUT=5 psql -X -tAq -h "$from" -U "$REPL_USER" -d postgres -c 'SELECT pg_is_in_recovery()' 2>/dev/null)" == "f" ]] \
        || err "$from 에 복제 계정으로 붙지 못했거나 그쪽이 주가 아닙니다. 주에서 'pg-ha primary --peer $(self_ip)' 를 먼저."

    save_conf
    configure_cluster
    install_guard

    if cluster_running; then info "로컬 클러스터 중지"; systemctl stop "$UNIT"; fi
    if [[ -d "$DATA_DIR" ]]; then
        # **옛 데이터는 지우지 않고 옆으로.** 승격 뒤의 옛 주에는 상대에 못 간 마지막 몇 초가
        # 있을 수 있다 — 사람이 볼 수 있게 한 벌은 남긴다(그 전 것은 지운다).
        local aside="$DATA_DIR.old-$(date +%Y%m%d-%H%M%S)"
        info "기존 데이터 옆으로: $aside"
        find "$(dirname "$DATA_DIR")" -maxdepth 1 -name "$(basename "$DATA_DIR").old-*" -exec rm -rf {} +
        mv "$DATA_DIR" "$aside"
    fi
    # 슬롯이 남아 있으면 -C 가 실패한다 — 지우고 다시 만든다.
    PGPASSWORD="$pw" psql -X -tAq -h "$from" -U "$REPL_USER" -d postgres \
        -c "SELECT pg_drop_replication_slot('$SLOT') FROM pg_replication_slots WHERE slot_name='$SLOT'" >/dev/null
    info "pg_basebackup ← $from (슬롯 $SLOT)"
    as_pg env PGPASSWORD="$pw" pg_basebackup -h "$from" -p 5432 -U "$REPL_USER" -D "$DATA_DIR" \
        -R -X stream -C -S "$SLOT" --checkpoint=fast --progress
    # 대기가 주에게 자기를 알린다 — pg_stat_replication 의 application_name.
    as_pg sed -i "s/^primary_conninfo = '\(.*\)'$/primary_conninfo = '\1 application_name=$SLOT'/" "$DATA_DIR/postgresql.auto.conf"
    rm -f "$DEMOTED"
    systemctl start "$UNIT"
    sleep 2
    in_recovery || err "기동했지만 대기 상태가 아닙니다 — journalctl -u $UNIT"
    info "대기 준비 완료 — $from 을 따라갑니다"
    cmd_status
}

cmd_promote() {
    need_root
    cluster_running || err "클러스터가 떠 있지 않습니다"
    in_recovery || { info "이미 주입니다"; return 0; }
    info "승격"
    as_pg pg_ctlcluster "$PG_VERSION" "$PG_CLUSTER" promote
    for _ in $(seq 1 30); do is_primary && break; sleep 1; done
    is_primary || err "승격이 끝나지 않았습니다 — journalctl -u $UNIT"
    rm -f "$DEMOTED"
    info "이제 이 서버가 주입니다. 옛 주(${PEER_IP:-?})는 다시 켜져도 주로 못 뜹니다(guard)."
    echo "  옛 주를 대기로: 그 서버에서  sudo pg-ha standby --from $(self_ip)"
    echo "  DB VIP 가 없다면: 각 플랫폼 .env 의 DATABASE_URL 을 이 서버로 바꾸고 앱 재시작"
}

cmd_demote() {
    need_root
    is_primary || err "주가 아닙니다"
    [[ "$(peer_in_recovery)" == "t" ]] || err "상대($PEER_IP)가 대기로 붙어 있지 않습니다 — 지금 내려오면 주가 없어집니다"
    local lag; lag="$(psql_local -c "SELECT COALESCE(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn),-1) FROM pg_stat_replication LIMIT 1")"
    info "복제 지연 ${lag:-?} 바이트 — 곱게 멈추면 남은 WAL 을 대기가 다 받는다"
    mkdir -p "$STATE_DIR"; touch "$DEMOTED"
    systemctl stop "$UNIT"
    info "주에서 내려왔습니다. 이 서버의 DB 는 다시 켜도 주로 뜨지 않습니다."
    echo "  다음: 상대에서  sudo pg-ha promote   → 그 뒤 여기서  sudo pg-ha standby --from $PEER_IP"
}

cmd_status() {
    echo "== pg-ha ($(hostname -s) · $(self_ip)) =="
    echo "  클러스터  : $PG_VERSION/$PG_CLUSTER  $(pg_lsclusters -h 2>/dev/null | awk -v v="$PG_VERSION" -v c="$PG_CLUSTER" '$1==v && $2==c {print $4}')"
    if cluster_running; then
        if in_recovery; then
            echo "  역할      : 대기 (standby)"
            echo "  복제      : $(psql_local -c "SELECT COALESCE((SELECT status||' ← '||sender_host FROM pg_stat_wal_receiver), '끊김')")"
            echo "  재생 지연 : $(psql_local -c "SELECT COALESCE(EXTRACT(EPOCH FROM now()-pg_last_xact_replay_timestamp())::int::text||'초', '-')")"
        else
            echo "  역할      : 주 (primary)$( [[ -f "$DEMOTED" ]] && echo '  ※ demoted 표시가 남아 있음' )"
            echo "  대기      : $(psql_local -c "SELECT COALESCE(string_agg(application_name||' '||state||' '||COALESCE(pg_wal_lsn_diff(pg_current_wal_lsn(),replay_lsn)::text,'?')||'B 뒤', ', '), '없음') FROM pg_stat_replication")"
        fi
    else
        echo "  역할      : (멈춤)$( [[ -f "$DATA_DIR/standby.signal" ]] && echo ' 대기 데이터' || echo ' 주 데이터' )$( [[ -f "$DEMOTED" ]] && echo ' · demoted' )"
    fi
    echo "  상대      : ${PEER_IP:-(없음)}$( [[ -n "$PEER_IP" ]] && case "$(peer_in_recovery)" in t) echo ' — 대기';; f) echo ' — 주';; *) echo ' — 응답 없음';; esac )"
    echo "  DB VIP    : ${DB_VIP:-(없음)}$( [[ -n "$DB_VIP" ]] && { holds_vip && echo ' — 이 서버가 쥠' || echo ' — 다른 곳'; } )"
}

case "${1:-}" in
    primary)        shift; cmd_primary "$@" ;;
    standby)        shift; cmd_standby "$@" ;;
    promote)        cmd_promote ;;
    demote)         cmd_demote ;;
    status)         cmd_status ;;
    check)          hook_check ;;
    check-primary)  hook_check_primary ;;
    on-master)      hook_on_master ;;
    guard)          hook_guard ;;
    *) sed -n '2,20p' "$0"; exit 1 ;;
esac
