#!/usr/bin/env bash
# up.sh 가 띄운 Kafka 만 내린다 — **PID 로만**(이름으로 찾아 죽이면 같은 PC 의 다른 Java 까지 내린다).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.work/kafka"
file="$ROOT/kafka.pid"
[[ -f "$file" ]] || { echo "띄운 것이 없습니다"; exit 0; }
pid="$(cat "$file")"
if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null && echo "아직 내려가는 중입니다(PID $pid)" || echo "Kafka($pid) 내림"
fi
rm -f "$file"
