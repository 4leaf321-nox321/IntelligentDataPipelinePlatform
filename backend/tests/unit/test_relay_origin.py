"""원천 기록(SP ADR 0087) — 대상 SP 에 올리는 묶음에 이 설치 · 파이프라인 · 실행 번호를
싣는다.

SP 가 묶음 판과 그 판이 넣은 객체의 이력에 남기고, 이력이 이 설치의 「거꾸로 보기」 로
링크한다. 예전에는 SP 이력에 「시스템 관리자 · 일괄 가져오기」 만 남아 어느 실행에서 왔는지
여기서만 볼 수 있었다.

- 바탕(`Pipeline.origin` — 작업 워커가 채운다)이 없으면 싣지 않는다(CLI).
- 실행 번호는 보내는 폴더의 이름 — 나눠 보낸 조각도 같은 번호.
- 지문에는 안 넣는다(실행마다 번호가 달라 「같은 계획」 비교가 늘 「다르다」 가 된다).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from relay_core import runner, sp
from relay_core.state import Workdir
from relay_core.submit import Submitter
from tests.unit.test_relay_parts import _graves_only, _split_pipeline, small_parts
from tests.unit.test_relay_rest import FakeSubmitter, _pipeline

BASE = {
    "system": "relay-x",
    "url": "https://relay.example/relay/pipelines/t",
    "trace": "https://relay.example/relay/pipelines/t?trace_type={type}&trace_key={key}",
}

__all__ = ["small_parts"]  # 픽스처를 이 모듈에서 쓴다


def test_바탕이_없으면_싣지_않고_있으면_파이프라인과_실행_번호를_더한다(
    tmp_path: Path,
) -> None:
    pipeline = _pipeline(tmp_path, [])
    assert runner.origin_for(pipeline, "sends/20261118T000000Z") is None
    with_base = dataclasses.replace(pipeline, origin=BASE)
    got = runner.origin_for(with_base, "sends/20261118T000000Z-resync")
    assert got == {**BASE, "pipeline": pipeline.slug, "run": "20261118T000000Z-resync"}


def test_제출은_origin_을_싣되_지문은_그대로다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []

    def post(_server: str, _token: str, body: dict[str, Any]) -> dict[str, Any]:
        sent.append(body)
        return {"ok": True, "job_id": f"j-{len(sent)}", "objects": [], "relations": []}

    monkeypatch.setattr(sp.pipeline, "_post_bundle", post)
    target = _pipeline(tmp_path, []).target
    plain = Submitter(target).preview(_graves_only(tmp_path / "a"))
    origin = {**BASE, "pipeline": "t", "run": "20261118T000000Z"}
    marked = Submitter(target).preview(_graves_only(tmp_path / "b"), origin=origin)
    assert "origin" not in sent[0]
    assert sent[1]["origin"] == origin
    # 같은 묶음이면 지문이 같다 — origin 은 지문 밖이다.
    assert plain.digest == marked.digest


def test_실행과_나눠_보낸_조각이_같은_실행_번호를_싣는다(
    tmp_path: Path, small_parts: None
) -> None:
    from tests.unit.test_relay_parts import _rows

    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()
    pipeline = dataclasses.replace(_split_pipeline(tmp_path, _rows()), origin=BASE)
    first = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    pending = work.load_state().pending
    assert first.status == "planned" and pending is not None, first.message
    run_id = Path(pending.send).name
    assert fake.origins == [{**BASE, "pipeline": pipeline.slug, "run": run_id}]

    runner.apply(pipeline, work, fake)  # type: ignore[arg-type]
    # 뒤 조각의 미리 보기도 같은 실행 번호.
    assert len(fake.origins) >= 2
    assert {one["run"] for one in fake.origins if one} == {run_id}


def test_작업_워커의_바탕은_이_설치의_이름과_화면_주소다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import get_settings
    from app.modules.relay import jobs

    settings = get_settings()
    monkeypatch.setattr(settings, "public_url", "")
    # 화면 주소를 안 적으면 링크 없이 이름만 — 작업 워커는 브라우저가 보는 주소를 모른다.
    assert jobs.origin_base("mes parts") == {"system": settings.app_slug}

    monkeypatch.setattr(settings, "public_url", "https://relay.example/datarelay/")
    got = jobs.origin_base("mes parts")
    page = "https://relay.example/datarelay/relay/pipelines/mes%20parts"
    assert got == {
        "system": settings.app_slug,
        "url": page,
        "trace": f"{page}?trace_type={{type}}&trace_key={{key}}",
    }
