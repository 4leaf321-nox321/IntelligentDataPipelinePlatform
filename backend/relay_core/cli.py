"""`relay` 명령 — 화면 없이 한 바퀴를 돌린다(3단계의 「지금 실행」 과 같은 함수).

    python -m relay_core.cli run    <정의.json> [--work DIR]   받기 → 비교 → 미리보기
    python -m relay_core.cli apply  <정의.json> [--work DIR]   대기 중인 계획 적용
    python -m relay_core.cli status <정의.json> [--work DIR]   워터마크 · 기준 · 대기 계획

끝나는 값: 0 정상(보낼 것 없음 · 올림 · 적용) · 1 사람이 볼 것(미해결 · 계획 오류) · 2
멈춤(원천 · 대상 · 정의 오류). 야간 작업의 경보로 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from relay_core import runner
from relay_core.adapters.rest import SourceError
from relay_core.definition import DefinitionError, load
from relay_core.state import Workdir
from relay_core.submit import SubmitError

DEFAULT_WORK = Path(os.environ.get("DATARELAY_WORK", Path.cwd() / ".work"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="relay")
    parser.add_argument("command", choices=["run", "apply", "status"])
    parser.add_argument("definition", type=Path)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    parser.add_argument(
        "--human-edits",
        choices=["keep", "overwrite"],
        default=None,
        help=(
            "대상 SP 에서 사람이 고친 칸 — keep(기본, 비켜 가고 계획에 적음) · "
            "overwrite(원천 값으로 덮음)"
        ),
    )
    args = parser.parse_args(argv)
    try:
        pipeline = load(args.definition)
        work = Workdir(args.work, pipeline.slug)
        if args.command == "status":
            state = work.load_state()
            print(
                json.dumps(
                    {
                        "watermark": state.watermark,
                        "applied_run": state.applied_run,
                        "applied_at": state.applied_at,
                        "pending": state.pending.__dict__ if state.pending else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "run":
            options = {"human_edits": args.human_edits} if args.human_edits else None
            outcome = runner.run(pipeline, work, options=options)
        else:
            outcome = runner.apply(pipeline, work)
    except (DefinitionError, SourceError, SubmitError) as failure:
        print(f"멈춤: {failure}", file=sys.stderr)
        return 2
    print(f"[{outcome.status}] {outcome.message}")
    if outcome.link:
        print(f"계획: {outcome.link}")
    if outcome.counts:
        print(json.dumps(outcome.counts, ensure_ascii=False))
    return 1 if outcome.status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
