#!/usr/bin/env bash
# 시험용 Kafka(4.3.2) · Java 런타임(Temurin 21)을 .work/kafka 에 받는다 — 체크섬이 맞을 때만 푼다.
# 한 번만. 이미 풀었으면 아무것도 안 한다.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.work/kafka"
KAFKA=4.3.2
JRE_TAG="jdk-21.0.13%2B9"; JRE_FILE="OpenJDK21U-jre_x64_linux_hotspot_21.0.13_9.tar.gz"
if [[ -x "$ROOT/kafka/bin/kafka-server-start.sh" && -x "$ROOT/jre/bin/java" ]]; then
    echo "이미 있습니다: $ROOT"; exit 0
fi
mkdir -p "$ROOT/dl-kafka" "$ROOT/dl-jre" "$ROOT/kafka" "$ROOT/jre"
name="kafka_2.13-$KAFKA.tgz"
for base in "https://downloads.apache.org/kafka/$KAFKA" "https://archive.apache.org/dist/kafka/$KAFKA"; do
    if curl -sfL -o "$ROOT/dl-kafka/kafka.tgz" "$base/$name" \
        && curl -sfL -o "$ROOT/dl-kafka/kafka.tgz.sha512" "$base/$name.sha512"; then break; fi
done
release="https://github.com/adoptium/temurin21-binaries/releases/download/$JRE_TAG/$JRE_FILE"
curl -sfL -o "$ROOT/dl-jre/jre.tgz" "$release"
curl -sfL -o "$ROOT/dl-jre/jre.tgz.sha256.txt" "$release.sha256.txt"
python3 - "$ROOT" <<'PY'
import hashlib, sys
root = sys.argv[1]
want = "".join(open(f"{root}/dl-kafka/kafka.tgz.sha512").read().split(":", 1)[1].split()).lower()
got = hashlib.sha512(open(f"{root}/dl-kafka/kafka.tgz", "rb").read()).hexdigest()
jre_want = open(f"{root}/dl-jre/jre.tgz.sha256.txt").read().split()[0]
jre_got = hashlib.sha256(open(f"{root}/dl-jre/jre.tgz", "rb").read()).hexdigest()
if want != got or jre_want != jre_got:
    sys.exit("체크섬이 맞지 않습니다 — 받은 것을 지우고 다시 받으세요.")
print("체크섬 확인")
PY
tar -xzf "$ROOT/dl-jre/jre.tgz" -C "$ROOT/jre" --strip-components=1
tar -xzf "$ROOT/dl-kafka/kafka.tgz" -C "$ROOT/kafka" --strip-components=1
"$ROOT/jre/bin/java" -version 2>&1 | head -1
