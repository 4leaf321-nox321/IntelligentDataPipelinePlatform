/**
 * 연결 지도 — 서버가 준 마디를 그리고, 살펴볼 것을 먼저 말하고, 거르기는 그 길만 남긴다.
 *
 * 흐름도(React Flow)를 흉내 내지 않고 그대로 띄운다 — 마디에 크기를 주므로 재기 전에도 그려진다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { sampleMap } from '@/modules/relay/fixtures'

const relayApi = vi.hoisted(() => ({ map: vi.fn() }))
vi.mock('@/modules/relay/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/relay/api')>()),
  relayApi,
}))

async function mount() {
  const { default: RelayMapPage } = await import('@/modules/relay/RelayMapPage')
  render(
    <MemoryRouter initialEntries={['/relay/map']}>
      <RelayMapPage />
    </MemoryRouter>,
  )
}

describe('연결 지도', () => {
  beforeEach(() => vi.clearAllMocks())

  it('원천부터 그 너머까지 마디를 그리고, 살펴볼 것을 먼저 말한다', async () => {
    relayApi.map.mockResolvedValue(sampleMap())
    await mount()

    expect(await screen.findByText('plm-tasks')).toBeInTheDocument()
    // 거르기의 고를 것에도 같은 이름이 있다 — 그림 안에서 찾는다.
    const flow = within(document.querySelector<HTMLElement>('.react-flow')!)
    for (const label of ['PLM', 'MES', 'GET /tasks · plm.local', '자주검증 Datahub', 'plm_model', '리포트 아카이브']) {
      // 대상 이름은 그 타입 마디의 작은 글에도 선다.
      expect(flow.getAllByText(label).length).toBeGreaterThan(0)
    }
    // 열 제목이 그림 안에 선다.
    expect(flow.getByText('대상 SP')).toBeInTheDocument()
    expect(flow.getByText('그 너머')).toBeInTheDocument()
    // 상태는 배지로 — 닿지 않는 대상 · 실패한 파이프라인.
    expect(flow.getByText('닿지 않음')).toBeInTheDocument()
    expect(flow.getByText('실패')).toBeInTheDocument()
    expect(screen.getByText('제조 데이터허브(http://mfg)에 닿지 않습니다.')).toBeInTheDocument()

    // 파이프라인은 상세로, 적용 대기는 대상 SP 의 계획으로(새 탭).
    expect(screen.getByText('plm-models').closest('a')).toHaveAttribute('href', '/relay/pipelines/plm-models')
    const plan = screen.getByText('계획 보기').closest('a')
    expect(plan).toHaveAttribute('href', 'http://hub/jobs?job=j-1')
    expect(plan).toHaveAttribute('target', '_blank')
    expect(screen.getAllByText('계획 보기')).toHaveLength(1)
  })

  it('원천으로 거르면 다른 원천의 길이 사라진다', async () => {
    relayApi.map.mockResolvedValue(sampleMap())
    await mount()
    await screen.findByText('plm-tasks')

    await userEvent.selectOptions(screen.getByLabelText('원천 시스템'), 'system:mes')
    await waitFor(() => expect(screen.queryByText('plm-tasks')).not.toBeInTheDocument())
    expect(screen.getByText('mes-lots')).toBeInTheDocument()
    expect(screen.queryByText('리포트 아카이브')).not.toBeInTheDocument()
    expect(screen.getByText(/파이프라인 1 \/ 3/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '거르기 풀기' }))
    expect(await screen.findByText('plm-tasks')).toBeInTheDocument()
  })

  it('파이프라인이 없으면 정의를 어디 두는지 말한다', async () => {
    relayApi.map.mockResolvedValue({ generated_at: '', nodes: [], edges: [], problems: [] })
    await mount()
    expect(await screen.findByText('파이프라인이 없습니다')).toBeInTheDocument()
    expect(screen.getByText('RELAY_PIPELINES_DIR')).toBeInTheDocument()
  })
})
