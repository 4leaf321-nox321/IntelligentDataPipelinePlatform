# datarelay — 데이터 중계

**레거시 시스템(PLM · MES …)의 데이터를 매일 받아, 정해 둔 규칙(레시피)으로 Standard Platform
온톨로지 모양으로 바꿔, 맞는 데이터허브에 넣는다. 그리고 그 연결을 한눈에 보여 준다.**

    PLM (정본) ──┐                              ┌─▶ 자주검증 Datahub (SP) ──▶ 다른 SP 플랫폼들
                 ├─▶ datarelay ── sp-bundle/1 ──┤
    MES (정본) ──┘                              └─▶ 제조 데이터허브 (SP)

- **정본이 아니다** — 정본은 PLM · MES 다. **다른 플랫폼에 뿌리지 않는다** — 그것은 허브가 한다.
  **기준정보를 만들지 않는다** — SP 의 대표 플랫폼이 뿌린다.
- AI 는 매번 데이터를 가공하지 않는다. **AI 는 레시피를 짓고(MCP), 사람이 켜고, 규칙이 매일 돈다.**
  규칙이 못 푼 줄만 사내 LLM(GLM)이 본다.

결정과 그 까닭은 [ADR 0001](docs/adr/0001-허브-앞단의-중계-플랫폼.md), 순서와 남은 일은
[개발 계획](docs/개발계획.md).

## 구성

| 경로 | 내용 |
| --- | --- |
| `backend/relay_core/` | **엔진** — 어댑터 · 미러 · 변환 · 출력 비교. DB 도 웹도 모른다 |
| `backend/app/` | StandardPlatform 틀(v0.4.72 포크) — 로그인 · 권한 · 감사 · 알림 · 작업 워커 · 서버 상태 |
| `frontend/` | 화면 — React 19 · Vite · Tailwind 4 · shadcn. 첫 화면은 연결 지도(3단계) |
| `vendor/sp_pipeline/` | SP 정제 도구(`sp_pipeline.py` · `sp_table.py`)의 **판을 고정한 사본** — 고치지 않는다 |
| `scripts/sync_sp_pipeline.sh` | 그 사본을 SP 태그에서 다시 가져온다(`--check` 는 해시 대조) |
| `docs/adr/` | 이 저장소의 결정 |
| `docs/sp/` | 틀의 문서 · ADR(v0.4.72 시점) — **틀에서 온 코드 주석의 ADR 번호는 여기 것이다** |

## 포트

| | 운영 / 개발 / MCP | Vite |
| --- | --- | --- |
| datarelay | **8080 / 8081 / 8082** | 5250 |

옆 플랫폼: MatNexus 8010 · TestScope 8020 · CrossAXTF 8030 · StandardPlatform 8040 · CompCore 8060 ·
SimEngBay 8070.

## 개발 환경

준비물: Python 3.12 · Node 20 · PostgreSQL 16. (틀과 달리 pgvector · Ollama 는 필요 없다.)

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# DB 두 개 — 이름은 APP_SLUG(datarelay) 그대로. 시험용(_test)까지.
createdb datarelay && createdb datarelay_test

# .env — BOM 없이 UTF-8. 최소: APP_ENV=development · DATABASE_URL · APP_SLUG=datarelay
cp .env.example .env

.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_install.py --email admin --name 관리자
.venv/bin/python run.py                  # 8081 (개발) — 작업 워커를 자식으로 함께 띄운다
```

```bash
cd frontend
npm ci
npm run dev                              # 5250 — /api 를 8081 로 넘긴다
```

## 검증 — 고칠 때마다

```bash
cd backend
.venv/bin/ruff format . && .venv/bin/ruff check .
.venv/bin/mypy                           # strict — app · relay_core · tests
.venv/bin/python -m pytest
.venv/bin/alembic check                  # 모델과 마이그레이션이 같은가

cd ../frontend
npm run lint && npm test && npm run build
```

API 스키마가 바뀌면: `backend/scripts/export_openapi.py` → `frontend` 에서 `npm run api:types`.

## SP 정제 도구의 판 올리기

```bash
scripts/sync_sp_pipeline.sh v0.4.80      # SP 저장소(../StandardPlatform)의 그 태그에서
scripts/sync_sp_pipeline.sh --check      # 사본이 VERSION 의 해시와 같은가
```

사본은 고치지 않는다. 고칠 것은 SP 에서 고친다 — 거기서 백엔드 시험이 실제 API 와 함께 도구를
지킨다(ADR 0001 §5).

## 개발 규칙

[AGENTS.md](AGENTS.md) 가 정본이다(틀의 규칙 그대로 — 온톨로지 절은 이 저장소에 해당하지 않는다).
배포 절차는 틀의 것을 그대로 들고 왔고 아직 relay 에 맞게 고치지 않았다 — [개발 계획](docs/개발계획.md)
「알고 있는 남은 일」.
