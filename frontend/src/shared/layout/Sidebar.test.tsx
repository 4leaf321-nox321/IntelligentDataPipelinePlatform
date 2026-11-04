/**
 * 사이드바의 **묶음 접기** — 타입이 늘면 목록이 길어지고, 그때 사람은 자기가 쓰는 묶음을
 * 찾으려고 매번 스크롤한다. 접은 것은 브라우저에 기억하고, 접힌 묶음 안에 지금 보는 화면이
 * 있으면 점으로 알려 준다(접었다고 「어디 있는지」 를 모르게 두지 않는다).
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { is_system_admin: false, memberships: [] } }),
}))
const systemApi = vi.hoisted(() => ({ health: vi.fn() }))
vi.mock('@/shared/api/system', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/api/system')>()),
  systemApi,
}))
const ontologyApi = vi.hoisted(() => ({ nav: vi.fn() }))
vi.mock('@/modules/ontology/api', () => ({ ontologyApi }))

async function mount(path = '/w/hq', nav: unknown[] = []) {
  systemApi.health.mockResolvedValue({ status: 'ok', version: '0.0.0' })
  ontologyApi.nav.mockResolvedValue(nav)
  const { Sidebar } = await import('@/shared/layout/Sidebar')
  render(
    <MemoryRouter initialEntries={[path]}>
      <Sidebar collapsed={false} workspaceSlug="hq" />
    </MemoryRouter>,
  )
  await waitFor(() => expect(screen.getByRole('button', { name: /내 활동/ })).toBeInTheDocument())
}

describe('사이드바 묶음 접기', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
  })

  it('접힌 묶음 안에 지금 보는 화면이 있으면 점을 찍는다', async () => {
    await mount('/jobs')
    const head = screen.getByRole('button', { name: /내 활동/ })
    expect(within(head).queryByTitle(/지금 보는 화면/)).not.toBeInTheDocument()

    await userEvent.click(head)
    expect(within(head).getByTitle(/지금 보는 화면/)).toBeInTheDocument()

    // 다른 화면을 보고 있으면 점이 없다.
    screen.getByRole('button', { name: /공통/ }).click()
    expect(
      within(screen.getByRole('button', { name: /공통/ })).queryByTitle(/지금 보는 화면/),
    ).not.toBeInTheDocument()
  })

  it('제목 없는 묶음(홈)은 접는 단추가 없다', async () => {
    await mount()
    expect(screen.getByRole('link', { name: '홈' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '홈' })).not.toBeInTheDocument()
  })
})
