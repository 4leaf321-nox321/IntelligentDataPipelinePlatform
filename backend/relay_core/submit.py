"""대상 SP 에 올리기 — 미리보기 · 적용 · 「적용됐나」.

미리보기는 틀의 `sp_pipeline.cmd_preview` 와 같은 길이다(검증 → `POST /api/bundles/import`
apply=false → 작업 결과 · 지문을 `preview.json` 에). 그 계획은 대상 SP 의 「작업」 · 검토함
「적용 대기 계획」 에 뜬다. 하나만 다르다 — **무덤만 든 묶음**(원천에서 지워진 것만 있는 날,
나눠 보낸 마지막 조각)을 틀의 검증은 「묶음이 비어 있습니다」 로 거절하지만 SP 는 받는다
(`source` 묶음의 tombstones). 그 한 오류만 넘긴다.

**적용은 묶음을 다시 보내지 않고 그 계획을 적용한다**(`POST /api/jobs/{id}/apply`) — 같은 파일
· 같은 지문으로 SP 가 다시 돌리고, 계획의 `applied_by` 가 채워져 검토함에서 내려간다. 사람이 SP
화면에서 누르든 relay 가 누르든 같은 길이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relay_core import sp
from relay_core.definition import Target


class SubmitError(RuntimeError):
    pass


#: 틀의 검증이 정의 · 객체 · 관계가 없는 묶음에 내는 오류의 앞머리.
EMPTY = "묶음이 비어 있습니다"


def graves_only(run: Any) -> bool:
    """무덤만 든 묶음인가 — 정의 · 객체 · 관계 없이 tombstones 만."""
    empty = run.ontology is None and not run.objects and not run.relations
    return empty and bool(run.tombstones)


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
        loaded = sp.pipeline.load(run)
        errors = list(sp.pipeline.validate(loaded).errors)
        if graves_only(loaded):
            errors = [one for one in errors if not one.startswith(EMPTY)]
        if errors:
            raise SubmitError(
                "검증을 먼저 통과하세요:\n" + "\n".join(f"오류: {one}" for one in errors)
            )
        body = sp.pipeline.payload(loaded)
        digest = str(sp.pipeline.digest(body))
        try:
            result = sp.pipeline._post_bundle(
                self.target.server, self.target.token, {**body, "apply": False}
            )
        except sp.pipeline.Stop as stop:
            raise SubmitError(str(stop)) from None
        sp.pipeline._write(
            run / sp.pipeline.PREVIEW,
            {
                "at": sp.pipeline._stamp(),
                "server": self.target.server,
                "platform": self.target.name,
                "digest": digest,
                "result": result,
            },
        )
        job_id = str(result.get("job_id") or "")
        return Plan(
            job_id=job_id,
            ok=bool(result.get("ok")),
            summary=str(sp.pipeline.summarize(result)),
            link=str(sp.pipeline.jobs_link(self.target.server, job_id)),
            digest=digest,
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
