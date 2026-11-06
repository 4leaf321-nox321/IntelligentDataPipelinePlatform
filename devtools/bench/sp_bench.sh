#!/usr/bin/env bash
# 진짜 SP 로 대용량 시험 — 크기마다 **시험 인스턴스를 새로 세우고**(빈 DB) sp_bench.py 를 부른다.
#
#   devtools/bench/sp_bench.sh 20000 50000 100000
#
# 시험 인스턴스는 slug `jvhubbig` · 포트 8086 · DB `jvhubbig`(jvhubtest · SP 개발 DB 와 따로).
# 새 DB 는 `standardplatform_template`(pgvector 확장만 깔린 빈 DB)에서 만든다 — 확장은 postgres
# 계정만 만들 수 있어서다. 끝나면 인스턴스를 내리고 DB 를 지운다(`KEEP=1` 이면 남긴다).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export SANDBOX_SLUG=jvhubbig SANDBOX_PORT="${SANDBOX_PORT:-8086}"
export SANDBOX_WORK="$ROOT/.work/sandbox_big"
source "$ROOT/devtools/sandbox_sp/env.sh"
export SANDBOX_URL SANDBOX_WORK
ADMIN_DB="${SANDBOX_PG%/*}/postgres"

# 내린 워커는 하던 작업을 마치고 나간다 — 그 접속이 닫힌 뒤에 DB 를 지운다(최대 10분).
drop_db() {
    "$ROOT/devtools/sandbox_sp/down.sh" >/dev/null
    for _ in $(seq 1 600); do
        open="$(psql "$ADMIN_DB" -Atc "select count(*) from pg_stat_activity where datname='$SANDBOX_SLUG'")"
        [[ "$open" == "0" ]] && break
        sleep 1
    done
    psql "$ADMIN_DB" -qc "drop database if exists $SANDBOX_SLUG"
}

fresh() {
    drop_db
    psql "$ADMIN_DB" -qc "create database $SANDBOX_SLUG template standardplatform_template"
    rm -rf "$SANDBOX_WORK"
    "$ROOT/devtools/sandbox_sp/up.sh" >/dev/null
    "$ROOT/devtools/sandbox_sp/setup.sh" >/dev/null
}

for n in "$@"; do
    fresh
    (cd "$ROOT/backend" && .venv/bin/python ../devtools/bench/sp_bench.py "$n")
done
if [[ -z "${KEEP:-}" ]]; then
    drop_db
fi
