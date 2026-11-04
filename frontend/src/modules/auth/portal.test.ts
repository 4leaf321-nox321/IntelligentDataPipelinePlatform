/**
 * HWAX 포털 안에서 바로 들어온 사람의 자동 로그인(ADR 0028, Report Archive 와 같은 길).
 *
 * - 포털 안인지는 포털 쿠키(`hwax_csrf`)와 서버가 심은 타일 id 둘 다로 본다.
 * - 포털의 표준 launch(`POST /systems/<id>/launch`)를 포털 세션 · CSRF 머리로 부르고, 받은 토큰을
 *   서버의 `/auth/portal-exchange` 로 바꾼다.
 * - 자동 시도는 브라우저 세션당 한 번, 로그아웃하면 안 한다 — **인스턴스마다 따로** 기억한다.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  clearPortalOptOut,
  insidePortal,
  markPortalOptOut,
  portalHandoff,
  shouldAutoHandoff,
} from '@/modules/auth/portal'
import { ApiError } from '@/shared/api/client'
import { APP_SLUG } from '@/shared/branding'

const api = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('@/shared/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/api/client')>()),
  api,
}))

const BODY = { access_token: 'acc', expires_in: 900, user: { email: 'a@corp.test' } }

function setSystem(value: string | null) {
  document.head.querySelector('meta[name="app-portal-system"]')?.remove()
  if (value === null) return
  const meta = document.createElement('meta')
  meta.name = 'app-portal-system'
  meta.content = value
  document.head.appendChild(meta)
}

function setCookie(value: string | null) {
  document.cookie =
    value === null
      ? 'hwax_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
      : `hwax_csrf=${value}; path=/`
}

function portalAnswers(status: number, body: unknown = {}) {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  setSystem('hwaxapp')
  setCookie('csrf-1')
})

afterEach(() => {
  vi.unstubAllGlobals()
  setSystem(null)
  setCookie(null)
})

describe('포털 안인지', () => {
  it('포털 쿠키와 타일 id 가 둘 다 있어야 포털 안이다', () => {
    expect(insidePortal()).toBe(true)
    setCookie(null)
    expect(insidePortal()).toBe(false) // 포털 밖(앱 포트로 직접)
    setCookie('csrf-1')
    setSystem('')
    expect(insidePortal()).toBe(false) // 포털 로그인이 꺼진 설치
  })

  it('자동 시도는 세션당 한 번, 로그아웃하면 안 한다 — 인스턴스마다 따로 기억한다', async () => {
    portalAnswers(401)
    expect(shouldAutoHandoff()).toBe(true)
    await portalHandoff({ auto: true })
    expect(shouldAutoHandoff()).toBe(false)
    expect(sessionStorage.getItem(`${APP_SLUG}:portal_tried`)).toBe('1')

    clearPortalOptOut()
    expect(shouldAutoHandoff()).toBe(true)
    markPortalOptOut()
    expect(shouldAutoHandoff()).toBe(false)
    expect(sessionStorage.getItem(`${APP_SLUG}:portal_optout`)).toBe('1')
    // 단추(사람이 직접)는 막아 둔 것을 푼다.
    clearPortalOptOut()
    expect(shouldAutoHandoff()).toBe(true)
  })
})

describe('포털 로그인 받기', () => {
  it('포털의 launch 를 포털 세션 · CSRF 머리로 부르고 받은 토큰을 바꾼다', async () => {
    const fetch = portalAnswers(200, { mode: 'auto_post', fields: { token: 'launch-1' } })
    api.post.mockResolvedValue(BODY)
    const got = await portalHandoff()
    expect(got).toEqual({ ok: true, body: BODY })
    expect(fetch).toHaveBeenCalledWith('/systems/hwaxapp/launch', {
      method: 'POST',
      credentials: 'include',
      headers: { 'X-CSRF-Token': 'csrf-1' },
    })
    expect(api.post).toHaveBeenCalledWith('/auth/portal-exchange', { token: 'launch-1' })
  })

  it('주소에 싣는 꼴(redirect)의 토큰도 꺼낸다', async () => {
    portalAnswers(200, { mode: 'redirect', url: 'https://portal.test/hwaxapp/cb?token=launch-2' })
    api.post.mockResolvedValue(BODY)
    await portalHandoff()
    expect(api.post).toHaveBeenCalledWith('/auth/portal-exchange', { token: 'launch-2' })
  })

  it('포털 로그인이 끝났으면 portal-login, 타일이 없으면 no-tile — 서버에 묻지 않는다', async () => {
    portalAnswers(401)
    expect(await portalHandoff()).toEqual({ ok: false, reason: 'portal-login' })
    portalAnswers(404)
    expect(await portalHandoff()).toEqual({ ok: false, reason: 'no-tile' })
    portalAnswers(200, { fields: {} })
    expect(await portalHandoff()).toEqual({ ok: false, reason: 'no-tile' })
    expect(api.post).not.toHaveBeenCalled()
  })

  it('서버의 거절은 콜백과 같은 사유로(승인 대기 등)', async () => {
    portalAnswers(200, { fields: { token: 'launch-3' } })
    api.post.mockRejectedValue(
      new ApiError(403, {
        error: { code: 'APP-AUTH-0020', message: 'x', details: { reason: 'pending' } },
      }),
    )
    expect(await portalHandoff()).toEqual({ ok: false, reason: 'pending' })
  })

  it('포털 밖이면 아무것도 부르지 않는다', async () => {
    setCookie(null)
    const fetch = portalAnswers(200)
    expect(await portalHandoff()).toEqual({ ok: false, reason: 'no-tile' })
    expect(fetch).not.toHaveBeenCalled()
  })
})
