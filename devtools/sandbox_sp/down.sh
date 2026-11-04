#!/usr/bin/env bash
# up.sh 가 띄운 두 프로세스만 내린다 — **PID 로만.** 이름으로 찾아 죽이면 같은 PC 의 다른
# 플랫폼(같은 uvicorn · app.worker)까지 내린다.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
for name in api worker; do
    file="$SANDBOX_WORK/$name.pid"
    [[ -f "$file" ]] || continue
    pid="$(cat "$file")"
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" && echo "$name($pid) 내림"
    fi
    rm -f "$file"
done
