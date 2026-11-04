/**
 * 「작업」 화면이 지키는 것 — **계획을 여기서 읽고 여기서 적용한다.**
 *
 * 대화상자를 닫아도 일은 계속된다고 해 놓고 돌아와서 적용할 자리가 없으면, 그 말은 절반만
 * 참이다. 그리고 워커가 없으면 그 사실부터 말해야 한다 — 안 그러면 「대기」 가 영영 대기인
 * 이유를 사람이 알 길이 없다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Job } from '@/modules/jobs/api'

const jobsApi = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  rowsCsv: vi.fn(),
  kinds: vi.fn(),
  workers: vi.fn(),
  apply: vi.fn(),
  cancel: vi.fn(),
  applyMany: vi.fn(),
  cancelMany: vi.fn(),
  download: vi.fn(),
}))
const auth = vi.hoisted(() => ({ admin: true, id: 'u1' }))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { id: auth.id, is_system_admin: auth.admin, memberships: [] } }),
}))
vi.mock('@/modules/jobs/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/jobs/api')>()),
  jobsApi,
}))

function job(over: Partial<Job> = {}): Job {
  return {
    id: 'j1',
    kind: 'objects_import',
    kind_label: '객체 일괄 입력',
    status: 'done',
    params: { type_slug: 'part' },
    progress: { stage: '계획', done: 2, total: 2 },
    result: null,
    error: null,
    parent_id: null,
    input_file_name: 'rows.csv',
    has_output: false,
    requested_by_name: '관리자',
    workspace_slug: 'hq',
    cancel_requested: false,
    attempts: 1,
    created_at: '2026-09-21T09:00:00+09:00',
    started_at: null,
    finished_at: null,
    ...over,
  }
}

const CLEAN_PLAN = {
  applied: false,
  rows: [
    {
      row: 1,
      action: 'create' as const,
      label: '볼트',
      key: 'P-1',
      object_id: null,
      changes: ['weight'],
      message: '',
    },
  ],
  errors: [],
  counts: { create: 1, update: 0, unchanged: 0, unlink: 0, error: 0 },
}

async function mount(jobs: Job[], alive = true) {
  jobsApi.list.mockResolvedValue({ items: jobs, total: jobs.length })
  jobsApi.kinds.mockResolvedValue([
    { name: 'objects_import', label: '객체 일괄 입력', needs_file: true, two_step: true },
    { name: 'datasource_sync', label: '데이터 소스 동기화', needs_file: false, two_step: false },
    { name: 'filestore_gc', label: '고아 첨부 파일 정리', needs_file: false, two_step: true },
    { name: 'ontology_retype', label: '속성 종류 변경', needs_file: false, two_step: true },
    { name: 'objects_photos', label: '사진 일괄 업로드', needs_file: true, two_step: true },
    { name: 'workspace_reassign', label: '부서 자료 이동', needs_file: false, two_step: true },
  ])
  jobsApi.workers.mockResolvedValue(
    alive
      ? [
          {
            worker_id: 'w1',
            host: 'a',
            pid: 1,
            started_at: '',
            last_seen: '',
            current_job_id: null,
            alive: true,
          },
        ]
      : [],
  )
  const { default: JobsPage } = await import('@/modules/jobs/JobsPage')
  render(<JobsPage />)
  // **줄이 그려질 때까지 기다린다.** 요청이 나간 것만 보고 넘어가면, 느린 기계에서 표가
  // 아직 비어 있는 채로 단추를 찾는다.
  // 줄이 여럿일 수 있다 — 하나만 찾으면 「여러 개」 로 터진다.
  if (jobs.length) await screen.findAllByRole('button', { name: /펼치기|접기/ })
  else await waitFor(() => expect(jobsApi.list).toHaveBeenCalled())
}

describe('작업 화면', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
  })

  it('남의 작업에는 「취소」 · 「적용」 을 안 세운다 — 시킨 사람과 시스템 관리자만', async () => {
    auth.admin = false
    await mount([
      job({ id: 'theirs', requested_by_id: 'u2', result: CLEAN_PLAN }),
      job({ id: 'running', requested_by_id: 'u2', status: 'running' }),
    ])
    await waitFor(() => expect(jobsApi.kinds).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: '취소' })).not.toBeInTheDocument()
    expect(screen.queryByText('적용 대기')).not.toBeInTheDocument()
  })

  it('결과가 스스로 한 줄로 말하는 계획(고아 첨부 정리)도 그 말과 함께 적용이 선다', async () => {
    await mount([
      job({
        kind: 'filestore_gc',
        kind_label: '고아 첨부 파일 정리',
        params: {},
        input_file_name: null,
        result: {
          applied: false,
          ok: true,
          orphans: 3,
          summary: '지울 것 — 고아 파일 3개 · 임시 파일 1개(0.1MB)',
        },
      }),
    ])
    expect(screen.getByText(/지울 것 — 고아 파일 3개/)).toBeInTheDocument()
    expect(await screen.findByText('적용 대기')).toBeInTheDocument()
  })

  it('부서 자료 이동의 계획도 펼쳐 읽고 적용한다 — 막히는 것이 있으면 적용이 안 선다', async () => {
    // 한 부서 객체 20만 개면 적용이 90초 남짓이라 통폐합 창을 닫는 사람이 있다 — 돌아와서
    // 여기서 같은 계획을 읽고 적용한다(2026-10-10).
    const reassign = (over: Record<string, unknown> = {}) => ({
      reassign: true,
      applied: false,
      ok: true,
      source_slug: 'old',
      source_name: '없어지는 팀',
      target_slug: 'new',
      target_name: '합쳐지는 팀',
      items: [
        { kind: 'members', label: '멤버', count: 3 },
        { kind: 'objects', label: '객체', count: 200_000 },
      ],
      total: 200_003,
      moved: {},
      errors: [],
      summary: '계획 — 이동할 것: 멤버 3 · 객체 200,000 (없어지는 팀 → 합쳐지는 팀)',
      ...over,
    })
    const blocked = {
      ok: false,
      errors: ['객체: 옮겨 갈 부서에 같은 것이 이미 있어 객체를 옮기지 않습니다: 부품 식별자 「K-1」'],
      summary: '계획 — 이동할 것: 객체 3 (없어지는 팀 → 합쳐지는 팀) · 막힘 1',
    }
    await mount([
      job({
        id: 'r1',
        kind: 'workspace_reassign',
        kind_label: '부서 자료 이동',
        params: {},
        input_file_name: null,
        result: reassign(),
      }),
      job({
        id: 'r2',
        kind: 'workspace_reassign',
        kind_label: '부서 자료 이동',
        params: {},
        input_file_name: null,
        result: reassign(blocked),
      }),
    ])
    expect(screen.getByText(/이동할 것: 멤버 3 · 객체 200,000/)).toBeInTheDocument()
    expect(await screen.findAllByText('적용 대기')).toHaveLength(1)
    const [first, second] = screen.getAllByRole('button', { name: '펼치기' })
    await userEvent.click(first)
    expect(screen.getByText('200,000')).toBeInTheDocument()
    jobsApi.apply.mockResolvedValue(job({ id: 'r3' }))
    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('r1'))

    await userEvent.click(second)
    expect(screen.getByText(/「K-1」/)).toBeInTheDocument()
    expect(screen.getByText(/막히는 것이 있거나 이동할 것이 없어/)).toBeInTheDocument()
  })

  it('링크의 작업이 목록에 없으면 그렇다고 말한다 — 조용히 아무것도 안 열리지 않게', async () => {
    window.history.replaceState(null, '', '/jobs?job=다른-계정의-것')
    try {
      await mount([job({ result: CLEAN_PLAN })])
      expect(await screen.findByText('링크의 작업이 이 목록에 없습니다')).toBeInTheDocument()
    } finally {
      window.history.replaceState(null, '', '/')
    }
  })

  it('이미 적용한 계획은 「적용」 을 거둔다 — 화면에서 두 번 누르지 않게', async () => {
    // 계획의 result.applied 는 영영 거짓이다(적용은 새 작업) — 서버가 applied_by 로 알려 준다.
    await mount([job({ result: CLEAN_PLAN, applied_by: 'j9-적용-작업' })])
    expect(screen.queryByText('적용 대기')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.queryByRole('button', { name: '적용' })).not.toBeInTheDocument()
    expect(screen.getByText(/적용했습니다 — 적용 작업/)).toBeInTheDocument()
  })

  it('내보내기는 파일을 받는다', async () => {
    await mount([
      job({
        kind: 'objects_export',
        kind_label: '객체 내보내기',
        has_output: true,
        input_file_name: null,
        result: { type_slug: 'part', rows: 12, format: 'csv' },
      }),
    ])
    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    await userEvent.click(screen.getByRole('button', { name: /파일 받기/ }))
    await waitFor(() => expect(jobsApi.download).toHaveBeenCalled())
  })

  it('기본은 내가 시킨 것만 — 타이머가 넣은 줄에 파묻히지 않게', async () => {
    await mount([job({ result: CLEAN_PLAN })])
    expect(jobsApi.list).toHaveBeenCalledWith(expect.objectContaining({ mine: true }))

    await userEvent.click(screen.getByLabelText('내가 시킨 것만'))
    await waitFor(() =>
      expect(jobsApi.list).toHaveBeenLastCalledWith(expect.objectContaining({ mine: false })),
    )

    await userEvent.selectOptions(screen.getByLabelText('종류'), 'datasource_sync')
    await waitFor(() =>
      expect(jobsApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ kind: 'datasource_sync' }),
      ),
    )
  })

  it('워커가 하나도 없으면 그것부터 말한다', async () => {
    await mount([job({ status: 'queued', result: null })], false)
    await waitFor(() => expect(screen.getByText('워커가 살아 있지 않습니다')).toBeInTheDocument())
    // 도는 중인 작업은 펼칠 것이 없다.
    expect(screen.getByRole('button', { name: '펼치기' })).toBeDisabled()
  })
})

describe('작업 화면 · 고른 것을 한 번에', () => {
  it('끝난 작업은 취소에 안 들어간다', async () => {
    const running = job({ id: 'j9', status: 'running', result: null })
    const finished = job({ id: 'j8', status: 'done', result: CLEAN_PLAN })
    jobsApi.cancelMany.mockResolvedValue([{ id: 'j9', status: 'ok', message: '취소를 요청했습니다.', job_id: null }])
    await mount([running, finished])

    await userEvent.click(screen.getByRole('checkbox', { name: '이 쪽 전부 선택' }))
    await userEvent.click(screen.getByRole('button', { name: /선택 취소/ }))
    const dialog = screen.getByRole('dialog')
    await userEvent.click(within(dialog).getByRole('button', { name: '취소 요청' }))
    await waitFor(() => expect(jobsApi.cancelMany).toHaveBeenCalledWith(['j9']))
  })
})
