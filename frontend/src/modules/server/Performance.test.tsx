/**
 * 느린 요청 · 큰 응답(ADR 0026) — 작업 목록 한 번이 93MB · 4.4초였던 것을 사용자가 말하고 나서야
 * 알았다(2026-10-09). 화면은 경로 틀마다 묶은 수를 보이고, 문턱을 넘은 줄에 표시를 달고, 줄을
 * 클릭하면 그 경로의 최악 사례(누가 · 언제 · 요청 ID)를 연다. 비었으면 **왜 비었는지** 말한다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/shared/api/client', () => ({ api: client }))

import {
  Performance,
  periodOf,
  shapeParts,
  shownBytes,
  shownMs,
  sortOf,
} from '@/modules/server/Performance'
import type {
  CutQuestions,
  JobStats,
  RequestCase,
  RequestStat,
  RequestStats,
} from '@/modules/server/Performance'

function stat(extra: Partial<RequestStat> = {}): RequestStat {
  return {
    method: 'GET',
    route: '/api/jobs',
    calls: 1200,
    p50_ms: 850,
    p95_ms: 4400,
    max_ms: 6100,
    avg_bytes: 40 * 1024 ** 2,
    max_bytes: 93 * 1024 ** 2,
    file: false,
    slow: true,
    large: true,
    ...extra,
  }
}

function stats(extra: Partial<RequestStats> = {}): RequestStats {
  return {
    period: '24h',
    since: '2026-10-09T06:00:00Z',
    capped: false,
    first_recorded_at: '2026-10-01T00:00:00Z',
    thresholds: { p95_ms: 3000, min_calls: 5, response_bytes: 5 * 1024 ** 2 },
    items: [
      stat(),
      stat({
        method: 'GET',
        route: '/api/attachments/{attachment_id}/download',
        calls: 3,
        p95_ms: 9000,
        file: true,
        slow: false,
        large: false,
      }),
    ],
    total: 2,
    limit: 50,
    offset: 0,
    ...extra,
  }
}

const CASES: RequestCase[] = [
  {
    created_at: '2026-10-09T05:00:00Z',
    user_label: '김운영',
    path: '/api/jobs',
    status_code: 200,
    duration_ms: 6100,
    response_bytes: 93 * 1024 ** 2,
    content_type: 'application/json',
    request_id: 'a1b2c3d4e5f6',
  },
]

const JOBS: JobStats = {
  period: '24h',
  since: '2026-10-09T06:00:00Z',
  items: [
    {
      kind: 'objects_import',
      label: '객체 일괄 입력',
      done: 12,
      failed: 1,
      cancelled: 0,
      open: 2,
      run_p50_s: 4.2,
      run_p95_s: 75,
      run_max_s: 130,
      wait_p50_s: 0.4,
      wait_p95_s: 2,
      wait_max_s: 3,
      queued_oldest_s: 600,
    },
  ],
}

const NO_CUT: CutQuestions = { period: '24h', since: '2026-10-09T06:00:00Z', items: [], total: 0 }

const CUT: CutQuestions = {
  period: '24h',
  since: '2026-10-09T06:00:00Z',
  total: 2,
  items: [
    {
      kind: 'timeout',
      kind_label: '시간 상한',
      code: 'APP-COMMON-0504',
      method: 'GET',
      route: '/api/objects/{type_slug}/summary',
      shape: {
        type: 'svc_case',
        group_by: 'ref.model.ref.task.ref.project.label',
        conditions: ['ref.parts.label eq'],
      },
      count: 7,
      users: 3,
      last_at: '2026-10-09T05:00:00Z',
      last_request_id: 'f00dcafe1234',
    },
    {
      kind: 'records',
      kind_label: '빠진 기록 타입',
      code: 'APP-RDF-0006',
      method: 'POST',
      route: '/api/rdf/query',
      shape: { types: [], query: 'SELECT ?x WHERE { ?x a sp:svc_case }' },
      count: 1,
      users: 1,
      last_at: '2026-10-09T04:00:00Z',
      last_request_id: null,
    },
  ],
}

function answer(requests: RequestStats = stats(), jobs: JobStats = JOBS, cut = NO_CUT) {
  client.get.mockImplementation((url: string) => {
    if (url.startsWith('/server/performance/cut')) return Promise.resolve(cut)
    if (url.startsWith('/server/performance/requests/cases')) return Promise.resolve(CASES)
    if (url.startsWith('/server/performance/requests')) return Promise.resolve(requests)
    if (url.startsWith('/server/performance/jobs')) return Promise.resolve(jobs)
    return Promise.reject(new Error(`모르는 주소: ${url}`))
  })
}

function calls(prefix: string): string[] {
  return client.get.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith(prefix))
}

describe('느린 요청 · 큰 응답', () => {
  beforeEach(() => {
    client.get.mockReset()
  })

  it('경로 틀마다 수를 보이고, 문턱을 넘은 줄에 표시를 단다', async () => {
    answer()
    render(
      <MemoryRouter>
        <Performance />
      </MemoryRouter>,
    )
    const row = (await screen.findByRole('button', { name: '/api/jobs' })).closest('tr')
    expect(row).not.toBeNull()
    const cells = within(row as HTMLElement)
    expect(cells.getByText('1,200')).toBeInTheDocument()
    expect(cells.getByText('850ms')).toBeInTheDocument()
    expect(cells.getByText('4.4초')).toBeInTheDocument()
    expect(cells.getByText('93.0MB')).toBeInTheDocument()
    expect(cells.getByText('느림')).toBeInTheDocument()
    expect(cells.getByText('큼')).toBeInTheDocument()

    // 파일 응답은 느려도 「느림」 이 아니다 — 대신 파일이라고 말한다.
    const file = screen
      .getByRole('button', { name: '/api/attachments/{attachment_id}/download' })
      .closest('tr') as HTMLElement
    expect(within(file).getByText('파일')).toBeInTheDocument()
    expect(within(file).queryByText('느림')).toBeNull()

    expect(calls('/server/performance/requests')[0]).toBe(
      '/server/performance/requests?period=24h&sort=p95&limit=50&offset=0',
    )
    // 작업 종류마다 — 이름 · 소요 · 아직 대기인 것.
    expect(await screen.findByText('객체 일괄 입력')).toBeInTheDocument()
    expect(screen.getByText('1분 15초')).toBeInTheDocument()
    expect(screen.getByText('가장 오래 대기 10분')).toBeInTheDocument()
  })

  it('줄을 클릭하면 그 경로의 최악 사례(누가 · 요청 ID)를 연다', async () => {
    answer()
    render(
      <MemoryRouter>
        <Performance />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '/api/jobs' }))
    expect(await screen.findByText('김운영')).toBeInTheDocument()
    expect(screen.getByText('a1b2c3d4e5f6')).toBeInTheDocument()
    expect(calls('/server/performance/requests/cases')).toEqual([
      '/server/performance/requests/cases?route=%2Fapi%2Fjobs&method=GET&period=24h&order=duration',
    ])
    // 다시 클릭하면 닫힌다.
    await userEvent.click(screen.getByRole('button', { name: '/api/jobs' }))
    await waitFor(() => expect(screen.queryByText('김운영')).toBeNull())
  })

  it('큰 순 · 기간을 바꾸면 다시 세고, 최악 사례도 큰 순으로 연다', async () => {
    answer()
    render(
      <MemoryRouter>
        <Performance />
      </MemoryRouter>,
    )
    await screen.findByRole('button', { name: '/api/jobs' })
    await userEvent.click(screen.getByRole('button', { name: '7일' }))
    await waitFor(() =>
      expect(calls('/server/performance/jobs')).toContain('/server/performance/jobs?period=7d'),
    )
    await userEvent.click(screen.getByRole('button', { name: '큰 순' }))
    await waitFor(() =>
      expect(calls('/server/performance/requests?')).toContain(
        '/server/performance/requests?period=7d&sort=max_bytes&limit=50&offset=0',
      ),
    )
    expect(screen.getByRole('button', { name: '큰 순' })).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(await screen.findByRole('button', { name: '/api/jobs' }))
    await screen.findByText('김운영')
    expect(calls('/server/performance/requests/cases').at(-1)).toContain('order=bytes')
  })

  it('비었으면 아직 안 남긴 것인지 그 기간에 없던 것인지 가른다', async () => {
    answer(stats({ items: [], total: 0, first_recorded_at: null }), { ...JOBS, items: [] })
    const { unmount } = render(
      <MemoryRouter>
        <Performance />
      </MemoryRouter>,
    )
    expect(await screen.findByText('아직 기록이 없습니다')).toBeInTheDocument()
    expect(await screen.findByText('이 기간에 들어온 작업이 없습니다')).toBeInTheDocument()
    unmount()

    answer(stats({ items: [], total: 0 }))
    render(
      <MemoryRouter>
        <Performance initialPeriod="1h" />
      </MemoryRouter>,
    )
    expect(await screen.findByText('이 기간에 기록된 요청이 없습니다')).toBeInTheDocument()
  })

  it('기록이 너무 많아 최근 쪽만 셌으면 그렇게 말한다', async () => {
    answer(stats({ capped: true }))
    render(
      <MemoryRouter>
        <Performance initialPeriod="7d" />
      </MemoryRouter>,
    )
    expect(await screen.findByText(/최근 기록만 셌습니다/)).toBeInTheDocument()
  })

  it('끊긴 질문을 모양과 함께 많이 끊긴 것부터 보인다', async () => {
    answer(stats(), JOBS, CUT)
    render(
      <MemoryRouter>
        <Performance />
      </MemoryRouter>,
    )
    const heading = await screen.findByRole('heading', { name: '끊긴 질문' })
    const section = heading.closest('section') as HTMLElement
    // 머리 줄이 먼저 그려진다 — 몸통 둘까지 기다린다(부하가 걸리면 그 사이에 찾기가 끝났다).
    await waitFor(() => expect(within(section).getAllByRole('row')).toHaveLength(3))
    const rows = within(section).getAllByRole('row')
    expect(within(rows[1]).getByText('시간 상한')).toBeInTheDocument()
    expect(within(rows[1]).getByText('ref.model.ref.task.ref.project.label')).toBeInTheDocument()
    expect(within(rows[1]).getByText('ref.parts.label eq')).toBeInTheDocument()
    expect(within(rows[1]).getByText('7')).toBeInTheDocument()
    expect(within(rows[2]).getByText('기본(기록 타입 뺌)')).toBeInTheDocument()
    expect(within(rows[2]).getByText(/sp:svc_case/)).toBeInTheDocument()
  })

  it('끊긴 질문이 없으면 없다고 말한다', async () => {
    answer()
    render(
      <MemoryRouter>
        <Performance />
      </MemoryRouter>,
    )
    expect(await screen.findByText('이 기간에 끊긴 질문이 없습니다')).toBeInTheDocument()
    expect(shapeParts({ q: true, under: true })).toEqual([
      { label: '검색어', value: '있음' },
      { label: '트리', value: '가지 하나' },
    ])
  })

  it('시간 · 크기는 단위를 바꿔 적고, 주소의 모르는 값은 기본으로', () => {
    expect(shownMs(850)).toBe('850ms')
    expect(shownMs(4400)).toBe('4.4초')
    expect(shownMs(42_000)).toBe('42초')
    expect(shownMs(125_000)).toBe('2분 5초')
    expect(shownBytes(512)).toBe('512B')
    expect(shownBytes(12 * 1024)).toBe('12KB')
    expect(shownBytes(5 * 1024 ** 2)).toBe('5.0MB')
    expect(periodOf('7d')).toBe('7d')
    expect(periodOf('30d')).toBe('24h')
    expect(sortOf('max_bytes')).toBe('max_bytes')
    expect(sortOf(null)).toBe('p95')
  })
})
