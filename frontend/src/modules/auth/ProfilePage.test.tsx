/**
 * 내 정보의 토큰 폐기 — **실패하면 말한다.**
 *
 * 삼키던 때는 눌러도 아무 일이 없어, 사람은 끊었다고 믿고 그 토큰은 살아 있었다(2026-10-08).
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn(), delete: vi.fn() }))
vi.mock('@/shared/api/client', async (original) => ({
  ...(await original<typeof import('@/shared/api/client')>()),
  api: { get: client.get, delete: client.delete },
}))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { email: 'hong', display_name: '홍길동' }, reload: vi.fn() }),
}))
vi.mock('@/modules/auth/McpSetupGuide', () => ({ McpSetupGuide: () => null }))
vi.mock('@/modules/auth/PipelineKitGuide', () => ({ PipelineKitGuide: () => null }))

describe('토큰 폐기', () => {
  it('서버가 거절하면 그 이유가 보인다', async () => {
    client.get.mockImplementation(async (path: string) =>
      path === '/auth/token-scopes'
        ? { scopes: ['read'] }
        : [
            {
              id: 't1',
              name: '정제 스크립트',
              prefix: 'sp_abc',
              scopes: ['read'],
              created_at: '2026-10-01T00:00:00Z',
              expires_at: null,
              last_used_at: null,
              revoked_at: null,
            },
          ],
    )
    client.delete.mockRejectedValue(new Error('토큰을 폐기하지 못했습니다'))
    const { default: ProfilePage } = await import('@/modules/auth/ProfilePage')
    render(<ProfilePage />)
    await userEvent.click(await screen.findByRole('button', { name: '폐기' }))
    expect(await screen.findByText('토큰을 폐기하지 못했습니다')).toBeInTheDocument()
  })
})
