#!/usr/bin/env bash
# 백업에서 되돌리고 — **되돌아왔는지 확인한다.**
#
#   ./restore.sh -b ~/backup/<slug> -d <slug>_restore_check          확인용 (기본)
#   ./restore.sh -b ~/backup/<slug> -d <slug> -i ~/apps/<slug> -f    실제 복구
#   ./restore.sh -b <백업> -d <slug> -F /data/<공통>/<slug>/filestore -f   첨부가 공용 스토리지에 있을 때
#
# `backup.sh` 의 마지막 줄은 "한 번은 실제로 복구해 보세요" 라고 말한다. 그런데 그
# 「복구」 가 사람이 손으로 치는 명령 두 줄이면, 치다 틀렸을 때 **절반만 돌아온
# 상태**가 되고 그것을 알아챌 자리가 없다.
#
# ## 이 스크립트가 하는 일의 절반은 검사다
#
# 되돌리는 것 자체는 pg_restore 와 파일 복사다. **어려운 것은 둘의 시점이 맞는지**다.
# DB 에는 첨부의 경로와 해시가 있고 filestore 에는 그 내용이 있다 — 한쪽만 되돌리면
# 「DB 에는 있는데 파일이 없는」 행이 생기고 **그 상태로 앱이 뜬다.** 화면은 멀쩡하고
# 그 첨부를 열 때만 터진다.
#
# 그래서 복구가 끝나면 **DB 가 가리키는 파일이 실제로 있는지 세어 본다.**
#
# ## 기본은 확인용이다
#
# 다른 이름의 DB 로 풀어 "이 백업이 살아 있는가" 를 본다. 운영을 덮어쓰려면 `-f` 를
# 의식적으로 준다. **첨부는 `-i` 를 줄 때만 되돌린다** — DB 만 확인하는데 운영
# 파일을 건드리면 그 자체가 사고다.

set -euo pipefail

err()  { echo "오류: $*" >&2; exit 1; }
info() { echo "==> $*"; }

BACKUP_ROOT=""; DB_NAME=""; INSTALL_DIR=""; FILESTORE=""; DUMP_FILE=""; FORCE=0

while getopts ":b:d:i:F:D:fh" opt; do
    case "$opt" in
        b) BACKUP_ROOT="$OPTARG" ;;
        d) DB_NAME="$OPTARG" ;;
        i) INSTALL_DIR="$OPTARG" ;;
        F) FILESTORE="$OPTARG" ;;
        D) DUMP_FILE="$OPTARG" ;;
        f) FORCE=1 ;;
        h) sed -n '2,30p' "$0"; exit 0 ;;
        *) err "쓰지 않는 옵션입니다. -h 로 사용법을 보세요." ;;
    esac
done

[[ -n "$BACKUP_ROOT" ]] || err "-b <백업 폴더> 가 필요합니다."
# 첨부를 되돌릴 자리 — -F 로 직접, 또는 -i 의 설치 폴더 아래.
[[ -n "$FILESTORE" || -z "$INSTALL_DIR" ]] || FILESTORE="$INSTALL_DIR/filestore"
[[ -n "$DB_NAME" ]]     || err "-d <복구할 DB 이름> 이 필요합니다."

# 접속 정보는 **백업이 함께 받아 둔 .env** 에서 읽는다. 앱이 살아 있지 않아도
# 복구할 수 있어야 한다 — 서버가 통째로 날아간 상황이 바로 복구가 필요한 상황이다.
ENV_FILE="$BACKUP_ROOT/env/.env"
[[ -f "$ENV_FILE" ]] || err "백업에 .env 가 없습니다: $ENV_FILE"
DSN="$(sed -n 's|^DATABASE_URL=||p' "$ENV_FILE" | tail -n1)"
[[ "$DSN" =~ ://([^:]+):([^@]*)@([^:/]+):([0-9]+)/(.+)$ ]] || err "DATABASE_URL 을 해석하지 못했습니다."
DB_USER="${BASH_REMATCH[1]}"; DB_PW="${BASH_REMATCH[2]}"
DB_HOST="${BASH_REMATCH[3]}"; DB_PORT="${BASH_REMATCH[4]}"; SOURCE_DB="${BASH_REMATCH[5]}"

if [[ "$DB_NAME" == "$SOURCE_DB" && $FORCE -ne 1 ]]; then
    err "'$DB_NAME' 은 운영 데이터베이스입니다.
확인만 하려면 다른 이름을 주세요:  -d ${SOURCE_DB}_restore_check
정말 운영을 덮어쓰려면 -f 를 주세요. **되돌릴 수 없습니다.**"
fi

# ── 덤프 고르기 ───────────────────────────────────────────────────────────────
if [[ -z "$DUMP_FILE" ]]; then
    DUMP_FILE="$(find "$BACKUP_ROOT/db" -maxdepth 1 -name 'db-*.dump' | sort -r | head -n1)"
    [[ -n "$DUMP_FILE" ]] || err "백업에 덤프가 없습니다: $BACKUP_ROOT/db"
fi
[[ -f "$DUMP_FILE" ]] || err "덤프를 찾을 수 없습니다: $DUMP_FILE"
info "덤프: $DUMP_FILE ($(du -m "$DUMP_FILE" | cut -f1)MB)"

export PGPASSWORD="$DB_PW"
psql_() { psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -tAc "$1" "${2:-postgres}"; }

# ── 대상 DB 준비 ──────────────────────────────────────────────────────────────
# **비어 있는 DB 로 복구한다.** 기존 표 위에 덮으면 덤프에 없는 옛 행이 남아,
# 복구했는데도 데이터가 섞인 상태가 된다.
if [[ "$(psql_ "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")" == "1" ]]; then
    [[ $FORCE -eq 1 ]] || err "'$DB_NAME' 이 이미 있습니다. 지우고 다시 만들려면 -f 를 주세요."
    info "기존 $DB_NAME 삭제"
    psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -c "DROP DATABASE \"$DB_NAME\"" postgres >/dev/null
fi
psql -U "$DB_USER" -h "$DB_HOST" -p "$DB_PORT" -c "CREATE DATABASE \"$DB_NAME\" ENCODING 'UTF8'" postgres >/dev/null

# **pgvector 확장은 postgres 계정만 만든다**(ADR 0050). 덤프가 그것을 들고 있으면 앱 계정의 pg_restore 가
# 만들지 못해, 그것을 쓰는 표가 안 돌아온 채 「복구됐다」 고 끝난다. 그래서 먼저 만든다 — pg_restore 의
# 「이미 있다」 는 무해하다.
if pg_restore -l "$DUMP_FILE" 2>/dev/null | grep -qE 'EXTENSION - vector( |$)'; then
    VECTOR_SQL="CREATE SCHEMA IF NOT EXISTS extensions; GRANT USAGE ON SCHEMA extensions TO PUBLIC; CREATE EXTENSION IF NOT EXISTS vector SCHEMA extensions"
    if [[ $EUID -eq 0 ]] && sudo -u postgres psql -X -q -d "$DB_NAME" -c "$VECTOR_SQL" >/dev/null 2>&1; then
        info "pgvector 확장 준비 (extensions 스키마)"
    else
        err "덤프에 pgvector 확장이 있는데 만들지 못했습니다 — 확장은 postgres 계정만 만듭니다. DB 가 있는 서버에서 sudo 로 다시 돌리세요(postgresql-<판>-pgvector 패키지가 있어야 한다 — sudo ./deploy.sh pgvector 가 깐다). 만든 빈 DB '$DB_NAME' 는 남아 있으니 -f 를 붙여 다시."
    fi
fi

info "복구 중"
# pg_restore 는 경고를 내며 1 로 끝날 수 있다(소유권 등). 그것으로 전체를 실패로
# 보지 않는다 — 아래 표 개수와 파일 검사로 판정한다.
pg_restore --host "$DB_HOST" --port "$DB_PORT" --username "$DB_USER" \
    --dbname "$DB_NAME" --no-owner --no-privileges "$DUMP_FILE" || true

TABLES="$(psql_ "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" "$DB_NAME")"
info "복구된 표: $TABLES 개"
[[ "$TABLES" -gt 0 ]] || err "표가 하나도 복구되지 않았습니다. 덤프를 확인하세요."

# ── 첨부 ──────────────────────────────────────────────────────────────────────
CHECK_ROOT=""
if [[ -n "$FILESTORE" ]]; then
    if [[ -d "$BACKUP_ROOT/filestore" ]]; then
        info "첨부 복구: $BACKUP_ROOT/filestore -> $FILESTORE"
        mkdir -p "$FILESTORE"
        rsync -a --delete "$BACKUP_ROOT/filestore/" "$FILESTORE/"
    else
        echo "경고: 백업에 filestore 가 없습니다." >&2
    fi
    CHECK_ROOT="$FILESTORE"
elif [[ -d "$BACKUP_ROOT/filestore" ]]; then
    # -i 없이 리허설만 할 때도 백업 안의 것으로 견줘 볼 수 있다.
    CHECK_ROOT="$BACKUP_ROOT/filestore"
else
    info "첨부는 건드리지 않았습니다 (-i 를 주지 않음)."
fi

# ── DB 가 가리키는 파일이 실제로 있나 ─────────────────────────────────────────
#
# **이 검사가 이 스크립트의 절반이다.** 표 이름을 손으로 적지 않고 카탈로그에
# 물어본다 — 도메인이 자기 경로 컬럼을 더해도 여기를 안 고쳐도 되고, 컬럼 이름을
# 잘못 적어 검사가 조용히 아무것도 안 보는 일이 없다.
CHECKED=0; MISSING=0
if [[ -n "$CHECK_ROOT" ]]; then
    info "가리키는 파일이 있는지 확인"
    while IFS='.' read -r table column; do
        [[ -n "$table" && -n "$column" ]] || continue
        while read -r rel; do
            [[ -n "$rel" ]] || continue
            CHECKED=$((CHECKED + 1))
            [[ -f "$CHECK_ROOT/$rel" ]] || MISSING=$((MISSING + 1))
        done < <(psql_ "SELECT \"$column\" FROM \"$table\" WHERE \"$column\" IS NOT NULL" "$DB_NAME")
    done < <(psql_ "SELECT table_name || '.' || column_name
                    FROM information_schema.columns
                    WHERE table_schema='public'
                      AND column_name IN ('relative_path','storage_path','source_path')
                    ORDER BY 1" "$DB_NAME")
fi

echo
if [[ -z "$CHECK_ROOT" ]]; then
    echo "경고: 첨부를 안 봤습니다 — DB 만 되돌린 상태입니다." >&2
elif [[ $CHECKED -eq 0 ]]; then
    # **0개를 「전부 있다」 로 적으면 안 된다.** 가리키는 파일이 없는 것과 검사가
    # 아무것도 못 찾은 것은 다르고, 뒤엣것은 검사가 고장난 것이다.
    echo "경고: DB 가 가리키는 파일이 하나도 없습니다 — 첨부가 없는 설치이거나, 검사가 표를 못 찾았습니다." >&2
elif [[ $MISSING -gt 0 ]]; then
    err "DB 가 가리키는 파일 $MISSING 개가 없습니다 (확인한 것 $CHECKED 개).

**DB 와 첨부의 시점이 어긋났습니다.** 이 상태로 앱을 띄우면 화면은 멀쩡하고
그 첨부를 열 때만 터집니다. 같은 백업 폴더의 filestore/ 를 함께 되돌리세요."
else
    info "가리키는 파일 $CHECKED 개가 전부 있습니다."
fi

echo
info "복구 완료: $DB_NAME (표 $TABLES 개, 파일 $CHECKED 개 확인)"
if [[ "$DB_NAME" != "$SOURCE_DB" ]]; then
    echo
    echo "  확인용 DB 입니다. 다 봤으면 지우세요:"
    echo "    dropdb --host=$DB_HOST --port=$DB_PORT --username=$DB_USER $DB_NAME"
fi
