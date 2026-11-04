/**
 * 플랫폼 자기소개(ADR 0019) — **한 번 쓰고 끝나는 글이 아니다.** 사람이 쓴 뒤 타입 · 데이터
 * 소스가 바뀌면 화면이 그렇게 말하고, 확인하고 저장하면 그 표시가 사라진다. 「지금 담긴 것」 은
 * 조회할 때마다 센 것이라 사람이 옮겨 적지 않는다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }))
vi.mock('@/shared/api/client', () => ({ api: client }))

import { PlatformProfile } from '@/modules/server/PlatformProfile'
import type { PlatformProfileLive } from '@/modules/server/PlatformProfile'

function live(extra: Partial<PlatformProfileLive> = {}): PlatformProfileLive {
  return {
    slug: 'caedatahub',
    name: 'CAE 그룹 Datahub',
    tagline: '',
    summary: '해석 기록',
    notes: '',
    updated_at: '2026-10-01T00:00:00Z',
    facts: [{ key: 'types', label: '담긴 것', lines: ['기록 — 보고서 1.2만'] }],
    stale: [],
    ...extra,
  }
}

describe('플랫폼 자기소개', () => {
  beforeEach(() => {
    client.get.mockReset()
    client.put.mockReset()
  })

  it('저장한 뒤 바뀐 것을 말하고, 저장하면 다시 받아 온다', async () => {
    client.get
      .mockResolvedValueOnce(live({ stale: ['생김: 타입 「보고서」'] }))
      .mockResolvedValueOnce(live())
    client.put.mockResolvedValue({})
    render(<PlatformProfile />)
    expect(await screen.findByText('생김: 타입 「보고서」')).toBeInTheDocument()
    expect(screen.getByText(/기록 — 보고서 1.2만/)).toBeInTheDocument()

    const summary = screen.getByLabelText(/담는 것/)
    await userEvent.clear(summary)
    await userEvent.type(summary, '해석 · 보고서 기록')
    await userEvent.click(screen.getByRole('button', { name: '저장' }))
    expect(client.put).toHaveBeenCalledWith('/server/profile', {
      summary: '해석 · 보고서 기록',
      notes: '',
    })
    await waitFor(() => expect(screen.queryByText('생김: 타입 「보고서」')).toBeNull())
  })

  it('아직 소개가 없으면 그렇게 말하고, 너무 길면 저장할 수 없다', async () => {
    client.get.mockResolvedValue(live({ summary: '', stale: ['자기소개를 아직 안 적었다'] }))
    render(<PlatformProfile />)
    expect(await screen.findByText(/아직 소개가 없습니다/)).toBeInTheDocument()
    // 「낡음」 목록이 아니라 「없음」 으로 — 같은 줄을 두 번 말하지 않는다.
    expect(screen.queryByText('자기소개를 아직 안 적었다')).toBeNull()
    await userEvent.type(screen.getByLabelText(/다른 플랫폼과의 사이/), 'x')
    expect(screen.getByRole('button', { name: '저장' })).toBeEnabled()
    await userEvent.click(screen.getByLabelText(/담는 것/))
    await userEvent.paste('가'.repeat(301))
    expect(screen.getByRole('button', { name: '저장' })).toBeDisabled()
  })
})
