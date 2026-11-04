/**
 * 이 화면이 무슨 플랫폼인가 — **서버가 말해 준다.**
 *
 * 번들 하나로 여러 플랫폼(허브 · 그룹 쌍둥이들)을 띄운다. 그래서 이름은 빌드에 굽지 않고,
 * 서버가 `index.html` 에 심는 `<meta name="app-name">` 들에서 읽는다(backend/app/main.py —
 * 설치의 `.env` 가 정한다). 첫 화면부터 값이 있으므로 깜빡임이 없다.
 *
 * meta 가 없는 곳은 둘뿐이다 — Vite 개발 서버(vite.config.ts 가 `backend/.env` 에서 같은 meta 를
 * 심는다)와 시험. 그때만 아래 기본값(틀의 이름)이 쓰인다. **기본값을 제품 이름으로 고치지
 * 않는다** — 그러면 모든 설치가 그 이름으로 보인다. 설치의 이름은 `.env` 에.
 *
 * 서버 응답에도 이름이 실려 오므로(`/api/health`) 둘이 어긋나면 서버 화면에서 드러난다.
 */

function meta(name: string): string | null {
  if (typeof document === 'undefined') return null
  return document.querySelector(`meta[name="${name}"]`)?.getAttribute('content') ?? null
}

/** 틀의 기본값 — meta 가 없을 때(개발 서버 · 시험)만. 백엔드 `branding.py` 와 같은 값. */
export const DEFAULT_APP_NAME = '데이터 중계'
export const DEFAULT_APP_SLUG = 'datarelay'
export const DEFAULT_APP_TAGLINE = '레거시 시스템 → Standard Platform 데이터 중계'

/** 브라우저 탭·로그인 화면·사이드바 머리글. */
export const APP_NAME = meta('app-name') || DEFAULT_APP_NAME

/**
 * 기계가 읽는 이름 — 서버의 `app_slug`. 저쪽에서는 DB 이름·쿠키 이름·토큰 표식을 만들고,
 * 이쪽에서는 localStorage 키만 만든다 — 같은 서버의 다른 경로에 두 플랫폼을 얹는 구성에서
 * 저장소가 출처로 안 갈리기 때문이다.
 */
export const APP_SLUG = meta('app-slug') || DEFAULT_APP_SLUG

/**
 * 한 줄 설명. 로그인 화면과 사이드바가 같은 말을 한다. **비면 안 그린다.** 틀의 기본 문구는
 * 이름도 틀 그대로일 때만 — 이름을 정한 인스턴스 밑에 「공통 틀」 이 붙으면 틀린 말이다.
 */
export const APP_TAGLINE =
  meta('app-tagline') ?? (APP_NAME === DEFAULT_APP_NAME ? DEFAULT_APP_TAGLINE : '')

/**
 * 이 설치가 켠 확장 — **첫 그림의 씨앗.** 진짜 목록은 서버가 답한다
 * (`/api/server/enabled-extensions`, `extensions/EnabledProvider`). 켜짐이 화면에서 바뀌므로
 * 메타만 믿으면 껐는데 메뉴가 남고, Vite 개발 서버는 `backend/.env` 를 심어 아예 안 맞는다.
 */
export const ENABLED_EXTENSIONS: readonly string[] = (meta('app-extensions') ?? '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)

/** localStorage 키의 앞머리. */
export const STORAGE_PREFIX = APP_SLUG

/** 클라이언트가 만드는 오류의 코드 앞머리. 서버의 ERROR_PREFIX 와 같아야 한다. */
export const ERROR_PREFIX = 'APP'
