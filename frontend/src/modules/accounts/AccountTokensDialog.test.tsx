/**
 * 그 사람의 액세스 토큰 — **계정을 지우지 않고 연동 하나만 끊는다.**
 *
 * 예전에는 본인만 폐기할 수 있어서, 퇴사자 · 정지된 계정의 연동을 끊으려면 계정을 지워야
 * 했다(2026-10-08). 여기서 지키는 것: 목록이 상태(사용 · 만료 · 폐기)를 말하는가, 폐기는 무엇이
 * 끊기는지 적고 사유를 받는가, 서버가 거절하면(이미 폐기됨) 그 말이 창에 남는가.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@/shared/api/client'

const accountApi = vi.hoisted(() => ({ tokens: vi.fn(), revokeToken: vi.fn() }))
vi.mock('@/modules/accounts/api', () => ({ accountApi }))

const ACCOUNT = {
  id: 'acc-1',
  email: 'hong',
  display_name: '홍길동',
  status: 'suspended',
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

const LIVE = {
  id: 'pat-live',
  name: 'MatNexus 야간',
  prefix: 'sp_abc',
  scopes: ['core:read'],
  created_at: '2026-09-01T00:00:00Z',
  expires_at: null,
  last_used_at: '2026-10-07T01:00:00Z',
  revoked_at: null,
}
const EXPIRED = {
  ...LIVE,
  id: 'pat-old',
  name: '옛 스크립트',
  expires_at: '2026-01-01T00:00:00Z',
  last_used_at: null,
}
const REVOKED = { ...LIVE, id: 'pat-gone', name: '끊은 연동', revoked_at: '2026-10-01T00:00:00Z' }

async function open(tokens = [LIVE, EXPIRED, REVOKED]) {
  accountApi.tokens.mockResolvedValue(tokens)
  const { AccountTokensDialog } = await import('@/modules/accounts/AccountTokensDialog')
  const onClose = vi.fn()
  render(<AccountTokensDialog account={ACCOUNT} onClose={onClose} />)
  await waitFor(() => expect(screen.getByText('MatNexus 야간')).toBeInTheDocument())
  return { onClose }
}

function rowOf(name: string): HTMLElement {
  const row = screen.getByText(name).closest('tr')
  if (!row) throw new Error(`줄이 없다: ${name}`)
  return row
}

describe('계정 › 액세스 토큰', () => {
  beforeEach(() => vi.clearAllMocks())

  it('그 사람의 토큰을 상태와 함께 보이고, 폐기된 것에는 폐기 단추가 없다', async () => {
    await open()
    expect(accountApi.tokens).toHaveBeenCalledWith('acc-1')
    expect(within(rowOf('MatNexus 야간')).getByText('사용')).toBeInTheDocument()
    expect(within(rowOf('옛 스크립트')).getByText('만료')).toBeInTheDocument()
    expect(within(rowOf('옛 스크립트')).getByText('미사용')).toBeInTheDocument()
    expect(within(rowOf('끊은 연동')).getByText('폐기')).toBeInTheDocument()
    expect(within(rowOf('끊은 연동')).queryByRole('button', { name: '폐기' })).toBeNull()
    expect(within(rowOf('MatNexus 야간')).getByRole('button', { name: '폐기' })).toBeEnabled()
  })

  it('폐기는 무엇이 끊기는지 적고 사유를 받아 보낸 뒤 목록을 다시 읽는다', async () => {
    accountApi.revokeToken.mockResolvedValue({ ...LIVE, revoked_at: '2026-10-08T00:00:00Z' })
    await open()
    await userEvent.click(within(rowOf('MatNexus 야간')).getByRole('button', { name: '폐기' }))
    const dialog = await screen.findByRole('dialog', { name: /MatNexus 야간.*폐기/ })
    expect(within(dialog).getByText(/즉시 인증에 실패합니다/)).toBeInTheDocument()
    expect(within(dialog).getByText(/최종 사용/)).toBeInTheDocument()
    await userEvent.type(within(dialog).getByLabelText('폐기 사유'), '퇴사')
    await userEvent.click(within(dialog).getByRole('button', { name: '폐기' }))
    await waitFor(() =>
      expect(accountApi.revokeToken).toHaveBeenCalledWith('acc-1', 'pat-live', '퇴사'),
    )
    await waitFor(() => expect(accountApi.tokens).toHaveBeenCalledTimes(2))
  })

  it('서버가 거절하면(이미 폐기됨) 창을 닫지 않고 그 말을 보인다', async () => {
    accountApi.revokeToken.mockRejectedValue(
      new ApiError(409, {
        error: { code: 'APP-ACCOUNTS-0012', message: '이미 폐기된 토큰입니다' },
      }),
    )
    await open()
    await userEvent.click(within(rowOf('MatNexus 야간')).getByRole('button', { name: '폐기' }))
    const dialog = await screen.findByRole('dialog', { name: /폐기/ })
    await userEvent.click(within(dialog).getByRole('button', { name: '폐기' }))
    expect(await within(dialog).findByText('이미 폐기된 토큰입니다')).toBeInTheDocument()
  })

  it('토큰이 없으면 어디서 발급하는지 말한다', async () => {
    accountApi.tokens.mockResolvedValue([])
    const { AccountTokensDialog } = await import('@/modules/accounts/AccountTokensDialog')
    render(<AccountTokensDialog account={ACCOUNT} onClose={vi.fn()} />)
    expect(await screen.findByText('발급한 토큰이 없습니다')).toBeInTheDocument()
  })
})
