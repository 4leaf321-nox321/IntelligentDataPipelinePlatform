"""본보기 확장 — **새 확장을 만들 때 이 폴더를 복사한다.**

하는 일은 하나, `GET /api/ext/sample/ping`. 켠 인스턴스에서만 있고, 안 켠 인스턴스에서는 404
다.
시험이 그 둘을 확인한다(`tests/api/test_extensions.py`). 실제 확장은 여기에 자기 모듈의
`routes.py` · `services.py` · `models.py` 를 두고, 공통 화면의 훅(`shared/extensions`)과 PAT
범위
(`shared/scopes`)를 `register` 에서 연다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.accounts.models import User
from app.shared.auth import current_user

#: 이 확장이 하는 일 한 줄 — 플랫폼 자기소개(MCP 안내문)와 서버 화면이 이름 곁에 싣는다.
#: **새 확장도 반드시 적는다**(구조 시험이 본다).
DESCRIPTION = "본보기 확장 — 새 확장을 만들 때 복사하는 틀(응답 확인 하나)"

router = APIRouter(prefix="/ext/sample", tags=["ext:sample"])


@router.get("/ping")
def ping(user: User = Depends(current_user)) -> dict[str, str]:
    return {"extension": "sample", "user": user.email}


def register(api: APIRouter) -> None:
    """코어가 부르는 유일한 자리 — 라우터 · 훅 · 범위를 여기서 연다."""
    api.include_router(router)
