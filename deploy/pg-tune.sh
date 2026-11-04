#!/usr/bin/env bash
# PostgreSQL 서버 설정 맞추기(ADR 0031) — 이 PC 의 메모리 · CPU 로 권장값을 내고 지금 값과 견줘 보인다.
#
# **개발 PC 와 운영 서버가 같은 것을 쓴다.** PostgreSQL 을 설치한 기본값(공유 버퍼 128MB · 디스크 비용은 회전
# 디스크 기준 · 병렬 작업자 8)은 기록 2,000만 건에 맞지 않는데, 배포(deploy.sh)는 PostgreSQL 설정을 안 건드린다.
# 요청마다 거는 것(작업 메모리 · 요청당 병렬 작업자)은 앱이 `.env` 로 건다(READ_WORK_MEM · READ_PARALLEL_WORKERS)
# — 여기는 서버 전체에만 걸리는 것이다.
#
#   ./deploy/pg-tune.sh                         권장값 · 지금 값(바꾸지 않는다)
#   sudo ./deploy/pg-tune.sh --apply            ALTER SYSTEM 으로 적고 다시 읽힌다
#   ./deploy/pg-tune.sh --url "$DATABASE_URL"   그 접속으로(개발 PC — 접속 계정이 superuser 면 --apply 도)
#
#   --hdd         회전 디스크(기본은 SSD 로 본다 — random_page_cost 1.1)
#   --share 0.5   이 PC 메모리 중 PostgreSQL 몫(기본 0.5 — 앱 · 작업 워커 · MCP 와 나눈다. DB 만 도는 서버면 0.8)
#   --port 5432   root 로 돌 때 붙을 포트
#
# 공유 버퍼 · 작업 프로세스 수는 **PostgreSQL 을 재시작해야** 바뀐다 — 스크립트는 재시작하지 않고 무엇이 남았는지
# 말한다(사람이 한가한 때 재시작한다). 이중화(서버 두 대)면 **두 서버 다** 돌린다 — ALTER SYSTEM 은 그 서버의
# postgresql.auto.conf 에만 적힌다. 되돌리기: ALTER SYSTEM RESET <이름> (또는 RESET ALL) 뒤 다시 읽히기.
set -euo pipefail

APPLY=0
SSD=1
SHARE=0.5
PORT=5432
URL=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --hdd) SSD=0 ;;
        --ssd) SSD=1 ;;
        --share) SHARE="$2"; shift ;;
        --port) PORT="$2"; shift ;;
        --url) URL="$2"; shift ;;
        -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "모르는 옵션: $1 (--help)" >&2; exit 1 ;;
    esac
    shift
done

# SQLAlchemy 주소(postgresql+psycopg://)도 그대로 받는다.
URL="${URL/postgresql+psycopg:\/\//postgresql://}"

psql_run() {  # $1 = SQL — 결과는 「|」 로 가른 줄
    if [[ -n "$URL" ]]; then
        psql "$URL" -X -At -F '|' -v ON_ERROR_STOP=1 -c "$1"
    elif [[ $EUID -eq 0 ]]; then
        runuser -u postgres -- psql -p "$PORT" -X -At -F '|' -v ON_ERROR_STOP=1 -c "$1"
    else
        echo "root 로 돌리거나(sudo) --url 로 접속을 준다." >&2
        exit 1
    fi
}

ram_mb=$(( $(sed -n 's/^MemTotal: *\([0-9]*\) kB/\1/p' /proc/meminfo) / 1024 ))
cpus=$(nproc)
pg_mb=$(awk -v r="$ram_mb" -v s="$SHARE" 'BEGIN { printf "%d", r * s }')
(( pg_mb >= 512 )) || { echo "PostgreSQL 몫이 너무 작다($pg_mb MB) — --share 를 확인한다." >&2; exit 1; }

min() { (( $1 < $2 )) && echo "$1" || echo "$2"; }
max() { (( $1 > $2 )) && echo "$1" || echo "$2"; }

# 공유 버퍼 = 몫의 1/4(16GB 까지), 캐시 어림 = 몫의 3/4(나머지는 OS 가 캐시로 쓴다 — 처음 17초 · 두 번째 3초의
# 차이가 그것이다), 유지보수 메모리 = 몫의 1/16(2GB 까지 — 색인 만들기 · VACUUM).
shared=$(min $(( pg_mb / 4 )) 16384)
cache=$(( pg_mb * 3 / 4 ))
maint=$(min $(( pg_mb / 16 )) 2048)
workers=$(max "$cpus" 8)
gather=$(min 4 "$(max 2 $(( cpus / 4 )))")
maint_workers=$(min 4 "$(max 1 $(( cpus / 4 )))")
if (( SSD )); then page_cost=1.1; io=200; else page_cost=4; io=2; fi
# 임시 파일 상한(ADR 0048) — 프로세스 하나가 정렬 · 해시로 디스크에 쓸 수 있는 크기. 데이터 디스크의 1/5(100GB
# 까지). 넘으면 그 질의 · 작업만 실패한다 — 디스크가 차서 DB 가 통째로 멈추는 것보다 낫다. 앱의 읽기 요청은 따로
# 더 작게 건다(READ_TEMP_LIMIT). 데이터 폴더를 못 읽으면(superuser 가 아닌 --url) 권하지 않는다.
temp_limit=""
data_dir="$(psql_run "SHOW data_directory" 2>/dev/null || true)"
if [[ -n "$data_dir" ]]; then
    disk_kb="$(df -Pk "$data_dir" 2>/dev/null | awk 'NR == 2 { print $2 }' || true)"
    [[ -n "$disk_kb" ]] && temp_limit="$(min $(( disk_kb / 1024 / 5 )) 102400)MB"
fi

declare -A WANT=(
    [shared_buffers]="${shared}MB"
    [effective_cache_size]="${cache}MB"
    [maintenance_work_mem]="${maint}MB"
    [random_page_cost]="$page_cost"
    [effective_io_concurrency]="$io"
    [max_worker_processes]="$workers"
    [max_parallel_workers]="$workers"
    [max_parallel_workers_per_gather]="$gather"
    [max_parallel_maintenance_workers]="$maint_workers"
)
ORDER=(shared_buffers effective_cache_size maintenance_work_mem random_page_cost effective_io_concurrency
       max_worker_processes max_parallel_workers max_parallel_workers_per_gather max_parallel_maintenance_workers)
if [[ -n "$temp_limit" ]]; then WANT[temp_file_limit]="$temp_limit"; ORDER+=(temp_file_limit); fi

names=$(printf "'%s'," "${ORDER[@]}"); names="${names%,}"
show() {
    psql_run "SELECT name, current_setting(name), pending_restart FROM pg_settings WHERE name IN ($names)"
}

echo "== 이 PC: 메모리 ${ram_mb}MB · CPU ${cpus}개 · 디스크 $( (( SSD )) && echo SSD || echo 회전) · PostgreSQL 몫 ${SHARE} (${pg_mb}MB)"
declare -A NOW=()
while IFS='|' read -r name value _pending; do
    [[ -n "$name" ]] && NOW[$name]="$value"
done < <(show)
printf '%-34s %-14s %s\n' "설정" "지금" "권장"
for name in "${ORDER[@]}"; do
    printf '%-34s %-14s %s\n' "$name" "${NOW[$name]:-?}" "${WANT[$name]}"
done

if (( ! APPLY )); then
    echo
    echo "바꾸지 않았다 — 적으려면 --apply(root 이거나, --url 의 계정이 superuser 여야 한다)."
    exit 0
fi

for name in "${ORDER[@]}"; do
    psql_run "ALTER SYSTEM SET $name = '${WANT[$name]}'" >/dev/null
done
psql_run "SELECT pg_reload_conf()" >/dev/null
sleep 1
pending=()
while IFS='|' read -r name _value is_pending; do
    [[ "$is_pending" == "t" ]] && pending+=("$name")
done < <(show)
echo
echo "적었다(postgresql.auto.conf) · 다시 읽혔다."
if (( ${#pending[@]} )); then
    echo "재시작해야 바뀌는 것: ${pending[*]}"
    echo "  한가한 때 PostgreSQL 을 재시작한다 — 운영: sudo systemctl restart postgresql (이중화면 대기 → 주 순서)"
    echo "                                    개발 PC(WSL): sudo service postgresql restart"
    echo "  앱은 다음 요청에서 다시 붙는다."
fi
