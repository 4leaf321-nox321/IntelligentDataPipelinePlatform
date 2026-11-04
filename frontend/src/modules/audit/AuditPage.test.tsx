/**
 * 변경 이력 — **감사 기록은 다 `{before, after}` 꼴이 아니다.** 홈 게시(`split: null`) 하나에서
 * `null.before` 로 화면 전체가 죽었다(2026-10-08). 꼴이 다른 기록도 내용을 잃지 않고 선다.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/shared/api/client', () => ({ api: client }))

import AuditPage from '@/modules/audit/AuditPage'

function entry(id: string, action: string, changes: Record<string, unknown>) {
  return {
    id,
    action,
    actor_label: '관리자',
    actor_client: null,
    target_table: 'metric_defs',
    target_id: null,
    target_label: 'm1',
    changes,
    reason: null,
    request_id: null,
    created_at: '2026-10-08T09:00:00+09:00',
  }
}

describe('변경 이력', () => {
  it('꼴이 다른 기록과 null 값도 그린다', async () => {
    client.get.mockResolvedValue({
      items: [
        entry('a', 'metric.home', { on_home: true, split: null, home_order: 2 }),
        entry('b', 'object.update', { label: { before: '가', after: '나' } }),
        entry('c', 'object.years', { years: [2025, 2026] }),
      ],
      total: 3,
    })
    render(<AuditPage />)
    expect(await screen.findByText('on_home: true, split: —, home_order: 2')).toBeInTheDocument()
    expect(screen.getByText('label: 가 -> 나')).toBeInTheDocument()
    expect(screen.getByText('years: [2025,2026]')).toBeInTheDocument()
  })
})
