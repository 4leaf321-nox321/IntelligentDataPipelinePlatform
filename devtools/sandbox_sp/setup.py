"""SP 시험 인스턴스를 relay 가 쓸 수 있게 세운다 — 관리자 · PAT · `plm-core` 정의.

    1. 관리자 로그인(시드가 만든 계정). 첫 로그인이면 비밀번호를 바꾼다.
    2. relay 용 PAT — `read` · `objects:write` · `ontology:write`.
       토큰은 .work/sandbox_sp/relay.token(600).
    3. `plm-core.json`(SP 저장소의 것)을 **`source: plm` 묶음으로** 넣는다 — 그래야 타입이
       `managed_by=plm` 이 되어 SP 화면에서 사람이 못 고친다(정본은 PLM). `source` 없는 정의
       가져오기로 넣으면 잠기지 않는다(2026-11-05 시험에서 겪었다). 고를 값이 빈 `enum` 칸은
       샘플 PLM 의 값으로 채운다.

다시 돌려도 된다 — 토큰이 살아 있으면 그대로 쓰고, 정의 가져오기는 더하고 고치기만 한다.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import httpx

WORK = Path(os.environ["SANDBOX_WORK"])
URL = os.environ["SANDBOX_URL"]
SP_REPO = Path(os.environ["SP_REPO"])
ADMIN = WORK / "admin.json"
TOKEN = WORK / "relay.token"
#: 원천 이름 — relay 파이프라인의 `source` 와 같아야 relay 의 묶음이 잠긴 타입을 고칠 수 있다.
SOURCE = "plm"

#: 샘플 PLM 이 내는 값(`devtools/mock_plm`). 빈 enum 을 그대로 넣으면 고를 값이 없어 모든
#: 줄이 오류다.
ENUMS = {
    ("plm_task", "task_status"): ["계획", "진행", "완료"],
    ("plm_model", "model_kind"): ["양산", "선행", "파생"],
    ("plm_model", "region"): ["KR", "US", "EU"],
}


def _secret_file(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o600)


def _admin() -> dict[str, str]:
    return dict(json.loads(ADMIN.read_text(encoding="utf-8")))


def _login(client: httpx.Client) -> str:
    admin = _admin()
    got = client.post(
        "/api/auth/login", json={"email": admin["email"], "password": admin["password"]}
    )
    got.raise_for_status()
    body = got.json()
    if body.get("must_change_password"):
        fresh = secrets.token_urlsafe(18)
        changed = client.post(
            "/api/auth/change-password",
            json={"current_password": admin["password"], "new_password": fresh},
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        changed.raise_for_status()
        _secret_file(ADMIN, json.dumps({**admin, "password": fresh}))
        return _login(client)
    return str(body["access_token"])


def _token(client: httpx.Client) -> str:
    if TOKEN.exists():
        token = TOKEN.read_text(encoding="utf-8").strip()
        if (
            client.get(
                "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 200
        ):
            print("relay 토큰: 그대로 씁니다")
            return token
    access = _login(client)
    made = client.post(
        "/api/auth/tokens",
        json={
            "name": "datarelay(시험)",
            "expires_in_days": 30,
            "scopes": ["read", "objects:write", "ontology:write"],
        },
        headers={"Authorization": f"Bearer {access}"},
    )
    made.raise_for_status()
    token = str(made.json()["token"])
    _secret_file(TOKEN, token)
    print("relay 토큰: 새로 발급했습니다")
    return token


def _ontology() -> dict[str, Any]:
    core = json.loads(
        (SP_REPO / "pipeline" / "core" / "plm-core.json").read_text(encoding="utf-8")
    )
    for one in core.get("types", []):
        for prop in one.get("properties", []):
            options = ENUMS.get((one["slug"], prop["key"]))
            if prop.get("data_type") == "enum" and not prop.get("enum_options"):
                prop["enum_options"] = options or ["(시험)"]
    return dict(core)


def _wait(client: httpx.Client, head: dict[str, str], job: dict[str, Any]) -> dict[str, Any]:
    for _ in range(120):
        got = client.get(f"/api/jobs/{job['id']}", headers=head).json()
        if got["status"] in ("done", "failed", "cancelled"):
            return dict(got)
        time.sleep(1)
    raise RuntimeError("작업이 끝나지 않습니다 — 시험 인스턴스의 워커가 떠 있나요(up.sh)?")


def main() -> int:
    with httpx.Client(base_url=URL, timeout=60) as client:
        token = _token(client)
        head = {"Authorization": f"Bearer {token}"}
        body = {"ontology": _ontology(), "source": SOURCE}
        for apply in (False, True):
            sent = client.post(
                "/api/bundles/import", json={**body, "apply": apply}, headers=head
            )
            if sent.status_code != 202:
                print(
                    f"정의 묶음이 거절됐습니다: {sent.status_code} {sent.text}",
                    file=sys.stderr,
                )
                return 1
            job = _wait(client, head, sent.json())
            if job["status"] != "done" or not (job.get("result") or {}).get("ok", True):
                print(
                    f"정의 묶음이 실패했습니다: {json.dumps(job, ensure_ascii=False)[:800]}",
                    file=sys.stderr,
                )
                return 1
        schema = client.get("/api/ontology/schema", headers=head).json()
        locked = sorted(
            f"{one['slug']}(managed_by={one.get('managed_by') or '-'})"
            for one in schema.get("types", [])
            if one["slug"].startswith("plm_")
        )
        print(f"정의: {', '.join(locked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
