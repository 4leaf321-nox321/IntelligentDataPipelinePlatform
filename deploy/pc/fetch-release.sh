#!/usr/bin/env bash
# 최신 릴리스 번들을 받는다 — 인터넷 되는 PC(맥 · 리눅스)에서.
#
#   ./fetch-release.sh                       최신 → ./downloads/
#   ./fetch-release.sh -o ~/bundles          폴더 지정
#   ./fetch-release.sh -v v0.1.0             특정 버전
#   ./fetch-release.sh -a                    apptainer .deb 도 함께(폐쇄망 서버용)
#   REPO=<소유자>/<저장소> ./fetch-release.sh  릴리스를 내는 저장소가 다를 때
set -euo pipefail
REPO="${REPO:-4leaf321-nox321/IntelligentDataPipelinePlatform}"
OUT="$(dirname "$0")/downloads"; VERSION=""; APPTAINER=0
while getopts ":o:v:ah" opt; do
    case "$opt" in
        o) OUT="$OPTARG" ;; v) VERSION="$OPTARG" ;; a) APPTAINER=1 ;;
        h) sed -n '2,8p' "$0"; exit 0 ;; *) echo "모르는 옵션 — -h"; exit 1 ;;
    esac
done
mkdir -p "$OUT"
if [[ -z "$VERSION" ]]; then
    VERSION="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1)"
    [[ -n "$VERSION" ]] || { echo "최신 버전을 알아내지 못했습니다 — -v 로 주세요"; exit 1; }
fi
echo "==> 버전 $VERSION → $OUT"
NAME="datarelay-$VERSION.tar.gz"
for f in "$NAME" "$NAME.sha256"; do
    echo "    받는 중: $f"
    curl -fL --progress-bar -o "$OUT/$f" "https://github.com/$REPO/releases/download/$VERSION/$f"
done
( cd "$OUT" && sha256sum -c "$NAME.sha256" ) || { echo "체크섬이 다릅니다 — 다시 받으세요."; exit 1; }
if [[ $APPTAINER -eq 1 ]]; then
    URL="$(curl -fsSL https://api.github.com/repos/apptainer/apptainer/releases/latest | grep -oE 'https://[^"]*/apptainer_[0-9.]+_amd64\.deb' | head -n1)"
    if [[ -n "$URL" ]]; then echo "    받는 중: $(basename "$URL")"; curl -fL --progress-bar -o "$OUT/$(basename "$URL")" "$URL"
    else echo "경고: apptainer .deb 를 찾지 못했습니다 — https://github.com/apptainer/apptainer/releases 에서 직접"; fi
fi
echo
echo "[OK] $OUT 에 받았습니다. 서버로 옮기기 (계정·IP 는 자기 것으로):"
echo "     scp $OUT/$NAME $OUT/$NAME.sha256 <계정>@<서버IP>:~/"
