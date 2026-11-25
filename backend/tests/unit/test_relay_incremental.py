"""증분 변환(`relay_core.incremental`)은 전체 변환과 **바이트까지 같다**.

무작위 미러에 무작위 변경(값 바뀜 · 새 행 · 지운 행 · 빈 행 · 충돌)과 대상 식별자의 변경을 여러
번 이어 가며, 매번 증분으로 낸 실행 폴더를 같은 미러를 처음부터 전체로 변환한 것과
견준다(`table-report.txt` 는 예시의 차례가 다를 수 있어 수만 — 전체 변환끼리도 바구니 수에 따라
그렇다).
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

from relay_core import compute, diff, incremental, sp, spill
from relay_core.adapters.base import Fetched
from relay_core.convert import transform, write_source
from relay_core.definition import Pipeline, Target
from relay_core.mirror import Changes
from relay_core.records import SourceRecord
from relay_core.state import MirrorFile, Workdir

RECIPE: dict[str, Any] = {
    "format": "sp-table/1",
    "title": "증분 시험",
    "types": [
        {
            "type_slug": "plm_model",
            "key": {"column": "MODEL_ID"},
            "label": {"column": "MODEL_NAME"},
            "fields": {
                "kind": {"column": "KIND", "map": {"A": "양산", "B": "선행"}},
                "base": {"column": "BASE", "match": {"keys": "@models.keys"}},
                "code": {"column": "CODE"},
            },
        },
        {
            "type_slug": "plm_task",
            "key": {"column": "TASK_ID"},
            "label": {"column": "TASK_NAME"},
            "fields": {
                "status": {"column": "STATUS"},
                "models": {"collect": {"column": "MODEL_ID"}},
                "note": {"join": {"column": "NOTE"}, "separator": " / "},
                "owner_base": {
                    "column": "OWNER",
                    "match": {"keys": "@owners.keys", "prefix": True},
                },
            },
        },
    ],
    "relations": [
        {
            "relation": "plm_task_model",
            "type_slug": "plm_task",
            "src": {"key_of": "plm_task"},
            "dst": {"key_of": "plm_model"},
            "properties": {"role": {"column": "ROLE"}},
        }
    ],
}

#: 두 번째 레시피 — 계산 필드 · 날짜 · 고정값 · 작업 공간 · 온톨로지 파일 · 도착점이 참조
#: 대조인 관계.
RICH: dict[str, Any] = {
    **RECIPE,
    "workspace_slug": "ws-root",
    "ontology": "ontology.json",
    "types": [
        RECIPE["types"][0],
        {
            **RECIPE["types"][1],
            "workspace_slug": "ws-task",
            "fields": {
                **RECIPE["types"][1]["fields"],
                "due": {"column": "DUE", "date": True},
                "kind_of": {"value": "과제"},
                "full": {"column": "FULL"},
                "first_owner": {"column": "OWNER2", "upper": True},
            },
        },
    ],
    "relations": [
        RECIPE["relations"][0],
        {
            "relation": "plm_task_owner",
            "type_slug": "plm_task",
            "src": {"key_of": "plm_task"},
            "dst": {
                "column": "OWNER",
                "match": {"keys": "@owners.keys", "prefix": True, "on_missing": "keep"},
            },
        },
    ],
}
#: 세 번째 레시피 — 대상 SP 의 식별자 · 별칭으로 푸는 참조(서버 쪽은 흉내 낸다)와 relay 사전.
SERVED: dict[str, Any] = {
    **RECIPE,
    "types": [
        {
            **RECIPE["types"][0],
            "fields": {
                **RECIPE["types"][0]["fields"],
                "base": {"column": "BASE", "match": {"keys": "master_model"}},
            },
        },
        {
            **RECIPE["types"][1],
            "fields": {
                **RECIPE["types"][1]["fields"],
                "owner_base": {
                    "column": "OWNER",
                    "match": {"keys": "master_owner", "prefix": True},
                },
            },
        },
    ],
    "relations": [
        RECIPE["relations"][0],
        {
            "relation": "plm_task_owner",
            "type_slug": "plm_task",
            "src": {"key_of": "plm_task"},
            "dst": {"column": "OWNER", "upper": True, "match": {"keys": "master_owner"}},
        },
    ],
    "relay_aliases": {"master_owner": {"모름": "OWN-2"}},
}
COMPUTED: dict[str, dict[str, Any]] = {
    "FULL": {"template": "{MODEL_ID}/{KIND}"},
    "OWNER2": {"first": ["OWNER", "STATUS"]},
}

STATUSES = ["진행", "완료", "보류", "", "중단"]
KINDS = ["A", "B", "Z", ""]
NOTES = ["가", "나", "", "다\n줄"]


class World:
    """시험용 원천과 대상 — 무작위로 바뀌는 미러와 식별자 파일."""

    def __init__(self, root: Path, rng: random.Random, *, kind: str = "plain") -> None:
        self.root, self.rng, self.kind = root, rng, kind
        root.mkdir(parents=True, exist_ok=True)
        recipe = {"plain": RECIPE, "rich": RICH, "served": SERVED}[kind]
        (root / "recipe.table.json").write_text(
            json.dumps(recipe, ensure_ascii=False), encoding="utf-8"
        )
        self.aliases = {"M999": "M4", "own-2": "OWN-2", "OWN-1x": "OWN-1"}
        self.aliases |= {f"M{n}": f"M{n - 1}" for n in range(1, 30, 4)}
        """대상 SP 가 별칭 · 이름으로 푸는 표기 — 날마다 생기고 사라진다."""
        (root / "ontology.json").write_text('{"types": []}', encoding="utf-8")
        self.rows: dict[str, dict[str, str]] = {}
        self.models_keys = {f"M{n}" for n in range(0, 30, 2)}
        self.owners_keys = {"OWN-1", "OWN-2", "OWN-10"}
        self.baseline: str | None = None
        """지난번 적용된 출력 — 실행마다 적용하지는 않는다(적용 대기)."""
        self.compared: list[str] = []
        """비교 방식 — `incremental` · `full`."""
        self.write_keys()
        for _ in range(rng.randint(20, 60)):
            self._put_random()

    def write_keys(self) -> None:
        (self.root / "models.keys").write_text(
            "\n".join(sorted(self.models_keys)) + "\n", encoding="utf-8"
        )
        (self.root / "owners.keys").write_text(
            "\n".join(sorted(self.owners_keys)) + "\n", encoding="utf-8"
        )

    def _values(self, task: int, model: int) -> dict[str, str]:
        rng = self.rng
        # 과제 · 모델의 칸은 대개 그 식별자로 정해지지만 가끔 갈리게(값 충돌) 바꾼다.
        status = STATUSES[task % 5] if rng.random() > 0.1 else rng.choice(STATUSES)
        return {
            "TASK_ID": f"T{task}" if rng.random() > 0.03 else "",
            "TASK_NAME": f"과제 {task}",
            "STATUS": status,
            "NOTE": rng.choice(NOTES),
            "OWNER": rng.choice(["OWN-1", "own-2", "OWN-1x", "", "모름"]),
            "MODEL_ID": f"M{model}" if rng.random() > 0.03 else "",
            "MODEL_NAME": f"모델 {model}" if rng.random() > 0.05 else f"모델 {model}'",
            "KIND": KINDS[model % 4] if rng.random() > 0.05 else rng.choice(KINDS),
            "BASE": rng.choice([f"M{model - 1}", "", "M999", f"m{model}", f"M{task}"]),
            "CODE": str(model * 3) if rng.random() > 0.02 else "",
            "ROLE": rng.choice(["주", "부", ""]),
            "DUE": rng.choice(["2026-03-01", "2026/03/02", "언젠가", ""]),
        }

    def _put_random(self) -> None:
        rng = self.rng
        task, model = rng.randint(0, 12), rng.randint(0, 29)
        key = f"K{rng.randint(0, 400):04d}"
        if rng.random() < 0.04:
            self.rows[key] = {name: "" for name in self._values(0, 0)}  # 빈 행
        else:
            self.rows[key] = self._values(task, model)

    def change(self) -> list[SourceRecord]:
        """한 단계의 변경 — 값 바꾸기 · 새 행 · 지우기, 그리고 가끔 대상 식별자도."""
        rng = self.rng
        before = dict(self.rows)
        for _ in range(rng.randint(1, 8)):
            kind = rng.random()
            if kind < 0.45 and self.rows:
                key = rng.choice(list(self.rows))
                row = dict(self.rows[key])
                for name in rng.sample(list(row), rng.randint(1, 3)):
                    row[name] = self._values(rng.randint(0, 12), rng.randint(0, 29))[name]
                self.rows[key] = row
            elif kind < 0.7:
                self._put_random()
            elif self.rows:
                del self.rows[rng.choice(list(self.rows))]
        if rng.random() < 0.5:
            self.models_keys ^= {f"M{rng.randint(0, 30)}"}
            self.owners_keys ^= {rng.choice(["OWN-1", "OWN-2", "OWN-3"])}
            self.write_keys()
            for text in ("M999", "모름", "OWN-1X", f"m{rng.randint(0, 29)}"):
                if rng.random() < 0.3 and self.aliases.pop(text, None) is None:
                    self.aliases[text] = rng.choice(["M2", "M8", "OWN-1", "OWN-9"])
        records = [
            SourceRecord(key=key, values=dict(values))
            for key, values in self.rows.items()
            if before.get(key) != values
        ]
        records.extend(
            SourceRecord(key=key, values={}, deleted=True)
            for key in before
            if key not in self.rows
        )
        return records

    @property
    def server(self) -> tuple[str, str]:
        return ("http://sp.test", "token") if self.kind == "served" else ("", "")

    def fetch_keys(self, _server: str, _token: str, target: str) -> set[str]:
        return set(self.models_keys if target == "master_model" else self.owners_keys)

    def resolve_names(
        self, _server: str, _token: str, target: str, names: list[str]
    ) -> dict[str, dict[str, Any]]:
        keys = self.fetch_keys("", "", target)
        return {
            name: {"match": "exact", "object": {"key": self.aliases[name]}}
            if self.aliases.get(name) in keys
            else {"match": "none"}
            for name in names
        }

    def computed(self) -> dict[str, Any]:
        return COMPUTED if self.kind == "rich" else {}

    @property
    def records(self) -> list[SourceRecord]:
        return [
            SourceRecord(key=key, values=dict(values)) for key, values in self.rows.items()
        ]


def _pipeline(root: Path, computed: dict[str, Any] | None = None) -> Pipeline:
    return Pipeline(
        slug="t",
        source="plm",
        adapter=None,  # type: ignore[arg-type]
        recipe=root / "recipe.table.json",
        target=Target(name="sp", server="", token=""),
        path=root / "pipeline.json",
        computed=computed or {},
    )


def _files(run: Path) -> dict[str, bytes]:
    return {
        path.relative_to(run).as_posix(): path.read_bytes()
        for path in sorted(run.rglob("*"))
        if path.is_file() and path.name != sp.table.REPORT
    }


def _index(work: Workdir) -> dict[str, Any]:
    """색인의 내용 — 통계와 표 넷(처음부터 지은 것과 이어 쓴 것이 같아야 한다)."""
    db = incremental._open(incremental.index_path(work), readonly=True)
    try:
        return {
            "totals": incremental._read_meta(db)["totals"],
            "rows": set(db.execute("SELECT * FROM rows")),
            "members": set(db.execute("SELECT * FROM members")),
            "lookups": set(db.execute("SELECT * FROM lookups")),
            "row_texts": set(db.execute("SELECT * FROM row_texts")),
        }
    finally:
        db.close()


def _same_index(ours: dict[str, Any], theirs: dict[str, Any]) -> None:
    assert ours["totals"] == theirs["totals"]
    assert ours["rows"] == theirs["rows"]
    assert ours["members"] == theirs["members"]
    # 바뀐 행은 식별자가 빈 모으기의 참조까지 적는다 — 더 있는 것은 다시 모으기를 늘릴 뿐이다.
    assert theirs["row_texts"] <= ours["row_texts"]
    assert theirs["lookups"] <= ours["lookups"]


def _full(
    work: Workdir,
    scratch: Path,
    recipe: Path,
    run: str,
    computed: dict[str, Any],
    server: tuple[str, str] = ("", ""),
) -> Path:
    """같은 미러를 **처음부터** 전체 변환한 실행 폴더 — 견줄 것."""
    twin = Workdir(scratch, "twin")
    twin.root.mkdir(parents=True, exist_ok=True)
    (twin.root / "mirror.json").write_bytes((work.root / "mirror.json").read_bytes())
    folder = twin.path(f"runs/{run}")
    if folder.exists():
        import shutil

        shutil.rmtree(folder)
    source = write_source(
        compute.wrap(MirrorFile(twin, twin.mirror_size()), computed),
        recipe,
        folder,
        work=twin.path("work"),
    )
    made = incremental.run_conversion(
        twin,
        replace(_pipeline(recipe.parent, computed), recipe=recipe),
        source,
        folder,
        None,
        server=server[0],
        token=server[1],
        transform=transform,
    )
    assert made.mode == "full"
    return folder


def _step(work: Workdir, world: World, records: list[SourceRecord], number: int) -> str:
    """한 단계 — 미러에 반영하고 변환해 전체와 견준다. 방식(`incremental` · `full`)을
    돌려준다."""
    computed, server = world.computed(), world.server
    pipeline = _pipeline(world.root, computed)
    when = f"20260101T{number:012d}Z"
    changes = incremental.record_changes(work, pipeline, work.path(f"work/{when}.changes"))
    staged = work.stage_mirror(
        Fetched(records=records, watermark="w", complete=True, full=False), changes
    )
    staged.commit()
    if staged.size == 0:
        return "empty"
    run = work.path(f"runs/{when}")
    source = write_source(
        compute.wrap(MirrorFile(work, staged.size), computed),
        pipeline.recipe,
        run,
        work=work.path("work"),
    )
    got = incremental.run_conversion(
        work,
        pipeline,
        source,
        run,
        changes,
        server=server[0],
        token=server[1],
        transform=transform,
        baseline=world.baseline,
    )
    made, mode, reason = got.made, got.mode, got.reason
    twin = _full(work, work.root / "twins", pipeline.recipe, when, computed, server)
    ours, theirs = _files(made.run), _files(twin)
    assert set(ours) == set(theirs), (mode, reason)
    for name in theirs:
        assert ours[name] == theirs[name], f"{name}: 증분 {mode}({reason})과 전체가 다릅니다"
    _same_index(_index(work), _index(Workdir(work.root / "twins", "twin")))
    _compare(work, world, run, got.touched)
    return mode


def _counts(found: diff.Delta) -> dict[str, Any]:
    return {
        kind: {slug: asdict(one) for slug, one in group.items()}
        for kind, group in (("objects", found.objects), ("relations", found.relations))
    }


def _compare(
    work: Workdir, world: World, run: Path, touched: incremental.Touched | None
) -> None:
    """기준과 견준다 — 건드린 것만 견준 보낼 폴더가 출력 전체를 견준 것과 바이트까지 같다."""
    baseline = work.path(world.baseline) if world.baseline else None
    whole = diff.delta(
        baseline, run, work.path(f"sends/{run.name}-whole"), source="plm", options={"x": "y"}
    )
    if touched is not None:
        part = incremental.delta(
            touched, work.path(f"sends/{run.name}"), source="plm", options={"x": "y"}
        )
        assert _counts(part) == _counts(whole)
        ours, theirs = _files(part.run), _files(whole.run)
        assert set(ours) == set(theirs)
        for name in theirs:
            assert ours[name] == theirs[name], f"{name}: 보낼 폴더가 다릅니다"
    world.compared.append("incremental" if touched is not None else "full")
    incremental.compared(work, run, world.baseline, empty=whole.empty)
    if world.rng.random() < 0.4:  # 적용하는 날도 있고 여러 날 기다리기도 한다
        world.baseline = f"runs/{run.name}"


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """실행 폴더의 머리(`bundle.json` 의 `created_at`)는 만든 시각이라 실행마다 다르다 —
    고정한다."""
    monkeypatch.setattr(sp.pipeline, "_stamp", lambda: "2026-01-01T00:00:00Z")


@pytest.mark.parametrize("kind", ["plain", "rich", "served"])
@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("bucket_rows", [7, 100_000])
def test_증분의_변환과_비교는_전체와_바이트까지_같다(
    tmp_path: Path, seed: int, bucket_rows: int, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 바구니를 작게 하면 변환 · 다시 모으기가 바구니로 나뉜다(큰 미러의 길).
    monkeypatch.setattr(spill, "BUCKET_ROWS", bucket_rows)
    if seed % 2:  # 타입마다 파일이 여럿으로 나뉜다(`<slug>-001.json` …)
        monkeypatch.setattr(sp.pipeline, "MAX_ROWS", 4)
    rng = random.Random(seed)
    world = World(tmp_path / "recipe", rng, kind=kind)
    monkeypatch.setattr(sp.pipeline, "fetch_keys", world.fetch_keys)
    monkeypatch.setattr(sp.pipeline, "resolve_names", world.resolve_names)
    work = Workdir(tmp_path / "work", "t")

    modes: list[str] = []
    records = world.records
    for number in range(8):
        modes.append(_step(work, world, records, number))
        records = world.change()
    assert "full" in modes  # 첫 실행은 색인을 짓는다
    assert "incremental" in modes
    assert modes.count("incremental") >= len(modes) - 3
    # 기준이 생긴 뒤로는 적용을 기다리는 동안에도 건드린 것만 견준다.
    assert "incremental" in world.compared or world.baseline is None


def test_변경이_없어도_행이_옮겨지지_않는다(tmp_path: Path) -> None:
    """같은 미러에 바뀐 것이 없으면 증분의 출력은 지난 출력에서 이름만 바뀐다."""
    rng = random.Random(99)
    world = World(tmp_path / "recipe", rng)
    work = Workdir(tmp_path / "work", "t")

    assert _step(work, world, world.records, 0) == "full"
    assert _step(work, world, [], 1) == "incremental"


def test_증분은_참조가_나중에_생기면_채우고_사라지면_비운다(tmp_path: Path) -> None:
    rng = random.Random(5)
    world = World(tmp_path / "recipe", rng)
    work = Workdir(tmp_path / "work", "t")

    _step(work, world, world.records, 0)
    pipeline = _pipeline(world.root)
    # 어떤 모델의 바탕이 대상에 없다가 생기고, 다시 사라진다 — 행은 하나도 안 바뀐다.
    for number, present in ((1, True), (2, False)):
        (world.models_keys.add if present else world.models_keys.discard)("M3")
        world.write_keys()
        assert _step(work, world, [], number) == "incremental"
    assert pipeline.recipe.exists()


def test_색인이_없으면_전체로_가고_까닭을_말한다(tmp_path: Path) -> None:
    rng = random.Random(1)
    world = World(tmp_path / "recipe", rng)
    work = Workdir(tmp_path / "work", "t")

    _step(work, world, world.records, 0)
    (work.root / incremental.INDEX).unlink()
    pipeline = _pipeline(world.root)
    changes = incremental.record_changes(work, pipeline, work.path("work/x.changes"))
    assert changes is None


def test_미러_변경_기록은_추가_변경_삭제와_자리를_적는다(tmp_path: Path) -> None:
    from relay_core.mirror import Merge

    def record(key: str, value: str) -> SourceRecord:
        return SourceRecord(key=key, values={"V": value})

    log = Changes(tmp_path / "c")
    existing = [record("a", "1"), record("b", "1"), record("c", "1")]
    merged = Merge(
        Fetched(
            records=[record("b", "2"), SourceRecord("c", {}, deleted=True), record("bb", "9")],
            watermark="w",
            complete=True,
            full=False,
        ),
        log,
    )
    out = list(merged.rows(iter(existing)))
    assert [one.key for one in out] == ["a", "b", "bb"]
    assert list(log) == [
        ("changed", "b", 1, 1, {"V": "1"}, {"V": "2"}),
        ("added", "bb", 2, 2, None, {"V": "9"}),
        ("removed", "c", 2, None, {"V": "1"}, None),
    ]
    assert (log.before, log.after) == (3, 3)
    log.drop()


def test_실행이_증분으로_변환하고_방식을_실행_추이에_적는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from relay_core import runner
    from tests.unit.test_relay_intake import _loaded, _model, _trend

    adapter, pipeline, work, fake = _loaded(tmp_path, count=60)
    first = _trend(work)[0]
    assert first["mode"] == "full" and first["full_reason"]
    adapter.full = False
    adapter.records = [_model(n, kind="B") for n in range(1, 4)]
    assert runner.run(pipeline, work, fake).status == "planned"  # type: ignore[arg-type]
    row = _trend(work)[-1]
    assert row["mode"] == "incremental" and "full_reason" not in row
    # 같은 미러를 처음부터 전체로 변환한 것과 바이트까지 같다.
    run = work.path(work.load_state().pending.run)  # type: ignore[union-attr]
    twin = _full(work, tmp_path / "twins", pipeline.recipe, run.name, {})
    assert _files(run) == _files(twin)

    # 색인이 망가지면 전체 변환으로 가고 색인을 새로 짓는다 — 실행은 실패하지 않는다.
    (work.root / incremental.INDEX).write_bytes(b"not a database")
    adapter.records = [_model(5, kind="B")]
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    row = _trend(work)[-1]
    assert row["mode"] == "full" and "증분 변환 오류" in row["full_reason"]
    adapter.records = [_model(6, kind="B")]
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert _trend(work)[-1]["mode"] == "incremental"


def test_증분_계획을_끄면_전체로만_간다(tmp_path: Path) -> None:
    from relay_core import runner
    from tests.unit.test_relay_intake import _loaded, _model, _trend

    adapter, pipeline, work, fake = _loaded(tmp_path, count=20)
    adapter.full = False
    adapter.records = [_model(1, kind="B")]
    runner.run(pipeline, work, fake, incremental_plan=False)  # type: ignore[arg-type]
    row = _trend(work)[-1]
    assert row["mode"] == "full" and row["full_reason"] == "증분 계획을 사용하지 않음"


def test_미러에_반영한_뒤_변환이_멈추면_다음_실행은_전체로_간다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """반영된 변경이 변환에 들어가지 않은 채 다음 실행이 왔다 — 그 실행의 변경만으로 이어 가면
    앞의 것이 영영 빠진다. 색인이 본 미러와 다르므로 전체로 간다."""
    from relay_core import runner
    from tests.unit.test_relay_intake import _loaded, _model, _trend

    adapter, pipeline, work, fake = _loaded(tmp_path, count=60)
    adapter.full = False
    adapter.records = [_model(n, kind="B") for n in range(1, 4)]
    with monkeypatch.context() as broken:

        def killed(*_args: Any, **_kwargs: Any) -> Any:
            raise MemoryError("상한")

        broken.setattr(incremental, "run_conversion", killed)
        with pytest.raises(MemoryError):
            runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert _trend(work)[-1]["stage"] == "변환"
    assert not list(work.path("work").glob("*.changes"))

    adapter.records = [_model(9, kind="B")]
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    row = _trend(work)[-1]
    assert row["mode"] == "full" and "미러가 따로 바뀜" in row["full_reason"]
    run = work.path(work.load_state().pending.run)  # type: ignore[union-attr]
    changed = {
        one["key"]
        for path in (work.path(f"sends/{run.name}") / "objects").glob("*.json")
        for one in json.loads(path.read_text(encoding="utf-8"))["rows"]
    }
    assert len(changed) >= 4  # 멈춘 실행의 셋도 계획에 있다
    adapter.records = [_model(10, kind="B")]
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert _trend(work)[-1]["mode"] == "incremental"


def test_변환_코드가_바뀌면_전체로_간다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from relay_core import runner
    from tests.unit.test_relay_intake import _loaded, _model, _trend

    adapter, pipeline, work, fake = _loaded(tmp_path, count=20)
    adapter.full = False
    adapter.records = [_model(1, kind="B")]
    monkeypatch.setattr(incremental, "_code", lambda: b"next release")
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    row = _trend(work)[-1]
    assert row["mode"] == "full" and "바뀜" in row["full_reason"]


def test_적용을_기다리는_동안에도_건드린_것만_견주고_남는_파일이_없다(tmp_path: Path) -> None:
    from relay_core import diff, runner
    from tests.unit.test_relay_intake import _loaded, _model, _trend

    adapter, pipeline, work, fake = _loaded(tmp_path, count=60)
    adapter.full = False
    baseline = work.load_state().applied_run
    assert baseline is not None
    for day in range(1, 4):  # 적용하지 않고 사흘 — 계획은 쌓인 변경 전부다
        adapter.records = [_model(day * 3, kind="B")]
        runner.run(pipeline, work, fake)  # type: ignore[arg-type]
        row = _trend(work)[-1]
        assert (row["mode"], row["compared"]) == ("incremental", "incremental")
        run = work.load_state().pending.run  # type: ignore[union-attr]
        whole = diff.delta(
            work.path(baseline), work.path(run), tmp_path / f"whole-{day}", source="plm"
        )
        assert _files(work.path(run.replace("runs/", "sends/"))) == _files(whole.run)
    assert row["plan"]["changed"] >= 3
    left = {path.suffix for path in work.path("work").iterdir()}
    assert left <= {".csv", ".json", ".edges", ".base"}


def test_색인을_짓지_못해도_실행은_성공하고_다음에_다시_짓는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from relay_core import runner
    from tests.unit.test_relay_intake import _loaded, _model, _trend

    with monkeypatch.context() as broken:

        def full_disk(_self: Any, *_args: Any) -> None:
            raise sqlite3.OperationalError("database or disk is full")

        broken.setattr(incremental.Builder, "seal", full_disk)
        adapter, pipeline, work, fake = _loaded(tmp_path, count=20)
    assert not incremental.index_path(work).exists()
    adapter.full = False
    adapter.records = [_model(1, kind="B")]
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert _trend(work)[-1]["mode"] == "full"
    # 색인을 이어 쓰다 죽은 흔적이 있으면 믿지 않는다 — 전체로 가고 흔적을 지운다.
    journal = work.root / f"{incremental.INDEX}-journal"
    journal.write_bytes(b"half")
    adapter.records = [_model(2, kind="B")]
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert "중단됨" in _trend(work)[-1]["full_reason"] and not journal.exists()
    adapter.records = [_model(3, kind="B")]
    runner.run(pipeline, work, fake)  # type: ignore[arg-type]
    assert _trend(work)[-1]["mode"] == "incremental"


def test_디스크의_참조_대조는_틀의_것과_같은_답을_준다(tmp_path: Path) -> None:
    rng = random.Random(3)
    letters = "abAB-_ .가1"
    words = {
        "".join(rng.choice(letters) for _ in range(rng.randint(1, 6))) for _ in range(800)
    }
    keys = set(rng.sample(sorted(words), 300))
    db = incremental._DiskMatch.store(tmp_path / "keys.sqlite")
    ours = incremental._DiskMatch(db, 0, keys)
    incremental._DiskMatch(db, 1, {"a"})  # 다른 대상의 식별자와 섞이지 않는다
    theirs = sp.table.KeyMatch(keys)
    for one in (ours, theirs):
        one.resolved = {"zz": "Z", "a": "A", "none": None}
    for text in [*words, *(word[:2] for word in words), "zz", "none", ""]:
        assert (text in ours.exact) == (text in theirs.exact)
        for prefix in (False, True):
            assert ours.find(text, prefix=prefix) == theirs.find(text, prefix=prefix), text
    db.close()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"key": {"column": "K", "match": {"keys": "master"}}}, "식별자 · 이름"),
        ({"fields": {"x": {"parser": "p", "slot": "a"}}}, "해석기"),
        (
            {
                "fields": {
                    "x": {"column": "X", "match": {"keys": "m", "on_missing": "unresolved"}}
                }
            },
            "unresolved",
        ),
    ],
)
def test_되풀이할_수_없는_레시피는_까닭과_함께_전체로_간다(
    tmp_path: Path, change: dict[str, Any], reason: str
) -> None:
    one = {"type_slug": "t", "key": {"column": "K"}, **change}
    assert reason in incremental._refused_recipe({"types": [one]})
    twice = {"types": [{"type_slug": "t", "key": {"column": "K"}}] * 2}
    assert "두 번" in incremental._refused_recipe(twice)
    broken = tmp_path / "broken.table.json"
    broken.write_text('{"format": "sp-table/1", "types": [1]}', encoding="utf-8")
    assert incremental.eligible_recipe(replace(_pipeline(tmp_path), recipe=broken))
    # `@파일` 은 대상에 묻지 않는다 — 식별자에 있어도 된다.
    filed = {"type_slug": "t", "key": {"column": "K", "match": {"keys": "@f.keys"}}}
    assert incremental._refused_recipe({"types": [filed]}) == ""


def test_미러의_키만_읽은_것이_행을_읽은_것과_같다(tmp_path: Path) -> None:
    work = Workdir(tmp_path, "t")
    assert list(work.mirror_keys()) == []
    keys = ["a", 'b"따옴표\\', "가\n나", "", "c|1", chr(0x2028), "😀"]
    records = [SourceRecord(key=key, values={"key": "다른 값"}) for key in keys]
    work.merge_mirror(Fetched(records=records, watermark="w", complete=True, full=True))
    found = list(work.mirror_keys())
    assert found == sorted(keys) == [one.key for one in work.mirror_rows()]
    # 옛 판(들여쓴 JSON 한 덩이)
    old = json.dumps([{"key": "k", "values": {"A": "1"}, "updated_at": None}], indent=2)
    (work.root / "mirror.json").write_text(old, encoding="utf-8")
    assert list(work.mirror_keys()) == ["k"]
