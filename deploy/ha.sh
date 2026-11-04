#!/usr/bin/env bash
# 이중화 — deploy.sh 가 source 한다. 단독으로 실행하지 않는다(shebang · set 은 검사 규칙 때문에,
# 그리고 source 하는 쪽과 같은 엄격 모드라는 뜻으로 둔다).
#
#   사용자 · 외부 AI ──HTTPS──▶ 메인 서버(포탈 + nginx — 메인 서버 쪽이 관리)
#                                  /<slug>/… → 접두어를 벗겨 A · B 로 분배
#                             ┌──────────┴──────────┐
#                        서버 A                  서버 B          둘 다 활성. 앱 :8040 · MCP :8042
#                        PostgreSQL 주 ── 복제 ──▶ 대기          DB VIP(있으면) 가 주를 따라간다
#                             └──── /data/<slug>/ 공용 ────┘      첨부 · 백업 · .env
#
# 서버 두 대(A · B)에 같은 플랫폼을 올린다. **로드밸런서는 메인 서버의 것**이다(LB_MODE=external,
# 기본) — A · B 에는 nginx 도 웹 VIP 도 인증서도 없고, 메인 서버에 넘길 nginx 조각만 만들어 준다.
# DB 는 한쪽이 주(쓰기), 한쪽이 대기(복제)이며 그 일은 /usr/local/sbin/pg-ha 가 한다. DB VIP 가
# 있으면 keepalived 가 그것을 주에 붙이고, 주가 죽으면 대기를 승격한다.
#
# 메인 서버가 없을 때(LB_MODE=local)는 A · B 자체에 nginx + keepalived 웹 VIP 를 세운다.
#
# **호스트 수준과 플랫폼 수준을 가른다.**
#   호스트 수준(서버 두 대에 하나): keepalived · PostgreSQL 주/대기 (local 이면 nginx server 블록 · 인증서도).
#     설정은 /etc/platform-ha.conf — 같은 서버의 모든 플랫폼이 공유한다.
#   플랫폼 수준(slug 마다): 메인 서버용 nginx 조각(또는 local 의 location · upstream), 앱 · MCP 유닛,
#     /data 의 자기 폴더. 설정은 /etc/platform-instances/<slug>.conf (deploy.sh 가 쓴다).
#
# 처음 한 번 env 로 주면 파일에 남아 다음 배포가 기억한다(MCP 설정과 같은 방식):
#   sudo HA_ROLE=master PEER_IP=<B> PUBLIC_HOST=hwax.sec.samsung.net DATA_DIR=/data/<slug> ./deploy.sh prepare

# ETC 를 주면 /etc 대신 그 아래에 쓴다 — 'deploy.sh render' 가 root 없이 결과를 보여 주는 길.
ETC="${ETC:-}"
set -euo pipefail

HA_CONF="$ETC/etc/platform-ha.conf"

# ───────────────────────── 설정 읽기 · 쓰기 ─────────────────────────
ha_conf_get() { [[ -f "$HA_CONF" ]] && sed -n "s|^$1=||p" "$HA_CONF" | tail -n1 || true; }

ha_load() {
    HA_ROLE="${HA_ROLE:-$(ha_conf_get HA_ROLE)}"
    LB_MODE="${LB_MODE:-$(ha_conf_get LB_MODE)}"; LB_MODE="${LB_MODE:-external}"
    PEER_IP="${PEER_IP:-$(ha_conf_get PEER_IP)}"
    WEB_VIP="${WEB_VIP:-$(ha_conf_get WEB_VIP)}"
    DB_VIP="${DB_VIP:-$(ha_conf_get DB_VIP)}"
    PUBLIC_HOST="${PUBLIC_HOST:-$(ha_conf_get PUBLIC_HOST)}"
    VRRP_IFACE="${VRRP_IFACE:-$(ha_conf_get VRRP_IFACE)}"
    TLS_CERT="${TLS_CERT:-$(ha_conf_get TLS_CERT)}"; TLS_CERT="${TLS_CERT:-/etc/nginx/tls/server.crt}"
    TLS_KEY="${TLS_KEY:-$(ha_conf_get TLS_KEY)}";    TLS_KEY="${TLS_KEY:-/etc/nginx/tls/server.key}"
    DATA_DIR="${DATA_DIR:-$(instance_conf_get "$APP_SLUG" DATA_DIR)}"
    BACKUP_HOST_DIR="${BACKUP_HOST_DIR:-$(instance_conf_get "$APP_SLUG" BACKUP_HOST_DIR)}"
    if [[ -n "$HA_ROLE" ]]; then
        [[ "$HA_ROLE" == "master" || "$HA_ROLE" == "backup" ]] || err "HA_ROLE 은 master 또는 backup 입니다: $HA_ROLE"
        [[ "$LB_MODE" == "external" || "$LB_MODE" == "local" ]] || err "LB_MODE 는 external(메인 서버의 nginx) 또는 local 입니다: $LB_MODE"
        [[ -n "$PEER_IP" && -n "$PUBLIC_HOST" ]] \
            || err "이중화에는 PEER_IP · PUBLIC_HOST 가 필요합니다 (한 번 주면 $HA_CONF 에 남습니다)"
        [[ "$LB_MODE" == "external" || -n "$WEB_VIP" ]] || err "LB_MODE=local 에는 WEB_VIP 가 필요합니다"
        [[ -n "$VRRP_IFACE" ]] || VRRP_IFACE="$(ip -o route get "$PEER_IP" 2>/dev/null | sed -n 's/.* dev \([^ ]*\).*/\1/p' | head -n1)"
        if [[ -z "$VRRP_IFACE" && ( -n "$DB_VIP" || "$LB_MODE" == "local" ) ]]; then
            err "VRRP 인터페이스를 알 수 없습니다 — VRRP_IFACE=<eth> 로 주세요"
        fi
    fi
    # 상대에게 갈 때 쓰는 내 주소 — NIC 가 여럿이면 SELF_IP=<주소> 로 준다(파일에 남는다).
    SELF_IP="${SELF_IP:-$(ha_conf_get SELF_IP)}"
    SELF_IP="${SELF_IP:-$(ip -o route get "${PEER_IP:-1.1.1.1}" 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n1)}"
}

ha_save() {
    [[ -n "$HA_ROLE" ]] || return 0
    mkdir -p "$(dirname "$HA_CONF")"
    cat > "$HA_CONF" <<EOF
# 서버 두 대 이중화 — 이 서버의 모든 플랫폼이 공유한다. deploy.sh 가 쓴다(env 로 덮으면 갱신).
HA_ROLE=$HA_ROLE
LB_MODE=$LB_MODE
PEER_IP=$PEER_IP
SELF_IP=$SELF_IP
WEB_VIP=$WEB_VIP
DB_VIP=$DB_VIP
PUBLIC_HOST=$PUBLIC_HOST
VRRP_IFACE=$VRRP_IFACE
TLS_CERT=$TLS_CERT
TLS_KEY=$TLS_KEY
EOF
    chmod 644 "$HA_CONF"
}

# ───────────────────────── pg-ha 설치 ─────────────────────────
install_pg_ha() {
    [[ -f "$HERE/pg-ha.sh" ]] || { warn "pg-ha.sh 가 번들에 없습니다 — DB 주/대기 명령을 건너뜁니다"; return 1; }
    install -o root -g root -m 755 "$HERE/pg-ha.sh" /usr/local/sbin/pg-ha
}

pg_in_recovery() { [[ "$(sudo -u postgres psql -X -tAqc 'SELECT pg_is_in_recovery()' 2>/dev/null)" == "t" ]]; }

cmd_db() {  # $1=primary|standby|promote|demote|status, 나머지는 그대로
    install_pg_ha || exit 1
    local sub="$1"; shift
    case "$sub" in
        primary) /usr/local/sbin/pg-ha primary --peer "${PEER_IP:?PEER_IP 가 필요합니다}" ${DB_VIP:+--vip "$DB_VIP"} "$@" ;;
        standby)
            # 복제 비밀번호는 주가 만든다. 공용 폴더에 복사해 두었으면 거기서 받는다.
            local pass=""
            [[ -n "$DATA_DIR" && -f "$DATA_DIR/db/replication.pass" ]] && pass="$DATA_DIR/db/replication.pass"
            /usr/local/sbin/pg-ha standby --from "${1:-${PEER_IP:?PEER_IP 가 필요합니다}}" \
                ${DB_VIP:+--vip "$DB_VIP"} ${pass:+--replpass-from "$pass"}
            ;;
        *) /usr/local/sbin/pg-ha "$sub" "$@" ;;
    esac
    # 주가 만든 복제 비밀번호를 공용 폴더에 — 대기 서버의 standby 가 집어 간다.
    if [[ "$sub" == "primary" && -n "$DATA_DIR" && -f /etc/pg-ha.replpass ]]; then
        mkdir -p "$DATA_DIR/db"; install -m 600 /etc/pg-ha.replpass "$DATA_DIR/db/replication.pass"
        info "복제 비밀번호를 $DATA_DIR/db/replication.pass 에 두었습니다 — 대기 서버의 db-standby 가 읽습니다"
    fi
}

# ───────────────────────── 인증서 ─────────────────────────
ensure_tls() {
    [[ -f "$TLS_CERT" && -f "$TLS_KEY" ]] && return 0
    mkdir -p "$(dirname "$TLS_CERT")" "$(dirname "$TLS_KEY")"
    # 공용 폴더에 이미 있으면(다른 서버가 만들었으면) 그것을 — 두 서버가 같은 인증서를 낸다.
    if [[ -n "$DATA_DIR" && -f "$DATA_DIR/tls/server.crt" && -f "$DATA_DIR/tls/server.key" ]]; then
        info "인증서: $DATA_DIR/tls 의 것을 가져옵니다"
        install -m 644 "$DATA_DIR/tls/server.crt" "$TLS_CERT"
        install -m 600 "$DATA_DIR/tls/server.key" "$TLS_KEY"
        return 0
    fi
    info "자체 서명 인증서 생성: $PUBLIC_HOST (정식 인증서를 받으면 $TLS_CERT · $TLS_KEY 를 바꾸고 nginx reload)"
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -sha256 \
        -keyout "$TLS_KEY" -out "$TLS_CERT" -subj "/CN=$PUBLIC_HOST" \
        -addext "subjectAltName=DNS:$PUBLIC_HOST,IP:$WEB_VIP,IP:$SELF_IP,IP:$PEER_IP" >/dev/null 2>&1
    chmod 600 "$TLS_KEY"
    if [[ -n "$DATA_DIR" ]]; then
        mkdir -p "$DATA_DIR/tls"
        install -m 644 "$TLS_CERT" "$DATA_DIR/tls/server.crt"
        install -m 600 "$TLS_KEY"  "$DATA_DIR/tls/server.key"
    fi
}

# ───────────────────────── nginx ─────────────────────────
# 호스트 수준: server 블록 하나가 플랫폼들의 location 을 include 한다.
render_nginx_host() {
    mkdir -p "$ETC/etc/nginx/platforms.d" "$ETC/etc/nginx/sites-available"
    cat > "$ETC/etc/nginx/sites-available/platform-ha" <<EOF
# deploy.sh 가 쓴다 — 이 서버의 플랫폼들이 같은 호스트명 아래 경로로 갈린다.
# 플랫폼마다의 location 은 /etc/nginx/platforms.d/<slug>.conf 에 있다.
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name $PUBLIC_HOST $WEB_VIP $SELF_IP $PEER_IP _;
    return 301 https://\$host\$request_uri;
}

server {
    # 우분투 24.04 의 nginx(1.24)는 'http2 on;' 을 모른다 — 옛 형태로 적는다.
    listen 443 ssl http2 default_server;
    listen [::]:443 ssl http2 default_server;
    server_name $PUBLIC_HOST $WEB_VIP $SELF_IP $PEER_IP _;

    ssl_certificate     $TLS_CERT;
    ssl_certificate_key $TLS_KEY;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;

    # 첨부 · 대량 가져오기.
    client_max_body_size 512m;

    location = / { return 404; }
    include /etc/nginx/platforms.d/*.conf;
}
EOF
    mkdir -p "$ETC/etc/nginx/sites-enabled" "$ETC/etc/nginx/conf.d"
    ln -sf /etc/nginx/sites-available/platform-ha "$ETC/etc/nginx/sites-enabled/platform-ha"
    rm -f "$ETC/etc/nginx/sites-enabled/default"
}

# 플랫폼 수준: /<slug>/ → 두 서버의 앱, /<slug>/mcp → 두 서버의 MCP.
render_nginx_platform() {
    local prefix="/$APP_SLUG"
    cat > "$ETC/etc/nginx/conf.d/${APP_SLUG}-upstream.conf" <<EOF
# deploy.sh 가 쓴다 — $APP_NAME 의 앱 · MCP, 두 서버 모두 활성.
upstream ${APP_SLUG}_app {
    server $SELF_IP:$APP_PORT max_fails=3 fail_timeout=10s;
    server $PEER_IP:$APP_PORT max_fails=3 fail_timeout=10s;
    keepalive 32;
}
upstream ${APP_SLUG}_mcp {
    server $SELF_IP:$MCP_PORT max_fails=3 fail_timeout=10s;
    server $PEER_IP:$MCP_PORT max_fails=3 fail_timeout=10s;
}
EOF
    cat > "$ETC/etc/nginx/platforms.d/${APP_SLUG}.conf" <<EOF
# deploy.sh 가 쓴다 — $APP_NAME. 앱은 접두어 없이 받는다(PUBLIC_PATH=$prefix 가 화면 · 쿠키를 맞춘다).
location = $prefix { return 301 $prefix/; }

# MCP(스트리밍 HTTP) — 버퍼를 끄고 오래 연다.
location $prefix/mcp {
    proxy_pass http://${APP_SLUG}_mcp/mcp;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}

location $prefix/ {
    proxy_pass http://${APP_SLUG}_app/;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Prefix $prefix;
    # 대량 가져오기 · 그래프 — 기본 60초로는 끊긴다.
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    # 한 앱이 죽으면 그 요청을 다른 앱으로 — 읽기와 멱등한 것만(POST 는 두 번 가면 안 된다).
    proxy_next_upstream error timeout http_502 http_503;
    proxy_next_upstream_tries 2;
}

# 사진 일괄 업로드(zip) — 2GB 까지(앱의 JOB_PHOTOS_MAX_BYTES). 다른 경로는 위의 512m 그대로다 —
# 여기만 넓힌다(아무 경로나 2GB 를 받으면 그만큼이 앱의 임시 폴더에 먼저 쌓인다). 본문은 nginx 에
# 모으지 않고 흘려 넘긴다(proxy_request_buffering off — 다 받은 뒤 넘기면 nginx 디스크에 2GB 가
# 한 벌 더 생기고 그동안 앱은 아무것도 못 본다). 정규식 location 이라 proxy_pass 에 주소를 못
# 붙인다 — 접두어째 넘기고 앱이 받는다(PrefixMiddleware). JOB_PHOTOS_MAX_BYTES 를 올리면 여기도.
location ~ ^$prefix/api/objects/[^/]+/photos/import\$ {
    proxy_pass http://${APP_SLUG}_app;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Prefix $prefix;
    client_max_body_size 2100m;
    proxy_request_buffering off;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
}

# 화면 조각(이름에 해시) — **한 서버에 없으면 다른 서버에서.** 이중화를 한 대씩 올리는 동안
# 두 서버의 판이 다르다: 새 판에서 받은 화면이 조각을 옛 판 서버에 물으면 404 가 난다(위의
# 앱 location 은 404 를 넘기지 않는다 — POST 가 두 번 가면 안 되므로). 여기는 GET 뿐이다.
location $prefix/assets/ {
    proxy_pass http://${APP_SLUG}_app/assets/;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Prefix $prefix;
    proxy_next_upstream error timeout http_404 http_502 http_503;
    proxy_next_upstream_tries 2;
}
EOF
}

# ───────────────────────── 메인 서버용 nginx 조각 (LB_MODE=external) ─────────────────────────
# 메인 서버의 nginx 는 메인 서버 쪽이 관리한다 — 우리는 **넣어 달라고 할 조각**을 만들어 준다.
# 핵심은 X-Forwarded-Proto — 앱이 https 인 줄 알아야 쿠키(Secure)와 주소가 맞는다. 접두어는
# 벗겨 넘기든 그대로 넘기든 앱이 둘 다 받는다(PrefixMiddleware).
render_main_server_snippet() {
    local prefix="/$APP_SLUG" out="${1:-$INSTALL_DIR/main-server-nginx.conf}"
    mkdir -p "$(dirname "$out")"
    cat > "$out" <<EOF
# $APP_NAME — 메인 서버 nginx 에 넣을 조각 (deploy.sh 가 만들었다 · $(date -I))
# https://$PUBLIC_HOST$prefix/ → 서버 A · B 의 앱, $prefix/mcp → A · B 의 MCP.
# upstream 은 http 컨텍스트에, location 은 $PUBLIC_HOST 의 server(443) 블록 안에.

upstream ${APP_SLUG}_app {
    server $SELF_IP:$APP_PORT max_fails=3 fail_timeout=10s;
    server $PEER_IP:$APP_PORT max_fails=3 fail_timeout=10s;
    keepalive 32;
}
upstream ${APP_SLUG}_mcp {
    server $SELF_IP:$MCP_PORT max_fails=3 fail_timeout=10s;
    server $PEER_IP:$MCP_PORT max_fails=3 fail_timeout=10s;
}

# ---- 아래는 server { listen 443 ssl; server_name $PUBLIC_HOST; … } 안에 ----
location = $prefix { return 301 $prefix/; }

# MCP(스트리밍 HTTP) — 버퍼를 끄고 오래 연다.
location $prefix/mcp {
    proxy_pass http://${APP_SLUG}_mcp/mcp;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}

# 앱 — 끝의 '/' 가 접두어 $prefix 를 벗겨 넘긴다. 앱은 벗겨 오든 붙은 채 오든 둘 다 받으므로
# 'proxy_pass http://${APP_SLUG}_app;' (그대로 넘김) 이어도 된다. X-Forwarded-Proto 는 꼭 있어야 한다.
location $prefix/ {
    proxy_pass http://${APP_SLUG}_app/;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Prefix $prefix;
    client_max_body_size 512m;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    # 한 앱이 죽으면 다른 앱으로 — 읽기와 멱등한 것만(POST 는 두 번 가면 안 된다).
    proxy_next_upstream error timeout http_502 http_503;
    proxy_next_upstream_tries 2;
}

# 사진 일괄 업로드(zip) — 2GB 까지(앱의 JOB_PHOTOS_MAX_BYTES). 다른 경로는 위의 512m 그대로다 —
# 여기만 넓힌다(아무 경로나 2GB 를 받으면 그만큼이 앱의 임시 폴더에 먼저 쌓인다). 본문은 nginx 에
# 모으지 않고 흘려 넘긴다(proxy_request_buffering off — 다 받은 뒤 넘기면 nginx 디스크에 2GB 가
# 한 벌 더 생기고 그동안 앱은 아무것도 못 본다). 정규식 location 이라 proxy_pass 에 주소를 못
# 붙인다 — 접두어째 넘기고 앱이 받는다(PrefixMiddleware). JOB_PHOTOS_MAX_BYTES 를 올리면 여기도.
location ~ ^$prefix/api/objects/[^/]+/photos/import\$ {
    proxy_pass http://${APP_SLUG}_app;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Prefix $prefix;
    client_max_body_size 2100m;
    proxy_request_buffering off;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
}

# 화면 조각(이름에 해시) — **한 서버에 없으면 다른 서버에서.** 이중화를 한 대씩 올리는 동안
# 두 서버의 판이 다르다: 새 판에서 받은 화면이 조각을 옛 판 서버에 물으면 404 가 난다(위의
# 앱 location 은 404 를 넘기지 않는다 — POST 가 두 번 가면 안 되므로). 여기는 GET 뿐이다.
location $prefix/assets/ {
    proxy_pass http://${APP_SLUG}_app/assets/;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-Proto \$scheme;
    proxy_set_header X-Forwarded-Prefix $prefix;
    proxy_next_upstream error timeout http_404 http_502 http_503;
    proxy_next_upstream_tries 2;
}
EOF
    [[ -n "$ETC" ]] || chown "$OPERATOR:$OPERATOR" "$out" 2>/dev/null || true
}

# ───────────────────────── keepalived ─────────────────────────
# 호스트 수준. 웹 VIP 는 nginx 를 보고, DB VIP 는 pg-ha 를 본다.
# keepalived 가 필요한가 — DB VIP 가 있거나, 웹 VIP 를 우리가 쥐어야 할 때.
keepalived_needed() { [[ -n "$DB_VIP" || "$LB_MODE" == "local" ]]; }

render_keepalived() {
    keepalived_needed || return 0
    local prio_web=100 prio_db=100
    [[ "$HA_ROLE" == "master" ]] && prio_web=150
    # DB VIP 는 **주가 항상 이긴다**(check-primary +50). 서버 우선순위는 동점을 가를 뿐이다.
    [[ "$HA_ROLE" == "master" ]] && prio_db=101
    # 인증 문자열은 두 서버가 같아야 한다 — 호스트명에서 만들어 따로 맞출 것이 없게(8자 제한).
    local pass; pass="$(printf '%s' "$PUBLIC_HOST" | sha256sum | cut -c1-8)"
    mkdir -p "$ETC/etc/keepalived"
    cat > "$ETC/etc/keepalived/keepalived.conf" <<EOF
# deploy.sh 가 쓴다 — 이 서버의 역할: $HA_ROLE, 상대: $PEER_IP
global_defs {
    router_id $(hostname -s)
    enable_script_security
    script_user root
    vrrp_garp_master_delay 1
    vrrp_garp_master_repeat 3
}

EOF
    if [[ "$LB_MODE" == "local" ]]; then
        cat >> "$ETC/etc/keepalived/keepalived.conf" <<EOF

vrrp_script chk_nginx {
    script "/usr/bin/pidof nginx"
    interval 2
    fall 3
    rise 2
}

# ── 웹 VIP: nginx 가 살아 있는 쪽. 둘 다 살아 있으면 master 역할 서버가 쥔다. ──
vrrp_instance VI_WEB {
    state $( [[ "$HA_ROLE" == "master" ]] && echo MASTER || echo BACKUP )
    interface $VRRP_IFACE
    virtual_router_id 51
    priority $prio_web
    advert_int 1
    unicast_src_ip $SELF_IP
    unicast_peer { $PEER_IP }
    authentication { auth_type PASS  auth_pass $pass }
    virtual_ipaddress { $WEB_VIP }
    track_script { chk_nginx }
}
EOF
    fi
    if [[ -n "$DB_VIP" ]]; then
        cat >> "$ETC/etc/keepalived/keepalived.conf" <<EOF

vrrp_script chk_pg {
    script "/usr/local/sbin/pg-ha check"
    interval 5
    fall 3
    rise 2
}
vrrp_script chk_pg_primary {
    script "/usr/local/sbin/pg-ha check-primary"
    interval 5
    fall 2
    rise 2
    weight 50
}

# ── DB VIP: 주 DB 가 있는 쪽. 주가 죽어 대기가 VIP 를 받으면 pg-ha 가 승격한다. ──
# 승격까지 chk_pg 의 fall 3 × 5초 ≈ 15초 — 잠깐의 흔들림에 승격하지 않게 일부러 느리다.
vrrp_instance VI_DB {
    state BACKUP
    interface $VRRP_IFACE
    virtual_router_id 52
    priority $prio_db
    advert_int 1
    unicast_src_ip $SELF_IP
    unicast_peer { $PEER_IP }
    authentication { auth_type PASS  auth_pass $pass }
    virtual_ipaddress { $DB_VIP }
    track_script { chk_pg chk_pg_primary }
    notify_master "/usr/local/sbin/pg-ha on-master"
}
EOF
    fi
    chmod 644 "$ETC/etc/keepalived/keepalived.conf"
    # **keepalived 는 DB 와 nginx 가 뜬 뒤에.** 재부팅에서 keepalived 가 먼저 뜨면 아직 안 뜬
    # 주 DB 를 「죽었다」 로 보고 15초 뒤 대기를 승격한다 — 재부팅 한 번이 전환이 된다.
    mkdir -p "$ETC/etc/systemd/system/keepalived.service.d"
    cat > "$ETC/etc/systemd/system/keepalived.service.d/platform-ha.conf" <<EOF
[Unit]
After=postgresql.service$( [[ "$LB_MODE" == "local" ]] && echo ' nginx.service' )
EOF
    [[ -n "$ETC" ]] || systemctl daemon-reload
}

# root 없이 결과만 본다 — 'ETC=<폴더> ./deploy.sh render' 가 nginx · keepalived 설정을 거기 쓴다.
render_lb() {
    [[ -n "$HA_ROLE" ]] || { echo "(HA_ROLE 이 없어 로드밸런서 설정은 없다)"; return 0; }
    if [[ "$LB_MODE" == "local" ]]; then
        render_nginx_host
        render_nginx_platform
    else
        render_main_server_snippet "${ETC:+$ETC/main-server-nginx.conf}"
    fi
    render_keepalived
}

setup_lb() {
    [[ -n "$HA_ROLE" ]] || return 0
    install_pg_ha || true
    if [[ "$LB_MODE" == "local" ]]; then
        command -v nginx >/dev/null || err "nginx 가 없습니다 — 'sudo ./deploy.sh prepare' 를 먼저"
        ensure_tls
        render_nginx_host
        render_nginx_platform
        nginx -t >/dev/null 2>&1 || { nginx -t; err "nginx 설정이 틀렸습니다"; }
        systemctl enable nginx >/dev/null 2>&1 || true
        systemctl reload-or-restart nginx
        info "로드밸런서(이 서버): https://$PUBLIC_HOST/$APP_SLUG/  (VIP $WEB_VIP · 이 서버 $SELF_IP · 상대 $PEER_IP)"
    else
        render_main_server_snippet
        info "메인 서버에 넣을 nginx 조각: $INSTALL_DIR/main-server-nginx.conf  → https://$PUBLIC_HOST/$APP_SLUG/"
        echo "    메인 서버 쪽에 부탁할 것: 이 조각 그대로 — 특히 X-Forwarded-Proto (접두어는 벗겨도 그대로 넘겨도 된다)."
    fi
    if keepalived_needed; then
        command -v keepalived >/dev/null || err "keepalived 가 없습니다 — 'sudo ./deploy.sh prepare' 를 먼저"
        render_keepalived
        systemctl enable keepalived >/dev/null 2>&1 || true
        systemctl reload-or-restart keepalived
    fi
    [[ -n "$DB_VIP" ]] || warn "DB VIP 가 없어 자동 승격은 꺼져 있습니다 — 받으면 sudo DB_VIP=<주소> ./deploy.sh lb (양쪽)"
}

ha_status() {
    [[ -n "$HA_ROLE" ]] || { echo "  이중화   : (단독 서버)"; return 0; }
    echo "== 이중화 ($HA_ROLE · 이 서버 $SELF_IP · 상대 $PEER_IP · LB $LB_MODE) =="
    echo "  주소     : https://$PUBLIC_HOST/$APP_SLUG/"
    local code
    if [[ "$LB_MODE" == "local" ]]; then
        echo "  웹 VIP   : $WEB_VIP$( ip -o addr show 2>/dev/null | grep -q " inet $WEB_VIP/" && echo ' — 이 서버가 쥠' || echo ' — 다른 곳')"
        echo "  nginx    : $(systemctl is-active nginx 2>/dev/null)"
        code="$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 3 "https://127.0.0.1/$APP_SLUG/api/health" 2>/dev/null || true)"
        echo "  LB 경유  : https://127.0.0.1/$APP_SLUG/api/health → ${code:-실패}"
    else
        code="$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 5 "https://$PUBLIC_HOST/$APP_SLUG/api/health" 2>/dev/null || true)"
        echo "  메인 서버 경유 : https://$PUBLIC_HOST/$APP_SLUG/api/health → ${code:-실패 (메인 서버에 조각이 들어갔는지)}"
        echo "  상대 앱  : $(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "http://$PEER_IP:$APP_PORT/api/health" 2>/dev/null || echo 실패)"
    fi
    keepalived_needed && echo "  keepalived: $(systemctl is-active keepalived 2>/dev/null)"
    if [[ -x /usr/local/sbin/pg-ha ]]; then echo; /usr/local/sbin/pg-ha status; fi
}
