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

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from relay_core import bundle, sp
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
    """보낼 몸통의 지문 — 대기 중인 계획과 같은 것을 또 올리지 않으려고 견준다. 흘려서 잰다
    (`bundle.payload` — 틀의 지문과 바이트까지 같고, 실행 폴더를 메모리에 들지 않는다)."""
    return bundle.payload(run).digest


class NotApplied(SubmitError):
    """적용 작업이 끝났지만 대상 SP 가 아무것도 쓰지 않았다 — 적용 시점의 검사에 오류."""


def errors_of(result: Mapping[str, Any]) -> list[str]:
    """SP 계획 결과의 오류 — 번들 · 정의 · 줄마다 있는 것을 모은다."""
    out: list[str] = []

    def walk(found: Any, depth: int = 0) -> None:
        if isinstance(found, dict):
            for name, value in found.items():
                if name in ("errors", "error") and value:
                    rows = value if isinstance(value, list) else [value]
                    out.extend(str(one) for one in rows)
                elif depth < 4:
                    walk(value, depth + 1)
        elif isinstance(found, list) and depth < 4:
            for one in found:
                walk(one, depth + 1)

    walk(dict(result))
    return list(dict.fromkeys(out))


class Submitter:
    def __init__(self, target: Target) -> None:
        self.target = target

    def preview(self, run: Path, *, origin: dict[str, str] | None = None) -> Plan:
        """`origin` — 원천 기록(`runner.origin_for`). 대상 SP 가 묶음 판과 객체의 이력에 남긴다
        (SP ADR 0087). **지문에는 안 넣는다** — 실행마다 번호가 달라, 넣으면 같은 계획을 또
        올리지 않는 비교(`digest`)가 늘 「다르다」 가 된다. 옛 SP 는 모르는 칸이라 무시한다."""
        loaded = sp.pipeline.load(run)
        errors = list(sp.pipeline.validate(loaded).errors)
        if graves_only(loaded):
            errors = [one for one in errors if not one.startswith(EMPTY)]
        if errors:
            raise SubmitError(
                "번들 검증에 실패했습니다. 다음 오류를 수정하세요:\n"
                + "\n".join(f"오류: {one}" for one in errors)
            )
        body = sp.pipeline.payload(loaded)
        digest = str(sp.pipeline.digest(body))
        sent = {**body, "apply": False}
        if origin:
            sent["origin"] = dict(origin)
        try:
            result = sp.pipeline._post_bundle(self.target.server, self.target.token, sent)
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

    def applying(self, job_id: str) -> bool | None:
        """그 계획을 **지금 적용하고 있나** — 적용 작업이 대기 · 실행 중. 못 닿으면 None.

        SP 는 적용을 **시작하면** 계획의 `applied_by` 를 채운다(적용 작업이 대기 · 실행
        중이어도 — 같은 계획을 두 번 넣지 않게). 그래서 「적용됐나」(`applied`)만 보면 반쯤
        들어간 대상을 다 들어간 것으로 안다 — 대조가 그 사이에 읽으면 거짓 어긋남이 난다."""
        plan = self._probe(f"/api/jobs/{job_id}")
        if plan is None:
            return None
        child = str(plan.get("applied_by") or "")
        if not child:
            return False
        job = self._probe(f"/api/jobs/{child}")
        return None if job is None else str(job.get("status")) in ("queued", "running")

    def _probe(self, path: str) -> dict[str, Any] | None:
        """묻기만 하는 GET — 오래 매달리지 않는다(`PROBE_SECONDS`). 못 닿거나 200 이 아니면
        None."""
        headers = {"Authorization": f"Bearer {self.target.token}", "X-Client": "datarelay"}
        url = f"{self.target.server.rstrip('/')}{path}"
        pipeline = sp.pipeline
        try:
            if pipeline.SEND is pipeline._urllib_send:
                status, answer = pipeline._urllib_send(
                    "GET", url, headers, None, timeout=pipeline.PROBE_SECONDS
                )
            else:
                status, answer = pipeline.SEND("GET", url, headers, None)
        except (OSError, ValueError):
            return None
        return answer if status == 200 and isinstance(answer, dict) else None

    def apply(self, job_id: str) -> dict[str, Any]:
        """계획을 적용하고 끝날 때까지 본다 — 적용 작업의 결과. SP 의 적용 작업은 그 시점의
        대상으로 다시 검사해 **오류가 있으면 아무것도 쓰지 않고 「끝남」 으로 끝난다**
        (`applied: false`) — 그때는 `NotApplied`."""
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
        found = dict(result) if isinstance(result, dict) else {}
        if found.get("applied") is False:
            raise NotApplied(
                "대상 SP가 계획을 적용하지 않았습니다(적용 시점의 검사에 오류가 있습니다). "
                + " / ".join(errors_of(found)[:5])
            )
        return found
