/**
 * 파이프라인 상세 — 칸 대응 표가 대상에 없는 칸을 빨갛게 말하고, 실행 · 적용 · 정의 판 · 레시피 판은
 * 시스템 관리자에게만 선다. 넣은 작업은 작업 화면으로 가는 링크를 남긴다. 초안은 돌지 않는다고 말하고
 * 실행 단추를 세우지 않는다. 초안 판은 버릴 수 있고, 새 파이프라인의 마지막 초안을 버리면 목록으로
 * 돌아간다. 자동 적용은 사실 목록 · 이력 · 켜기 확인 창이 말한다. 대조는 타입마다 건수와 본보기를
 * 보이고, 본보기가 잘렸으면 전체 보고서를 권한다. 보정 계획은 적용을 기다리는 계획이 있으면 막힌다.
 * 값의 출처는 대조 본보기의 식별자를 클릭해도, 직접 입력해도 조회하고, 못 찾으면 서버의 까닭을 보인다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  DefinitionTrial,
  DefinitionVersion,
  PipelineDetail,
  RecipeTrial,
  RecipeVersion,
  ValueTrace,
} from '@/modules/relay/api'
import { fullReconcile, sampleReconcile } from '@/modules/relay/fixtures'
import { ApiError } from '@/shared/api/client'
import { statusLabel } from '@/shared/components/StatusBadge'

const relayApi = vi.hoisted(() => ({
  pipeline: vi.fn(),
  run: vi.fn(),
  apply: vi.fn(),
  reconcile: vi.fn(),
  reconcileReport: vi.fn(),
  trace: vi.fn(),
  recipes: vi.fn(),
  recipe: vi.fn(),
  testRecipe: vi.fn(),
  activateRecipe: vi.fn(),
  discardRecipe: vi.fn(),
  definitions: vi.fn(),
  definition: vi.fn(),
  definitionFile: vi.fn(),
  testDefinition: vi.fn(),
  activateDefinition: vi.fn(),
  discardDefinition: vi.fn(),
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
    schedule_label: '수동',
    auto_apply_label: '미사용',
    definition_label: '파일',
    draft: false,
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
    reconcile: null,
    reconcile_label: '수동',
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
        <Route path="/relay/pipelines" element={<p>파이프라인 목록</p>} />
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
    relayApi.definitions.mockResolvedValue([])
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
    expect(screen.getByText('생성 3 · 변경 1 · 삭제 2')).toBeInTheDocument()
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
    expect(screen.queryByText('정의 판')).not.toBeInTheDocument()
    expect(relayApi.definitions).not.toHaveBeenCalled()
  })

  it('지금 실행 — 덮기를 고르면 overwrite 로 넣고, 작업 화면 링크를 남기고, 다시 읽는다', async () => {
    relayApi.run.mockResolvedValue({ job_id: 'job-9', kind: 'relay_run' })
    await mount()

    await userEvent.click(screen.getByLabelText(/사람이 수정한 칸도 PLM 값으로 덮기\(overwrite\)/))
    await userEvent.click(screen.getByRole('button', { name: /지금 실행/ }))

    await waitFor(() => expect(relayApi.run).toHaveBeenCalledWith('plm-tasks', 'overwrite'))
    expect(await screen.findByText('실행 작업을 등록했습니다')).toBeInTheDocument()
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

  it('자동 적용 — 미사용이면 「미사용」 을 흐리게, 사용하면 기준을 적는다', async () => {
    auth.admin = false
    await mount()
    const off = screen.getByText('미사용')
    expect(off).toHaveClass('text-muted-foreground')
    expect(off.closest('div')).toHaveTextContent('자동 적용')
  })

  it('자동 적용 — 사용 중인 기준은 흐리지 않는다', async () => {
    auth.admin = false
    const label = '타이머 실행 · 사람 적용 3번 뒤 · 변경 500건 이하 · 삭제 없을 때만'
    relayApi.pipeline.mockResolvedValue(detail({ auto_apply_label: label }))
    await mount()
    const on = screen.getByText(label)
    expect(on).not.toHaveClass('text-muted-foreground')
    expect(on.closest('div')).toHaveTextContent('자동 적용')
  })

  it('이력 — 자동 적용은 사람의 적용과 갈리고, 안 한 까닭을 늘어놓는다', async () => {
    auth.admin = false
    relayApi.pipeline.mockResolvedValue(
      detail({
        history: [
          { at: '2026-11-05T09:00:00Z', event: 'applied', job_id: 'auto-1', by: 'auto' },
          {
            at: '2026-11-04T09:00:00Z',
            event: 'auto_skipped',
            job_id: 'plan-2',
            reasons: ['사람이 적용한 것이 1번 — 3번 뒤부터 자동입니다', '삭제 2건이 있습니다'],
          },
          { at: '2026-11-03T09:00:00Z', event: 'applied', job_id: 'hand-1' },
        ],
      }),
    )
    await mount()
    // 이력의 줄 — 까닭 목록(안쪽 ul)의 항목은 빼고 바깥 줄만.
    const list = screen.getByRole('heading', { name: '실행 이력' }).closest('section')!.querySelector('ul')!
    const items = Array.from(list.children) as HTMLElement[]
    expect(items).toHaveLength(3)
    expect(within(items[0]).getByText('자동 적용')).toBeInTheDocument()
    expect(within(items[1]).getByText('자동 적용 안 함')).toBeInTheDocument()
    expect(within(items[1]).getByText('사람이 적용한 것이 1번 — 3번 뒤부터 자동입니다')).toBeInTheDocument()
    expect(within(items[1]).getByText('삭제 2건이 있습니다')).toBeInTheDocument()
    // 사람의 적용에는 `by` 가 없다 — 그냥 「적용」.
    expect(within(items[2]).getByText('적용')).toBeInTheDocument()
    expect(within(items[2]).queryByText('자동 적용')).not.toBeInTheDocument()
  })

  it('적용을 누르면 적용 작업을 넣는다', async () => {
    relayApi.apply.mockResolvedValue({ job_id: 'job-10', kind: 'relay_apply' })
    await mount()
    await userEvent.click(screen.getByRole('button', { name: /적용/ }))
    await waitFor(() => expect(relayApi.apply).toHaveBeenCalledWith('plm-tasks'))
    expect(await screen.findByText('적용 작업을 등록했습니다')).toBeInTheDocument()
  })
})

describe('파이프라인 상세 · 레시피 판', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
    relayApi.pipeline.mockResolvedValue(detail())
    relayApi.recipes.mockResolvedValue(VERSIONS)
    relayApi.definitions.mockResolvedValue([])
  })

  it('판을 새것부터 늘어놓고, 지금 도는 것을 말한다', async () => {
    await mount()
    expect(await screen.findByText('판 2')).toBeInTheDocument()
    expect(screen.getByText('판 1')).toBeInTheDocument()
    expect(screen.getByText('마감일 칸 추가')).toBeInTheDocument()
    expect(screen.getByText('정의 파일의 레시피(사용 중인 판 없음)')).toBeInTheDocument()
    // 켜진 판에는 「켜기」 가 없다.
    expect(screen.getByRole('button', { name: '판 2 사용' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '판 1 사용' })).not.toBeInTheDocument()
  })

  it('시험 — 사용하면 보낼 것은 움직이는 타입만 싣는다', async () => {
    relayApi.testRecipe.mockResolvedValue(TRIAL)
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '판 2 시험' }))

    await waitFor(() => expect(relayApi.testRecipe).toHaveBeenCalledWith('plm-tasks', 2))
    expect(await screen.findByText('판 2 시험')).toBeInTheDocument()
    expect(screen.getAllByText('깨끗함').length).toBeGreaterThan(0)
    // 대조 칸도 같은 말(지난번 적용된 출력)을 쓴다 — 견준 것 한 줄에서 찾는다.
    expect(screen.getByText(/견준 것: 지난번 적용된 출력/)).toBeInTheDocument()
    const sendTable = screen.getByText('사용하면 보낼 것').parentElement!
    expect(within(sendTable).getByText('plm_task')).toBeInTheDocument()
    expect(within(sendTable).queryByText('plm_project')).not.toBeInTheDocument()
    expect(screen.getByText('변환 보고 펼치기')).toBeInTheDocument()
  })

  it('사용 — 확인 창을 거쳐 켜고, 판 목록과 상세를 다시 읽는다', async () => {
    relayApi.testRecipe.mockResolvedValue(TRIAL)
    relayApi.activateRecipe.mockResolvedValue({ ...VERSIONS[0], status: 'active' })
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '판 2 사용' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('판 2 을 사용합니다')).toBeInTheDocument()
    // 이 화면에서 시험하지 않았으면 그렇다고 먼저 말한다.
    expect(within(dialog).getByText(/아직 이 화면에서 시험하지 않았습니다/)).toBeInTheDocument()
    expect(relayApi.activateRecipe).not.toHaveBeenCalled()

    await userEvent.click(within(dialog).getByRole('button', { name: '사용' }))
    await waitFor(() => expect(relayApi.activateRecipe).toHaveBeenCalledWith('plm-tasks', 2))
    await waitFor(() => expect(relayApi.recipes).toHaveBeenCalledTimes(2))
    expect(relayApi.pipeline).toHaveBeenCalledTimes(2)
  })

  it('폐기 — 초안에만 서고, 확인 창을 거쳐 버리고 판 목록을 다시 읽는다', async () => {
    relayApi.discardRecipe.mockResolvedValue({ ...VERSIONS[0], status: 'discarded' })
    await mount()
    expect(await screen.findByRole('button', { name: '판 2 폐기' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '판 1 폐기' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '판 2 폐기' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('판 2 을 폐기합니다')).toBeInTheDocument()
    expect(
      within(dialog).getByText(/초안 판 2 을 폐기합니다 — 복원할 수 없습니다. 판 번호는 다시 사용하지 않습니다./),
    ).toBeInTheDocument()
    expect(relayApi.discardRecipe).not.toHaveBeenCalled()

    // 다시 읽으면 버린 판은 「버림」 으로 흐리게 남고, 시험 · 켜기 · 버리기가 없다.
    relayApi.recipes.mockResolvedValue([{ ...VERSIONS[0], status: 'discarded' }, VERSIONS[1]])
    await userEvent.click(within(dialog).getByRole('button', { name: '폐기' }))
    await waitFor(() => expect(relayApi.discardRecipe).toHaveBeenCalledWith('plm-tasks', 2))
    await waitFor(() => expect(relayApi.recipes).toHaveBeenCalledTimes(2))
    const row = (await screen.findByText('폐기')).closest('tr')!
    expect(row).toHaveClass('text-muted-foreground')
    expect(screen.queryByRole('button', { name: '판 2 시험' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '판 2 사용' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '판 2 폐기' })).not.toBeInTheDocument()
    expect(within(row).getByRole('button', { name: '판 2 본문 보기' })).toBeInTheDocument()
    // 켜진 판이 그대로 돌므로 상세는 다시 읽지 않는다.
    expect(relayApi.pipeline).toHaveBeenCalledTimes(1)
  })

  it('폐기 — 서버가 거절하면 창을 닫지 않고 오류 코드를 보인다', async () => {
    relayApi.discardRecipe.mockRejectedValue(
      new ApiError(409, {
        error: { code: 'DRL-RELAY-0017', message: '초안만 폐기할 수 있습니다 — 레시피 판 2 은 active 입니다.' },
      }),
    )
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '판 2 폐기' }))
    const dialog = await screen.findByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: '폐기' }))

    expect(await within(dialog).findByText(/초안만 폐기할 수 있습니다/)).toBeInTheDocument()
    expect(within(dialog).getByText('DRL-RELAY-0017')).toBeInTheDocument()
    expect(relayApi.recipes).toHaveBeenCalledTimes(1)
  })
})

// 정의 판 번호는 레시피 판(2 · 1)과 겹치지 않게 — 「판 2」 가 두 표에 서면 어느 것인지 못 가린다.
const DEFINITIONS: DefinitionVersion[] = [
  {
    version: 4,
    status: 'draft',
    note: '쪽 넘기기 고침',
    client: 'mcp',
    created_at: '2026-11-04T12:00:00Z',
    activated_at: null,
    summary: 'rest GET /tasks → 자주검증 Datahub · source plm',
  },
  {
    version: 3,
    status: 'active',
    note: '처음 판',
    client: '화면',
    created_at: '2026-11-02T10:00:00Z',
    activated_at: '2026-11-02T11:00:00Z',
    summary: 'rest GET /tasks → 자주검증 Datahub · source plm',
  },
]

const DEFINITION_TRIAL: DefinitionTrial = {
  version: 4,
  problems: [],
  target: {
    server: 'http://hub',
    reachable: true,
    slug: 'datahub',
    version: '1.2',
    token: '정상',
    types_present: ['plm_task'],
    types_missing: ['plm_part'],
    managed_by: { plm_task: 'plm' },
  },
  source: {
    ok: true,
    probe: 'GET http://plm.local/tasks → 200',
    rows: 9,
    deleted: 1,
    complete: true,
    watermark: '2026-11-04T09:00:00Z',
    columns: ['code', 'due', 'dept'],
    always_empty: ['dept'],
    sample: [
      { key: 'T-1', code: 't-1', due: '2026-11-10', dept: '' },
      { key: 'T-2', code: 't-2', due: '2026-11-11', dept: '' },
    ],
  },
  recipe: { ...TRIAL, used: '사용 중인 레시피 판 1' },
}

/** 깨끗한 시험 — 대상에 타입이 다 있다. */
const CLEAN_TRIAL: DefinitionTrial = {
  ...DEFINITION_TRIAL,
  target: { ...DEFINITION_TRIAL.target!, types_present: ['plm_task', 'plm_part'], types_missing: [] },
}

/** 원천이 커서 앞 50줄만 받은 시험 — 레시피 쪽에 「사용하면 보낼 것」 이 없다. */
const CAPPED_TRIAL: DefinitionTrial = {
  ...CLEAN_TRIAL,
  source: {
    ok: true,
    probe: 'GET http://plm.local/tasks → 200',
    rows: 50,
    deleted: 0,
    complete: false,
    capped: 50,
    watermark: null,
    columns: ['code', 'due'],
    always_empty: [],
    sample: [{ key: 'T-1', code: 't-1', due: '2026-11-10' }],
  },
  recipe: {
    clean: true,
    problems: [],
    rows: { plm_task: 50 },
    unresolved_count: 0,
    unresolved: [],
    against: '앞부분만 받아 「사용하면 보낼 것」 은 세지 않았습니다',
    report: '',
    used: '사용 중인 레시피 판 1',
  },
}

/** 판의 본문 — 켜기 확인 창이 자동 적용을 보려고 읽는다. 내용은 지어낸 것. */
const bodyOf = (extra: Record<string, unknown> = {}) => ({
  ...DEFINITIONS[0],
  body: { format: 'relay-pipeline/1', source: 'plm', ...extra },
})

/** 켜진 판이 없는 새 파이프라인 — 정의 판 초안만 있다. */
const DRAFT_DETAIL: Partial<PipelineDetail> = {
  status: 'draft',
  status_label: '초안(미사용)',
  draft: true,
  definition_label: '초안 4',
  definition_path: '<정의 판:plm-tasks>',
  state: {},
  history: [],
  jobs: [],
}

const panel = () => screen.getByRole('heading', { name: '정의 판' }).closest('section')!

describe('파이프라인 상세 · 정의 판', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
    relayApi.pipeline.mockResolvedValue(detail())
    relayApi.recipes.mockResolvedValue(VERSIONS)
    relayApi.definitions.mockResolvedValue(DEFINITIONS)
    relayApi.definition.mockResolvedValue(bodyOf())
  })

  it('판을 새것부터 늘어놓고, 지금 도는 것 · 요약을 말한다', async () => {
    await mount()
    expect(await screen.findByText('판 4')).toBeInTheDocument()
    const section = within(panel())
    expect(section.getByText('판 3')).toBeInTheDocument()
    expect(section.getByText('쪽 넘기기 고침')).toBeInTheDocument()
    expect(section.getAllByText('rest GET /tasks → 자주검증 Datahub · source plm')).toHaveLength(2)
    expect(section.getByText('정의 파일(사용 중인 판 없음)')).toBeInTheDocument()
    // 켜진 판에는 「켜기」 가 없다.
    expect(section.getByRole('button', { name: '정의 판 4 사용' })).toBeInTheDocument()
    expect(section.queryByRole('button', { name: '정의 판 3 사용' })).not.toBeInTheDocument()
    // 시험에 쓸 레시피는 레시피 판 목록에서 고른다.
    const picker = section.getByLabelText('시험에 사용할 레시피')
    expect(within(picker).getByRole('option', { name: '레시피 판 2 · 초안' })).toBeInTheDocument()
  })

  it('시험 — 원천 · 대상 · 레시피를 차례로, 대상에 없는 타입은 빨갛게', async () => {
    relayApi.testDefinition.mockResolvedValue(DEFINITION_TRIAL)
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '정의 판 4 시험' }))

    await waitFor(() => expect(relayApi.testDefinition).toHaveBeenCalledWith('plm-tasks', 4, {}))
    expect(await screen.findByText('정의 판 4 시험')).toBeInTheDocument()
    const section = within(panel())
    // 대상에 없는 타입이 있으면 막힘.
    expect(section.getAllByText('막힘').length).toBeGreaterThan(0)
    // 원천 — 받은 줄 · 열 · 늘 빈 열 · 보기 표.
    expect(section.getByText('받음')).toBeInTheDocument()
    expect(section.getByText(/받은 줄 9 · 삭제 표시 1/)).toBeInTheDocument()
    expect(section.getByText('code · due · dept')).toBeInTheDocument()
    expect(section.getByText(/받은 줄에서 늘 빈 열 1개/)).toBeInTheDocument()
    expect(section.getByText('T-2')).toBeInTheDocument()
    // 대상 — 토큰 · 없는 타입(빨강) · 있는 타입.
    expect(section.getByText('연결됨')).toBeInTheDocument()
    expect(section.getByText('정상')).toBeInTheDocument()
    const missing = section.getByText(/대상 SP 정의에 없는 타입 1개/)
    expect(missing).toHaveClass('text-destructive')
    expect(within(missing).getByText('plm_part')).toBeInTheDocument()
    expect(section.getByText('plm_task(관리: plm)')).toBeInTheDocument()
    // 레시피 — 레시피 판 시험과 같은 그림, 무엇을 썼나가 제목이다.
    expect(section.getByText('레시피 — 사용 중인 레시피 판 1')).toBeInTheDocument()
    expect(section.getByText('사용하면 보낼 것')).toBeInTheDocument()
  })

  it('시험에 쓸 레시피를 고르면 그 판으로 바꿔 본다', async () => {
    relayApi.testDefinition.mockResolvedValue(CLEAN_TRIAL)
    await mount()
    await userEvent.selectOptions(await screen.findByLabelText('시험에 사용할 레시피'), '2')
    await userEvent.click(screen.getByRole('button', { name: '정의 판 4 시험' }))
    await waitFor(() =>
      expect(relayApi.testDefinition).toHaveBeenCalledWith('plm-tasks', 4, { recipe_version: 2 }),
    )
  })

  it('원천을 못 받으면 그 까닭을 말하고 막힘이다', async () => {
    relayApi.testDefinition.mockResolvedValue({
      version: 4,
      problems: [],
      target: { server: 'http://hub', reachable: false, error: '대상에 닿지 않습니다(응답 없음).' },
      source: { ok: false, error: '원천이 401 을 돌려주었습니다' },
    } satisfies DefinitionTrial)
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '정의 판 4 시험' }))

    const section = within(panel())
    expect(await section.findByText('원천이 401 을 돌려주었습니다')).toBeInTheDocument()
    expect(section.getByText('못 받음')).toBeInTheDocument()
    expect(section.getByText('닿지 않음')).toBeInTheDocument()
    expect(section.getByText('원천을 못 받아 레시피로 바꿔 보지 못했습니다.')).toBeInTheDocument()
    expect(section.getAllByText('막힘').length).toBeGreaterThan(0)
  })

  it('사용 — 확인 창이 시험 결과와 파일 대신 돈다는 것을 말하고, 판 목록과 상세를 다시 읽는다', async () => {
    relayApi.testDefinition.mockResolvedValue(CLEAN_TRIAL)
    relayApi.activateDefinition.mockResolvedValue({ ...DEFINITIONS[0], status: 'active' })
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '정의 판 4 시험' }))
    await screen.findByText('정의 판 4 시험')
    await userEvent.click(screen.getByRole('button', { name: '정의 판 4 사용' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('정의 판 4 을 사용합니다')).toBeInTheDocument()
    expect(within(dialog).getByText(/같은 slug 의 정의 파일이 있으면 그 파일 대신입니다/)).toBeInTheDocument()
    expect(within(dialog).getByText(/시험은 깨끗했습니다 — 원천 9줄을 사용 중인 레시피 판 1/)).toBeInTheDocument()
    expect(relayApi.activateDefinition).not.toHaveBeenCalled()

    // 본문을 다 읽어야 누를 수 있다 — 자동 적용이 꺼진 판이라 그 말은 없다.
    const confirm = within(dialog).getByRole('button', { name: '사용' })
    await waitFor(() => expect(confirm).toBeEnabled())
    expect(relayApi.definition).toHaveBeenCalledWith('plm-tasks', 4)
    expect(within(dialog).queryByText(/자동 적용을 사용합니다/)).not.toBeInTheDocument()
    await userEvent.click(confirm)
    await waitFor(() => expect(relayApi.activateDefinition).toHaveBeenCalledWith('plm-tasks', 4))
    await waitFor(() => expect(relayApi.definitions).toHaveBeenCalledTimes(2))
    expect(relayApi.pipeline).toHaveBeenCalledTimes(2)
  })

  it('시험 — 원천이 커서 앞부분만 받았으면 그렇다고 말하고 「사용하면 보낼 것」 을 세지 않는다', async () => {
    relayApi.testDefinition.mockResolvedValue(CAPPED_TRIAL)
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '정의 판 4 시험' }))
    await screen.findByText('정의 판 4 시험')

    const section = within(panel())
    const note = section.getByText(/원천이 커서 앞 50줄만 받아 봤습니다/)
    expect(note).toHaveClass('text-amber-700')
    expect(note).toHaveTextContent('덜 받은 채 견주면 나머지가 다 삭제로 보입니다')
    // 상한 때문이지 쪽을 못 받은 것이 아니다 — 그 말은 안 낸다.
    expect(section.queryByText(/마지막 쪽까지 못 받았습니다/)).not.toBeInTheDocument()
    // 보낼 것 표 대신 왜 안 셌나를 싣는다. 막는 것은 아니다.
    expect(section.getByText('레시피 — 사용 중인 레시피 판 1')).toBeInTheDocument()
    expect(section.queryByText('사용하면 보낼 것')).not.toBeInTheDocument()
    expect(section.getByText('앞부분만 받아 「사용하면 보낼 것」 은 세지 않았습니다.')).toBeInTheDocument()
    expect(section.getAllByText('깨끗함').length).toBeGreaterThan(0)

    // 켜기 확인 창도 「사용하면 보낼 것: …」 대신 앞부분만 받았다고 말한다.
    await userEvent.click(screen.getByRole('button', { name: '정의 판 4 사용' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/원천 앞 50줄을 사용 중인 레시피 판 1/)).toBeInTheDocument()
    expect(
      within(dialog).getByText(/원천이 커서 앞부분만 받아 「사용하면 보낼 것」 은 세지 않았습니다/),
    ).toBeInTheDocument()
    expect(within(dialog).queryByText(/사용하면 보낼 것:/)).not.toBeInTheDocument()
  })

  it('폐기 — 초안에만 서고, 확인 창을 거쳐 버리고 판 목록을 다시 읽는다', async () => {
    relayApi.discardDefinition.mockResolvedValue({ ...DEFINITIONS[0], status: 'discarded' })
    await mount()
    const section = within(panel())
    expect(await section.findByRole('button', { name: '정의 판 4 폐기' })).toBeInTheDocument()
    expect(section.queryByRole('button', { name: '정의 판 3 폐기' })).not.toBeInTheDocument()

    await userEvent.click(section.getByRole('button', { name: '정의 판 4 폐기' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('정의 판 4 을 폐기합니다')).toBeInTheDocument()
    expect(within(dialog).getByText(/초안 판 4 을 폐기합니다 — 복원할 수 없습니다/)).toBeInTheDocument()
    // 정의 파일이 도는 파이프라인 — 사라진다는 말은 없다.
    expect(within(dialog).queryByText(/마지막 초안입니다/)).not.toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: '폐기' }))
    await waitFor(() => expect(relayApi.discardDefinition).toHaveBeenCalledWith('plm-tasks', 4))
    await waitFor(() => expect(relayApi.definitions).toHaveBeenCalledTimes(2))
    expect(relayApi.pipeline).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('파이프라인 목록')).not.toBeInTheDocument()
  })

  it('폐기한 판은 「폐기」 로 흐리게 남고 본문만 볼 수 있다 — 시험에 쓸 레시피에서도 빠진다', async () => {
    expect(statusLabel('relay_definition', 'discarded')).toBe('폐기')
    expect(statusLabel('relay_recipe', 'discarded')).toBe('폐기')
    relayApi.definitions.mockResolvedValue([{ ...DEFINITIONS[0], status: 'discarded' }, DEFINITIONS[1]])
    relayApi.recipes.mockResolvedValue([{ ...VERSIONS[0], status: 'discarded' }, VERSIONS[1]])
    await mount()

    const section = within(panel())
    const row = (await section.findByText('판 4')).closest('tr')!
    expect(within(row).getByText('폐기')).toBeInTheDocument()
    expect(row).toHaveClass('text-muted-foreground')
    expect(within(row).queryByRole('button', { name: '정의 판 4 시험' })).not.toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: '정의 판 4 사용' })).not.toBeInTheDocument()
    expect(within(row).queryByRole('button', { name: '정의 판 4 폐기' })).not.toBeInTheDocument()
    expect(within(row).getByRole('button', { name: '정의 판 4 본문 보기' })).toBeInTheDocument()
    const picker = section.getByLabelText('시험에 사용할 레시피')
    expect(within(picker).queryByRole('option', { name: /레시피 판 2/ })).not.toBeInTheDocument()
    expect(within(picker).getByRole('option', { name: '레시피 판 1 · 사용 중' })).toBeInTheDocument()
  })

  it('폐기 — 새 파이프라인의 마지막 초안이면 사라진다고 말하고, 버린 뒤 목록으로 돌아간다', async () => {
    relayApi.pipeline.mockResolvedValue(detail(DRAFT_DETAIL))
    relayApi.definitions.mockResolvedValue([DEFINITIONS[0]])
    relayApi.discardDefinition.mockResolvedValue({ ...DEFINITIONS[0], status: 'discarded' })
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '정의 판 4 폐기' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/이 파이프라인의 마지막 초안입니다/)).toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '폐기' }))

    await waitFor(() => expect(relayApi.discardDefinition).toHaveBeenCalledWith('plm-tasks', 4))
    expect(await screen.findByText('파이프라인 목록')).toBeInTheDocument()
    // 404 가 될 상세는 다시 읽지 않는다.
    expect(relayApi.definitions).toHaveBeenCalledTimes(1)
    expect(relayApi.pipeline).toHaveBeenCalledTimes(1)
  })

  it('폐기 — 새 파이프라인이라도 초안이 남으면 판 목록과 상세를 다시 읽는다', async () => {
    relayApi.pipeline.mockResolvedValue(detail({ ...DRAFT_DETAIL, definition_label: '초안 5' }))
    relayApi.definitions.mockResolvedValue([{ ...DEFINITIONS[0], version: 5 }, DEFINITIONS[0]])
    relayApi.discardDefinition.mockResolvedValue({ ...DEFINITIONS[0], version: 5, status: 'discarded' })
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '정의 판 5 폐기' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByText(/마지막 초안입니다/)).not.toBeInTheDocument()
    await userEvent.click(within(dialog).getByRole('button', { name: '폐기' }))

    await waitFor(() => expect(relayApi.discardDefinition).toHaveBeenCalledWith('plm-tasks', 5))
    await waitFor(() => expect(relayApi.definitions).toHaveBeenCalledTimes(2))
    expect(relayApi.pipeline).toHaveBeenCalledTimes(2)
    expect(screen.queryByText('파이프라인 목록')).not.toBeInTheDocument()
  })

  it('사용 — 본문에 자동 적용이 켜져 있으면 기준을 늘어놓고, 다 읽기 전에는 누르지 못한다', async () => {
    let answer: (value: unknown) => void = () => {}
    relayApi.definition.mockReturnValue(new Promise((resolve) => (answer = resolve)))
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '정의 판 4 사용' }))

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/판의 본문을 읽는 중/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '사용' })).toBeDisabled()

    answer(bodyOf({ auto_apply: { after_manual: 5, max_changes: 200 } }))
    expect(
      await within(dialog).findByText(
        '이 판은 자동 적용을 사용합니다 — 타이머의 실행이 기준을 넘은 계획을 사람 없이 적용합니다.',
      ),
    ).toBeInTheDocument()
    // 적힌 것은 그대로, 빠진 칸(allow_gone)은 기본으로.
    expect(within(dialog).getByText('사람이 적용한 것이 5번 된 뒤부터')).toBeInTheDocument()
    expect(within(dialog).getByText(/생성 \+ 변경이 200건 이하일 때/)).toBeInTheDocument()
    expect(within(dialog).getByText('삭제(사용 중지 · 관계 해제)가 없을 때만')).toBeInTheDocument()
    expect(
      within(dialog).getByText('auto_apply: {"after_manual":5,"max_changes":200}'),
    ).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: '사용' })).toBeEnabled()
  })

  it('사용 — auto_apply 가 true 면 기본 기준(사람 적용 3번 뒤 · 500건 이하 · 삭제 없을 때만)', async () => {
    relayApi.definition.mockResolvedValue(bodyOf({ auto_apply: true }))
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '정의 판 4 사용' }))

    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(/자동 적용을 사용합니다/)).toBeInTheDocument()
    expect(within(dialog).getByText('사람이 적용한 것이 3번 된 뒤부터')).toBeInTheDocument()
    expect(within(dialog).getByText(/생성 \+ 변경이 500건 이하일 때/)).toBeInTheDocument()
    expect(within(dialog).getByText('삭제(사용 중지 · 관계 해제)가 없을 때만')).toBeInTheDocument()
    expect(within(dialog).getByText('auto_apply: true')).toBeInTheDocument()
  })

  it('사용 — auto_apply 가 false 면 자동 적용을 말하지 않는다', async () => {
    relayApi.definition.mockResolvedValue(bodyOf({ auto_apply: false }))
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '정의 판 4 사용' }))

    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect(within(dialog).getByRole('button', { name: '사용' })).toBeEnabled())
    expect(within(dialog).queryByText(/자동 적용을 사용합니다/)).not.toBeInTheDocument()
  })

  it('사용 — 시험하지 않았으면 그렇다고 먼저 말한다', async () => {
    await mount()
    await userEvent.click(await screen.findByRole('button', { name: '정의 판 4 사용' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/아직 이 화면에서 시험하지 않았습니다/)).toBeInTheDocument()
  })

  it('초안 — 아직 안 돈다고 말하고, 실행 · 적용 단추가 없다', async () => {
    relayApi.pipeline.mockResolvedValue(detail(DRAFT_DETAIL))
    relayApi.definitions.mockResolvedValue([DEFINITIONS[0]])
    await mount()

    expect(
      screen.getByText('이 파이프라인은 아직 초안입니다 — 정의 판을 사용해야 실행됩니다'),
    ).toBeInTheDocument()
    expect(screen.getByText('초안(미사용)')).toBeInTheDocument()
    expect(screen.getByText('초안 4')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /지금 실행/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /적용/ })).not.toBeInTheDocument()
    expect(screen.getByText(/초안은 실행되지 않습니다/)).toBeInTheDocument()
    // 켜는 길은 정의 판에 있다.
    expect(await screen.findByRole('button', { name: '정의 판 4 사용' })).toBeInTheDocument()
    expect(within(panel()).getByText(/초안 4 — 사용 중인 판이 없어 실행되지 않습니다/)).toBeInTheDocument()
  })
})

describe('파이프라인 상세 · 대조', () => {
  /** 대조 칸 — 제목 「대조」 의 section. */
  const section = () => screen.getByRole('heading', { name: '대조' }).closest('section')!
  const NO_PENDING = { watermark: '2026-11-04T09:00:00Z', applied_run: 'runs/1', applied_at: '2026-11-03T09:00:00Z' }

  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
    relayApi.pipeline.mockResolvedValue(detail())
    relayApi.recipes.mockResolvedValue(VERSIONS)
    relayApi.definitions.mockResolvedValue([])
  })

  it('대조한 적이 없으면 그렇다고 말하고, 일정을 적는다', async () => {
    await mount()
    const box = within(section())
    expect(box.getByText('아직 대조한 적이 없습니다')).toBeInTheDocument()
    expect(box.getByText('수동')).toHaveClass('text-muted-foreground')
    expect(box.getByText(/「대조」 를 클릭하면 대상 SP 의 객체를 읽어 기준과 견줍니다/)).toBeInTheDocument()
    // 어긋남이 없으므로 보정 계획 단추는 서지 않는다.
    expect(box.getByRole('button', { name: '대조' })).toBeEnabled()
    expect(box.queryByRole('button', { name: /보정 계획 생성/ })).not.toBeInTheDocument()
  })

  it('타입마다 건수 · 본보기 — 잘린 본보기는 그렇다고 말하고 전체 보고서를 불러온다', async () => {
    auth.admin = false
    relayApi.pipeline.mockResolvedValue(
      detail({ reconcile: sampleReconcile(), reconcile_label: '7일마다 03:00' }),
    )
    relayApi.reconcileReport.mockResolvedValue(fullReconcile())
    await mount()
    const box = within(section())
    expect(box.getByText('7일마다 03:00')).not.toHaveClass('text-muted-foreground')
    expect(box.getByText(/대상과 어긋남 29건/)).toBeInTheDocument()
    expect(box.getByText('어긋남')).toBeInTheDocument()

    // 건수 — 기준 객체 · 빠짐 · 안 씀 · 다름 · 대상에만 · 확인 못 한 참조.
    const task = within(screen.getByTestId('reconcile-type-plm_task'))
    const cells = screen.getByTestId('reconcile-type-plm_task').querySelectorAll('td')
    expect(Array.from(cells).slice(1, 7).map((one) => one.textContent)).toEqual(['30', '25', '1', '1', '1', '2'])
    expect(task.queryByText('(사람이 판단)')).not.toBeInTheDocument()
    // 관리하지 않는 타입의 「대상에만」 은 보정 계획에서 빠진다 — 사람이 판단.
    const project = within(screen.getByTestId('reconcile-type-plm_project'))
    expect(project.getByText('(사람이 판단)')).toBeInTheDocument()

    // 본보기 — 다름은 식별자 → 다른 칸.
    expect(screen.queryByText('T001')).not.toBeInTheDocument()
    await userEvent.click(box.getByRole('button', { name: 'plm_task 본보기 펼치기' }))
    expect(screen.getByText('T001')).toBeInTheDocument()
    expect(screen.getByText('T020')).toBeInTheDocument()
    expect(screen.queryByText('T021')).not.toBeInTheDocument()
    expect(screen.getByText('— 앞의 20건만 표시')).toBeInTheDocument()
    expect(screen.getByText('T200').parentElement).toHaveTextContent('T200 — label · due')
    await userEvent.click(box.getByRole('button', { name: 'plm_project 본보기 펼치기' }))
    expect(screen.getByText(/보정 계획에 포함하지 않으며 사람이 판단합니다/)).toBeInTheDocument()

    // 전체 보고서 — 불러오면 잘린 본보기가 다 서고 단추는 사라진다.
    await userEvent.click(box.getByRole('button', { name: '전체 보고서 불러오기' }))
    await waitFor(() => expect(relayApi.reconcileReport).toHaveBeenCalledWith('plm-tasks'))
    expect(await screen.findByText('T025')).toBeInTheDocument()
    expect(screen.queryByText('— 앞의 20건만 표시')).not.toBeInTheDocument()
    expect(box.getByText('전체 보고서를 표시하고 있습니다.')).toBeInTheDocument()
    expect(box.queryByRole('button', { name: '전체 보고서 불러오기' })).not.toBeInTheDocument()
  })

  it('시스템 관리자가 아니면 대조 · 보정 계획 단추가 안 선다', async () => {
    auth.admin = false
    relayApi.pipeline.mockResolvedValue(detail({ reconcile: sampleReconcile(), state: NO_PENDING }))
    await mount()
    const box = within(section())
    expect(box.queryByRole('button', { name: '대조' })).not.toBeInTheDocument()
    expect(box.queryByRole('button', { name: /보정 계획 생성/ })).not.toBeInTheDocument()
    expect(box.getByText('「대조」 · 「보정 계획 생성」 은 시스템 관리자가 합니다.')).toBeInTheDocument()
  })

  it('대조 — plan 없이 넣고, 작업 화면 링크를 남기고, 다시 읽는다', async () => {
    relayApi.reconcile.mockResolvedValue({ job_id: 'job-r1', kind: 'relay_reconcile' })
    await mount()
    await userEvent.click(within(section()).getByRole('button', { name: '대조' }))
    await waitFor(() => expect(relayApi.reconcile).toHaveBeenCalledWith('plm-tasks', false))
    expect(await screen.findByText('대조 작업을 등록했습니다')).toBeInTheDocument()
    expect(within(section()).getByRole('link', { name: '작업 화면에서 진행 보기' })).toHaveAttribute(
      'href',
      '/jobs?job=job-r1',
    )
    await waitFor(() => expect(relayApi.pipeline).toHaveBeenCalledTimes(2))
  })

  it('보정 계획 — 적용을 기다리는 계획이 있으면 막고 까닭을 말한다', async () => {
    // 기본 상세는 원천의 계획(plan-1)이 적용을 기다린다.
    relayApi.pipeline.mockResolvedValue(detail({ reconcile: sampleReconcile() }))
    await mount()
    const box = within(section())
    expect(box.getByRole('button', { name: /보정 계획 생성/ })).toBeDisabled()
    expect(box.getByText(/적용을 기다리는 계획이 있어 보정 계획을 생성할 수 없습니다/)).toBeInTheDocument()
  })

  it('보정 계획 — 확인 창이 갈래마다 무엇을 하는지 말하고, plan 으로 넣는다', async () => {
    relayApi.pipeline.mockResolvedValue(detail({ reconcile: sampleReconcile(), state: NO_PENDING }))
    relayApi.reconcile.mockResolvedValue({ job_id: 'job-r2', kind: 'relay_reconcile' })
    await mount()
    const button = within(section()).getByRole('button', { name: /보정 계획 생성/ })
    expect(button).toBeEnabled()
    await userEvent.click(button)

    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.getByText('보정 계획을 생성합니다')).toBeInTheDocument()
    expect(dialog.getByText(/대상 SP 의 검토함에 미리 보기로 등록합니다/)).toBeInTheDocument()
    expect(dialog.getByText('빠짐 25건 — 기준 그대로 다시 추가')).toBeInTheDocument()
    expect(dialog.getByText('안 씀 1건 — 다시 「사용」 으로 표시')).toBeInTheDocument()
    expect(dialog.getByText('다름 1건 — 기준 값으로 수정')).toBeInTheDocument()
    expect(dialog.getByText('대상에만 1건 — 사용 중지(이 원천이 관리하는 타입)')).toBeInTheDocument()
    expect(
      dialog.getByText('대상에만 1건 — 포함하지 않음(이 원천이 관리하지 않는 타입 — 사람이 판단)'),
    ).toBeInTheDocument()
    expect(relayApi.reconcile).not.toHaveBeenCalled()

    await userEvent.click(dialog.getByRole('button', { name: '생성' }))
    await waitFor(() => expect(relayApi.reconcile).toHaveBeenCalledWith('plm-tasks', true))
    expect(await screen.findByText('보정 계획 작업을 등록했습니다')).toBeInTheDocument()
    await waitFor(() => expect(relayApi.pipeline).toHaveBeenCalledTimes(2))
  })

  it('보정 계획 — 관리하지 않는 타입의 「대상에만」 뿐이면 실을 것이 없다고 말한다', async () => {
    const report = sampleReconcile({ drift: 1 })
    delete (report.types as Record<string, unknown>).plm_task
    relayApi.pipeline.mockResolvedValue(detail({ reconcile: report, state: NO_PENDING }))
    await mount()
    const box = within(section())
    expect(box.getByRole('button', { name: /보정 계획 생성/ })).toBeDisabled()
    expect(box.getByText(/보정 계획에 실을 것이 없습니다 — 사람이 판단합니다/)).toBeInTheDocument()
  })

  it('보정 계획 — 대조하지 못한 것이 있으면 막고, 그 까닭을 늘어놓는다', async () => {
    const report = sampleReconcile({ problems: ['대상에 타입이 없습니다: plm_task'] })
    report.types.plm_task.absent = true
    relayApi.pipeline.mockResolvedValue(detail({ reconcile: report, state: NO_PENDING }))
    await mount()
    const box = within(section())
    expect(box.getByText('대조 못 함')).toBeInTheDocument()
    expect(box.getByText('대상에 타입이 없습니다: plm_task')).toBeInTheDocument()
    expect(box.getByText('대상에 없는 타입')).toBeInTheDocument()
    expect(box.getByRole('button', { name: /보정 계획 생성/ })).toBeDisabled()
    expect(box.getByText(/마지막 대조를 마치지 못해 보정 계획을 생성할 수 없습니다/)).toBeInTheDocument()
  })

  it('이력 · 작업 · 대기 계획 — 대조는 「대조」, 대조가 올린 계획은 「보정 계획」', async () => {
    relayApi.pipeline.mockResolvedValue(
      detail({
        reconcile: sampleReconcile(),
        state: {
          ...NO_PENDING,
          pending: { job_id: 'fix-1', ok: true, summary: '고침 2', at: '2026-11-05T03:10:00Z', origin: 'reconcile' },
        },
        history: [
          {
            at: '2026-11-05T03:10:00Z',
            event: 'planned',
            origin: 'reconcile',
            job_id: 'fix-1',
            ok: true,
            counts: { objects: { plm_task: { created: 1, changed: 1, unchanged: 0, gone: 0 } } },
          },
          { at: '2026-11-05T03:05:00Z', event: 'reconciled', drift: 2, summary: '대상과 어긋남 2건(빠짐 1 · 다름 1) — 기준 객체 9건.' },
          { at: '2026-10-29T03:00:00Z', event: 'reconciled', drift: 0, summary: '대상과 같습니다(객체 9건).' },
          { at: '2026-10-28T09:00:00Z', event: 'planned', job_id: 'plan-0', ok: true },
        ],
        jobs: [
          {
            id: 'job-r2',
            kind: 'relay_reconcile',
            status: 'done',
            created_at: '2026-11-05T03:10:00Z',
            finished_at: '2026-11-05T03:10:10Z',
            summary: '[planned] 대상과 어긋남 2건 보정 계획: 고침 2',
            error: null,
          },
          {
            id: 'job-r1',
            kind: 'relay_reconcile',
            status: 'done',
            created_at: '2026-11-05T03:05:00Z',
            finished_at: '2026-11-05T03:05:10Z',
            summary: '[drift] 대상과 어긋남 2건(빠짐 1 · 다름 1) — 기준 객체 9건.',
            error: null,
          },
        ],
      }),
    )
    await mount()

    const list = screen.getByRole('heading', { name: '실행 이력' }).closest('section')!.querySelector('ul')!
    const items = Array.from(list.children) as HTMLElement[]
    expect(within(items[0]).getByText('보정 계획')).toBeInTheDocument()
    expect(within(items[0]).getByText('생성 1 · 변경 1 · 삭제 0')).toBeInTheDocument()
    expect(within(items[1]).getByText('대조')).toBeInTheDocument()
    expect(within(items[1]).getByText('어긋남')).toHaveClass('bg-amber-500/10')
    expect(within(items[1]).getByText('대상과 어긋남 2건(빠짐 1 · 다름 1) — 기준 객체 9건.')).toBeInTheDocument()
    expect(within(items[2]).getByText('대상과 같음')).toHaveClass('bg-muted')
    expect(within(items[3]).getByText('계획 생성')).toBeInTheDocument()

    const jobs = screen.getByRole('heading', { name: '최근 작업' }).closest('section')!
    const rows = within(jobs).getAllByRole('row').slice(1)
    expect(within(rows[0]).getByText('대조 · 보정 계획')).toBeInTheDocument()
    expect(within(rows[1]).getByText('대조')).toBeInTheDocument()

    // 대기 계획이 보정 계획이면 그렇게 적고, 보정 계획을 또 올리지 않는다.
    expect(screen.getByText('적용 대기 계획').closest('div')).toHaveTextContent('보정 계획')
    expect(within(section()).getByRole('button', { name: /보정 계획 생성/ })).toBeDisabled()
    expect(within(section()).getByText(/보정 계획이 이미 적용을 기다리고 있습니다/)).toBeInTheDocument()
  })

  it('초안에는 대조 단추가 없다', async () => {
    relayApi.pipeline.mockResolvedValue(detail({ draft: true, status: 'draft', definition_label: '초안 4' }))
    relayApi.definitions.mockResolvedValue([])
    await mount()
    const box = within(section())
    expect(box.queryByRole('button', { name: '대조' })).not.toBeInTheDocument()
    expect(box.getByText(/초안은 실행되지 않아 견줄 기준이 없습니다/)).toBeInTheDocument()
  })
})

describe('파이프라인 상세 · 값의 출처', () => {
  const area = () => screen.getByRole('heading', { name: '값의 출처' }).closest('section')!
  const TRACE: ValueTrace = {
    type_slug: 'plm_task',
    key: 'T200',
    label: '알파 1차 개발',
    found: true,
    run: 'runs/20261105T010000000000Z',
    applied_at: '2026-11-05T01:00:00Z',
    basis: '기준 실행의 원천 표',
    recipe_basis: '기준 실행의 레시피',
    note: '2행에서 모음',
    fields: [
      { name: 'key', rule: '열 TASK_CODE', columns: ['TASK_CODE'], value: 'T200', seen: [{ value: 'T200', rows: [2, 3] }] },
      { name: 'label', rule: '열 TASK_NAME', columns: ['TASK_NAME'], value: '알파 1차 개발', seen: [{ value: '알파 1차 개발', rows: [2, 3] }] },
      { name: 'due', rule: '열 DUE · 날짜로', columns: ['DUE'], value: '2026-08-01', seen: [{ value: '2026.8.1', rows: [2, 3] }] },
    ],
    rows: [
      { row: 2, values: { TASK_CODE: 'T200', TASK_NAME: '알파 1차 개발', DUE: '2026.8.1' } },
      { row: 3, values: { TASK_CODE: 'T200', TASK_NAME: '알파 1차 개발', DUE: '2026.8.1' } },
    ],
    rows_total: 2,
    relations: [{ relation: 'plm_task_of', direction: 'in', other: 'M-0001' }],
    reconcile: { state: 'changed', at: '2026-11-06T03:00:00Z', fields: ['label', 'due'], stale: false },
    problems: [],
  }

  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = false
    relayApi.pipeline.mockResolvedValue(detail({ reconcile: sampleReconcile() }))
    relayApi.recipes.mockResolvedValue(VERSIONS)
    relayApi.definitions.mockResolvedValue([])
    relayApi.trace.mockResolvedValue(TRACE)
  })

  it('대조 본보기의 식별자를 클릭하면 그 값의 출처를 조회한다 — 다른 칸 · 규칙 · 원천 값 · 관계', async () => {
    await mount()
    await userEvent.click(screen.getByRole('button', { name: 'plm_task 본보기 펼치기' }))
    await userEvent.click(screen.getByRole('button', { name: 'plm_task T200 출처 조회' }))
    await waitFor(() => expect(relayApi.trace).toHaveBeenCalledWith('plm-tasks', 'plm_task', 'T200'))

    const box = within(await screen.findByTestId('value-trace-result'))
    expect(within(area()).getByLabelText('식별자')).toHaveValue('T200')
    expect(box.getByText(/다른 칸: label · due/)).toBeInTheDocument()
    const due = within(box.getByTestId('trace-field-due'))
    expect(due.getByText('다름')).toBeInTheDocument()
    expect(due.getByText('열 DUE · 날짜로')).toBeInTheDocument()
    expect(due.getByText('2026.8.1')).toBeInTheDocument() // 원천의 원래 값 — 규칙이 날짜로 바꿨다
    expect(within(box.getByTestId('trace-field-key')).queryByText('다름')).not.toBeInTheDocument()
    expect(box.getByText(/기준 실행의 원천 표 · 2행 · 변환기: 2행에서 모음/)).toBeInTheDocument()
    expect(box.getByText('M-0001')).toBeInTheDocument()
    expect(box.queryByText(/지금 기준을 적용하기 전의 대조입니다/)).not.toBeInTheDocument()
  })

  it('지금 기준보다 앞선 대조는 그렇다고 말한다', async () => {
    relayApi.trace.mockResolvedValue({ ...TRACE, reconcile: { ...TRACE.reconcile!, state: 'same', fields: [], stale: true } })
    await mount()
    const box = within(area())
    await userEvent.type(box.getByLabelText('식별자'), 'T200')
    await userEvent.click(box.getByRole('button', { name: '출처 조회' }))
    expect(await box.findByText(/지금 기준을 적용하기 전의 대조입니다/)).toBeInTheDocument()
    expect(box.getByText('대상과 같음')).toBeInTheDocument()
  })

  it('직접 입력해 조회하고, 못 찾으면 서버의 까닭을 보인다', async () => {
    relayApi.trace.mockRejectedValue(
      new ApiError(404, {
        error: {
          code: 'DRL-RELAY-0019',
          message: 'plm_task 의 T999 를 기준 출력에서도 원천 표에서도 찾지 못했습니다.',
          request_id: 'r1',
        },
      }),
    )
    await mount()
    const box = within(area())
    expect(box.getByRole('button', { name: '출처 조회' })).toBeDisabled() // 식별자 없이는 묻지 않는다
    await userEvent.type(box.getByLabelText('식별자'), 'T999')
    await userEvent.click(box.getByRole('button', { name: '출처 조회' }))
    await waitFor(() => expect(relayApi.trace).toHaveBeenCalledWith('plm-tasks', 'plm_task', 'T999'))
    expect(await box.findByText(/찾지 못했습니다/)).toBeInTheDocument()
    expect(screen.queryByTestId('value-trace-result')).not.toBeInTheDocument()
  })
})
