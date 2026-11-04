/**
 * 알림 화면 — **한 건씩도 읽고, 실패하면 말하고, 배지가 곧바로 따라온다.**
 *
 * 한 건 읽음(`/notifications/{id}/read`)을 안 부르던 때는 「보러 가기」 로 가도 안 읽은 채였고,
 * 「모두 읽음」 뒤 배지가 최대 60초 그대로였으며, 실패는 삼켜졌다(2026-10-08).
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('@/shared/api/client', async (original) => ({
  ...(await original<typeof import('@/shared/api/client')>()),
  api: { get: client.get, post: client.post },
}))

const ROWS = [
  {
    id: 'n1',
    kind: 'signup',
    title: '가입 신청 1건',
    body: null,
    link: '/admin/accounts?status=pending',
    read_at: null,
    created_at: '2026-10-08T00:00:00Z',
  },
  {
    id: 'n2',
    kind: 'calibration',
    title: '교정 만료',
    body: null,
    link: null,
    read_at: null,
    created_at: '2026-10-07T00:00:00Z',
  },
]

async function open() {
  const { default: NotificationsPage } = await import(
    '@/modules/notifications/NotificationsPage'
  )
  const { NotificationBell } = await import('@/shared/layout/NotificationBell')
  render(
    <MemoryRouter>
      <NotificationBell />
      <NotificationsPage />
    </MemoryRouter>,
  )
  await screen.findByText('교정 만료')
}

describe('알림', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    client.get.mockImplementation(async (path: string) =>
      path === '/notifications/unread-count' ? { unread: 2 } : ROWS,
    )
    client.post.mockResolvedValue({})
  })

  it('「보러 가기」 로 가면 그 한 건을 읽음으로 하고, 배지가 곧바로 다시 센다', async () => {
    await open()
    const counted = client.get.mock.calls.filter(([path]) => path === '/notifications/unread-count')
    const before = counted.length
    await userEvent.click(screen.getByRole('link', { name: '보러 가기' }))
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/notifications/n1/read'))
    await waitFor(() =>
      expect(
        client.get.mock.calls.filter(([path]) => path === '/notifications/unread-count').length,
      ).toBeGreaterThan(before),
    )
  })

  it('한 건씩도 읽음으로 할 수 있다', async () => {
    await open()
    const [, second] = screen.getAllByRole('button', { name: '읽음' })
    await userEvent.click(second)
    await waitFor(() => expect(client.post).toHaveBeenCalledWith('/notifications/n2/read'))
  })

  it('「모두 읽음」 이 실패하면 그 이유가 보인다', async () => {
    await open()
    client.post.mockRejectedValueOnce(new Error('서버가 잠시 응답하지 않습니다'))
    await userEvent.click(screen.getByRole('button', { name: '모두 읽음' }))
    expect(await screen.findByText(/서버가 잠시 응답하지 않습니다/)).toBeInTheDocument()
  })
})
