"""큰 미러의 실행 — 단계별 최고 메모리와 시간을 잰다(2026-11-25, `docs/개발계획.md` 「큰 미러의
실행」).

    backend/.venv/bin/python devtools/bench/relay_scale.py --rows 580000 --days 2
    backend/.venv/bin/python devtools/bench/relay_scale.py --rows 2240000 --daily-rows 20000

**지어낸 원천**이다(사내 데이터를 쓰지 않는다). 과제(`plm_task`) 하나가 개발모델(`plm_model`)
넷에 걸친 행 넷으로 오고(과제 · 모델 쌍이 원천 키), 과제는 모델 목록을 모으고(`collect`), 과제
→ 모델 관계가 있고, 모델의 바탕 모델은 참조 대조(`match` — 식별자 파일, 90% 만 있다)로 푼다.
하루치 변경은 과제 단위다 — 상태가 바뀐 과제는 그 행 넷이 다 온다(원천이 과제를 바꾸면 그
과제의 행을 다 보낸다). 새 과제 · 지운 과제도 섞는다.

실행마다 **새 프로세스**(`relay_core.isolate`)에서 돌려 그 실행만의 최고 메모리를 잰다. 대상 SP
없이 — 미리 보기 · 적용은 가짜 제출기가 표시만 한다. 첫 적재(전량) → 적용 → 하루치 실행 → 적용
…
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from relay_core import isolate, records, runner  # noqa: E402
from relay_core.adapters.base import Fetched  # noqa: E402
from relay_core.definition import Pipeline, Target  # noqa: E402
from relay_core.records import SourceRecord  # noqa: E402
from relay_core.state import Workdir  # noqa: E402
from relay_core.submit import Plan, digest_of  # noqa: E402

MODELS_PER_TASK = 4

RECIPE: dict[str, Any] = {
    "format": "sp-table/1",
    "title": "규모 시험(지어낸 원천)",
    "types": [
        {
            "type_slug": "plm_model",
            "key": {"column": "MODEL_ID"},
            "label": {"column": "MODEL_NAME"},
            "fields": {
                "model_kind": {
                    "column": "KIND",
                    "map": {"A": "양산", "B": "선행", "C": "단종"},
                },
                "base_model": {"column": "BASE", "match": {"keys": "@models.keys"}},
            },
        },
        {
            "type_slug": "plm_task",
            "key": {"column": "TASK_ID"},
            "label": {"column": "TASK_NAME"},
            "fields": {
                "status": {"column": "STATUS"},
                "owner": {"column": "OWNER"},
                "models": {"collect": {"column": "MODEL_ID"}},
            },
        },
    ],
    "relations": [
        {
            "relation": "plm_task_model",
            "type_slug": "plm_task",
            "src": {"key_of": "plm_task"},
            "dst": {"key_of": "plm_model"},
        }
    ],
}


@dataclass(frozen=True)
class Shape:
    rows: int

    @property
    def tasks(self) -> int:
        return max(1, self.rows // MODELS_PER_TASK)

    @property
    def models(self) -> int:
        return max(7, self.rows // 11)


def _model_of(shape: Shape, task: int, slot: int) -> int:
    return (task * 37 + slot * 1009) % shape.models


def _row(shape: Shape, task: int, slot: int, status: str) -> SourceRecord:
    model = _model_of(shape, task, slot)
    task_id, model_id = f"T{task:08d}", f"M{model:07d}"
    base = f"M{(model - 1) % shape.models:07d}" if model % 5 == 0 else ""
    return SourceRecord(
        key=f"{task_id}|{slot}",
        values={
            "TASK_ID": task_id,
            "TASK_NAME": f"과제 {task}",
            "STATUS": status,
            "OWNER": f"담당{task % 997}",
            "MODEL_ID": model_id,
            "MODEL_NAME": f"모델 {model}",
            "KIND": "ABC"[model % 3],
            "BASE": base,
        },
    )


def _task_rows(
    shape: Shape, task: int, status: str, deleted: bool = False
) -> list[SourceRecord]:
    out = []
    for slot in range(MODELS_PER_TASK):
        one = _row(shape, task, slot, status)
        out.append(SourceRecord(key=one.key, values=one.values, deleted=deleted))
    return out


class BenchAdapter:
    """첫날은 전량, 다음 날부터 그날의 변경만 — 정해진 씨앗으로 늘 같은 행."""

    @dataclass
    class _Source:
        full_every: int = 0
        deleted: str | None = None

    def __init__(self, shape: Shape, day: int, daily_rows: int) -> None:
        self.source = BenchAdapter._Source()
        self.shape, self.day, self.daily_rows = shape, day, daily_rows

    def fetch(self, since: str | None, *, limit: int | None = None) -> Fetched:
        shape, day = self.shape, self.day
        got = records.Records()
        if day == 0:
            for task in range(shape.tasks):
                got.extend(_task_rows(shape, task, "진행"))
            return Fetched(records=got, watermark="d0", complete=True, full=True)
        changed = max(1, self.daily_rows // MODELS_PER_TASK)
        step = max(1, shape.tasks // changed)
        picked = [(day * 7919 + index * step) % shape.tasks for index in range(changed)]
        for number, task in enumerate(picked):
            if number % 20 == 0:
                got.extend(_task_rows(shape, task, "", deleted=True))  # 지운 과제
            else:
                got.extend(_task_rows(shape, task, f"단계{day}"))
        for extra in range(max(1, changed // 20)):  # 새 과제
            got.extend(_task_rows(shape, shape.tasks + day * 100_000 + extra, "신규"))
        return Fetched(records=got, watermark=f"d{day}", complete=True, full=False)


class BenchSubmitter:
    """대상 SP 대신 — 미리 보기는 지문만, 적용은 표시만."""

    def __init__(self, folder: Path) -> None:
        self.file = folder / "bench-applied.json"

    def _applied(self) -> set[str]:
        return set(json.loads(self.file.read_text())) if self.file.exists() else set()

    def preview(self, run: Path, *, origin: dict[str, str] | None = None) -> Plan:
        job = f"job-{run.parent.name}-{run.name}"
        return Plan(job_id=job, ok=True, summary="계획", link="", digest=digest_of(run))

    def applied(self, job_id: str) -> bool | None:
        return job_id in self._applied()

    def apply(self, job_id: str) -> dict[str, Any]:
        self.file.write_text(json.dumps(sorted(self._applied() | {job_id})))
        return {"summary": "적용"}


def _pipeline(folder: Path, shape: Shape, day: int, daily_rows: int) -> Pipeline:
    recipe = folder / "recipe.table.json"
    if not recipe.exists():
        recipe.write_text(json.dumps(RECIPE, ensure_ascii=False), encoding="utf-8")
        keys = "\n".join(f"M{one:07d}" for one in range(shape.models) if one % 10)
        (folder / "models.keys").write_text(keys + "\n", encoding="utf-8")
    return Pipeline(
        slug="bench",
        source="plm",
        adapter=BenchAdapter(shape, day, daily_rows),  # type: ignore[arg-type]
        recipe=recipe,
        target=Target(name="sp", server="", token="", part_mb=64),
        path=folder / "pipeline.json",
    )


def one_run(folder: str, rows: int, day: int, daily_rows: int) -> dict[str, Any]:
    """실행 한 번 — 자식 프로세스에서. 단계별 메모리는 `runner.run` 이 잰다."""
    base = Path(folder)
    pipeline = _pipeline(base, Shape(rows), day, daily_rows)
    work = Workdir(base / "work", "bench")
    started = time.monotonic()
    outcome = runner.run(pipeline, work, BenchSubmitter(base))  # type: ignore[arg-type]
    seconds = time.monotonic() - started
    return {
        "status": outcome.status,
        "seconds": round(seconds, 1),
        "mirror": work.mirror_size(),
        **outcome.memory,
        "mode": work.read_json("trend.json")[-1].get("mode"),
        "compared": work.read_json("trend.json")[-1].get("compared"),
        "plan": runner._totals(outcome.counts) if outcome.counts else None,
    }


def one_apply(folder: str, rows: int) -> str:
    base = Path(folder)
    pipeline = _pipeline(base, Shape(rows), 0, 0)
    work = Workdir(base / "work", "bench")
    return runner.apply(pipeline, work, BenchSubmitter(base)).status  # type: ignore[arg-type]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--rows", type=int, default=580_000)
    parser.add_argument("--days", type=int, default=2, help="첫 적재 뒤 하루치 실행 수")
    parser.add_argument("--daily-rows", type=int, default=20_000)
    parser.add_argument("--folder", type=Path, default=ROOT / ".work" / "bench")
    parser.add_argument("--keep", action="store_true", help="이미 만든 첫 적재를 다시 쓴다")
    args = parser.parse_args()
    folder = args.folder / f"rows-{args.rows}"
    if folder.exists() and not args.keep:
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    report: list[dict[str, Any]] = []
    start_day = 1 if args.keep and (folder / "work" / "bench" / "mirror.json").exists() else 0
    for day in range(start_day, args.days + 1):
        got, _record = isolate.call(
            "relay_scale.one_run", str(folder), args.rows, day, args.daily_rows, label="실행"
        )
        got["day"] = day
        report.append(got)
        stages = " · ".join(f"{k} {v:,.0f}" for k, v in (got.get("memory") or {}).items())
        print(
            f"[{'첫 적재' if day == 0 else f'{day}일차'}] {got['status']} · "
            f"미러 {got['mirror']:,}행 · 변환 {got['mode']} · 비교 {got['compared']} · "
            f"{got['seconds']}초 · 최고 {got.get('peak_mb', 0):,.0f}MB ({stages})",
            flush=True,
        )
        status, _record = isolate.call("relay_scale.one_apply", str(folder), args.rows)
        if status != "applied":
            print(f"  적용: {status}", flush=True)
    (folder / "bench-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
