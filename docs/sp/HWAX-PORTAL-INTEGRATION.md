# StandardPlatform ↔ HWAX 포털 통합

> 포털 플레이북(`HWAXPortal/docs/PORTAL-INTEGRATION-PLAYBOOK.md`) 부록 B 의 틀을 우리 값으로 채운 것. 결정과 까닭은
> [ADR 0028](adr/0028-HWAX-포털에-붙이기.md), 포털 쪽이 할 일은 [「[참고] HWAX포탈_연동_요청서」](./[참고]%20HWAX포탈_연동_요청서.md)
> (**틀** — 인스턴스마다 `sudo APP_SLUG=<slug> ./deploy.sh hwax-request` 가 그 설치의 값으로 채운다), 우리 서버에서 치는
> 순서는 운영 안내서(`deploy/README_OPERATOR.md`) 9절이다. 아래 `<id>` 는 포털의 앱 id 이고 **설치의 `APP_SLUG` 와 같게
> 둔다** — 같은 코드의 인스턴스(「자주검증 Datahub」 · 「Failure Trace」 …)가 각자의 slug 로 붙는다. 내부 주소(서버 IP)와
> 위임 비밀은 이 문서에 적지 않는다 — 포털 담당에게 따로.
>
> **Report Archive(RA)와 같은 방식이다** — 화면은 포털 경로(`/<id>/`)로, MCP 는 포털 경로로 열지 않고 **게이트웨이가**
> 우리 MCP 포트를 부르며, 사람마다 우리 `POST /api/auth/sso` 로 그 사람 명의의 토큰을 받는다(사람별 위임).

```markdown
# <NAME> ↔ HWAX 포털 통합

- integration_type: jwt-handoff   (proxy + 포털 로그인, base /<id>/)
- 서빙: combined — FastAPI 한 프로세스가 SPA + /api 를 :<앱 포트>에서(Apptainer · systemd `<slug>.service`, 0.0.0.0).
        MCP 는 다른 프로세스 :<앱 포트+2> (`<slug>-mcp.service`, 경로 /mcp — 게이트웨이가 닿게 MCP_HOST=0.0.0.0)
        서버쌍 A(주) · B(대기) — 둘 다 앱이 떠 있다. 같은 서버쌍에 다른 인스턴스가 함께 뜬다(포트가 다르다).
- 빌드: 접두어를 빌드에 굽지 않는다 — `vite build`(base "./"), 서버가 index.html 에
        `<base href="/<id>/">` · `<meta name="app-base">` 를 심는다(PUBLIC_PATH). 같은 번들이 어느 접두어에서도 뜬다.
- 서브패스 손잡이: `.env` 의 PUBLIC_PATH=/<id> 한 줄 — 라우터 basename · API 주소 · 쿠키 path · FastAPI root_path 가
        전부 거기서 나온다. 앱은 접두어를 벗겨 오든 붙어 오든 받는다(PrefixMiddleware).
- routes.local.env:  <id>=http://<A>:<앱 포트>/            (STRIP — 끝 /. MCP 경로는 없다)
- loc_extras: <id> → client_max_body_size 2048m · proxy_request_buffering off · proxy_buffering off · proxy_read_timeout 600s
- 게이트웨이: 백엔드 <id> = {url: http://<A>:<MCP 포트>/mcp, transport: streamable_http} (서비스 토큰 없음)
        provision.env: PER_USER_SSO_APPS 에 <id>:<접두> · <접두>_SSO_SECRET · <접두>_SSO_URL=http://<A>:<앱 포트>/api/auth/sso
- 권한: access.local.yaml 의 platforms {id: <id>, systems: [<id>], gateway: [<id>]}
- DB: 우리 서버쌍의 PostgreSQL 16(주 · 대기 스트리밍 복제, DB VIP). 포털 DB 와 무관. extension: pg_trgm(마이그레이션이 만든다)
- 실행: `sudo ./deploy.sh install|update`(릴리스 번들) — 포털 쪽 값은 `sudo PORTAL_JWKS_URL=… PORTAL_LAUNCH_URL=… ./deploy.sh update`,
        요청서와 위임 비밀은 `sudo APP_SLUG=<slug> ./deploy.sh hwax-request`
- SSO: jwt-handoff, 콜백 = /<id>/api/auth/portal-callback (POST, 폼 필드 token)
- 위임: POST /api/auth/sso (게이트웨이가 앱 포트로 직접 — X-Heax-Gateway-Secret)
- 검증: 아래 체크리스트(플레이북 §10 을 우리 주소로)
```

## 1. 주소와 경로

| 무엇 | 공개 주소(포털 경유) | 우리 서버에서 |
| --- | --- | --- |
| 화면 | `https://hwax.sec.samsung.net/<id>/` | `http://<A>:<앱 포트>/` (접두어 없이 와도 화면이 붙여 옮긴다) |
| API | `https://hwax.sec.samsung.net/<id>/api/…` | `:<앱 포트>/api/…` |
| 포털 로그인 콜백 | `https://hwax.sec.samsung.net/<id>/api/auth/portal-callback` | `:<앱 포트>/api/auth/portal-callback` |
| Claude(MCP) | 포털 게이트웨이 `https://hwax.sec.samsung.net/mcp-gw/mcp`(포털 개인 토큰) — 도구 이름에 `<id>_` | 게이트웨이가 `http://<A>:<MCP 포트>/mcp` 를 부른다 |
| 사람별 위임 발급 | — (포털 nginx 를 안 거친다) | 게이트웨이가 `http://<A>:<앱 포트>/api/auth/sso` 를 직접 |
| 포털 JWKS(우리가 받는다) | — | `http://<포털>:8088/.well-known/jwks.json`(사내망 http 권장) |

**MCP 는 포털 경로로 열지 않는다**(RA 와 같다). 사람들의 Claude 는 포털 게이트웨이에만 붙고, 게이트웨이가 우리 MCP 포트를
직접 부른다 — 그래서 `MCP_HOST=0.0.0.0`(이중화면 이미 그렇다)이고, `MCP_ALLOWED_HOSTS` 를 쓰는 설치는 게이트웨이가 부르는
주소를 **포트까지**(`<A>:<MCP 포트>`, B 도) 넣는다 — 없으면 421. 자기 토큰으로 직접 붙는 사람(사내망)을 위해
`MCP_PUBLIC_URL=http://<A>:<MCP 포트>/mcp` 를 적어 둔다 — 비우면 화면(내 정보의 Claude 연결 안내)이 `https://<포털>/<id>/mcp`
를 짐작하는데 그 길은 없다(서버 화면이 경고한다).

**큰 요청 · 긴 요청** — 사진 일괄 업로드 zip 은 2GB(`JOB_PHOTOS_MAX_BYTES`), 내보내기 결과는 수백 MB, 큰 계획(일괄 입력 ·
검토함 미리 보기)은 요청 안에서 수십 초 ~ 수 분이다. 포털 템플릿의 기본(전역 2048m · 버퍼링 켬 · 응답 대기 60초)이면
업로드가 포털 디스크(`/tmp`)에 한 벌 쌓인 뒤 넘어오고, 60초를 넘는 요청은 504 로 끊긴다. 그래서 `loc_extras` 에 RA 와
같은 갈래를 요청한다. 정확히 2GiB 짜리 zip 은 본문 머리 몫만큼 2048m 을 넘어 포털 nginx 가 413 으로 먼저 자른다 — 실사용에는
지장이 없고, 맞추려면 그 갈래만 2100m(우리 이중화 조각과 같은 값).

## 2. 포털 로그인(jwt-handoff)

```
사람 ──(타일 · /launch/<id>)──▶ 포털: 그 사람으로 RS256 launch 토큰(90초) ──숨은 폼 POST token──▶
  /<id>/api/auth/portal-callback ──(포털 nginx STRIP)──▶ 우리 :8040/api/auth/portal-callback
    ① 포털 JWKS 로 서명 확인(5분 캐시 · 모르는 kid 면 한 번 다시) · RS256 만
    ② iss = PORTAL_ISSUER · aud = PORTAL_AUDIENCE(기본 slug) · exp · nbf(여유 10초) · scope == "launch"
    ③ jti 한 번만(DB `portal_launch_jtis` — 이중화 두 서버가 함께 본다)
    ④ email → 없으면 sub 로 계정 찾기(normalize_login_id). sub 로만 맞은 시스템 관리자 계정에는 안 붙인다
    ⑤ 활성 → refresh 쿠키 + 303 /<id>/          (SPA 가 기동 때 그 쿠키로 access 를 받는다)
       승인 대기 · 처음 보는 사람 → 303 /<id>/login?sso=pending   (처음 보는 사람은 가입 신청이 된다)
       정지 · 삭제 · 확인 실패 · 만료 · 공개 키 못 받음 → 303 /<id>/login?sso=<사유>
```

| 설정(`.env`) | 값 | 비우면 |
| --- | --- | --- |
| `PORTAL_JWKS_URL` | `http://<포털>:8088/.well-known/jwks.json` | **포털 로그인 꺼짐** — 콜백이 404 JSON |
| `PORTAL_LAUNCH_URL` | `https://hwax.sec.samsung.net/launch/<id>` | 로그인 화면에 「HWAX 포털로 로그인」 단추가 없다 |
| `PORTAL_AUDIENCE` | 포털 타일의 `audience` | `APP_SLUG` |
| `PORTAL_ISSUER` | 포털의 `JWT_ISSUER` | `https://hwax.sec.samsung.net`(포털 기본값) |
| `PUBLIC_PATH` | `/<id>` | 로그인 뒤 돌아갈 곳이 포털 첫 화면이 된다(서버 화면이 경고) |
| `TRUST_PROXY` | `true` | 포털이 https 여도 쿠키에 Secure 가 안 붙는다(로그인은 된다) |
| `MCP_PUBLIC_URL` | `http://<A>:<MCP 포트>/mcp` | 화면이 `https://<포털>/<id>/mcp`(없는 길)를 짐작한다 |

**직접 접속한 사람** — 앱 주소(`http://<A>:<앱 포트>/`)로 들어와도 로그인 화면의 「HWAX 포털로 로그인」 이 포털의
`/launch/<id>` 로 보낸다. 포털이 그 사람으로 토큰을 만들어 포털 주소의 콜백에 보내므로, 로그인 뒤에는 포털 주소
(`https://hwax.sec.samsung.net/<id>/`) 위에 있다.

**포털 안에서 바로 들어온 사람**(북마크 · 링크 — Report Archive 와 같은 길) — 화면이 포털 로그인 쿠키(`hwax_csrf`)를 보면
포털의 표준 launch(`POST /systems/<id>/launch`, 포털 세션 · `X-CSRF-Token`)로 토큰을 받아 `POST /api/auth/portal-exchange`
로 바꾼다 — 콜백과 같은 확인 · 같은 세션(refresh 쿠키 + access), 거절은 `details.reason` 이 콜백의 `?sso=` 와 같다. 브라우저
세션당 한 번이고, 포털 안에서 로그아웃하면 그 세션에는 다시 저절로 들이지 않는다(인스턴스마다 따로 기억). 타일 id 는 서버가
`<meta name="app-portal-system">`(= `PORTAL_AUDIENCE`)로 심는다 — 포털 쪽에 새로 할 일은 없다.

**처음 보는 사람** — 가입 신청(승인 대기)이 되고 관리자의 홈 「남은 일」 에 「가입 승인 대기」 로 뜬다. 희망 부서가 비어
있으므로 관리 › 계정의 「승인」 이 부서를 묻는다. 비밀번호가 없는 계정이다 — 아이디 · 비밀번호로도 들어와야 하면 승인 뒤
「비밀번호 초기화」.

**진단** — 관리 › 서버 › 「HWAX 포털 로그인」(또는 `GET /api/auth/portal-status`, 시스템 관리자): 켜졌나 · 공개 키를 지금
받아 보고 닿나 · kid · 마지막 오류 · 설정끼리 안 맞는 것. 거절된 로그인의 자세한 사유는 앱 로그(`journalctl -u <slug>`)에
`포털 로그인 거절(<사유>): …` 로 남는다. 감사 기록은 `auth.portal_login` · `account.portal_requested`(통로 `hwax-portal`),
접근 로그는 `PORTAL_LOGIN`.

## 2b. 게이트웨이의 사람별 위임(MCP)

```
사람의 Claude ──(포털 개인 토큰)──▶ 포털 게이트웨이 /mcp-gw/mcp ──▶ 도구 <id>_objects_list …
  게이트웨이: 그 사람의 위임 토큰이 없거나 12시간이 지났으면
    POST http://<A>:<앱 포트>/api/auth/sso          (본문 없음, 포털 nginx 를 안 거친다)
      X-Heax-Gateway-Secret: <비밀>  X-Hwax-Delegate-Email · X-Heax-User-Email: <그 사람>  X-Heax-Client: gateway
    ◀ 200 {"access_token": "<slug>_pat_…", "token_type": "bearer", "expires_in": 86400}
  게이트웨이 ──(Authorization: Bearer <그 토큰>)──▶ 우리 MCP :<MCP 포트>/mcp ──(그대로)──▶ 백엔드
  백엔드가 401 → MCP 도구가 「Error executing tool X: GET /api/… → HTTP 401: …」 → 게이트웨이가 한 번 다시 받는다
```

| 설정(`.env`) | 값 | 비우면 |
| --- | --- | --- |
| `HEAX_SSO_SECRET` | 인스턴스마다 따로(`openssl rand -hex 32`) — `deploy.sh hwax-request` 가 없으면 만든다 | **위임 꺼짐** — `/api/auth/sso` 가 404 JSON |
| `HEAX_SSO_ALLOWED_IPS` | 포털 박스 주소(쉼표 · CIDR) | 주소를 보지 않는다(비밀만) |

- **토큰** — 그 사람 명의의 액세스 토큰(PAT): 이름 「HWAX 게이트웨이(<client>)」 · 하루 · 범위 `read` + 등록된 쓰기 범위
  (관리 범위 없음). 같은 client 의 직전 토큰은 폐기하고 그 전에 죽은 줄은 지운다(「내 정보」 에 쌓이지 않게).
- **거절** — 꺼짐 404 JSON · 비밀 다름 401 · 이메일 머리 없음 400 · 처음 보는 사람 · 대기 · 정지 · 삭제 · 허용 주소 밖 403.
  404 밖의 거절은 **사람이 읽을 한국어 한 줄**(text/plain) — 게이트웨이가 본문 앞 200자를 그 사람에게 그대로 보인다.
  처음 보는 사람을 만들지 않는다 — 문구가 「포털에서 타일을 한 번 열면 가입 신청이 된다」 를 말한다.
- **허용 주소** — TCP 원격 주소로만 본다. `TRUST_PROXY` 설치에서는 서버가 `X-Forwarded-For` 로 원격 주소를 바꿔 적으므로
  목록이 있으면 그 머리가 붙은 요청을 받지 않는다. 못 읽는 값이 있으면 모두 거절(서버 화면이 말한다).
- **기록** — 감사 `auth.gateway_delegated` · `auth.gateway_delegation_revoked`, 위임 토큰으로 든 요청의 통로는
  `hwax-gateway`(감사 `actor_client`, 토큰 이름은 `actor_token`). 접근 로그는 발급이 `GATEWAY_SSO`, 위임 토큰으로 든
  요청이 `GATEWAY_API`(발급 · 회수 · 점검 모두 `GATEWAY_SSO` — 점검의 거절도 서버 화면의 「마지막 거절」 에 잡힌다). 거절의 자세한 사유는 앱 로그 `게이트웨이 위임 거절(<숫자>): …`.
- **점검** — `POST /api/auth/sso/verify`(같은 머리 — 이메일은 안 본다) → 본문 없는 **204**. 토큰 · 계정 · 감사를 건드리지
  않는다 — 게이트웨이가 「이 앱과 연결 설정(주소 · 비밀)이 맞나」 만 볼 때(`/sso` 로 보면 토큰이 실제로 나가고 직전 토큰이
  회수된다). 거절은 꺼짐 404 JSON · 허용 밖 403 · 비밀 401 — 발급과 같은 순서 · 같은 한 줄. 세 플랫폼(Standard Platform ·
  ComponentCore · SimEngBay)이 네 창구(portal-callback · sso · sso/verify · sso/revoke)를 같은 계약으로 둔다.
- **회수** — `POST /api/auth/sso/revoke`(같은 머리) → `{"revoked": n}`. 사람을 끊는 것은 계정 정지가 맞다(위임 토큰은
  정지된 사람에게 안 통하고, 게이트웨이가 다시 받으려 하면 403 「정지된 계정」).
- **도구 이름** — 같은 틀의 인스턴스는 도구 이름이 같아 게이트웨이가 `<id>_` 를 붙인다(둘 이상이면 93개 전부).
- **위험 도구** — 게이트웨이 범용 실행기의 이름 규칙(`delete_` 로 시작)에 우리 이름이 안 걸려, `mcp_server/server.py` 의
  `DESTRUCTIVE_TOOLS` 를 `_INVOKE_DENY_EXACT` 에 넣어 달라고 요청서 7절이 적는다(한 번 — 인스턴스가 몇이든).
  그와 별개로 MCP 서버가 이 도구를 **시스템 관리자만** 쓰게 한다(ADR 0030) — 직접 바인딩이어도 그 사람이
  시스템 관리자가 아니면 막힌다.
- **진단** — 관리 › 서버 › 「HWAX 포털 로그인」 의 「게이트웨이 위임」: 켜졌나 · 허용 주소 · 범위 · 지금 사용 중인 위임 토큰 수 ·
  마지막 발급 · 마지막 거절(때 · 숫자 · 까닭 — 다른 서버 · 프로세스가 거절했으면 숫자의 뜻).

## 3. 포털의 진단표 — 콜백에 가짜 토큰을 던졌을 때 우리 답

포털 `update-all` 이 `curl -X POST …/<id>/api/auth/portal-callback -d token=probe-not-a-token` 으로 가른다(RA 와 같은 표).

| 숫자 | 뜻 | 누구 몫 |
| --- | --- | --- |
| **303** (`Location: /<id>/login?sso=invalid`) | 라우트 · 우리 새 판 · SSO 켜짐 · 도달 — 다 섰다 | 정상 |
| 404 + **JSON** (`APP-AUTH-0009`) | 우리 판은 새것인데 `PORTAL_JWKS_URL` 이 없다(SSO 꺼짐) | 우리: `.env` 에 적고 `sudo ./deploy.sh restart` |
| 405 | 콜백이 없는 옛 판(0.4.51 이하) — SPA 의 받아내기가 POST 를 거절 | 우리: 새 번들로 `update` |
| 200 + HTML | 포털 nginx 에 `<id>` 라우트가 없어 포털 SPA 로 떨어졌다 | 포털: `routes.local.env` · gen-nginx |
| 404 + HTML | nginx 가 냈다 — 경로가 어긋났다 | 포털 |
| 502 · 504 · 000 | 포털이 `<A>:<앱 포트>` 에 못 닿는다 | 방화벽 · 우리 앱 상태 |

GET 은 405(JSON, `APP-AUTH-0010`)로 따로 답한다 — 콜백은 POST 로만 온다.

## 4. 검증 체크리스트 (플레이북 §10 을 우리 주소로)

포털 박스에서(포털 nginx 경유 `:8088` · 게이트웨이 `:9110`):

```bash
P=http://127.0.0.1:8088; ID=<slug>                    # 포털의 앱 id = 우리 APP_SLUG
curl -s -o /dev/null -w "%{http_code}\n" $P/$ID/                        # 200 — SPA(index.html)
curl -s $P/$ID/api/health                                              # {"status":"ok","version":"v…","slug":"…"}
A=$(curl -s $P/$ID/ | grep -o 'assets/[^"]*\.js' | head -1)
curl -s -o /dev/null -w "%{http_code}\n" $P/$ID/$A                     # 200 — 해시 붙은 자산
curl -s -o /dev/null -w "%{http_code}\n" $P/$ID/admin/server            # 200 — 딥링크(새로 고침)
curl -s -o /dev/null -w "%{http_code} %{redirect_url}\n" -X POST $P/$ID/api/auth/portal-callback -d token=probe-not-a-token
                                                                       # 303 …/$ID/login?sso=invalid (3절 표)
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://<A>:<앱 포트>/api/auth/sso -H "X-Heax-Gateway-Secret: <비밀>"
                                                                       # 400 — 위임 켜짐 · 비밀 맞음(이메일을 안 줬다)
curl -s http://127.0.0.1:9110/health | python3 -c 'import json,sys; print(json.load(sys.stdin)["backends"].get("'$ID'"))'
                                                                       # True — 게이트웨이가 우리 MCP 에 붙었다
for x in / /report-archive/ /heax-hub/; do curl -s -o /dev/null -w "$x %{http_code}\n" $P$x; done   # 다른 라우트 무영향
```

우리 서버에서:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://<포털>:8088/.well-known/jwks.json   # 200 — 이 서버에서 포털 JWKS 가 닿나
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:<MCP 포트>/mcp            # 406 · 400 — MCP 가 떠 있다
```

- [ ] `/<id>/` 화면 · 자산 200 · 딥링크(새로 고침) 200
- [ ] `/<id>/api/health` 가 우리 slug · 버전
- [ ] 포털 타일을 누르면 로그인된 첫 화면(`/<id>/`) — 처음 보는 사람은 로그인 화면에 「가입 승인을 기다립니다」
- [ ] 우리 로그인 화면의 「HWAX 포털로 로그인」 이 같은 결과(직접 접속 `http://<A>:<앱 포트>/` 에서도)
- [ ] 관리 › 서버 › 「HWAX 포털 로그인」 이 켜짐 · 공개 키 받음 · 문제 없음, 「게이트웨이 위임」 켜짐
- [ ] 위임 발급(승인된 사람의 이메일로) 200 + `access_token` — 그 토큰으로 `/api/auth/me` 가 그 사람
- [ ] Claude 를 포털 `/mcp-gw/mcp` 로 붙여 `<id>_whoami` 가 **그 사람** — 서버 화면의 「마지막 발급」 이 방금
- [ ] 사진 zip(수백 MB) 업로드가 413 · 504 없이 끝까지 — 진행률이 실제를 따라간다(버퍼링 끔)
- [ ] 다른 앱 라우트 무영향
