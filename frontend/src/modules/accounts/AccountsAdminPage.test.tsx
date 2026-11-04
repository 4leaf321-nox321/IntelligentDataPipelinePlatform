/**
 * 계정 화면에서 지키는 것 — **시스템 관리자 지정은 한 번 눌러서 되지 않는다.**
 *
 * 그 한 번이 전 부서의 자료와 계정 · 서버 설정을 여는 일이라, 잘못 누른 것을 알아채는
 * 자리가 어디에도 없다. 무엇이 열리는지 적고 한 번 더 묻는다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Account } from '@/modules/accounts/api'

const accountApi = vi.hoisted(() => ({
  list: vi.fn(),
  summary: vi.fn(),
  setSystemAdmin: vi.fn(),
  resetPassword: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  suspend: vi.fn(),
  activate: vi.fn(),
  create: vi.fn(),
  tokens: vi.fn(),
  revokeToken: vi.fn(),
}))
const workspaceApi = vi.hoisted(() => ({ options: vi.fn() }))
vi.mock('@/modules/accounts/api', () => ({ accountApi }))
vi.mock('@/modules/workspaces/api', () => ({ workspaceApi }))

const ACCOUNT: Account = {
  id: 'acc-1',
  email: 'hong',
  display_name: '홍길동',
  status: 'active',
  is_system_admin: false,
  must_change_password: false,
  home_workspace_slug: 'hq',
  home_workspace_name: '본사',
  requested_workspace_slug: null,
  requested_workspace_name: null,
  memberships: ['hq'],
  workspaces: [{ slug: 'hq', name: '본사', path: '본사', role: 'member', is_home: true }],
  created_at: '2026-01-01T00:00:00Z',
  decided_at: null,
  decision_note: null,
}

async function open(account = ACCOUNT, { url = '/admin/accounts', total = 1 } = {}) {
  accountApi.list.mockImplementation(async (query: { limit: number; offset: number }) => ({
    items: [account],
    total,
    limit: query.limit,
    offset: query.offset,
  }))
  accountApi.summary.mockResolvedValue({ pending: 0, active: 1, suspended: 0 })
  workspaceApi.options.mockResolvedValue([{ slug: 'hq', name: '본사', path: '본사', depth: 0 }])
  accountApi.setSystemAdmin.mockResolvedValue({ ...account, is_system_admin: true })
  const { default: AccountsAdminPage } = await import('@/modules/accounts/AccountsAdminPage')
  render(
    <MemoryRouter initialEntries={[url]}>
      <AccountsAdminPage />
    </MemoryRouter>,
  )
  await waitFor(() => expect(screen.getByText('홍길동')).toBeInTheDocument())
}

describe('계정 관리 · 쪽 넘김과 상태 거르기', () => {
  beforeEach(() => vi.clearAllMocks())

  it('50명이 넘으면 쪽을 넘긴다 — 처음 50명이 전부로 보이지 않는다', async () => {
    // 맨 리스트로 받던 때는 limit/offset 없이 한 번 부르고 끝이었다(2026-10-08).
    await open(ACCOUNT, { total: 120 })
    expect(accountApi.list).toHaveBeenLastCalledWith({ status: null, limit: 50, offset: 0 })
    expect(screen.getByText(/120명 중 1–50/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /다음/ }))
    await waitFor(() =>
      expect(accountApi.list).toHaveBeenLastCalledWith({ status: null, limit: 50, offset: 50 }),
    )
  })

  it('알림 링크의 ?status=pending 을 읽어 그 상태만 보인다', async () => {
    await open({ ...ACCOUNT, status: 'pending' }, { url: '/admin/accounts?status=pending' })
    expect(accountApi.list).toHaveBeenLastCalledWith({ status: 'pending', limit: 50, offset: 0 })
    expect(screen.getByRole('button', { name: '승인 대기' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )

    await userEvent.click(screen.getByRole('button', { name: '전체' }))
    await waitFor(() =>
      expect(accountApi.list).toHaveBeenLastCalledWith({ status: null, limit: 50, offset: 0 }),
    )
  })
})

describe('계정 관리 · 그 사람의 토큰', () => {
  beforeEach(() => vi.clearAllMocks())

  // 정지는 토큰을 그대로 둔다 — 계정을 지우지 않고 연동만 끊는 자리가 여기다.
  it('정지된 계정도 「토큰」 으로 그 사람의 토큰 목록을 연다', async () => {
    accountApi.tokens.mockResolvedValue([])
    await open({ ...ACCOUNT, status: 'suspended' })
    await userEvent.click(screen.getByRole('button', { name: '토큰' }))
    const dialog = await screen.findByRole('dialog', { name: /홍길동\(hong\) 의 액세스 토큰/ })
    expect(dialog).toBeInTheDocument()
    await waitFor(() => expect(accountApi.tokens).toHaveBeenCalledWith('acc-1'))
  })
})

describe('계정 관리 · 시스템 관리자 지정', () => {
  // 가짜는 시험마다 비운다 — 안 비우면 앞 시험의 호출이 뒤 시험의 「안 불렸다」 를 깬다.
  beforeEach(() => vi.clearAllMocks())

  it('누르면 바로 바뀌지 않고, 무엇이 열리는지 먼저 말한다', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: '관리자 지정' }))
    // **확인 전에는 아무 일도 없다.**
    expect(accountApi.setSystemAdmin).not.toHaveBeenCalled()
    expect(screen.getByText(/시스템 관리자로 지정합니다/)).toBeInTheDocument()
    expect(screen.getByText(/모든 부서의 자료/)).toBeInTheDocument()

    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: '관리자 지정' }))
    await waitFor(() => expect(accountApi.setSystemAdmin).toHaveBeenCalledWith('acc-1', true))
  })

  it('해제는 무엇을 잃는지 말하고, 마지막 관리자는 서버가 막는다고 적는다', async () => {
    await open({ ...ACCOUNT, is_system_admin: true })
    await userEvent.click(screen.getByRole('button', { name: '관리자 해제' }))
    expect(screen.getByText(/마지막 관리자는 해제되지 않습니다/)).toBeInTheDocument()
    expect(accountApi.setSystemAdmin).not.toHaveBeenCalled()
  })

  it('창을 닫으면 그대로다 — 잘못 누른 것을 되돌릴 자리가 있다', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: '관리자 지정' }))
    await userEvent.click(screen.getByRole('button', { name: '취소' }))
    expect(accountApi.setSystemAdmin).not.toHaveBeenCalled()
  })
})

describe('계정 관리 · 희망 부서 없는 신청의 승인', () => {
  beforeEach(() => vi.clearAllMocks())

  const PENDING = {
    ...ACCOUNT,
    status: 'pending',
    home_workspace_slug: null,
    home_workspace_name: null,
    memberships: [],
    workspaces: [],
  }

  // HWAX 포털로 들어온 신청은 부서를 짐작하지 않는다(ADR 0028) — 그대로 승인하면 서버가 「부서를
  // 골라 주세요」 로 거절하는데, 고를 자리가 없으면 관리자는 그 신청을 처리할 수 없다.
  it('부서를 묻고, 고른 부서로 승인한다', async () => {
    accountApi.approve.mockResolvedValue({ ...PENDING, status: 'active' })
    await open(PENDING)
    expect(screen.getByText('희망 부서 없음 — 승인할 때 선택')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '승인' }))
    expect(accountApi.approve).not.toHaveBeenCalled()
    const dialog = await screen.findByRole('dialog', { name: '가입 승인 — 부서 선택' })
    expect(within(dialog).getByRole('button', { name: '승인' })).toBeDisabled()

    await userEvent.click(within(dialog).getByRole('combobox'))
    await userEvent.click(await screen.findByRole('button', { name: /본사/ }))
    await userEvent.click(within(dialog).getByRole('button', { name: '승인' }))
    await waitFor(() =>
      expect(accountApi.approve).toHaveBeenCalledWith('acc-1', { workspace_slug: 'hq' }),
    )
  })

  it('희망 부서가 있으면 묻지 않고 그 부서로 승인한다', async () => {
    accountApi.approve.mockResolvedValue({ ...PENDING, status: 'active' })
    await open({ ...PENDING, requested_workspace_slug: 'hq', requested_workspace_name: '본사' })
    await userEvent.click(screen.getByRole('button', { name: '승인' }))
    await waitFor(() => expect(accountApi.approve).toHaveBeenCalledWith('acc-1', {}))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})
