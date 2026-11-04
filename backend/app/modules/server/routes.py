"""서버·시스템 관리 라우터.

**한 화면이 답해야 하는 물음이 셋이다**: 지금 뭐가 깔렸나, DB 는 맞춰져 있나,
무엇이 얼마나 쌓였나. 셋을 따로 두면 아무도 다 보지 않는다. 그리고 문제가 났을 때
첫 물음은 언제나 "지금 서버 버전이 뭐냐" 와 "어느 DB 를 보고 있냐" 다.

**세는 것과 남은 일은 이 모듈이 모른다.** 도메인이 `shared/extensions.py` 에
등록하고 여기는 그것을 모아 낸다 — 공통 틀이 도메인 표를 import 하면 방향이
거꾸로 서고, 그때 import 순서 하나로 서버가 안 뜬다.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app import schema_version, version
from app.config import get_settings
from app.database import engine, get_db
from app.modules.accounts.models import User
from app.modules.server import performance, services
from app.modules.server.schemas import (
    BackupOut,
    CutQuestionOut,
    CutQuestionsOut,
    DiskOut,
    ExtensionApiOut,
    ExtensionOut,
    ExtensionPatchIn,
    JobKindStatOut,
    JobStatsOut,
    MaintenanceItemOut,
    PerformanceThresholdsOut,
    PlatformProfileIn,
    PlatformProfileLiveOut,
    PlatformProfileOut,
    ProfileFactOut,
    RequestCaseOut,
    RequestStatOut,
    RequestStatsOut,
    ServerStatusOut,
    TableCountOut,
)
from app.shared import extensions, ops
from app.shared.auth import current_user, require_system_admin
from app.shared.pagination import MAX_LIMIT, clamp_limit

router = APIRouter(prefix="/server", tags=["server"])

#: 프로세스가 언제 떴나. 재시작을 눈으로 확인할 수 있는 유일한 값이다.
STARTED_AT = datetime.now(UTC)


def _safe_url(url: str) -> str:
    """비밀번호를 지운 접속 문자열. **화면에 그대로 뜨는 값이다.**"""
    if "@" not in url:
        return url
    head, tail = url.rsplit("@", 1)
    if ":" in head:
        scheme_user = head.rsplit(":", 1)[0]
        return f"{scheme_user}:***@{tail}"
    return f"{head}@{tail}"


@router.get("/status", response_model=ServerStatusOut)
def status(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> ServerStatusOut:
    settings = get_settings()
    head = schema_version.code_head()
    current = schema_version.db_revision(engine)

    disk: DiskOut | None = None
    try:
        # **filestore 폴더를 그대로 잰다 — 부모가 아니라.**
        #
        # 부모를 재면 컨테이너에서 틀린 값이 나온다: `/data` 는 이미지 안의
        # tmpfs 고 `/data/filestore` 만 호스트에 bind-mount 된다. 실측으로
        # 「전체 64MB, 여유 64MB」 가 나왔다 — 관리자가 보는 화면에서 그것은
        # 곧 터질 디스크로 읽히고, 정작 진짜 볼륨은 아무도 안 보게 된다.
        #
        # 이 폴더는 기동 가드(`_guard_writable_paths`)가 만들어 두므로 있다.
        usage = shutil.disk_usage(settings.filestore_dir)
        disk = DiskOut(
            path=str(settings.filestore_dir),
            total_bytes=usage.total,
            free_bytes=usage.free,
            used_percent=round((usage.total - usage.free) / usage.total * 100, 1),
        )
    except OSError:
        # 경로가 없거나 권한이 없다. **화면은 떠야 한다** — 디스크를 못 읽는다고
        # 서버 상태 전체를 못 보면 정작 원인을 볼 데가 없어진다.
        disk = None

    return ServerStatusOut(
        app_name=settings.app_name,
        app_slug=settings.app_slug,
        extensions=list(services.enabled_names(db)),
        extensions_unknown=list(services.unknown_defaults()),
        version=version.current(),
        app_env=settings.app_env,
        database_url_safe=_safe_url(settings.database_url),
        schema_head=head,
        schema_current=current,
        schema_behind=bool(head and current and head != current),
        disk=disk,
        backup=BackupOut(**vars(ops.backup_status())),
        counts=[
            TableCountOut(label=item.label, count=item.count)
            for item in extensions.stat_items(db)
        ],
        started_at=STARTED_AT,
    )


@router.get("/enabled-extensions", response_model=list[str])
def enabled_extensions(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[str]:
    """지금 켜진 확장 이름 — **화면이 메뉴를 그릴 때 묻는 자리.**

    `index.html` 의 메타는 페이지를 받은 순간의 사진이라, 관리자가 켜고 끈 뒤에도 화면은
    새로 고침 전까지 옛 메뉴를 들고 있다. 개발 서버(Vite)는 `backend/.env` 를 심으므로
    아예 안 맞는다 — **실측으로 「껐는데 메뉴가 그대로」 가 나왔다.** 그래서 목록은 여기서
    받고, 메타는 첫 그림의 씨앗으로만 쓴다.

    시스템 관리자만이 아니라 **누구나** 읽는다. 메뉴는 모든 사람이 그린다.
    """
    return list(services.enabled_names(db))


@router.get("/extension-api", response_model=list[ExtensionApiOut])
def extension_api(
    request: Request,
    _: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ExtensionApiOut]:
    """켠 확장의 **부를 수 있는 자리 목록** — 이름 · 경로 · 한 줄 설명 · 받는 칸.

    확장마다 도구를 만들지 않기 위해 있다(MCP 의 `extensions_schema` 가 이것을 읽는다).
    도구 목록이 길어질수록 그것을 읽는 쪽은 엉뚱한 것을 고르므로, **부를 수 있는 것이
    무엇인지는 목록 하나가 말하고** 부르는 일은 한 도구가 한다.

    **꺼진 확장은 안 나온다.** 번들에는 다 들어 있고 문이 404 로 답하는데, 목록에 보이면
    그것을 부르는 쪽이 「있는데 안 된다」 로 읽는다.

    누구나 읽는다 — 목록을 아는 것이 권한을 넓히지 않는다. 그 자리를 부를 수 있는지는
    부를 때 서버가 판정한다.
    """
    return [
        ExtensionApiOut(**one)
        for one in services.extension_api(request.app.openapi(), services.enabled_names(db))
    ]


@router.get("/extensions", response_model=list[ExtensionOut])
def extension_list(
    _: User = Depends(require_system_admin), db: Session = Depends(get_db)
) -> list[ExtensionOut]:
    """이 번들에 든 확장과 그 켜짐. **고를 수 있는 것은 여기 있는 것뿐이다.**"""
    return [
        ExtensionOut(
            name=name,
            description=services.description(name),
            enabled=on,
            pinned=pinned,
            updated_at=at,
        )
        for name, on, pinned, at in services.states(db)
    ]


@router.patch("/extensions/{name}", response_model=ExtensionOut)
def extension_toggle(
    name: str,
    payload: ExtensionPatchIn,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> ExtensionOut:
    """확장을 켜거나 끈다 — **시스템 관리자만.**

    끄면 그 확장의 API 는 404 가 되고 메뉴 · 경로가 사라진다. **자료는 남는다** —
    표는 확장과 무관하게 이미 있고(마이그레이션은 전부 돈다), 다시 켜면 그대로다.

    화면은 `/server/enabled-extensions` 를 다시 받아 **새로 고침 없이** 따라온다.
    """
    services.set_enabled(db, user, name, payload.enabled)
    row = next(one for one in services.states(db) if one[0] == name)
    return ExtensionOut(
        name=row[0],
        description=services.description(row[0]),
        enabled=row[1],
        pinned=row[2],
        updated_at=row[3],
    )


@router.get("/maintenance", response_model=list[MaintenanceItemOut])
def maintenance(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[MaintenanceItemOut]:
    """남은 일. **홈이 이것을 보여 준다.**

    관리 화면에 들어가야만 보이는 목록은 아무도 안 본다 — 승인 대기가 며칠씩
    방치되고, 안 채워진 자료는 영영 안 채워진다.

    **0 건인 항목은 안 내보낸다**(레지스트리가 거른다). 다 0 인 목록을 매일 보면
    사람은 그 자리를 아예 안 읽게 되고, 그때 진짜 하나가 떠도 눈에 안 들어온다.
    """
    return [
        MaintenanceItemOut(
            key=item.key,
            label=item.label,
            count=item.count,
            link=item.link,
            severity=item.severity,
        )
        for item in extensions.maintenance_items(db, user)
    ]


def _profile_out(db: Session) -> PlatformProfileOut:
    settings = get_settings()
    row = services.profile(db)
    return PlatformProfileOut(
        slug=settings.app_slug,
        name=settings.app_name,
        tagline=settings.app_tagline,
        summary=row.summary if row else "",
        notes=row.notes if row else "",
        updated_at=row.updated_at if row else None,
    )


@router.get("/profile", response_model=PlatformProfileOut)
def platform_profile(db: Session = Depends(get_db)) -> PlatformProfileOut:
    """**이 플랫폼의 자기소개** — 무엇을 담고, 다른 플랫폼과 어떤 사이인가. **로그인 없이
    읽힌다.**

    같은 틀로 띄운 플랫폼 여럿이 한 에이전트에 도구로 붙으면 도구 이름 · 설명이 전부 같다. MCP
    서버가 이것을 읽어 안내문 첫머리에 싣는다 — 안내문은 에이전트가 토큰을 싣기 전에 서야 해서
    로그인을 요구할 수 없다. 그래서 **비밀을 적는 자리가 아니다.**
    """
    return _profile_out(db)


@router.get("/profile/live", response_model=PlatformProfileLiveOut)
def platform_profile_live(
    _: User = Depends(current_user), db: Session = Depends(get_db)
) -> PlatformProfileLiveOut:
    """자기소개 + **지금 담긴 것**(읽을 때 센다 — 기록 · 축과 건수, 들어오는 곳, 정본이 바깥인
    타입, 지표, 확장) + **낡음**(사람이 쓴 뒤 생기고 없어진 것). 로그인한 사람(토큰)에게 —
    MCP 가 접속 때 그 사람의 토큰으로 읽어 안내문에 싣는다."""
    facts = services.live_facts(db)
    return PlatformProfileLiveOut(
        **_profile_out(db).model_dump(),
        facts=[ProfileFactOut(key=one.key, label=one.label, lines=one.lines) for one in facts],
        stale=services.stale_reasons(services.profile(db), facts),
    )


@router.put("/profile", response_model=PlatformProfileOut)
def platform_profile_update(
    payload: PlatformProfileIn,
    user: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> PlatformProfileOut:
    """자기소개를 고친다 — **시스템 관리자만.** 토큰으로는 `ontology:write` 범위가 든다(정의를
    바꾸는 일과 같은 무게 — 에이전트가 이 글로 플랫폼을 고른다)."""
    services.set_profile(db, user, summary=payload.summary, notes=payload.notes)
    return _profile_out(db)


# --- 정제 도구 키트 ---------------------------------------------------------------
#
# 사용자 PC 에 푸는 zip(`sp-pipeline`) — 원천 파일을 정제해 묶음으로 만들고 수만 줄을 한 번에
# 넣는 길이다. **서버가 같은 판을 들고 있다가 내려준다.** 따로 받게 두면 사내망에서는 GitHub 에
# 못 닿아 받을 길이 없고, 운영의 사람도 AI 도 그런 것이 있는 줄 몰랐다(실측).
#
# 로그인한 사람이면 누구나 — 비밀이 없다(릴리스에 공개로 올라가는 것과 같은 파일이다). 데이터를
# 정제하는 사람은 대개 관리자가 아니다.


@router.get("/performance/requests", response_model=RequestStatsOut)
def performance_requests(
    period: performance.Period = "24h",
    sort: performance.RequestSort = "p95",
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> RequestStatsOut:
    """경로 틀 · 메서드마다 호출 수 · p50 · p95 · 최대 시간 · 평균/최대 응답 크기.

    `sort` — `p95`(느린 순) · `max_ms` · `max_bytes`(큰 순) · `avg_bytes` · `calls`. 기록이
    너무 많으면 최근 쪽만 세고 `capped` 로 말한다(`performance.MAX_ROWS`).
    """
    window = performance.window(db, period)
    items, total = performance.route_stats(
        db, window.since, sort=sort, limit=clamp_limit(limit), offset=offset
    )
    limits = performance.thresholds()
    return RequestStatsOut(
        period=period,
        since=window.since,
        capped=window.capped,
        first_recorded_at=performance.first_recorded_at(db),
        thresholds=PerformanceThresholdsOut(
            p95_ms=limits.p95_ms,
            min_calls=limits.min_calls,
            response_bytes=limits.response_bytes,
        ),
        items=[RequestStatOut(**vars(one)) for one in items],
        total=total,
        limit=clamp_limit(limit),
        offset=offset,
    )


@router.get("/performance/requests/cases", response_model=list[RequestCaseOut])
def performance_cases(
    route: str = Query(max_length=300),
    method: str = Query(max_length=10),
    period: performance.Period = "24h",
    order: performance.CaseOrder = "duration",
    limit: int = Query(default=20, ge=1, le=50),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> list[RequestCaseOut]:
    """그 경로 틀의 **최근 최악 사례** — 오래 걸린 순(`order=bytes` 면 큰 순).

    요청 ID 로 app.log 에서 그 요청의 줄을 찾고, 원 경로로 어느 객체였나를 본다.
    """
    window = performance.window(db, period)
    return [
        RequestCaseOut(**vars(one))
        for one in performance.cases(
            db, route=route, method=method, since=window.since, order=order, limit=limit
        )
    ]


@router.get("/performance/cut", response_model=CutQuestionsOut)
def performance_cut(
    period: performance.Period = "24h",
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> CutQuestionsOut:
    """**끊긴 질문**(ADR 0031) — 시간 상한(504)에 걸린 목록 · 통계와 SPARQL 의 거절(그래프가
    큼 · 빠진 기록 타입)을 같은 모양끼리 묶어 많이 끊긴 것부터. 무엇을 지표로 미리 세어
    둘지, 트리플 스토어가 정말 필요한지를 실제 물음으로 가른다."""
    since = datetime.now(UTC) - performance.PERIODS[period]
    items, total = performance.cut_stats(db, since, limit=limit)
    return CutQuestionsOut(
        period=period,
        since=since,
        items=[CutQuestionOut(**vars(one), suggestion=None) for one in items],
        total=total,
    )


@router.get("/performance/jobs", response_model=JobStatsOut)
def performance_jobs(
    period: performance.Period = "24h",
    _: User = Depends(require_system_admin),
    db: Session = Depends(get_db),
) -> JobStatsOut:
    """작업 종류마다 — 그 기간에 들어온 작업의 끝난 수 · 소요 · 대기(초). 느린 것이 위로."""
    since = datetime.now(UTC) - performance.PERIODS[period]
    return JobStatsOut(
        period=period,
        since=since,
        items=[JobKindStatOut(**vars(one)) for one in performance.job_stats(db, since)],
    )
