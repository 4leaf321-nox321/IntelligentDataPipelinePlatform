"""미해결 작업함 · relay 사전 · GLM 의 제안.

- 미해결은 **진짜 변환**(`relay_core.convert`, 틀의 변환기 규칙)이 낸 것을 읽는다 — 틀의 미해결
  글 모양이 바뀌면 여기서 먼저 깨진다.
- relay 사전은 참조 대조가 못 푼 표기만, **대상에 있는 키로만** 푼다. 대상이 이미 푸는 표기는
  대상의 판정이 이긴다.
- GLM 은 넘긴 후보 안에서만 고르고, 확신이 모자라면 고르지 않는다.

참조 대조의 대상은 `@파일`(대상 SP 대신 — 한 줄에 키 하나)이다. 실행에서는 대상 SP 의 식별자 ·
별칭이다(`relay_core.runner` 가 주소 · 토큰을 넘긴다).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from relay_core import aliases, inbox
from relay_core.convert import convert
from relay_core.inbox import Candidate, TargetObject
from relay_core.llm import Llm, LlmError
from relay_core.mirror import Mirror
from relay_core.records import SourceRecord

SITES = "@sites.txt"
MATCH = {"keys": SITES, "on_missing": "unresolved"}
RECIPE: dict[str, Any] = {
    "format": "sp-table/1",
    "title": "시험",
    "types": [
        {
            "type_slug": "t_model",
            "key": {"column": "MODEL_ID"},
            "label": {"column": "NAME"},
            "fields": {"site": {"column": "SITE", "match": MATCH}},
        }
    ],
    "relations": [
        {
            "relation": "r_at",
            "type_slug": "t_model",
            "src": {"key_of": "t_model"},
            "dst": {"column": "SITE", "match": MATCH},
        }
    ],
}


def _mirror(rows: list[tuple[str, str, str]]) -> Mirror:
    return Mirror(
        SourceRecord(key=f"{key}#{n}", values={"MODEL_ID": key, "NAME": name, "SITE": site})
        for n, (key, name, site) in enumerate(rows)
    )


ROWS = [
    ("M1", "모델 1", "S-SEOUL"),  # 그대로
    ("M2", "모델 2", "s-busan"),  # 대소문자만 다름
    ("M3", "모델 3", "서울시험소"),
    ("M4", "모델 4", "서울시험소"),
    ("M5", "모델 5", "부산 시험소"),
]


def _convert(tmp_path: Path, body: dict[str, Any], rows: list[tuple[str, str, str]]) -> Path:
    root = tmp_path / f"r{len(list(tmp_path.glob('r*')))}"
    root.mkdir()
    (root / "sites.txt").write_text("S-SEOUL\nS-BUSAN\n", encoding="utf-8")
    recipe = root / "recipe.table.json"
    recipe.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    made = convert(_mirror(rows), recipe, root / "run", work=root / "work")
    return made.run


def _unresolved(run: Path) -> list[dict[str, Any]]:
    body: list[dict[str, Any]] = json.loads((run / "unresolved.json").read_text("utf-8"))
    return body


def _objects(run: Path) -> dict[str, dict[str, Any]]:
    batch = json.loads((run / "objects" / "t_model.json").read_text(encoding="utf-8"))
    return {row["key"]: row for row in batch["rows"]}


def test_미해결을_참조와_값_갈림으로_나누고_대상_타입_원천_칸을_붙인다(tmp_path: Path) -> None:
    rows = [*ROWS, ("M1", "모델 하나", "S-SEOUL")]  # 같은 M1 인데 이름이 갈린다
    run = _convert(tmp_path, RECIPE, rows)
    references, splits = inbox.parse(_unresolved(run), RECIPE)
    by = {(one.owner, one.name, one.text): one for one in references}
    seoul = by[("t_model", "site", "서울시험소")]
    assert seoul.target == SITES and seoul.column == "SITE" and seoul.rows == 2
    assert ("t_model", "site", "부산 시험소") in by
    # 관계의 끝도 — 「관계 <이름>」 의 src · dst.
    assert by[("관계 r_at", "dst", "서울시험소")].target == SITES
    assert ("t_model", "site", "s-busan") not in by  # 대소문자만 다른 것은 틀이 푼다
    (split,) = splits
    assert "M1" in split.what and "label" in split.what
    assert sorted(split.options) == ["모델 1", "모델 하나"] and split.rows == 2


def test_relay_사전은_못_푼_표기만_대상에_있는_키로_푼다(tmp_path: Path) -> None:
    body = aliases.merge(
        RECIPE,
        [
            aliases.Entry(SITES, "서울시험소", "S-SEOUL"),
            aliases.Entry(SITES, "부산 시험소", "S-BUSAN"),
        ],
    )
    assert body["relay_aliases"] == {
        SITES: {"부산 시험소": "S-BUSAN", "서울시험소": "S-SEOUL"}
    }
    assert "relay_aliases" not in RECIPE  # 새 본문이다 — 원래 것은 그대로
    run = _convert(tmp_path, body, ROWS)
    assert _unresolved(run) == []
    found = _objects(run)
    assert found["M3"]["site"] == "S-SEOUL" and found["M5"]["site"] == "S-BUSAN"
    report = (run / "table-report.txt").read_text(encoding="utf-8")
    assert "relay 사전으로 푼 표기: @sites.txt 2개" in report

    # 대상에 없는 키로는 풀지 않는다 — 그 표기는 미해결로 남고 보고서가 말한다.
    wrong = aliases.merge(RECIPE, [aliases.Entry(SITES, "서울시험소", "S-NOWHERE")])
    run = _convert(tmp_path, wrong, ROWS)
    left = {one.text for one in inbox.parse(_unresolved(run), wrong)[0]}
    assert left == {"서울시험소", "부산 시험소"}
    assert "대상에 없는 키 1개" in (run / "table-report.txt").read_text(encoding="utf-8")


def test_relay_사전의_모양과_쓰일_곳을_본다() -> None:
    assert aliases.check(RECIPE) == []
    assert aliases.match_targets(RECIPE) == {SITES}
    odd = {**RECIPE, "relay_aliases": {"t_other": {"x": "y"}, SITES: {"": "S-SEOUL"}}}
    problems = aliases.check(odd)
    assert any("t_other" in one and "쓰이지 않습니다" in one for one in problems)
    assert any("빈 표기" in one for one in problems)
    assert aliases.check({**RECIPE, "relay_aliases": ["x"]})
    merged = aliases.merge(
        {**RECIPE, "relay_aliases": {SITES: {"서울시험소": "S-BUSAN"}}},
        [aliases.Entry(SITES, "서울시험소", "S-SEOUL")],
    )
    assert merged["relay_aliases"][SITES] == {"서울시험소": "S-SEOUL"}  # 새것으로 바꾼다
    assert [one.key for one in aliases.rows_for_platform(merged)] == ["S-SEOUL"]


OBJECTS = [
    TargetObject("S-SEOUL", "서울 시험소", ("Seoul Lab",)),
    TargetObject("S-BUSAN", "부산 시험소"),
    TargetObject("S-OLD", "서울시험소", status="deprecated"),
    TargetObject("V-ANSYS", "앤시스코리아"),
]


def test_후보는_대상에_있는_것_중_닮은_것만_점수_차례로() -> None:
    got = inbox.candidates("서울시험소", OBJECTS)
    assert got[0].key == "S-SEOUL" and got[0].score == 1.0 and "이름" in got[0].why
    assert "S-OLD" not in [one.key for one in got]  # 안 씀은 내지 않는다
    assert "V-ANSYS" not in [one.key for one in got]  # 닮지 않은 것은 늘어놓지 않는다
    assert inbox.candidates("seoul lab", OBJECTS)[0].key == "S-SEOUL"  # 별칭도 본다
    assert inbox.candidates("앤시스", OBJECTS)[0].key == "V-ANSYS"  # 한쪽이 품는다
    # 두 글자 코드는 아무 키에나 우연히 들어 있다 — 품음으로 견주지 않는다.
    assert inbox.candidates("US", [TargetObject("S-AUSTIN", "오스틴 시험소")]) == []
    assert inbox.candidates("   ", OBJECTS) == []
    assert inbox.as_objects(
        [{"key": "A", "label": "가", "aliases": ["a"], "status": "active"}]
    )


def _llm(reply: dict[str, Any] | str, seen: list[dict[str, Any]], status: int = 200) -> Llm:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        content = reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
        return httpx.Response(status, json={"choices": [{"message": {"content": content}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Llm("http://glm.test/v1", "glm", "t0ken", min_confidence=0.8, client=client)


CANDIDATES = [
    Candidate("S-SEOUL", "서울 시험소", 0.9, "이름"),
    Candidate("S-BUSAN", "부산", 0.5, ""),
]


def test_GLM_은_후보_안에서만_고르고_확신이_모자라면_고르지_않는다() -> None:
    seen: list[dict[str, Any]] = []
    good = _llm({"key": "S-SEOUL", "confidence": 0.93, "reason": "이름이 같다"}, seen)
    picked = good.pick(
        text="서울시험소", target="t_site", column="SITE", candidates=CANDIDATES
    )
    assert picked.key == "S-SEOUL" and picked.confidence == 0.93
    schema = seen[0]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["key"]["enum"] == ["S-SEOUL", "S-BUSAN", ""]
    assert "서울시험소" in seen[0]["messages"][1]["content"] and seen[0]["temperature"] == 0

    low = _llm({"key": "S-SEOUL", "confidence": 0.5, "reason": "?"}, [])
    assert low.pick(text="x", target="t", column="C", candidates=CANDIDATES).key is None
    stray = _llm({"key": "S-JEJU", "confidence": 0.99, "reason": "?"}, [])
    out = stray.pick(text="x", target="t", column="C", candidates=CANDIDATES)
    assert out.key is None and "후보에 없는 키" in out.note
    none = _llm({"key": "", "confidence": 0.9, "reason": "없음"}, [])
    assert none.pick(text="x", target="t", column="C", candidates=CANDIDATES).key is None

    asked: list[dict[str, Any]] = []
    empty = _llm({"key": "S-SEOUL", "confidence": 1, "reason": ""}, asked)
    assert empty.pick(text="x", target="t", column="C", candidates=[]).key is None
    assert asked == []  # 후보가 없으면 묻지 않는다

    with pytest.raises(LlmError, match="거절"):
        _llm({}, [], status=500).pick(text="x", target="t", column="C", candidates=CANDIDATES)
    with pytest.raises(LlmError, match="JSON"):
        _llm("정답은 S-SEOUL", []).pick(
            text="x", target="t", column="C", candidates=CANDIDATES
        )


def test_실행은_대상의_식별자로_참조_대조하고_멈춘_실행을_작업함이_집는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from relay_core import runner, sp
    from relay_core.definition import Pipeline, Target
    from relay_core.state import Workdir
    from tests.unit.test_relay_intake import _Adapter
    from tests.unit.test_relay_rest import FakeSubmitter

    asked: list[tuple[str, str, str]] = []

    def fetch_keys(server: str, token: str, type_slug: str) -> set[str]:
        asked.append((server, token, type_slug))
        return {"S-SEOUL", "S-BUSAN"}

    monkeypatch.setattr(sp.pipeline, "fetch_keys", fetch_keys)
    monkeypatch.setattr(sp.pipeline, "resolve_names", lambda *_args: None)
    body = json.loads(json.dumps(RECIPE).replace(SITES, "t_site"))
    recipe = tmp_path / "recipe.table.json"
    recipe.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    adapter = _Adapter()
    adapter.records = list(_mirror(ROWS).records())
    pipeline = Pipeline(
        slug="t",
        source="plm",
        adapter=adapter,  # type: ignore[arg-type]
        recipe=recipe,
        target=Target(name="sp", server="http://sp.test", token="tok"),
        path=tmp_path / "p.json",
    )
    work = Workdir(tmp_path / "work", "t")
    fake = FakeSubmitter()

    held = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert held.status == "blocked" and "미해결" in held.message, held.message
    assert asked and asked[0] == ("http://sp.test", "tok", "t_site")  # 보낼 곳과 같은 대상
    blocked = inbox.blocked_run(work)
    assert blocked is not None
    references, _ = inbox.parse(inbox.read(work, blocked[0]), body)
    assert {one.text for one in references} == {"서울시험소", "부산 시험소"}

    recipe.write_text(
        json.dumps(
            aliases.merge(
                body,
                [
                    aliases.Entry("t_site", "서울시험소", "S-SEOUL"),
                    aliases.Entry("t_site", "부산 시험소", "S-BUSAN"),
                ],
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    done = runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert done.status == "planned", done.message
    assert inbox.blocked_run(work) is None
