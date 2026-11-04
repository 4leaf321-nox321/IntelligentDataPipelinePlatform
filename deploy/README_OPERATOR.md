# 운영 배포 가이드

**이 문서는 릴리스 번들 안에 함께 담긴다.** 서버가 tar 하나만 받는 환경이어도
설치하는 자리에서 이것을 볼 수 있어야 한다.

---

## 0. 받아서 옮기고 푼다

번들은 **GitHub Actions 가 태그에서 만든다.** 저장소를 받는 PC 에서는 만들 수
없다 — Apptainer 는 리눅스 전용이다. 받는 것은 만들어진 `tar.gz` 하나다.

```bash
# 받는 PC 에서 — 저장소의 deploy/pc/fetch-release.{bat,sh} 가 최신 버전을 골라 받고 체크섬까지 본다.
# 손으로 받으면 (Windows PowerShell 의 curl 도 같은 줄로 된다):
curl -LO https://github.com/<소유자>/<저장소>/releases/download/<태그>/<slug>-<태그>.tar.gz
curl -LO https://github.com/<소유자>/<저장소>/releases/download/<태그>/<slug>-<태그>.tar.gz.sha256

scp <slug>-<태그>.tar.gz <계정>@<서버>:~/
```

서버에 붙어서:

```bash
ssh <계정>@<서버>

# **푸는 것보다 먼저 맞춰 본다.** 절반만 받아진 tar 는 푸는 순간에야 드러나고,
# 그때는 이미 배포하려고 서버에 붙어 있는 자리다.
sha256sum -c <slug>-<태그>.tar.gz.sha256    # sha256 파일도 함께 옮겼을 때

tar xzf <slug>-<태그>.tar.gz
cd <slug>-<태그>
ls
#  app.sif  deploy.sh  ha.sh  pg-ha.sh  backup.sh  restore.sh
#  app.service.template  worker.service.template  mcp.service.template  sync.service.template  sync.timer.template
#  metrics.service.template  metrics.timer.template  backup.service.template  backup.timer.template
#  .env.example  BUILD_INFO  README.md  쉬운-설치.md  HWAX-연동요청서.template.md(9절 — hwax-request 가 채운다)
#  mcp_server/   (Claude 연동 MCP 서버 + 오프라인 설치용 휠)
#  apptainer_debs/  (폐쇄망용 apptainer .deb — prepare 가 먼저 본다)
#  pgvector_debs/   (폐쇄망용 pgvector .deb — 뜻으로 찾기의 확장, 5d-3)
```

임베딩 모델(Ollama · bge-m3, 약 2.6GB)은 번들에 없다 — `install` · `update` 가 인터넷으로 받는다. 폐쇄망이면
**임베딩 꾸러미**를 따로 옮겨 이 폴더에 푼다(5d-3).

**정제 도구 키트**(사용자 PC 에 푸는 `sp-pipeline` — 원천 파일을 한꺼번에 넣는 도구)는 `app.sif`
안에 같은 판으로 들어 있다. 운영자가 따로 옮길 것이 없다 — 각 사람이 화면 「내 정보」 에서 받는다.

> `/home` 에서 실행이 막히는 서버가 있다(noexec 마운트). 그때는 `/tmp` 에 풀어
> 실행한다 — 설치 **대상** 폴더는 그대로 `/home/<계정>/apps/<slug>` 다.

**번들 하나로 여러 플랫폼(인스턴스)을 설치한다.** 어느 플랫폼인지는 설치할 때 주는
`APP_SLUG` 가 정한다 — DB 이름 · systemd 유닛 · 설치 경로 · 주소 접두어가 전부 거기서 나온다.
`BUILD_INFO` 에는 버전과 **아무것도 안 줬을 때의 기본값**(틀의 이름 · 기본 포트)만 있다.

```bash
# 처음 한 번 — 이름 · 포트 · 확장을 준다. /etc/platform-instances/<slug>.conf 에 남는다
sudo APP_SLUG=plmhub APP_NAME="PLM 기준정보" APP_PORT=8040 ./deploy.sh prepare
sudo APP_SLUG=plmhub ./deploy.sh install
# 그다음부터 — 이 서버에 인스턴스가 하나면 APP_SLUG 를 안 줘도 그것이다
sudo ./deploy.sh update
```

> **env 는 `sudo` 뒤에 붙인다** — `sudo APP_NAME=… ./deploy.sh update`. `APP_NAME=… sudo ./deploy.sh`
> 처럼 앞에 두면 우분투 기본 설정(`env_reset`)이 그 값을 **조용히 버린다**(24.04 운영 서버에서
> 확인, 2026-10-05). 스크립트에는 「안 줬다」 로 보여 기억한 값이나 기본값으로 그대로 진행한다.

| 설치 때 주는 것 | 뜻 | 나중에 |
| --- | --- | --- |
| `APP_SLUG` | 기계가 읽는 이름. 소문자·숫자 한 덩어리, 32자 이내 | **바꾸지 않는다** — DB · 쿠키 · 토큰 · 유닛이 다 걸린다 |
| `APP_NAME` · `APP_TAGLINE` | 화면에 보이는 이름 · 한 줄 설명 | `sudo APP_NAME=… ./deploy.sh update` 로 바꿀 수 있다 |
| `APP_PORT` | 앱 포트(MCP 는 +2). 같은 서버의 인스턴스마다 10씩 벌린다 | 바꾸면 메인 서버 조각도 다시 넣어야 한다 |
| `EXTENSIONS` | 확장 모듈의 **기본값**, 쉼표로. 안 줘도 된다 | **화면에서 켠다** — 시스템 관리자 › 서버 › 「확장 모듈」 |

**번들에 든 확장** — `caegroup`(디지털 트윈 역량) · `jajuverify`(자주검증 회의 표 — 심사회 · 포스트모텀 · 설명회) · `sample`
(본보기). 켜는 길은 **화면**이다 — 시스템 관리자 › 서버 › 「확장 모듈」 에서 켜면 재시작 없이 메뉴가 생긴다. 설치 때 기본값으로
주려면 `sudo APP_SLUG=<slug> EXTENSIONS=jajuverify ./deploy.sh prepare`(여럿이면 쉼표로). 확장의 표(자주검증은 `jv_meetings` ·
`jv_groups` · `jv_rows` · `jv_presence`, 마이그레이션 0090 ~ 0092)는 켰는지와 무관하게 `update` 의 마이그레이션이 늘 만든다 — 켜기 전에 따로 할 일은 없다. 끄면 메뉴 ·
경로가 사라지고(404) 자료는 남는다(다시 켜면 그대로).

같은 서버에 두 번째 인스턴스는 다른 `APP_SLUG` · `APP_PORT` 로 같은 명령을 한 번 더. 인스턴스가
여럿이면 `update` · `status` 에도 붙인다 — `sudo APP_SLUG=<slug> ./deploy.sh update`(안 붙이면 어느 것인지 묻는다).

### SSH 로만 붙는 서버라면 — 미리 알아 둘 넷

이 스크립트들은 **서버 콘솔 앞에 앉아 있든 SSH 로 붙든 똑같이 돈다.** 다만 원격일
때만 걸리는 것이 넷 있다.

| | |
| --- | --- |
| **`root` 로 바로 ssh 했다면** | 운영 계정을 알 수 없어 멈춘다(`sudo` 를 거치지 않아 `SUDO_USER` 가 없다). `OPERATOR=<계정> ./deploy.sh install` 로 준다 |
| **임시 비밀번호는 한 번만 찍힌다** | 세션이 끊기면 잃는다. `sudo ./deploy.sh install 2>&1 \| tee ~/install-<태그>.log` 로 받아 둔다 |
| **`reset` 은 되묻는다** | `ssh <서버> 'sudo ./deploy.sh reset'` 은 TTY 가 없어 그 물음에서 실패한다. `ssh -t` 로 붙는다 |
| **`prepare` 는 apt 를 쓴다** | 서버가 우분투 저장소(또는 사내 미러)에 닿아야 한다. apptainer 는 번들에 동봉한 `.deb` 로 깔리므로 PPA 에 닿을 필요는 없다. 그래도 못 깔면 `prepare` 가 DB·폴더까지 만든 뒤 **무엇을 먼저 깔지 말하고 멈춘다** — 해결 뒤 `prepare` 를 다시 돌린다(앞 단계는 멱등하다) |

번들 전체를 한 줄로 밀어 넣는 것도 된다:

```bash
scp <slug>-<태그>.tar.gz <계정>@<서버>:~/ && \
  ssh -t <계정>@<서버> "tar xzf <slug>-<태그>.tar.gz && cd <slug>-<태그> && sudo ./deploy.sh"
```

인자 없는 `./deploy.sh` 는 **설치된 흔적을 보고 스스로 고른다** — 처음이면
`install`, 있으면 `update`. **처음이고 리눅스가 낯설면 `sudo ./deploy.sh setup`** — 물어보며
전부 한다([쉬운-설치.md](쉬운-설치.md)).

---

## 1. (최초 1회) 서버 준비

```bash
sudo ./deploy.sh prepare
```

apt 패키지(postgresql·python3-venv), **apptainer**, DB 역할과 데이터베이스, pgvector 확장(5d-3), 설치 폴더를 만든다.
**멱등하다** — 다시 돌려도 이미 있는 것은 건드리지 않는다.

**apptainer 는 우분투 기본 저장소에 없다.** 그래서 `prepare` 는 이 순서로 찾는다:

1. 이미 깔려 있으면 그대로 쓴다.
2. **번들에 동봉한 `.deb`**(`apptainer_debs/` — 빌드가 받아 넣는다)가 있으면 그것을 깐다. **폐쇄망은 여기서 끝난다.**
3. 닿는 저장소(사내 미러 등)에 있으면 거기서 받는다.
4. 우분투면 **공식 PPA(`ppa:apptainer/ppa`)를 더해** 받는다 — CI 가 번들을 만들 때 쓰는 것과 같은 곳이다.

2 가 없는 옛 번들(v0.4.4 이전)이고 PPA 에도 닿지 않는 서버라면 `prepare` 가 **DB·폴더까지 만든 뒤** 멈추고
할 일을 말한다. 새 번들을 쓰거나, 닿는 PC 에서 `.deb` 를 받아 옮겨 깔고 다시 돌린다:

```bash
# 닿는 PC 에서: https://github.com/apptainer/apptainer/releases 의 amd64 .deb 를 받아
scp apptainer_*.deb <계정>@<서버>:~/

# 서버에서
sudo apt install ./apptainer_*.deb
sudo ./deploy.sh prepare      # 앞 단계는 이미 서 있어 건너뛴다
```

우분투가 아니면 PPA 를 쓸 수 없다 — https://apptainer.org/docs/admin/main/installation.html 대로
먼저 깔고 `prepare` 를 돌린다.

---

## 2. 설치

```bash
sudo ./deploy.sh install
```

하는 일:

1. `~/apps/<slug>/{filestore,logs}` 생성 (공용 스토리지 `DATA_DIR` 를 주면 첨부 · `.env` · 백업은 거기 — 「8. 이중화」)
2. `.env` 생성 — **JWT 비밀키를 난수로 만들고 DB 비밀번호를 돌린다**
   (이미 있으면 손대지 않는다. 덮으면 전원이 다시 로그인한다)
3. `app.sif` 배치
4. pgvector 확장(postgres 계정으로 — 5d-3) → 마이그레이션 → 설치 시드
5. systemd 유닛 렌더 → `enable` → `start`
6. `/api/health` 확인
7. MCP 서버 설치(별도 venv + `<slug>-mcp` 유닛) — 아래 「MCP 서버」. 실패해도
   백엔드 배포는 그대로다
8. 임베딩 모델(Ollama · bge-m3) — 없으면 받아 띄운다(5d-3). 실패해도 그대로다

> 관리자 **임시 비밀번호가 화면에 한 번만** 찍힌다. 받아 적어 전달한다.
> 첫 로그인에서 변경이 강제된다.
>
> **SSH 로 붙어 있다면** 세션이 끊기는 것만으로 잃는다. 그때는 되찾을 길이
> `reset`(파괴적)뿐이다 — 그 계정이 유일한 관리자이기 때문이다.
> `sudo ./deploy.sh install 2>&1 | tee ~/install.log` 로 받아 두고, 전달한 뒤 지운다.

확인:

```bash
curl http://127.0.0.1:<포트>/api/health
sudo journalctl -u <slug> -f
```

---

## 3. 갱신

```bash
# 새 번들을 풀고 그 안에서
sudo ./deploy.sh update
```

**서비스를 내리기 전에** pgvector 확장부터 갖추고, 앱이 다시 뜬 뒤 끝에 임베딩 모델을 확인한다(5d-3 — 처음이면
깔고 받는다). 둘 다 못 갖춰도 갱신은 끝난다 — 경고를 찍고 서버 화면이 말한다. 스크립트로 돌려도 묻는 것이 없다.

**스크립트도 새 번들의 것을 쓴다** — 옛 `deploy.sh` 로 새 SIF 를 깔면 그 릴리스가
기대하는 단계가 통째로 안 돌고, 안 돌았다는 사실은 아무 데도 안 적힌다. 번들
안에서 실행하면 저절로 그렇게 된다.

직전 이미지는 `app.sif.prev` 로 남는다. 롤백:

```bash
sudo systemctl stop <slug>
sudo mv ~/apps/<slug>/app.sif.prev ~/apps/<slug>/app.sif
sudo systemctl start <slug>
```

**파일만 되돌아간다 — 마이그레이션은 취소되지 않는다.** 파괴적 마이그레이션
(컬럼 삭제·이름 변경)을 적용했다면 DB 는 백업에서 따로 복구해야 한다.

---

## 4. 백업

```bash
./backup.sh -i ~/apps/<slug> -o ~/backup/<slug>
```

**DB 와 첨부를 같은 시각에 함께 받는다.** 둘 중 하나만 받으면 복구되지 않는다.

매일 받게 하려면 systemd 타이머로 건다 — `deploy.sh` 가 백업 폴더를 알면(`DATA_DIR`, 또는
`sudo BACKUP_HOST_DIR=<폴더> ./deploy.sh update`) `<slug>-backup.timer` 를 스스로 건다(매일 03:00,
이중화의 대기 서버는 03:30 — 그날 것이 이미 있으면 건너뛴다). 손으로 걸려면 `backup.sh` 머리말에
유닛 예시가 있다. **앱 프로세스에 넣지 않는다** — 앱이 죽은 날 백업도 조용히 죽는다.

그리고 `.env` 에 적어 둔다:

```
BACKUP_DIR=/home/<계정>/backup/<slug>
```

적어 두면 앱이 마지막 덤프 시각을 읽어 **36시간이 넘으면 홈의 「남은 일」 에
올린다.** 안 적으면 백업이 멈춘 것을 **복구가 필요한 날**에야 알게 된다.

### 한 번은 실제로 복구해 본다

```bash
./restore.sh -b ~/backup/<slug> -d <slug>_restore_check
```

받아만 두고 복구를 해 본 적이 없는 백업은 백업이 아니다. `restore.sh` 는 되돌린 뒤
**DB 가 가리키는 파일이 실제로 있는지 세어 본다** — 하나라도 없으면 거기서 멈춘다.

---

## 5. 초기화 (파괴적)

```bash
sudo ./deploy.sh reset
```

DB 를 지우고 다시 만들며 첨부도 지운다. `.env`·DB 역할·systemd 유닛은 남는다.
**DB 이름을 그대로 입력해야 진행된다.**

되묻는 자리가 있으므로 **원격에서는 `ssh -t`** 로 붙는다. 아니면 그 물음에서
읽기가 실패하고, 아무것도 안 지운 채 오류로 끝난다.

---

## 5b. MCP 서버 (Claude 연동, 선택)

`deploy.sh install`/`update` 가 **MCP 서버까지 자동 설치**한다 — 별도 venv 생성 + (번들에
동봉된 휠로) **오프라인 pip 설치** + `<slug>-mcp` systemd 서비스 기동까지. 사용자는
Claude Code 에서 온톨로지를 읽고 채울 수 있다.

- **상태**: `sudo systemctl status <slug>-mcp` / 로그 `journalctl -u <slug>-mcp -f`
  (`sudo ./deploy.sh status` 도 함께 보여 준다)
- **끄기**: `sudo MCP_ENABLED=0 ./deploy.sh update` (유닛은 별도 `systemctl disable --now <slug>-mcp`)
- **주소**: `http://<서버>:<앱포트+2>/mcp` — **`<slug>` 접두어가 붙지 않는다.** 접두어가 붙는
  형태(`https://<도메인>/<slug>/mcp`)는 **메인 서버 nginx 뒤에서만** 성립한다(조각이 그 길을
  MCP 로 넘긴다). 앱 포트에 `/<slug>/mcp` 를 물으면 앱이 404 로 답한다.
  화면(「내 정보」)이 보여 주는 주소는 **들어온 주소로 판단한다**: 표준 포트(80 · 443)면
  접두어 형태, 앱 포트로 직접이면 `+2` 형태. 표준이 아닌 포트의 프록시 뒤라면 그 둘 다
  아니므로 `.env` 에 `MCP_PUBLIC_URL=https://<도메인>:<포트>/<slug>/mcp` 를 적어 정본을 준다.
  MCP 의 사진 업로드도 같은 규칙으로 업로드 주소를 만든다 — 위 두 형태(표준 포트의 nginx 뒤 ·
  MCP 포트로 직접)면 적지 않아도 맞고, 직접일 때는 앱 포트(MCP 포트 −2)로 올린다. ReportArchive
  처럼 MCP 직접 주소(`http://<IP>:<앱+2>/mcp`)를 적어 두어도 된다 — 화면은 그 주소를 안내하고,
  업로드는 들어온 주소로 만든다.
  **HWAX 포털 뒤**면 포털은 MCP 경로를 열지 않는다(Report Archive 와 같다 — 9절) — 사람들의 Claude 는 포털의
  게이트웨이(`/mcp-gw/mcp`)로 붙고 게이트웨이가 이 MCP 포트를 직접 부른다. 직접 붙는 사람을 위해
  `MCP_PUBLIC_URL=http://<서버>:<MCP 포트>/mcp` 를 적는다(비우면 화면이 없는 길 `https://<포털>/<id>/mcp` 를 짐작한다).
- **포트**: `BUILD_INFO` 의 `mcp_port` — **앱 포트 +2** (운영 +0 · 개발 +1 다음 자리).
  바꾸려면 `sudo MCP_PORT=<포트> ./deploy.sh update`.
  한 서버에 쌍둥이를 여럿 얹을 때 **앱 포트를 10 씩 벌린다** — MCP 가 +2 를 쓰므로 8040 과
  8042 를 나란히 고르면 겹친다. `deploy.sh` 가 이미 설치된 인스턴스와 견줘 **겹치면 멈춘다**
  (`/etc/platform-instances/<slug>.conf` 의 `APP_PORT` · `MCP_PORT`), `setup` 은 빈 자리를
  기본값으로 보여 준다.
- **살아 있나 보기**: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:<mcp포트>/mcp`
  → **406 · 400 이면 정상**(스트리밍 규약이라 평범한 GET 에는 그렇게 답한다), `404` 면 앱에
  물은 것, 연결 실패면 `MCP_HOST` 가 `127.0.0.1` 이라 밖에 안 열린 것이다.
- **외부 노출**: 기본 `127.0.0.1` (로컬만). 사내망에 열려면 **한 번만**
  `sudo MCP_HOST=0.0.0.0 ./deploy.sh update` — 이후 `./deploy.sh update` 는 설치된 유닛에서
  값을 읽어 **자동으로 유지**하므로 매번 다시 붙일 필요 없다(되돌릴 땐 그때만 `sudo MCP_HOST=127.0.0.1 ./deploy.sh update`).
  외부망이면 nginx 리버스프록시(TLS) 권장. 백엔드가 다른 주소면 `MCP_API_BASE=http://127.0.0.1:<포트>`.
- **Host 보호**: 비-localhost 로 열면 server.py 가 DNS rebinding 보호를 자동으로 끈다(사내망 가정).
  더 단단히 하려면 `MCP_ALLOWED_HOSTS="<서버호스트>:<mcp포트>,<서버IP>:<mcp포트>"` (또는 nginx 도메인) 지정 —
  이 값도 유닛에 저장돼 자동 유지된다. (지정한 Host 만 허용, 나머지는 421 차단)
- **못 찾은 말이 남는다**(ADR 0025) — 화면 · MCP 의 이름 풀이 · 통합 검색 · 목록 검색이
  아무것도 못 찾은 말을 `search_misses` 표에 남긴다(별칭 후보 — 데이터 품질 화면 · MCP `alias_candidates`).
  **누가 찾았는지는 안 남고** 몇 사람인지(설치의 `JWT_SECRET` 으로 섞은 짧은 지문)만 센다. 숫자뿐인 말 ·
  메일 주소 · 비밀번호처럼 보이는 말 · 긴 글은 거른다. 처리한 줄은 90일, 드문 대기 줄은 180일 뒤 지우고
  대기 줄은 10만이 상한이다 — 앱이 시간마다 스스로 정리하므로 타이머를 따로 둘 것이 없다. 표는
  마이그레이션 `0070_search_misses` 가 만든다(업데이트가 돌린다).
- **사용자 등록(각자)** — 토큰은 화면의 「내 정보」 에서 발급:
  ```bash
  claude mcp add --transport http <slug> http://<서버>:<mcp포트>/mcp \
    --header "Authorization: Bearer <내 토큰>"
  ```
  → 인증은 **사용자별 토큰**이 그대로 백엔드로 전달돼 그 토큰의 범위로 동작한다.
- **남의 토큰 끊기**(퇴사 · 연동 종료 · 유출) — 화면의 관리 › 계정 › 그 사람의 「토큰」 (또는 온톨로지 ›
  코어 › 액세스 토큰)에서 **그 토큰 하나만 폐기**한다. 계정은 그대로다. 계정 **정지는 토큰을 끊지 않는다**
  (정지 동안만 막히고 다시 켜면 이어진다), 계정 **삭제는 전부** 폐기한다. 기록은 감사의
  `account.token_revoked`(누가 · 누구의 · 어느 토큰 · 사유), 토큰 주인에게 알림이 간다. API 로는
  `POST /api/accounts/{계정 id}/tokens/{토큰 id}/revoke` (`{"reason": "…"}`, 시스템 관리자).
- **오프라인 휠이 없던 빌드**라면 MCP 만 설치가 건너뛰어진다(백엔드는 정상). 그땐 호스트에서
  `cd <INSTALL_DIR>/mcp_server && ./venv/bin/pip install -r requirements.txt` 후 서비스 재기동.

---

## 5b-2. 작업 워커 — 일괄 입력이 여기서 돈다

`deploy.sh install`/`update` 가 `<slug>-worker.service` 를 함께 설치한다. 일괄 입력(객체 · 관계)은
보내는 순간 **작업**이 되고, 이 워커가 집어 돌린다 — 앱과 **같은 SIF · 같은 .env**, `python -m app.worker`.
설계는 저장소의 `docs/작업-워커-설계.md`.

- **없으면 일괄 입력이 영영 「대기」 다.** 화면의 「작업」 이 「워커가 살아 있지 않습니다」 라고 띄운다.
  `sudo systemctl status <slug>-worker` / 로그 `journalctl -u <slug>-worker -f`.
- **웹훅도 여기서 나간다**(2026-09-21부터). 앱만 살아 있고 워커가 죽어 있으면 바깥 시스템으로
  가는 알림이 멎는다 — 보낼 기록은 표에 쌓여 있다가 워커가 살아나면 그대로 나간다. **작업으로 한 일은
  작업마다 웹훅 한 건**(무엇이 몇 건인지 · 작업 번호)이다 — 0.4.50 까지는 작업으로 한 일은 웹훅 ·
  지켜보기 알림이 아예 안 갔다(0.4.51 부터 간다 — 받는 쪽이 일괄 입력 요약을 처음 받게 된다).
- **이중화면 A · B 에 하나씩 뜬다.** 같은 작업을 두 번 안 집는다(DB 의 `SKIP LOCKED`). 한 대가 죽으면
  그 대가 하던 작업은 5분 뒤 다른 대가 되살린다(3번까지, 그 뒤 「실패」 로 남긴다).
- **워커가 한 시간마다 치운다**: 작업 파일 7일(`JOB_FILE_TTL_DAYS`) · **끝난 작업 기록 30일**
  (`JOB_TTL_DAYS`) · **요청 시간 기록 30일**(`LOG_RETENTION_DAYS`, 아래 5e) · **접근 로그 1년**
  (`ACCESS_LOG_RETENTION_DAYS`, 0 이면 안 지움 — 2026-10-10 전에는 지우지 않아 쌓이기만 했다. **감사 기록
  (변경 이력)은 지우지 않는다** — 객체 이력 · 되돌리기가 기댄다). 도는 작업은 아무리 오래돼도 안 지운다. 손으로 당기려면
  `POST /api/jobs/maintenance/purge`(시스템 관리자).
- **고아 첨부 파일도 워커가 하루에 한 번 지운다**(2026-10-08부터, ADR 0024) — 첨부를 지우면 행만 지우고
  파일은 남겼다(같은 내용을 여러 첨부가 가리킬 수 있어서). 살아 있는 첨부가 하나도 안 가리키고 **하루가
  지난** 파일과 끊긴 업로드의 임시 파일(`_incoming/`)만 지운다 — 저장소 모양(`ab/cd/<sha256>`)이 아닌
  것은 손대지 않으므로 `FILESTORE_DIR` 아래에 데이터 소스 폴더(`incoming/`)를 둬도 된다. 주기는 `.env` 의
  `FILESTORE_GC_HOURS`(기본 24, `0` 이면 끈다). 워커가 뜨고 한 시간 뒤가 첫 차례다. 지우기 전에 세어
  보려면 서버 화면의 「고아 파일 지금 세어 보기」 → 「작업」 에서 계획을 읽고 적용. **지운 파일은
  되살릴 수 없다** — 백업 덤프에는 파일이 안 들므로 `filestore/` 를 따로 백업하는 설치는 그 주기를 본다.
- **작업 파일 — 작은 것은 DB, 큰 것은 공용 저장소**(2026-10-10부터). 올린 파일(일괄 입력 CSV · 묶음 ·
  사진 zip)과 작업이 만든 결과 파일(내보내기)은 `JOB_FILE_INLINE_MAX_BYTES`(기본 32MB) 이하면 DB
  (`job_files.data`)에, 넘으면 **`FILESTORE_DIR/_jobs/<id>`** 에 놓이고 DB 에는 경로만 남는다. 이중화면
  `FILESTORE_DIR` 이 두 서버의 공용 폴더(`/data/<slug>/filestore`)라 어느 서버에서 올려도 어느 워커가
  집어도 읽는다. 7일(`JOB_FILE_TTL_DAYS`) 뒤 위의 한 시간 정리가 행과 그 파일을 함께 지운다 — 행 없이
  남은 파일(받다 끊긴 `*.part` · 넣다 실패한 것)도 하루가 지나면 지운다. 첨부의 고아 정리는 `_jobs/` 를
  안 본다(작업 파일은 첨부가 아니다).
- **상한은 종류마다**(`.env`): 표 파일(일괄 입력 · 묶음) `JOB_FILE_MAX_BYTES`(기본 100MB — 처리가 통째로
  메모리에 올려 푼다), **사진 일괄 업로드의 zip `JOB_PHOTOS_MAX_BYTES`(기본 2GB)** — 받을 때도 처리할 때도
  파일로 흘려 다룬다. 결과 파일(내보내기) `JOB_OUTPUT_MAX_BYTES`(512MB — 10만 행이 넘는 내보내기는 zip 으로
  나간다). 화면은 이 값을 서버에 물어 보내기 전에 견준다.
- **디스크** — 사진 zip 하나가 앱 서버에서 두 번 놓인다: 받는 동안 **앱의 임시 폴더**(`TMPDIR`, 기본
  `/tmp` — Apptainer 는 호스트의 `/tmp` 를 쓴다), 다 받으면 `_jobs/` 로 옮긴다(임시 파일은 요청이 끝나면
  지워진다). 동시에 올리는 사람 수 × 2GB 만큼 `/tmp` 에 여유가 있어야 하고, 공용 폴더는 7일 치 작업 파일
  만큼 더 찬다 — 서버 화면 「쌓인 것」 의 「작업 파일 (DB … · 저장소 …)」 로 본다. `/tmp` 가 작으면
  `sudo systemctl edit <slug>` 로 `Environment=APPTAINERENV_TMPDIR=<컨테이너 안 경로>` 를 더한다 —
  컨테이너에 보이는 곳이어야 한다(기본 bind 는 `/tmp` · `/data/filestore` · `/data/logs`). `.env` 에 적으면
  안 먹는다(앱 설정이 아니라 프로세스 환경이다). `filestore/` 를 따로 백업하는 설치는 `_jobs/` 를 빼도
  된다(임시물이다).
- **첨부 한 파일 `ATTACHMENT_MAX_BYTES`(기본 500MB, 2026-10-16 — 그 전에는 50MB 고정)** — 객체 첨부 · 게시판 ·
  사진 일괄 업로드의 한 장 · MCP 업로드가 함께 쓴다. 넘는 업로드는 **본문을 받기 전에** 413 이다(밝힌 크기로
  끊는다 — 화면은 서버에 상한을 물어 넘는 파일을 아예 보내지 않는다). 받는 동안 앱의 임시 폴더(`/tmp`)에 한
  벌 놓이므로 동시 업로드 수 × 상한만큼 여유를 본다. 이중화 nginx 의 `client_max_body_size 512m` 를 넘겨
  올리면 그 조각도 함께 올리고(안 올리면 nginx 가 413), 업로드는 요청 안에서 받으므로 nginx 의 600초 안에
  끝나는 크기인지도 본다. HWAX 포털에 붙은 설치는 포털 nginx 의 상한도 따로 있다(포털 담당에게 묻는다).
- **누가 무엇을 보나 `VIEW_SCOPE`(기본 `all`, 2026-10-22 — ADR 0034)** — `all` 은 부서와 상관없이 가입자
  누구나 모든 자료를 본다(목록 · 검색 · 그래프 · 지표 · RDF · MCP · 코어 API 가 모두 같다). `department` 는
  예전 규칙인 「전역 + 내가 속한 부서 것만」 이다. 고치는 것은 어느 쪽이든 소유 부서의 관리자다. 바꾸면 앱을
  다시 띄운다. 코어 API 의 받는 쪽은 토큰 주인의 부서와 상관없이 공개 타입 전부를 받게 된다 — `all` 에서
  `department` 로 좁히면 받던 것이 표식 없이 빠지므로 받는 쪽에 「처음부터 다시 받기」 를 알린다.
- **이중화 nginx** — 다른 경로는 `client_max_body_size 512m` 그대로이고 **사진 zip 경로
  (`/<slug>/api/objects/<타입>/photos/import`)만 2100m · 흘려 넘김(`proxy_request_buffering off`)** 이다
  (`main-server-nginx.conf` 의 정규식 location — 메인 서버 쪽에 그 조각을 다시 넣어 달라고 한다).
  `JOB_PHOTOS_MAX_BYTES` 를 올리면 그 값도 함께 올린다 — 안 올리면 큰 zip 은 앱에 닿기 전에 nginx 가
  413 을 낸다.
- **공용 폴더 없는 두 대(리허설, 8.1b)** — 큰 작업 파일이 올린 서버의 로컬 폴더에 놓여, 다른 서버의 워커가
  집으면 「작업 파일 … 이(가) 저장소에 없습니다」(`JOBS-0030`)로 실패한다. 그런 설치는
  `JOB_FILE_INLINE_MAX_BYTES` 를 `JOB_PHOTOS_MAX_BYTES` 이상으로 두어 전부 DB 에 넣는다 — 그때는 bytea 한
  값이 1GB 까지이고 받는 동안 메모리에 모으므로 `JOB_PHOTOS_MAX_BYTES` 도 512MB 쯤으로 낮춘다.
- **백업 덤프에는 작업 파일의 내용이 안 담긴다**(임시물이라 — `backup.sh` 의 `--exclude-table-data=job_files`).
  복구한 서버에서는 그 작업의 파일이 없고, 그때는 다시 올린다.
- 사진 일괄 업로드의 적용이 도중에 실패하면 붙인 행은 모두 무르고, 먼저 저장소에 쓴 사진 파일은 위의 고아
  정리가 하루 뒤에 지운다.
- **워커는 앱과 같은 조립으로 뜬다**(2026-10-10) — 기동 로그에 앱과 같은 「… 기동」 한 줄이 함께 찍힌다.
  예전 워커는 앱의 등록(첨부 자리 · 부서 자료 종류 …)을 몰라, 사진 일괄 업로드의 적용이 객체의 부서 대신
  전역으로 판정됐다(부서 관리자는 거절). 이 판 이전에 그렇게 붙은 사진은 소유 부서가 비어 있다(전역 —
  시스템 관리자만 수정).
- **부서 자료 이동(통폐합)도 작업이다**(2026-10-10) — 부서 관리의 「자료 이동」 이 계획(무엇이 몇 건 ·
  막히는 것)을 보이고, 적용은 한 트랜잭션이라 도중에 취소 · 실패하면 아무것도 안 옮겨진다. 한 부서 객체
  20만 개면 적용이 90초 남짓이고 그동안 그 부서 객체를 고치는 요청은 기다린다 — 사람이 적은 시간에 한다.
  이동이 안 끝난 부서는 삭제를 막는다(`WORKSPACES-0017`).
- **일괄 입력의 「고치기」 는 덩어리로 쓰고, 참조 색인 트리거는 달라진 줄만 고친다**(2026-10-10,
  마이그레이션 `0075_refs_trigger`, ADR 0010 결정 14). 이미 있는 객체를 고치는 줄을 5천 줄씩 UPDATE 한
  문장으로 쓰고, 트리거(`object_refs_updated`)는 참조 칸이 달라진 줄만 지우고 넣는다 — 서비스 기록
  5만 줄 한 칸 고치기 적용이 44초 남짓 → 23초 남짓(관계 2만 줄 근거 고치기 14초 → 4초). 0075 는
  **함수 몸만 바꾼다**(`CREATE OR REPLACE FUNCTION` — 표를 잠그지 않고 색인을 다시 채우지 않아 바로
  끝난다. 색인의 내용은 그대로다). 이 판 전에는 객체를 덩어리로 고치는 일(종류 변경 · 병합 · 부서 자료
  이동)이, 같은 연결이 앞서 한 줄짜리 갱신을 했으면 덩어리마다 수십 초로 느려질 수 있었다 — 트리거의
  SQL 계획이 연결마다 첫 호출에서 굳어 큰 문장에도 그대로 쓰였다(5천 줄 덩어리 1.5초 → 40초, 실측).
  업데이트 뒤에도 그렇게 흔들리면 0075 가 안 돈 것이다(`sudo ./deploy.sh update`). SIF 만 되돌려도
  (3. 갱신) 새 함수는 옛 코드와 맞다 — 그대로 둔다.
- `update` 는 앱과 워커를 함께 멈춘다(하던 작업은 최대 2분 기다린다) — 옛 코드가 새 마이그레이션 위에서
  작업을 집으면 안 된다.
- 로그의 **「작업 … 은 이제 이 워커(…)의 것이 아닙니다 — 결과를 버리고 롤백합니다」** 는 고장이 아니다.
  그 워커가 5분 넘게 멎은 사이(VM 정지 · DB 끊김) 다른 대가 작업을 되살려 다시 집었고, 원래 워커가
  깨어나 **커밋하려는 순간** 그것을 보고 물러난 것이다 — 같은 적용이 두 번 들어가지 않는다(2026-10-08).
  동기화 차례라면 「이 차례에서 이미 돈 소스」 가 함께 적힌다(그 소스들은 이미 들어갔고 이어받은 쪽이
  한 번 더 「그대로」 로 돈다).

## 5c. 데이터 소스 동기화 타이머

`deploy.sh install`/`update` 가 `<slug>-sync.timer` 를 함께 설치한다 — 5분마다 「몇 분마다」 가
정해진 데이터 소스(관리 › 데이터 소스) 중 차례가 된 것을 **작업으로 넣는다**(돌리는 것은 워커,
5b-2). 앱과 **같은 SIF·같은 .env** 로 `scripts/sync_datasources.py --due` 를 실행한다. 진행 · 결과는
화면 「작업」 과 소스 화면의 기록에 남는다. 워커를 거치지 않고 그 자리에서 돌려 보려면(진단)
`--slug <소스> --now`.

- **한 차례는 작업 하나**(「데이터 소스 동기화(차례대로)」)다 — 차례가 된 소스들을 **가리키는 쪽부터
  한 줄로** 돌린다(참조 칸 · 관계의 도착 타입을 정의에서 읽는다 — 메커니즘 → 고장 모드 → 시험군).
  예전에는 소스마다 작업을 따로 넣어 순서가 없었고, 가리키는 쪽이 늦으면 선이 끝점을 못 찾았다.
  한 소스가 실패해도 차례는 끝까지 간다. 앞 차례가 안 끝났으면 이번 차례는 건너뛴다.
- **끝점을 아직 못 찾은 선**은 나머지를 막지 않는다 — 소스에 「기다리는 선」 으로 남았다가 다음
  동기화가 다시 넣는다(형제 코어 소스). 실행 기록에 「선 기다림 N」 과 예시가 남는다. 서로 가리키는
  타입(순환)도 이것으로 다음 차례에 선다.
- **상대가 「처음부터 다시」(reset)를 보내면**(오래 안 받아 무덤 보관 기간을 넘겼다) 그 소스의 선은
  전량을 다시 받는다. 5만 줄이 넘으면 여러 차례에 걸치고(기록에 「여러 차례에 나눠 받습니다」),
  **마지막 쪽까지 받은 차례에** 상대에 없는 선을 끊는다 — 그 소스가 이은 선만이다(사람이 화면에서 이은
  선 · 파일로 넣은 선 · 0.4.49 까지 이 소스가 이은 선은 누가 이었는지 가를 기록이 없어 안 끊는다).
  도중의 차례가 실패하면 아무것도 안 끊고 다음 차례가 같은 자리에서 잇는다. 도중의 차례는 받은 선에
  「이번 다시 받기에서 봤다」 를 따로 적는다(수정 시각은 그대로라 코어 창구 · 지표에 영향이 없다 —
  마이그레이션 0071). **한꺼번에 절반 넘게 안 오면 끊지 않고 실패로 적는다** — 상대 토큰의 부서 · 공개 범위가
  바뀌었는지 먼저 본다. 정말 끊긴 것이면 화면에서 끊거나 관계 파일을 「파일대로 맞춤」 으로 넣는다.

- **상태**: `systemctl list-timers <slug>-sync.timer` / 로그 `journalctl -u <slug>-sync`
- **끄기**: `sudo SYNC_ENABLED=0 ./deploy.sh update` (유닛은 `systemctl disable --now <slug>-sync.timer`)
- **손으로 한 번**: `sudo systemctl start <slug>-sync` (차례가 된 것만) — 하나만 지정해 돌리려면
  화면의 「동기화」 나 컨테이너 안에서 `scripts/sync_datasources.py --slug <소스>`.
- 결과는 화면(관리 › 데이터 소스 › 최근 동기화)에도 남는다.
- **파일 소스**(CSV·Excel 을 폴더에 떨어뜨리는 방식)를 쓰려면 `.env` 에 `DATASOURCE_DIR` 을 적는다
  — 컨테이너 안 경로라 bind-mount 아래여야 한다(`/data/filestore/incoming` 권장). 안 적으면
  URL 로만 읽는다.

## 5d. 지표 다시 계산 타이머

지표(공통 › 지표 — 기록을 미리 세어 둔 값, ADR 0013)는 지표마다 정한 주기로 다시 센다.
`deploy.sh install`/`update` 가 `<slug>-metrics.timer` 를 함께 설치한다 — **매시간(:30)** 차례가 된 지표마다
**작업 하나를 넣는다**(세는 것은 워커, 5b-2).

- **「매일 밤」 · 「N일마다 밤」**(주기 24 · 48 …): **밤 시간에만** 센다 — `.env` 의 `METRICS_NIGHT_HOUR`
  (기본 2, 서버 현지 시각). 02:30 은 백업(03:00)보다 앞이라 세어 둔 값까지 덤프에 든다. 차례는 「타이머가
  지난번에 넣은 밤」 에서 센다 — 예전(v0.4.47 까지)에는 계산이 끝난 시각에서 세어, 타이머가 어제보다 몇 분
  일찍 깨는 밤은 건너뛰어 이틀에 한 번꼴로 셀 수 있었다. 서버가 꺼져 밤을 놓쳤으면 아침에 따라잡는다.
- **「N시간마다」**(주기 1 ~ 23): 그 시간이 지났으면 낮에도 센다. 앱과 **같은 SIF·같은 .env**
로 `scripts/recompute_metrics.py --due` 를 실행한다. 적재(데이터 소스 동기화 · 파일 가져오기)가 끝나면
그 타입을 원천으로 쓰는 지표는 타이머를 기다리지 않고 바로 작업이 들어간다.

- **상태**: `systemctl list-timers <slug>-metrics.timer` / 로그 `journalctl -u <slug>-metrics`
- **끄기**: `sudo METRICS_ENABLED=0 ./deploy.sh update` (유닛은 `systemctl disable --now <slug>-metrics.timer`)
- **손으로 한 번**: `sudo systemctl start <slug>-metrics` (차례가 된 것만) — 하나만 지정해 돌리려면
  화면의 「지금 다시 계산」 이나 컨테이너 안에서 `scripts/recompute_metrics.py --slug <지표>`.
- 결과는 화면(공통 › 지표 › 지표를 열고 「계산 기록」 탭)과 홈의 「남은 일」(실패 · 오래 안 셈)에 남는다.
- 200만 건을 묶을 때 `work_mem` 이 모자라면 느리다 — `.env` 의 `METRICS_WORK_MEM`(기본 256MB).
- **타이머 · 적재 뒤의 계산은 되면 「바뀐 기간만」 센다**(2026-10-08부터, ADR 0024) — 지난 계산 뒤에 바뀐
  기록이 든 기간의 셀만 지우고 다시 넣는다. **전부** 세는 때: 정의를 바꾼 뒤 첫 계산 · 전부 센 지
  `METRICS_FULL_EVERY_DAYS`(기본 7)일이 지남 · 다른 객체 너머(참조 · 관계)가 바뀜 · 바뀐 기록이 원천의
  `METRICS_INCREMENTAL_MAX_SHARE`(기본 0.2)를 넘음 · 감사 없이 바뀐 기록(백필)이 있음 · 시간 칸이 없거나
  방문 · 머무는 기간 지표. 어느 쪽이었는지와 그 까닭은 「계산 기록」 탭의 「방식」 · 「이유 · 메모」 에.
  화면의 「지금 다시 계산」 은 늘 전부 센다(값을 의심할 때 누르는 단추라서). 타이머에서 전부 세게 하려면
  `scripts/recompute_metrics.py --all --full`.
- **날짜 칸 색인** — 바뀐 기간만 읽으려면 켜진 지표의 시간 칸마다 색인이 있어야 한다. 타이머가 지표를
  넣은 **뒤에** 맞춘다(`ix_objects_mt_*`, 기록 5만 건 이상인 타입만, `CREATE INDEX CONCURRENTLY` — 쓰기를
  막지 않는다). 2,000만 건이면 처음 한 번 몇 분 걸린다. 안 쓰게 된 색인과 세우다 끊긴 색인은 다음 차례에
  지운다. 손으로: 컨테이너 안에서 `scripts/recompute_metrics.py --indexes`. 마이그레이션(alembic)은 이
  색인을 모른다 — 앱이 실행 중에 세우고 지우는 것이라 일부러 뺐다.
- **경보**(ADR 0016)는 지표를 다시 센 **같은 작업 안에서** 그 지표에 걸린 경보를 차례로 확인한다 — 따로 타이머가
  없다. 경보 하나의 실패는 계산을 실패로 만들지 않고 그 경보의 주인에게 처음 한 번 알린다. 작업 결과의
  `runs[].alerts` 가 확인한 경보 수와 새 발생 수다.

## 5d-2. 지식 그래프 「전체 지도」 — 판 · 덧붙임 · 집계 (ADR 0046)

지식 그래프 › 탐색 › 「전체 지도」 는 **서버가 미리 놓아 둔 자리**로 그린다. 자리를 정하는 일은 요청이 아니라
**작업**이고, 따로 타이머를 깔지 않는다 — **워커(5b-2)가 5분마다** 차례를 보고 하나를 넣는다(판단은 DB 의
시각이라 워커를 다시 띄워도 밀리지 않는다). 워커가 없으면 지도는 「판이 아직 없습니다」 로 남는다.

| 작업(작업 화면의 이름) | 언제 | 2,000만 건 DB | 200만 건 DB |
| --- | --- | --- | --- |
| 지식 그래프 지도 전체 배치 | 처음 판(기준정보가 있으면 워커가 곧) · 시스템 관리자의 「전체 다시 배치」 | 처음 200초(점 표 채우기 132초 포함) · 다시 49초 | 처음 33초 · 다시 12초 |
| 지식 그래프 지도 덧붙임 | 매일 밤 `GRAPH_MAP_NIGHT_HOUR`(기본 4시, 서버 현지 시각) — 놓쳤으면 30시간 뒤 아무 때나 | 새 것이 없으면 12초 · 새 개발모델 60이면 16초 | 4 ~ 6초 |
| 지식 그래프 지도 집계(+ 확대 점 표 맞춤) | 한 시간마다(지난 집계에서 55분) | 11초(+ 바뀐 기록 2만이면 점 표 8 ~ 9초) | 3초(+ 1초) |

- **겹치지 않는다** — 셋이 같은 자문 잠금을 쥔다(배치는 기다리고, 집계는 배치가 돌면 건너뛴다). 「전체 다시
  배치」 를 두 번 눌러도 작업은 하나다.
- **기준정보(기록 아닌 타입의 객체)가 20만을 넘거나 기록이 `GRAPH_MAP_MAX_RECORDS`(기본 5천만)를 넘으면** 판을
  계산하지 않고 바로 거절한다 — 작업이 실패로 끝나고 그 까닭이 지도 화면에 그대로 뜬다. 정적 그리기 · 탐색은
  그대로 쓸 수 있다.
- **큰 설치의 안전장치(ADR 0049)** — 운영의 고장 추적 DB(293GB)에서 전체 배치가 메모리 31GB 를 다 써 강제
  종료되고, 판이 없으니 5분마다 다시 들어가 한 바퀴마다 임시 파일 50GB 를 썼다. 지금은:

  | | |
  | --- | --- |
  | **끄기** | `.env` 에 `GRAPH_MAP_AUTO=false` → `sudo ./deploy.sh restart`. 워커는 지도 작업을 하나도 넣지 않는다(다른 작업은 그대로). 「전체 다시 배치」 는 시스템 관리자가 누를 때만 돈다 |
  | **실패 · 취소하면 멈춘다** | 전체 배치 · 덧붙임이 실패하거나 취소되면 워커는 다시 넣지 않는다 — 지도 화면에 「자동 계산을 멈췄습니다」 가 뜨고, 시스템 관리자가 「전체 다시 배치」 를 눌러 끝나면 다시 돈다. 집계의 실패는 6시간 뒤 다시 본다 |
  | **한 번만** | 워커가 죽어(메모리 부족 등) 멎은 지도 작업은 되살려 다시 돌리지 않는다 — 다시 돌면 또 죽는다 |
  | **취소가 바로 먹는다** | 지도 작업은 취소하면 도는 질의까지 끊는다(30초 안 — 워커의 박동 차례) |
  | **계산의 상한** | 공동 참조 짝짓기는 참조 줄이 1억 · 짝 줄이 2억을 넘을 것 같으면 기록을 고르게 덜어 짝짓고(판의 `coref_share`), 기준정보마다 위 24개 짝만 받는다. 지도 작업의 임시 파일은 `GRAPH_MAP_TEMP_LIMIT`(기본 20GB)까지 — 권한이 있을 때(5f 의 `READ_TEMP_LIMIT` 와 같은 권한) |
  | **꺼 둔 워커** | `sudo systemctl disable --now <slug>-worker` 로 끈 워커는 `update` 가 다시 켜지 않는다(`stop` 만 하면 다음 `update` 가 켠다). 다시 켜기: `sudo systemctl enable --now <slug>-worker` |
- **저장 크기**: 큰 것은 **확대 점 표** `graph_map_points`(기록마다 한 줄 — id · 허브 · 해시, 색인 둘) —
  2,000만 건 DB 3.06GB, 200만 건 DB 336MB. 나머지 지도 표(기준정보 자리 · 얼린 수 · 시간마다
  수 · 공동 참조 선)는 합쳐 수십 MB 다.
- **쓰기 비용은 없다** — 지도는 기록을 쓰는 길에 아무것도 안 붙인다. 점 표는 **시간마다의 집계가 바뀐
  기록만**(수정 시각 기준, 지난 맞춤보다 두 시간 앞부터) 다시 맞춘다 — 그래서 새 기록은 다음 집계(한 시간 안)부터
  확대에 점으로 보인다. 허브 칸을 바꾼 뒤의 전체 다시 배치 · 처음 판은 점 표를 **통째로** 다시 채운다(2,000만
  건 DB 약 2분 — 그동안 확대가 기다린다).
- **손으로**: 작업은 전용 경로로만 넣는다(일반 작업 API 로는 안 된다). 전체 다시 배치는 화면의 단추(시스템
  관리자). 지도가 안 서면 「작업」 화면에서 지도 작업의 실패 까닭을 본다.
- **허브 칸**(기록을 어느 기준정보 곁에 둘까)은 온톨로지 › 타입 편집 창의 「지도의 허브 칸」 — 바꾸면 **다음
  전체 다시 배치부터** 반영된다.

## 5d-3. 뜻으로 찾기의 바탕 — pgvector · 임베딩 모델 (ADR 0050)

글을 **뜻의 자리**(숫자 1,024개)로 바꿔 DB 에 담아 두면, 낱말이 달라도(「냉각수 누설」 과 「쿨런트 샘」) 뜻이
가까운 것을 찾는다. 바꾸는 모델(bge-m3)은 **이 서버 안의 Ollama** 가 돌린다 — 글이 서버 밖으로 나가지 않는다.
배포가 둘을 갖춘다.

| | 무엇 | 언제 | 실패하면 |
| --- | --- | --- | --- |
| **pgvector** | DB 확장(`postgresql-16-pgvector`, 우분투 판 0.6). DB 의 `extensions` 스키마에 산다 | `prepare` · `install` · `reset`(마이그레이션 앞) · `update`(서비스를 내리기 전) | **안 멈춘다** — 이 판에는 그것을 쓰는 표가 아직 없다. 경고를 찍고, 서버 화면의 「뜻으로 찾기」 가 말한다 |
| **Ollama + bge-m3** | 임베딩 모델 서버(`ollama.service`, 127.0.0.1:11434) + 모델 1.2GB | `install` · `update` 의 끝(앱이 뜬 뒤) | **안 멈춘다** — 앱은 그대로 돌고, 서버 화면의 「뜻으로 찾기」 가 무엇이 빠졌는지 말한다 |

**따로 칠 것은 없다 — `update` 하나로 갖춰진다.** 스크립트로 돌려도 된다: apt 는 묻지 않게 부르고
(needrestart 의 「어느 서비스를 다시 띄울까」 창 없음 · 다른 서비스를 다시 띄우지 않음), 받는 진행 막대는
터미널일 때만 찍는다. 처음 한 번은 Ollama 1.4GB · 모델 1.2GB 를 받느라 몇 분 더 걸린다(앱은 이미 떠 있다).
**쓰는 표가 드는 판부터는** pgvector 를 못 갖추면 서비스를 내리기 전에 멈춘다 — 그 전에 이 판으로 한 번
올려 두면 그때는 이미 있다.

- **따로 돌리기**: `sudo ./deploy.sh pgvector`(확장만) · `sudo ./deploy.sh ollama`(모델만 — 고친 뒤 다시).
- **Ollama 는 서버에 하나** — 이 서버의 플랫폼들과 다른 제품(보고서 보관함 같은)이 함께 쓴다. 이미 깔려
  있으면(누가 깔았든) 판도 유닛도 건드리지 않고 모델만 확인한다. 새로 깔 때는 박아 둔 판(`OLLAMA_VERSION`,
  `deploy.sh` — 개발 PC 에서 bge-m3 을 재 본 판)을 `/usr/local` 에 푼다. 공식 설치 스크립트는 쓰지 않는다 —
  NVIDIA 드라이버까지 깔려 든다.
- **GPU** — NVIDIA 드라이버가 있으면 Ollama 가 저절로 쓴다(`update` 의 마지막 줄 「임베딩: bge-m3 · 1024차원 ·
  GPU — …」). 없으면 CPU 로 돈다 — 화면에서 고른 칸만 바꾸므로 돌기는 하지만, 기록 수백만 건을 처음 채우는
  일은 GPU 가 있어야 하루 안에 끝난다(개발 PC RTX 3070: 초당 약 86건).
- **끄기**: `.env` 에 `EMBEDDING_BACKEND=off` → `sudo ./deploy.sh restart`. 배포도 Ollama 를 깔지 않는다.
  다른 서버의 Ollama 를 쓰면 `OLLAMA_BASE_URL=http://<그 서버>:11434` — 이 서버에는 깔지 않는다.
- **폐쇄망** — pgvector 는 번들의 `pgvector_debs/` 로 깐다(따로 할 일 없음). Ollama · 모델은 **임베딩 꾸러미**를
  인터넷이 되는 PC 에서 한 번 만들어(저장소의 `./deploy/build_ai_bundle.sh` → `release/ai-bundle-<판>-bge-m3.tar`,
  약 2.6GB · sudo 필요 없음) 옮긴 뒤 번들 폴더에 푼다:

  ```bash
  tar xf ai-bundle-v0.30.10-bge-m3.tar -C <번들 폴더>    # → <번들 폴더>/ai/
  sudo ./deploy.sh update                                 # 또는 이미 갱신했으면 sudo ./deploy.sh ollama
  # 다른 자리에 풀었으면: sudo AI_BUNDLE=<풀어 둔 ai 폴더> ./deploy.sh ollama
  ```

  꾸러미는 앱 판과 무관하다 — 한 번 만들어 두고 서버를 새로 세울 때마다 쓴다. 이미 깔린 서버는 다시 안 받는다.
- **디스크** — Ollama 약 2GB(`/usr/local/lib/ollama`, GPU 라이브러리 포함) + 모델 1.2GB
  (`/usr/share/ollama/.ollama/models`). DB 는 **뜻의 자리를 담은 객체 하나에 약 14KB**(실측 — 표 5.7KB + 찾는
  색인 8.2KB) — 100만 건이면 약 14GB, 1,000만 건이면 약 140GB. 무엇을 담을지는 관리자가 칸마다 고른다(고른 것만 늘어난다).
- **복구** — 덤프에 pgvector 가 들어 있으면 `restore.sh` 가 pg_restore 앞에서 확장을 만든다. 확장은 postgres
  계정만 만들므로 **DB 가 있는 서버에서 `sudo` 로** 돌린다.
- **이중화** — 패키지는 두 서버 다(승격하는 날 필요하다), 확장은 주에서 만들어 복제로 온다. 갱신은 B 먼저인데
  B 는 확장을 만들 수 없다 — B 는 패키지만 깔고 지나가고, 이어지는 A 의 갱신이 만든다. 순서를 바꿀 것도 따로
  칠 것도 없다.

## 5e. 느린 요청 · 큰 응답 — 어디가 느린가 (ADR 0026)

작업 목록 한 번이 93MB · 4.4초였던 것을 사용자가 「적용을 누르면 화면이 너무 느리다」 고 말하고 나서야
알았다(2026-10-09). 그 뒤로 앱은 **요청마다 걸린 시간 · 응답 크기를 남긴다** — 조회(GET)까지 전부, 폴링 ·
헬스체크만 뺀다. 따로 설치할 것이 없다(마이그레이션 `0073_access_timing` 이 표를 만든다).

- **보는 곳**: 관리 › 서버 › **「느린 요청 · 큰 응답」** 탭. 기간(최근 1시간 · 24시간 · 7일)마다 경로 틀
  (`/api/objects/{type_slug}/{object_id}` 처럼 값 자리를 비운 경로)과 메서드별 호출 수 · p50 · p95 · 최대
  시간 · 평균/최대 응답 크기. 「느린 순 · 큰 순 · 호출 많은 순」 으로 정렬하고, **줄을 클릭하면** 그 경로의
  최근 최악 사례 20건(누가 · 언제 · 상태 · 걸린 시간 · 크기 · 요청 ID). 아래에는 **작업 종류마다** 끝난
  수 · 소요(시작 → 끝) · 대기(들어옴 → 시작) · 아직 대기인 것 중 가장 오래 기다린 것.
- **요청 ID 로 로그를 찾는다**: 서버에서 `grep <요청 ID> ~/apps/<slug>/logs/app.log*`(컨테이너 안에서는
  `/data/logs`) — 그 요청의 모든 줄(경고 · 오류)이 나온다.
- **걸린 시간은 응답의 마지막 바이트를 보낼 때까지**다(첫 바이트까지가 아니다). 그래서 큰 응답 · 내려받기는
  받는 쪽 회선이 느리면 그만큼 길게 남는다 — **파일 응답**(JSON 이 아닌 것)에 「파일」 표시가 붙고 아래
  문턱에서 빠지는 이유다.
- **홈 「남은 일」**: 최근 24시간에 문턱을 넘은 경로가 있으면 시스템 관리자의 홈에 「느린 요청」 · 「큰 응답」
  이 그 경로 수와 함께 뜬다(5분마다 다시 판정). `.env` 로 바꾼다(고친 뒤 `sudo ./deploy.sh restart`):

  | 설정 | 기본 | 뜻 |
  | --- | --- | --- |
  | `SLOW_REQUEST_P95_SECONDS` | 3 | 경로의 p95(스무 번 중 한 번만 이보다 느리다)가 이것을 넘으면 「느린 요청」 |
  | `SLOW_REQUEST_MIN_CALLS` | 5 | 24시간에 이만큼 호출된 경로만 p95 를 본다 — 기동 직후 찬 캐시의 한두 번이 하루 내내 뜨지 않게 |
  | `LARGE_RESPONSE_MB` | 5 | 이보다 큰 응답을 한 번이라도 낸 경로는 「큰 응답」 |
  | `READ_QUERY_SECONDS` | 60 | 목록 · 통계 · 추이 **한 질의의 시간 상한**(ADR 0031). 넘으면 끊고 504 「좁히거나 지표로」(503 이 아니다 — 이중화 nginx 가 503 이면 다른 서버에 다시 보낸다). 0 이면 안 건다. MCP 의 읽기 대기(90초)보다 짧게. 작업(내보내기 · 지표 계산)에는 안 걸린다 |
  | `LOG_RETENTION_DAYS` | 30 | 요청 시간 기록을 이만큼 두고 워커가 한 시간마다 지운다(파일 로그와 같은 값) |

- **기록은 몇 초씩 모아 넣는다**(프로세스마다, 3초 또는 500건) — 요청마다 DB 에 쓰면 그 왕복이 다른 요청을
  막는다. 그래서 화면은 몇 초 늦게 보고, 앱이 죽으면 마지막 몇 초가 사라진다. DB 가 막혀 5만 건이 밀리면
  그 뒤의 것은 버리고 app.log 에 「요청 시간 기록 N줄을 버렸습니다」 를 남긴다 — 성능을 보려는 기록이 성능을
  해치면 안 된다.
- **표 크기**: 요청마다 한 줄(색인 포함 약 230바이트) — 하루 10만 요청 × 30일이면 약 0.7GB. 「쌓인 것」 에는
  안 뜬다(셀 때마다 표를 훑게 된다). 크다 싶으면 `LOG_RETENTION_DAYS` 를 줄인다 — 다음 정리부터 한 시간에
  백만 줄씩 지운다.
- **끊긴 질문**(ADR 0031) — 시간 상한(`READ_QUERY_SECONDS`)에 걸린 목록 · 통계와 SPARQL 의 거절(그래프가 큼 ·
  빠진 기록 타입)을 같은 모양끼리 묶어 같은 화면에 보인다(`cut_questions` 표 — 모양만, 조건의 값 · 검색어는
  안 남긴다). 최근 24시간에 있으면 홈 「남은 일」 에도 뜬다. 자주 끊기는 물음은 지표로 미리 세어 둘 후보다.
  보관 기간은 요청 시간 기록과 같다(`LOG_RETENTION_DAYS`).
- **기록이 아주 많으면**(기간에 100만 건 넘게) 화면은 최근 100만 건만 세고 그렇다고 말한다 — 묶어서 백분위를
  내는 데 70만 줄이 0.6초였다. 기간을 좁히면 전부 센다.
- **접근 로그(`access_logs`)와는 다른 표다.** 접근 로그는 상태를 바꾼 요청만(사용자 지원용) 남기고, 지금은
  지우는 주기가 없다.

## 5f. PostgreSQL 설정 맞추기 — 기록이 수백만 건을 넘으면 (ADR 0031)

PostgreSQL 은 설치한 기본값(공유 버퍼 128MB · 작업 메모리 4MB · 디스크 비용은 회전 디스크 기준)으로 돌고, 배포는
그 설정을 건드리지 않는다. 기록 2,000만 건에서 「기록 전부를 프로젝트별로」 같은 통계가 기본값이면 27~34초,
작업 메모리 64MB · 병렬 작업자 4면 22초였다(개발 PC, 다른 일과 같이 돌아 흔들린다). 두 층이다.

- **요청마다 거는 것 — `.env`(앱이 건다, 개발 · 운영 같음).** 목록 · 통계 · 추이 한 번에만 걸리고 끝나면 풀린다.

  | 설정 | 기본 | 뜻 |
  | --- | --- | --- |
  | `READ_WORK_MEM` | 64MB | 그 요청의 정렬 · 해시 메모리. 정렬 하나마다 · 작업자마다 잡혀 동시 요청이 많으면 그 곱 — 크게 잡지 않는다 |
  | `READ_PARALLEL_WORKERS` | 4 | 그 요청이 나눠 쓸 병렬 작업자 수. 서버의 `max_parallel_workers` 안에서만 뜬다. -1 이면 서버 설정 |
  | `READ_QUERY_SECONDS` | 60 | 질의 하나의 시간 상한(5e 위 표) |
  | `READ_TEMP_LIMIT` | 5GB | 그 요청의 **임시 파일 상한**(ADR 0048) — 정렬 · 해시가 디스크로 넘어간 것. 넘으면 그 질의만 끊고 507 「범위를 좁혀」. superuser 만 바꾸는 설정이라 `prepare` · `update` 가 앱 계정에 권한을 준다(PostgreSQL 15부터, 「임시 파일 상한 권한」 줄). 권한이 없으면 안 걸린다. 비우면 안 건다 |

- **오래 도는 읽기의 안전장치(ADR 0048)** — 지식 그래프의 그림(정적 그리기 · 타입 전체 표시 · 이웃)과 영향
  범위는 질의를 여러 번 돌린다. 사람이 화면을 닫거나(요청이 끊기거나) 씨앗 · 단계를 바꾸면 서버가 그 DB 질의를
  취소하고 멈춘다. 로그에는 `499 … 요청한 쪽이 떠나 멈췄습니다` 가 남는다.

  | 설정 | 기본 | 뜻 |
  | --- | --- | --- |
  | `LONG_READ_SECONDS` | 120 | 그 요청 **하나**의 시간 상한(질의 여러 번을 합친 것). 넘으면 취소하고 504. 0 이면 안 건다 |
  | `WHOLE_SLOTS` | 2 | 정적 그리기를 **설치 전체**(이중화의 두 서버 · 워커 모두)에서 동시에 몇 개까지. 넘는 요청은 5초 기다렸다가 429 「앞의 것이 끝나면 다시」. 0 이면 안 센다 |

- **서버 전체에 거는 것 — `sudo ./deploy.sh pg-tune`.** 이 서버의 메모리 · CPU 로 권장값을 내고 지금 값과 견줘
  보인다(**바꾸지 않는다**). `--apply` 면 `ALTER SYSTEM` 으로 적고 다시 읽힌다.

  ```bash
  sudo ./deploy.sh pg-tune                # 지금 값 · 권장값
  sudo ./deploy.sh pg-tune --apply        # 적기 — 공유 버퍼 · 작업 프로세스 수는 재시작해야 바뀐다(말해 준다)
  sudo ./deploy.sh pg-tune --share 0.8    # DB 만 도는 서버(기본 0.5 — 앱 · 워커 · MCP 와 메모리를 나눈다)
  sudo ./deploy.sh pg-tune --hdd          # 회전 디스크(기본은 SSD — random_page_cost 1.1)
  ```

  공유 버퍼 = 몫의 1/4(16GB 까지), 캐시 어림 = 몫의 3/4, 유지보수 메모리 = 몫의 1/16(2GB 까지), 병렬 작업자 = CPU
  수, **임시 파일 상한(`temp_file_limit`) = 데이터 디스크의 1/5(100GB 까지)**. 마지막 것은 작업 · 마이그레이션까지
  모든 DB 프로세스에 걸리는 안전망이다. 무거운 질의 하나가 디스크를 채워 DB 가 통째로 멈추는 대신 그 질의만 실패하고,
  재시작 없이 바뀐다(ADR 0048). **재시작은 스크립트가 하지 않는다** — 한가한 때 `sudo systemctl restart postgresql`(앱은 다음 요청에서 다시
  붙는다). **이중화면 두 서버 다** 돌린다(`ALTER SYSTEM` 은 그 서버의 `postgresql.auto.conf` 에만 적힌다) —
  대기를 먼저 재시작하고 주를 나중에. 되돌리기: `ALTER SYSTEM RESET <이름>`(또는 `RESET ALL`) 뒤 다시 읽히기.
- **개발 PC 도 같은 스크립트다** — 저장소에서 `./deploy/pg-tune.sh --url "<backend/.env 의 DATABASE_URL>"`
  (그 계정이 superuser 면 `--apply` 도). WSL 의 재시작은 `sudo service postgresql restart`. 같은 PostgreSQL 을
  다른 프로젝트도 쓰면 그쪽에도 걸린다.

---

## 6. 한 서버에 여러 플랫폼

같은 번들의 인스턴스든 다른 제품군이든 **slug 하나로 전부 갈린다**:

| | 값 |
| --- | --- |
| 설치 경로 | `~/apps/<slug>` |
| 데이터베이스 · 역할 | `<slug>` |
| systemd 유닛 | `<slug>.service` · `<slug>-mcp.service` · `<slug>-sync.timer` · `<slug>-metrics.timer` |
| 포트 | 설치 때 준 `APP_PORT` (없으면 `BUILD_INFO` 의 `port`) · MCP 는 +2 (플랫폼마다 10씩 벌린다) |

**slug 가 겹치면 서로를 덮어쓴다.** 유닛 이름이 같으면 나중 배포가 앞의 것을
그대로 지우고, 그 사실은 아무 데도 안 적힌다. 설치된 인스턴스는 `/etc/platform-instances/` 에
한 파일씩 있다 — 어느 것이 있는지는 `ls` 로 본다.

---

## 7. 겪게 될 것들

전부 **증상이 원인을 안 가리키는** 부류다.

| 증상 | 원인과 해결 |
| --- | --- |
| `apptainer: command not found` | `sudo ./deploy.sh prepare` — 공식 PPA 를 더해 깐다. 폐쇄망이면 `.deb` 로 먼저(1. 서버 준비) |
| `prepare` 가 `Unable to locate package apptainer` 로 멈춘다 | **v0.1.0 번들이다** — PPA 없이 apt 에서 찾았다. 새 번들로 다시 돌리거나 `sudo add-apt-repository -y ppa:apptainer/ppa && sudo apt-get update` 후 `prepare` 를 다시 |
| 서비스가 `failed` | `journalctl -u <slug> -n 50` |
| **파일 업로드에서 `Read-only file system`** | `.env` 의 `FILESTORE_DIR` 가 bind-mount 밖을 가리킨다. `/data/filestore` 여야 한다 |
| 동기화가 「… 가 관리하는 타입이라 여기서 넣지 않습니다」 로 실패 | 허브가 내려준(잠긴) 타입이다. 소스 수정 › **출처 이름**에 그 이름을 적는다 — 비우면 소스의 slug 가 이름이 된다 |
| 모든 페이지가 JSON 404 | SIF 에 `frontend/dist` 가 없다. 라우팅 버그처럼 보이지만 아니다 |
| 운영이 development 로 뜬다 | `.env` 에 BOM 이 붙어 **첫 줄 키만 조용히 무시**됐다 |
| 기동 거부: `JWT_SECRET` | 기본값 그대로다. 그것이 의도다 — `.env` 에 난수를 넣는다 |
| `connection refused` (DB) | `systemctl status postgresql`, `.env` 의 포트 확인 |
| 화면이 500, 로그에 "없는 컬럼" | 마이그레이션이 안 돌았다. `sudo ./deploy.sh update` |
| `update` 가 「pgvector 를 깔지 못했습니다」 를 경고한다 | 저장소에 안 닿고 번들에 `.deb` 가 없다(갱신은 끝난다). 닿는 PC 에서 `apt-get download postgresql-16-pgvector` → 서버에서 `sudo dpkg -i` → `sudo ./deploy.sh pgvector`(5d-3). DB 가 다른 서버면 그 서버에서 |
| 서버 화면의 「뜻으로 찾기」 가 「준비 안 됨」 | 그 줄의 까닭대로 — 대개 Ollama 가 없거나 모델을 못 받았다. `sudo ./deploy.sh ollama`, 폐쇄망이면 임베딩 꾸러미(5d-3) |
| 포트가 이미 쓰인다 | 같은 서버의 다른 플랫폼과 겹쳤다. `ss -ltnp 'sport = :<포트>'` |
| 업로드 파일에 `Permission denied` | `sudo chown -R <계정>:<계정> ~/apps/<slug>/filestore` |
| 사진 zip 업로드가 413 · 「Request Entity Too Large」(nginx 의 말) | 메인 서버 nginx 에 사진 zip 경로의 location(2100m)이 없다 — `main-server-nginx.conf` 를 다시 넘긴다(5b-2). 앱의 413 은 「파일이 너무 큽니다」(`JOBS-0001`)로 온다 |
| 작업이 「작업 파일 … 이(가) 저장소에 없습니다」(`JOBS-0030`)로 실패 | 큰 작업 파일은 `FILESTORE_DIR/_jobs/` 에 놓인다 — 두 서버의 `FILESTORE_DIR` 이 같은 공용 폴더가 아니다(5b-2) |
| 업로드 도중 `No space left on device` | 받는 동안 앱의 임시 폴더(`/tmp`)에 먼저 놓인다 — `TMPDIR` 을 넓은 곳으로(5b-2) |
| 지표 「계산 기록」 이 늘 「전량」 · 「정의가 바뀌었다(또는 예전 판에서 센 셀이다)」 | 0.4.48 이하에서 센 셀이다 — 한 번 전부 센 뒤부터 바뀐 기간만 센다. 계속 그러면 「이유 · 메모」 의 까닭을 본다(일주일 · 참조 너머 · 감사 없는 백필) |
| 지표의 「바뀐 기간만」 이 전부 세는 것만큼 느리다 | 날짜 칸 색인이 아직 없다(`\di ix_objects_mt_*`). 타이머가 다음 차례에 세운다 — 기다리지 않으려면 `scripts/recompute_metrics.py --indexes` |
| **`bad interpreter: /usr/bin/env bash^M`** | 스크립트가 Windows 를 거치며 CRLF 가 됐다. 번들에서 푼 것을 그대로 쓴다(리눅스에서 만들어진다). 이미 섞였으면 `sed -i 's/\r$//' *.sh` |
| `운영 계정을 알 수 없습니다` | `root` 로 바로 ssh 했다. `OPERATOR=<계정> ./deploy.sh install` |
| `reset` 이 입력을 못 받고 끝난다 | TTY 가 없다. `ssh -t <계정>@<서버>` 로 붙는다 |
| MCP 가 `python3 -m venv 실패` | `sudo apt install python3-venv` 후 `sudo ./deploy.sh update` |
| Claude 에서 MCP 가 421 `Invalid Host header` | 비-localhost 로 열었는데 `MCP_ALLOWED_HOSTS` 가 안 맞는다. 5b 참고 |
| 이중화: postgres 가 안 뜨고 로그에 `pg-ha:` | 옛 주가 주로 뜨려 했다(guard). `sudo ./deploy.sh db-standby --from <지금 주>` |
| 이중화: 화면이 `/<slug>/` 밑에서 API 404 | `.env` 에 `PUBLIC_PATH=/<slug>` 가 없거나 nginx 가 접두어를 안 뗐다. `sudo ./deploy.sh render` 로 설정을 본다 |
| 이중화: 로그인이 유지되지 않는다(새로고침마다 로그인) | `.env` 에 `REFRESH_COOKIE_SECURE=true` 가 있는데 http 로 들어왔다 — 지우거나 false 로(앱이 https 일 때 스스로 붙인다). **고친 뒤 `sudo ./deploy.sh restart`.** 또는 `TRUST_PROXY` 가 꺼져 앱이 https 인 줄 모른다 |
| 이중화: B 의 `install` 이 「DB 는 대기입니다」 로 멈춘다 | `/data/…/.env` 가 없다 — A 에서 `install` 을 먼저 |
| 이중화: 대기의 복제가 `끊김` | 주의 pg_hba 에 대기 IP 가 없거나 `/etc/pg-ha.replpass` 가 다르다. 주에서 `db-primary` 다시 → 대기에서 `db-standby` |

### `.env` 를 고친 뒤에는

**`.env` 는 프로세스가 뜰 때 한 번만 읽는다.** 파일만 고치면 도는 것은 옛 값을 그대로 쓰고,
그 사실은 아무 데도 안 뜬다 — 「고쳤는데 왜 그대로지」 를 한참 찾게 된다.

```bash
sudo ./deploy.sh restart      # 앱 · 작업 워커 · MCP 를 함께
```

유닛 이름을 외울 필요가 없다 — 스크립트가 이 설치의 slug 를 안다. 무엇을 껐다 켜는지만
보려면 `./deploy.sh units`(root 없이 된다). 켜고 끄기만 하려면 `start` · `stop`.

포트를 바꿨으면 그것으로 충분하다 — **Apptainer 는 호스트 네트워크를 그대로 쓴다**
(포트 매핑이 없다).

---

## 8. 이중화 — 서버 두 대 · 메인 서버 뒤 · 자동 전환

설계와 결정은 저장소의 `docs/이중화-배포-설계.md`. 여기는 **손으로 치는 순서**다.

```
사용자 · 외부 AI ──HTTPS──▶ 메인 서버 (포탈 + nginx — 메인 서버 쪽이 관리)
                              /<slug>/… → 접두어를 벗겨 A · B 로 분배
                         ┌──────────┴──────────┐
                    서버 A                  서버 B          둘 다 활성 (앱 :8040 · MCP :8042)
                    PostgreSQL 주 ── 복제 ──▶ 대기          DB VIP(있으면) 가 주를 따라간다
                         └──── /data/<slug>/ 공용 ────┘      첨부 · 백업 · .env
```

| 무엇이 어디에 | |
| --- | --- |
| 코드(SIF) · 로그 · MCP venv | 각 서버 `~/apps/<slug>` (`INSTALL_DIR`) |
| 첨부 · 백업 · `.env` · 복제 비밀번호 | `/data/<slug>/{filestore,backup,.env,db/}` (`DATA_DIR` — 경로는 env) |
| DB 원본 | 각 서버 로컬(`/var/lib/postgresql/16/main`) — `/data` 는 느려서 두지 않는다 |
| TLS · 로드밸런싱 · 접두어 벗기기 | **메인 서버의 nginx.** 우리는 넣어 달라고 할 조각(`~/apps/<slug>/main-server-nginx.conf`)을 만든다 |
| (HWAX 포털에 붙일 때) | **HWAX 는 위 조각을 쓰지 않는다** — 포털 nginx 는 경로 목록(`routes.local.env`)으로 location 을 만든다. 9절 |
| 호스트 수준 설정 | `/etc/platform-ha.conf` (역할 · 상대 IP · DB VIP · 호스트명) — 그 서버의 모든 플랫폼이 공유 |
| DB 주/대기 도구 | `/usr/local/sbin/pg-ha` (deploy.sh 가 깐다) · `/etc/pg-ha.conf` · `/etc/pg-ha.replpass` |

**운영 계정은 두 서버에서 같은 이름 · 같은 uid** 여야 한다 — `/data` 의 파일을 둘 다 읽고 써야 한다.

메인 서버가 없이 A · B 가 직접 받아야 하면 `LB_MODE=local WEB_VIP=<VIP>` — A · B 에 nginx + keepalived 웹 VIP + 자체 서명 인증서를 세운다(8.9).

### 8.1 최초 설치 — A(주) 먼저, 그다음 B

**가장 쉬운 길은 `setup` 이다.** 물음에 답하면 아래 단계를 순서대로 알아서 하고, 답한 값은
`/etc/platform-instances/<slug>.conf` · `/etc/platform-ha.conf` 에 남아 **다음부터는
`sudo ./deploy.sh update` 만** 치면 된다. 처음 하는 사람은 [번들의 「쉬운 설치」](쉬운-설치.md)를 본다.

```bash
# ── 서버 A ──   물음: 주(A) · 플랫폼 이름 · 화면 이름 · 포트 · 확장 · B 의 IP · 호스트명 · 공용 폴더
sudo ./deploy.sh setup
# ── 서버 B ──   물음: 대기(B) · 플랫폼 이름 · A 의 IP · 호스트명 · 공용 폴더 · A 의 계정
sudo ./deploy.sh setup           # A 에서 .env · 복제 비밀번호를 scp 로 받아 온다(A 계정 비밀번호를 한 번 묻는다)
```

`setup` 이 하는 것을 손으로 하면 이렇다(환경 변수 이름은 §0):

```bash
# ── 서버 A (주) ──
sudo APP_SLUG=<slug> APP_NAME=<이름> APP_PORT=<포트> \
  HA_ROLE=master PEER_IP=<B의 IP> PUBLIC_HOST=<호스트명> DATA_DIR=/data/<slug> \
  ./deploy.sh prepare              # 패키지(postgresql-16 · keepalived) · DB 역할
sudo ./deploy.sh db-primary        # 복제 계정 · pg_hba · 감시 훅 · 원복 잠금. 비밀번호를 /data/…/db/ 에 둔다
sudo ./deploy.sh install           # .env(/data 에) · SIF · 마이그레이션 · 시드 · 유닛 · 메인 서버용 nginx 조각

# ── 서버 B (대기) ──  (같은 APP_* 값으로)
sudo APP_SLUG=<slug> APP_NAME=<이름> APP_PORT=<포트> \
  HA_ROLE=backup PEER_IP=<A의 IP> PUBLIC_HOST=<호스트명> DATA_DIR=/data/<slug> \
  ./deploy.sh prepare
sudo ./deploy.sh db-standby        # A 에서 pg_basebackup — 기존 로컬 DB 는 옆으로 치운다
sudo ./deploy.sh install           # .env 는 /data 의 것을 그대로(만들지 않는다) · 마이그레이션은 이미 돼 있어 통과

# ── 메인 서버 쪽에 ──
cat ~/apps/<slug>/main-server-nginx.conf   # 이것을 그대로 넣어 달라고 한다

# 확인 (양쪽)
sudo ./deploy.sh status            # 앱 · 상대 앱 · 메인 서버 경유 health · pg-ha 역할 · 복제 지연 · VIP
```

**메인 서버 쪽에 부탁할 것** — 조각에 그대로 있다: `X-Forwarded-Proto $scheme` (앱이 https 인 줄 알아야 쿠키가 산다). 접두어 `/<slug>/` 는 벗겨 넘기든 그대로 넘기든 앱이 둘 다 받는다. MCP 경로는 `proxy_buffering off` · 긴 타임아웃.
**HWAX 포털은 이 조각을 쓰지 않는다** — 포털 nginx 는 경로 목록으로 location 을 만들고, 우리가 넘길 것은 조각이 아니라
요청서(주소 · 큰 요청 갈래 · 타일)다. 9절.

**메인 서버가 아직 없어도** A · B 설치와 8.3 · 8.4 의 리허설은 전부 된다 — 화면은 `http://<A의 IP>:8040/<slug>/` 로 직접 본다(접두어를 붙여서). http 로 직접 보는 동안은 쿠키에 Secure 가 안 붙을 뿐 로그인 유지는 된다.

DB VIP 가 있으면 A 의 첫 명령부터 `DB_VIP=<주소>` 를 함께 준다. **없으면** 앱은 A 의 IP 로 DB 에 붙고 자동 승격은 꺼진다 — 받은 뒤 「8.5」.

### 8.1b DB VIP 도 `/data` 도 아직 없을 때 — 리허설은 된다

| 없는 것 | 대신 |
| --- | --- |
| **DB VIP** | `DB_VIP` 를 안 주면 keepalived 도 자동 승격도 없다. 앱은 A 의 IP 로 DB 에 붙는다. 승격은 손으로(8.3 「DB VIP 없이」). guard 는 VIP 없이도 상대에게 물어 동작한다. 받으면 8.5 |
| **`/data/<slug>`** | `DATA_DIR` 를 안 주면 각 서버 `~/apps/<slug>` 에 전부 둔다. **B 의 `.env` 와 복제 비밀번호는 A 의 것**이어야 한다 — `setup` 이 A 의 `~/apps/<slug>/handoff/` 에서 scp 로 받아 온다(손으로 하면 `scp <A>:~/apps/<slug>/handoff/.env ~/apps/<slug>/.env`, 복제 비밀번호는 `/etc/pg-ha.replpass` 에 root:postgres 640). 첨부는 서버마다 따로 쌓인다(리허설이면 그것으로 충분). 나중에 `/data` 가 오면 `~/apps/<slug>/{.env,filestore}` 를 옮기고 `sudo DATA_DIR=/data/<slug> ./deploy.sh update` — 양쪽 |

리허설에서 볼 수 있는 것: 복제(`db-status` 의 지연), 손 승격 · demote · `db-standby` 재구성, guard(옛 주 부팅), 두 앱 동시 운영(동시 편집 · 타이머 한 대만 · 웹훅 한 번만), B→A 업데이트, 재부팅. 못 보는 것: 자동 승격, 공용 첨부, 백업 폴더, 메인 서버 경유.

### 8.2 업데이트 — B 먼저, 그다음 A

```bash
# B 에서 (새 번들 안에서)
sudo ./deploy.sh update            # B 앱 중지 → SIF 교체 → 마이그레이션(주 DB 에 — 한 번만 돈다) → 기동
# A 에서
sudo ./deploy.sh update
```

한 대씩 하므로 **서비스는 끊기지 않는다** — 메인 서버의 nginx 가 멈춘 쪽을 빼고 보낸다(`max_fails=3`). 마이그레이션은 어느 서버에서 돌려도 주 DB 로 가고 두 번째는 할 일이 없다. 파괴적 마이그레이션(컬럼 삭제)은 옛 SIF 가 아직 도는 몇 분 동안 오류를 낼 수 있다 — 그런 릴리스는 두 대를 빠르게 잇달아 한다. 앱 포트 · MCP 포트 · slug 가 바뀌지 않는 한 메인 서버의 조각은 그대로다. **단 v0.4.48 에서 조각에
`location /<slug>/assets/` 가 더해졌다** — 한 대씩 올리는 동안 두 서버의 판이 달라, 화면 조각을 옛 판 서버에
물으면 404 가 나던 것을 다른 서버에서 받게 한다. 그 판으로 처음 올릴 때 새 `main-server-nginx.conf` 를 메인
서버 쪽에 한 번 다시 넘긴다.

### 8.3 장애 — 무엇이 죽었나

| 죽은 것 | 저절로 | 사람이 |
| --- | --- | --- |
| **앱 한 대** | 메인 서버 nginx 가 3번 실패 뒤 뺀다. 살아나면 다시 넣는다 | `journalctl -u <slug>` |
| **서버 B(대기) 통째** | 아무 일도 없다. 복제만 멈춘다 | 살아나면 복제가 이어진다. `sudo ./deploy.sh db-status` 로 지연 확인. 오래 죽어 슬롯이 버려졌으면(`max_slot_wal_keep_size`) `db-standby` 로 다시 |
| **서버 A(주) 통째** | **DB VIP 가 있으면** 약 15초 뒤 B 가 승격되고 DB VIP → B. 마지막 몇 초의 쓰기는 유실될 수 있다. 앱은 B 만 남는다 | A 가 살아나도 **주로 못 뜬다**(guard). 「8.4」 대로 A 를 대기로 |
| **주 DB 만**(A 의 postgres) | 위와 같다(`pg-ha check` 가 3번 실패 → VIP 이동 → 승격) | 같다 |
| **DB VIP 없이 A 통째** | 앱은 B 만 남지만 **DB 를 잃는다** | B 에서 `sudo ./deploy.sh db-promote` → `/data/…/.env` 의 `DATABASE_URL` 호스트를 B 로 → `sudo systemctl restart <slug>` |
| **메인 서버** | 아무도 못 들어온다 — 메인 서버 쪽 일 | A · B 는 그대로 돈다. 급하면 `http://<A>:8040/<slug>/` 로 직접(접두어를 붙여서. http 라 리프레시 쿠키는 안 산다 — 확인용으로만) |
| **/data 가 안 보인다** | 첨부 · 백업이 멈춘다. 앱 재시작은 `.env` 를 못 읽어 실패한다(떠 있는 앱은 계속 돈다) | 마운트를 살린다. 그동안은 앱을 재시작하지 않는다 |

### 8.4 승격 뒤 원복 — 옛 주를 대기로, 그리고 (원하면) 다시 주로

승격된 채로 운영해도 된다 — **B 가 주인 것은 정상 상태다.** 필요한 것은 옛 주 A 를 대기로 돌려 다시 두 대가 되게 하는 것뿐이다.

```bash
# A 에서 — A 의 옛 데이터는 /var/lib/postgresql/16/main.old-<시각> 으로 옆에 남는다(한 벌만)
sudo ./deploy.sh db-standby --from <B의 IP>
sudo ./deploy.sh db-status        # 「대기 · streaming ← B」
```

굳이 A 를 다시 주로 하려면 **계획 전환** — 수십 초 중단, 유실 0:

```bash
# B(지금 주) 에서: 곱게 멈추고 「주 아님」 표시. 대기 A 가 남은 WAL 을 다 받는다
sudo ./deploy.sh db-demote
# A 에서: 승격. DB VIP 가 있으면 저절로 따라온다(check-primary)
sudo ./deploy.sh db-promote
# B 에서: A 의 대기로 재구성
sudo ./deploy.sh db-standby --from <A의 IP>
```

DB VIP 가 없을 때는 각 단계 사이에 `/data/…/.env` 의 `DATABASE_URL` 을 바꾸고 양쪽 앱을 재시작한다.

### 8.5 DB VIP 를 나중에 받았을 때

```bash
# 양쪽 모두
sudo DB_VIP=<주소> ./deploy.sh lb           # keepalived 에 DB VIP 인스턴스 · 감시 · 자동 승격
# 주 서버에서
sudo ./deploy.sh db-primary                  # /etc/pg-ha.conf 에 VIP 를 적는다(감시가 그것을 본다)
# /data/…/.env 의 DATABASE_URL 호스트를 VIP 로 → 양쪽 sudo systemctl restart <slug>
```

### 8.6 재부팅 점검

부팅 순서는 유닛이 정한다 — PostgreSQL → keepalived(그 뒤에 떠야 「아직 안 뜬 주」 를 죽었다고 보지 않는다) → 앱. 켠 뒤:

```bash
sudo ./deploy.sh status
```

- 「역할」 이 기대와 같은가(A 주 · B 대기). 옛 주가 guard 에 막혀 postgres 가 안 떴으면 `journalctl -u postgresql@16-main` 에 「pg-ha: …standby --from…」 이 있다 — 그대로 한다.
- DB VIP 가 주 DB 서버에 있나.
- 메인 서버 경유 health 가 200 인가.

### 8.7 스플릿 브레인 — 두 주가 되는 것을 막는 세 겹

1. **guard**(systemd `ExecStartPre`): 데이터 폴더가 「주」 모양인데 DB VIP 에서 다른 DB 가 응답하거나 상대가 주라고 답하면 postgres 를 **띄우지 않는다**.
2. **check-primary**: 내가 주라도 DB VIP 를 남이 쥐고 거기서 DB 가 응답하면 우선순위 +50 을 잃는다 — VIP 를 되찾지 못한다.
3. **demote 표시**(`/var/lib/pg-ha/demoted`): 계획 전환으로 내려온 주는 `db-standby` 전까지 안 뜬다.

셋 다 **주로 뜨는 것**만 막고 대기로 뜨는 것은 막지 않는다. 막혔을 때 할 일은 언제나 같다 — `db-standby --from <지금 주>`.

### 8.8 한 서버 두 대에 여러 플랫폼

`/etc/platform-ha.conf` · keepalived · PostgreSQL 주/대기는 **호스트에 하나**다. 두 번째 플랫폼(같은 번들의 다른 인스턴스든 다른 제품군이든)은 다른 `APP_SLUG` · `APP_PORT` 로 `prepare` → `install` 만 하면 메인 서버용 조각이 하나 더 생기고 같은 PostgreSQL 클러스터에 DB 하나가 더 생긴다(복제도 저절로 함께). `db-primary` · `db-standby` 는 **클러스터에 한 번**이면 된다 — 두 번째 플랫폼에서 다시 돌리면 pg_hba 만 갱신되고 같다.

### 8.9 메인 서버 없이 — A · B 가 직접 받을 때 (`LB_MODE=local`)

`LB_MODE=local WEB_VIP=<VIP>` 를 더해 8.1 을 그대로 하면 A · B 에 nginx(TLS 종단 · 접두어 벗기기 · 두 앱 분배)와 keepalived 웹 VIP(vrid 51, master 역할 서버가 쥔다)가 선다. 인증서는 A 가 자체 서명으로 만들어 `/data/…/tls/` 에 두고 B 가 가져간다. 정식 인증서를 받으면 두 서버의 `/etc/nginx/tls/server.{crt,key}` 를 바꾸고 `sudo systemctl reload nginx`.

---

## 9. HWAX 포털에 붙이기 — `https://hwax.sec.samsung.net/<id>/` · 포털 로그인 · Claude(게이트웨이)

HWAX 포털(사내 앱 모음)이 `/<id>/` 경로로 이 앱을 넘겨 주고, 타일을 누르면 포털에 로그인한 그 사람으로 들어오게 하며
(jwt-handoff — 북마크로 `/<id>/` 를 바로 열어도 포털 안이면 화면이 포털의 launch 로 저절로 로그인한다), 사람들의 Claude 는 **포털의 MCP 게이트웨이**가 사람마다 그 사람 명의의 토큰을 받아 이 설치의 MCP 를 부른다
(사람별 위임). **Report Archive 를 붙였을 때와 같은 방식이다** — 결정과 까닭은 저장소의 `docs/adr/0028-HWAX-포털에-붙이기.md`,
통합 정리는 `docs/HWAX-PORTAL-INTEGRATION.md`. **이중화 조각(`main-server-nginx.conf`)은 쓰지 않는다** — 포털 nginx 는 경로
목록으로 location 을 만든다. 포털의 앱 id `<id>` 는 **`APP_SLUG` 와 같게** 둔다(타일 id · audience · 게이트웨이 키 · 권한
id 가 전부 그 하나다).

**인스턴스마다 한 번씩**이다 — 같은 번들로 띄운 인스턴스(「자주검증 Datahub」 · 「Failure Trace」 …)는 slug · 포트 · `.env`
가 달라 각자의 요청서를 보낸다. 아래 명령은 전부 `APP_SLUG=<slug>` 를 준다(이 서버에 인스턴스가 하나면 생략된다).

### 9.1 순서

```bash
# ① 요청서 + 게이트웨이 위임 비밀 — 비밀이 .env 에 없으면 만들어 적는다(있으면 그대로). 요청서는 이 설치의 값으로 채워진다
sudo APP_SLUG=<slug> ./deploy.sh hwax-request          # → ~/apps/<slug>/HWAX-연동요청서.md
sudo APP_SLUG=<slug> ./deploy.sh restart               # 비밀을 새로 만들었으면 — 앱이 읽게(이중화면 다른 서버에서도)
# ② 게이트웨이가 MCP 에 닿게(한 번 — 유닛에 남는다). 이중화면 이미 0.0.0.0 이다
sudo APP_SLUG=<slug> MCP_HOST=0.0.0.0 ./deploy.sh update
# ③ 요청서를 포털 담당에게 — A · B 주소와 위임 비밀은 **따로**(①이 화면에 「따로 전할 것」 으로 찍는다)
sudo grep '^HEAX_SSO_SECRET=' ~<운영 계정>/apps/<slug>/.env   # (이중화면 /data/<공통폴더>/<slug>/.env)
# ④ 포털 담당에게서 JWKS 주소를 받으면 — 준 것만 .env 에 적는다
sudo APP_SLUG=<slug> PORTAL_JWKS_URL=http://<포털>:8088/.well-known/jwks.json \
     PORTAL_LAUNCH_URL=https://hwax.sec.samsung.net/launch/<slug> ./deploy.sh update
# ⑤ (선택) 위임 발급을 포털 박스 주소에서만 받게
sudo APP_SLUG=<slug> HEAX_SSO_ALLOWED_IPS=<포털 박스 주소> ./deploy.sh update
```

`hwax-request` 는 그 설치의 `.env` 와 설치 폴더만 만진다 — 운영 계정으로 sudo 없이도 된다(`./deploy.sh hwax-request`).
요청서에는 위임 비밀도 내부 주소도 **안 쓴다**(메일 · 메신저로 도는 파일이다). 이미 포털에 전한 비밀은 바꾸지 않는다 —
바꾸면 그 순간 게이트웨이의 위임이 전부 401 이다(바꿀 때는 포털 담당과 같이: `.env` 에서 지우고 ① 다시 → 새 값을 전한다).

`deploy.sh` 는 `PORTAL_*` · `HEAX_SSO_ALLOWED_IPS` 를 **준 것만** `.env` 에 적고(빈 값을 주면 비운다 — 끄는 길),
`PORTAL_JWKS_URL` 이 있는데 `PUBLIC_PATH` · `TRUST_PROXY` 가 없으면 `/<slug>` · `true` 를 더한다. 위임 비밀은 env 로 받지
않는다(화면에 찍히고 셸 기록에 남는다). 나머지는 `.env` 를 손으로 고치고 `sudo ./deploy.sh restart`:

| `.env` | 값 | 왜 |
| --- | --- | --- |
| `PUBLIC_PATH` | `/<id>` | 포털이 `/<id>/` 를 벗겨 넘긴다 — 화면 주소 · 쿠키 path · 로그인 뒤 돌아갈 곳이 그 아래여야 한다 |
| `TRUST_PROXY` | `true` | 포털 nginx 가 주는 https 판정(쿠키 Secure) · 접속 주소를 믿는다 |
| `PORTAL_JWKS_URL` | 포털 담당이 준 주소 | **비우면 포털 로그인이 꺼진다** — 콜백이 404 JSON |
| `PORTAL_LAUNCH_URL` | `https://hwax.sec.samsung.net/launch/<id>` | 로그인 화면의 「HWAX 포털로 로그인」. 비우면 단추가 없다 |
| `PORTAL_AUDIENCE` · `PORTAL_ISSUER` | (보통 안 적는다) | 기본이 `APP_SLUG` · `https://hwax.sec.samsung.net` — 포털 id · 포털 `JWT_ISSUER` 가 다를 때만 |
| `HEAX_SSO_SECRET` | `hwax-request` 가 만든다(64자) | **비우면 게이트웨이 위임이 꺼진다** — `/api/auth/sso` 가 404 JSON, 포털의 Claude 가 이 설치를 못 부른다 |
| `HEAX_SSO_ALLOWED_IPS` | 포털 박스 주소(쉼표 · CIDR) | 주소를 보지 않는다(비밀만). 못 읽는 값이 있으면 **모두 거절** |
| `MCP_PUBLIC_URL` | `http://<이 서버>:<MCP 포트>/mcp` | 자기 토큰으로 직접 붙는 사람의 안내 주소(5b). 비우면 화면이 없는 길(`https://<포털>/<id>/mcp`)을 짐작한다 |

방화벽: 포털 박스(nginx · 게이트웨이) → 이 서버 `:<앱 포트>`(화면 · 위임 발급) · `:<MCP 포트>`(게이트웨이 → MCP). 이 서버 →
포털 `:8088`(JWKS). `MCP_ALLOWED_HOSTS` 를 적어 둔 설치면 게이트웨이가 부르는 주소를 **포트까지**(`<이 서버 주소>:<MCP 포트>`,
B 도) 넣는다 — 아니면 MCP 가 421 로 거절한다.

### 9.2 포털 담당에게 줄 것 · 받을 것

- **줄 것** — ①이 쓴 요청서 한 장 + **A · B 의 내부 주소와 위임 비밀**(따로 — 안전한 길로). 요지는 RA 와 같다: 화면 라우트
  `<id>=http://<A>:<앱 포트>/`(접두어 벗김) · `loc_extras()` 에 `<id>` · jwt-handoff 타일 · 게이트웨이 백엔드 `<id>`(우리 MCP
  주소) · 사람별 위임 쌍(`PER_USER_SSO_APPS` 의 `<id>:<접두>` · `<접두>_SSO_SECRET` · `<접두>_SSO_URL=http://<A>:<앱 포트>/api/auth/sso`)
  · 권한 표 한 블록 · 위험 도구 목록(한 번). **MCP 를 포털 nginx 경로(`/<id>/mcp`)로 열어 달라고 하지 않는다.**
- **권하는 것** — 포털 `update-all` 에 목록형 단계 한 번(요청서 9절, `SP_APPS="<id>:<접두> …"` + `<접두>_HOST`) — 그 뒤로는
  인스턴스를 더할 때 주소 한 줄이다.
- **받을 것** — 이 서버에서 닿는 **포털 JWKS 주소**(사내망 `http://<포털>:8088/.well-known/jwks.json` 이 간단 — https 면
  사내 CA 체인도), 그리고 포털에서 찍은 **콜백 숫자 · 위임 점검 숫자**(9.3 의 표).
- **B 전환** — A 가 죽으면 포털 담당이 주소를 B 로 바꾸고 `update-all` 을 다시 돌린다(RA 와 같다). 우리 쪽 DB 승격은 8.3.

### 9.3 확인

```bash
# 이 서버에서 — 포털 JWKS 가 닿나 · 위임이 켜졌나(점검 창구 — 토큰 · 계정을 건드리지 않는다)
curl -s -o /dev/null -w '%{http_code}\n' http://<포털>:8088/.well-known/jwks.json             # 200
S=$(sudo sed -n 's/^HEAX_SSO_SECRET=//p' <.env 경로>)
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:<앱 포트>/api/auth/sso/verify -H "X-Heax-Gateway-Secret: $S"   # 204
# 포털 경유 — 콜백에 가짜 토큰(포털 update-all 의 진단과 같은 것)
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' -X POST \
  https://hwax.sec.samsung.net/<id>/api/auth/portal-callback -d token=probe-not-a-token       # 303 …/<id>/login?sso=invalid
```

| 콜백 숫자 | 뜻 | 할 일 |
| --- | --- | --- |
| **303** | 다 섰다 | — |
| 404 + JSON | 포털 로그인이 꺼져 있다 | `.env` 의 `PORTAL_JWKS_URL` → `sudo ./deploy.sh restart` |
| 405 | 콜백이 없는 옛 판 | 새 번들로 `sudo ./deploy.sh update` |
| 200 + HTML · 404 + HTML | 포털 nginx 에 라우트가 없다 · 경로가 어긋났다 | 포털 담당(`routes.local.env`) |
| 502 · 504 · 000 | 포털이 이 서버 `:<앱 포트>` 에 못 닿는다 | 방화벽 · `systemctl status <slug>` |

| 위임 숫자(`/api/auth/sso` · `/sso/verify`) | 뜻 | 할 일 |
| --- | --- | --- |
| **204**(점검 `/sso/verify`) · **400**(`/sso` 에 이메일 없이) · **200**(승인된 사람으로) | 켜짐 · 비밀 맞음 | — |
| 401 + 한 줄 | 비밀이 다르다 | 포털 담당과 같은 값인지(따로 전한 것) |
| 403 + 한 줄 | 처음 보는 사람 · 승인 대기 · 정지 · 허용 주소 밖 — 본문이 무엇인지 말한다 | 사람이면 포털에서 타일을 한 번 열고(가입 신청) 승인 · 주소면 `HEAX_SSO_ALLOWED_IPS` |
| 404 + JSON | 위임이 꺼져 있다 | `HEAX_SSO_SECRET` → `sudo ./deploy.sh restart` |
| 405 | 위임이 없는 옛 판(`/sso/verify` 는 v0.4.57 까지 없다) | 새 번들로 `sudo ./deploy.sh update` |

화면에서는 **관리 › 서버 › 「HWAX 포털 로그인」**(시스템 관리자) — 켜졌나 · 포털 공개 키를 지금 받아 보고 닿나 · kid · 마지막
오류 · 설정끼리 안 맞는 것(접두어 없음 · `TRUST_PROXY` 꺼짐 · `MCP_PUBLIC_URL` …), 그리고 「게이트웨이 위임」 — 켜졌나 · 허용
주소 · 범위 · 지금 사용 중인 위임 토큰 수 · 마지막 발급 · 마지막 거절(때 · 숫자 · 까닭). 그다음 포털 타일을 눌러 첫 화면이
뜨는지, 포털의 Claude 에서 `<id>_whoami` 가 그 사람인지 본다.

| 증상 | 원인 |
| --- | --- |
| 로그인 화면에 「포털의 공개 키를 받지 못해…」 | 이 서버에서 `PORTAL_JWKS_URL` 이 안 닿는다 — 위 curl · 방화벽 :8088. 서버 화면의 「마지막 오류」 |
| 「로그인 정보를 확인하지 못했습니다」 가 계속 | 포털 타일의 `audience` 와 `PORTAL_AUDIENCE`(기본 slug)가 다르거나 포털 `JWT_ISSUER` 를 바꿔 쓰는 박스 — 앱 로그 `journalctl -u <slug> \| grep '포털 로그인 거절'` 에 사유(InvalidAudience · InvalidIssuer …)가 있다 |
| 「유효 시간을 벗어났습니다」 | 이 서버와 포털의 시계가 10초 넘게 어긋났다 — `timedatectl` |
| 로그인 뒤 포털 첫 화면으로 간다 | `PUBLIC_PATH` 가 없다(9.1) |
| 처음 들어온 사람이 「가입 승인을 기다립니다」 | 정상 — 관리 › 계정의 승인 대기(홈 「남은 일」). 희망 부서가 없어 「승인」 이 부서를 묻는다 |
| 포털의 Claude 가 「… 자격증명으로 호출하지 못했습니다 (SSO 403: …)」 | 위임 거절 — 괄호 안이 까닭(승인된 계정 없음 · 대기 · 정지 · 주소). 서버 화면의 「마지막 거절」 · `journalctl -u <slug> \| grep '게이트웨이 위임 거절'` |
| 포털의 Claude 가 「… (SSO 401: …)」 | 게이트웨이의 비밀과 `HEAX_SSO_SECRET` 이 다르다 — 포털 담당과 맞춘다 |
| 포털의 Claude 에 이 설치의 도구가 없다 | 게이트웨이가 MCP 에 못 닿는다 — `MCP_HOST` 가 127.0.0.1 · 방화벽 `:<MCP 포트>` · `MCP_ALLOWED_HOSTS` 에 게이트웨이가 부르는 주소(포트까지)가 없다(421) |
| 위임 토큰이 「내 정보」 에 「HWAX 게이트웨이(gateway)」 로 보인다 | 정상 — 게이트웨이가 반나절마다 새로 받고 지난 것은 폐기된 줄로 하나만 남는다. 폐기해도 게이트웨이가 다시 받는다 — 사람을 끊는 것은 계정 정지 |
