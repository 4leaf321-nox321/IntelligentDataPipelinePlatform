"""범위 레지스트리의 주인 — 확장이 연 범위는 그 확장이 꺼지면 발급 목록에서 빠진다(DB 없이)."""

from __future__ import annotations

import pytest

from app.shared import scopes


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """레지스트리는 프로세스 전역이다 — 시험이 더한 범위가 다른 시험의 목록에 새지 않게."""
    monkeypatch.setattr(scopes, "_known", [scopes.READ])
    monkeypatch.setattr(scopes, "_write", [])
    monkeypatch.setattr(scopes, "_owners", {})


def test_확장의_범위는_그_확장을_켰을_때만_있다() -> None:
    scopes.register_write_scope("/api/objects", "objects:write")
    with scopes.owned_by("hub"):
        scopes.register_write_scope("/api/ext/hub", "hub:write")
    assert scopes.known_scopes(()) == ("read", "objects:write")
    assert scopes.known_scopes(("hub",)) == ("read", "objects:write", "hub:write")
    # 위임 토큰이 받는 쓰기 범위도 같다.
    assert scopes.write_scopes(()) == ("objects:write",)
    assert scopes.write_scopes(("hub", "bom")) == ("objects:write", "hub:write")
    assert scopes.extension_of("hub:write") == ("hub",)
    assert scopes.extension_of("objects:write") == ()


def test_코어가_연_범위는_확장이_같은_것을_열어도_안_빠진다() -> None:
    scopes.register_write_scope("/api/objects", "objects:write")
    with scopes.owned_by("hub"):
        scopes.register_write_scope("/api/ext/hub/objects", "objects:write")
    assert "objects:write" in scopes.known_scopes(())
    assert scopes.extension_of("objects:write") == ()


def test_둘이_연_범위는_어느_하나만_켜도_있다() -> None:
    with scopes.owned_by("hub"):
        scopes.register_write_scope("/api/ext/hub", "plm:write")
    with scopes.owned_by("bom"):
        scopes.register_write_scope("/api/ext/bom", "plm:write")
    assert "plm:write" in scopes.known_scopes(("bom",))
    assert "plm:write" not in scopes.known_scopes(("sample",))
    assert scopes.extension_of("plm:write") == ("bom", "hub")


def test_등록이_끝나면_주인이_풀린다() -> None:
    with scopes.owned_by("hub"):
        pass
    scopes.register_write_scope("/api/review", "review:write")
    assert scopes.extension_of("review:write") == ()
