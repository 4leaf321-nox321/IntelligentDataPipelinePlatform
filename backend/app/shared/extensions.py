"""도메인이 공통 화면에 자기를 끼우는 자리 — **여섯.**

공통 틀에는 도메인을 모르는 화면이 있고, 그것들은 도메인이 무엇인지 모른다.

    홈의 「남은 일」      -> maintenance     무엇이 아직 안 채워졌나
    서버 화면의 「쌓인 것」 -> stats           무엇이 얼마나 있나
    부서 삭제 확인       -> references      무엇이 이 부서를 가리키나
    부서 자료 옮기기     -> contents        이 부서가 가진 것을 어떻게 옮기나
    객체의 연도 추론     -> temporal_source  이 객체가 언제 쓰였나
    첨부 올리기 · 떼기   -> attachment_owner 그 자리가 있나 · 고칠 수 있나 · 무엇만 받나

## 왜 레지스트리인가

`shared` 가 도메인 모델을 import 하면 방향이 거꾸로 서고, 그때 import 순서 하나로
서버가 안 뜬다. 그래서 **도메인이 등록하고 공통이 부른다.** 등록은 각 모듈의
`__init__.py` 가 아니라 `app/main.py` 가 부르는 `register()` 하나에서 한다 —
조립 지점이 하나여야 "이게 왜 안 뜨지" 를 물을 자리가 생긴다.

## 세 화면이 지키는 규칙

- **0 건인 항목은 안 내보낸다**(maintenance). 다 0 인 목록을 매일 보면 사람은 그
  자리를 아예 안 읽게 되고, 그때 진짜 하나가 떠도 눈에 안 들어온다.
- **막는 참조는 반드시 표시한다**(references). 누르기 전에 아는 것이 이 틀의
  무늬다 — 지우고 나서 「장비 12대가 사라졌다」 를 알게 되면 되돌릴 방법이 없다.
- **0 건도 내보낸다**(contents). 여기는 「무엇을 옮길 수 있나」 라서, 0 이라는 답도
  화면에 필요하다 — 목록에서 빠지면 사람은 그 종류가 안 옮겨진 줄 안다.

## 참조와 자료는 다른 물음이다

`references` 는 **이 부서를 가리키는 것**(지우면 깨지는 것)이고, `contents` 는
**이 부서가 가진 것**(다른 부서로 넘길 수 있는 것)이다. 하나가 둘 다 하는 것이
간단해 보이지만, 「이 부서를 담당 부서로 적은 객체」 처럼 가리키기만 하고 옮길 수
없는 것이 있다 — 그 둘을 한 목록에 섞으면 옮기기 화면이 못 옮기는 줄을 보여 준다.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CursorResult, Result
from sqlalchemy.orm import Session

from app.modules.accounts.models import User


@dataclass(frozen=True)
class MaintenanceItem:
    """홈의 「남은 일」 한 줄."""

    key: str
    label: str
    count: int
    link: str | None = None
    severity: str = "info"
    """info · warning. 경고는 색이 붙는다 — **아무거나 경고로 두지 않는다.**
    전부 노란색이면 노란색은 아무 뜻도 못 갖는다."""


@dataclass(frozen=True)
class ProfileFact:
    """플랫폼 자기소개의 **자동 요약** 한 갈래(ADR 0019) — 읽을 때마다 센다, 저장하지 않는다.

    사람이 쓴 소개는 쓴 날에 멈춘다. 타입 · 데이터 소스가 늘어도 안 따라가고, 그 사실을
    아무도 모른다. 그래서 「무엇이 담겼나」 는 도메인이 그때그때 말하고, 사람이 쓸 때의
    표시(`marks`)를 남겨 두었다가 달라지면 「낡음」 으로 알린다."""

    key: str
    label: str
    """「기록」 · 「들어오는 곳」 처럼 — 에이전트 안내문에 그대로 선다."""
    lines: list[str]
    marks: dict[str, str]
    """낡음 판정의 표시 → 사람이 읽는 이름(`type:ra_report` → 「타입 「보고서」」). 이것이
    사람이 쓸 때와 달라지면 소개가 낡은 것이다. 건수처럼 늘 변하는 것은 넣지 않는다."""


@dataclass(frozen=True)
class StatItem:
    """서버 화면의 「쌓인 것」 한 칸."""

    label: str
    count: int


@dataclass(frozen=True)
class WorkspaceReference:
    """이 부서를 가리키는 참조 하나."""

    table: str
    label: str
    count: int
    blocks_delete: bool
    """지우려면 먼저 정리해야 하는가. **DB 가 RESTRICT 로 거부하는 것은 반드시
    True 다** — False 로 두면 화면은 지울 수 있다고 말하고 서버는 500 을 낸다."""


@dataclass(frozen=True)
class WorkspaceContent:
    """이 부서가 **가진** 것 한 종류. 부서 통폐합 때 다른 부서로 통째 넘긴다.

    `move` 는 (db, 원본 부서 id, 대상 부서 id) 를 받아 **옮긴 건수**를 돌려준다.
    커밋하지 않는다 — 여러 종류를 한 트랜잭션으로 옮겨야 하고, 중간에 실패하면
    절반만 옮겨진 상태가 남기 때문이다.
    """

    kind: str
    """화면과 요청이 쓰는 이름. `objects` 처럼 표 이름을 따른다."""
    label: str
    count: int
    move: Callable[[Session, uuid.UUID, uuid.UUID], int]
    move_by: Callable[[Session, uuid.UUID, uuid.UUID, User], int] | None = None
    """`move` 와 같되 **누가 옮기나**까지 받는다 — 있으면 통폐합이 이것을 부른다.

    옮긴 것마다 기록을 남기는 종류가 쓴다(객체 — 옮겨 간 객체를 바깥 수신 측에 「더 이상 안
    보임」 으로 알리는 근거가 객체마다의 소유 부서 기록이다, `coreapi/services._moved_away`).
    기록에는 사람이 박혀야 한다 — 「시스템」 으로 남기면 반년 뒤 누가 옮겼는지 그 객체의
    이력으로는 답할 수 없다."""
    move_steps: MoveSteps | None = None
    """`move_by` 와 같되 **덩어리로 나눠** 옮기며 덩어리마다 (옮긴 수, 전체) 를 알린다 — 수십만
    건이 될 수 있는 종류(객체)가 쓴다. 있으면 통폐합이 이것을 부른다.

    통폐합은 작업(`workspace_reassign`)이다 — 한 부서의 객체 20만 개를 옮기는 데 90초 남짓이라
    요청 안에서는 끊긴다(2026-10-10). 알림을 받는 쪽(작업의 진행률)이 **취소 · 빼앗김을 던질 수
    있다** — 커밋 전이라 그때는 아무것도 안 옮겨진다. 커밋하지 않는다(`move` 와 같다)."""
    check: Callable[[Session, uuid.UUID, uuid.UUID], list[str]] | None = None
    """옮기면 **막히는 것** — 한 줄에 하나, 없으면 빈 목록. 통폐합의 계획이 미리 보인다(객체:
    부서 안에서 하나여야 할 식별자가 대상 부서와 겹침). 적용(`move*`)도 같은 검사로 다시
    막는다 — 계획과 적용 사이에 누가 같은 값을 만들 수 있다."""


#: 옮기는 쪽이 덩어리마다 부르는 것 — (지금까지 옮긴 수, 전체).
MoveProgress = Callable[[int, int], None]
#: (db, 원본 부서 id, 대상 부서 id, 옮기는 사람, 진행률) → 옮긴 건수.
MoveSteps = Callable[[Session, uuid.UUID, uuid.UUID, User, MoveProgress], int]


def rows_changed(result: Result[Any]) -> int:
    """`UPDATE` 가 실제로 건드린 행 수. `move` 가 돌려줄 값이다.

    형이 맞지 않아 캐스팅한다 — `Session.execute` 의 선언 형은 `Result` 지만 DML 의
    실제 형은 `CursorResult` 다. 부르는 쪽마다 이 캐스팅을 적으면 언젠가 하나는
    `or 0` 을 빠뜨리고, 그 자리는 옮긴 게 없을 때 None 을 화면까지 올려 보낸다.
    """
    return int(cast("CursorResult[Any]", result).rowcount or 0)


#: 홈이 부르는 것들. 보는 사람에 따라 다른 답을 낼 수 있어 User 를 받는다
#: (가입 승인 대기는 시스템 관리자에게만 뜬다).
MaintenanceProvider = Callable[[Session, User], list[MaintenanceItem]]

#: 서버 화면이 부르는 것들.
StatProvider = Callable[[Session], list[StatItem]]

#: 부서 삭제 확인이 부르는 것들.
ReferenceProvider = Callable[[Session, uuid.UUID], list[WorkspaceReference]]

#: 부서 자료 옮기기 화면이 부르는 것들.
ContentProvider = Callable[[Session, uuid.UUID], list["WorkspaceContent"]]

#: 객체 id 목록 -> 그 객체가 **쓰인 연도들.** 도메인이 자기 기록에서 답한다.
TemporalProvider = Callable[[Session, list[uuid.UUID]], dict[uuid.UUID, set[int]]]


@dataclass(frozen=True)
class AttachmentOwner:
    """첨부를 붙일 자리 — 도메인이 답한다. 공통 틀(files)은 도메인 표를 모른다.

    답하는 쪽은 **그 자리가 없거나 고칠 수 없으면 스스로 던진다**(NotFound · Forbidden ·
    InvalidValue). 돌려주는 것은 붙여도 될 때의 조건이다.
    """

    workspace_id: uuid.UUID | None
    """첨부의 소유 부서 — **그 자료의 소유 부서를 따른다.** 올린 쪽이 부서를 고르게 두면
    첨부와 자료가 서로 다른 부서에 서고, 자료를 볼 수 있는 사람이 첨부를 못 본다."""
    accept: str | None = None
    """`image` 면 서버가 이미지로 읽은 것만 붙는다(ADR 0012)."""
    label: str = ""
    """감사에 남길 자료 이름."""
    changed: Callable[[Session, User, str | None, str, bool], None] | None = None
    """붙였다(True) · 뗐다(False) — (db, 사람, 자리, 파일 이름, 붙였나). 도메인이 자기 이력에
    남긴다. **커밋하지 않는다** — 첨부 행과 같은 트랜잭션이다."""


#: (db, 사람, 자료 id, 자리) -> 붙일 조건. 자료 표마다 하나. 뗄 때는 자리를 None 으로
#: 묻는다 — 칸이 지워졌거나 종류가 바뀐 뒤에도 이미 붙은 것은 뗄 수 있어야 한다.
AttachmentOwnerProvider = Callable[[Session, User, uuid.UUID, str | None], AttachmentOwner]

_maintenance: list[MaintenanceProvider] = []
_stats: list[StatProvider] = []
_references: list[ReferenceProvider] = []
_contents: list[ContentProvider] = []
_temporal: list[TemporalProvider] = []
_attachment_owners: dict[str, AttachmentOwnerProvider] = {}
_profile: list[Callable[[Session], list[ProfileFact]]] = []


# 셋 다 **같은 것을 두 번 넣어도 안전하다.** `create_app()` 은 시험에서 두 번
# 불릴 수 있고, 그때 목록이 늘면 홈이 같은 줄을 두 번 보여 준다 — 그 화면을 보는
# 사람은 그것을 데이터가 두 배라고 읽는다.


def register_maintenance(provider: MaintenanceProvider) -> None:
    if provider not in _maintenance:
        _maintenance.append(provider)


def register_stats(provider: StatProvider) -> None:
    if provider not in _stats:
        _stats.append(provider)


def register_profile_facts(provider: Callable[[Session], list[ProfileFact]]) -> None:
    """플랫폼 자기소개의 자동 요약에 한 갈래를 더한다(ADR 0019)."""
    if provider not in _profile:
        _profile.append(provider)


def count_text(n: int) -> str:
    """건수를 안내문의 말로 — 만을 넘으면 「1.2만」 · 「200만」(어림이면 된다)."""
    if n < 10_000:
        return f"{n:,}"
    value = n / 10_000
    return f"{value:.0f}만" if value >= 100 or value == int(value) else f"{value:.1f}만"


def profile_facts(db: Session) -> list[ProfileFact]:
    """**빈 갈래는 버린다** — 타입이 없는 설치의 「기록 — 」 같은 빈 줄은 에이전트에게
    소음이다."""
    out: list[ProfileFact] = []
    for provider in _profile:
        out.extend(one for one in provider(db) if one.lines)
    return out


def register_workspace_reference(provider: ReferenceProvider) -> None:
    if provider not in _references:
        _references.append(provider)


def register_workspace_content(provider: ContentProvider) -> None:
    """부서를 통폐합할 때 **이 표의 소유 부서를 바꿔 주는 길.**

    등록하지 않으면 그 표는 옮기기 화면에 안 뜨고, 사람은 부서를 비울 방법이 없어
    **삭제도 보관도 못 한다**(FK 가 RESTRICT 라 삭제가 막힌다).
    """
    if provider not in _contents:
        _contents.append(provider)


def register_temporal_source(provider: TemporalProvider) -> None:
    """`temporal_kind='derived'` 인 축의 **연도를 도메인이 답한다.**

    「그 객체가 쓰인 기록의 연도」 에서 추론하는 정책인데, **공통 틀에는 「기록」
    이 없다** — 그것은 도메인의 것이다(보고서·시험·거래).

    등록하지 않으면 그 축은 **연도 필터를 무시한다**(evergreen 처럼 군다).
    조용히 빈 목록을 주지 않는 이유는, 빈 목록이 「데이터가 없다」 로 읽히기
    때문이다 — 그러면 사람은 없는 것을 새로 만든다.
    """
    if provider not in _temporal:
        _temporal.append(provider)


def register_attachment_owner(owner_table: str, provider: AttachmentOwnerProvider) -> None:
    """그 표의 자료에 첨부를 붙일 때 **자리를 확인하는 길.**

    등록하지 않은 표는 예전 동작이다 — 올린 쪽이 고른 부서의 관리자면 붙는다. 등록하면
    자료가 있는지 · 고칠 수 있는지 · 그 자리가 첨부를 받는 칸인지를 도메인이 본다.
    """
    _attachment_owners[owner_table] = provider


def attachment_owner(
    db: Session, user: User, owner_table: str, owner_id: uuid.UUID, owner_field: str | None
) -> AttachmentOwner | None:
    """등록된 표면 그 답, 아니면 None(부르는 쪽이 예전 동작으로)."""
    provider = _attachment_owners.get(owner_table)
    if provider is None:
        return None
    return provider(db, user, owner_id, owner_field)


def has_temporal_source() -> bool:
    """도메인이 연도를 답할 수 있는가. **없으면 부르는 쪽이 필터를 건너뛴다.**"""
    return bool(_temporal)


def temporal_years(db: Session, object_ids: list[uuid.UUID]) -> dict[uuid.UUID, set[int]]:
    """객체별로 「쓰인 연도」 들. **등록이 없으면 빈 dict** 이고, 부르는 쪽은
    그때 필터를 적용하지 않는다."""
    out: dict[uuid.UUID, set[int]] = {}
    for provider in _temporal:
        for object_id, years in provider(db, object_ids).items():
            out.setdefault(object_id, set()).update(years)
    return out


def maintenance_items(db: Session, viewer: User) -> list[MaintenanceItem]:
    """**0 건은 버린다.** 거르는 자리를 여기 한 곳에 두면 등록하는 쪽이 매번
    같은 조건을 다시 적지 않아도 되고, 빠뜨릴 자리도 없다."""
    out: list[MaintenanceItem] = []
    for provider in _maintenance:
        out.extend(item for item in provider(db, viewer) if item.count)
    return out


def stat_items(db: Session) -> list[StatItem]:
    """**0 건도 내보낸다.** 여기는 「지금 이 설치에 뭐가 있나」 라서, 0 이라는
    답 자체가 정보다 — 남은 일과 성격이 다르다."""
    out: list[StatItem] = []
    for provider in _stats:
        out.extend(provider(db))
    return out


def workspace_references(db: Session, workspace_id: uuid.UUID) -> list[WorkspaceReference]:
    """**0 건은 버린다.** 안 걸린 참조를 늘어놓으면 정작 막는 하나가 묻힌다."""
    out: list[WorkspaceReference] = []
    for provider in _references:
        out.extend(item for item in provider(db, workspace_id) if item.count)
    return out


def workspace_contents(db: Session, workspace_id: uuid.UUID) -> list[WorkspaceContent]:
    """**0 건도 남긴다.** 옮기기 화면은 「무엇을 옮길 수 있나」 의 전체 목록이라,
    빠진 종류는 「안 옮겨지는 것」 으로 읽힌다."""
    out: list[WorkspaceContent] = []
    for provider in _contents:
        out.extend(provider(db, workspace_id))
    return out
