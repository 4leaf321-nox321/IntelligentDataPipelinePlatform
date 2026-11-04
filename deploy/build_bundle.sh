#!/usr/bin/env bash
# 릴리스 번들 하나를 만든다 — 폐쇄망 서버에 이 파일 하나만 옮기면 된다.
#
#   ./deploy/build_bundle.sh            버전은 git describe 에서
#   ./deploy/build_bundle.sh v0.1.0     직접 지정
#
# 산출물: release/<slug>-<버전>.tar.gz
#   app.sif · deploy.sh · ha.sh · pg-ha.sh · pg-tune.sh · backup.sh · restore.sh · *.template · .env.example
#   · BUILD_INFO · README.md · mcp_server/ (서버 + 오프라인 설치용 휠) · apptainer_debs/ · pgvector_debs/ (폐쇄망용)
#
# **배포 스크립트를 번들에 함께 담는다.** 서버가 릴리스만 받는 환경이어도 tar 하나로
# 그다음 배포가 돌아야 한다 — 빠뜨리면 첫 배포에 저장소를 클론하는 수밖에 없고,
# 폐쇄망에는 그 길이 없다.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

command -v apptainer >/dev/null || { echo "오류: apptainer 가 없습니다"; exit 1; }
command -v npm       >/dev/null || { echo "오류: npm 이 없습니다 (프론트 빌드에 필요)"; exit 1; }

# ── 기본 정보 — **branding.py 와 config.py 에서 읽는다** ──────────────────────
#
# **번들 하나로 여러 플랫폼을 설치한다.** 여기서 읽는 이름은 설치가 APP_SLUG 를 안 줬을 때의
# 기본값(틀의 이름)이고 번들 파일 이름이 된다. 실제 플랫폼 이름은 deploy.sh 에 주는 env 다.
# 여기서 다시 적으면 그때부터 두 벌이다.
read -r APP_NAME APP_SLUG APP_PORT <<EOF
$(python3 - <<'PY'
import ast, pathlib, sys

def literals(path, names):
    tree = ast.parse(pathlib.Path(path).read_text(encoding='utf-8'))
    out = {}
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name) and target.id in names:
            try:
                out[target.id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out

branding = literals('backend/app/branding.py', {'DEFAULT_APP_NAME', 'DEFAULT_APP_SLUG'})
config = literals('backend/app/config.py', {'port'})
for key in ('DEFAULT_APP_NAME', 'DEFAULT_APP_SLUG'):
    if key not in branding:
        sys.exit(f'branding.py 에서 {key} 를 읽지 못했습니다.')
if 'port' not in config:
    sys.exit('config.py 에서 port 를 읽지 못했습니다.')
print(branding['DEFAULT_APP_NAME'], branding['DEFAULT_APP_SLUG'], config['port'])
PY
)
EOF
[[ -n "$APP_SLUG" ]] || { echo "오류: 플랫폼 정보를 읽지 못했습니다"; exit 1; }

VERSION="${1:-$(git describe --tags --always --dirty 2>/dev/null || date +%Y%m%d-%H%M%S)}"
RELEASE_NAME="${APP_SLUG}-${VERSION}"
OUT_DIR="release"
STAGE="${OUT_DIR}/${RELEASE_NAME}"

# MCP 서버 포트 — **앱 포트 +2.** 플랫폼마다 10씩 벌리는 규칙 안에서 운영(+0)·개발(+1)
# 다음 자리다. 따로 적으면 옆 플랫폼과 겹치는지 볼 자리가 하나 더 생긴다.
MCP_PORT=$((APP_PORT + 2))

echo "==> $APP_NAME ($APP_SLUG) · 포트 $APP_PORT (MCP $MCP_PORT) · 버전 $VERSION"

# ── 1. 프론트엔드 ─────────────────────────────────────────────────────────────
# **이게 없으면 배포된 앱이 모든 페이지에 API 의 JSON 404 를 돌려준다** —
# 라우팅 버그처럼 보이지만 아니다. 그래서 SIF 를 만들기 **전에** 여기서 만든다.
echo
echo "==> [1/4] 프론트엔드 빌드"
(cd frontend && npm ci && npm run build)
[[ -f frontend/dist/index.html ]] || { echo "오류: frontend/dist/index.html 이 없습니다"; exit 1; }

# 프론트는 API 절대주소를 굽지 않는다(항상 상대경로 /api). 굽는 방식이면 값이
# 빠졌을 때 **사용자 브라우저가 자기 PC 를 부른다** — 흔적이 남았는지 본다.
if grep -rlqs -e '127.0.0.1:80' -e 'localhost:80' frontend/dist/assets 2>/dev/null; then
    echo "오류: 번들에 개발 서버 주소가 남아 있습니다. API 주소를 굽지 않도록 고치세요."
    exit 1
fi
echo "    API 주소 검사 통과"

# ── 1b. 정제 도구 키트 — 같은 판을 이미지 안에 넣는다 ─────────────────────────
# 사용자 PC 에 푸는 zip(`sp-pipeline`)이다. 서버가 들고 있다가 화면(내 정보)에서 내려준다.
# **따로 받게 두면** 사내망에서는 GitHub 에 못 닿아 받을 길이 없고, 운영의 사람도 AI 도 그런
# 것이 있는 줄 몰랐다(실측). 서버와 **같은 판**이어야 한다 — 어긋나면 키트가 보내는 칸(백필
# 칸 같은 것)을 서버가 모른다.
#
# 릴리스는 키트를 먼저 만들어 두므로(`release.yml`) 그것을 그대로 쓴다 — 릴리스에 올라가는
# zip 과 서버가 내려주는 zip 이 **같은 파일**이다. 없으면 여기서 만든다.
echo
echo "==> [1b/4] 정제 도구 키트 (이미지에 넣는다)"
KIT="${OUT_DIR}/sp-pipeline-${VERSION}.zip"
if [[ ! -f "$KIT" ]]; then
    ./deploy/build_pipeline_kit.sh "$VERSION"
fi
[[ -f "$KIT" ]] || { echo "오류: 정제 도구 키트를 만들지 못했습니다: $KIT"; exit 1; }
# .def 의 %files 가 집는 자리 — 이름에 판을 안 넣는다(.def 는 고정 경로만 받는다).
cp "$KIT" deploy/pipeline-kit.zip
echo "    $(basename "$KIT") ($(du -h "$KIT" | cut -f1))"

# ── 2. SIF ────────────────────────────────────────────────────────────────────
echo
echo "==> [2/4] Apptainer SIF 빌드 (처음에는 5~10분)"
rm -rf "$STAGE"
mkdir -p "$STAGE"

# **이미지가 자기 버전을 들고 들어가게 한다.** 안 넣으면 `/api/health` 가
# `version: unknown` 을 돌려주고, 「지금 서버에 뭐가 깔렸나」 를 물을 자리가
# 없어진다 — 실측으로 그렇게 나왔다. .def 의 %files 가 이 파일을 집는다.
cat > deploy/BUILD_INFO.txt <<INFO
version=$VERSION
app_name=$APP_NAME
app_slug=$APP_SLUG
python=3.12
INFO
# CI 러너에는 subuid/subgid 매핑이 없어 비특권 빌드가 실패한다. 로컬 개발은
# sudo 없이 그대로 되게 두고, 필요한 곳에서만 켠다.
APPTAINER_BUILD=(apptainer build --force)
if [[ "${USE_SUDO_APPTAINER:-0}" == "1" ]]; then
    APPTAINER_BUILD=(sudo apptainer build --force)
fi
"${APPTAINER_BUILD[@]}" "$STAGE/app.sif" deploy/apptainer.def

# ── 3. 스크립트·문서·BUILD_INFO ───────────────────────────────────────────────
echo
echo "==> [3/4] 배포 스크립트와 문서 동봉"
cp deploy/deploy.sh                  "$STAGE/"
# **백업·복구도 함께 담는다.** README 가 번들 안에서 `./backup.sh` 를 시키는데
# 없으면, 그 사실은 **백업이 필요해진 날**에야 드러난다. 그리고 SSH 로만 닿는
# 서버에서는 「저장소에서 마저 가져오기」 가 성립하지 않는다.
cp deploy/backup.sh                  "$STAGE/"
cp deploy/restore.sh                 "$STAGE/"
cp deploy/app.service.template       "$STAGE/"
cp deploy/mcp.service.template       "$STAGE/"
cp deploy/worker.service.template    "$STAGE/"
cp deploy/sync.service.template      "$STAGE/"
cp deploy/sync.timer.template        "$STAGE/"
cp deploy/metrics.service.template   "$STAGE/"
cp deploy/metrics.timer.template     "$STAGE/"
cp deploy/backup.service.template    "$STAGE/"
cp deploy/backup.timer.template      "$STAGE/"
# 이중화 — deploy.sh 가 source 하는 ha.sh 와, 서버에 /usr/local/sbin/pg-ha 로 깔리는 pg-ha.sh.
# **ha.sh 가 없으면 deploy.sh 가 단독 서버에서도 안 돈다**(첫 줄에서 찾는다).
cp deploy/ha.sh                      "$STAGE/"
cp deploy/pg-ha.sh                   "$STAGE/"
# PostgreSQL 서버 설정 맞추기 — `deploy.sh pg-tune` 이 부른다(ADR 0031). 개발 PC 는 저장소의 것을 그대로.
cp deploy/pg-tune.sh                 "$STAGE/"
cp deploy/.env.production.example    "$STAGE/.env.example"
cp deploy/README_OPERATOR.md         "$STAGE/README.md"
cp deploy/쉬운-설치.md               "$STAGE/쉬운-설치.md"
# HWAX 포털 연동 요청서의 틀 — `deploy.sh hwax-request` 가 이 설치의 값으로 채운다(ADR 0028). 정본은 docs/ 에
# 있고(포털 담당이 저장소에서도 읽는다) 번들에는 이름만 바꿔 담는다 — 없으면 서버에서 요청서를 못 만든다.
cp "docs/[참고] HWAX포탈_연동_요청서.md" "$STAGE/HWAX-연동요청서.template.md"
chmod +x "$STAGE"/*.sh

# ── 3a. apptainer .deb 동봉 (폐쇄망 대비) ────────────────────────────────────
# apptainer 는 우분투 기본 저장소에 없다(공식 PPA 전용). 운영 서버가 폐쇄망이면 prepare 가
# 설치하지 못해 사람이 .deb 를 구해 와야 했다 — 첫 서버 설치 때 실제로 겪었다. 빌드 머신
# (PPA 있음 · 운영과 같은 우분투 24.04/amd64)에서 미리 받아 두면 deploy.sh 의
# ensure_apptainer 가 이것을 1순위로 깐다. uidmap · libfuse3-3 은 최소 설치 서버에 없을 수
# 있는 의존성이다.
echo "==> [3a/4] apptainer .deb 동봉 (오프라인 설치용)"
mkdir -p "$STAGE/apptainer_debs"
if (cd "$STAGE/apptainer_debs" && apt-get download apptainer uidmap libfuse3-3 >/dev/null 2>&1); then
    echo "    동봉: $(ls "$STAGE/apptainer_debs" | tr '\n' ' ')"
else
    rm -rf "$STAGE/apptainer_debs"
    echo "    ⚠ apt-get download 실패(빌드 머신에 apptainer PPA 가 없나?) — .deb 미동봉."
    echo "      폐쇄망 서버에서는 ensure_apptainer 가 안내하는 수동 반입이 필요합니다."
fi

# pgvector(ADR 0050) — 뜻으로 찾기의 확장. 폐쇄망 서버도 갱신만으로 깔리게(update 가 이 폴더를 먼저 본다). **우분투 저장소의 판을 고른다** — 빌드 머신에 PostgreSQL 공식 저장소(PGDG)가 붙어 있으면 그쪽의 더
# 새 판이 먼저 잡혀, 인터넷으로 까는 서버(우분투 판)와 판이 갈린다. 운영과 같은 PostgreSQL 주 판(16).
PG_VERSION="${PG_VERSION:-16}"
echo "==> [3a/4] pgvector .deb 동봉 (postgresql-$PG_VERSION-pgvector)"
mkdir -p "$STAGE/pgvector_debs"
# 주소로 고르지 않는다 — GitHub 러너는 우분투 저장소를 미러 목록(`mirror+file:/etc/apt/apt-mirrors.txt`)으로
# 가리켜 주소에 ubuntu.com 이 없다(v0.4.71 의 첫 빌드가 그래서 못 받았다). PGDG 만(`<판>-pgdg`) 뺀다.
pgvector_ver="$(apt-cache madison "postgresql-$PG_VERSION-pgvector" 2>/dev/null \
    | awk -F'|' '$3 !~ /pgdg/ { gsub(/ /, "", $2); print $2; exit }')"
if [[ -n "$pgvector_ver" ]] \
    && (cd "$STAGE/pgvector_debs" && apt-get download "postgresql-$PG_VERSION-pgvector=$pgvector_ver" >/dev/null 2>&1); then
    echo "    동봉: $(ls "$STAGE/pgvector_debs" | tr '\n' ' ')"
else
    rm -rf "$STAGE/pgvector_debs"
    echo "    ⚠ 우분투 저장소에서 postgresql-$PG_VERSION-pgvector 를 받지 못했습니다 — .deb 미동봉."
    echo "      폐쇄망 서버에서는 deploy.sh 가 안내하는 수동 반입이 필요합니다."
fi

# ── 3b. MCP 서버 (별도 venv 로 운영 서버에서 돌아감) ──────────────────────────
# 백엔드 SIF 와 의존성이 충돌해 컨테이너에 못 넣는다. 소스 + 오프라인 설치용 휠을
# 동봉 → deploy.sh 가 운영 호스트에서 venv 만들고 `pip install --no-index` 로 설치.
echo "==> [3b/4] MCP 서버 + 오프라인 휠 동봉"
mkdir -p "$STAGE/mcp_server"
cp mcp_server/server.py        "$STAGE/mcp_server/"
cp mcp_server/requirements.txt "$STAGE/mcp_server/"
[[ -f mcp_server/README.md ]] && cp mcp_server/README.md "$STAGE/mcp_server/" || true
# 스킬 스텁(사용자가 ~/.claude/skills 로 설치) — 번들에 동봉
[[ -d mcp_server/skill ]] && cp -r mcp_server/skill "$STAGE/mcp_server/skill" || true
# 사용 가이드(get_guide 가 읽는 본문) — **서버가 쥔다.** 빠지면 get_guide 가
# 빈 응답을 주고 AI 는 도구 설명만으로 헤맨다.
[[ -d mcp_server/guide ]] && cp -r mcp_server/guide "$STAGE/mcp_server/guide" || true
# 빌드 머신(인터넷 O)에서 휠을 받아 둔다. 운영 호스트가 폐쇄망이어도 설치되게.
# 빌드/운영 아키텍처가 같다고 가정(둘 다 linux x86_64). 다르면 --platform 지정 필요.
if python3 -m pip download --only-binary=:all: \
        -r mcp_server/requirements.txt -d "$STAGE/mcp_server/wheels" >/dev/null 2>&1; then
    echo "    휠 $(ls "$STAGE/mcp_server/wheels" | wc -l)개 동봉"
else
    echo "    ⚠ pip download 실패 — 휠 미동봉. 운영 호스트에 인터넷/사내 PyPI 있어야 MCP 설치됨."
fi

# **번들의 기본값.** deploy.sh 는 APP_SLUG 를 안 줬을 때만 여기 이름을 쓴다 — 배포
# 스크립트에는 제품 이름이 박혀 있지 않고, 실제 이름은 설치마다 env 로 온다.
cat > "$STAGE/BUILD_INFO" <<INFO
app_name=$APP_NAME
app_slug=$APP_SLUG
port=$APP_PORT
mcp_port=$MCP_PORT
version=$VERSION
python=3.12
built_at=$(date -Is)
INFO

# ── 4. tar ────────────────────────────────────────────────────────────────────
echo
echo "==> [4/4] 묶기"
tar czf "${OUT_DIR}/${RELEASE_NAME}.tar.gz" -C "$OUT_DIR" "$RELEASE_NAME"

# **체크섬을 함께 낸다.** 이 파일은 기계를 두 번 건넌다(빌드 -> 내려받는 PC ->
# scp -> 서버). 중간에 잘리거나 절반만 받아진 tar 는 **푸는 순간**에야 드러나고,
# 그때는 배포하려고 서버에 붙어 있는 자리다.
( cd "$OUT_DIR" && sha256sum "${RELEASE_NAME}.tar.gz" > "${RELEASE_NAME}.tar.gz.sha256" )

SIZE=$(du -h "${OUT_DIR}/${RELEASE_NAME}.tar.gz" | cut -f1)
echo
echo "[OK] ${OUT_DIR}/${RELEASE_NAME}.tar.gz  (${SIZE})"
echo "     ${OUT_DIR}/${RELEASE_NAME}.tar.gz.sha256"
echo "     이 파일 하나를 운영 서버로 옮기면 됩니다."
