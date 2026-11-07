"""대용량 시험 — 샘플 PLM 과 같은 모양의 원천 N 행으로 엔진(relay_core)을 끝까지 돌리며 잰다.

    cd backend && .venv/bin/python ../devtools/bench/relay_bench.py 200000

첫 적재 → 적용 → 1% 증분 → 대조 → 값의 출처 → 정리를 단계마다 초 · **그 단계의** 최대
메모리(`VmHWM` 를 단계마다 되돌린다) · 보내는 본문의 크기로 찍는다. **크기마다 새
프로세스로**. 대상 SP 는 가짜(묶음 지문만)라 SP 의 처리 시간은 들지 않는다 — SP 쪽은
`sp_bench.sh`. 원천 · 대상 · 작업 폴더는 임시 폴더에만 쓰고 끝나면 지운다(`KEEP=1` 이면
남긴다 — 20만 행이면 수백 MB). 결과는 docs/개발계획.md 「대용량 시험」.
"""

from __future__ import annotations

import contextlib
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from relay_core import reconcile, runner, sp, trace  # noqa: E402
from relay_core.adapters.base import Fetched  # noqa: E402
from relay_core.definition import Pipeline, Target  # noqa: E402
from relay_core.records import SourceRecord  # noqa: E402
from relay_core.state import Workdir  # noqa: E402

# 대상 SP 대신 — 미리 보기는 지문만, 적용은 표시만(단위 시험의 것 그대로).
from tests.unit.test_relay_rest import FakeSubmitter  # noqa: E402

RECIPE = REPO / "devtools/mock_plm/recipe.table.json"
KINDS = ["01", "02", "03"]
REGIONS = ["KR", "US", "EU"]


def rows(n: int, version: int = 0) -> list[SourceRecord]:
    """모델 n 개 — 과제는 모델 셋마다 하나(프로젝트 · 시험소를 하나씩만), `version` 이면 1% 가
    이름이 바뀐다."""
    out = []
    for i in range(n):
        task = i // 3
        project, site = task // 10, task % 10
        changed = version and i % 100 == 0
        values = {
            "MODEL_ID": f"M-{i:07d}",
            "MODEL_NAME": f"MODEL-{i}" + (" v2" if changed else ""),
            "KIND_CD": KINDS[i % 3],
            "REGION": REGIONS[i % 3],
            "REVISION": f"A{i % 9}",
            "BASE_MODEL": f"M-{i - 1:07d}" if i % 3 == 2 else "",
            "TASK_CODE": f"T-{task:06d}",
            "TASK_NAME": f"과제 {task}",
            "TASK_STATUS": ["PL", "IP", "DN"][task % 3],
            "SRA_DATE": "2026-08-01" if task % 2 else "Skip",
            "PROJECT_CODE": f"P-{project:05d}",
            "PROJECT_NAME": f"프로젝트 {project}",
            "SITE_CODE": f"S-{site:02d}",
            "SITE_NAME": f"시험소 {site}",
            "SITE_REGION": REGIONS[site % 3],
        }
        stamp = f"2026-11-0{1 + version}T00:00:00Z"
        out.append(SourceRecord(key=values["MODEL_ID"], values=values, updated_at=stamp))
    return out


@dataclass
class _Source:
    full_every: int = 0
    deleted: str | None = None


class Adapter:
    source = _Source()

    def __init__(self) -> None:
        self.next: Fetched | None = None

    def fetch(self, since: str | None, *, limit: int | None = None) -> Fetched:
        assert self.next is not None
        got, self.next = self.next, None
        return got


def body_size(send: Path) -> int:
    """보내는 본문 전체의 바이트 — `sp_pipeline.payload` 그대로(출처 · 메모의 `_` 칸을 떼고
    들여쓰기 없이). 조각(`parts/`)은 세지 않는다(같은 줄이다)."""
    body = sp.pipeline.payload(sp.pipeline.load(send))
    return len(json.dumps(body, ensure_ascii=False).encode())


def target_items(run: Path, recipe: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """기준 출력을 대상 SP 의 객체 모양으로 — 대조가 「같음」 을 내게(참조 칸은 상대의 id)."""
    refs = {
        one["type_slug"]: {
            name for name, spec in (one.get("fields") or {}).items() if "key_of" in spec
        }
        for one in recipe["types"]
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for file in (run / "objects").glob("*.json"):
        body = json.loads(file.read_text(encoding="utf-8"))
        slug = body["type_slug"]
        for row in body["rows"]:
            props = {
                k: (f"id-{v}" if k in refs[slug] and v else v)
                for k, v in row.items()
                if k not in ("key", "label") and not k.startswith("_")
            }
            item = {"id": f"id-{row['key']}", "key": row["key"], "label": row.get("label")}
            item |= {"status": "active", "properties": props, "ref_labels": {}}
            out.setdefault(slug, []).append(item)  # 5,000줄씩 나뉜 파일을 합친다
    return out


class Reader:
    def __init__(self, items: dict[str, list[dict[str, Any]]]) -> None:
        self.items = items

    def objects(self, type_slug: str) -> list[dict[str, Any]] | None:
        return self.items.get(type_slug)

    def managed_by(self) -> dict[str, str | None]:
        return {slug: "plm" for slug in self.items}


def _reset() -> None:
    """최대 RSS(`VmHWM`)를 지금 값으로 되돌린다 — 단계마다 **그 단계의** 최대를 잰다(리눅스).
    잰 값에는 앞 단계가 남긴 것(파이썬이 돌려주지 않은 메모리)이 든다 — 그 단계의 위 끝이다."""
    with contextlib.suppress(OSError):
        Path("/proc/self/clear_refs").write_text("5")


def _peak() -> int:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1]) // 1024
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)


def step(name: str, started: float, extra: str = "") -> float:
    """단계 하나의 시간 · 최대 메모리. 시험 틀의 몫(보낸 본문의 크기 재기, 대조의 가짜 대상)은
    단계 밖에서 하고 `_reset` 한다."""
    now = time.perf_counter()
    print(f"  {name:<22} {now - started:7.2f}초 · 최대 {_peak():,}MB  {extra}", flush=True)
    return now


def sent(work: Workdir) -> tuple[int, int]:
    """대기 계획의 본문 크기 · 조각 수(나누지 않았으면 0)."""
    pending = work.load_state().pending
    assert pending is not None
    return body_size(work.path(pending.send)), len(pending.parts)


def main(n: int) -> None:
    root = Path(tempfile.mkdtemp(prefix=f"bench{n}-"))
    adapter = Adapter()
    pipeline = Pipeline(
        slug="bench",
        source="plm",
        adapter=adapter,  # type: ignore[arg-type]
        recipe=RECIPE,
        target=Target(name="sp", server="http://sp.test", token="x"),
        path=root / "p.json",
    )
    work = Workdir(root / "work", "bench")
    fake: Any = FakeSubmitter()
    print(f"N = {n:,} 행 (과제 {n // 3:,} · 프로젝트 {n // 30:,} · 시험소 10) — {root}")

    adapter.next = Fetched(records=rows(n), watermark="w1", complete=True, full=True)
    _reset()  # 받은 행(어댑터가 내는 목록)은 실행의 몫이다 — 진짜 어댑터도 목록으로 낸다
    t = time.perf_counter()
    first = runner.run(pipeline, work, fake)
    t = step("첫 적재(실행)", t, first.status)
    size, cut = sent(work)
    print(f"    본문 {size / 1e6:.1f}MB · 조각 {cut}개", flush=True)
    _reset()
    t = time.perf_counter()
    runner.apply(pipeline, work, fake)
    t = step("첫 적용", t)

    changed = [r for r in rows(n, 1) if r.values["MODEL_NAME"].endswith("v2")]
    adapter.next = Fetched(records=changed, watermark="w2", complete=True, full=False)
    del changed
    _reset()
    t = time.perf_counter()
    again = runner.run(pipeline, work, fake)
    t = step("1% 증분(실행)", t, again.status)
    size, _ = sent(work)
    print(f"    본문 {size / 1e6:.2f}MB", flush=True)
    runner.apply(pipeline, work, fake)

    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    items = target_items(work.path(work.load_state().applied_run or ""), recipe)
    _reset()  # 가짜 대상이 대상 객체를 다 들고 있다 — 대조의 최대에 그 몫이 든다
    t = time.perf_counter()
    report = reconcile.compare(pipeline, work, reader=Reader(items))
    t = step("대조", t, report.summary()[:60])
    del items
    _reset()
    t = time.perf_counter()
    for type_slug, key in (("plm_task", "T-000001"), ("plm_model", f"M-{n - 1:07d}")):
        got = trace.trace(work, RECIPE, type_slug, key)
        found = "있음" if got.found else "없음"
        t = step(f"값의 출처({type_slug})", t, f"원천 {got.rows_total}행 · 기준에 {found}")
    t = step("정리", t, f"{len(work.prune(10))}개")
    mirror = work.path("mirror.json").stat().st_size
    print(f"  미러 파일 {mirror / 1e6:.1f}MB", flush=True)
    if not os.environ.get("KEEP"):
        shutil.rmtree(root)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10_000)
