/**
 * 자료 이동(부서 통폐합) 창 — **작업이다**(2026-10-10). 먼저 계획(무엇이 몇 건 · 무엇이 막히나)을
 * 보고, 적용은 그 계획의 작업으로 간다. 막히는 것이 있으면 적용이 잠기고, 도는 동안은 진행률과
 * 「이동 취소」 가 선다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Job } from '@/modules/jobs/api'
import type { Workspace } from '@/modules/workspaces/api'

const workspaceApi = vi.hoisted(() => ({ contents: vi.fn(), reassign: vi.fn() }))
vi.mock('@/modules/workspaces/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/workspaces/api')>()),
  workspaceApi,
}))
const jobsApi = vi.hoisted(() => ({ waitFor: vi.fn(), apply: vi.fn(), cancel: vi.fn() }))
vi.mock('@/modules/jobs/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/jobs/api')>()),
  jobsApi,
}))
// 부서 피커는 포털 · 검색이 있는 컴포넌트다 — 여기서 보는 것은 창의 흐름이라 고르기만 한다.
vi.mock('@/shared/components/SearchablePicker', () => ({
  SearchablePicker: ({
    id,
    options,
    value,
    onChange,
  }: {
    id: string
    options: { value: string; label: string }[]
    value: string | null
    onChange: (value: string) => void
  }) => (
    <select id={id} value={value ?? ''} onChange={(event) => onChange(event.target.value)}>
      <option value="">선택</option>
      {options.map((one) => (
        <option key={one.value} value={one.value}>
          {one.label}
        </option>
      ))}
    </select>
  ),
}))

function space(slug: string, name: string): Workspace {
  return {
    id: slug,
    slug,
    name,
    description: '',
    parent_slug: null,
    depth: 0,
    path: name,
    sort_order: 0,
    is_active: true,
    created_at: '',
    member_count: 1,
    my_role: 'manager',
  }
}

const SOURCE = space('old', '없어지는 팀')
const ALL = [SOURCE, space('new', '합쳐지는 팀')]

function plan(over: Record<string, unknown> = {}) {
  return {
    reassign: true,
    applied: false,
    ok: true,
    source_slug: 'old',
    source_name: '없어지는 팀',
    target_slug: 'new',
    target_name: '합쳐지는 팀',
    items: [
      { kind: 'members', label: '멤버', count: 2 },
      { kind: 'objects', label: '객체', count: 5 },
    ],
    total: 7,
    moved: {},
    errors: [],
    summary: '계획 — 이동할 것: 멤버 2 · 객체 5 (없어지는 팀 → 합쳐지는 팀)',
    ...over,
  }
}

function job(id: string, status: Job['status'], result: unknown = null, error?: string): Job {
  return {
    id,
    kind: 'workspace_reassign',
    kind_label: '부서 자료 이동',
    status,
    params: {},
    progress: { stage: '', done: 0, total: 0 },
    result: result as Job['result'],
    error: error ?? null,
    parent_id: null,
    input_file_name: null,
    has_output: false,
    requested_by_name: '관리자',
    workspace_slug: null,
    cancel_requested: false,
    attempts: 1,
    created_at: '',
    started_at: null,
    finished_at: null,
  }
}

async function open() {
  workspaceApi.contents.mockResolvedValue([
    { kind: 'members', label: '멤버', count: 2 },
    { kind: 'objects', label: '객체', count: 5 },
    { kind: 'saved_views', label: '저장된 뷰', count: 0 },
  ])
  const onDone = vi.fn()
  const { WorkspaceReassignDialog } = await import('@/modules/workspaces/WorkspaceReassignDialog')
  render(<WorkspaceReassignDialog source={SOURCE} all={ALL} onClose={vi.fn()} onDone={onDone} />)
  await screen.findByText('객체 5건')
  await userEvent.selectOptions(screen.getByLabelText('받을 부서'), 'new')
  return onDone
}

describe('자료 이동', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('먼저 계획을 보고, 적용은 그 계획의 작업으로 간다', async () => {
    const onDone = await open()
    workspaceApi.reassign.mockResolvedValue(job('p1', 'queued'))
    jobsApi.waitFor.mockResolvedValueOnce(job('p1', 'done', plan()))
    await userEvent.click(screen.getByRole('button', { name: '미리 보기 — 7건' }))
    await waitFor(() =>
      expect(workspaceApi.reassign).toHaveBeenCalledWith('old', 'new', ['members', 'objects']),
    )
    expect(await screen.findByText(/계획\(아직 아무것도 이동하지 않았습니다\)/)).toBeInTheDocument()
    expect(onDone).not.toHaveBeenCalled()

    jobsApi.apply.mockResolvedValue(job('a1', 'queued'))
    jobsApi.waitFor.mockImplementationOnce(async (_id: string, onTick?: (one: Job) => void) => {
      const running = job('a1', 'running')
      running.progress = { stage: '객체 이동', done: 4, total: 7 }
      onTick?.(running)
      return job(
        'a1',
        'done',
        plan({
          applied: true,
          moved: { members: 2, objects: 5 },
          items: [
            { kind: 'members', label: '멤버', count: 2, moved: 2 },
            { kind: 'objects', label: '객체', count: 5, moved: 5 },
          ],
        }),
      )
    })
    await userEvent.click(screen.getByRole('button', { name: '적용 — 7건 이동' }))
    await waitFor(() => expect(jobsApi.apply).toHaveBeenCalledWith('p1'))
    expect(await screen.findByText('합쳐지는 팀 으로 옮겼습니다.')).toBeInTheDocument()
    expect(onDone).toHaveBeenCalled()
  })

  it('막히는 것이 있으면 그 까닭을 보이고 적용이 잠긴다', async () => {
    await open()
    workspaceApi.reassign.mockResolvedValue(job('p1', 'queued'))
    jobsApi.waitFor.mockResolvedValueOnce(
      job(
        'p1',
        'done',
        plan({ ok: false, errors: ['객체: 옮겨 갈 부서에 같은 것이 이미 있어 … 「K-1」'] }),
      ),
    )
    await userEvent.click(screen.getByRole('button', { name: '미리 보기 — 7건' }))
    expect(await screen.findByText(/「K-1」/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '적용 — 7건 이동' })).toBeDisabled()
  })

  it('도는 동안 진행을 보이고 취소하면 아무것도 옮기지 않았다고 말한다', async () => {
    await open()
    workspaceApi.reassign.mockResolvedValue(job('p1', 'queued'))
    jobsApi.waitFor.mockResolvedValueOnce(job('p1', 'done', plan()))
    await userEvent.click(screen.getByRole('button', { name: '미리 보기 — 7건' }))
    await screen.findByRole('button', { name: '적용 — 7건 이동' })

    let finish: (value: Job) => void = () => {}
    jobsApi.apply.mockResolvedValue(job('a1', 'queued'))
    jobsApi.waitFor.mockImplementationOnce((_id: string, onTick?: (one: Job) => void) => {
      const running = job('a1', 'running')
      running.progress = { stage: '객체 이동', done: 2, total: 7 }
      onTick?.(running)
      return new Promise<Job>((resolve) => {
        finish = resolve
      })
    })
    await userEvent.click(screen.getByRole('button', { name: '적용 — 7건 이동' }))
    expect(await screen.findByText('객체 이동 2 / 7건')).toBeInTheDocument()
    jobsApi.cancel.mockResolvedValue(job('a1', 'running'))
    await userEvent.click(screen.getByRole('button', { name: '이동 취소' }))
    await waitFor(() => expect(jobsApi.cancel).toHaveBeenCalledWith('a1'))
    finish(job('a1', 'cancelled'))
    expect(
      await screen.findByText('취소했습니다 — 아무것도 이동하지 않았습니다.'),
    ).toBeInTheDocument()
  })
})
