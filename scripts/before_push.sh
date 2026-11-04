#!/usr/bin/env bash
# 밀기 전 점검 — **CI 가 보는 것을 같은 환경에서 먼저 본다.**
#
# 왜 있나: 로컬에서 통과한 것이 CI 에서 두 번 깨졌다. 한 번은 시험이 개발 `.env` 의
# `EXTENSIONS=sample` 에 기대서(CI 에는 `.env` 가 없다), 한 번은 `openapi.json` 을
# 재생성한 **뒤에** 독스트링을 고쳐서. 둘 다 사람이 순서를 기억해야 하는 종류이고,
# 그런 것은 바쁠 때 반드시 빠진다. 한 번 돌리는 데 4~6분 — CI 왕복 한 번과 같다.
#
#   scripts/before_push.sh
#
# 첫 실패에서 멈추지 않는다. CI 는 세 작업이 나란히 돌아 문제를 한 번에 보여 주는데,
# 이쪽이 하나씩 멈추면 같은 대기를 여러 번 하게 된다.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$REPO/backend"
FRONT="$REPO/frontend"
PY="${PY:-$BACKEND/.venv/bin/python}"
# MCP 는 환경을 가른다(`mcp` 를 백엔드에 깔면 시험이 쓰는 HTTP 스택이 바뀐다). 저장소
# 안의 것을 먼저 본다 — `/tmp` 에 두면 날이 바뀔 때 사라지고, 그때 이 시험이 조용히
# 건너뛰어진다(실측).
MCP_PY="${MCP_PY:-}"
if [ -z "$MCP_PY" ]; then
  for candidate in "$REPO/mcp_server/venv/bin/python" /tmp/mcpvenv/bin/python; do
    [ -x "$candidate" ] && MCP_PY="$candidate" && break
  done
fi
MCP_PY="${MCP_PY:-/tmp/mcpvenv/bin/python}"
LOGS="$(mktemp -d)"
FAILED=()
SKIPPED=()

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# step <이름> <명령...> — 실패해도 돌아온다(`set -e` 가 안 물게 조건문 안에서 부른다).
step() {
  local name="$1"; shift
  local log="$LOGS/${name//[ \/]/_}.log"
  local start=$SECONDS
  if "$@" >"$log" 2>&1; then
    printf '  ✓ %s — %d초\n' "$name" $((SECONDS - start))
  else
    printf '  ✗ %s — %d초\n' "$name" $((SECONDS - start))
    FAILED+=("$name|$log")
  fi
  return 0
}

# ── 0. 저장소 규칙 ──────────────────────────────────────────────────────
#
# CI 가 안 보는 것들이다. 한 번씩 실제로 사고가 났던 자리만 둔다.
check_private_docs() {
  # **`docs/사내/` 는 저장소가 public 이라 절대 추적되면 안 된다.**
  ! git -C "$REPO" ls-files | grep -q '사내'
}

check_emptied_files() {
  # **비우고 다시 쓰는 스크립트가 파일을 지운 적이 있다**(`쉬운-설치.md` 154줄,
  # `package-lock.json` 10,503줄이 빈 채로 밀렸다). 내용이 있던 파일이 0바이트가
  # 됐으면 그것은 의도가 아니다.
  local bad=()
  while IFS= read -r path; do
    [ -f "$REPO/$path" ] || continue
    [ -s "$REPO/$path" ] && continue
    git -C "$REPO" cat-file -e "HEAD:$path" 2>/dev/null || continue
    [ -s <(git -C "$REPO" show "HEAD:$path") ] && bad+=("$path")
  done < <(git -C "$REPO" diff --name-only HEAD)
  if [ ${#bad[@]} -gt 0 ]; then
    printf '빈 파일이 됐습니다: %s\n' "${bad[*]}"
    return 1
  fi
}

say "0. 저장소 규칙"
step "사내 문서가 추적되지 않는다" check_private_docs
step "내용이 있던 파일이 비지 않았다" check_emptied_files

# ── 1. 백엔드 ───────────────────────────────────────────────────────────
say "1. 백엔드 (ruff · mypy · pytest)"
ruff_check() { cd "$BACKEND" && "$PY" -m ruff check . ../mcp_server ../pipeline --config pyproject.toml; }
ruff_format() { cd "$BACKEND" && "$PY" -m ruff format --check . ../mcp_server ../pipeline --config pyproject.toml; }
mypy_strict() { cd "$BACKEND" && "$PY" -m mypy; }
# **`EXTENSIONS` 를 비운다.** CI 에는 `.env` 가 없어서 확장 기본값이 「꺼짐」 이다 —
# 개발 `.env` 를 딛고 통과한 시험은 거기서만 통과한다.
# **작업자 여럿으로**(pytest-xdist) — 순서대로는 13분, 작업자 8개는 1분(실측 2026-10-07). 작업자마다
# DB 를 따로 쓴다(`<이름>_gw0_test` …, tests/conftest.py). 작업자 하나가 DB 접속을 열댓 개까지
# 쓰니 PostgreSQL 의 max_connections(기본 100)를 넘지 않게 묶는다 — PYTEST_WORKERS 로 바꾼다.
PYTEST_WORKERS="${PYTEST_WORKERS:-8}"
pytest_all() { cd "$BACKEND" && EXTENSIONS= "$PY" -m pytest -q -n "$PYTEST_WORKERS"; }
step "ruff check" ruff_check
step "ruff format" ruff_format
step "mypy strict" mypy_strict
step "pytest (EXTENSIONS 빈 값)" pytest_all

# ── 2. MCP · 파이프라인 ─────────────────────────────────────────────────
#
# 환경을 갈라 본다 — `mcp` 를 백엔드 환경에 깔면 시험이 쓰는 HTTP 스택이 바뀐다.
say "2. MCP · 파이프라인"
if [ -x "$MCP_PY" ]; then
  mcp_tests() { cd "$REPO" && "$MCP_PY" -m pytest mcp_server/tests pipeline/tests -q; }
  step "pytest (mcp_server · pipeline)" mcp_tests
else
  printf '  – 건너뜀 (%s 없음 — mcp_server 에서 python3 -m venv venv 로 만든다)\n' "$MCP_PY"
  SKIPPED+=("mcp_server · pipeline 시험")
fi

# ── 3. 마이그레이션 정합 ────────────────────────────────────────────────
#
# 모델과 마이그레이션이 어긋나면 **배포 뒤 마이그레이션에서만** 터진다. 임시 DB 를
# 만들어 확인하고 지운다 — 개발 DB 에 올리면 그 DB 가 시험의 부산물을 들게 된다.
say "3. 마이그레이션 (alembic upgrade · check)"
alembic_check() {
  cd "$BACKEND"
  local parts base probe
  parts="$("$PY" - <<'PY'
from app.config import Settings

url = Settings().database_url
base, _, name = url.rpartition("/")
print(base, name, sep="|")
PY
)"
  base="${parts%%|*}"
  probe="${parts##*|}_precheck"
  local host port user password
  read -r host port user password <<<"$("$PY" - <<'PY'
import re

from app.config import Settings

m = re.search(r"://([^:]+):([^@]+)@([^:/]+):?(\d*)/", Settings().database_url)
print(m.group(3), m.group(4) or "5432", m.group(1), m.group(2))
PY
)"
  export PGPASSWORD="$password"
  local drop="DROP DATABASE IF EXISTS $probe"
  # **pgvector 를 든 틀에서 만든다**(ADR 0050) — 확장은 postgres 계정만 만들어, 앱 계정이 만든 빈 DB
  # 에는 없다(그것을 쓰는 마이그레이션이 거기서 멈춘다). 틀은 `sudo ./scripts/dev_pgvector.sh` 가 만든다.
  local template="${parts##*|}_template" from=""
  if [[ "$(psql -h "$host" -p "$port" -U "$user" -d postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname = '$template'")" == "1" ]]; then
    from=" TEMPLATE $template"
  fi
  psql -h "$host" -p "$port" -U "$user" -d postgres -tAc "$drop" >/dev/null
  psql -h "$host" -p "$port" -U "$user" -d postgres -tAc "CREATE DATABASE $probe$from" >/dev/null
  # **성공이든 실패든 지운다.** `trap ... RETURN` 은 함수 밖에서 펼쳐져 지역 변수를
  # 못 보고, 그때 `set -u` 가 스크립트를 죽인다 — 실측으로 그렇게 멈췄다.
  local status=0
  DATABASE_URL="$base/$probe" "$PY" -m alembic upgrade head || status=1
  DATABASE_URL="$base/$probe" "$PY" -m alembic check || status=1
  psql -h "$host" -p "$port" -U "$user" -d postgres -tAc "$drop" >/dev/null || true
  return "$status"
}
step "alembic upgrade + check" alembic_check

# ── 4. 프론트 ───────────────────────────────────────────────────────────
say "4. 프론트 (tsc · vitest · oxlint · build)"
# **생성 타입(`schema.d.ts`) 없이 본다** — 커밋하지 않는 생성물이라 CI 의 빌드 단계에는 아직 없다(생성은
# 그 뒤 최신성 검사에서). 이 PC 에 남은 파일로 타입을 맞추면 그것을 가져다 쓴 코드가 여기선 통과하고
# CI 에서 떨어진다(v0.4.50, 2026-10-09). 지워도 5번이 다시 만든다.
tsc_build() { cd "$FRONT" && rm -f src/shared/api/schema.d.ts && npx tsc -b --noEmit; }
vitest_all() { cd "$FRONT" && npx vitest run --silent; }
oxlint_all() { cd "$FRONT" && npx oxlint src; }
vite_build() { cd "$FRONT" && npm run build; }
step "tsc" tsc_build
step "vitest" vitest_all
step "oxlint" oxlint_all
step "build" vite_build

# ── 5. 생성물 최신성 — **맨 끝이어야 한다** ─────────────────────────────
#
# 코드나 독스트링을 고친 뒤에 다시 뽑아야 한다. 먼저 뽑고 나중에 고치면 커밋된
# 명세가 한 줄 뒤처지고, 그 한 줄 때문에 CI 를 한 번 더 돈다(실측).
say "5. 생성물 (openapi.json · api 타입)"
regen_openapi() { cd "$BACKEND" && "$PY" scripts/export_openapi.py; }
regen_types() { cd "$FRONT" && npm run api:types; }
openapi_clean() {
  if ! git -C "$REPO" diff --quiet -- backend/openapi.json; then
    echo "openapi.json 이 코드와 다릅니다 — 방금 재생성했으니 이 변경을 커밋에 포함하세요."
    git -C "$REPO" diff --stat -- backend/openapi.json
    return 1
  fi
}
step "openapi.json 재생성" regen_openapi
step "api 타입 재생성" regen_types
step "openapi.json 이 최신" openapi_clean

# ── 마무리 ──────────────────────────────────────────────────────────────
say "결과"
for one in "${SKIPPED[@]:-}"; do
  [ -n "$one" ] && printf '  – 건너뜀: %s\n' "$one"
done
if [ ${#FAILED[@]} -eq 0 ]; then
  printf '  \033[32m전부 통과 — 밀어도 된다.\033[0m\n\n'
  exit 0
fi
printf '  \033[31m%d 개 실패\033[0m\n' "${#FAILED[@]}"
for one in "${FAILED[@]}"; do
  printf '\n\033[1m── %s ──\033[0m\n' "${one%%|*}"
  tail -n 25 "${one##*|}"
done
printf '\n전체 기록: %s\n' "$LOGS"
exit 1
