# [참고] HWAX포탈 연동 요청서 — {{NAME}}(`{{ID}}`)

<!-- 틀: 시작 -->
> **이 파일은 틀(템플릿)이다 — 그대로 보내지 않는다.** 같은 코드로 띄운 인스턴스(「자주검증 Datahub」 ·
> 「Failure Trace」 …)가 각자 한 장씩 보낸다. 그 설치의 서버에서 `sudo APP_SLUG=<slug> ./deploy.sh hwax-request`
> 를 치면 아래의 자리(이름 · slug · 포트 · 접두)를 그 설치의 `.env` · 설치 기록 값으로 채워
> `~/apps/<slug>/HWAX-연동요청서.md` 에 쓴다 — 이 상자(틀 설명)는 빠진다. 위임 비밀(`HEAX_SSO_SECRET`)이 없으면
> 그때 만들어 `.env` 에 적고, **비밀과 내부 주소는 파일에 쓰지 않는다**(담당에게 따로).
>
> 자리: 이름(ID) = `APP_SLUG` · NAME = `APP_NAME` · TAGLINE = `APP_TAGLINE` · APP_PORT · MCP_PORT(보통 앱 +2) ·
> PREFIX = slug 를 대문자로(하이픈은 밑줄로) · DATE · VERSION. 결정과 까닭은
> [ADR 0028](adr/0028-HWAX-포털에-붙이기.md), 우리 쪽 정리는 [HWAX-PORTAL-INTEGRATION](HWAX-PORTAL-INTEGRATION.md),
> 우리 서버에서 치는 순서는 운영 안내서 9절이다. 위험 도구 목록(7절)은 `mcp_server/server.py` 의
> `DESTRUCTIVE_TOOLS` 와 같아야 한다(MCP 시험이 견준다).
<!-- 틀: 끝 -->

> **보내는 곳** {{NAME}}(`{{ID}}`) 담당 → **받는 곳** HWAX 포털 담당 · {{DATE}} · 판 {{VERSION}}
>
> **Report Archive(RA)를 붙였을 때와 같은 일**을 요청한다 — 화면 라우트 · 큰 요청 갈래 · 타일(jwt-handoff) · 게이트웨이
> MCP 백엔드와 사람별 위임(`per_user_sso`) · 권한 표 · JWKS 주소. 포털 저장소에 우리 전용 코드는 생기지 않는다.
> 같은 코드로 띄운 인스턴스가 여럿이라(같은 서버쌍에 함께 뜰 수 있다) **요청서는 인스턴스마다 한 장, 값만 다르다** —
> 9절의 목록형 단계를 한 번 만들어 두면 그다음 인스턴스부터는 RA 의 `RA_HOST` 처럼 주소 한 줄이다.
>
> **내부 주소(A · B 의 IP)와 위임 비밀은 이 문서에 없다 — 담당에게 따로 전한다.** 아래 `<A 주소>` 가 그 자리다.

## 0. 한눈에

| 항 | 포털 · 게이트웨이 쪽 변경 | RA 에서는 |
| --- | --- | --- |
| 2 | 이름은 하나 — `{{ID}}` (경로 id = 타일 id = audience = 게이트웨이 키 = 위임 키 = 권한 plat id) | `report-archive` · `reportarchive` 둘 |
| 3 | `routes.local.env` 의 `{{ID}}=http://<A 주소>:{{APP_PORT}}/` · `loc_extras()` 에 `{{ID}}` | 1e 가 `RA_HOST` 로 유도 · 같은 갈래 |
| 4 | `systems.yaml` 타일(jwt-handoff) · 포털 JWKS 주소를 우리에게 | 같다 |
| 5 | 게이트웨이 MCP 백엔드 `{{ID}}` + 사람별 위임 `PER_USER_SSO_APPS` 의 `{{ID}}:{{PREFIX}}` | `RA_MCP_URL` · `RA_SSO_SECRET` |
| 6 | `access.local.yaml` 의 플랫폼 `{{ID}}` | `access.yaml` 의 `reportarchive` |
| 7 | 우리 위험 도구를 게이트웨이 `_INVOKE_DENY_EXACT` 에(**한 번** — 인스턴스가 몇이든) | RA 도구(`trash_report` · `publish_report` …)가 이미 있다 |
| 8 | B 전환 — 사람이 주소를 바꾸고 `update-all` 다시 | 같다 |
| 9 | **(권장)** `update-all` 에 목록형 단계 한 번 — 그 뒤로 인스턴스마다 주소 한 줄 | 1e 와 같은 일 |
| 12 | 요청하지 않는 것 — `/{{ID}}/mcp` 라우트 · 포털 「연결」(PAT 등록부) · B 자동 전환 | RA 도 안 한다 |

## 1. 주소와 방화벽

| | |
| --- | --- |
| 공개 주소 | `https://hwax.sec.samsung.net/{{ID}}/` |
| 서버 | 서버쌍 **A(주) · B(대기)** — 둘 다 앱이 떠 있고 DB 는 주에서 대기로 복제된다. 같은 서버쌍에 다른 인스턴스가 함께 뜰 수 있다(포트가 다르다). **내부 주소는 담당에게 따로** |
| 앱 | `:{{APP_PORT}}` — 한 프로세스가 화면 + API(`/api/…`). 포털 nginx 는 **접두어를 벗겨** 넘긴다(끝 `/`) — 우리 `PUBLIC_PATH=/{{ID}}` 가 화면 · 쿠키 · 돌아갈 곳을 그 아래로 맞춘다 |
| 포털 로그인 콜백 | `/{{ID}}/api/auth/portal-callback` — POST, 폼 필드 `token`(포털 기본 `handoff_param`) |
| 사람별 위임 발급 | `http://<A 주소>:{{APP_PORT}}/api/auth/sso` — 게이트웨이가 **직접** 부른다(포털 nginx 를 안 거친다 · 접두어 없음) |
| MCP | `http://<A 주소>:{{MCP_PORT}}/mcp`(streamable-http) — **게이트웨이만** 부른다. 포털 nginx 경로로는 열지 않는다(RA 와 같다) |
| 방화벽 | 포털 박스(nginx · 게이트웨이) → A · B 의 `:{{APP_PORT}}` · `:{{MCP_PORT}}`. A · B → 포털 `:8088`(JWKS — 4절) |

## 2. 이름은 하나로 — `{{ID}}`

이 인스턴스의 slug(`APP_SLUG`)를 **모든 자리에 그대로** 쓴다: 포털 경로 id · 타일 id · 토큰 `audience` · 게이트웨이 백엔드 키 ·
사람별 위임 키(`per_user_sso`) · 권한 표의 플랫폼 id. 갈리면 조용히 안 된다:

- 게이트웨이는 **위임 키 = 백엔드 키**일 때만 사람별 위임을 탄다 — 다르면 그 백엔드를 서비스 신분(토큰 없음)으로 불러 전부 401 이다.
- 토큰의 `aud` 가 우리 `PORTAL_AUDIENCE`(기본 slug)와 다르면 서명이 맞아도 「다른 앱에 낸 토큰」 으로 거절한다.
- 게이트웨이는 겹치는 도구 이름에 **키에서 하이픈을 뺀 것**을 접두어로 단다 — 키에 하이픈이 있으면 접두어(`…_objects_list`)와
  키가 글자로 달라 찾기 어렵다. slug 는 소문자 · 숫자 한 덩어리라 하이픈이 없다 — **그대로 쓰면 된다.**

## 3. 화면 라우트

`backend/config/routes.local.env`(gitignore) — 한 줄:

```bash
{{ID}}=http://<A 주소>:{{APP_PORT}}/        # 끝 / = 접두어 STRIP — 앱은 /api/… 로 받는다
```

RA 는 이 줄을 1e 가 `RA_HOST` 로 유도한다. 인스턴스가 여럿이면 9절의 목록형 단계가 같은 일을 한다 — 그 전까지는 손으로 한 줄.

`infra/scripts/gen-nginx-conf.sh` 의 `loc_extras()` — RA 와 같은 갈래에 id 하나:

```bash
    # {{ID}}: 사진 일괄 업로드 zip 2GB · 내보내기 결과 수백 MB · 큰 계획(일괄 입력 · 검토함 미리 보기)이 요청 안에서 수 분
    ste|apps|heax-hub|report-archive|odb-hub|{{ID}})      # ← 기존 줄에 id 하나
      ...(client_max_body_size 2048m · proxy_request_buffering off · proxy_buffering off · proxy_read_timeout 600s — 그대로)
```

없으면 업로드가 포털 디스크(`/tmp`)에 한 벌 쌓인 뒤 넘어와 진행률이 멈춘 듯 보이고, 60초를 넘는 요청은 504 로 끊긴다.
정확히 2GiB 짜리 zip 은 본문 머리 몫만큼 2048m 을 넘어 413 이 난다 — 실사용에는 지장이 없다(맞추려면 그 갈래만 2100m).

## 4. 포털 로그인(jwt-handoff)

### 4-1. `backend/config/systems.yaml` 타일

로그인 토큰을 넘기는 타일은 박스 파일(`systems.local.yaml`)로 만들 수 없다 — 추적 파일에 한 블록:

```yaml
  - id: {{ID}}
    name: {{NAME}}
    tagline: {{TAGLINE}}
    description: >-
      {{NAME}} — 타입 · 속성 · 관계로 엮은 사내 데이터 플랫폼. 목록 · 상세 · 지식 그래프 · 지표로 보고,
      Claude(포털 게이트웨이)로 묻고 채웁니다.
    accent: teal
    category: Data
    status: available
    # 포털 경유 프록시 + 타일 클릭 SSO. 목적지는 routes.local.env 의 {{ID}}= 이다(3절).
    # 아래 url 은 프록시 목적지가 아니라 launch 콜백 주소다.
    integration_type: jwt-handoff
    audience: {{ID}}          # 토큰 aud — 우리 PORTAL_AUDIENCE(기본 APP_SLUG)와 글자 단위로 같다
    url: /{{ID}}/api/auth/portal-callback
    # 라우트가 없는 박스에서는 숨긴다 — 켜 두면 클릭이 포털 자신에게 떨어져 405 가 뜬다.
    hide_unless_routed: true
    sort_order: 70
```

토큰은 포털 기본 그대로다 — `iss` = `JWT_ISSUER`(기본 `https://hwax.sec.samsung.net`) · `aud` · `scope: "launch"` · 90초 · `jti` ·
머리의 `kid`. 포털의 `JWT_ISSUER` 를 기본값에서 바꾼 박스라면 그 값을 알려 달라(우리 `PORTAL_ISSUER`). 우리 로그인 화면의
「HWAX 포털로 로그인」 은 `https://hwax.sec.samsung.net/launch/{{ID}}` 로 보낸다 — 포털 쪽에 따로 할 일은 없다.

**포털 안에서 바로 들어온 사람의 자동 로그인**(RA 와 같다) — 북마크 · 링크로 `/{{ID}}/` 에 바로 오면 우리 화면이 포털의
표준 launch(`POST /systems/{{ID}}/launch`, 포털 세션 · `X-CSRF-Token`)를 불러 토큰을 받고 우리 `/api/auth/portal-exchange`
로 바꾼다. 위 타일과 권한 표(6절)가 있으면 되고 **포털 쪽에 새로 할 일은 없다.**

### 4-2. 포털 JWKS 주소를 우리에게

A · B 가 포털의 공개 키를 받아 갈 주소 — **우리 서버에서 닿는 주소**라 포털이 확정해 알려 줘야 한다(RA 요청서 4-2 와 같다).
사내망 http `http://<포털 박스>:8088/.well-known/jwks.json` 이 간단하다(우리 쪽에 CA 가 필요 없다). 받으면 우리 `.env` 의
`PORTAL_JWKS_URL` 에 적는다.

## 5. MCP — 게이트웨이 백엔드와 사람별 위임(RA 와 같게)

사람들의 Claude 는 **포털 게이트웨이**(`https://hwax.sec.samsung.net/mcp-gw/mcp`, 포털 개인 토큰)에만 붙는다. 게이트웨이가
우리 MCP 를 부르고, 사람마다 우리 `POST /api/auth/sso` 로 **그 사람 명의의 토큰**을 받아 싣는다 — 우리 쪽 권한 · 부서 ·
감사가 그 사람으로 걸린다. 사람이 우리 토큰을 발급해 포털에 붙여 넣는 일(포털 「연결」)은 없다.

### 5-1. 백엔드 항목 — `gateway_config.json` 에 손으로(재생성 때 보존된다)

```json
"{{ID}}": {
  "url": "http://<A 주소>:{{MCP_PORT}}/mcp",
  "transport": "streamable_http",
  "label": "{{NAME}}",
  "description": "{{NAME}} — 사내 데이터 플랫폼의 온톨로지(타입 · 객체 · 관계 · 지표)를 읽고 채운다. 사람별 위임으로 그 사람의 권한만큼."
}
```

- **서비스 토큰 없이** 둔다 — 도구 목록은 인증 없이 받는다(TestScope 와 같다). 신원 없는 호출은 우리 백엔드가 401 로 거절한다.
- 권한 표(6절)를 **먼저** 적는다 — 표에 없는 백엔드는 게이트웨이가 「전체 공개」 로 본다.
- (선택) `tool_areas.json` 의 `apps` 에 `"{{ID}}": "data"` — 없으면 도구 선택 화면에 「미분류」 로 뜬다.

### 5-2. 사람별 위임 — 게이트웨이 `provision.env`

```bash
PER_USER_SSO_APPS="… {{ID}}:{{PREFIX}}"                       # 이미 있는 쌍 뒤에 공백으로 — 위임 키 = 백엔드 키(5-1)
{{PREFIX}}_SSO_SECRET=<비밀 — {{NAME}} 담당이 따로 전한다>
{{PREFIX}}_SSO_URL=http://<A 주소>:{{APP_PORT}}/api/auth/sso
```

그다음 `./infra/scripts/update-all.sh` — §5 가 재프로비저닝해 `per_user_sso.{{ID}}`(`client: "gateway"`)를 만든다. 우리 쪽은 이 요청서를
보낼 때 이미 켜 두었다(10-2 의 첫 `curl` 이 400) — 위임 쌍이 생기는 순간부터 게이트웨이는 우리를 **위임으로만** 부른다.
되돌리기: 쌍과 두 줄을 지우고 `update-all`(`PER_USER_SSO_OFF` 로 위임이 지워진다).

### 5-3. 우리가 지키는 위임 계약

| | |
| --- | --- |
| 요청 | `POST /api/auth/sso`, 본문 없음. 머리 `X-Heax-Gateway-Secret`(상수 시간 비교) · `X-Hwax-Delegate-Email`(먼저) 또는 `X-Heax-User-Email` · `X-Heax-Client` · `X-Heax-User-Name`(받되 쓰지 않는다) |
| 200 | `{"access_token": "{{ID}}_pat_…", "token_type": "bearer", "expires_in": 86400}` — 봉투 없이. 그 사람 명의의 **개인 토큰**(하루). 게이트웨이 캐시는 12시간 |
| 거절 | 위임 꺼짐 **404 JSON** · 비밀 다름 401 · 이메일 머리 없음 400 · 처음 보는 사람 · 승인 대기 · 정지 · 삭제 · 허용 주소 밖 403. 404 밖의 거절 본문은 **사람이 읽을 한국어 한 줄**(text/plain, 끝에 오류 코드) — 게이트웨이가 그대로 그 사람에게 보인다 |
| 계정 | 이메일(소문자)로 찾는다. **처음 보는 사람을 만들지 않는다** — 거절 본문이 「포털에서 「{{NAME}}」 을 한 번 열면 가입 신청이 된다」 를 말한다(타일 로그인이 가입 신청을 만들고 우리 관리자가 승인한다) |
| 토큰 | 이름 `HWAX 게이트웨이(<client>)` · 범위 `read` + 쓰기 범위(관리 범위 없음) · **같은 client 의 직전 토큰은 회수**한다. 게이트웨이를 둘 이상 띄우면 서로의 토큰을 회수한다(401 → 한 번 다시 받기로 이어진다) |
| 401 신호 | 토큰이 거절되면 우리 MCP 도구는 `Error executing tool <이름>: GET /api/… → HTTP 401: …` 로 실패한다 — 게이트웨이 `_TOKEN_REJECTED` 에 맞아 한 번 다시 받는다 |
| 함께 오는 머리 | `X-Heax-User-Affiliation` · `X-Heax-Aff-Proof` 는 쓰지 않는다(우리 공유는 부서가 정한다) |
| 회수 | `POST /api/auth/sso/revoke`(같은 머리) → `{"revoked": n}` — 포털이 사람을 정지할 때 불러도 된다(선택) |
| 점검 | `POST /api/auth/sso/verify`(같은 머리 — 이메일은 안 본다) → 본문 없는 **204**. 토큰 · 계정을 건드리지 않는다 — 연결 설정(주소 · 비밀)만 볼 때. 거절은 꺼짐 404 JSON · 허용 밖 403 · 비밀 401(세 플랫폼 공통 계약) |
| 주소 제한 | 우리 쪽 선택 `HEAX_SSO_ALLOWED_IPS`(포털 박스 주소) — TCP 원격 주소로만 본다(프록시 머리를 믿지 않는다) |

### 5-4. 도구 이름 — 접두어 `{{ID}}_`

우리 MCP 는 도구 93개를 낸다(`search` · `whoami` · `objects_list` · `ontology_schema` …). 같은 코드로 띄운 인스턴스는 **이름이 전부
같다** — 게이트웨이 규칙대로 겹친 것은 양쪽 모두 `<키>_<도구>` 로 노출되므로, 인스턴스가 둘 이상이면 93개 전부 `{{ID}}_objects_list`
꼴이 된다(하나뿐이어도 다른 앱과 겹치는 `search` 같은 것은 붙는다). `{{ID}}_<도구>` 별칭으로는 늘 부를 수 있다. 우리 쪽에서 할 것은 없다.

## 6. 권한 표 — `backend/config/access.local.yaml`(박스 파일)

```yaml
platforms:
  - id: {{ID}}
    label: {{NAME}}
    systems: [{{ID}}]       # 타일(4-1) — 허가가 있는 사람에게만 보인다
    gateway: [{{ID}}]       # 게이트웨이 백엔드(5-1) — 허가가 있는 사람에게만 도구가 붙는다
```

게이트웨이 백엔드를 붙이기 **전에** 적는다. 플랫폼 허가(`plat:{{ID}}`)는 포털이 정한다(소속 `CAEG` 는 `"*"` 라 이미 전부).

## 7. 위험 도구 — 게이트웨이 `_INVOKE_DENY_EXACT` 에

게이트웨이의 범용 실행기(`invoke_tool`)는 이름이 `delete_` · `remove_` · `cancel_` 로 **시작**하는 도구를 막는데, 우리 이름은
`objects_delete` · `relation_remove` · `job_cancel` 꼴이라 안 걸린다. 아래를 `HWAXMcpGateway/gateway.py` 의 `_INVOKE_DENY_EXACT` 에
더해 달라. 차단은 **해석된 원래 이름**으로 보므로 인스턴스가 몇이든 이 목록 하나로 덮이고, 사람이 고른 도구(직접 바인딩)는
그대로 불린다. 기준은 「미리 보기 없이 바로 바꾸거나, apply 한 번에 여러 줄을 바꾸거나, 사라진 것을 되살릴 수 없는가」 다.
우리 MCP 서버도 이 도구를 **시스템 관리자만** 쓰게 막는다 — 게이트웨이 차단은 그 위의 한 겹이다(AI 가 고른 호출은
관리자여도 범용 실행기로 안 나가게).

<!-- 위험 도구: 시작 -->
```python
# {{NAME}} 등 StandardPlatform 계열 — 원래 이름(접두어 없이). 인스턴스가 늘어도 이대로
"objects_delete", "object_merge", "object_restore", "attachment_remove", "relation_remove",
"ontology_delete", "ontology_restore", "ontology_retype", "ontology_rename_option",
"bulk_edit", "bulk_edit_undo", "bundle_undo", "job_apply", "job_cancel", "filestore_gc",
"review_decide", "extension_call", "review_undo", "config_delete", "jobs_purge",
```
<!-- 위험 도구: 끝 -->

## 8. B 전환

A 가 죽으면 **사람이** 주소를 B 로 바꾸고 `./infra/scripts/update-all.sh` 를 다시 돌린다(RA 와 같다). 9절의 단계가 있으면
`{{PREFIX}}_HOST` 한 줄이고, 없으면 세 자리를 같이 바꾼다 — `routes.local.env` 의 `{{ID}}=` · `provision.env` 의
`{{PREFIX}}_SSO_URL` · `gateway_config.json` 의 `{{ID}}.url`. 우리 B 는 늘 앱이 떠 있고 DB 승격은 우리 몫이다. 자동 전환은 요청하지 않는다.

## 9. (권장) `update-all` 에 목록형 단계 한 번

RA 의 「`RA_HOST` 한 줄」 은 1e 가 그 이름으로 짜인 코드라서 된다. 같은 코드의 인스턴스가 여럿이면 이름마다 코드를 더하는 대신
**목록을 읽는 단계 하나**를 만들어 두면, 그 뒤로는 인스턴스가 늘어도 운영자가 적는 것이 RA 처럼 주소 한 줄이다:

```bash
# infra/.env — 인스턴스마다 쌍 하나를 공백으로 더한다(예: "{{ID}}:{{PREFIX}} <다른 id>:<그 접두>")
SP_APPS="{{ID}}:{{PREFIX}}"
{{PREFIX}}_HOST=<A 주소>          # B 전환은 이 한 줄
{{PREFIX}}_PORT={{APP_PORT}}
{{PREFIX}}_MCP_PORT={{MCP_PORT}}
{{PREFIX}}_SSO_SECRET=<비밀>      # 또는 provision.env 에 바로
```

단계가 쌍마다 하는 일(1e 와 같은 독자 `_envfile_value` · 같은 가드 — 모양 검사 · 루프백 거부 · 거부한 값은 비움, 적기는 `_upsert_kv`):

1. `routes.local.env` 에 `<id>=http://$HOST:$PORT/`.
2. 게이트웨이 `provision.env` 에 `<P>_SSO_URL=http://$HOST:$PORT/api/auth/sso` · `PER_USER_SSO_APPS` 에 `<id>:<P>`(없으면) · 비밀.
3. 게이트웨이 백엔드 `<id>` 의 `url = http://$HOST:$MCP_PORT/mcp`(없으면 5-1 모양으로 만든다 — `label` 은 타일 이름).
4. §6 판정에 콜백 숫자(10-1)와 위임 점검(10-2 의 첫 `curl` — 토큰을 만들지 않는다) 한 줄씩.

`$HOST` 가 비면 그 쌍은 ○ 장부에 「미설정」(1e 의 `hwax_skip` 과 같은 말)로 남기고 붙이지 않는다. 타일(4-1) · 권한 표(6) · 위험 도구(7)는
인스턴스마다 추적 파일이 바뀌는 일이라 이 단계 밖이다 — 타일과 권한 표만 인스턴스마다 한 블록씩 남는다.

## 10. 진단 — 우리가 답하는 숫자

### 10-1. 포털 로그인 콜백(RA 와 같은 표)

```bash
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' -m 8 -X POST \
  http://127.0.0.1:8088/{{ID}}/api/auth/portal-callback -d token=probe-not-a-token
```

| 숫자 | 뜻 | 누구 몫 |
| --- | --- | --- |
| **303** (`Location: /{{ID}}/login?sso=invalid`) | 라우트 · 우리 새 판 · SSO 켜짐 · 도달 — 다 섰다 | 정상 |
| 404 + **JSON** | 우리 판은 새것인데 포털 로그인이 꺼져 있다(우리 `.env` 에 `PORTAL_JWKS_URL` 없음) | 우리 — 4-2 의 주소를 받으면 적는다 |
| 405 | 우리가 옛 판(콜백 없음) | 우리 — 새 번들로 update |
| 200 + HTML | 라우트가 없어 포털 SPA 로 떨어졌다 | 포털 — `routes.local.env` · gen-nginx |
| 404 + HTML | nginx 가 냈다 — 경로가 어긋났다 | 포털 |
| 502 · 503 · 504 · 000 | 포털이 `<A 주소>:{{APP_PORT}}` 에 못 닿는다 | 방화벽 · 우리 앱 상태 — 같이 본다 |

### 10-2. 사람별 위임 발급

포털 박스에서(게이트웨이가 부르는 그 주소로):

```bash
S='<비밀>'; A='<A 주소>'
curl -s -o /dev/null -w '%{http_code}\n' -X POST "http://$A:{{APP_PORT}}/api/auth/sso/verify" -H "X-Heax-Gateway-Secret: $S"
#   204 — 켜짐 · 비밀 맞음 · 허용 주소 안. 토큰을 만들지 않는 점검 창구다
curl -s -X POST "http://$A:{{APP_PORT}}/api/auth/sso" -H "X-Heax-Gateway-Secret: $S" \
  -H "X-Heax-User-Email: <우리에게 승인된 사람>" -H "X-Heax-Client: gateway"
#   200 {"access_token":"{{ID}}_pat_…","token_type":"bearer","expires_in":86400}
```

| 숫자 | 뜻 | 누구 몫 |
| --- | --- | --- |
| **200** + `access_token` | 정상 — 받은 토큰으로 `GET http://$A:{{APP_PORT}}/api/auth/me` 가 그 사람 | — |
| **204**(`/sso/verify`) · **400**(`/sso` 에 이메일 없이) | 켜짐 · 비밀 맞음 · 도달 | 정상 |
| 401 + 한 줄 | 비밀이 다르다 | 둘이 같이 — 같은 값인지(따로 전한 것) |
| 403 + 한 줄 | 처음 보는 사람 · 승인 대기 · 정지 · 허용 주소 밖 — 본문이 무엇인지 말한다 | 사람이면 포털에서 타일을 한 번 열고 우리 승인을 기다린다 · 주소면 우리 |
| 404 + JSON | 우리 위임이 꺼져 있다(`HEAX_SSO_SECRET` 없음 · 재시작 전) | 우리 |
| 405 | 위임이 없는 옛 판(`/sso/verify` 는 우리 v0.4.57 까지 없다) | 우리 — 새 번들로 update |
| 000 | `<A 주소>:{{APP_PORT}}` 에 못 닿는다 | 방화벽 · 우리 앱 상태 |

## 11. 검증 체크리스트

포털 박스에서, 3 · 5 · 6절을 적고 `update-all` 을 돌린 뒤:

```bash
P=http://127.0.0.1:8088; ID={{ID}}
curl -s -o /dev/null -w "%{http_code}\n" $P/$ID/                        # 200 — 화면
curl -s $P/$ID/api/health                                              # {"status":"ok",…,"slug":"{{ID}}"}
curl -s -o /dev/null -w "%{http_code}\n" $P/$ID/admin/server            # 200 — 딥링크(새로 고침)
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" -X POST $P/$ID/api/auth/portal-callback -d token=probe-not-a-token
                                                                       # 303 …/{{ID}}/login?sso=invalid  (10-1)
curl -s http://127.0.0.1:9110/health | python3 -c 'import json,sys; print(json.load(sys.stdin)["backends"].get("{{ID}}"))'
                                                                       # True — 게이트웨이가 우리 MCP 에 붙었다
curl -s http://127.0.0.1:9110/tools-map | python3 -c 'import json,sys; print(sum(1 for b in json.load(sys.stdin)["map"].values() if b=="{{ID}}"))'
                                                                       # 76 — 우리 도구가 보인다
grep -n "{{ID}}" infra/nginx/hwax.conf | head                         # location 하나 · loc_extras 가 들어갔나
```

- [ ] ① 위 숫자 — 화면 200 · health(우리 slug) · 딥링크 200 · 콜백 **303** · 게이트웨이 백엔드 True · 도구 93
- [ ] ② 10-2 — 이메일 없이 400, 승인된 사람으로 200 + `access_token`
- [ ] ③ 포털 타일 「{{NAME}}」 을 누르면 로그인된 첫 화면(`/{{ID}}/`). 처음 보는 사람은 우리 로그인 화면에 「가입 승인을 기다립니다」
- [ ] ④ Claude 를 포털 `/mcp-gw/mcp` 로 붙여 `{{ID}}_whoami`(또는 `whoami`)가 **그 사람**을 말한다 — 게이트웨이 감사에 `mode=as-user-pat`
- [ ] ⑤ 범용 실행기로 `{{ID}}_objects_delete` 를 부르면 「파괴·제어성 도구라 범용 실행기로 부를 수 없습니다」(7절)
- [ ] ⑥ 수백 MB 사진 zip 업로드가 413 · 504 없이 끝난다(3절)
- [ ] ⑦ 다른 앱 라우트(`/`, `/report-archive/`, `/heax-hub/` …) 무영향
- [ ] ⑧ 우리에게 JWKS 주소(4-2)와 ① · ② 의 숫자를 전달

## 12. 이번에 요청하지 않는 것

- **`/{{ID}}/mcp` 라우트** — 포털 nginx 로 MCP 를 열지 않는다. 사람들의 Claude 는 게이트웨이로만 붙는다(RA 와 같다).
- **포털 「연결」(PAT 등록부)** — 서비스가 포털 코드에 박혀 있어 인스턴스마다 비싸고, 사람이 토큰을 붙여 넣어야 한다. 위임(5절)이 대신한다.
- B 자동 전환 · 포털 `groups` 를 우리 부서로 옮기기(우리는 처음 보는 사람을 가입 신청으로 받고 부서는 우리 관리자가 정한다).
