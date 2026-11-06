"""대조 — 대상에 **지금 실제로 있는 것**과 지난번 적용된 출력(기준)을 견주고, 어긋난 줄만 다시
보내는 보정 계획을 올린다.

원천 · 제출기는 `test_relay_rest` 의 가짜 그대로. 대상의 목록은 가짜 읽개(`FakeReader`)가
준다 — 진짜 SP 로 끝까지 도는 것은 `devtools/`(샘플 PLM + SP 시험 인스턴스)가 본다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from relay_core import reconcile, runner
from relay_core.reconcile import Report, _Refs, compare, resync
from relay_core.state import Workdir
from tests.unit.test_relay_rest import ROWS, FakeSubmitter, _pipeline


class FakeReader:
    def __init__(
        self, items: dict[str, list[dict[str, Any]] | None], managed: dict[str, Any]
    ) -> None:
        self.items = items
        self.managed = managed

    def objects(self, type_slug: str) -> list[dict[str, Any]] | None:
        return self.items.get(type_slug)

    def managed_by(self) -> dict[str, str | None]:
        return dict(self.managed)


def _item(key: str, label: str, kind: str, status: str = "active") -> dict[str, Any]:
    return {
        "id": f"id-{key}",
        "key": key,
        "label": label,
        "status": status,
        "properties": {"model_kind": kind},
        "ref_labels": {},
    }


#: 기준 그대로의 대상 — `ROWS` 를 레시피(A → 양산, B → 선행)로 옮긴 것.
SAME = [
    _item("M1", "갤럭시", "양산"),
    _item("M2", "엣지", "선행"),
    _item("M3", "노트", "양산"),
]


def _applied(tmp_path: Path) -> tuple[Any, Workdir, Any]:
    work = Workdir(tmp_path / "work", "t")
    fake: Any = FakeSubmitter()
    pipeline = _pipeline(tmp_path, [dict(one) for one in ROWS])
    assert runner.run(pipeline, work, fake).status == "planned"
    assert runner.apply(pipeline, work, fake).status == "applied"
    return pipeline, work, fake


def test_기준이_없으면_견주지_않는다(tmp_path: Path) -> None:
    work = Workdir(tmp_path / "work", "t")
    pipeline = _pipeline(tmp_path, [dict(one) for one in ROWS])
    fake: Any = FakeSubmitter()
    report = compare(pipeline, work, FakeReader({}, {}), fake)
    assert report.problems and "적용한 것이 없습니다" in report.summary()


def test_대상과_같으면_어긋남이_없고_이력에_남는다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    reader = FakeReader({"plm_model": SAME}, {"plm_model": "plm"})
    report = compare(pipeline, work, reader, fake)
    assert report.drift == 0 and not report.problems, report.summary()
    assert "같습니다(객체 3건)" in report.summary()
    last = work.load_state().history[-1]
    assert last["event"] == "reconciled" and last["drift"] == 0
    assert reconcile.last_report(work) == Report.from_json(work.read_json(reconcile.REPORT))


def test_빠짐_안씀_다름_대상에만을_가른다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    items = [
        _item("M2", "엣지", "선행", status="deprecated"),
        _item("M3", "노트 2", "양산"),
        _item("M9", "누가 만든 것", "양산"),
        _item("M8", "예전에 지운 것", "양산", status="deprecated"),  # 이미 무덤 — 셈에서 뺀다
    ]
    report = compare(pipeline, work, FakeReader({"plm_model": items}, {}), fake)
    one = report.types["plm_model"]
    assert (one.missing, one.deprecated, one.changed, one.extra) == (
        ["M1"],
        ["M2"],
        {"M3": ["label"]},
        ["M9"],
    )
    assert report.drift == 4 and not one.managed
    assert "어긋남 4건(빠짐 1 · 안 씀 1 · 다름 1 · 대상에만 1)" in report.summary()


def test_보정_계획은_어긋난_줄과_닿는_선을_싣고_잠긴_타입이면_무덤도(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    baseline = work.load_state().applied_run
    items = [
        _item("M2", "엣지", "선행", status="deprecated"),
        _item("M3", "노트", "선행"),
        _item("M9", "되돌린 백업에서 살아난 것", "양산"),
    ]
    reader = FakeReader({"plm_model": items}, {"plm_model": "plm"})
    outcome = resync(pipeline, work, reader, fake)
    assert outcome.status == "planned", outcome.message
    assert "보정 계획" in outcome.message

    sent = fake.previews[-1]
    rows = json.loads((sent / "objects" / "plm_model.json").read_text(encoding="utf-8"))[
        "rows"
    ]
    by_key = {row["key"]: row for row in rows}
    assert set(by_key) == {"M1", "M2", "M3"}
    assert by_key["M2"]["status"] == "active" and "status" not in by_key["M1"]
    graves = json.loads((sent / "tombstones.json").read_text(encoding="utf-8"))
    assert graves["objects"] == [{"type_slug": "plm_model", "key": "M9", "merged_into": None}]
    edges = json.loads((sent / "relations" / "plm_model.json").read_text(encoding="utf-8"))
    assert [(row["src"], row["dst"]) for row in edges["rows"]] == [("M2", "M1")]
    manifest = json.loads((sent / "bundle.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "plm" and "보정 계획" in manifest["title"]

    state = work.load_state()
    assert state.pending is not None and state.pending.origin == "reconcile"
    assert state.pending.run == baseline  # 적용돼도 기준은 그대로다(원천이 안 바뀌었다)
    assert outcome.counts["objects"]["plm_model"] == {
        "created": 1,
        "changed": 2,
        "unchanged": 0,
        "gone": 1,
    }

    # 기다리는 계획이 있으면 또 올리지 않는다 — 사람이 보던 계획이 사라진다
    assert resync(pipeline, work, reader, fake).status == "waiting"
    assert runner.apply(pipeline, work, fake).status == "applied"
    assert work.load_state().applied_run == baseline


def test_잠기지_않은_타입의_대상에만은_사람이_정한다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    reader = FakeReader({"plm_model": [*SAME, _item("M9", "사람이 만든 것", "양산")]}, {})
    outcome = resync(pipeline, work, reader, fake)
    assert outcome.status == "nothing" and "사람이 정합니다" in outcome.message
    assert len(fake.previews) == 1  # 처음 적재뿐


def test_대상에_타입이_없으면_멈춘다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    outcome = resync(pipeline, work, FakeReader({"plm_model": None}, {}), fake)
    assert outcome.status == "blocked" and "타입이 없습니다" in outcome.message


def test_값은_모양이_달라도_같으면_같다() -> None:
    refs = _Refs()
    refs.add([{"id": "u-1", "key": "T-1"}])
    same = refs.same
    assert same("12.50", 12.5, {}) and same("1,200", 1200, {}) and not same("12", 13, {})
    assert same("true", True, {}) and not same("false", True, {})
    assert same("2026-01-05 00:00:00", "2026-01-05T00:00:00Z", {})
    assert same("a;b", ["b", "a"], {})
    # 참조 — 같은 대조에서 받은 객체면 식별자로, 아니면 이름으로, 둘 다 아니면 모른다
    assert same("T-1", "u-1", {}) is True
    assert same("T-2", "u-1", {}) is False
    assert same("서울 시험소", "u-9", {"u-9": "서울 시험소"}) is True
    assert same("S-1", "u-9", {"u-9": "서울 시험소"}) is None


# --- 대상을 묶음 내보내기로 읽기 ---------------------------------------------------------

SCHEMA: list[dict[str, Any]] = [
    {
        "slug": "plm_model",
        "nav_group_slug": "plm",
        "managed_by": "plm",
        "properties": [
            {"key": "task", "data_type": "object_ref", "ref_type_slug": "plm_task"}
        ],
    },
    {
        "slug": "plm_task",
        "nav_group_slug": "plm",
        "managed_by": "plm",
        "properties": [{"key": "site", "data_type": "object_ref", "ref_type_slug": "site"}],
    },
    {"slug": "site", "nav_group_slug": "core", "managed_by": None, "properties": []},
    {"slug": "loose", "nav_group_slug": None, "properties": []},
]


def test_내보낼_묶음은_참조가_가리키는_타입의_묶음까지() -> None:
    schema = {one["slug"]: one for one in SCHEMA}
    # 견주는 타입의 참조만 풀리면 된다 — 한 단계(과제가 가리키는 시험소는 모델을 견줄 때
    # 안 쓴다).
    assert reconcile.export_groups(schema, ["plm_model"]) == ["plm"]
    assert reconcile.export_groups(schema, ["plm_task", "없는타입"]) == ["core", "plm"]
    assert reconcile.export_groups(schema, ["loose"]) is None  # 사이드바 묶음 밖
    system = {**schema, "site": {**schema["site"], "kind_class": "system"}}
    assert reconcile.export_groups(system, ["plm_task"]) is None


class _Sp:
    """대상 SP 대신 — 정의 · 내보내기 작업 · 결과 파일 · 목록을 준다."""

    def __init__(self, export_status: int = 202, core: tuple[str, ...] = ()) -> None:
        self.export_status = export_status
        self.core = core
        self.calls: list[tuple[str, str]] = []
        self.envelope = {
            "ontology": {
                "types": [{"slug": "plm_model"}, {"slug": "plm_task"}, {"slug": "site"}]
            },
            "objects": [
                {
                    "type_slug": "plm_model",
                    "rows": [
                        {"key": "M1", "label": "갤럭시", "status": "active", "task": "T-1"},
                        {"key": "M2", "label": "엣지", "status": "deprecated", "task": None},
                    ],
                },
                {"type_slug": "plm_task", "rows": [{"key": "T-1", "label": "과제 1"}]},
            ],
        }

    def send(self, _server: str, method: str, url: str, *_a: Any, **_k: Any) -> Any:
        path = url.split("sp.test", 1)[1]
        self.calls.append((method, path))
        if path == "/api/ontology/schema":
            return 200, {"types": SCHEMA}
        if path == "/api/core":
            return 200, {"types": [{"slug": slug} for slug in self.core]}
        if path.startswith("/api/core/plm_model?limit=2000"):
            if "cursor=" not in path:  # 첫 쪽 — 다음 쪽이 있다
                rows = [
                    {"key": "M1", "label": "갤럭시", "status": "active"},
                    {"key": "M0", "label": "지움", "deleted": True},
                ]
                return 200, {"items": rows, "next": "c 1"}
            assert "cursor=c%201" in path
            row = {"key": "M2", "label": "엣지", "status": "deprecated"}
            props = {"task": "T-1", "aliases": ["엣지폰"]}
            return 200, {"items": [{**row, "properties": props}], "next": None}
        if path == "/api/bundles/export":
            if self.export_status != 202:
                refused = {"error": {"code": "AUTH-0003", "message": "관리자만"}}
                return self.export_status, refused
            return 202, {"id": "job-x"}
        if path.startswith("/api/objects/loose"):
            return 200, {"items": [{"id": "u1", "key": "L1", "label": "목록"}], "total": 1}
        return 404, {}

    def wait(self, _server: str, _token: str, job: Any, **_k: Any) -> Any:
        return {"id": job["id"], "status": "done"}

    def download(self, _server: str, _token: str, path: str, **_k: Any) -> Any:
        assert path == "/api/jobs/job-x/download"
        return self.envelope


def _sp(monkeypatch: Any, fake: _Sp) -> Any:
    from relay_core import sp

    monkeypatch.setattr(sp.pipeline, "_send", fake.send)
    monkeypatch.setattr(sp.pipeline, "_wait_job", fake.wait)
    monkeypatch.setattr(sp.pipeline, "_get_json", fake.download)
    return _pipeline(Path("/tmp"), []).target


def test_내보내기_한_번으로_읽고_참조는_식별자로_온다(monkeypatch: Any) -> None:
    fake = _Sp()
    reader = reconcile.ExportReader(_sp(monkeypatch, fake), ["plm_model", "loose", "없는타입"])
    assert reader.via == "목록" and "사이드바 묶음" in reader.fallback  # loose 는 묶음 밖

    reader = reconcile.ExportReader(_sp(monkeypatch, fake), ["plm_model"])
    assert reader.via == "내보내기" and not reader.fallback
    assert ("POST", "/api/bundles/export") in fake.calls
    models = reader.objects("plm_model")
    assert models is not None
    first = models[0]
    assert first["key"] == "M1" and first["properties"] == {"task": "T-1"}
    assert models[1]["status"] == "deprecated"
    assert reader.objects("site") == []  # 내보냈지만 객체가 없다
    assert reader.objects("없는타입") is None  # 대상에 타입이 없다
    assert reader.managed_by()["plm_model"] == "plm"
    # 목록 API 는 부르지 않았다 — 200개씩 넘기지 않는다.
    assert not [one for one in fake.calls if one[1].startswith("/api/objects/")]


def test_내보내기를_못_쓰면_목록으로_읽고_까닭을_적는다(monkeypatch: Any) -> None:
    fake = _Sp(export_status=403)
    reader = reconcile.ExportReader(_sp(monkeypatch, fake), ["plm_model"])
    assert reader.via == "목록" and "403" in reader.fallback and "관리자만" in reader.fallback
    assert reader.objects("loose") == [{"id": "u1", "key": "L1", "label": "목록"}]


def test_내보내기의_줄로_견주면_참조를_식별자로_바로_본다() -> None:
    refs = _Refs()
    row = {"key": "M1", "label": "갤럭시", "task": "T-1", "tags": ["a", "b"]}
    item = reconcile._export_item(row)
    assert item["label"] == "갤럭시" and item["properties"]["task"] == "T-1"
    same = reconcile._diff_row({"key": "M1", "label": "갤럭시", "task": "T-1"}, item, refs)
    assert same == ([], 0)
    differs, unverified = reconcile._diff_row({"key": "M1", "task": "T-2"}, item, refs)
    assert differs == ["task"] and unverified == 0
    assert reconcile._diff_row({"key": "M1", "tags": "b;a"}, item, refs) == ([], 0)


def test_보고서는_어떻게_읽었는지_남긴다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)

    class Listed(FakeReader):
        via = "목록"
        fallback = "묶음 내보내기(plm)를 못 썼습니다: 403"

    reader = Listed({"plm_model": SAME}, {"plm_model": "plm"})
    report = compare(pipeline, work, reader, fake)
    assert report.drift == 0 and report.via == "목록"
    assert "목록 API 로 읽었습니다" in report.summary()
    again = Report.from_json(work.read_json(reconcile.REPORT))
    assert again.via == "목록" and again.notes == report.notes

    class Exported(FakeReader):
        via = "내보내기"
        notice = "코어로 열리지 않은 타입(plm_model)이 있어 내보내기로 읽었습니다"

    exported = Exported({"plm_model": SAME}, {"plm_model": "plm"})
    report = compare(pipeline, work, exported, fake)
    assert report.notes == [Exported.notice] and "코어로 열리지 않은" in report.summary()


def test_다_코어로_열렸으면_코어_API_를_커서로_읽는다(monkeypatch: Any) -> None:
    fake = _Sp(core=("plm_model", "plm_task"))
    reader = reconcile.best_reader(_sp(monkeypatch, fake), ["plm_model"])
    assert reader.via == "코어" and reader.keyed  # type: ignore[attr-defined]
    got = list(reader.objects("plm_model") or [])
    # 지운 것은 빼고, 다음 쪽까지 — 참조는 식별자, 별칭은 견주지 않는다.
    assert [one["key"] for one in got] == ["M1", "M2"]
    assert got[1]["properties"] == {"task": "T-1"} and got[1]["status"] == "deprecated"
    assert reader.objects("없는타입") is None
    assert ("POST", "/api/bundles/export") not in fake.calls
    assert not [one for one in fake.calls if one[1].startswith("/api/objects/")]


def test_일부만_코어면_내보내기로_둘_다_안_되면_목록으로_까닭을_짧게(monkeypatch: Any) -> None:
    fake = _Sp(core=("plm_task",))
    reader = reconcile.best_reader(_sp(monkeypatch, fake), ["plm_model", "plm_task"])
    assert reader.via == "내보내기"  # type: ignore[attr-defined]
    # 코어로 열기로 하고 하나를 빠뜨렸다 — 대조는 돌고, 빠진 타입을 보고서가 말한다.
    told = reader.notice  # type: ignore[attr-defined]
    assert "코어로 열리지 않은 타입(plm_model)" in told and not reader.fallback  # type: ignore[attr-defined]

    class Broken(_Sp):
        def wait(self, *_a: Any, **_k: Any) -> Any:
            from relay_core import sp

            raise sp.pipeline.Stop("작업이 실패했습니다: OperationalError: …\n[SQL: SELECT …]")

    reader = reconcile.best_reader(_sp(monkeypatch, Broken()), ["plm_model"])
    said = reader.fallback  # type: ignore[attr-defined]
    assert reader.via == "목록" and "코어로 열리지 않은 타입(plm_model)" in said  # type: ignore[attr-defined]
    assert "OperationalError" in said and "SQL" not in said


def test_코어가_주지_않는_칸은_확인_못_함으로_센다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)

    class Core(FakeReader):
        via = "코어"
        keyed = True
        blind = frozenset({"label"})  # 시험에서는 이름을 못 보는 길로

        def objects(self, type_slug: str) -> Any:
            found = self.items.get(type_slug)
            return None if found is None else iter(found)  # 흘려 준다

    items = [{**one, "label": "다른 이름"} for one in SAME]
    report = compare(pipeline, work, Core({"plm_model": items}, {"plm_model": "plm"}), fake)
    assert report.drift == 0 and report.via == "코어"
    assert report.types["plm_model"].unverified == 3
