"""`system` 타입의 원 표 — **행을 복제하지 않고 투영한다.**

부서를 온톨로지에 넣겠다고 `objects` 에 행을 만들면 두 벌이 되고, 두 벌은 반드시
갈린다 — 부서 이름을 바꾼 날 「담당 부서」 칸은 옛 이름을 보여 주고, 그 차이는 아무
데도 안 뜬다. 그래서 `kind_class='system'` 인 타입은 행이 없다. 목록·상세·참조 풀이가
전부 **원 표(`workspaces`·`users`)** 에서 나오고, 그 끝단의 관계는 `object_links` 에
담긴다.

## 왜 레지스트리인가

`shared` 는 도메인 모델을 모른다(방향은 shared -> 모듈 한 쪽). 그래서 원 표를 아는
모듈이 자기 `SystemSource` 를 등록하고, `objects` 는 여기서 찾아 쓴다. 등록은
`app/main.py` 의 `_register_extensions()` 하나에서 — 조립 지점이 하나여야 「이게 왜
안 뜨지」 를 물을 자리가 생긴다.

## 승격의 끝이 여기다

JSONB 로 커진 타입을 전용 표로 내리면(설계 문서 4단계) 그 표도 여기에 등록한다.
그러면 온톨로지에서 그 타입은 **똑같이 보이고 똑같이 참조된다** — 표가 바뀐 것을
화면과 MCP 는 모른다. 절차는 `docs/sp/승격-경로.md`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.modules.accounts.models import User


@dataclass(frozen=True)
class SystemRef:
    """원 표의 행 하나를 온톨로지가 보는 모양."""

    id: uuid.UUID
    key: str
    """사람이 적는 식별자 — 부서면 slug, 계정이면 로그인 아이디. 파일로 넣을 때
    이것으로 적는다."""
    label: str
    hint: str = ""
    """같은 이름이 여럿일 때 구별해 주는 줄. 부서라면 경로."""
    active: bool = True
    """보관·정지된 것은 거짓. picker 에서 「안 쓰는 값」 으로 뜬다 — 이미 걸린 참조는
    그대로 남는다."""


#: (db, viewer, 검색어, limit, offset) -> (행들, 전체 수)
SearchFn = Callable[[Session, User, str | None, int, int], tuple[list[SystemRef], int]]
#: (db, id 들) -> {id: 행}. **지워진 것은 안 준다** — 없는 것을 가리키는 참조를 잡는 근거.
LookupFn = Callable[[Session, list[uuid.UUID]], dict[uuid.UUID, SystemRef]]
#: (db) -> 전부. 파일의 식별자·이름을 id 로 풀 때 한 번 읽는다.
ListAllFn = Callable[[Session], list[SystemRef]]


@dataclass(frozen=True)
class SystemSource:
    key: str
    """타입 정의의 `system_source` 가 가리키는 값(`workspace` · `user`)."""
    label: str
    search: SearchFn
    lookup: LookupFn
    list_all: ListAllFn


_sources: dict[str, SystemSource] = {}


def register_system_source(source: SystemSource) -> None:
    """같은 키를 두 번 등록해도 안전하다 — `create_app()` 은 시험에서 두 번 불린다."""
    _sources[source.key] = source


def system_source(key: str) -> SystemSource | None:
    return _sources.get(key)


def system_sources() -> list[SystemSource]:
    return sorted(_sources.values(), key=lambda one: one.key)


def known_source_keys() -> tuple[str, ...]:
    return tuple(sorted(_sources))
