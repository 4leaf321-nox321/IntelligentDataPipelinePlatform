/**
 * 기동 때의 세션 — refresh 쿠키로 되살리고, 못 되살렸는데 HWAX 포털 안이면 포털로 저절로 로그인한다
 * (ADR 0028). 그 사이는 「불러오는 중」 이다(로그인 화면이 번쩍이지 않게). 포털 안에서 사용자가
 * 로그아웃하면 그 브라우저 세션에는 다시 저절로 들이지 않는다.
 */

import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthProvider, useAuth } from '@/shared/auth/AuthContext'

const client = vi.hoisted(() => ({
  refreshSession: vi.fn(),
  api: { post: vi.fn(), get: vi.fn() },
  session: { setToken: vi.fn(), onLost: vi.fn() },
}))
vi.mock('@/shared/api/client', () => client)
const portal = vi.hoisted(() => ({
  shouldAutoHandoff: vi.fn(),
  portalHandoff: vi.fn(),
  insidePortal: vi.fn(),
  markPortalOptOut: vi.fn(),
  clearPortalOptOut: vi.fn(),
}))
vi.mock('@/modules/auth/portal', () => portal)

const USER = { id: 'u1', email: 'a@corp.test', display_name: '포털 사람' }

function Probe() {
  const { status, user, portalRefusal, logout } = useAuth()
  return (
    <div>
      <p>상태 {status}</p>
      <p>사람 {user?.email ?? '없음'}</p>
      <p>거절 {portalRefusal ?? '없음'}</p>
      <button type="button" onClick={() => void logout()}>
        로그아웃
      </button>
    </div>
  )
}

function boot() {
  return render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  client.refreshSession.mockRejectedValue(new Error('세션 없음'))
  client.api.post.mockResolvedValue(undefined)
})

describe('기동 때 포털 자동 로그인', () => {
  it('쿠키로 못 되살렸고 포털 안이면 포털로 로그인한다 — 그 사이는 불러오는 중', async () => {
    portal.shouldAutoHandoff.mockReturnValue(true)
    let finish: (value: unknown) => void = () => {}
    portal.portalHandoff.mockReturnValue(new Promise((resolve) => (finish = resolve)))
    boot()
    await waitFor(() => expect(portal.portalHandoff).toHaveBeenCalledWith({ auto: true }))
    expect(screen.getByText('상태 loading')).toBeInTheDocument()
    await act(async () => {
      finish({ ok: true, body: { access_token: 'acc', expires_in: 900, user: USER } })
    })
    expect(await screen.findByText('상태 authenticated')).toBeInTheDocument()
    expect(screen.getByText('사람 a@corp.test')).toBeInTheDocument()
    expect(client.session.setToken).toHaveBeenCalledWith('acc')
  })

  it('포털 밖이거나 이미 시도했으면 하지 않는다', async () => {
    portal.shouldAutoHandoff.mockReturnValue(false)
    boot()
    expect(await screen.findByText('상태 anonymous')).toBeInTheDocument()
    expect(portal.portalHandoff).not.toHaveBeenCalled()
  })

  it('계정 사유로 거절되면 그 사유를 로그인 화면에 넘긴다 — 포털 쪽 사정은 조용히', async () => {
    portal.shouldAutoHandoff.mockReturnValue(true)
    portal.portalHandoff.mockResolvedValue({ ok: false, reason: 'pending' })
    boot()
    expect(await screen.findByText('거절 pending')).toBeInTheDocument()
    expect(screen.getByText('상태 anonymous')).toBeInTheDocument()
  })

  it('포털 로그인이 끝난 것(portal-login)은 사유로 넘기지 않는다', async () => {
    portal.shouldAutoHandoff.mockReturnValue(true)
    portal.portalHandoff.mockResolvedValue({ ok: false, reason: 'portal-login' })
    boot()
    expect(await screen.findByText('상태 anonymous')).toBeInTheDocument()
    expect(screen.getByText('거절 없음')).toBeInTheDocument()
  })
})

describe('로그아웃', () => {
  it('포털 안에서 로그아웃하면 이번 세션에는 저절로 다시 들이지 않는다', async () => {
    client.refreshSession.mockResolvedValue({ access_token: 'acc', expires_in: 900, user: USER })
    portal.insidePortal.mockReturnValue(true)
    boot()
    expect(await screen.findByText('상태 authenticated')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '로그아웃' }))
    expect(portal.markPortalOptOut).toHaveBeenCalled()
    expect(client.api.post).toHaveBeenCalledWith('/auth/logout')
    expect(await screen.findByText('상태 anonymous')).toBeInTheDocument()
  })

  it('포털 밖에서는 막을 것이 없다', async () => {
    client.refreshSession.mockResolvedValue({ access_token: 'acc', expires_in: 900, user: USER })
    portal.insidePortal.mockReturnValue(false)
    boot()
    expect(await screen.findByText('상태 authenticated')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '로그아웃' }))
    expect(portal.markPortalOptOut).not.toHaveBeenCalled()
  })
})
