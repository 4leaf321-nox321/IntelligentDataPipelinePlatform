"""기계 자격(PAT)의 범위 — **표에 없는 경로는 못 쓴다.**

사람 세션에는 범위를 안 건다. 그 사람의 권한이 이미 한계이고, 둘을 같은 축으로
섞으면 화면에서 되던 일이 이유 없이 막힌다. 범위는 **기계 자격에만 있는 개념**이다.

## 왜 레지스트리인가

공통 틀은 도메인 경로를 모른다. 그런데 판정은 한 곳(`shared/auth.py`)에서 해야
하므로, 도메인이 자기 경로와 범위를 **등록**한다. 조립은 `app/main.py` 가 한다 —
모듈이 서로를 import 하지 않게 하려면 조립 지점이 하나여야 한다.

## 기본은 읽기뿐이다

`register_scope` 로 더하지 않은 범위는 존재하지 않고, `register_write_scope` 로
열지 않은 경로는 어떤 범위로도 못 고친다. **모르는 것은 막는다** 가 맞는 기본값이다:
새 엔드포인트가 생길 때마다 자동으로 열리면, 그것을 알아채는 사람이 아무도 없다.
"""

from __future__ import annotations

from collections.abc import Collection, Iterator
from contextlib import contextmanager

#: 언제나 있는 범위. 읽기는 하나로 묶는다 — 읽기를 경로마다 쪼개면 토큰 하나를
#: 만들 때마다 목록을 다 훑어야 하고, 그러면 사람은 전부 켜 버린다.
READ = "read"

#: 이 설치가 아는 범위 전부. 발급 요청에 없는 범위가 오면 거절한다.
_known: list[str] = [READ]

#: 범위 → 그것을 연 확장. **코어가 연 범위는 여기 없다** — 언제나 있다.
#:
#: 확장의 범위는 그 확장이 꺼져 있으면 발급 목록에서 빠진다. 꺼진 확장의 경로는 404 라 그
#: 범위로 열 문이 없는데, 목록에 남아 있으면 사람은 켜져 있는 기능으로 읽는다(2026-10-23).
_owners: dict[str, set[str]] = {}

#: 지금 범위를 등록하고 있는 확장 — `main.py` 가 확장의 `register` 를 `owned_by` 안에서 부른다.
_registering: str | None = None

#: (경로 앞머리, 그 아래 **쓰기**에 필요한 범위).
_write: list[tuple[str, str]] = []

#: (경로 앞머리, 그 아래를 **읽을 수 있는** 좁은 범위).
#:
#: 기본은 `read` 하나다 — 읽기를 경로마다 쪼개면 토큰 하나를 만들 때마다 목록을 다 훑어야
#: 하고, 그러면 사람은 전부 켜 버린다. 그런데 **바깥 시스템에 주는 토큰**은 반대다: 사내
#: 전부를 읽히면 안 되고 열어 준 자리만 읽혀야 한다. 그 자리에만 좁은 범위를 둔다.
_narrow_reads: list[tuple[str, str]] = []

#: POST 지만 **읽기인** 경로. 본문에 물음을 싣거나(검색) 결과를 만들어 주기만 하는
#: 자리(내보내기)다.
#:
#: 이걸 빼먹으면 읽기 전용 토큰이 검색을 못 한다 — 그리고 검색은 대개 그 플랫폼이
#: 존재하는 이유다. 도메인이 `/api/search/` 같은 것을 만들면 여기 등록한다.
_read_only_posts: list[str] = []

#: POST 지만 읽기인 경로 — **중간에 이름이 끼는 것**(`/api/objects/*/export`, `*` 는 마디
#: 하나). 앞머리로는 못 적는다: `/api/objects/` 로 열면 그 아래 쓰기까지 통째로 열린다.
#: 끝(`/export`)만 보던 때는 slug 가 `export` 인 타입의 **객체 만들기**(`POST
#: /api/objects/export`)가 읽기 토큰으로 됐다(2026-10-08) — 마디 수까지 맞춘다.
_read_only_post_patterns: list[tuple[str, ...]] = []

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@contextmanager
def owned_by(extension: str) -> Iterator[None]:
    """이 안에서 연 범위는 그 확장의 것이다.

    확장이 자기 이름을 적게 두지 않고 조립 지점(`main.py`)이 씌운다 — 적게 두면 언젠가
    빠뜨리고, 빠뜨린 범위는 확장을 끈 뒤에도 목록에 남는다.
    """
    global _registering
    before, _registering = _registering, extension
    try:
        yield
    finally:
        _registering = before


def register_scope(scope: str) -> None:
    """이 설치가 아는 범위에 하나 더한다. 같은 것을 두 번 넣어도 안전하다.

    코어가 연 범위는 확장이 같은 것을 열어도 코어의 것이다 — 확장을 꺼도 안 빠진다.
    """
    if _registering is None:
        _owners.pop(scope, None)
    elif scope not in _known or scope in _owners:
        _owners.setdefault(scope, set()).add(_registering)
    if scope not in _known:
        _known.append(scope)


def _live(scope: str, enabled: Collection[str]) -> bool:
    owners = _owners.get(scope)
    return owners is None or not owners.isdisjoint(enabled)


def register_write_scope(path_prefix: str, scope: str) -> None:
    """이 경로 아래를 고치려면 이 범위가 있어야 한다.

    범위 자체도 함께 등록한다 — 열어 놓고 발급할 수 없는 상태를 안 만든다.
    """
    register_scope(scope)
    if (path_prefix, scope) not in _write:
        _write.append((path_prefix, scope))


def register_read_scope(path_prefix: str, scope: str) -> None:
    """이 경로 아래는 **이 좁은 범위로도** 읽을 수 있다(`read` 는 언제나 된다).

    바깥 시스템에 주는 토큰을 위한 자리다 — `read` 하나를 주면 그 계정이 볼 수 있는 전부가
    나간다. 여기 등록한 앞머리만 그 범위로 열린다.
    """
    register_scope(scope)
    if (path_prefix, scope) not in _narrow_reads:
        _narrow_reads.append((path_prefix, scope))


def _under(path: str, prefix: str) -> bool:
    """`path` 가 `prefix` 자리이거나 그 **아래**인가 — 마디 경계로. 글자 앞머리만 보면
    `/api/metrics/plan` 이 `/api/metrics/plant_yield/…` 까지 연다(2026-10-08)."""
    base = prefix.rstrip("/")
    return path == base or path.startswith(base + "/")


def may_read(path: str, token_scopes: list[str]) -> bool:
    """이 토큰이 이 경로를 읽어도 되나. `read` 가 있으면 전부, 없으면 좁은 범위의 자리만."""
    if READ in token_scopes:
        return True
    return any(
        _under(path, prefix) and scope in token_scopes for prefix, scope in _narrow_reads
    )


def register_read_only_post(path_prefix: str) -> None:
    """POST 지만 읽기인 경로. 읽기 범위로 통과시킨다."""
    if path_prefix not in _read_only_posts:
        _read_only_posts.append(path_prefix)


def register_read_only_post_pattern(pattern: str) -> None:
    """이 모양의 POST 는 읽기다 — `/api/objects/*/export` 처럼 중간에 이름이 끼는 자리
    (`*` 는 마디 하나, 마디 수가 같아야 맞는다).

    **내보내기는 읽기다.** 작업 한 줄을 남기므로 표로는 쓰기지만, 사람이 하는 일은 「가진
    것을 파일로 받기」 다. 읽기 토큰으로 못 하게 두면 「허브에서 정의를 받아 가는」 길이
    쓰기 권한을 요구하게 되고, 그러면 받아만 가면 되는 쪽에 쓰기 토큰을 주게 된다.
    """
    parts = tuple(pattern.strip("/").split("/"))
    if parts not in _read_only_post_patterns:
        _read_only_post_patterns.append(parts)


def _matches(path: str, pattern: tuple[str, ...]) -> bool:
    parts = path.strip("/").split("/")
    return len(parts) == len(pattern) and all(
        want in ("*", got) for want, got in zip(pattern, parts, strict=True)
    )


def known_scopes(enabled: Collection[str]) -> tuple[str, ...]:
    """지금 발급할 수 있는 범위 — 코어의 것 전부와 **켠** 확장의 것.

    `enabled` 는 켠 확장의 이름이다(`server.services.enabled_names`). 켜짐은 DB 가 정하므로
    여기서 묻지 않고 받는다 — shared 는 모듈의 표를 모른다.
    """
    return tuple(one for one in _known if _live(one, enabled))


def extension_of(scope: str) -> tuple[str, ...]:
    """이 범위를 연 확장 — 코어의 범위면 빈 것. 꺼진 확장의 범위를 거절할 때 이름을 말한다."""
    return tuple(sorted(_owners.get(scope, ())))


def write_scopes(enabled: Collection[str]) -> tuple[str, ...]:
    """쓰기를 여는 범위 — 등록된 순서로, 한 번씩, 꺼진 확장의 것은 빼고. 좁은 읽기
    범위(`core:read`)는 빠진다(`read` 가 이미 덮는다).

    HWAX 게이트웨이의 위임 토큰(ADR 0028)이 「그 사람이 화면에서 받을 수 있는 최대」 로 `read`
    와 이것을 받는다 — 확장이 범위를 더하면(그리고 켜면) 다음 발급부터 따라온다. 범위는 경로를
    여는 것일 뿐이고 고칠 수 있나는 그 사람의 권한이 정한다(사람 세션과 같다)."""
    return tuple(dict.fromkeys(scope for _prefix, scope in _write if _live(scope, enabled)))


def is_reading(method: str, path: str) -> bool:
    """읽기인가. 읽기로 연 POST 는 **POST 만** — 같은 경로의 PATCH · DELETE 까지 읽기로 치면
    읽기 토큰이 그 자리를 고치고 지운다(메서드를 안 보던 때 그랬다, 2026-10-08)."""
    if method in SAFE_METHODS:
        return True
    if method != "POST":
        return False
    if any(_under(path, prefix) for prefix in _read_only_posts):
        return True
    return any(_matches(path, pattern) for pattern in _read_only_post_patterns)


def needed_scope(path: str) -> str | None:
    """이 경로의 쓰기에 필요한 범위. 없으면 None (= 아무 범위로도 못 쓴다).

    **긴 앞머리부터 본다.** `/api/equipment-series` 가 `/api/equipment` 보다 먼저
    걸려야 한다 — 아니면 계열 수정이 장비 범위로 통과한다. 이 함정은 등록 순서가
    아니라 길이로만 막을 수 있다.
    """
    for prefix, scope in sorted(_write, key=lambda one: -len(one[0])):
        if _under(path, prefix):
            return scope
    return None
