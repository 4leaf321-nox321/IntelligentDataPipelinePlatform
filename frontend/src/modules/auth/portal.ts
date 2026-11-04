/**
 * HWAX 포털 로그인(ADR 0028) — 로그인 화면의 단추와 `?sso=` 사유.
 *
 * 포털이 그 사람으로 launch 토큰을 만들어 서버의 콜백(`/api/auth/portal-callback`)에 POST 한다.
 * 성공하면 서버가 refresh 쿠키를 심고 SPA 루트로 보내므로 화면은 할 일이 없다 — 기동 때 그
 * 쿠키로 세션을 되살린다(`AuthContext`). 실패 · 승인 대기 · 정지면 서버가 **로그인 화면에
 * `?sso=<사유>` 를 달아** 보낸다. 사유는 사람이 할 일이 갈리는 만큼만 온다(자세한 것은 서버 로그).
 */

import { ApiError, api } from '@/shared/api/client'
import type { LoginResponse } from '@/shared/auth/types'
import { APP_SLUG } from '@/shared/branding'

/**
 * 단추가 보낼 곳 — 포털의 `/launch/<id>`. 서버가 `<meta name="app-portal-launch">` 로 심는다
 * (받을 준비 — `PORTAL_JWKS_URL` — 까지 된 설치만). **비면 단추가 없다.** 부를 때마다 읽는다 —
 * 시험이 meta 를 바꿔 끼운다.
 */
export function portalLaunchUrl(): string {
  if (typeof document === 'undefined') return ''
  return document.querySelector('meta[name="app-portal-launch"]')?.getAttribute('content') ?? ''
}

export type SsoTone = 'info' | 'error'

// --- 포털 안에서 바로 들어온 사람의 자동 로그인(Report Archive 와 같은 길) -----------------
//
// 포털 타일을 누르면 포털이 토큰을 서버 콜백에 POST 한다(위). 북마크 · 링크로 **바로** 들어오면
// 토큰이 없다 — 그때 포털 안이면(포털 로그인 쿠키 `hwax_csrf` 가 보이면 — 포털 nginx 뒤라 같은
// 오리진이다) 화면이 포털의 **표준 launch**(`POST /systems/<id>/launch`, 타일이 쓰는 그것)를
// 포털 세션으로 불러 토큰을 받고, 서버의 `/api/auth/portal-exchange` 로 바꾼다.

/** 포털의 로그인 쿠키 — 포털이 심는다. 포털 nginx 뒤에서만 보인다. */
const CSRF_COOKIE = 'hwax_csrf'

/**
 * 「이번 브라우저 세션에 자동 시도를 했나 · 사용자가 로그아웃했나」 — **인스턴스마다 따로.** 한
 * 포털 오리진 밑에 같은 틀의 인스턴스가 여럿 붙는다(`/<slug>/`) — 키를 나누지 않으면 한
 * 인스턴스에서 로그아웃한 것이 옆 인스턴스의 자동 로그인까지 막는다.
 */
const TRIED_KEY = `${APP_SLUG}:portal_tried`
const OPTOUT_KEY = `${APP_SLUG}:portal_optout`

function readCookie(name: string): string | null {
  if (typeof document === 'undefined') return null
  const found = document.cookie.match(new RegExp(`(?:^|;)\\s*${name}\\s*=\\s*([^;]+)`))
  return found ? decodeURIComponent(found[1]) : null
}

function remembered(key: string): boolean {
  try {
    return sessionStorage.getItem(key) !== null
  } catch {
    return false
  }
}

function remember(key: string, on: boolean): void {
  try {
    if (on) sessionStorage.setItem(key, '1')
    else sessionStorage.removeItem(key)
  } catch {
    // 사생활 보호 모드 — 자동 로그인의 기억만 안 된다.
  }
}

/**
 * 포털이 부를 타일 id — 서버가 `<meta name="app-portal-system">` 로 심는다(타일 id = audience,
 * 기본 slug). 포털 로그인이 꺼진 설치는 비어 있다 — 그때는 자동 로그인을 시도하지 않는다.
 */
export function portalSystemId(): string {
  if (typeof document === 'undefined') return ''
  return document.querySelector('meta[name="app-portal-system"]')?.getAttribute('content') ?? ''
}

/** 지금 HWAX 포털 안에서 열렸나 — 포털 로그인이 켜진 설치이고 포털 쿠키가 보이면. */
export function insidePortal(): boolean {
  return Boolean(portalSystemId() && readCookie(CSRF_COOKIE))
}

/** 화면이 뜰 때 자동으로 포털 로그인을 시도해도 되나 — 브라우저 세션당 한 번, 로그아웃하면 안 함. */
export function shouldAutoHandoff(): boolean {
  return insidePortal() && !remembered(OPTOUT_KEY) && !remembered(TRIED_KEY)
}

/** 사용자가 로그아웃했다 — 이번 브라우저 세션에는 저절로 다시 들이지 않는다. */
export function markPortalOptOut(): void {
  remember(OPTOUT_KEY, true)
}

/** 사용자가 직접 포털로 들어가기로 했다(단추) — 막아 둔 것을 푼다. */
export function clearPortalOptOut(): void {
  remember(OPTOUT_KEY, false)
  remember(TRIED_KEY, false)
}

/** 포털의 launch 응답(HandoffPayload)에서 토큰을 꺼낸다 — 폼으로 보내는 꼴 · 주소에 싣는 꼴 둘 다. */
function tokenFrom(payload: unknown): string | null {
  const body = (payload ?? {}) as { fields?: Record<string, unknown>; url?: unknown }
  const fields = body.fields ?? {}
  const first = typeof fields.token === 'string' ? fields.token : Object.values(fields)[0]
  if (typeof first === 'string' && first) return first
  if (typeof body.url === 'string') {
    try {
      return new URL(body.url, window.location.origin).searchParams.get('token')
    } catch {
      return null
    }
  }
  return null
}

/**
 * 포털 자동 로그인의 결과. 안 되면 `reason` — 서버의 사유(`pending` · `invalid` …, 콜백의
 * `?sso=` 와 같다) 또는 화면의 사유: `portal-login`(포털 로그인이 끝났다 — 포털의 401) ·
 * `no-tile`(포털이 토큰을 주지 않았다 — 타일이 없거나 이 사람에게 안 보인다) · `network`.
 */
export type HandoffResult = { ok: true; body: LoginResponse } | { ok: false; reason: string }

export async function portalHandoff({ auto = false } = {}): Promise<HandoffResult> {
  if (auto) remember(TRIED_KEY, true)
  const csrf = readCookie(CSRF_COOKIE)
  const system = portalSystemId()
  if (!csrf || !system) return { ok: false, reason: 'no-tile' }
  let launched: string | null
  try {
    // **포털 API 는 오리진 루트에 있다** — 우리 api 클라이언트는 접두어(`/<slug>/api`)를
    // 붙이므로 여기는 fetch 로 절대 경로를 부른다.
    const response = await fetch(`/systems/${encodeURIComponent(system)}/launch`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': csrf },
    })
    if (response.status === 401) return { ok: false, reason: 'portal-login' }
    if (!response.ok) return { ok: false, reason: 'no-tile' }
    launched = tokenFrom(await response.json())
  } catch {
    return { ok: false, reason: 'network' }
  }
  if (!launched) return { ok: false, reason: 'no-tile' }
  try {
    const body = await api.post<LoginResponse>('/auth/portal-exchange', { token: launched })
    return { ok: true, body }
  } catch (caught) {
    if (caught instanceof ApiError) {
      const reason = caught.details.reason
      return { ok: false, reason: typeof reason === 'string' ? reason : 'invalid' }
    }
    return { ok: false, reason: 'network' }
  }
}

/**
 * `?sso=` 사유 → 사람 말. **모르는 사유도 말한다** — 서버가 새 사유를 더했는데 화면이 그 말을
 * 모르면 아무것도 안 뜨고, 사람은 단추가 고장 난 줄 안다.
 */
export function ssoMessage(reason: string): { tone: SsoTone; text: string } {
  switch (reason) {
    case 'pending':
      return {
        tone: 'info',
        text: '가입 승인을 기다립니다. HWAX 포털로 확인된 신원으로 가입 신청이 접수되어 있습니다 — 관리자가 승인하면 HWAX 포털에서 다시 열거나 「HWAX 포털로 로그인」 으로 로그인할 수 있습니다.',
      }
    case 'suspended':
      return { tone: 'error', text: '정지된 계정입니다. 관리자에게 문의하세요.' }
    case 'deleted':
      return { tone: 'error', text: '삭제된 계정입니다. 관리자에게 문의하세요.' }
    case 'expired':
      return {
        tone: 'error',
        text: 'HWAX 포털의 로그인 정보가 유효 시간을 벗어났습니다. HWAX 포털에서 다시 여세요 — 계속되면 서버 시각이 어긋났을 수 있으니 관리자에게 알려 주세요.',
      }
    case 'unavailable':
      return {
        tone: 'error',
        text: '이 서버가 HWAX 포털의 공개 키를 받지 못해 포털 로그인을 확인할 수 없습니다. 아이디 · 비밀번호로 로그인하거나, 관리자에게 알려 주세요.',
      }
    case 'admin_sub':
      return {
        tone: 'error',
        text: '이 계정은 아이디가 포털의 메일 주소와 달라 포털 로그인으로 연결하지 않습니다(시스템 관리자 계정). 아이디 · 비밀번호로 로그인하세요.',
      }
    case 'portal-login':
      return {
        tone: 'info',
        text: 'HWAX 포털 로그인이 끝났습니다. HWAX 포털에 다시 로그인한 뒤 여세요 — 또는 이 플랫폼의 아이디 · 비밀번호로 로그인하세요.',
      }
    case 'no-tile':
      return {
        tone: 'error',
        text: 'HWAX 포털이 이 플랫폼의 로그인 정보를 주지 않았습니다 — 포털에 이 플랫폼이 등록되지 않았거나 사용 권한이 없습니다. 관리자에게 알려 주세요.',
      }
    case 'network':
      return {
        tone: 'error',
        text: 'HWAX 포털 로그인 중 연결이 끊겼습니다. 잠시 뒤 다시 시도하거나 아이디 · 비밀번호로 로그인하세요.',
      }
    case 'invalid':
      return {
        tone: 'error',
        text: 'HWAX 포털의 로그인 정보를 확인하지 못했습니다. HWAX 포털에서 다시 여세요 — 한 번 사용한 로그인 정보는 다시 받지 않습니다.',
      }
    default:
      return {
        tone: 'error',
        text: `HWAX 포털 로그인에 실패했습니다(${reason}). HWAX 포털에서 다시 열고, 계속되면 관리자에게 알려 주세요.`,
      }
  }
}

/** 게이트웨이 위임을 마지막으로 거절한 일 — 모든 서버 · 프로세스(접근 로그)에서. */
export interface GatewayRefusal {
  at: string
  /** 400 머리 없음 · 401 비밀 다름 · 403 계정 · 주소 · 404 꺼짐. */
  status: number
  /** 이 프로세스가 본 거절이면 자세한 사유, 아니면 숫자의 뜻. */
  reason: string
}

/**
 * HWAX 게이트웨이의 사람별 위임(`POST /api/auth/sso`, ADR 0028) — 포털 로그인과 따로 켜진다.
 * 게이트웨이는 위임이 200 이 아니면 그 사람의 도구 호출을 거절한다(서비스 계정으로 안 내려간다).
 */
export interface GatewayDelegation {
  /** `HEAX_SSO_SECRET` 이 있나. */
  enabled: boolean
  /** 게이트웨이가 부르는 길 — 앱 포트로 직접(접두어 없이). */
  sso_path: string
  allowed_ips: string[]
  /** 못 읽은 허용 주소 — 하나라도 있으면 모든 발급을 거절한다. */
  invalid_ips: string[]
  scopes: string[]
  token_seconds: number
  live_tokens: number
  last_issued_at: string | null
  last_refusal: GatewayRefusal | null
}

/** 서버 화면이 보는 포털 로그인 상태(`GET /api/auth/portal-status`, 시스템 관리자). */
export interface PortalStatus {
  enabled: boolean
  jwks_url: string | null
  /** 토큰의 `aud` 로 받는 값 — 포털 타일의 `audience` 와 같아야 한다. */
  audience: string
  issuer: string
  launch_url: string | null
  /** 포털 타일의 `url` 에 적을 길(이 설치의 접두어가 붙은 모양). */
  callback_path: string
  /** 공개 키를 지금 받았나. 꺼져 있으면 null. */
  reachable: boolean | null
  kids: string[]
  /** **이 프로세스가** 마지막으로 받은 때 — 워커마다 따로 든다. */
  fetched_at: string | null
  last_error: string | null
  last_error_at: string | null
  /** 설정이 서로 안 맞는 것 — 접두어 없음 · TRUST_PROXY 꺼짐 · 위임 비밀이 짧음 등. */
  problems: string[]
  delegation: GatewayDelegation
}

export const portalApi = {
  status: () => api.get<PortalStatus>('/auth/portal-status'),
}
