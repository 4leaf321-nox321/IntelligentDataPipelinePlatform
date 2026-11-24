/**
 * 파이프라인 목록의 운영 현황 — 확인 필요 판정 · 상태별 건수 · 검색 · 정렬. 순수 함수다.
 *
 * 파이프라인이 수십 개를 넘으면 정상인 것까지 한 표에 늘어놓은 목록은 읽히지 않는다. 그래서 기본 보기는
 * **확인이 필요한 것**만 보이고, 정상은 건수로만 남긴다(2026-11-24). 지도와 같은 응답(`/relay/map`)의
 * 파이프라인 행을 읽으므로 상태 판정은 지도 · 배지와 같다.
 */

import { driftOf, pipelineDetailOf } from '@/modules/relay/api'
import type { PipelineRow } from '@/modules/relay/graph'

/** 사람이 확인해야 하는 상태 — 정상 · 미실행 · 초안은 해당하지 않는다. */
export const ATTENTION_STATUSES = ['failed', 'broken', 'blocked', 'late', 'stopped', 'pending'] as const

/** 상태 칩 · 문제 우선 정렬의 차례 — 앞이 더 급하다. */
const STATUS_ORDER = [...ATTENTION_STATUSES, 'never', 'draft', 'ok']

const orderOf = (status: string) => {
  const index = STATUS_ORDER.indexOf(status)
  return index < 0 ? STATUS_ORDER.length : index
}

/** 확인 필요 — 상태가 문제이거나, 정상이어도 마지막 대조가 불일치 · 대조 실패. */
export function needsAttention(row: PipelineRow): boolean {
  if ((ATTENTION_STATUSES as readonly string[]).includes(row.node.status ?? '')) return true
  const drift = driftOf(row.node)
  return drift.drift > 0 || drift.problem
}

/** 확인이 필요한 까닭 한 줄 — 상태 라벨 외에 무엇을 볼지. 없으면 빈 문자열. */
export function attentionReason(row: PipelineRow): string {
  const detail = pipelineDetailOf(row.node)
  const drift = driftOf(row.node)
  const reasons: string[] = []
  if (detail.late) reasons.push(detail.late)
  if (detail.held?.length) reasons.push(...detail.held)
  if (detail.problems?.length) reasons.push(...detail.problems)
  if (drift.problem) reasons.push('대조 실패')
  else if (drift.drift) reasons.push(`대상 불일치 ${drift.drift.toLocaleString()}건`)
  return reasons.join(' · ')
}

export interface StatusCount {
  value: string
  label: string
  count: number
}

/** 상태별 건수 — 지금 목록에 있는 상태만, 급한 차례로, 서버가 붙인 라벨로. */
export function statusCounts(rows: PipelineRow[]): StatusCount[] {
  const found = new Map<string, StatusCount>()
  for (const { node } of rows) {
    const value = node.status || 'never'
    const one = found.get(value)
    if (one) one.count += 1
    else found.set(value, { value, label: node.status_label || value, count: 1 })
  }
  return [...found.values()].sort((a, b) => orderOf(a.value) - orderOf(b.value))
}

/** 검색 — 파이프라인 slug · 원천 시스템 · API · 대상의 이름과 주소. 대소문자를 구분하지 않는다. */
export function matchesQuery(row: PipelineRow, query: string): boolean {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return true
  const haystack = [row.node, ...row.systems, ...row.endpoints, ...row.targets]
    .flatMap((one) => [one.label, one.sub ?? ''])
    .join(' ')
    .toLowerCase()
  return words.every((word) => haystack.includes(word))
}

export type SortKey = 'attention' | 'last_run' | 'applied' | 'name'

export const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'attention', label: '문제 우선' },
  { value: 'last_run', label: '최근 실행' },
  { value: 'applied', label: '최근 적용' },
  { value: 'name', label: '이름' },
]

export const isSortKey = (value: string): value is SortKey =>
  SORT_OPTIONS.some((one) => one.value === value)

/** 마지막 실행 · 적용 작업의 시각(서버의 `last_job_at`). 없으면 빈 문자열. */
export const lastRunOf = (row: PipelineRow): string => pipelineDetailOf(row.node).last_job_at ?? ''

const byName = (a: PipelineRow, b: PipelineRow) => a.node.label.localeCompare(b.node.label)
/** 최근 것이 앞 — 시각이 없는 것은 뒤로. ISO 문자열이라 글자 비교가 곧 시각 비교다. */
const byRecent = (pick: (row: PipelineRow) => string) => (a: PipelineRow, b: PipelineRow) =>
  pick(b).localeCompare(pick(a)) || byName(a, b)

export function sortRows(rows: PipelineRow[], key: SortKey): PipelineRow[] {
  const sorted = [...rows]
  if (key === 'name') return sorted.sort(byName)
  if (key === 'last_run') return sorted.sort(byRecent(lastRunOf))
  if (key === 'applied')
    return sorted.sort(byRecent((row) => pipelineDetailOf(row.node).applied_at ?? ''))
  return sorted.sort(
    (a, b) =>
      Number(needsAttention(b)) - Number(needsAttention(a)) ||
      orderOf(a.node.status ?? '') - orderOf(b.node.status ?? '') ||
      byName(a, b),
  )
}

export type BoardView = 'attention' | 'all'

export interface BoardFilter {
  view: BoardView
  /** 빈 문자열이면 모든 상태. */
  status: string
  query: string
  sort: SortKey
}

/** 보기 · 상태 · 검색을 거쳐 정렬한 행. */
export function boardRows(rows: PipelineRow[], filter: BoardFilter): PipelineRow[] {
  const kept = rows.filter(
    (row) =>
      (filter.view === 'all' || needsAttention(row)) &&
      (!filter.status || (row.node.status || 'never') === filter.status) &&
      matchesQuery(row, filter.query),
  )
  return sortRows(kept, filter.sort)
}

/** 보기를 고르지 않았을 때 — 확인이 필요한 것이 있으면 그것만, 없으면 전체. */
export function defaultView(rows: PipelineRow[]): BoardView {
  return rows.some(needsAttention) ? 'attention' : 'all'
}

/** 적용 대기이고 대상에서 적용할 수 있는 계획 — 일괄 적용의 후보. */
export function applicable(row: PipelineRow): boolean {
  return row.node.status === 'pending' && Boolean(pipelineDetailOf(row.node).pending_job)
}
