"""대상 SP 에 올리기 — 미리보기 · 적용 · 「적용됐나」.

미리보기는 틀의 `sp_pipeline.cmd_preview`(검증 → `POST /api/bundles/import` apply=false → 작업
결과 · 지문을 `preview.json` 에)를 그대로 쓴다. 그 계획은 대상 SP 의 「작업」 · 검토함 「적용
대기 계획」 에 뜬다.

**적용은 묶음을 다시 보내지 않고 그 계획을 적용한다**(`POST /api/jobs/{id}/apply`) — 같은 파일
· 같은 지문으로 SP 가 다시 돌리고, 계획의 `applied_by` 가 채워져 검토함에서 내려간다. 사람이 SP
화면에서 누르든 relay 가 누르든 같은 길이다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relay_core import sp
from relay_core.definition import Target


class SubmitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Plan:
    job_id: str
    ok: bool
    summary: str
    link: str
    digest: str


def digest_of(run: Path) -> str:
    """보낼 몸통의 지문 — 대기 중인 계획과 같은 것을 또 올리지 않으려고 견준다."""
    return str(sp.pipeline.digest(sp.pipeline.payload(sp.pipeline.load(run))))


class Submitter:
    def __init__(self, target: Target) -> None:
        self.target = target

    def preview(self, run: Path) -> Plan:
        try:
            ok, summary = sp.pipeline.cmd_preview(
                run,
                server=self.target.server,
                token=self.target.token,
                platform=self.target.name,
            )
        except sp.pipeline.Stop as stop:
            raise SubmitError(str(stop)) from None
        seen = json.loads((run / sp.pipeline.PREVIEW).read_text(encoding="utf-8"))
        job_id = str((seen.get("result") or {}).get("job_id") or "")
        return Plan(
            job_id=job_id,
            ok=bool(ok),
            summary=str(summary),
            link=str(sp.pipeline.jobs_link(self.target.server, job_id)),
            digest=str(seen.get("digest") or ""),
        )

    def applied(self, job_id: str) -> bool | None:
        """그 계획을 어디서든 적용했나 — 못 닿으면 None(모른다)."""
        result = sp.pipeline.plan_applied(self.target.server, self.target.token, job_id)
        return None if result is None else bool(result)

    def apply(self, job_id: str) -> dict[str, Any]:
        """계획을 적용하고 끝날 때까지 본다 — 적용 작업의 결과."""
        headers = {
            "Authorization": f"Bearer {self.target.token}",
            "X-Client": "datarelay",
        }
        url = f"{self.target.server}/api/jobs/{job_id}/apply"
        try:
            status, answer = sp.pipeline._send(
                self.target.server, "POST", url, headers, None, who="대상 SP"
            )
            if status != 202:
                sp.pipeline._refused("대상 SP", status, answer)
            job = sp.pipeline._wait_job(self.target.server, self.target.token, answer)
        except sp.pipeline.Stop as stop:
            raise SubmitError(str(stop)) from None
        result = job.get("result")
        return dict(result) if isinstance(result, dict) else {}
