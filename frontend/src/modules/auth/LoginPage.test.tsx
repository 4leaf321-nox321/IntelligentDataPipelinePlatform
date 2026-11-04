/**
 * 로그인 화면의 **HWAX 포털로 로그인**(ADR 0028).
 *
 * - 단추는 서버가 주소를 심었을 때만 선다 — 포털에 붙지 않은 설치에 단추가 있으면 누른 사람이
 *   어디에도 닿지 않는다.
 * - 서버가 `?sso=<사유>` 로 돌려보내면 사람 말로 보인다. 모르는 사유도 말한다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import LoginPage from '@/modules/auth/LoginPage'
import { ssoMessage } from '@/modules/auth/portal'

const auth = vi.hoisted(() => ({
  status: 'anonymous',
  user: null,
  login: vi.fn(),
  loginWithPortal: vi.fn(),
  portalRefusal: null as string | null,
}))
vi.mock('@/shared/auth/AuthContext', () => ({ useAuth: () => auth }))

const LAUNCH = 'https://portal.example/launch/hwaxapp'

function setLaunch(value: string | null) {
  document.head.querySelector('meta[name="app-portal-launch"]')?.remove()
  if (value === null) return
  const meta = document.createElement('meta')
  meta.name = 'app-portal-launch'
  meta.content = value
  document.head.appendChild(meta)
}

function setInside(on: boolean) {
  document.head.querySelector('meta[name="app-portal-system"]')?.remove()
  document.cookie = on
    ? 'hwax_csrf=csrf-1; path=/'
    : 'hwax_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/'
  if (!on) return
  const meta = document.createElement('meta')
  meta.name = 'app-portal-system'
  meta.content = 'hwaxapp'
  document.head.appendChild(meta)
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/" element={<p>첫 화면</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  auth.portalRefusal = null
})

afterEach(() => {
  setLaunch(null)
  setInside(false)
  vi.unstubAllGlobals()
})

describe('HWAX 포털로 로그인', () => {
  it('서버가 주소를 심으면 단추가 그 주소로 간다', () => {
    setLaunch(LAUNCH)
    renderAt('/login')
    const link = screen.getByRole('link', { name: 'HWAX 포털로 로그인' })
    expect(link).toHaveAttribute('href', LAUNCH)
  })

  it('주소가 없거나 비면 단추가 없다', () => {
    renderAt('/login')
    expect(screen.queryByRole('link', { name: 'HWAX 포털로 로그인' })).toBeNull()
    setLaunch('')
    renderAt('/login')
    expect(screen.queryByRole('link', { name: 'HWAX 포털로 로그인' })).toBeNull()
  })

  it('승인 대기는 기다리라고 말하고 오류 색을 쓰지 않는다', () => {
    renderAt('/login?sso=pending')
    const shown = screen.getByRole('status')
    expect(shown).toHaveTextContent('가입 승인을 기다립니다')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('정지 · 확인 실패는 사유를 오류로 보인다', () => {
    renderAt('/login?sso=suspended')
    expect(screen.getByRole('alert')).toHaveTextContent('정지된 계정입니다')
  })

  it('사유가 없으면 아무것도 안 뜬다', () => {
    renderAt('/login')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(screen.queryByRole('status')).toBeNull()
  })
})

describe('포털 안에서 열렸을 때(자동 로그인과 같은 길)', () => {
  it('단추가 페이지를 옮기지 않고 그 자리에서 포털로 로그인한다', async () => {
    setLaunch(LAUNCH)
    setInside(true)
    auth.loginWithPortal.mockResolvedValue({ ok: true, body: {} })
    renderAt('/login')
    expect(screen.queryByRole('link', { name: 'HWAX 포털로 로그인' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'HWAX 포털로 로그인' }))
    expect(auth.loginWithPortal).toHaveBeenCalled()
    expect(await screen.findByText('첫 화면')).toBeInTheDocument()
  })

  it('거절되면 그 사유를 사람 말로 — 포털 로그인이 끝났으면 포털로 보낸다', async () => {
    setLaunch(LAUNCH)
    setInside(true)
    auth.loginWithPortal.mockResolvedValue({ ok: false, reason: 'pending' })
    renderAt('/login')
    await userEvent.click(screen.getByRole('button', { name: 'HWAX 포털로 로그인' }))
    expect(await screen.findByRole('status')).toHaveTextContent('가입 승인을 기다립니다')

    const assign = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign })
    auth.loginWithPortal.mockResolvedValue({ ok: false, reason: 'portal-login' })
    await userEvent.click(screen.getByRole('button', { name: 'HWAX 포털로 로그인' }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith(LAUNCH))
  })

  it('기동 때 자동 로그인이 거절된 사유도 보인다', () => {
    auth.portalRefusal = 'suspended'
    renderAt('/login')
    expect(screen.getByRole('alert')).toHaveTextContent('정지된 계정입니다')
  })
})

describe('?sso= 사유 문구', () => {
  it.each([
    ['pending', 'info', '가입 승인을 기다립니다'],
    ['deleted', 'error', '삭제된 계정입니다'],
    ['expired', 'error', '유효 시간'],
    ['invalid', 'error', '확인하지 못했습니다'],
    ['unavailable', 'error', '공개 키를 받지 못해'],
    ['admin_sub', 'error', '아이디 · 비밀번호로 로그인하세요'],
    ['portal-login', 'info', 'HWAX 포털에 다시 로그인한 뒤'],
    ['no-tile', 'error', '등록되지 않았거나 사용 권한이 없습니다'],
    ['network', 'error', '연결이 끊겼습니다'],
  ])('%s', (reason, tone, words) => {
    const got = ssoMessage(reason)
    expect(got.tone).toBe(tone)
    expect(got.text).toContain(words)
  })

  it('모르는 사유도 말한다 — 화면이 모르는 말이 와도 아무것도 안 뜨지는 않는다', () => {
    const got = ssoMessage('brand-new')
    expect(got.tone).toBe('error')
    expect(got.text).toContain('brand-new')
  })
})
