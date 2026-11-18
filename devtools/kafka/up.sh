#!/usr/bin/env bash
# 시험용 Kafka 한 대(KRaft, 127.0.0.1 만) — 평문 9192 · SASL(PLAIN · SCRAM-SHA-512) 9193 · 컨트롤러 9194.
# 먼저 fetch.sh. 비밀번호는 새로 만들어 0600 파일(.work/kafka/test-secrets.json)에만 — 출력하지 않는다.
# PID 는 .work/kafka/kafka.pid — down.sh 가 그 PID 만 내린다.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.work/kafka"
[[ -x "$ROOT/kafka/bin/kafka-server-start.sh" && -x "$ROOT/jre/bin/java" ]] \
    || { echo "먼저 devtools/kafka/fetch.sh" >&2; exit 1; }
CONF="$ROOT/conf"; DATA="$ROOT/data"; LOGS="$ROOT/logs"
export JAVA_HOME="$ROOT/jre" KAFKA_HEAP_OPTS="-Xms256m -Xmx768m" LOG_DIR="$LOGS"
mkdir -p "$CONF" "$LOGS"
if ss -ltn | grep -qE ':(9192|9193|9194)\b'; then echo "포트가 이미 쓰입니다" >&2; exit 1; fi

SECRETS="$ROOT/test-secrets.json"
if [[ ! -f "$SECRETS" ]]; then
    umask 077
    python3 - "$SECRETS" <<'EOF'
import json, secrets, sys
json.dump({"admin": secrets.token_urlsafe(18), "plain": secrets.token_urlsafe(18),
           "scram": secrets.token_urlsafe(18)}, open(sys.argv[1], "w"))
EOF
fi
read -r ADMIN PLAIN SCRAM < <(python3 -c "import json,sys; d=json.load(open('$SECRETS')); print(d['admin'], d['plain'], d['scram'])")

umask 077
cat > "$CONF/server.properties" <<EOF
process.roles=broker,controller
node.id=1
controller.quorum.bootstrap.servers=127.0.0.1:9194
listeners=PLAINTEXT://127.0.0.1:9192,SASL_PLAINTEXT://127.0.0.1:9193,CONTROLLER://127.0.0.1:9194
advertised.listeners=PLAINTEXT://127.0.0.1:9192,SASL_PLAINTEXT://127.0.0.1:9193,CONTROLLER://127.0.0.1:9194
inter.broker.listener.name=PLAINTEXT
controller.listener.names=CONTROLLER
listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT,SASL_PLAINTEXT:SASL_PLAINTEXT
sasl.enabled.mechanisms=PLAIN,SCRAM-SHA-512
listener.name.sasl_plaintext.plain.sasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username="admin" password="$ADMIN" user_svc_plain="$PLAIN";
listener.name.sasl_plaintext.scram-sha-512.sasl.jaas.config=org.apache.kafka.common.security.scram.ScramLoginModule required;
log.dirs=$DATA
num.partitions=3
auto.create.topics.enable=false
offsets.topic.replication.factor=1
share.coordinator.state.topic.replication.factor=1
share.coordinator.state.topic.min.isr=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
group.initial.rebalance.delay.ms=0
log.retention.check.interval.ms=300000
EOF

if [[ ! -f "$DATA/meta.properties" ]]; then
    ID="$("$ROOT/kafka/bin/kafka-storage.sh" random-uuid)"
    "$ROOT/kafka/bin/kafka-storage.sh" format --standalone -t "$ID" -c "$CONF/server.properties" \
        --add-scram "SCRAM-SHA-512=[name=svc_scram,password=$SCRAM]" > "$LOGS/format.out" 2>&1
    echo "저장소 준비(클러스터 $ID)"
fi

setsid nohup "$ROOT/kafka/bin/kafka-server-start.sh" "$CONF/server.properties" \
    > "$LOGS/server.out" 2>&1 < /dev/null &
echo $! > "$ROOT/kafka.pid"
for _ in $(seq 1 60); do
    if ss -ltn | grep -qE ':9192\b' && ss -ltn | grep -qE ':9193\b'; then break; fi
    sleep 1
done
ss -ltn | grep -E ':(9192|9193|9194)\b' | awk '{print $4}'
echo "Kafka PID $(cat "$ROOT/kafka.pid")"
