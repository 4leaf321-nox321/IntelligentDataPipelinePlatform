"""FastAPI 앱 — API 와 SPA 를 한 프로세스가 서빙한다.

배포 산출물이 하나면 롤백도 하나다. 프론트를 따로 띄우는 구성은 개발에서는 편하지만
출하 형태가 성립하지 않는다 — "어느 쪽이 옛 버전인가" 를 물을 자리가 생긴다.

**조립은 여기 하나다.** 라우터도, 확장 지점 등록도, PAT 범위도 전부 이 파일을
거친다. 모듈이 서로를 import 하지 않게 하려면 조립 지점이 하나여야 하고, 그래야
"이게 왜 안 뜨지" 를 물을 자리가 생긴다.
"""

from __future__ import annotations

import logging
import re
from html import escape as html_escape

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.types import ASGIApp, Receive, Scope, Send

from app import extensions as ext_loader
from app import version
from app.config import Settings, get_settings
from app.database import SessionLocal, engine, get_db
from app.logging_setup import setup_logging
from app.modules.accounts import routes as accounts_routes
from app.modules.accounts import services as accounts_services
from app.modules.audit import routes as audit_routes
from app.modules.auth import routes as auth_routes
from app.modules.files import routes as files_routes
from app.modules.files import services as files_services
from app.modules.jobs import routes as jobs_routes
from app.modules.jobs import services as jobs_services
from app.modules.notices import routes as notices_routes
from app.modules.notifications import routes as notifications_routes
from app.modules.server import performance as server_performance
from app.modules.server import routes as server_routes
from app.modules.server import services as server_services
from app.modules.workspaces import routes as workspaces_routes
from app.modules.workspaces import services as workspaces_services
from app.schema_version import warn_if_behind
from app.shared import extensions, ops, scopes, system_sources
from app.shared.access_log import AccessLogMiddleware
from app.shared.errors import NotFound, code, register_error_handlers
from app.shared.request_context import RequestIdMiddleware

logger = logging.getLogger(__name__)

API_PREFIX = "/api"


def _api_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix=API_PREFIX)

    @router.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        # **버전을 함께 준다.** 원격에서 "지금 서버에 뭐가 깔렸나" 를 물을 수 있는
        # 유일한 자리다. 배포 뒤 확인도, 나중의 점검 스크립트도 여기를 본다.
        return {
            "status": "ok",
            "version": version.current(),
            "app": settings.app_name,
            "slug": settings.app_slug,
        }

    # 모듈 라우터는 **여기서만** 모은다.
    router.include_router(auth_routes.router)
    router.include_router(accounts_routes.router)
    router.include_router(workspaces_routes.router)
    router.include_router(notices_routes.router)
    router.include_router(notifications_routes.router)
    router.include_router(files_routes.router)
    router.include_router(audit_routes.router)
    # 오래 걸리는 일(파이프라인 실행)은 요청이 아니라 작업이다 — 워커가 돈다.
    router.include_router(jobs_routes.router)
    router.include_router(server_routes.router)

    # --- 확장의 라우터 ---------------------------------------------------
    # 코어는 확장을 import 하지 않는다. 이름으로 찾아 붙이는 자리가 여기 하나다.
    #
    # **번들에 든 확장은 전부 붙이고, 켜짐은 문이 판단한다.** 켜고 끄는 일이 화면으로
    # 옮겨 왔으므로(시스템 관리자 › 서버) 기동에서 라우터를 가릴 수 없다 — 운영은 워커가
    # 넷이라, 한 프로세스가 기동 때 본 값을 들고 있으면 넷이 서로 다른 답을 한다.
    # 꺼진 확장은 문이 404 로 답한다(`require_extension`).
    names = ext_loader.available()
    server_services.register_available(names)
    for name in names:
        gated = APIRouter(dependencies=[Depends(server_services.require_extension(name))])
        module = ext_loader.load(name)
        # 여기서 연 범위는 그 확장의 것 — 끄면 토큰 발급 목록에서 빠진다(`shared/scopes.py`).
        with scopes.owned_by(name):
            module.register(gated)
        # 하는 일 한 줄 — 자기소개 · 서버 화면이 이름 곁에 싣는다(코어는 확장을 모르므로
        # 여기서 넘긴다).
        server_services.register_description(name, getattr(module, "DESCRIPTION", ""))
        router.include_router(gated)

    # --- 여기에 도메인 라우터를 더한다 -----------------------------------
    #
    #   from app.modules.<name> import routes as <name>_routes
    #   router.include_router(<name>_routes.router)

    return router


def _register_extensions() -> None:
    """공통 화면에 무엇이 뜨는지 — **등록하지 않으면 안 뜬다.**

    홈의 「남은 일」, 서버 화면의 「쌓인 것」, 부서 삭제 확인의 참조 목록이
    여기서 정해진다. 도메인이 자기 표를 안 걸면 **부서를 지울 때 그 표는 아무 데도
    안 나타나고**, 사람은 아무것도 안 걸린 줄 안다.
    """
    extensions.register_maintenance(accounts_services.maintenance)
    extensions.register_stats(accounts_services.stats)
    extensions.register_stats(files_services.stats)
    # **백업이 오래되면 홈이 말한다.** 스케줄러가 죽어도 아무도 모르는 것이
    # 백업의 기본 실패 방식이고, 그 사실은 복구가 필요한 날에야 드러난다.
    extensions.register_maintenance(ops.maintenance)
    # **부서를 지울 때 첨부가 목록에 뜬다.** 안 걸면 사람은 아무것도 안 걸린 줄
    # 알고 지우려 하는데, FK 가 RESTRICT 라 서버가 500 을 낸다.
    extensions.register_workspace_reference(files_services.workspace_reference)
    # **부서를 통폐합할 때 자료를 넘기는 길.** 안 걸면 그 표는 옮기기 화면에 안 뜨고,
    # 사람은 없어지는 부서를 비울 방법이 없어 삭제도 보관도 못 한다.
    extensions.register_workspace_content(files_services.workspace_content)

    # 부서 · 계정의 투영 — SP 에서는 온톨로지 `system` 타입이 비추는 원 표였다. 온톨로지는
    # 가져오지 않았지만 등록부는 공통이라 그대로 둔다(SP 에서 고친 것을 옮겨 오기 쉽게).
    system_sources.register_system_source(workspaces_services.SYSTEM_SOURCE)
    system_sources.register_system_source(accounts_services.SYSTEM_SOURCE)

    # **조용히 멎는 것들을 홈이 말한다.** 정기 작업은 잘 도는 동안 아무도 그 화면을 안
    # 연다 — 백업을 같은 이유로 여기 걸어 두었다(ops.maintenance).
    # 끝났는데 아무도 안 누른 계획 · 실패한 내 작업 · 멎은 워커. **워커가 죽으면 파이프라인이
    # 멎는다** — 그 사실이 「작업」 화면 안에만 있으면 아무도 제때 모른다.
    extensions.register_maintenance(jobs_services.maintenance)
    extensions.register_stats(jobs_services.stats)
    extensions.register_maintenance(server_services.maintenance)
    # **느린 요청 · 큰 응답**(ADR 0026) — 작업 목록 한 번이 93MB · 4.4초였던 것을 사용자가
    # 「화면이 느리다」 고 말하고 나서야 알았다(2026-10-09). 문턱을 넘은 경로가 홈에 뜬다.
    extensions.register_maintenance(server_performance.maintenance)

    # **기계 자격(PAT)으로 쓰는 길.** 안 열면 PAT 로는 못 고친다 — 기본이 「막힘」 이고,
    # 그것이 맞는 기본값이다(shared/scopes.py). MCP 는 사용자의 PAT 를 그대로 넘기므로
    # 레시피 초안 · 시험(ADR 0001)도 이 범위로 열린다.
    scopes.register_write_scope("/api/jobs", "relay:write")
    scopes.register_write_scope("/api/attachments", "relay:write")
    # 플랫폼의 자기소개 — 에이전트가 이 글로 어느 플랫폼에 물을지 고른다. 서버 화면의 다른 쓰기
    # (확장 켜기)는 토큰에 열지 않는다.
    scopes.register_write_scope("/api/server/profile", "relay:write")

    # --- 여기에 도메인 확장을 더한다 ---------------------------------------
    #
    #   extensions.register_stats(equipment_services.stats)
    #   extensions.register_maintenance(equipment_services.maintenance)
    #   extensions.register_workspace_reference(equipment_services.workspace_reference)
    #   extensions.register_workspace_content(equipment_services.workspace_content)
    #
    # PAT 범위도 같은 자리에서 연다. **안 열면 기계 자격으로 못 고친다** —
    # 그것이 맞는 기본값이다(shared/scopes.py).
    #
    #   scopes.register_write_scope("/api/equipment", "equipment:write")
    #   scopes.register_read_only_post("/api/search/")


#: `index.html` 의 확장 메타에 박아 두고 요청마다 바꿔 끼우는 자리.
_EXTENSIONS_TOKEN = "__APP_EXTENSIONS__"


def _enabled_meta(db: Session, settings: Settings) -> str:
    """화면에 심을 켜진 확장 목록.

    **DB 를 못 읽어도 화면은 떠야 한다.** 여기서 터지면 로그인 화면조차 안 뜨고,
    그러면 사람은 원인을 볼 자리가 없다 — 그때는 `.env` 의 기본값으로 그린다.
    """
    try:
        return ",".join(server_services.enabled_names(db))
    except SQLAlchemyError:
        logger.warning("확장 켜짐을 DB 에서 못 읽었습니다 — .env 기본값으로 화면을 그립니다.")
        return ",".join(settings.extension_names)


def _mount_spa(app: FastAPI, settings: Settings) -> None:
    dist = settings.frontend_dist
    index = dist / "index.html"
    if not index.exists():
        logger.info("frontend dist 없음 (%s) — API만 서빙합니다.", dist)
        return

    # 해시가 붙은 자산은 오래 캐시해도 안전하다.
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    # **접두어를 화면에 심는다.** 빌드는 접두어를 모르고(`base: "./"`), 브라우저는
    # `<base href>` 로 자산 주소를, `<meta name="app-base">` 로 API · 라우터 주소를 푼다.
    # 그래서 같은 이미지가 `/` 에서도 `/plm/` 에서도 뜬다 — 접두어는 배포 설정(.env)이지
    # 빌드가 아니다.
    # **이름도 심는다.** 번들 하나로 여러 플랫폼을 띄우므로 화면은 자기가 무슨 플랫폼인지
    # 빌드로는 모른다 — `<meta name="app-name">` 들이 말해 주고, 없을 때(개발 서버)만
    # 화면의 기본값이 쓰인다. `<title>` 도 여기서 바꾼다.
    base = settings.base_path
    metas = "\n    ".join(
        f'<meta name="{key}" content="{html_escape(value)}" />'
        for key, value in (
            ("app-base", base),
            ("app-name", settings.app_name),
            ("app-slug", settings.app_slug),
            ("app-tagline", settings.app_tagline),
            # **MCP 주소.** 비어 있으면 화면이 짐작한다(앱 포트 +2 · 프록시 뒤면 접두어).
            # 짐작이 안 되는 구성에서만 `.env` 의 `MCP_PUBLIC_URL` 이 채운다.
            ("app-mcp", settings.mcp_public_url),
            # **HWAX 포털로 로그인**(ADR 0028) — 로그인 화면이 이 주소가 있을 때만 단추를 낸다.
            # 받는 쪽(`PORTAL_JWKS_URL`)이 꺼져 있으면 비운다: 단추를 누른 사람이 포털을 돌아
            # 콜백의 404 JSON 을 보게 된다.
            (
                "app-portal-launch",
                settings.portal_launch_url.strip() if settings.portal_jwks_url.strip() else "",
            ),
            # **포털 안에서 열렸을 때의 자동 로그인**(ADR 0028) — 화면이 포털의 표준 launch
            # (`/systems/<id>/launch`)를 부를 타일 id. 타일 id = audience(기본 slug)다. 받는
            # 쪽이 꺼져 있으면 비운다 — 그때 화면은 자동 로그인을 시도하지 않는다.
            (
                "app-portal-system",
                settings.portal_audience.strip() if settings.portal_jwks_url.strip() else "",
            ),
            # **이 자리는 요청마다 채운다.** 켜짐이 화면에서 바뀌므로(시스템 관리자 ›
            # 서버) 기동 때 박아 두면 재시작까지 옛 메뉴가 붙는다.
            ("app-extensions", _EXTENSIONS_TOKEN),
        )
    )
    html = index.read_text(encoding="utf-8").replace(
        "<head>", f'<head>\n    <base href="{base}/" />\n    {metas}', 1
    )
    title = f"<title>{html_escape(settings.app_name)}</title>"
    html = re.sub(r"<title>.*?</title>", title, html, count=1)

    # response_model=None — 이 경로는 스키마에 나오지 않으므로 응답 모델이 필요
    # 없다. 반환 애노테이션에 Union 을 쓰면 FastAPI 가 모델을 만들려다 기동에
    # 실패하므로, 여기는 앞으로도 단일 Response 타입으로 둔다.
    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def spa(full_path: str, db: Session = Depends(get_db)) -> HTMLResponse:
        # /api 아래는 위에서 이미 매칭됐어야 한다. 여기 닿았다면 없는 엔드포인트다.
        # index.html 을 돌려주면 프론트가 200 HTML 을 JSON 으로 파싱하려다 실패해
        # 원인이 흐려지므로, 명시적으로 404 를 준다. 응답 본문은 직접 만들지 않고
        # 오류 핸들러에 맡긴다 — 그래야 request_id 와 로그가 함께 남는다.
        if full_path.startswith("api/"):
            raise NotFound(
                code("COMMON", 404),
                "존재하지 않는 엔드포인트입니다.",
                details={"path": f"/{full_path}"},
            )
        # index.html 은 캐시하지 않는다. 배포 후 사용자가 옛 index 를 들고 있으면
        # 사라진 청크를 요청하게 된다.
        return HTMLResponse(
            html.replace(_EXTENSIONS_TOKEN, _enabled_meta(db, settings)),
            headers={"Cache-Control": "no-store"},
        )

    logger.info("SPA 서빙: %s", dist)


def _guard_production_secrets(settings: Settings) -> None:
    """운영에서 기본 비밀키로 뜨는 것을 막는다.

    기본값이 그대로 배포되면 누구나 access 토큰을 위조할 수 있다. **경고 로그는
    아무도 읽지 않으므로 기동 자체를 거부한다.**
    """
    if settings.app_env != "production":
        return
    if settings.jwt_secret == Settings.model_fields["jwt_secret"].default:
        raise RuntimeError(
            "JWT_SECRET 이 기본값입니다. .env 에 난수 값을 넣고 다시 시작하세요."
        )


def _guard_writable_paths(settings: Settings) -> None:
    """쓸 수 있어야 하는 곳에 쓸 수 있나 — **로그를 열기 전에 본다.**

    컨테이너 루트는 읽기 전용이다. 이 경로들이 bind-mount 밖을 가리키면 앱은
    `[Errno 30] Read-only file system` 으로 죽는데, **그 메시지는 무엇을 고쳐야
    하는지 말해 주지 않는다** — 실측으로 겪었다: `.env` 에 `LOG_DIR` 이 없어서
    기본값인 이미지 안 경로로 떨어졌고, 트레이스백은 `pathlib.mkdir` 을 가리켰다.

    로그 설정보다 **먼저** 부른다. 로그를 여는 것 자체가 첫 번째 쓰기라서, 그
    뒤에 두면 이 검사가 영영 안 돈다.
    """
    targets = (
        ("LOG_DIR", settings.log_dir),
        ("FILESTORE_DIR", settings.filestore_dir),
    )
    for label, path in targets:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-probe"
            probe.touch()
            probe.unlink()
        except OSError as failure:
            raise RuntimeError(
                f"{label} 에 쓸 수 없습니다: {path} ({failure.strerror})\n"
                f"\n"
                f"컨테이너로 돌고 있다면 그 경로가 bind-mount 밖입니다. "
                f"systemd 유닛의 --bind 대상과 이 값이 같아야 합니다 "
                f"(이미지는 /data/logs · /data/filestore 를 기본으로 둡니다).\n"
                f"호스트에서 돌고 있다면 그 폴더의 소유자와 권한을 확인하세요."
            ) from failure


class PrefixMiddleware:
    """접두어(`/<slug>`)가 **없이** 온 요청에 접두어를 붙여 준다.

    Starlette 는 `root_path` 가 경로에 **붙어 있다**고 본다 — 라우트 매칭은 붙어 있으면
    떼고 없으면 그대로 두지만, 정적 파일 마운트는 붙어 있어야만 찾는다(없으면
    `/assets/…` 가 404 — 실측). 그런데 앞의 nginx 는 접두어를 떼고 넘기는 쪽이 흔하고,
    직접 붙는 확인은 붙인 채로 온다. 어느 쪽이든 되게 여기서 모양을 하나로 맞춘다.
    """

    def __init__(self, app: ASGIApp, base_path: str) -> None:
        self.app = app
        self.base = base_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.base and scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path != self.base and not path.startswith(self.base + "/"):
                scope = dict(scope)
                scope["path"] = self.base + path
                if scope.get("raw_path"):
                    scope["raw_path"] = self.base.encode() + scope["raw_path"]
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    settings = get_settings()
    # **로그보다 먼저다.** 로그를 여는 것이 첫 번째 쓰기다.
    _guard_writable_paths(settings)
    setup_logging(settings)
    _guard_production_secrets(settings)

    app = FastAPI(
        title=f"{settings.app_name} API",
        version=version.current(),
        docs_url=f"{API_PREFIX}/docs",
        openapi_url=f"{API_PREFIX}/openapi.json",
        # 앞의 nginx 가 `/<slug>` 를 떼고 넘기므로 문서 · 리다이렉트가 만드는 주소에 접두어를
        # 다시 붙여야 한다 — root_path 가 그 자리다.
        root_path=settings.base_path,
    )

    # 순서가 중요하다. add_middleware 는 **나중에 더한 것이 바깥**이므로 아래 두
    # 줄은 RequestId(바깥) -> AccessLog(안쪽) 이 된다. 접근 로그가 요청 id 를
    # 읽으려면 그 id 가 먼저 설정돼 있어야 한다.
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(RequestIdMiddleware)
    # 맨 바깥 — 접근 로그도 접두어가 붙은 한 가지 모양으로 본다.
    app.add_middleware(PrefixMiddleware, base_path=settings.base_path)

    # 요청 처리 밖에서 DB 를 쓰는 곳(접근 로그)이 참조한다. 테스트는 이 값을 자기
    # DB 로 바꿔 끼운다.
    app.state.session_factory = SessionLocal
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    register_error_handlers(app)
    _register_extensions()
    app.include_router(_api_router(settings))

    # SPA catch-all 은 반드시 API 라우터 뒤에 등록한다.
    _mount_spa(app, settings)

    # **DB 가 코드보다 뒤처져 있으면 여기서 말한다.** 안 그러면 사람은 화면의 500
    # 으로 먼저 만나는데, 거기엔 원인이 안 적힌다.
    warn_if_behind(engine)

    logger.info(
        "%s (%s) 기동 (env=%s, 확장 번들=%s, .env 기본=%s)",
        settings.app_name,
        settings.app_slug,
        settings.app_env,
        ",".join(ext_loader.available()) or "없음",
        # **켜짐은 DB 가 답한다** — 기동 때는 DB 를 안 보므로 기본값만 적는다.
        ",".join(settings.extension_names) or "없음",
    )
    return app


app = create_app()
