#!/usr/bin/env bash
# SP 시험 인스턴스를 띄운다 — DB 가 없으면 만들고, 마이그레이션을 올리고, API(8087)와 작업 워커를 띄운다.
# 끝나면 PID 를 .work/sandbox_sp/*.pid 에 적는다(down.sh 가 그 PID 만 내린다).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
mkdir -p "$LOG_DIR" "$FILESTORE_DIR"

if ss -ltn "sport = :$SANDBOX_PORT" | grep -q LISTEN; then
    echo "포트 $SANDBOX_PORT 이 이미 쓰입니다 — 이미 떠 있으면 down.sh 부터." >&2
    exit 1
fi
psql "$SANDBOX_PG" -Atc "select 1 from pg_database where datname='$SANDBOX_SLUG'" | grep -q 1 \
    || psql "$SANDBOX_PG" -qc "create database $SANDBOX_SLUG"
cd "$SP_REPO/backend"
.venv/bin/alembic upgrade head 2>&1 | tail -1

setsid nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "$SANDBOX_PORT" \
    > "$LOG_DIR/api.out" 2>&1 < /dev/null &
echo $! > "$SANDBOX_WORK/api.pid"
setsid nohup .venv/bin/python -m app.worker > "$LOG_DIR/worker.out" 2>&1 < /dev/null &
echo $! > "$SANDBOX_WORK/worker.pid"

for _ in $(seq 1 30); do
    curl -sf "$SANDBOX_URL/api/health" >/dev/null 2>&1 && break
    sleep 1
done
curl -s "$SANDBOX_URL/api/health"; echo
echo "API PID $(cat "$SANDBOX_WORK/api.pid") · 워커 PID $(cat "$SANDBOX_WORK/worker.pid")"
