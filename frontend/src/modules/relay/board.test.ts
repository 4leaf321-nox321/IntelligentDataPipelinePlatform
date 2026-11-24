import { describe, expect, it } from 'vitest'

import type { MapNode, RelayMap } from '@/modules/relay/api'
import {
  applicable,
  attentionReason,
  boardRows,
  defaultView,
  needsAttention,
  statusCounts,
} from '@/modules/relay/board'
import { buildGraph, pipelineRows } from '@/modules/relay/graph'
import { SUMMARY_AT, focusLink, summaryGraph } from '@/modules/relay/summary'

const LABEL: Record<string, string> = {
  ok: '정상',
  pending: '적용 대기',
  failed: '실패',
  blocked: '중단',
  draft: '초안(미사용)',
}

function pipeline(slug: string, status: string, detail: Record<string, unknown> = {}): MapNode {
  return {
    id: `pipeline:${slug}`,
    kind: 'pipeline',
    label: slug,
    sub: '',
    status,
    status_label: LABEL[status] ?? status,
    link: `/relay/pipelines/${slug}`,
    detail,
  }
}

/** PLM 두 API · MES 하나 → 대상 둘. */
function map(): RelayMap {
  const node = (id: string, kind: string, label: string): MapNode => ({
    id,
    kind,
    label,
    sub: '',
    status: '',
    status_label: '',
    link: '',
    detail: {},
  })
  const nodes: MapNode[] = [
    node('system:plm', 'system', 'PLM'),
    node('system:mes', 'system', 'MES'),
    node('endpoint:plm-a', 'endpoint', 'PLM 모델 API'),
    node('endpoint:mes-a', 'endpoint', 'MES 실적 API'),
    node('target:hub', 'target', '자주검증 Datahub'),
    node('target:mfg', 'target', '제조 데이터허브'),
    pipeline('plm_model', 'ok', { applied_at: '2026-11-20T01:00:00Z', last_job_at: '2026-11-24T01:00:00Z' }),
    pipeline('plm_task', 'failed', { last_job_at: '2026-11-24T02:00:00Z' }),
    pipeline('plm_part', 'ok', { drift: 3, applied_at: '2026-11-23T01:00:00Z' }),
    pipeline('mes_result', 'pending', {
      pending_job: 'j1',
      counts: { objects: { mes_result: { created: 2, changed: 0, gone: 1 } } },
    }),
    pipeline('mes_draft', 'draft'),
  ]
  const link = (source: string, target: string, weight = 0) => ({ source, target, weight, status: '' })
  const edges = [
    link('system:plm', 'endpoint:plm-a'),
    link('system:mes', 'endpoint:mes-a'),
    ...['plm_model', 'plm_task', 'plm_part'].map((slug) => link('endpoint:plm-a', `pipeline:${slug}`)),
    ...['mes_result', 'mes_draft'].map((slug) => link('endpoint:mes-a', `pipeline:${slug}`)),
    link('pipeline:plm_model', 'target:hub', 5),
    link('pipeline:plm_task', 'target:hub', 7),
    link('pipeline:plm_part', 'target:hub'),
    link('pipeline:mes_result', 'target:mfg', 3),
  ]
  return { generated_at: '', nodes, edges, problems: [] }
}

const rows = () => pipelineRows(buildGraph(map()))
const slugs = (list: { node: MapNode }[]) => list.map((one) => one.node.label)

describe('파이프라인 운영 현황', () => {
  it('확인 필요는 문제 상태와 대상 불일치 — 정상 · 초안은 아니다', () => {
    expect(slugs(rows().filter(needsAttention))).toEqual(['plm_task', 'plm_part', 'mes_result'])
    expect(defaultView(rows())).toBe('attention')
  })

  it('상태별 건수는 급한 차례로, 서버의 라벨로', () => {
    expect(statusCounts(rows())).toEqual([
      { value: 'failed', label: '실패', count: 1 },
      { value: 'pending', label: '적용 대기', count: 1 },
      { value: 'draft', label: '초안(미사용)', count: 1 },
      { value: 'ok', label: '정상', count: 2 },
    ])
  })

  it('보기 · 상태 · 검색을 거쳐 정렬한다', () => {
    const base = { view: 'attention' as const, status: '', query: '', sort: 'attention' as const }
    expect(slugs(boardRows(rows(), base))).toEqual(['plm_task', 'mes_result', 'plm_part'])
    // 같은 '정상' 이어도 대상 불일치가 있으면 먼저.
    expect(slugs(boardRows(rows(), { ...base, view: 'all', status: 'ok' }))).toEqual([
      'plm_part',
      'plm_model',
    ])
    // 검색은 원천 시스템 · API · 대상의 이름에도 걸린다 — 낱말마다 모두 맞아야 한다.
    expect(slugs(boardRows(rows(), { ...base, view: 'all', query: 'mes 데이터허브' }))).toEqual([
      'mes_result',
    ])
    expect(slugs(boardRows(rows(), { ...base, view: 'all', sort: 'last_run' })).slice(0, 2)).toEqual([
      'plm_task',
      'plm_model',
    ])
    expect(slugs(boardRows(rows(), { ...base, view: 'all', sort: 'applied' })).slice(0, 2)).toEqual([
      'plm_part',
      'plm_model',
    ])
  })

  it('사유와 일괄 적용 후보', () => {
    const byName = new Map(rows().map((one) => [one.node.label, one]))
    expect(attentionReason(byName.get('plm_part')!)).toBe('대상 불일치 3건')
    expect(rows().filter(applicable).map((one) => one.node.label)).toEqual(['mes_result'])
  })
})

describe('지도 요약 보기', () => {
  it('원천 시스템 · 대상의 짝마다 파이프라인을 모은다', () => {
    const graph = summaryGraph(buildGraph(map()), null)
    const groups = graph.nodes.filter((one) => one.id.startsWith('group:'))
    const plm = groups.find((one) => one.id === 'group:system:plm|target:hub')!
    expect(plm.label).toBe('파이프라인 3개')
    expect(plm.sub).toBe('확인 필요 2건')
    expect(plm.status).toBe('failed') // 가장 급한 상태
    expect(plm.link).toBe(focusLink('system:plm', 'target:hub'))
    // 대상이 없는 초안은 원천 시스템 쪽 그룹으로만.
    expect(groups.map((one) => one.id).sort()).toEqual([
      'group:system:mes|',
      'group:system:mes|target:mfg',
      'group:system:plm|target:hub',
    ])
    const toHub = graph.edges.find((one) => one.source === plm.id && one.target === 'target:hub')!
    expect(toHub.parts.reduce((sum, one) => sum + one.weight, 0)).toBe(12)
    expect(toHub.status).toBe('failed')
    expect(graph.nodes.some((one) => one.kind === 'endpoint')).toBe(false)
  })

  it('필터에 남은 파이프라인만 모은다', () => {
    const graph = buildGraph(map())
    const visible = new Set(['pipeline:plm_model', 'system:plm', 'endpoint:plm-a', 'target:hub'])
    const groups = summaryGraph(graph, visible).nodes.filter((one) => one.id.startsWith('group:'))
    expect(groups.map((one) => [one.label, one.sub])).toEqual([['파이프라인 1개', '모두 정상']])
    expect(SUMMARY_AT).toBeGreaterThan(0)
  })
})
