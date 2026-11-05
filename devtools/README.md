# devtools — 개발 PC 에서 끝까지 돌려 보기

개발 PC 에는 PLM 도 Datahub 도 없다. 그래서 둘을 흉내 낸다:

| 것 | 자리 | 포트 |
| --- | --- | --- |
| 샘플 PLM API | `mock_plm/` — **OData v4**(`/odata/v4/plm/DevModels`, `$metadata` · `$filter` · `$expand` · `nextLink`)와 같은 데이터의 REST 판. 개발모델 행 안에 과제 · 프로젝트 · 시험 주체가 중첩, 삭제 표시, Bearer | 8088 |
| SP 시험 인스턴스 「자주검증 Datahub(시험)」 | `sandbox_sp/` — **SP 저장소의 코드를 그대로** slug `jvhubtest` · 별도 DB 로 띄운다 | 8087 |

**SP 개발 DB(`standardplatform`)는 건드리지 않는다** — 거기에는 분석 실험 데이터가 있다. 시험
인스턴스는 자기 DB(`jvhubtest`)를 쓴다. 상태 · 토큰 · 로그는 저장소 루트의 `.work/`(커밋 안 함).

## 한 바퀴

```bash
cd ~/projects/IntelligentDataPipelinePlatform

# 1) 샘플 PLM — 앞에서 띄워 둔다(창 하나)
MOCK_PLM_DATA=.work/mock_plm/data.json \
  backend/.venv/bin/python -m uvicorn mock_plm.app:app --app-dir devtools --host 127.0.0.1 --port 8088

# 2) SP 시험 인스턴스 — DB · 마이그레이션 · API · 작업 워커, 그다음 관리자 · relay 토큰 · plm-core 정의
devtools/sandbox_sp/up.sh
devtools/sandbox_sp/setup.sh

# 3) relay
cd backend
export MOCK_PLM_TOKEN=mock-plm-token RELAY_TARGET_TOKEN="$(cat ../.work/sandbox_sp/relay.token)"
.venv/bin/python -m relay_core.cli run   ../devtools/mock_plm/pipeline.json --work ../.work/relay
.venv/bin/python -m relay_core.cli apply ../devtools/mock_plm/pipeline.json --work ../.work/relay

# 4) 원천에서 하루치 변경 → 바뀐 것만 간다
curl -X POST http://127.0.0.1:8088/_mock/scenario/change
.venv/bin/python -m relay_core.cli run   ../devtools/mock_plm/pipeline.json --work ../.work/relay
.venv/bin/python -m relay_core.cli apply ../devtools/mock_plm/pipeline.json --work ../.work/relay

# 5) 대상과 대조 — 허브 쪽에서 바뀐 것을 찾는다(읽기만). --plan 이면 보정 계획(미리보기)까지
.venv/bin/python -m relay_core.cli reconcile ../devtools/mock_plm/pipeline.json --work ../.work/relay

# datarelay 자신(화면 · MCP 까지) — 창 셋. backend/.env 에 MOCK_PLM_TOKEN · RELAY_TARGET_TOKEN 을 적는다
#   backend: .venv/bin/python -m uvicorn app.main:app --port 8081
#   backend: .venv/bin/python -m app.worker          (API 가 뜬 뒤에)
#   mcp_server: venv/bin/python server.py              (8082 — DATARELAY_API=http://127.0.0.1:8081)
#   frontend: npm run dev                              (5250)

# 내리기 — 시험 인스턴스는 PID 로만(같은 PC 의 다른 플랫폼을 건드리지 않게)
devtools/sandbox_sp/down.sh
```

시험 인스턴스 화면은 http://127.0.0.1:8087 — 관리자 계정과 (바뀐) 비밀번호는
`.work/sandbox_sp/admin.json`. 계획은 화면의 「작업」 에서도 적용할 수 있고, 그러면 다음 `run` 이
그것을 알아챈다(`applied_by`).

처음부터 다시: `curl -X POST http://127.0.0.1:8088/_mock/reset` · `rm -rf .work/relay` · 시험 DB 를
지우려면 `down.sh` 뒤 `dropdb jvhubtest` 와 `rm -rf .work/sandbox_sp`.

## 2026-11-05 에 돌려 본 결과

| 단계 | relay | 시험 인스턴스 |
| --- | --- | --- |
| 첫 `run` | 받음 10 → 계획: 프로젝트 2 · 시험 주체 2 · 과제 3 · 개발모델 10 · 파생 선 6, 오류 0 | 「작업」 에 계획 |
| 적용 전 `run` 다시 | `waiting` — 같은 계획이 기다린다, 새로 안 올림 | — |
| `apply` | `POST /api/jobs/{id}/apply` | 객체가 들어가고 계획의 `applied_by` 가 찬다 |
| 하루치 변경 뒤 `run` | 받음 6(원천 행 4 바뀜) → 보낼 것: 모델 고침 1 · 새로 1, 과제 고침 1, 사용 중지 1, 선 끊기 2 | — |
| `apply` | — | M-0005 `deprecated`(지우지 않음), M-0009 의 파생 선 끊김, 나머지 선 그대로 |
| 또 `run` | `nothing` — 보낼 것 없음 | — |

원천 행 넷이 바뀌었는데 모델 객체는 하나만 간 까닭: 그 셋은 **과제 상태**가 바뀐 것이라 과제 객체
하나로 모인다 — 출력끼리 비교하는 이유가 이것이다(ADR 0001 §2).

**겪은 것 — 잠금은 타입에 걸린다.** 정의를 `source` 없는 정의 가져오기로 넣었을 때는 SP 화면 · API 에서
객체를 손으로 고칠 수 있었다(200). `source: plm` 묶음으로 정의를 넣으면 타입이 `managed_by=plm` 이 되고
손 수정이 409(「plm 가 관리하는 타입이라 여기서 수정하지 않습니다」)로 막힌다. `setup.py` 가 그렇게 넣는다.

## MCP 로 새 원천 붙이기(정의의 판)

datarelay(8081) · MCP(8082) · 샘플 PLM 이 떠 있으면, Claude Desktop(또는 MCP 클라이언트)이 시스템 관리자의
PAT 로 붙어 `get_guide('adapter')` 의 차례를 돈다: `source_discover`(OData 면 `$metadata` 의 엔티티 셋부터) →
`pipeline_draft_save` → `pipeline_test` → `recipe_draft_save` → `pipeline_test(recipe_version=N)`. 켜기는 화면
「파이프라인 › 정의 판」 에서 사람이. 개발에서 AI 요청이 닿는 곳은 `127.0.0.1` · `localhost` · 켜진 정의의 주소,
읽을 수 있는 파일 폴더는 `devtools/` 다(`RELAY_SOURCE_HOSTS` · `RELAY_FILE_ROOTS` 로 바꾼다).
