"""GLM 의 제안 — 미해결 작업함의 참조 하나에 **대상의 후보 안에서** 키를 하나 고르게 한다.

ADR 0001 §2 「실행 중의 LLM 은 남은 것만」 의 자리다: OpenAI 호환 클라이언트 하나로 운영은
B300 의 GLM, 개발은 Ollama — 주소 · 모형만 다르다(`RELAY_LLM_URL` · `RELAY_LLM_MODEL`).

- **고를 수 있는 것은 넘긴 후보뿐이다** — 출력을 JSON 스키마로 강제하고(`enum`), 돌아온 키가
  후보에 없으면 버린다. 대상에 없는 키를 지어내지 못한다.
- **확신이 모자라면 고르지 않는다**(`min_confidence`) — 「모르겠다」 도 답이다.
- **제안일 뿐이다** — 사람이 작업함에서 승인해야 relay 사전에 들어간다. 실행 중에 혼자 정하지
  않는다(미해결은 미해결로 멈춘다).
- 원천 표기 · 대상의 키 · 이름이 그 주소로 나간다 — **사내 서빙 주소만** 적는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from relay_core.inbox import Candidate


class LlmError(Exception):
    pass


@dataclass(frozen=True)
class Pick:
    key: str | None
    """고른 키 — 후보에 없거나 확신이 모자라면 None."""
    confidence: float
    reason: str
    note: str = ""
    """고르지 않았으면 왜인지(화면이 그대로 보인다)."""


SYSTEM = (
    "너는 데이터 중계의 참조 대조를 돕는다. 원천 시스템이 적은 표기 하나가 대상 플랫폼의 "
    "어느 객체를 가리키는지, 주어진 후보 중에서만 고른다. 후보의 식별자 · 이름 · 별칭과 "
    "표기를 견주고, 하나로 정해지지 않거나 맞는 것이 없으면 key 를 빈 문자열로 둔다. "
    "짐작하지 않는다. "
    "confidence 는 0~1, reason 은 한국어 한 문장."
)


class Llm:
    def __init__(
        self,
        url: str,
        model: str,
        token: str = "",
        *,
        min_confidence: float = 0.8,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self.token = token
        self.min_confidence = min_confidence
        self.client = client or httpx.Client(timeout=timeout)

    def _ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            answer = self.client.post(
                f"{self.url}/chat/completions", json=payload, headers=headers
            )
        except httpx.HTTPError as failure:
            raise LlmError(f"GLM 에 닿지 않습니다({self.url}): {failure}") from failure
        if answer.status_code != 200:
            raise LlmError(f"GLM 이 거절했습니다({answer.status_code}): {answer.text[:200]}")
        try:
            content = answer.json()["choices"][0]["message"]["content"]
            body = json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as failure:
            raise LlmError(f"GLM 의 답이 정한 모양(JSON)이 아닙니다: {failure}") from failure
        if not isinstance(body, dict):
            raise LlmError("GLM 의 답이 정한 모양(JSON 객체)이 아닙니다.")
        return body

    def pick(
        self,
        *,
        text: str,
        target: str,
        column: str,
        candidates: list[Candidate],
        aliases: dict[str, list[str]] | None = None,
    ) -> Pick:
        """후보 중 하나 — 후보가 없으면 묻지 않는다."""
        if not candidates:
            return Pick(
                None, 0.0, "", note="대상에서 비슷한 후보를 못 찾아 GLM 에 묻지 않았습니다."
            )
        keys = [one.key for one in candidates]
        question = {
            "원천 표기": text,
            "원천 칸": column,
            "대상 타입": target,
            "후보": [
                {
                    "key": one.key,
                    "label": one.label,
                    "aliases": (aliases or {}).get(one.key, []),
                }
                for one in candidates
            ],
        }
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(question, ensure_ascii=False)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "reference_pick",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "key": {"type": "string", "enum": [*keys, ""]},
                            "confidence": {"type": "number"},
                            "reason": {"type": "string"},
                        },
                        "required": ["key", "confidence", "reason"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        body = self._ask(payload)
        key = str(body.get("key") or "")
        try:
            confidence = float(body.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        reason = str(body.get("reason") or "")[:300]
        if not key:
            return Pick(
                None,
                confidence,
                reason,
                note="GLM 이 고르지 않았습니다 — 맞는 후보가 없다고 봅니다.",
            )
        if key not in keys:
            return Pick(
                None,
                confidence,
                reason,
                note=f"GLM 이 후보에 없는 키({key!r})를 내 버렸습니다.",
            )
        if confidence < self.min_confidence:
            return Pick(
                None,
                confidence,
                reason,
                note=f"GLM 의 확신({confidence:.2f})이 기준({self.min_confidence:.2f})보다 "
                f"낮아 고르지 않았습니다 — 참고: {key}",
            )
        return Pick(key, confidence, reason)
