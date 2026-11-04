/**
 * 소속 변경에서 지키는 것 — **이름으로 보이고, 통째로 보내고, 대표 소속이 비지 않는다.**
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const accountApi = vi.hoisted(() => ({ setWorkspaces: vi.fn() }))
vi.mock('@/modules/accounts/api', () => ({ accountApi }))

const ACCOUNT = {
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

const OPTIONS = [
  { slug: 'hq', name: '본사', path: '본사', depth: 0 },
  { slug: 'lab', name: '재료시험팀', path: '개발본부 / 재료시험팀', depth: 1 },
]

async function open() {
  accountApi.setWorkspaces.mockResolvedValue(ACCOUNT)
  const { AccountWorkspacesDialog } = await import('@/modules/accounts/AccountWorkspacesDialog')
  const onSaved = vi.fn()
  render(
    <AccountWorkspacesDialog
      account={ACCOUNT}
      options={OPTIONS}
      onClose={vi.fn()}
      onSaved={onSaved}
    />,
  )
  return onSaved
}

describe('계정의 소속 변경', () => {
  it('slug 가 아니라 이름으로 보여 준다', async () => {
    await open()
    // **「hq」 라고 쓰면 사람은 그것이 자기 부서인지 모른다.**
    expect(screen.getAllByText('본사').length).toBeGreaterThan(0)
    expect(screen.queryByText('hq')).toBeNull()
  })

  it('부서를 더하고 빼서 통째로 보낸다', async () => {
    const onSaved = await open()
    await userEvent.click(screen.getByRole('combobox'))
    await userEvent.click(await screen.findByRole('button', { name: /재료시험팀/ }))
    await userEvent.click(screen.getByRole('button', { name: '본사 소속 빼기' }))
    await userEvent.click(screen.getByRole('button', { name: '저장' }))

    await waitFor(() =>
      expect(accountApi.setWorkspaces).toHaveBeenCalledWith('acc-1', {
        workspace_slugs: ['lab'],
        // 대표 소속을 뺐으면 남은 부서로 옮겨 간다 — 비워 보내면 서버가 정하고, 화면과 어긋난다.
        home_workspace_slug: 'lab',
      }),
    )
    expect(onSaved).toHaveBeenCalled()
  })

  it('소속을 다 빼면 저장할 수 없다', async () => {
    await open()
    await userEvent.click(screen.getByRole('button', { name: '본사 소속 빼기' }))
    expect(screen.getByRole('button', { name: '저장' })).toBeDisabled()
    expect(screen.getByText(/소속 없는 계정은/)).toBeInTheDocument()
  })
})
