"""확장 모듈 — **인스턴스마다 켜고 끄는 기능.**

번들 하나로 여러 플랫폼(허브 · 그룹 쌍둥이들)을 띄우는데, 어떤 기능은 한 인스턴스에만 있다.
그것을 여기 `app/extensions/<이름>/` 에 둔다.

**켜고 끄는 자리는 화면이다** — 시스템 관리자 › 서버 › 「확장 모듈」(`extension_states` 표,
`modules/server/services.py`). `.env` 의 `EXTENSIONS` 는 **아직 아무도 켜고 끈 적 없는 확장의
기본값**이다. 운영은 워커가 넷이고 A · B 두 대이므로 켜짐이 파일에 있으면 프로세스마다 ·
서버마다 답이 갈린다 — 표는 한 벌이다.

## 규칙 하나

**확장은 코어(`app.modules` · `app.shared`)를 부르되, 코어는 확장을 모른다.** 코어가 확장을
import 하는 순간 「끄면 안 뜨는 코어」 가 되고, 그 사실은 확장을 안 켠 인스턴스에서만 드러난다.
구조 시험이 이것을 지킨다(`tests/architecture/test_boundaries.py`).

## 확장의 모양

    app/extensions/<이름>/__init__.py   register(router: APIRouter) -> None  (필수)
    app/extensions/<이름>/models.py     ORM 모델 — all_models.py 에도 적는다
                                        (마이그레이션은 늘 전부 돈다)
    frontend/src/extensions/<이름>/     화면 쪽 짝 — 메뉴 · 페이지
                                        (frontend/src/extensions/index.ts)

`register` 는 라우터를 받아 자기 라우터를 붙이고, 필요하면 공통 화면의 훅
(`shared/extensions`)과 PAT 범위(`shared/scopes`)를 연다. **안 켠 인스턴스에서는 그 확장의
표가 비어 있을 뿐** 다른 것은 같다 — 마이그레이션을 확장마다 가르지 않는 이유다.

라우터는 번들에 든 확장 **전부**가 기동에서 붙고, 꺼진 것은 문이 404 로 답한다
(`require_extension`). 런타임에 라우터를 뗄 수 없어서다 — 그래서 「끔」 은 감추는 것이고,
자료와 표는 남는다. `register` 에서 연 PAT 범위도 같다 — `main.py` 가 그것을 그 확장의 것으로
적어 두고(`scopes.owned_by`), 꺼진 확장의 범위는 토큰 발급 목록 · 위임 토큰에서 빠진다. 이미
받은 토큰의 범위는 지우지 않는다(다시 켜면 그대로 쓴다).
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

from fastapi import APIRouter


class Extension(ModuleType):
    """`load()` 가 돌려주는 모듈의 모양 — 타입 검사용."""

    DESCRIPTION: str
    """하는 일 한 줄 — 플랫폼 자기소개 · 서버 화면이 이름 곁에 싣는다."""

    def register(self, router: APIRouter) -> None: ...


def available() -> tuple[str, ...]:
    """이 번들에 들어 있는 확장 이름 — **파일로 센다, import 하지 않는다.**

    켜짐은 DB 가 정하고(`modules/server/services.py`), 고를 수 있는 값은 이 목록이다.
    `.env` 에 적힌 이름만 보면 「화면에서 켤 수 있는 것」 을 알 방법이 없고, 그러면
    관리자는 없는 이름을 적어 보게 된다.
    """
    here = Path(__file__).resolve().parent
    return tuple(
        sorted(
            one.name
            for one in here.iterdir()
            if one.is_dir()
            and not one.name.startswith(("_", "."))
            and (one / "__init__.py").exists()
        )
    )


def load(name: str) -> Extension:
    """이름으로 확장을 찾는다. 없거나 `register` 가 없으면 **기동에서 멈춘다** — `.env` 의
    오타가 「메뉴가 안 보이는」 조용한 고장으로 남지 않게."""
    if not name.isidentifier() or name.startswith("_"):
        raise RuntimeError(f"EXTENSIONS 의 이름이 이상합니다: {name!r}")
    try:
        module = importlib.import_module(f"app.extensions.{name}")
    except ModuleNotFoundError as missing:
        if missing.name and missing.name.startswith(f"app.extensions.{name}"):
            raise RuntimeError(
                f"확장 {name!r} 이 없습니다 (app/extensions/{name}/). "
                ".env 의 EXTENSIONS 를 확인하세요."
            ) from missing
        raise
    if not callable(getattr(module, "register", None)):
        raise RuntimeError(f"확장 {name!r} 에 register(router) 가 없습니다.")
    return module  # type: ignore[return-value]
