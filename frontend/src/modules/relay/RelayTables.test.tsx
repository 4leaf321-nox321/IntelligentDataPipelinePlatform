/**
 * 파이프라인 · 원천 시스템 · 대상 인스턴스 표 — 지도와 같은 응답에서 같은 길을 읽는다.
 */

import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { sampleMap, withDraft } from '@/modules/relay/fixtures'

const relayApi = vi.hoisted(() => ({ map: vi.fn() }))
const auth = vi.hoisted(() => ({ admin: true }))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', is_system_admin: auth.admin, memberships: [] } }),
}))
vi.mock('@/modules/relay/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/relay/api')>()),
  relayApi,
}))

const PAGES = {
  PipelinesPage: () => import('@/modules/relay/PipelinesPage'),
  SourcesPage: () => import('@/modules/relay/SourcesPage'),
  TargetsPage: () => import('@/modules/relay/TargetsPage'),
}

async function show(page: keyof typeof PAGES) {
  const { default: Page } = await PAGES[page]()
  render(
    <MemoryRouter>
      <Page />
    </MemoryRouter>,
  )
}

const rowOf = (text: string) => screen.getByText(text).closest('tr')!

describe('중계 표', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
    relayApi.map.mockResolvedValue(sampleMap())
  })

  it('파이프라인 — 시스템 관리자에게만 「새 연결」', async () => {
    await show('PipelinesPage')
    expect(await screen.findByRole('link', { name: /새 연결/ })).toHaveAttribute('href', '/relay/pipelines/new')
  })

  it('파이프라인 — 원천 · API · 대상 · 상태, 이름은 상세로', async () => {
    await show('PipelinesPage')
    const link = await screen.findByRole('link', { name: 'plm-models' })
    expect(link).toHaveAttribute('href', '/relay/pipelines/plm-models')
    const row = within(rowOf('plm-models'))
    expect(row.getByText('PLM')).toBeInTheDocument()
    expect(row.getByText('GET /models · plm.local')).toBeInTheDocument()
    expect(row.getByText('자주검증 Datahub')).toBeInTheDocument()
    expect(row.getByText('적용 대기')).toBeInTheDocument()
    expect(row.queryByTestId('relay-drift')).not.toBeInTheDocument()
    // 마지막 대조에서 어긋난 것은 상태 곁에 — 지도와 같은 응답 · 같은 배지.
    expect(within(rowOf('plm-tasks')).getByTestId('relay-drift')).toHaveTextContent('어긋남3건')
  })

  it('파이프라인 — 초안은 「초안(미사용)」 회색 배지, 마지막 적용은 없다', async () => {
    relayApi.map.mockResolvedValue(withDraft(sampleMap()))
    await show('PipelinesPage')
    const row = within((await screen.findByRole('link', { name: 'plm-parts' })).closest('tr')!)
    const badge = row.getByText('초안(미사용)')
    expect(badge).toHaveClass('bg-muted')
    expect(row.getByText('—')).toBeInTheDocument()
  })

  it('원천 시스템 — API 마다 읽는 파이프라인', async () => {
    await show('SourcesPage')
    const row = within((await screen.findByText('OData Lots · mes.local')).closest('tr')!)
    expect(row.getByRole('link', { name: 'mes-lots' })).toBeInTheDocument()
    expect(row.getByText('실패')).toBeInTheDocument()
  })

  it('대상 인스턴스 — 상태 · 타입 · 그 너머', async () => {
    await show('TargetsPage')
    const hub = within((await screen.findByText('datahub · v1.2')).closest('tr')!)
    expect(hub.getByText('연결됨')).toBeInTheDocument()
    expect(hub.getByText('plm_task, plm_project, plm_model')).toBeInTheDocument()
    expect(hub.getByText('리포트 아카이브')).toBeInTheDocument()
    const mfg = within(rowOf('제조 데이터허브'))
    expect(mfg.getByText('닿지 않음')).toBeInTheDocument()
    // 닿지 않으면 그 너머를 모른다 — 「없음」 이라고 하지 않는다.
    expect(mfg.getByText('모름')).toBeInTheDocument()
  })
})
