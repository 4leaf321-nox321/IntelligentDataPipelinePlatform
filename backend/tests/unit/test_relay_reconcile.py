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
    assert report.problems and "적용한 계획이 없어" in report.summary()


def test_대상과_같으면_어긋남이_없고_이력에_남는다(tmp_path: Path) -> None:
    pipeline, work, fake = _applied(tmp_path)
    reader = FakeReader({"plm_model": SAME}, {"plm_model": "plm"})
    report = compare(pipeline, work, reader, fake)
    assert report.drift == 0 and not report.problems, report.summary()
    assert "일치합니다(객체 3건)" in report.summary()
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
    assert (
        "불일치 4건(누락 1 · 사용 중지 1 · 값 불일치 1 · 대상에만 존재 1)" in report.summary()
    )


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
    assert outcome.status == "nothing" and "사람이 판단합니다" in outcome.message
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
    assert reader.via == "목록" and "사이드바 그룹" in reader.fallback  # loose 는 묶음 밖

    reader = reconcile.ExportReader(_sp(monkeypatch, fake), ["plm_model"])
    assert reader.via == "내보내기" and not reader.fallback
    assert ("POST", "/api/bundles/export") in fake.calls
    got = reader.objects("plm_model")
    assert got is not None
    models = list(got)
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
    assert list(reader.objects("loose") or []) == [{"id": "u1", "key": "L1", "label": "목록"}]


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
    assert "목록 API로 조회했습니다" in report.summary()
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
    assert "코어 공개되지 않은 타입(plm_model)" in told and not reader.fallback  # type: ignore[attr-defined]

    class Broken(_Sp):
        def wait(self, *_a: Any, **_k: Any) -> Any:
            from relay_core import sp

            raise sp.pipeline.Stop("작업이 실패했습니다: OperationalError: …\n[SQL: SELECT …]")

    reader = reconcile.best_reader(_sp(monkeypatch, Broken()), ["plm_model"])
    said = reader.fallback  # type: ignore[attr-defined]
    assert reader.via == "목록" and "코어 공개되지 않은 타입(plm_model)" in said  # type: ignore[attr-defined]
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


def test_바구니로_나눠_견줘도_한꺼번에_견준_것과_같다(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """기준 · 대상을 (타입, 식별자)의 해시로 나눠 견준다 — 바구니가 여럿이어도 보고서가 같고,
    바구니 폴더는 남지 않는다."""
    pipeline, work, fake = _applied(tmp_path)
    items = [
        _item("M2", "엣지", "선행", status="deprecated"),
        _item("M3", "노트 2", "양산"),
        _item("M3", "노트 2 — 같은 식별자가 또", "양산"),  # 처음 것만 본다
        _item("M9", "누가 만든 것", "양산"),
    ]
    reader = FakeReader({"plm_model": items}, {})
    whole = compare(pipeline, work, reader, fake).to_json()
    monkeypatch.setattr(reconcile, "BUCKET_ROWS", 2000)  # 파일 하나(5,000줄 몫) → 바구니 3
    split = compare(pipeline, work, reader, fake).to_json()
    assert {k: v for k, v in split.items() if k != "at"} == {
        k: v for k, v in whole.items() if k != "at"
    }
    assert whole["types"]["plm_model"]["changed"] == {"M3": ["label"]}
    assert not list(work.path("work").glob("*.reconcile.spill"))


# --- 목록을 커서로 · 대상 객체를 다 쥐지 않기 ---------------------------------------------

KIND_ID = "0b6c6a8e-0000-4000-8000-00000000000a"
NEW_ID = "0b6c6a8e-0000-4000-8000-00000000000b"


class _ListSp(_Sp):
    """목록 API 를 커서로(`cursors`) 또는 옛 SP 처럼 offset 으로만 넘기는 대상."""

    def __init__(self, cursors: bool = True) -> None:
        super().__init__()
        self.cursors = cursors
        self.models = [
            {"id": "u-m1", "key": "M1", "label": "갤럭시", "status": "active"},
            {"id": "u-m2", "key": "M2", "label": "엣지", "status": "deprecated"},
            {"id": "u-m3", "key": "M3", "label": "노트", "status": "active"},
        ]

    def send(self, _server: str, method: str, url: str, *_a: Any, **_k: Any) -> Any:
        path = url.split("sp.test", 1)[1]
        if not path.startswith("/api/objects/plm_model?"):
            return super().send(_server, method, url, *_a, **_k)
        self.calls.append((method, path))
        query = dict(one.split("=", 1) for one in path.split("?", 1)[1].split("&"))
        size = min(int(query["limit"]), 2)  # 시험에서는 두 줄씩
        if self.cursors and "cursor" in query:
            at = 0 if query["cursor"] == "start" else int(query["cursor"].removeprefix("c%20"))
            page = self.models[at : at + size]
            more = at + size < len(self.models)
            after = f"c {at + size}" if more else None
            return 200, {"items": page, "total": 3, "next_cursor": after}
        at = int(query.get("offset", 0))  # 옛 SP — cursor 는 모르는 인자라 버린다
        return 200, {"items": self.models[at : at + size], "total": 3, "limit": 200}


def test_대상이_커서로_넘기면_목록을_커서로_흘려_읽는다(monkeypatch: Any) -> None:
    fake = _ListSp()
    reader = reconcile.best_reader(_sp(monkeypatch, fake), ["plm_model"])
    assert isinstance(reader, reconcile.TargetReader) and reader.via == "목록"
    # 코어로 안 열렸고 SP 가 커서로 넘긴다 — 봉투를 통째로 받는 내보내기보다 먼저다.
    assert "코어 공개되지 않은 타입(plm_model)" in reader.notice and "커서" in reader.notice
    assert ("POST", "/api/bundles/export") not in fake.calls
    got = reader.objects("plm_model")
    assert got is not None and not isinstance(got, list)  # 쪽마다 흘려 준다
    assert [one["key"] for one in got] == ["M1", "M2", "M3"]
    listed = [path for _m, path in fake.calls if path.startswith("/api/objects/plm_model")]
    assert listed[-2:] == [
        "/api/objects/plm_model?limit=2000&cursor=start",
        "/api/objects/plm_model?limit=2000&cursor=c%202",
    ]
    assert not [one for one in listed if "offset=" in one] and not reader.slow
    assert reader.objects("없는타입") is None

    # 다른 표를 비추는 타입은 커서를 거절한다(422) — offset 으로 읽되 「옛 SP」 라 하지 않는다.
    class Projected(_ListSp):
        def send(self, _server: str, method: str, url: str, *_a: Any, **_k: Any) -> Any:
            if "cursor=start" in url:
                return 422, {"error": {"code": "OBJECTS-0156"}}
            return super().send(_server, method, url, *_a, **_k)

    reader = reconcile.TargetReader(_sp(monkeypatch, Projected(cursors=False)))
    assert [one["key"] for one in reader.objects("plm_model") or []] == ["M1", "M2", "M3"]
    assert not reader.slow and reader.via == "목록"


def test_옛_SP_면_목록을_offset_으로_넘기고_까닭을_적는다(
    tmp_path: Path, monkeypatch: Any
) -> None:
    fake = _ListSp(cursors=False)
    target = _sp(monkeypatch, fake)
    reader = reconcile.TargetReader(target)
    assert not reader.cursors(["없는타입", "plm_model"])  # 있는 타입 하나에 한 줄만 묻는다
    got = list(reader.objects("plm_model") or [])
    assert [one["key"] for one in got] == ["M1", "M2", "M3"]
    assert fake.calls[-1][1] == "/api/objects/plm_model?limit=2000&offset=2"
    assert reader.via == reconcile.OFFSET_VIA and "SP v0.4.89" in reader.slow

    pipeline, work, submit = _applied(tmp_path)
    report = compare(pipeline, work, reconcile.TargetReader(target), submit)
    assert report.via == "목록(offset)" and any("offset" in one for one in report.notes)


class _Ided(FakeReader):
    """목록 API 처럼 참조를 상대의 **id** 로 주는 읽개 — 참조가 가리키는 타입도 준다."""

    def __init__(self, items: dict[str, list[dict[str, Any]] | None], refs: list[str]) -> None:
        super().__init__(items, {})
        self.refs = refs
        self.asked: list[str] = []

    def objects(self, type_slug: str) -> Any:
        self.asked.append(type_slug)
        found = self.items.get(type_slug)
        return None if found is None else iter(found)

    def referenced(self, slugs: Any) -> list[str]:
        return list(self.refs)


def test_목록의_참조_id_는_디스크의_표로_풀고_바구니로_나눠도_같다(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """참조 칸(여기서는 model_kind)이 상대 타입 객체의 id 로 온다 — 그 타입을 함께 읽어 id →
    식별자를 디스크의 표에 적고, 바구니마다 그 바구니의 id 만 꺼내 견준다."""
    pipeline, work, fake = _applied(tmp_path)
    models = [
        {**_item("M1", "갤럭시", ""), "properties": {"model_kind": KIND_ID}},
        {**_item("M2", "엣지", ""), "properties": {"model_kind": [NEW_ID]}},
        {**_item("M3", "노트", ""), "properties": {"model_kind": NEW_ID}},
    ]
    kinds = [{"id": KIND_ID, "key": "양산"}, {"id": NEW_ID, "key": "선행"}]
    reader = _Ided({"plm_model": models, "plm_kind": kinds}, ["plm_kind"])
    whole = compare(pipeline, work, reader, fake)
    assert reader.asked == ["plm_kind", "plm_model"]  # 참조가 가리키는 타입을 먼저
    one = whole.types["plm_model"]
    # M3 은 「양산」 이어야 하는데 「선행」 의 id 다 — 다름. 나머지는 id 를 풀어 같다.
    assert one.changed == {"M3": ["model_kind"]} and one.unverified == 0, whole.summary()
    monkeypatch.setattr(reconcile, "BUCKET_ROWS", 2000)  # 바구니 3
    split = compare(pipeline, work, reader, fake)
    assert split.to_json()["types"] == whole.to_json()["types"]
    assert not list(work.path("work").glob("*.reconcile.spill"))

    # 참조가 가리키는 타입을 못 읽으면 그 참조는 이름(`ref_labels`)으로 — 이름이 다르면
    # 어긋남이 아니라 확인 못 함이다(그 id 가 무엇인지 모른다). 까닭은 보고서에.
    class Broken(_Ided):
        def objects(self, type_slug: str) -> Any:
            if type_slug == "plm_kind":
                raise reconcile.ReconcileError("403")
            return super().objects(type_slug)

    labelled = [{**one, "ref_labels": {KIND_ID: "양산", NEW_ID: "선행"}} for one in models]
    broken = Broken({"plm_model": labelled}, ["plm_kind"])
    report = compare(pipeline, work, broken, fake)
    assert any("참조 대상 타입(plm_kind)" in one for one in report.notes)
    assert (
        report.types["plm_model"].changed == {} and report.types["plm_model"].unverified == 1
    )


def test_디스크의_표는_그_바구니의_id_만_꺼낸다(tmp_path: Path) -> None:
    refs = reconcile._DiskRefs(tmp_path / "ids.sqlite")
    refs.add([{"id": f"u-{n}", "key": f"K-{n}"} for n in range(12_000)])
    refs.load(["u-5", "u-11999", "없는 id"])
    assert refs.keys == {"u-5": "K-5", "u-11999": "K-11999"}
    assert refs.same("K-5", "u-5", {}) is True and refs.same("K-6", "u-5", {}) is False
    refs.load([])
    assert refs.keys == {}
    refs.close()
    item = {"properties": {"a": KIND_ID, "b": [NEW_ID, "글"], "c": 3, "d": "아님"}}
    assert list(reconcile._ids_in(item)) == [KIND_ID, NEW_ID]


# --- 적용보다 뒤에 ------------------------------------------------------------------------


def _waiting(tmp_path: Path) -> tuple[Any, Workdir, Any, str]:
    """적용한 기준 위에 다음 계획이 대상에서 기다린다."""
    pipeline, work, fake = _applied(tmp_path)
    rows = [dict(one) for one in ROWS]
    rows[2] = {**rows[2], "name": "노트 2", "updated_at": "2026-11-10T00:00:00Z"}
    changed = runner.run(_pipeline(tmp_path, rows), work, fake)
    assert changed.status == "planned", changed.message
    pending = work.load_state().pending
    assert pending is not None
    return pipeline, work, fake, pending.job_id


def test_대상에서_적용하는_중이면_대조를_미루고_보고서는_그대로(tmp_path: Path) -> None:
    pipeline, work, fake, job = _waiting(tmp_path)
    reader = FakeReader({"plm_model": SAME[:1]}, {"plm_model": "plm"})
    fake.applying_jobs.add(job)
    history = len(work.load_state().history)

    report = compare(pipeline, work, reader, fake)
    assert report.deferred and "적용하고 있어 대조를 연기했습니다" in report.summary()
    assert work.read_json(reconcile.REPORT) is None  # 지난 대조(없음)가 그대로
    state = work.load_state()
    assert len(state.history) == history and state.pending is not None  # 기준도 안 옮겼다
    outcome = resync(pipeline, work, reader, fake)
    assert outcome.status == "waiting" and "적용하고 있습니다" in outcome.message
    assert len(fake.previews) == 2  # 보정 계획을 안 올렸다

    # 적용이 끝나면 그 계획이 기준이 되고 대조가 돈다.
    fake.applying_jobs.discard(job)
    fake.applied_jobs.add(job)
    report = compare(pipeline, work, reader, fake)
    assert not report.deferred and report.baseline == work.load_state().applied_run
    assert work.read_json(reconcile.REPORT)["drift"] == report.drift > 0


def test_읽는_사이에_적용이_시작되면_읽은_것을_버린다(tmp_path: Path) -> None:
    pipeline, work, fake, job = _waiting(tmp_path)

    class Racing(FakeReader):
        def objects(self, type_slug: str) -> Any:
            fake.applying_jobs.add(job)  # 사람이 대상 화면에서 적용을 눌렀다
            return super().objects(type_slug)

    report = compare(pipeline, work, Racing({"plm_model": SAME}, {}), fake)
    assert report.deferred and "결과를 폐기했습니다" in report.summary()
    assert work.read_json(reconcile.REPORT) is None
    # 아직 적용하는 중이면 보정 계획도 미룬다.
    assert resync(pipeline, work, Racing({"plm_model": SAME}, {}), fake).status == "waiting"
