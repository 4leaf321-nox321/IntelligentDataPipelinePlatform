/**
 * 허용 주소 — 서버 설정 · 사용 중인 정의의 주소는 보이기만, 화면에서 더한 것만 고쳐 저장한다.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AllowedHostsOut } from '@/modules/relay/api'

const relayApi = vi.hoisted(() => ({ allowedHosts: vi.fn(), putAllowedHosts: vi.fn() }))
const auth = vi.hoisted(() => ({ admin: true }))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', is_system_admin: auth.admin, memberships: [] } }),
}))
vi.mock('@/modules/relay/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/relay/api')>()),
  relayApi,
}))

const LISTED: AllowedHostsOut = {
  source: { settings: ['plm-odata.corp.local'], screen: ['mes-api.corp.local'], definitions: [] },
  target: { settings: [], screen: [], definitions: ['jvhub.corp.local:8040'] },
  updated_at: '2026-11-19T01:00:00+00:00',
  updated_by: 'ops@corp.local',
}

async function show() {
  const { default: Page } = await import('@/modules/relay/AllowedHostsPage')
  render(
    <MemoryRouter>
      <Page />
    </MemoryRouter>,
  )
}

describe('허용 주소', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
    relayApi.allowedHosts.mockResolvedValue(LISTED)
    relayApi.putAllowedHosts.mockResolvedValue(LISTED)
  })

  it('설정 · 정의의 주소는 보이기만, 화면에서 더한 것을 고쳐 저장한다', async () => {
    await show()
    const source = within(await screen.findByTestId('allowed-source'))
    expect(source.getByText('plm-odata.corp.local')).toBeInTheDocument()
    const target = within(screen.getByTestId('allowed-target'))
    expect(target.getByText('jvhub.corp.local:8040')).toBeInTheDocument()
    const field = target.getByRole('textbox')
    fireEvent.change(field, { target: { value: 'mfghub.corp.local:8040\n\n, other:1' } })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() =>
      expect(relayApi.putAllowedHosts).toHaveBeenCalledWith(
        ['mes-api.corp.local'],
        ['mfghub.corp.local:8040', 'other:1'],
      ),
    )
    expect(await screen.findByText(/지금부터 사용됩니다/)).toBeInTheDocument()
  })

  it('관리자가 아니면 고치지 못한다', async () => {
    auth.admin = false
    await show()
    const target = within(await screen.findByTestId('allowed-target'))
    expect(target.getByRole('textbox')).toBeDisabled()
    expect(screen.queryByRole('button', { name: '저장' })).not.toBeInTheDocument()
  })
})
