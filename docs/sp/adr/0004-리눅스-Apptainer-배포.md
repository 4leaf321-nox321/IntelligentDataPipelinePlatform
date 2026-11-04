# 0004. 배포는 리눅스 · Apptainer 단일 이미지로 한다

- 상태: 채택
- 날짜: 2026-09-10
- 대체: 이 틀의 초기 Windows 서버 배포(NSSM 서비스 + `C:\Server\tools` 스크립트)

## 배경

이 틀을 얹을 서버가 대부분 리눅스다. 초기 배포 방식은 MatNexus 의 Windows 절차를
그대로 옮긴 것이었다 — 그 방식은 Windows 서버에서 잘 먹혔지만, 옮겨 갈 곳이
리눅스라면 **잘 먹힌 경험이 통째로 자산이 아니다.**

한편 같은 WSL 안에 `ReportArchive` 가 이미 Apptainer 로 돌고 있다. 운영하는 사람이
같다면, **두 플랫폼의 배포가 서로 달라서 얻는 것은 없다.** 헷갈릴 자리만 는다.

## 결정

**ReportArchive 와 같은 지형을 쓴다.** 새로 설계하지 않는다.

    SIF 한 개 · systemd 유닛 · 호스트 Postgres · bind-mount 셋

### 왜 Apptainer 인가 (Docker 가 아니라)

| | Apptainer | Docker |
| --- | --- | --- |
| 이미지 | **파일 하나**(`app.sif`) — scp 로 옮긴다 | 레지스트리나 `save/load` |
| 데몬 | 없음. 프로세스가 그냥 뜬다 | 데몬이 있어야 한다 |
| 권한 | 사용자 권한으로 실행 | 보통 root 그룹 |
| 네트워크 | **호스트 네트워크** — 포트 매핑이 없다 | 매핑을 적어야 한다 |

폐쇄망에 `tar.gz` 하나를 옮겨 푸는 것이 배포의 전부여야 한다는 요구와 정확히 맞는다.
레지스트리를 둘 수 없는 망에서 Docker 는 결국 `save/load` 로 가는데, 그러면
Apptainer 의 단일 파일과 같은 일을 더 많은 단계로 하는 셈이다.

호스트 네트워크는 값이면서 함정이다 — **`PORT` 가 곧 호스트의 포트다.** 겹치면
나중에 뜬 쪽이 그냥 못 뜬다. ADR 0003 의 「플랫폼마다 10씩」 규칙이 여기서 효력을
갖는다.

### 이미지는 읽기 전용, 쓰는 곳은 셋뿐

```
--bind <install>/.env:/opt/app/backend/.env:ro
--bind <install>/filestore:/data/filestore
--bind <install>/logs:/data/logs
```

`.env` 를 이미지에 굽지 않는 이유는 그것이 **설치마다 다른 값**이라서다. 구우면
비밀번호를 바꾸는 데 이미지를 다시 만들어야 하고, 그러면 아무도 안 바꾼다.

### 이미지가 자기 배치를 스스로 안다

`apptainer.def` 의 `%environment` 가 `LOG_DIR=/data/logs` 와
`FILESTORE_DIR=/data/filestore` 를 내보낸다. `.env` 가 기억하게 두지 않는다 —
빠뜨린 날 앱이 이미지 안에 쓰려다 `[Errno 30] Read-only file system` 으로 죽고,
**그 메시지는 무엇을 고쳐야 하는지 말해 주지 않는다.** 실측으로 겪었다.

같은 이유로 `%files` 가 `BUILD_INFO.txt` 를 이미지에 넣는다. 없으면 `/api/health`
가 `version: unknown` 을 돌려주고, 「지금 서버에 뭐가 깔렸나」 를 물을 자리가
사라진다. 이것도 실측으로 나왔다.

### 번들이 자기가 무슨 플랫폼인지 말한다

`build_bundle.sh` 가 `branding.py`·`config.py` 를 파싱해 `BUILD_INFO` 를 쓰고,
`deploy.sh` 가 그것을 읽어 DB 이름·유닛 이름·설치 경로·포트를 정한다.

**이것이 ReportArchive 와 다른 유일한 지점이고, 의도한 차이다.** 이 저장소는
포크되라고 있다. 배포 스크립트에 제품 이름이 한 글자라도 박혀 있으면 포크한 사람이
그 자리를 고쳐야 하고, 빠뜨린 날 새 플랫폼이 **옆 플랫폼의 DB 를 마이그레이션한다.**
`tests/architecture/test_deploy.py` 가 배포 자산에 플랫폼 이름이 섞였는지 본다.

### Python 은 3.12

ubuntu:24.04 가 apt 로 주는 버전이다. 더 높은 버전을 쓰려면 PPA 나 소스 빌드가
필요한데, 그것은 **이미지가 커지는 대신 아무 기능도 안 늘어나는 거래**다.

## 대안

### Windows 배포를 유지하고 리눅스를 얹는다

두 벌이 된다. 그리고 두 벌 중 한쪽은 **아무도 안 돌려 본 쪽**이 된다 — 그 사실은
그쪽에 배포하려는 날에야 드러난다. Windows 자산은 `70_StandardPlatform` 폴더에
그대로 남겨 두었다. 참고는 되되 따라오지는 않는다.

### 리눅스에서도 컨테이너 없이 venv + systemd

간단하다. 하지만 폐쇄망에서 `pip install` 이 성립하지 않고, 그러면 wheel 을 미리
받아 옮기는 절차가 새로 생긴다 — SIF 하나를 옮기는 것보다 단계가 는다. 그리고
호스트의 Python 버전에 배포가 묶인다.

### Docker Compose

Postgres 까지 컨테이너로 묶을 수 있다. 하지만 이 조직의 서버들은 이미 호스트
Postgres 를 운영하고 백업 절차가 그것을 전제한다. 컨테이너 Postgres 로 옮기면
**배포가 아니라 DB 운영을 바꾸는 일**이 되고, 그것은 이 결정의 범위가 아니다.

## 결과

- 배포는 `tar.gz` 하나 → `deploy.sh install` 이다. ReportArchive 와 같은 손놀림이다.
- 앱이 못 쓰는 경로를 가리키면 **기동 가드**가 무엇을 고쳐야 하는지 말하며 죽는다
  (`app/main.py` 의 `_guard_writable_paths`). 트레이스백을 읽을 일이 없다.
- 배포 자산에 제품 이름이 없다. 포크는 `branding.py` 만 고치면 된다.
- Windows 배포는 이 저장소에 없다. 되살리려면 이 ADR을 대체하는 결정이 필요하다.
