#!/usr/bin/env bash
# SP 정제 도구(sp_pipeline.py · sp_table.py)를 판을 골라 가져온다 — vendor/sp_pipeline/ 의 정본.
#
#   scripts/sync_sp_pipeline.sh v0.4.72                 SP 저장소 기본 위치(../StandardPlatform)
#   SP_REPO=/path/to/StandardPlatform scripts/sync_sp_pipeline.sh v0.4.80
#   scripts/sync_sp_pipeline.sh --check                 사본이 VERSION 의 해시와 같은가만 본다
#
# **사본은 고치지 않는다**(ADR 0001). 고칠 것이 있으면 SP 에서 고치고 — 거기서 백엔드 시험이 실제
# API 와 함께 도구를 지킨다 — 이 스크립트로 판을 올린다. 작업 트리가 아니라 **태그의 파일**을
# 꺼내므로 SP 에서 아직 커밋 안 한 것은 안 따라온다.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HERE/vendor/sp_pipeline"
FILES=(sp_pipeline.py sp_table.py)

if [[ "${1:-}" == "--check" ]]; then
    cd "$DEST"
    grep -E '^[0-9a-f]{64}  ' VERSION | sed 's#vendor/sp_pipeline/##' | sha256sum --check --quiet
    echo "사본이 VERSION 과 같습니다 ($(grep '^tag=' VERSION))"
    exit 0
fi

TAG="${1:?SP 태그를 주세요 (예: v0.4.72)}"
SP_REPO="${SP_REPO:-$HERE/../StandardPlatform}"
git -C "$SP_REPO" rev-parse --verify --quiet "$TAG^{commit}" >/dev/null \
    || { echo "SP 저장소($SP_REPO)에 $TAG 가 없습니다" >&2; exit 1; }
COMMIT="$(git -C "$SP_REPO" rev-parse --short "$TAG^{commit}")"

mkdir -p "$DEST"
for name in "${FILES[@]}"; do
    git -C "$SP_REPO" show "$TAG:pipeline/$name" > "$DEST/$name.new"
    mv "$DEST/$name.new" "$DEST/$name"
done
{
    echo "source=StandardPlatform/pipeline"
    echo "tag=$TAG"
    echo "commit=$COMMIT"
    (cd "$HERE" && sha256sum "${FILES[@]/#/vendor/sp_pipeline/}")
} > "$DEST/VERSION"
echo "가져왔습니다: $TAG ($COMMIT)"
cat "$DEST/VERSION"
echo "다음: cd backend && .venv/bin/python -m pytest tests/unit/test_relay_core.py"
