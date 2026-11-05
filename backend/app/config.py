"""설정 — DB행 -> 환경변수 -> 기본값 3단 fallback.

세 번째 단(DB행)은 아직 비어 있지만 자리를 지금 만들어 둔다. 자주 바뀌는 값
(워커 수·타임아웃·임계값)을 나중에 관리 화면에서 고치려면 **읽는 지점이 한 곳**
이어야 하기 때문이다. os.getenv 가 코드에 흩어지면 값 하나를 바꾸는 데 재배포가
필요해진다.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.branding import DEFAULT_APP_NAME, DEFAULT_APP_SLUG, DEFAULT_APP_TAGLINE

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent

#: HWAX 포털의 `JWT_ISSUER` 기본값 — launch 토큰의 `iss`(ADR 0028).
DEFAULT_PORTAL_ISSUER = "https://hwax.sec.samsung.net"


class Settings(BaseSettings):
    # utf-8-sig 로 읽는다. BOM 이 붙은 .env 는 **첫 줄 키만 조용히 무시된다** —
    # 그 키가 기본값으로 떨어지므로, 운영이 development 로 떠서 reload 가 켜진 채
    # 돈다. 서버에서 메모장으로 .env 를 고치기만 해도 같은 일이 나므로 읽는 쪽에서
    # 흡수한다.
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8-sig", extra="ignore"
    )

    app_env: str = "development"
    """development | production. 기동 방식과 로그 수준을 가른다."""

    # ── 이 설치가 무슨 플랫폼인가 ─────────────────────────────────────────────
    # **번들 하나로 여러 플랫폼을 띄운다.** 허브 · 그룹 쌍둥이들은 같은 코드에 `.env` 만
    # 다르다 — 그래서 이름은 빌드가 아니라 여기(배포 설정)서 온다. 안 주면 틀의 기본값.
    app_slug: str = DEFAULT_APP_SLUG
    """기계가 읽는 이름 — **DB 이름 · 리프레시 쿠키 · 토큰 표식 · 화면 저장소 키가 여기서
    나온다.** 소문자 · 숫자 한 덩어리, 32자 이내(`plmhub` · `simtools`). 같은 서버의 다른
    플랫폼과 같으면 셋이 전부 조용히 부딪힌다(branding.py). 설치 뒤에는 바꾸지 않는다 —
    바꾸면 쿠키 · 토큰이 다 무효가 되고 DB 이름이 어긋난다."""
    app_name: str = DEFAULT_APP_NAME
    """화면 제목 · API 문서 제목 · 기동 로그 · `/api/health` 의 `app`."""
    app_tagline: str = ""
    """한 줄 설명 — 로그인 화면과 사이드바. 비우면 안 뜬다. 이름을 안 준 설치(틀 그대로)만
    틀의 기본 문구가 들어간다 — 이름을 정한 인스턴스 밑에 「공통 틀」 이 붙으면 틀린 말이다."""
    extensions: str = ""
    """이 설치가 켜는 확장 모듈, 쉼표로(`hub,bom`). **코어는 확장을 모른다** — 이름으로
    `app/extensions/<이름>` 을 찾아 라우터 · 훅을 붙이고, 화면에도 같은 목록을 심어 그쪽
    메뉴 · 페이지가 붙는다. 개발에서는 전부 켜 두고, 운영 인스턴스는 자기 것만 켠다."""

    database_url: str = ""
    """비우면 `postgresql+psycopg://postgres:postgres@localhost:5432/<app_slug>` — **DB 이름은
    `app_slug` 에서 나온다.** 따로 적으면 언젠가 안 바뀌고, 그러면 두 플랫폼이 같은 DB 를
    보게 된다. 공통 틀에서 나온 표는 이름이 같아서(users·workspaces) **오류 없이 남의 계정
    표를 읽는다.**

    접속 정보(사용자·비밀번호·호스트)는 설치마다 다르므로 운영 `.env` 는 이 값을 통째로
    적는다(deploy.sh 가 slug 로 DB 이름을 넣는다).

    시험은 이 이름에서 `_test` 를 파생해 쓴다(tests/conftest.py) — 접속 정보를 두 곳에
    적으면 한쪽만 고쳐지고, 그때 시험이 어느 DB 에서 도는지 모른다."""

    host: str = "0.0.0.0"
    port: int = 8080
    public_path: str = ""
    """브라우저가 보는 **주소 접두어** — 여러 플랫폼이 한 호스트명에 경로로 붙을 때
    (`https://hwax.sec.samsung.net/<slug>/` 이면 `/<slug>`). 비우면 루트(`/`).

    앞의 nginx 가 접두어를 **떼고** 앱에 넘긴다(`location /<slug>/ { proxy_pass http://…/; }`).
    앱은 여전히 `/api` · `/assets` 로 받지만, 화면이 부를 주소 · 리프레시 쿠키의 path ·
    문서(`/api/docs`)의 주소는 접두어를 알아야 한다 — `index.html` 에 심어 준다(main.py).
    앞뒤 슬래시는 정리한다: `plm/` → `/plm`."""
    mcp_public_url: str = ""
    """화면이 사람에게 보여 줄 **MCP 주소** — 비우면 화면이 짐작한다.

    MCP 는 앱과 **다른 프로세스 · 다른 포트**(앱 포트 +2)다. 짐작하는 규칙은 둘이다:
    표준 포트(80 · 443)로 들어왔으면 앞에 리버스 프록시가 있는 것이니
    `<origin><접두어>/mcp`(그 nginx 가 그 길을 MCP 로 넘긴다), 앱 포트로 직접 들어왔으면
    `<호스트>:<앱포트+2>/mcp`(접두어 없음).

    그 둘로 안 되는 자리에만 적는다 — 표준이 아닌 포트의 프록시 뒤(`https://호스트:8443/plm/`)
    처럼 브라우저가 알 수 없는 구성. **틀린 주소를 화면이 알려 주면 사람은 그 주소로
    등록하고, 안 붙는 이유를 MCP 서버에서 찾는다.**

    MCP 의 사진 업로드(ADR 0012)도 이 값을 쓴다 — AI 에게 줄 `curl` 의 업로드 주소는
    사용자 PC 가 닿는 앱 주소여야 하는데, 프록시는 `Host` 에서 포트를 떼고 넘긴다. 적혀
    있으면 이 값에서 `/mcp` 를 뗀 것이 앱 주소다. 비어 있거나 MCP 포트를 바로 적은 값이면
    MCP 서버가 위 두 규칙으로 짐작한다."""
    trust_proxy: bool = False
    """앞에 리버스 프록시(nginx)가 있어 `X-Forwarded-For` · `X-Forwarded-Proto` 를 **믿는다.**
    켜면 접근 로그의 클라이언트 주소와 `https` 판정이 프록시가 준 값을 쓴다. 프록시 없이 켜면
    아무나 그 헤더를 위조할 수 있으므로 기본은 끔."""
    """**플랫폼마다 10씩 벌린다** — MatNexus 8010, TestScope 8020, CrossAXTF 8030,
    StandardPlatform 8040, CompCore 8060, SimEngBay 8070, **datarelay 8080**(번호 표의 정본은
    StandardPlatform `docs/새-플랫폼-만들기.md` §3.6).

    **Apptainer 는 호스트 네트워크를 그대로 쓴다**(포트 매핑이 없다). 컨테이너가
    이 포트를 열면 그것이 곧 호스트의 포트다 — 한 서버에 여러 플랫폼을 얹을 때
    겹치면 나중에 뜬 쪽이 그냥 못 뜨고, 그 이유는 journal 에만 남는다.

    0.0.0.0 은 IPv4 전역 바인딩이라 localhost(::1) 로는 닿지 않는다 — 확인은
    127.0.0.1 로 한다. 개발 백엔드는 8081 을 쓴다(run.py)."""

    uvicorn_workers: int = 4
    """운영에서 띄울 워커 수.

    **개발(reload)에서는 무시된다** — uvicorn 은 reload 와 다중 워커를 같이 못 쓴다.
    둘을 함께 주면 한쪽이 조용히 버려지므로 run.py 가 갈라서 준다."""

    attachment_max_bytes: int = 500 * 1024 * 1024
    """**첨부 한 파일**의 상한 — 500MB(2026-10-16, 그 전에는 코드에 50MB 고정). 객체 첨부 ·
    게시판 · 사진 일괄 업로드의 한 장 · MCP 업로드가 함께 쓴다. 넘는 업로드는 **본문을 받기
    전에**(밝힌 크기로) 거절한다. 이중화면 메인 서버 nginx 의 `client_max_body_size 512m`
    안이어야 한다 — 넘기면 그 조각도 함께 올린다(`deploy/ha.sh`). 업로드는 요청 안에서 받으므로
    그 시간(nginx 600초)도 본다."""
    job_file_max_bytes: int = 100 * 1024 * 1024
    """**표 파일**(일괄 입력 · 묶음)의 작업 파일 상한 — 종류가 따로 정하지 않으면 이것이다
    (`kinds.Kind.max_file_setting`). 처리가 파일을 통째로 메모리에 올려 풀므로 상한이 있어야
    한다."""
    job_photos_max_bytes: int = 2 * 1024 * 1024 * 1024
    """**사진 zip**(사진 일괄 업로드)의 상한 — 2GB(2026-10-10). 100MB 이던 때는 휴대폰
    사진(장당 3 ~ 5MB) 스무 장 남짓에서 나눠 올려야 했다. zip 은 받을 때도(흘려 받기) 처리할
    때도(파일로 열어 한 장씩) 통째로 메모리에 올리지 않으므로 표 파일과 상한이 다르다. 큰
    파일은 DB 가 아니라 공용 저장소에 놓인다(`job_file_inline_max_bytes`). 이중화면 메인 서버
    nginx 의 그 경로 `client_max_body_size` 도 함께 본다(`deploy/ha.sh`)."""
    job_file_inline_max_bytes: int = 32 * 1024 * 1024
    """작업 파일을 **DB 에 담는** 상한 — 넘으면 공용 저장소의 작업 폴더
    (`FILESTORE_DIR/_jobs/`)에 두고 행에는 경로만 둔다(`jobs/files.py`, 2026-10-10).

    작은 것은 DB(`job_files.data`)가 낫다 — 서버 두 대가 함께 읽고, 공용 폴더는 다른 사업장이라
    느리다(`docs/sp/작업-워커-설계.md` 3장). 큰 것은 DB 가 못 견딘다: bytea 한 값은 1GB
    까지이고,
    올린 2GB 가 그대로 WAL · 복제로 대기 서버까지 흐르며, 읽으려면 통째로 메모리에 올려야 한다.
    공용 폴더는 첨부가 이미 쓰는 곳이라(두 대가 같은 경로로 마운트) 어느 워커가 집어도 읽는다.
    작업 결과 파일(내보내기)도 같은 문턱을 따른다."""
    job_output_max_bytes: int = 512 * 1024 * 1024
    """작업이 **만드는** 결과 파일의 상한 — 올리는 파일(`job_file_max_bytes`)과 따로 둔다.
    기록 200만 건 내보내기는 zip 으로도 128MB 였다(실측). 큰 결과는 공용 저장소의 작업 폴더에
    놓이지만(`job_file_inline_max_bytes`) 만드는 동안 메모리에 다 들고 있으므로 이 상한은
    그대로 둔다 — 넘으면 조건으로 좁혀 나눠 내보내라고 말한다."""
    job_file_ttl_days: int = 7
    """작업 파일을 지우기까지. 임시물이 영구물이 되면 표가 DB 의 대부분이 된다."""
    job_ttl_days: int = 30
    """**끝난** 작업 기록을 지우기까지. 타이머가 소스마다 5분에 한 줄을 넣으므로 하루 288행이
    쌓인다 — 안 지우면 사람이 올린 작업이 그 사이에 파묻혀 「작업」 화면이 못 쓰게 된다.
    도는 것(`queued`·`running`)은 아무리 오래돼도 안 지운다."""
    job_notify_after_seconds: int = 20
    """이보다 오래 걸린 작업만 끝났을 때 알린다. 짧게 끝난 것은 사람이 그 화면에서 봤다 —
    본 것을 한 번 더 말하면 그 종은 잡음이 되고, 잡음이 된 종은 진짜 하나가 울려도 안
    읽힌다."""
    job_max_rows: int = 100_000
    """작업 경로의 행 상한. 요청 경로의 5,000(`bulk.MAX_ROWS`)은 요청 시간 때문이었다 —
    워커에서는 트랜잭션 하나가 감당하는 크기만 남는다."""
    worker_poll_seconds: float = 2.0
    worker_stale_seconds: int = 300
    """`running` 인데 이만큼 심장박동이 없으면 워커가 죽은 것으로 보고 되돌린다."""
    worker_max_attempts: int = 3

    view_scope: Literal["all", "department"] = "all"
    """**누가 무엇을 보나**(ADR 0034). `all` — 부서와 상관없이 가입자 누구나 모든 자료를
    본다(기본, 2026-10-22). `department` — 전역 + 내가 속한 부서 것만(그 전의 규칙). 고치는
    것은 어느 쪽이든 소유 부서의 관리자다. 이 값을 읽는 것은 `shared/permissions` 하나다 —
    목록 · 검색 · 그래프 · 지표 · 코어 API 가 모두 그것을 거친다."""

    tombstone_ttl_days: int = 90
    """**끊긴 선의 무덤**을 이만큼만 들고 있는다. 무덤은 영영 쌓이고, 내보내기는 그 이력을
    통째로 보낸다 — 몇 해가 지나면 「지금 무엇이 있나」 보다 「무엇이 없어졌나」 가 더 커진다.
    받는 쪽이 이 기간보다 오래 잠들어 있었다면 처음부터 다시 받아야 한다(그때는 무덤이
    아니라 전량이 정본이다)."""

    undo_journal_ttl_days: int = 30
    """**묶음 한 판을 되돌릴 기록**을 이만큼만 들고 있는다. 바뀐 줄마다 한 줄이라 백필 한
    번이 수만 줄을 남긴다 — 영영 두면 그 표가 데이터보다 커진다. 기간이 지난 판은 목록에
    남지만 「되돌릴 수 없음」 으로 뜬다: 지운 뒤에 되돌리기를 내밀면 눌러 본 뒤에야 안 되는
    것을 안다."""

    core_watermark_floor_seconds: int = 600
    """코어 API 가 주는 `as_of` 는 **아직 안 끝난 적재보다 앞서지 않는다**. 다만 열어 두고 잊은
    트랜잭션 하나가 그 시각을 영영 묶어 두면 받는 쪽은 새 값을 못 본다 — **이만큼 아무것도 안
    한** 트랜잭션은 셈에서 뺀다.

    ⚠️ 재는 것은 「얼마나 오래됐나」 가 **아니라** 「마지막으로 무엇이라도 한 때」 다.
       오래됐나로 자르면 한 시간 도는 백필이 빠지고, 그 사이에 받아 간 쪽은 그 백필분을
       영영 잃는다(그 구멍을 막은 것이 이 칸이다)."""

    metrics_max_cells: int = 10_000_000
    """지표 하나의 셀 상한 — 계획이 이보다 많이 어림하면 정의를 거절하고, **계산이 실제로
    이보다 많이 세면 그 계산을 실패로 둔다**(옛 값이 남는다, ADR 0013 · 0023). 셀은 기록보다
    훨씬 적어야 미리 세는 뜻이 있다. 자유 글자 기준 하나가 이것을 넘긴다. 셀 하나는 색인까지
    약 400바이트 — 1,000만이면 지표 하나에 4GB(다시 세는 동안 두 벌)."""
    metrics_night_hour: int = 2
    """하루 이상 주기의 지표를 세는 **밤 시간**(서버 현지 시각, 0 ~ 23). 타이머는 매시간 깨고,
    「매일 밤 · N일마다 밤」 지표는 이 시간에만 센다 — 무거운 계산이 낮에 돌지 않고, 백업
    (03:00)에 그날 값이 든다(`metrics/services.due`)."""
    metrics_full_every_days: int = 7
    """**전량으로 다시 세는 주기**(일). 그 사이 타이머 · 적재 뒤의 계산은 바뀐 기간만 센다
    (`metrics/incremental.py`). 증분이 못 보는 변경(감사 없이 바뀐 기록 · 타입 옮김 등)을 이
    주기로 바로잡는다. 0 이면 늘 전량."""
    metrics_incremental_max_share: float = 0.2
    """바뀐 원천 기록이 원천의 이 비율을 넘으면 전량으로 센다 — 그러면 전량이 오히려 싸다."""
    metrics_work_mem: str = "256MB"
    """지표 계산 트랜잭션의 `work_mem`. 200만 건을 묶을 때 기본값(4MB)이면 디스크로 넘어가
    열 배 느리다. 계산은 한 번에 하나만 돌므로(지표마다 자문 잠금) 넉넉히 준다."""
    metrics_max_read_cells: int = 20_000
    """화면 · MCP 의 읽기(표 · 추이 · 코호트) 한 번이 **돌려주는** 셀 상한 — 넘으면 `truncated`
    로 말한다. 응답 크기라 작게 둔다(셀 하나가 JSON 으로 약 400바이트)."""
    metrics_max_frame_cells: int = 200_000
    """분석 하나가 셀 위에서 계산하려고 **받는** 셀 상한 — 넘으면 거절한다(잘린 셀 위의 통계는
    틀린다, ADR 0014 · 0023). 셀 하나가 Python 에서 약 400바이트 — 20만이면 80MB · 1.4초
    (실측)."""

    log_dir: Path = BACKEND_DIR / "logs"
    """개발에서는 저장소 안. **운영에서는 `.env` 가 `/data/logs` 로 덮는다** —
    컨테이너 루트는 읽기 전용이라 이미지 안에는 못 쓴다."""
    log_retention_days: int = 30
    """파일 로그(`app.log`)를 며칠치 남기나. **요청 시간 기록(`request_timings`)도
    이만큼** 두고 워커가 지운다 — 요청마다 한 줄이라 가장 빨리 자라는 표다."""
    access_log_retention_days: int = 365
    """접근 로그(`access_logs` — 누가 어느 통로로 무엇을 불렀나)를 며칠치 남기나. 0 이면 지우지
    않는다. 지우는 주기가 없어 쌓이기만 했다(2026-10-10). **감사 기록(변경 이력)은 이것과
    상관없이 지우지 않는다** — 객체 이력과 되돌리기가 거기에 기댄다."""

    # ── 느린 요청 · 큰 응답(ADR 0026) ─────────────────────────────────────────────
    # 작업 목록 한 번이 93MB · 4.4초였던 것을 사람이 「화면이 느리다」 고 말하고 나서야 알았다
    # (2026-10-09). 최근 24시간에 이 문턱을 넘은 경로 틀이 있으면 시스템 관리자의 홈 「남은
    # 일」 에 뜬다. 파일 내려받기처럼 JSON 이 아닌 응답은 크고 받는 쪽 속도를 타는 것이
    # 정상이라 세지 않는다(화면에는 나온다).
    slow_request_p95_seconds: float = 3.0
    """경로 틀의 p95(스무 번 중 한 번만 이보다 느리다)가 이것을 넘으면 「느린 요청」."""
    slow_request_min_calls: int = 5
    """p95 를 따지려면 그 경로가 24시간에 이만큼은 불려야 한다. 한두 번 느린 것은 기동 직후의
    찬 캐시일 수 있다 — 그것으로 하루 내내 홈에 뜨면 그 줄은 잡음이 되고, 잡음이 된 줄은 진짜가
    떠도 안 읽힌다."""
    large_response_mb: float = 5.0
    """한 번이라도 이보다 큰 응답(MB)을 낸 경로 틀은 「큰 응답」. 화면은 그 응답을 통째로 받아
    풀어야 그린다 — 93MB 는 그것만으로 화면을 멈췄다."""
    read_query_seconds: float = 60.0
    """목록 · 통계 · 추이(그 내보내기와 0건 진단까지) 한 질의의 시간 상한(초). 넘으면 끊고 504
    「좁히거나 지표로」 — 이어진 것 너머를 걷는 기준 · 조건은 지나가는 줄 수만큼 들어, 기록
    2,000만 건을 지나가면 수 분이 걸린다(ADR 0031). 그동안 DB 연결과 API 작업자가 묶인다.
    0 이면 안 건다. **MCP 서버의 읽기 대기(90초)보다 짧게** — 길면 AI 는 이 안내 대신 끊긴
    연결을 받는다. 작업(내보내기 · 지표 계산)에는 안 걸린다."""
    read_work_mem: str = "64MB"
    """같은 읽기(목록 · 통계 · 추이)의 `work_mem` — 그 트랜잭션에서만(ADR 0031). 서버
    기본(4MB)이면 2,000만 건을 묶는 정렬 · 해시가 디스크로 넘어간다. 정렬 · 해시 하나마다,
    병렬 작업자마다 잡히므로(동시 요청이 많으면 그 곱 — 해시는 그 두 배까지) 지표
    계산(`METRICS_WORK_MEM`)보다 작게 둔다. 비우면 서버 설정을 따른다. 개발 · 운영이 같은
    값을 쓴다(`.env`)."""
    read_parallel_workers: int = 4
    """같은 읽기의 `max_parallel_workers_per_gather` — 기록 전부를 훑는 통계가 작업자 수만큼
    나뉜다. 서버의 `max_parallel_workers`(기본 8, `deploy/pg-tune.sh` 가 CPU 수로 맞춘다)
    안에서만 실제로 뜬다. -1 이면 서버 설정을 따른다."""
    read_temp_limit: str = "5GB"
    """같은 읽기의 `temp_file_limit` — 정렬 · 해시가 디스크로 넘어가는 임시 파일의 상한(ADR
    0048). 넘으면 그 질의만 끊고 「범위를 좁히라」 고 답한다. 없으면 질의 하나가 디스크를 다
    채워 DB 가 멈춘다. DB 프로세스(병렬 작업자 포함)마다 걸린다. superuser 만 바꾸는 설정이라
    배포가 앱 계정에 권한을 줄 때만 걸리고(`deploy.sh` — PostgreSQL 15 부터), 권한이 없으면
    서버 설정을 따른다. 비우면 안 건다."""
    long_read_seconds: float = 120.0
    """오래 도는 읽기(그래프의 그림 · 영향 범위) **요청 하나**의 시간 상한(초, ADR 0048). 질의
    하나의 상한(`read_query_seconds`)과 달리 질의 여러 번을 합친 시간이다. 넘으면 DB 질의를
    취소하고 504. 요청한 쪽이 떠나면(화면을 닫으면) 이것과 상관없이 바로 멈춘다. 0 이면 안
    건다."""
    whole_slots: int = 2
    """정적 그리기를 **설치 전체**(이중화의 두 서버 · 워커 모두)에서 동시에 몇 개까지
    돌리나(ADR 0048). 넘는 요청은 몇 초 기다렸다가 429 「앞의 것이 끝나면 다시」. 0 이면 안
    센다."""

    graph_map_night_hour: int = 4
    """지식 그래프 「전체 지도」 의 **매일 덧붙임**(새 기준정보만 빈자리에)을 워커가 넣는 밤
    시간 (서버 현지 시각, 0 ~ 23 — ADR 0046). 지표의 밤(`METRICS_NIGHT_HOUR`, 2시) ·
    백업(3시)과 겹치지 않게 4시. 시간마다의 집계는 이것과 상관없이 한 시간마다다."""
    graph_map_auto: bool = True
    """「전체 지도」 를 **워커가 스스로** 만들고 맞추나 — 처음 판 · 매일 덧붙임 · 시간마다
    집계(ADR 0049). false 면 타이머가 지도 작업을 하나도 넣지 않는다. 시스템 관리자의 「전체
    다시 배치」 는 그대로 돈다. 큰 설치에서 지도 계산이 서버를 버겁게 하면 끈다."""
    graph_map_max_records: int = 50_000_000
    """지도에 올릴 기록의 상한 — 넘으면 판을 계산하지 않고 바로 거절한다(작업 실패 · 화면이
    까닭을 보인다, ADR 0049). 확대 점 표가 기록 한 건에 한 줄이라(2,000만 건에 3GB) 그 위로는
    디스크 · 시간이 함께 커진다. 0 이면 안 본다."""
    graph_map_temp_limit: str = "20GB"
    """지도 작업의 임시 파일 상한(`temp_file_limit`, ADR 0049) — 넘으면 그 작업만 실패한다.
    `READ_TEMP_LIMIT` 처럼 배포가 앱 계정에 권한을 줄 때만 걸린다. 비우면 안 건다."""

    relay_pipelines_dir: Path = REPO_DIR / "devtools"
    """파이프라인 정의(`relay-pipeline/1`)가 있는 곳 — 아래를 전부 훑는다(`*.example.json` 은
    뺀다). **운영은 저장소 밖**(예: `/etc/datarelay/pipelines`)이다 — 정의 · 레시피는 열 이름과
    코드 체계가 사내 정보다(ADR 0001 §5). 개발 기본값은 샘플 PLM 의 정의가 있는 `devtools/`."""
    relay_work_dir: Path = REPO_DIR / ".work" / "relay"
    """파이프라인마다의 작업 폴더 — 미러 · 워터마크 · 지난번 적용된 출력 · 대기
    계획(`relay_core.state`)."""
    relay_allow_sql: bool = False
    """DB 를 바로 읽는 파이프라인(`kind: "sql"`)을 돌려도 되나. **기본은 꺼짐** — 원천 DB 를
    바로 읽는 일은 사내 보안 승인이 필요하다. 켜도 정의는 읽기 전용(SELECT)만 받는다."""
    relay_secrets_file: Path | None = None
    """정의의 `token_env` 를 풀 곳 — 프로세스 환경 변수가 먼저, 없으면 이 파일(dotenv 모양),
    그다음 `backend/.env`. 비밀은 정의에 적지 않는다."""
    relay_file_roots: str = ""
    """파일 어댑터(`kind: "file"`)가 읽어도 되는 로컬 폴더 — 쉼표로 여럿. 그 밖의 파일은 정의가
    가리켜도 읽지 않는다(AI 가 탐색으로 서버의 아무 파일이나 열지 못하게). 비우면 운영은
    `<RELAY_PIPELINES_DIR 의 부모>/drop`(배포: `/data/relay/drop`), 그 밖은 `devtools/`."""
    relay_trial_rows: int = 5000
    """정의 시험(`pipeline_test`)이 원천에서 받아 볼 줄 수의 상한 — 큰 원천(MES)을 끝까지 받지
    않게. 넘으면 앞 그만큼만 받고 「켜면 보낼 것」 은 세지 않는다(덜 받은 채 견주면 나머지가
    다 지움으로 보인다). 0 이면 끝까지."""
    relay_source_hosts: str = ""
    """AI(MCP)의 **탐색 · 정의 초안 시험**이 부를 수 있는 주소(`host` 또는 `host:port`, 쉼표로
    여럿) — 원천과 대상 둘 다. **켜진 정의가 쓰는 주소는 적지 않아도 된다**(사람이 켠 것).
    비우면 운영은 켜진 정의의 주소만, 그 밖은 `127.0.0.1` · `localhost` 도. 비밀(`token_env`)이
    엉뚱한 곳으로 나가지 않게 하는 울타리다."""

    @property
    def relay_file_root_paths(self) -> tuple[Path, ...]:
        named = [one.strip() for one in self.relay_file_roots.split(",") if one.strip()]
        if named:
            return tuple(Path(one).resolve() for one in named)
        if self.app_env == "production":
            return ((self.relay_pipelines_dir.parent / "drop").resolve(),)
        return ((REPO_DIR / "devtools").resolve(),)

    @property
    def relay_source_host_list(self) -> tuple[str, ...]:
        named = [
            one.strip().lower() for one in self.relay_source_hosts.split(",") if one.strip()
        ]
        if not named and self.app_env != "production":
            named = ["127.0.0.1", "localhost"]
        return tuple(named)

    filestore_gc_hours: int = 24
    """첨부 저장소의 **고아 파일 정리**를 워커가 몇 시간마다 하나(`files/gc.py`). 0 이면 안
    한다 — 시스템 관리자가 작업(「고아 첨부 파일 정리」)으로만."""

    filestore_dir: Path = BACKEND_DIR / "filestore"
    """첨부가 사는 곳. DB 에는 경로와 해시만 둔다.

    **운영에서는 반드시 bind-mount 된 경로여야 한다**(`/data/filestore`). SIF 루트는
    읽기 전용이라, 여기가 이미지 안을 가리키면 **첫 업로드에서** `[Errno 30]
    Read-only file system` 이 난다 — 그리고 그 오류는 파일을 올리는 사람에게만
    보인다. ReportArchive 가 임베드 번들 경로에서 정확히 그것을 겪었다."""

    datasource_dir: Path | None = None
    """데이터 소스가 **파일**(CSV·Excel·JSON)을 읽어도 되는 폴더. 비우면 URL 로만 읽는다.

    아무 경로나 읽게 두면 데이터 소스 화면이 서버의 모든 파일을 읽는 문이 된다 — 그래서
    이 폴더 아래만. 운영에서는 bind-mount 된 곳(예: `/data/filestore/incoming`)을 적고,
    바깥 시스템이 거기로 파일을 떨어뜨리게 한다."""

    backup_dir: Path | None = None
    """백업 스크립트(`deploy/backup.sh`)가 덤프를 남기는 폴더.

    **서버가 자기 백업을 볼 수 있어야 한다.** 백업은 작업 스케줄러가 돌리는데,
    아무도 안 보면 조용히 죽고 그 사실은 복구가 필요한 날에야 드러난다 — 그날은
    이미 늦다. 여기를 적어 두면 홈의 「남은 일」 이 오래된 백업을 말한다."""

    frontend_dist: Path = REPO_DIR / "frontend" / "dist"
    """존재하면 API 와 같은 프로세스가 SPA 를 서빙한다. 개발 중에는 없다."""

    """**정제 도구 키트** — 사용자 PC 에 푸는 zip(`sp-pipeline`). 있으면 화면이 내려준다.

    서버 번들이 같은 판의 키트를 이미지 안 이 자리에 넣는다(`build_bundle.sh` ·
    `apptainer.def`). 따로 받게 두면 사내망에서는 GitHub 에 못 닿아 받을 길이 없고, 운영의
    사람도 AI 도 그런 것이 있는 줄 모른다(실측). 개발 중에는 대개 없다 — 화면이 「이 설치에는
    없다」 로 말한다."""

    jwt_secret: str = "dev-only-insecure-secret-change-me"
    """운영에서는 설치 스크립트가 난수로 만들어 .env 에 넣는다.

    기본값이 운영에 새어 나가면 아무나 토큰을 위조할 수 있으므로,
    app_env=production 이면서 이 값이 그대로면 **기동을 거부한다**(main.py)."""

    access_token_minutes: int = 720  # 12시간
    refresh_token_days: int = 30
    refresh_cookie_name: str = ""
    """비우면 `<app_slug>_refresh`. **이름도 slug 에서 나온다.** 쿠키는 포트를 구분하지 않아서,
    같은 서버에 두 플랫폼을 띄웠을 때 이름이 같으면 한쪽 로그인이 다른 쪽 세션을
    덮어쓴다 — 번갈아 로그아웃되는 상태가 되고, 그 원인은 코드 어디에도 없다."""
    refresh_cookie_secure: bool = False
    """False 면 **요청이 https 일 때만** Secure 를 붙인다(프록시 뒤는 TRUST_PROXY 가 판정).
    True 는 강제 — http 로 접속하면 브라우저가 쿠키를 버려 로그인이 유지되지 않으므로, 메인
    서버 없이 IP 로 확인하는 동안은 켜지 않는다."""

    # ── HWAX 포털 로그인(jwt-handoff, ADR 0028) ───────────────────────────────────
    # 포털이 RS256 launch 토큰(90초)을 만들어 브라우저가 `POST <접두어>/api/auth/
    # portal-callback` 으로 보낸다. 포털에 붙는 설치는 `PUBLIC_PATH` 를 포털의 id 와 같게
    # (`/<id>`) 둔다 — 포털 nginx 가 `/<id>/` 를 벗겨 넘기고, 로그인 뒤 돌아갈 곳 · 쿠키
    # path 가 그 접두어 아래다.
    portal_jwks_url: str = ""
    """포털의 공개 키 주소(`…/.well-known/jwks.json`) — **비우면 포털 로그인이 꺼진다**(콜백이
    404 JSON 으로 답한다 — 포털 update-all 의 진단표가 그 숫자로 「앱 SSO 꺼짐」 을 가른다).
    다른 서버의 포털이면 포털 nginx 를 거친 주소(사내망
    `http://<포털>:8088/.well-known/jwks.json` 이 간단 — https 는 사내 CA 를 이 서버가 믿어야
    한다). 포털 담당에게서 받는다."""
    portal_audience: str = ""
    """launch 토큰의 `aud` — 포털 타일의 `audience`. 비우면 `app_slug`. 포털의 id 를 slug 와
    다르게 지었을 때만 적는다(다르면 서명이 맞아도 「다른 앱에 낸 토큰」 으로 거절한다)."""
    portal_issuer: str = ""
    """launch 토큰의 `iss` — 포털의 `JWT_ISSUER`. 비우면 포털의
    기본값(`DEFAULT_PORTAL_ISSUER`). 빈 값을 기본으로 읽는 까닭: `PORTAL_ISSUER=` 한 줄이 「iss
    가 빈 토큰만 받는다」 가 되어 모든 포털 로그인이 「확인 실패」 로 튕긴다."""
    portal_launch_url: str = ""
    """로그인 화면의 「HWAX 포털로 로그인」 이 보낼 주소 — 포털의 `/launch/<id>`
    (`https://hwax.sec.samsung.net/launch/<id>`). 비우면 그 단추가 안 뜬다. 포털이 그 사람으로
    토큰을 만들어 위 콜백에 보내 준다 — 앱에 직접(`http://<서버>:8080/`) 들어온 사람도
    같은 길."""

    # ── HWAX 게이트웨이의 사람별 위임(`POST /api/auth/sso`, ADR 0028) ───────────────
    # 포털의 MCP 게이트웨이가 사람마다 그 사람 명의의 개인 토큰을 받아 우리 MCP 를 부른다
    # (Report Archive 와 같은 계약). 게이트웨이는 **앱 포트로 직접** 부른다(포털 nginx 를 안
    # 거친다).
    heax_sso_secret: str = ""
    """게이트웨이와 이 설치만 아는 공유 비밀(`X-Heax-Gateway-Secret`) — **비우면 위임이
    꺼진다** (`/api/auth/sso` 가 404 JSON). 인스턴스마다 따로 둔다(다른 서비스와 묶지 않는다) —
    `sudo ./deploy.sh hwax-request` 가 없으면 만들어 적는다(`openssl rand -hex 32` 꼴). 포털
    담당에게는 요청서와 **따로** 전한다(게이트웨이 `provision.env` 의 `<접두>_SSO_SECRET`)."""
    heax_sso_allowed_ips: str = ""
    """위임 발급을 받아 줄 원격 주소(쉼표 · CIDR — `10.1.2.3,10.1.2.0/24`). 비우면 비밀만 본다.
    **TCP 의 원격 주소로 판정한다** — 게이트웨이는 직접 부르므로 프록시
    머리(`X-Forwarded-For`)를 믿을 까닭이 없고, `TRUST_PROXY` 를 켠 설치는 서버(uvicorn)가 그
    머리로 원격 주소를 바꿔 적는다. 그래서 목록이 있으면 그 머리가 붙은 요청은 받지 않는다. 못
    읽는 값이 하나라도 있으면 **전부 거절한다**(오타 하나로 제한이 풀리지 않게) — 서버 화면의
    「HWAX 포털 로그인」 이 그 값을 말한다."""

    @model_validator(mode="after")
    def _derive_from_slug(self) -> Settings:
        """slug 를 검사하고, 비워 둔 것들을 slug 에서 채운다."""
        if not re.fullmatch(r"[a-z][a-z0-9]{0,31}", self.app_slug):
            raise ValueError(
                f"APP_SLUG 는 소문자·숫자 한 덩어리 32자 이내여야 합니다: {self.app_slug!r}"
            )
        if not self.app_tagline and self.app_name == DEFAULT_APP_NAME:
            self.app_tagline = DEFAULT_APP_TAGLINE
        if not self.database_url:
            self.database_url = (
                f"postgresql+psycopg://postgres:postgres@localhost:5432/{self.app_slug}"
            )
        if not self.refresh_cookie_name:
            self.refresh_cookie_name = f"{self.app_slug}_refresh"
        if not self.portal_audience.strip():
            self.portal_audience = self.app_slug
        if not self.portal_issuer.strip():
            self.portal_issuer = DEFAULT_PORTAL_ISSUER
        return self

    @property
    def extension_names(self) -> tuple[str, ...]:
        """`EXTENSIONS=hub, bom` → `("hub", "bom")`. 순서는 적은 대로, 빈 것과 중복은 뺀다."""
        seen: list[str] = []
        for name in self.extensions.split(","):
            cleaned = name.strip()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return tuple(seen)

    @property
    def base_path(self) -> str:
        """정리된 접두어 — `""` 또는 `/<slug>`."""
        cleaned = self.public_path.strip().strip("/")
        return f"/{cleaned}" if cleaned else ""

    login_delay_after: int = 5
    """같은 계정의 로그인 실패가 이 횟수부터 응답을 늦춘다. **잠그지 않는다** —
    관리자 복구가 서버 콘솔뿐인 시스템에서 잠금은 자해다."""
    login_delay_step_seconds: int = 2
    login_delay_max_seconds: int = 30
    login_failure_window_minutes: int = 15
    """이 시간 안의 실패만 센다. 지나면 처음부터."""

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5250", "http://127.0.0.1:5250"]
    )
    """개발 서버(Vite)용. 배포에서는 동일 출처라 필요 없다."""


class RuntimeSettingProvider(Protocol):
    """DB 기반 런타임 설정의 자리. runtime_settings 표가 생기면 여기에 끼운다."""

    def get(self, key: str) -> str | None: ...


class _NullProvider:
    def get(self, key: str) -> str | None:
        return None


_provider: RuntimeSettingProvider = _NullProvider()


def set_runtime_provider(provider: RuntimeSettingProvider) -> None:
    global _provider
    _provider = provider


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_setting(key: str) -> str | None:
    """3단 fallback 으로 값 하나를 읽는다. DB 행이 있으면 환경변수를 이긴다."""
    from_db = _provider.get(key)
    if from_db is not None:
        return from_db
    value = getattr(get_settings(), key, None)
    return None if value is None else str(value)
