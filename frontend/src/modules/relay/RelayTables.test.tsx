/**
 * 파이프라인 · 원천 시스템 · 대상 인스턴스 표 — 지도와 같은 응답에서 같은 길을 읽는다.
 */

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { sampleMap, withDraft } from '@/modules/relay/fixtures'

const relayApi = vi.hoisted(() => ({
  map: vi.fn(),
  apply: vi.fn(),
  alertWebhook: vi.fn(),
  alertWebhookTest: vi.fn(),
}))
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

async function show(page: keyof typeof PAGES, entry = '/') {
  const { default: Page } = await PAGES[page]()
  render(
    <MemoryRouter initialEntries={[entry]}>
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
    relayApi.alertWebhook.mockResolvedValue({ configured: false, host: '', last_error: '' })
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
    expect(within(rowOf('plm-tasks')).getByTestId('relay-drift')).toHaveTextContent('불일치3건')
  })

  it('파이프라인 — 초안은 회색 배지, 마지막 실행 · 적용은 없다', async () => {
    relayApi.map.mockResolvedValue(withDraft(sampleMap()))
    await show('PipelinesPage', '/?view=all')
    const row = within((await screen.findByRole('link', { name: 'plm-parts' })).closest('tr')!)
    const badge = row.getByText('초안(미사용)')
    expect(badge).toHaveClass('bg-muted')
    // 마지막 실행 · 마지막 적용 모두 없다.
    expect(row.getAllByText('—')).toHaveLength(2)
  })

  it('파이프라인 — 확인 필요가 기본 보기이고, 전체 · 상태 칩 · 검색으로 좁힌다', async () => {
    relayApi.map.mockResolvedValue(withDraft(sampleMap()))
    await show('PipelinesPage')
    // 실패 · 적용 대기 · 대상 불일치가 확인 필요 — 초안은 [전체]에서.
    const attention = await screen.findByRole('button', { name: /확인 필요 3/ })
    expect(attention).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('link', { name: 'plm-parts' })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'mes-lots' })).toBeInTheDocument()
    // 문제 우선 — 실패가 맨 위.
    expect(within(screen.getAllByRole('row')[1]).getByRole('link')).toHaveTextContent('mes-lots')

    await userEvent.click(screen.getByRole('button', { name: /전체 4/ }))
    expect(await screen.findByRole('link', { name: 'plm-parts' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /실패\s*1/ }))
    expect(screen.getAllByRole('row')).toHaveLength(2)
    expect(screen.getByRole('link', { name: 'mes-lots' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /전체 4/ }))
    await userEvent.type(screen.getByLabelText('파이프라인 검색'), 'models')
    expect(screen.getAllByRole('row')).toHaveLength(2)
    expect(screen.getByRole('link', { name: 'plm-models' })).toBeInTheDocument()
  })

  it('파이프라인 — 확인 필요가 없으면 그렇다고 말한다', async () => {
    await show('PipelinesPage', '/?view=attention&q=없는이름')
    expect(await screen.findByText('조건에 맞는 파이프라인 없음')).toBeInTheDocument()
  })

  it('파이프라인 — 적용 대기 계획을 골라 일괄 적용한다(시스템 관리자만)', async () => {
    relayApi.apply.mockResolvedValue({ job_id: 'job-9' })
    await show('PipelinesPage')
    await userEvent.click(await screen.findByLabelText('plm-models 선택'))
    // 실패한 파이프라인은 고를 수 없다 — 적용할 계획이 없다.
    expect(screen.queryByLabelText('mes-lots 선택')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '선택한 계획 적용(1)' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('plm-models')).toBeInTheDocument()
    expect(within(dialog).getByRole('link', { name: /계획 보기/ })).toHaveAttribute(
      'href',
      'http://hub/jobs?job=j-1',
    )
    await userEvent.click(within(dialog).getByRole('button', { name: '1개 적용' }))
    expect(relayApi.apply).toHaveBeenCalledWith('plm-models', 'j-1')
    expect(await screen.findByText(/적용 작업 1건을 추가했습니다/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '작업 확인' })).toHaveAttribute('href', '/jobs?job=job-9')
  })

  it('파이프라인 — 시스템 관리자가 아니면 일괄 적용을 표시하지 않는다', async () => {
    auth.admin = false
    await show('PipelinesPage')
    await screen.findByRole('link', { name: 'plm-models' })
    expect(screen.queryByLabelText('plm-models 선택')).not.toBeInTheDocument()
  })

  it('파이프라인 — 알림 웹훅의 호스트 · 마지막 전송 결과와 시험 전송(주소 전체는 표시하지 않는다)', async () => {
    relayApi.alertWebhook.mockResolvedValue({
      configured: true,
      host: 'chat.corp.example',
      last_at: '2026-11-25T00:00:00+00:00',
      last_ok: false,
      last_error: 'HTTP 404',
    })
    relayApi.alertWebhookTest.mockResolvedValue({ ok: true, message: 'chat.corp.example(으)로 시험 메시지를 보냈습니다.' })
    await show('PipelinesPage')
    expect(await screen.findByText('chat.corp.example')).toBeInTheDocument()
    expect(screen.getByText(/실패\(HTTP 404\)/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /시험 전송/ }))
    expect(relayApi.alertWebhookTest).toHaveBeenCalled()
    expect(await screen.findByText(/시험 메시지를 보냈습니다/)).toBeInTheDocument()
  })

  it('파이프라인 — 시스템 관리자가 아니면 알림 웹훅을 표시하지 않는다', async () => {
    auth.admin = false
    await show('PipelinesPage')
    await screen.findByRole('link', { name: 'plm-models' })
    expect(screen.queryByText('알림 웹훅')).not.toBeInTheDocument()
    expect(relayApi.alertWebhook).not.toHaveBeenCalled()
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
    expect(mfg.getByText('연결 불가')).toBeInTheDocument()
    // 닿지 않으면 그 너머를 모른다 — 「없음」 이라고 하지 않는다.
    expect(mfg.getByText('확인 불가')).toBeInTheDocument()
  })
})
