# SP 시험 인스턴스(「자주검증 Datahub(시험)」)의 환경 — up.sh · down.sh · setup.py 가 함께 읽는다.
#
# **SP 개발 DB(standardplatform)를 건드리지 않는다.** 같은 SP 코드를 slug · DB · 포트만 바꿔 띄운다.
# 접속 계정은 SP 개발 .env 의 것(같은 PC 의 형제 플랫폼들이 다 그 계정을 쓴다)이고 DB 이름만 바꾼다.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SP_REPO="${SP_REPO:-$ROOT/../StandardPlatform}"
SANDBOX_WORK="${SANDBOX_WORK:-$ROOT/.work/sandbox_sp}"
SANDBOX_SLUG="${SANDBOX_SLUG:-jvhubtest}"
SANDBOX_PORT="${SANDBOX_PORT:-8087}"

_sp_url="$(grep -E '^DATABASE_URL=' "$SP_REPO/backend/.env" | cut -d= -f2-)"
export APP_ENV=development
export APP_SLUG="$SANDBOX_SLUG"
export APP_NAME="자주검증 Datahub(시험)"
export APP_TAGLINE="datarelay 시험용 SP 인스턴스"
export DATABASE_URL="${_sp_url%/*}/$SANDBOX_SLUG"
export LOG_DIR="$SANDBOX_WORK/logs"
export FILESTORE_DIR="$SANDBOX_WORK/filestore"
export EXTENSIONS=""
export MCP_DEV=0
SANDBOX_PG="$(echo "${_sp_url}" | sed 's#postgresql+psycopg#postgresql#')"
SANDBOX_URL="http://127.0.0.1:$SANDBOX_PORT"
