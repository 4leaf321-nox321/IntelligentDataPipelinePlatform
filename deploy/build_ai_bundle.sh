#!/usr/bin/env bash
# 폐쇄망 서버용 **임베딩 꾸러미** — Ollama 런타임 + 임베딩 모델(bge-m3)(ADR 0050).
#
#   ./deploy/build_ai_bundle.sh           → release/ai-bundle-<Ollama 판>-<모델>.tar (약 2.7GB)
#
# 인터넷이 되는 PC 에서 만든다 — sudo 도 Ollama 도 필요 없다(모델은 Ollama 저장소에서 바로 받는다). 필요한 것:
# curl · zstd · python3. 서버에서는 릴리스 번들을 푼 폴더 안에 풀면(`ai/` 가 생긴다) `deploy.sh` 의
# install · update · ollama 가 인터넷 대신 이것을 쓴다.
#
#   tar xf ai-bundle-<…>.tar -C <번들 폴더>
#   (다른 자리에 풀었으면) sudo AI_BUNDLE=<풀어 둔 ai 폴더> ./deploy.sh ollama
#
# **앱 판과 무관하다** — 한 번 만들어 두고 서버를 새로 세울 때마다 쓴다(이미 깔린 서버는 다시 안 받는다).
# 릴리스에 붙이지 않는 것은 크기 때문이다(GitHub 릴리스 파일 하나 2GB 상한).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# **판은 deploy.sh 가 정한다** — 여기서 다시 적으면 인터넷으로 까는 서버와 꾸러미로 까는 서버의 판이 갈린다.
OLLAMA_VERSION="${OLLAMA_VERSION:-$(sed -n 's/^OLLAMA_VERSION="\${OLLAMA_VERSION:-\(v[0-9.]*\)}".*/\1/p' deploy/deploy.sh)}"
[[ -n "$OLLAMA_VERSION" ]] || { echo "오류: deploy.sh 에서 OLLAMA_VERSION 을 읽지 못했습니다" >&2; exit 1; }
MODEL="${EMBEDDING_MODEL:-bge-m3}"
REGISTRY="https://registry.ollama.ai"

for tool in curl zstd python3 sha256sum; do
    command -v "$tool" >/dev/null || { echo "오류: $tool 이 없습니다" >&2; exit 1; }
done

# 모델 이름 — `bge-m3` 은 `library/bge-m3:latest` 다.
name="${MODEL%%:*}"; tag="${MODEL#*:}"
[[ "$tag" == "$MODEL" ]] && tag="latest"
[[ "$name" == */* ]] || name="library/$name"

STAGE="release/ai-bundle"
OUT="release/ai-bundle-${OLLAMA_VERSION}-${MODEL//[:\/]/-}.tar"
rm -rf "$STAGE"
mkdir -p "$STAGE/ai/models/blobs" "$STAGE/ai/models/manifests/registry.ollama.ai/$name"

echo "==> [1/3] Ollama $OLLAMA_VERSION (linux-amd64)"
# 받은 것은 .tar.zst 다 — 폐쇄망 서버에는 zstd 가 없을 수 있어 gzip 으로 다시 묶는다.
curl -fL --retry 3 -o "$STAGE/ollama.tar.zst" \
    "https://github.com/ollama/ollama/releases/download/$OLLAMA_VERSION/ollama-linux-amd64.tar.zst"
zstd -dc "$STAGE/ollama.tar.zst" | gzip -c > "$STAGE/ai/ollama-linux-amd64.tgz"
rm -f "$STAGE/ollama.tar.zst"
echo "    $(du -h "$STAGE/ai/ollama-linux-amd64.tgz" | cut -f1)"

echo "==> [2/3] 모델 $MODEL ($name:$tag)"
# Ollama 저장소의 모양 그대로 — manifests/registry.ollama.ai/<이름>/<태그> + blobs/sha256-<값>. 서버의
# 모델 폴더에 그대로 복사하면 Ollama 가 읽는다.
manifest="$STAGE/ai/models/manifests/registry.ollama.ai/$name/$tag"
curl -fsSL -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
    -o "$manifest" "$REGISTRY/v2/$name/manifests/$tag"
python3 - "$manifest" <<'PY' | while read -r digest; do
import json
import sys

manifest = json.load(open(sys.argv[1], encoding="utf-8"))
for layer in [manifest["config"], *manifest["layers"]]:
    print(layer["digest"])
PY
    blob="$STAGE/ai/models/blobs/${digest/:/-}"
    curl -fL --retry 3 -o "$blob" "$REGISTRY/v2/$name/blobs/$digest"
    [[ "sha256:$(sha256sum "$blob" | cut -d' ' -f1)" == "$digest" ]] \
        || { echo "오류: 받은 조각의 해시가 다릅니다: $digest" >&2; exit 1; }
    echo "    $digest $(du -h "$blob" | cut -f1)"
done

echo "==> [3/3] 묶기"
cat > "$STAGE/ai/MANIFEST.txt" <<INFO
임베딩 꾸러미 (ADR 0050)
ollama : $OLLAMA_VERSION linux-amd64
model  : $MODEL ($name:$tag)
built  : $(date -u +%Y-%m-%dT%H:%M:%SZ)
INFO
# 내용이 이미 눌린 것(.tgz · 모델 가중치)이라 다시 누르지 않는다.
tar -C "$STAGE" -cf "$OUT" ai
rm -rf "$STAGE"
echo
echo "==> $OUT ($(du -h "$OUT" | cut -f1))"
echo "    서버에서: tar xf $(basename "$OUT") -C <릴리스 번들 폴더>  →  sudo ./deploy.sh update (또는 ollama)"
