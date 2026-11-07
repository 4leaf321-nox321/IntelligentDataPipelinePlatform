"""`relay` 명령 — 화면 없이 한 바퀴를 돌린다(3단계의 「지금 실행」 과 같은 함수).

    python -m relay_core.cli run    <정의.json> [--work DIR]   받기 → 비교 → 미리보기
                                       [--accept]   받기 점검의 멈춤을 확인하고 진행
    python -m relay_core.cli apply  <정의.json> [--work DIR]   대기 중인 계획 적용
    python -m relay_core.cli status <정의.json> [--work DIR]   워터마크 · 기준 · 대기 계획
    python -m relay_core.cli check  <정의.json>                이 서버에서 원천 · 대상에 닿나
    python -m relay_core.cli reconcile <정의.json> [--plan]    대상과 대조 · 보정 계획
    python -m relay_core.cli trace <정의.json> --type T --key K 값의 출처(거꾸로 보기, JSON)

끝나는 값: 0 정상(보낼 것 없음 · 올림 · 적용 · 대상과 같음) · 1 사람이 볼 것(미해결 · 계획
오류 · 대상과 어긋남) · 2 멈춤(원천 · 대상 · 정의 오류). 야간 작업의 경보로 쓴다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from relay_core import intake, reconcile, runner, trace
from relay_core.adapters.rest import SourceError
from relay_core.definition import DefinitionError, Pipeline, load
from relay_core.state import Workdir
from relay_core.submit import SubmitError

DEFAULT_WORK = Path(os.environ.get("DATARELAY_WORK", Path.cwd() / ".work"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="relay")
    parser.add_argument(
        "command", choices=["run", "apply", "status", "check", "reconcile", "trace"]
    )
    parser.add_argument("definition", type=Path)
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK)
    parser.add_argument(
        "--plan", action="store_true", help="reconcile: 어긋난 줄의 보정 계획까지 생성한다"
    )
    parser.add_argument("--type", dest="type_slug", help="trace: 대상 타입 slug")
    parser.add_argument("--key", help="trace: 그 타입 안의 식별자")
    parser.add_argument(
        "--human-edits",
        choices=["keep", "overwrite"],
        default=None,
        help=(
            "대상 SP 에서 사람이 수정한 칸 — keep(기본, 비켜 가고 계획에 적음) · "
            "overwrite(원천 값으로 덮음)"
        ),
    )
    parser.add_argument(
        "--accept",
        action="store_true",
        help="run: 받기 점검이 멈춘 것(대량 삭제 · 빈 칸)을 확인하고 그만큼까지 진행한다",
    )
    args = parser.parse_args(argv)
    try:
        pipeline = load(args.definition)
        if args.command == "check":
            return check(pipeline)
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
        if args.command == "trace":
            if not args.type_slug or not args.key:
                print("멈춤: trace 에는 --type 과 --key 가 필요합니다.", file=sys.stderr)
                return 2
            got = trace.trace(work, pipeline.recipe, args.type_slug, args.key)
            print(json.dumps(got.to_json(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "reconcile" and not args.plan:
            report = reconcile.compare(pipeline, work)
            print(report.summary())
            for slug, one in report.types.items():
                if one.drift:
                    print(
                        f"  {slug}: 빠짐 {one.missing[:5]} · 안 씀 {one.deprecated[:5]} · "
                        f"다름 {sorted(one.changed)[:5]} · 대상에만 {one.extra[:5]}"
                    )
            return 2 if report.problems else (1 if report.drift else 0)
        if args.command == "reconcile":
            outcome = reconcile.resync(pipeline, work)
        elif args.command == "run":
            options = {"human_edits": args.human_edits} if args.human_edits else None
            accept = None
            if args.accept:
                accept = intake.Accept.of(work.load_state().held, by="relay CLI")
                if accept is None:
                    print("멈춤: 받기 점검에서 멈춘 것이 없습니다.", file=sys.stderr)
                    return 2
            outcome = runner.run(pipeline, work, options=options, accept=accept)
        else:
            outcome = runner.apply(pipeline, work)
    except (
        DefinitionError,
        SourceError,
        SubmitError,
        reconcile.ReconcileError,
        trace.TraceError,
    ) as failure:
        print(f"멈춤: {failure}", file=sys.stderr)
        return 2
    print(f"[{outcome.status}] {outcome.message}")
    if outcome.link:
        print(f"계획: {outcome.link}")
    if outcome.counts:
        print(json.dumps(outcome.counts, ensure_ascii=False))
    return 1 if outcome.status == "blocked" else 0


def check(pipeline: Pipeline) -> int:
    """**이 서버에서** 원천 · 대상에 닿나 — relay 를 둘 서버를 고를 때 · 설치 뒤 처음에 돌린다.

    원천에는 한 줄만 묻고, 대상에는 「누구인가」(`/api/health` 의 slug)와 「이 토큰이 사는가」
    (`/api/auth/me`)만 묻는다. 아무것도 바꾸지 않는다."""
    import httpx

    ok = True
    try:
        print(f"원천  {pipeline.source:8s} {pipeline.adapter.probe()}")
    except SourceError as failure:
        ok = False
        print(f"원천  {pipeline.source:8s} 실패 — {failure}")
    target = pipeline.target
    try:
        health = httpx.get(f"{target.server}/api/health", timeout=10).json()
        me = httpx.get(
            f"{target.server}/api/auth/me",
            headers={"Authorization": f"Bearer {target.token}"},
            timeout=10,
        )
        who = (
            me.json().get("email") if me.status_code == 200 else f"토큰 거절({me.status_code})"
        )
        admin = me.status_code == 200 and me.json().get("is_system_admin")
        print(
            f"대상  {target.name or target.server} — slug {health.get('slug')} · 판 "
            f"{health.get('version')} · 토큰 {who}"
            + ("" if admin else " · ⚠ 시스템 관리자가 아님")
        )
        ok = ok and me.status_code == 200 and bool(admin)
    except (httpx.HTTPError, ValueError) as failure:
        ok = False
        print(f"대상  {target.server} 실패 — {failure}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
