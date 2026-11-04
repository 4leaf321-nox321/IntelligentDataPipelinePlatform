#!/usr/bin/env bash
# SP 시험 인스턴스에 관리자 · relay 토큰 · plm-core 정의를 세운다(up.sh 뒤에). 다시 돌려도 된다.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
mkdir -p "$SANDBOX_WORK"
if [[ ! -f "$SANDBOX_WORK/admin.json" ]]; then
    password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
    (cd "$SP_REPO/backend" && .venv/bin/python scripts/seed_install.py \
        --email admin@jvhubtest.local --name "relay 시험 관리자" --password "$password" | grep -v "비밀번호")
    printf '{"email": "admin@jvhubtest.local", "password": "%s"}' "$password" > "$SANDBOX_WORK/admin.json"
    chmod 600 "$SANDBOX_WORK/admin.json"
fi
export SANDBOX_WORK SANDBOX_URL SP_REPO
"$ROOT/backend/.venv/bin/python" "$(dirname "${BASH_SOURCE[0]}")/setup.py"
