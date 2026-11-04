"""첫 설치 — 뿌리 부서 하나와 시스템 관리자 계정 하나.

**이것 없이는 아무도 로그인할 수 없다.** 가입은 승인이 필요하고 승인할 사람이
없기 때문이다. 그래서 설치 스크립트가 반드시 한 번 돈다.

**멱등하다.** 두 번 돌려도 이미 있는 관리자의 비밀번호를 되돌리지 않고, 이미 있는
부서의 이름도 덮지 않는다 — 설치 스크립트를 다시 돌리는 일은 흔하다.

    python scripts/seed_install.py --email admin --name 관리자

비밀번호를 안 주면 난수로 만들어 **화면에 한 번만** 찍는다. 그 계정은
must_change_password 로 만들어지므로 첫 로그인에서 반드시 바뀐다.

## 도메인이 심을 것이 생기면

기준정보 축·코드표처럼 **행이 없으면 화면이 성립하지 않는 것**은 도메인 모듈에
`ensure_reference_data(db)` 같은 함수를 두고 여기서 부른다. 마이그레이션에 넣지
않는다 — 모델로 표를 만드는 시험이 그 행을 못 받아서, 같은 목록을 시험 쪽에 한 벌
더 적게 되고 두 벌은 반드시 갈린다.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

import app.all_models  # noqa: F401  (DB 를 만지는 스크립트는 반드시 이것을 읽는다)
from app.config import get_settings
from app.database import SessionLocal
from app.modules.accounts.models import User
from app.modules.auth import security
from app.modules.workspaces.models import Workspace, WorkspaceMember


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{get_settings().app_name} 첫 설치 시드")
    parser.add_argument("--email", default="admin")
    parser.add_argument("--name", default="시스템 관리자")
    parser.add_argument("--password", default=None)
    parser.add_argument("--workspace-slug", default="hq")
    parser.add_argument("--workspace-name", default="본사")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        workspace = db.scalar(select(Workspace).where(Workspace.slug == args.workspace_slug))
        if workspace is None:
            workspace = Workspace(slug=args.workspace_slug, name=args.workspace_name)
            db.add(workspace)
            db.flush()
            print(f"부서 생성: {workspace.slug} ({workspace.name})")
        else:
            print(f"부서가 이미 있습니다: {workspace.slug}")

        email = args.email.strip().lower()
        user = db.scalar(select(User).where(User.email == email))
        if user is not None:
            # **이미 있으면 비밀번호를 덮어쓰지 않는다.** 설치 스크립트를 두 번
            # 돌리는 일은 흔하고, 그때 관리자 비밀번호가 조용히 바뀌면 아무도
            # 못 들어간다. 권한만 확인해 준다.
            if not user.is_system_admin:
                user.is_system_admin = True
                print(f"기존 계정에 시스템 관리자 권한 부여: {email}")
            db.commit()
            print(f"이미 있는 계정입니다: {email} (비밀번호는 그대로)")
            return 0

        password = args.password or secrets.token_urlsafe(9)
        user = User(
            email=email,
            password_hash=security.hash_password(password),
            display_name=args.name,
            status="active",
            is_system_admin=True,
            home_workspace_id=workspace.id,
            # 난수 비밀번호를 콘솔에서 받아 적는 방식이라, 첫 로그인에서 반드시
            # 바꾸게 한다 — 안 그러면 그 값이 그대로 남는다.
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="manager"))
        db.commit()

        print(f"관리자 계정 생성: {email}")
        print(f"임시 비밀번호: {password}")
        print("첫 로그인에서 비밀번호를 바꿔야 합니다.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
