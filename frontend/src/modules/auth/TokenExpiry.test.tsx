/**
 * 토큰 발급 — **만료를 적을 자리.**
 *
 * 연동이 끝난 뒤에도 살아 있는 자격이 가장 오래 남는 구멍이다. 칸이 없으면 아무도 안 적는다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() }))
vi.mock('@/shared/api/client', () => ({ api: client }))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { display_name: '홍길동', email: 'hong' }, refresh: vi.fn() }),
}))

async function open() {
  client.get.mockImplementation((path: string) => {
    if (path.includes('token-scopes')) return Promise.resolve({ scopes: ['read', 'core:read'] })
    return Promise.resolve([])
  })
  client.post.mockResolvedValue({ token: 'standardplatform_pat_xxx' })
  const { default: ProfilePage } = await import('@/modules/auth/ProfilePage')
  render(<ProfilePage />)
}

describe('액세스 토큰 발급', () => {
  it('만료 일수를 적으면 그대로 간다', async () => {
    await open()
    await userEvent.type(
      await screen.findByPlaceholderText(/토큰 용도/),
      'MatNexus 야간 동기화',
    )
    await userEvent.type(screen.getByLabelText('만료까지 일수'), '90')
    await userEvent.click(screen.getByRole('button', { name: '발급' }))

    await waitFor(() =>
      expect(client.post).toHaveBeenCalledWith(
        '/auth/tokens',
        expect.objectContaining({ name: 'MatNexus 야간 동기화', expires_in_days: 90 }),
      ),
    )
  })

  it('비우면 만료 없이 — 0 을 보내지 않는다', async () => {
    await open()
    await userEvent.type(await screen.findByPlaceholderText(/토큰 용도/), '정제 스크립트')
    await userEvent.click(screen.getByRole('button', { name: '발급' }))

    await waitFor(() =>
      expect(client.post).toHaveBeenCalledWith(
        '/auth/tokens',
        expect.objectContaining({ expires_in_days: null }),
      ),
    )
  })
})
