"""확장 모듈을 켜고 끄는 일 — **화면이 정본, `.env` 가 기본값.**

번들 하나로 여러 플랫폼을 띄우므로 「이 설치에 무슨 기능이 있나」 는 설치가 정한다.
그것을 서버 파일로만 정하면 운영자가 SSH · 배포 없이는 아무것도 못 조립하고, 켜고 끈
사실이 시스템 안에 안 남는다. 코어 공개(`ObjectType.core`)를 이미 같은 무늬로 두었다 —
DB 플래그 · 시스템 관리자 화면 · 감사 기록.

**고를 수 있는 것은 코드에 있는 확장뿐이다.** 목록은 `main.py` 가 기동에서 넣어 주고
(`register_available`), 화면은 그 목록에서만 고른다 — `.env` 오타처럼 「없는 이름」 이
켜지는 일이 없다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.modules.accounts.models import User
from app.modules.server.models import ExtensionState, PlatformProfile
from app.shared import audit, extensions
from app.shared.errors import NotFound, code

#: 이 번들에 들어 있는 확장 이름 — `main.py` 가 기동에서 한 번 넣는다.
#: **모듈은 `app.extensions` 를 import 하지 않는다**(구조 시험). 붙이는 자리가 하나여야
#: "이 확장이 왜 안 뜨지" 를 물을 자리가 생긴다.
_AVAILABLE: tuple[str, ...] = ()


def register_available(names: tuple[str, ...]) -> None:
    global _AVAILABLE
    _AVAILABLE = names


def available() -> tuple[str, ...]:
    return _AVAILABLE


#: 확장이 하는 일 한 줄 — `main.py` 가 확장을 실을 때 넣는다(모듈은 확장을 import 하지 않는다).
_DESCRIPTIONS: dict[str, str] = {}


def register_description(name: str, text: str) -> None:
    _DESCRIPTIONS[name] = " ".join(str(text or "").split())


def description(name: str) -> str:
    return _DESCRIPTIONS.get(name, "")


def _rows(db: Session) -> dict[str, ExtensionState]:
    return {one.name: one for one in db.scalars(select(ExtensionState))}


def _default_on(name: str) -> bool:
    """행이 없을 때의 답 — `.env` 의 `EXTENSIONS`."""
    return name in get_settings().extension_names


#: 확장 경로의 뿌리 — `/api/ext/<이름>/…`.
EXTENSION_ROOT = "/api/ext/"


def _fields(schema: dict[str, Any], components: dict[str, Any]) -> list[str]:
    """본문 칸 이름들 — 필수는 뒤에 `*`. **한 단만 펼친다.**

    스키마를 전부 펼쳐 내려 주면 목록이 본문보다 길어지고, 도구를 부르는 쪽은 그것을 다
    읽지 않는다. 이름과 필수 여부까지가 「무엇을 보내야 하나」 에 답하고, 나머지는 서버가
    거절하며 말해 준다(오류 문구에 무엇을 고칠지 적혀 있다).
    """
    ref = schema.get("$ref")
    if isinstance(ref, str):
        schema = components.get(ref.rsplit("/", 1)[-1], {})
    if schema.get("type") == "array":
        return ["[목록]"]
    required = set(schema.get("required") or [])
    return [
        f"{name}*" if name in required else name for name in (schema.get("properties") or {})
    ]


def extension_api(schema: dict[str, Any], names: Sequence[str]) -> list[dict[str, Any]]:
    """켠 확장의 엔드포인트 목록 — **OpenAPI 에서 깎아 낸다.**

    확장마다 도구를 만들지 않는 이유와 같다: 도구 목록이 길어질수록 그것을 읽는 쪽은
    엉뚱한 것을 고른다. 부를 수 있는 것이 무엇인지는 **목록 하나**가 말하고, 부르는 일은
    한 도구가 한다(MCP 의 `extensions_schema` · `extension_call`).

    **꺼진 확장은 내지 않는다.** 번들에는 다 들어 있고 문이 404 로 답하는데, 목록에 보이면
    그것을 부르는 쪽이 「있는데 안 된다」 로 읽는다.
    """
    components = (schema.get("components") or {}).get("schemas") or {}
    out: list[dict[str, Any]] = []
    for name in names:
        root = f"{EXTENSION_ROOT}{name}/"
        endpoints: list[dict[str, Any]] = []
        for path, methods in (schema.get("paths") or {}).items():
            if not path.startswith(root):
                continue
            for method, one in methods.items():
                if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                    continue
                # 독스트링 **첫 줄**이 그 자리의 한 줄 설명이다(summary 는 함수 이름이다).
                told = str(one.get("description") or "").strip().split("\n")[0]
                body = one.get("requestBody") or {}
                json_body = ((body.get("content") or {}).get("application/json") or {}).get(
                    "schema"
                ) or {}
                endpoints.append(
                    {
                        "method": method.upper(),
                        # 확장 뿌리부터 적는다 — 부르는 쪽이 이름을 두 번 적지 않게.
                        "path": path[len(root) :],
                        "summary": told or str(one.get("summary") or ""),
                        "query": [
                            f"{each['name']}*" if each.get("required") else str(each["name"])
                            for each in (one.get("parameters") or [])
                            if each.get("in") == "query"
                        ],
                        "body": _fields(json_body, components) if json_body else [],
                    }
                )
        endpoints.sort(key=lambda one: (one["path"], one["method"]))
        out.append({"name": name, "endpoints": endpoints})
    return out


def enabled_names(db: Session) -> tuple[str, ...]:
    """지금 켜져 있는 확장. 순서는 **이름 순**(번들에 들어 있는 순서)이다.

    `.env` 의 적은 순서를 쓰지 않는다 — 화면에서 켜고 끄기 시작하면 그 순서는
    아무 데도 없고, 사이드바가 오늘과 내일 다르게 보인다.
    """
    rows = _rows(db)
    return tuple(
        name
        for name in available()
        if (rows[name].enabled if name in rows else _default_on(name))
    )


def unknown_defaults() -> tuple[str, ...]:
    """`.env` 에 적혔는데 **이 번들에 없는** 이름.

    예전에는 이것이 기동을 막았다. 켜짐이 화면으로 옮겨 온 뒤로는 막지 않는다 —
    운영 재시작 중이라면 오타 하나가 서비스를 안 뜨게 하기 때문이다. 대신 **서버
    화면이 말한다**. 아무 데도 안 적으면 관리자는 「켰는데 메뉴가 없다」 로 만난다.
    """
    return tuple(one for one in get_settings().extension_names if one not in available())


def states(db: Session) -> list[tuple[str, bool, bool, datetime | None]]:
    """화면이 그리는 표 — `(이름, 켜짐, 화면에서_지정했나, 바뀐_시각)`."""
    rows = _rows(db)
    out: list[tuple[str, bool, bool, datetime | None]] = []
    for name in available():
        row = rows.get(name)
        if row is None:
            out.append((name, _default_on(name), False, None))
        else:
            out.append((name, row.enabled, True, row.updated_at))
    return out


def set_enabled(db: Session, user: User, name: str, on: bool) -> tuple[str, bool]:
    """켜고 끈다. **없는 이름은 거절한다** — 목록 밖의 값은 오타뿐이다."""
    if name not in available():
        raise NotFound(
            code("SERVER", 1),
            f"이 설치에 없는 확장입니다: {name}",
            details={"available": list(available())},
        )
    row = db.get(ExtensionState, name)
    was = row.enabled if row is not None else _default_on(name)
    if row is None:
        row = ExtensionState(name=name, enabled=on)
        db.add(row)
    else:
        row.enabled = on
    if was != on:
        # **켜고 끈 일은 남긴다.** 바깥으로 기능이 나타나고 사라지는 스위치에서
        # 「언제 누가」 를 물을 자리가 없으면, 켜 둔 것을 잊는다 — 코어 공개에서
        # 이미 같은 구멍을 메웠다(`ontology.type.core`).
        audit.record(
            db,
            action="extension.toggle",
            actor=user,
            target_table="extension_states",
            target_id=None,
            target_label=name,
            changes={"enabled": on, "was": was},
        )
    db.commit()
    return name, on


def require_extension(name: str) -> Callable[..., None]:
    """확장 라우터에 씌우는 문. **꺼져 있으면 없는 엔드포인트다.**

    라우터는 기동에서 전부 붙는다(런타임에 뗄 수 없다) — 그래서 끈 확장은 코드가
    프로세스에 남아 있고, 문이 404 로 답한다. 「끔」 은 감추는 것이고 자료는 남는다.
    """

    def guard(request: Request, db: Session = Depends(get_db)) -> None:
        if name not in enabled_names(db):
            raise NotFound(
                code("COMMON", 404),
                "존재하지 않는 엔드포인트입니다.",
                details={"path": request.url.path},
            )

    return guard


def live_facts(db: Session) -> list[extensions.ProfileFact]:
    """자동 요약 — 도메인이 등록한 갈래에 이 설치의 확장을 더한다. **읽을 때마다 센다.**"""
    out = extensions.profile_facts(db)
    names = list(enabled_names(db))
    if names:
        # **이름만으로는 무엇을 하는 곳인지 모른다** — 하는 일 한 줄을 곁에 싣는다. 켜고 끄면
        # 이 플랫폼에서 할 수 있는 일이 달라지므로 낡음 표시로 친다(드물어서 소란스럽지 않다).
        out.append(
            extensions.ProfileFact(
                key="extensions",
                label="확장",
                lines=[
                    " · ".join(
                        f"{name}({description(name)})" if description(name) else name
                        for name in names
                    )
                ],
                marks={f"extension:{name}": f"확장 「{name}」" for name in names},
            )
        )
    return out


def marks_of(facts: list[extensions.ProfileFact]) -> dict[str, str]:
    out: dict[str, str] = {}
    for one in facts:
        out.update(one.marks)
    return out


def stale_reasons(
    row: PlatformProfile | None, facts: list[extensions.ProfileFact]
) -> list[str]:
    """사람이 쓴 뒤 **달라진 것** — 생긴 타입 · 데이터 소스, 없어진 것, 정본이 바뀐 것. 소개가
    아직 없으면 그것이 한 줄이다."""
    if row is None or not row.summary.strip():
        return ["자기소개를 아직 안 적었다"]
    now = marks_of(facts)
    seen = dict(row.facts_seen or {})
    born = [f"생김: {now[key]}" for key in sorted(now) if key not in seen]
    gone = [f"없어짐: {seen[key]}" for key in sorted(seen) if key not in now]
    return born + gone


def maintenance(db: Session, viewer: User) -> list[extensions.MaintenanceItem]:
    """자기소개가 비었거나 낡았으면 시스템 관리자의 홈에 — 에이전트가 이 글로 어느 플랫폼에
    물을지 고르는데, 낡은 채 두면 새로 들어온 자료를 「여기 없다」 고 읽는다."""
    if not viewer.is_system_admin:
        return []
    reasons = stale_reasons(profile(db), live_facts(db))
    if not reasons:
        return []
    empty = reasons == ["자기소개를 아직 안 적었다"]
    return [
        extensions.MaintenanceItem(
            key="platform_profile",
            label=(
                "플랫폼 자기소개가 비어 있음 — 에이전트가 어느 플랫폼에 물을지 가르는 글"
                if empty
                else "플랫폼 자기소개가 그 뒤 바뀐 자료를 반영하지 않음 — "
                + " · ".join(reasons[:3])
                + (f" 외 {len(reasons) - 3}" if len(reasons) > 3 else "")
            ),
            count=len(reasons),
            link="/admin/server",
        )
    ]


def profile(db: Session) -> PlatformProfile | None:
    """이 플랫폼의 자기소개 — 한 행. 아직 아무도 안 적었으면 없다."""
    return db.get(PlatformProfile, 1)


def set_profile(db: Session, user: User, *, summary: str, notes: str) -> PlatformProfile:
    """자기소개를 고친다 — **시스템 관리자만**(부르는 쪽이 막는다). 바뀌었으면 감사에 전 ·
    후를 남긴다: 에이전트가 이 글로 플랫폼을 고르므로, 누가 언제 무엇으로 바꿨나가 답이 갈린
    까닭이 된다.
    """
    row = db.get(PlatformProfile, 1)
    before = (
        {"summary": row.summary, "notes": row.notes} if row else {"summary": "", "notes": ""}
    )
    after = {"summary": summary.strip(), "notes": notes.strip()}
    if row is None:
        row = PlatformProfile(id=1, **after)
        db.add(row)
    else:
        row.summary, row.notes = after["summary"], after["notes"]
    # **쓴 때 무엇이 담겨 있었나** — 이것과 지금이 달라지면 낡았다고 알린다. 글을 안 바꾸고
    # 저장만 해도 「지금 것을 봤다」 는 확인이 된다.
    row.facts_seen = marks_of(live_facts(db))
    row.updated_by_id = user.id
    if before != after:
        audit.record(
            db,
            action="server.profile",
            actor=user,
            target_table="platform_profile",
            target_id=None,
            target_label=get_settings().app_name,
            changes={"before": before, "after": after},
        )
    db.commit()
    db.refresh(row)
    return row
