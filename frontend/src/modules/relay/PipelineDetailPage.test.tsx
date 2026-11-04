/**
 * 파이프라인 상세 — 칸 대응 표가 대상에 없는 칸을 빨갛게 말하고, 실행 · 적용 · 레시피 판은 시스템
 * 관리자에게만 선다. 넣은 작업은 작업 화면으로 가는 링크를 남긴다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PipelineDetail, RecipeTrial, RecipeVersion } from '@/modules/relay/api'

const relayApi = vi.hoisted(() => ({
  pipeline: vi.fn(),
  run: vi.fn(),
  apply: vi.fn(),
  recipes: vi.fn(),
  recipe: vi.fn(),
  testRecipe: vi.fn(),
  activateRecipe: vi.fn(),
}))
const auth = vi.hoisted(() => ({ admin: true }))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', is_system_admin: auth.admin, memberships: [] } }),
}))
vi.mock('@/modules/relay/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/relay/api')>()),
  relayApi,
}))

function detail(over: Partial<PipelineDetail> = {}): PipelineDetail {
  return {
    slug: 'plm-tasks',
    system: 'PLM',
    source: 'plm',
    adapter_kind: 'rest',
    endpoint: 'http://plm.local/tasks',
    endpoint_label: 'GET /tasks · plm.local',
    target_name: '자주검증 Datahub',
    target_server: 'http://hub',
    target_token_env: 'HUB_TOKEN',
    definition_path: '/etc/datarelay/pipelines/plm-tasks.json',
    recipe_path: '/etc/datarelay/pipelines/plm-tasks.recipe.json',
    recipe_label: '파일',
    types: ['plm_task'],
    relations: ['belongs_to'],
    status: 'pending',
    status_label: '적용 대기',
    problems: [],
    lineage: [
      {
        target_type: 'plm_task',
        target_field: 'key',
        target_label: '',
        target_data_type: '',
        target_missing: false,
        columns: ['code'],
        source_paths: ['task.code'],
        transform: '대문자',
      },
      {
        target_type: 'plm_task',
        target_field: 'due',
        target_label: '마감일',
        target_data_type: 'date',
        target_missing: false,
        columns: ['due'],
        source_paths: ['task.dueDate'],
        transform: '날짜',
      },
      {
        target_type: 'plm_task',
        target_field: 'owner_dept',
        target_label: '',
        target_data_type: '',
        target_missing: true,
        columns: ['dept'],
        source_paths: ['task.owner.dept'],
        transform: '값 대응 3개',
      },
    ],
    state: {
      watermark: '2026-11-04T09:00:00Z',
      applied_at: '2026-11-03T09:00:00Z',
      pending: { job_id: 'plan-1', ok: true, summary: '새 3 · 고침 1', at: '2026-11-04T09:01:00Z' },
    },
    history: [
      {
        at: '2026-11-04T09:01:00Z',
        event: 'planned',
        job_id: 'plan-1',
        ok: true,
        counts: { objects: { plm_task: { created: 3, changed: 1, unchanged: 5, gone: 2 } } },
      },
    ],
    jobs: [
      {
        id: 'job-1',
        kind: 'relay_run',
        status: 'done',
        created_at: '2026-11-04T09:00:00Z',
        finished_at: '2026-11-04T09:01:00Z',
        summary: '계획을 올렸습니다',
        error: null,
      },
    ],
    target_schema_note: '',
    ...over,
  }
}

const VERSIONS: RecipeVersion[] = [
  { version: 2, status: 'draft', note: '마감일 칸 추가', client: 'mcp', created_at: '2026-11-04T10:00:00Z', activated_at: null },
  { version: 1, status: 'active', note: '처음', client: '화면', created_at: '2026-11-01T10:00:00Z', activated_at: '2026-11-01T11:00:00Z' },
]

const TRIAL: RecipeTrial = {
  version: 2,
  clean: true,
  problems: [],
  rows: { plm_task: 9 },
  unresolved_count: 0,
  unresolved: [],
  would_send: {
    objects: {
      plm_task: { created: 3, changed: 1, unchanged: 5, gone: 0 },
      plm_project: { created: 0, changed: 0, unchanged: 4, gone: 0 },
    },
    relations: {},
  },
  against: '지난번 적용된 출력',
  report: '타입 plm_task: 9행',
}

async function mount() {
  const { default: PipelineDetailPage } = await import('@/modules/relay/PipelineDetailPage')
  render(
    <MemoryRouter initialEntries={['/relay/pipelines/plm-tasks']}>
      <Routes>
        <Route path="/relay/pipelines/:slug" element={<PipelineDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
  await screen.findByText('칸 대응 표')
}

describe('파이프라인 상세', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
    relayApi.pipeline.mockResolvedValue(detail())
    relayApi.recipes.mockResolvedValue(VERSIONS)
  })

  it('칸 대응 표 — 대상 SP 정의에 없는 칸은 빨갛게, 식별자 줄은 따로 보인다', async () => {
    auth.admin = false
    await mount()

    expect(screen.getByText('task.dueDate')).toBeInTheDocument()
    expect(screen.getByText('마감일 · date')).toBeInTheDocument()
    expect(screen.getByText('식별자')).toBeInTheDocument()
    const missing = screen.getByText('owner_dept').closest('tr')!
    expect(missing).toHaveAttribute('data-missing', 'true')
    expect(within(missing).getByText('대상 SP 정의에 없는 칸')).toBeInTheDocument()
    expect(screen.getByText('대상 SP 정의에 없는 칸 1개')).toBeInTheDocument()

    // 비밀은 안 나온다 — 환경 변수 이름만.
    expect(screen.getByText('HUB_TOKEN')).toBeInTheDocument()
    // 이력의 건수 한 줄 · 작업 화면 링크 · 대상 SP 의 계획 링크.
    expect(screen.getByText('새 3 · 바뀜 1 · 무덤 2')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '작업 화면' })).toHaveAttribute('href', '/jobs?job=job-1')
    expect(screen.getByRole('link', { name: /대상 SP 에서 계획 보기/ })).toHaveAttribute(
      'href',
      'http://hub/jobs?job=plan-1',
    )
  })

  it('시스템 관리자가 아니면 실행 · 적용 · 레시피 판이 안 선다', async () => {
    auth.admin = false
    await mount()
    expect(screen.queryByRole('button', { name: /지금 실행/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /적용/ })).not.toBeInTheDocument()
    expect(screen.queryByText('레시피 판')).not.toBeInTheDocument()
    expect(relayApi.recipes).not.toHaveBeenCalled()
  })

  it('지금 실행 — 덮기를 고르면 overwrite 로 넣고, 작업 화면 링크를 남기고, 다시 읽는다', async () => {
    relayApi.run.mockResolvedValue({ job_id: 'job-9', kind: 'relay_run' })
    await mount()

    await userEvent.click(screen.getByLabelText(/사람이 고친 칸도 PLM 값으로 덮기\(overwrite\)/))
    await userEvent.click(screen.getByRole('button', { name: /지금 실행/ }))

    await waitFor(() => expect(relayApi.run).toHaveBeenCalledWith('plm-tasks', 'overwrite'))
    expect(await screen.findByText('실행 작업을 넣었습니다')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '작업 화면에서 진행 보기' })).toHaveAttribute(
      'href',
      '/jobs?job=job-9',
    )
    await waitFor(() => expect(relayApi.pipeline).toHaveBeenCalledTimes(2))
  })

  it('적용은 오류 없는 계획이 기다릴 때만 선다', async () => {
    relayApi.pipeline.mockResolvedValue(
      detail({ state: { pending: { job_id: 'p', ok: false, summary: '오류 2', at: '' } } }),
    )
    await mount()
    expect(screen.getByRole('button', { name: /적용/ })).toBeDisabled()
    expect(screen.getByText(/지금 계획에는 오류가 있어 적용할 수 없습니다/)).toBeInTheDocument()
  })

  it('적용을 누르면 적용 작업을 넣는다', async () => {
    relayApi.apply.mockResolvedValue({ job_id: 'job-10', kind: 'relay_apply' })
    await mount()
    await userEvent.click(screen.getByRole('button', { name: /적용/ }))
    await waitFor(() => expect(relayApi.apply).toHaveBeenCalledWith('plm-tasks'))
    expect(await screen.findByText('적용 작업을 넣었습니다')).toBeInTheDocument()
  })
})

describe('파이프라인 상세 · 레시피 판', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
    relayApi.pipeline.mockResolvedValue(detail())
    relayApi.recipes.mockResolvedValue(VERSIONS)
  })

  it('판을 새것부터 늘어놓고, 지금 도는 것을 말한다', async () => {
    await mount()
    expect(await screen.findByText('판 2')).toBeInTheDocument()
    expect(screen.getByText('판 1')).toBeInTheDocument()
    expect(screen.getByText('마감일 칸 추가')).toBeInTheDocument()
    expect(screen.getByText('정의 파일의 레시피(켜진 판 없음)')).toBeInTheDocument()
    // 켜진 판에는 「켜기」 가 없다.
    expect(screen.getByRole('button', { name: '판 2 켜기' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '판 1 켜기' })).not.toBeInTheDocument()
  })

  it('시험 — 켜면 보낼 것은 움직이는 타입만 싣는다', async () => {
    relayApi.testRecipe.mockResolvedValue(TRIAL)
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '판 2 시험' }))

    await waitFor(() => expect(relayApi.testRecipe).toHaveBeenCalledWith('plm-tasks', 2))
    expect(await screen.findByText('판 2 시험')).toBeInTheDocument()
    expect(screen.getAllByText('깨끗함').length).toBeGreaterThan(0)
    expect(screen.getByText(/지난번 적용된 출력/)).toBeInTheDocument()
    const sendTable = screen.getByText('켜면 보낼 것').parentElement!
    expect(within(sendTable).getByText('plm_task')).toBeInTheDocument()
    expect(within(sendTable).queryByText('plm_project')).not.toBeInTheDocument()
    expect(screen.getByText('변환 보고 펼치기')).toBeInTheDocument()
  })

  it('켜기 — 확인 창을 거쳐 켜고, 판 목록과 상세를 다시 읽는다', async () => {
    relayApi.testRecipe.mockResolvedValue(TRIAL)
    relayApi.activateRecipe.mockResolvedValue({ ...VERSIONS[0], status: 'active' })
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '판 2 켜기' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('판 2 을 켭니다')).toBeInTheDocument()
    // 이 화면에서 시험하지 않았으면 그렇다고 먼저 말한다.
    expect(within(dialog).getByText(/아직 이 화면에서 시험하지 않았습니다/)).toBeInTheDocument()
    expect(relayApi.activateRecipe).not.toHaveBeenCalled()

    await userEvent.click(within(dialog).getByRole('button', { name: '켜기' }))
    await waitFor(() => expect(relayApi.activateRecipe).toHaveBeenCalledWith('plm-tasks', 2))
    await waitFor(() => expect(relayApi.recipes).toHaveBeenCalledTimes(2))
    expect(relayApi.pipeline).toHaveBeenCalledTimes(2)
  })
})
