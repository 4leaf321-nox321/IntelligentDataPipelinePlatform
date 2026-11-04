/**
 * 지도 읽기 — 겹친 선을 한 길로 합치고, 거르기는 **그 길에 있는 것만** 남긴다.
 */

import { describe, expect, it } from 'vitest'

import { countsLine } from '@/modules/relay/api'
import type { RelayMap } from '@/modules/relay/api'
import { sampleMap } from '@/modules/relay/fixtures'
import {
  NO_FILTER,
  buildGraph,
  edgeWeight,
  pipelineRows,
  statusOptions,
  systemRows,
  targetRows,
  visibleIds,
} from '@/modules/relay/graph'

const graph = buildGraph(sampleMap())

describe('지도 읽기', () => {
  it('같은 길의 선은 하나로 — 건수는 파이프라인 몫을 더하고, 가리면 그 몫이 빠진다', () => {
    const n = (id: string, kind: string) => ({ id, kind, label: id, sub: '', status: '', status_label: '', link: '', detail: {} })
    const map: RelayMap = {
      generated_at: '',
      nodes: [n('s', 'system'), n('e', 'endpoint'), n('a', 'pipeline'), n('b', 'pipeline'), n('t', 'target'), n('x', 'type')],
      edges: [
        { source: 's', target: 'e', weight: 0, status: '' },
        { source: 'e', target: 'a', weight: 0, status: 'ok' },
        { source: 'a', target: 't', weight: 5, status: 'ok' },
        { source: 't', target: 'x', weight: 5, status: '' },
        { source: 's', target: 'e', weight: 0, status: '' },
        { source: 'e', target: 'b', weight: 0, status: 'failed' },
        { source: 'b', target: 't', weight: 2, status: 'failed' },
        { source: 't', target: 'x', weight: 2, status: '' },
        // 마디가 없는 선은 버린다.
        { source: 't', target: '없음', weight: 1, status: '' },
      ],
    }
    const merged = buildGraph(map)
    expect(merged.edges).toHaveLength(6)
    const toType = merged.edges.find((one) => one.id === 't->x')!
    expect(edgeWeight(toType)).toBe(7)
    expect(edgeWeight(toType, new Set(['b', 't', 'x']))).toBe(2)
    expect(merged.out.get('s')).toEqual(['e'])
  })

  it('거르기가 없으면 전부', () => {
    expect(visibleIds(graph, NO_FILTER)).toBeNull()
  })

  it('원천으로 거르면 그 원천의 길만 — 다른 대상 · 그 너머는 빠진다', () => {
    const visible = visibleIds(graph, { ...NO_FILTER, system: 'system:mes' })!
    expect([...visible].sort()).toEqual(
      ['endpoint:lots', 'pipeline:mes-lots', 'system:mes', 'target:mfg', 'type:mfg:lot'].sort(),
    )
  })

  it('상태로 거르면 그 파이프라인이 보내는 타입만 남는다(같은 대상의 남의 타입은 빠진다)', () => {
    const visible = visibleIds(graph, { ...NO_FILTER, status: 'pending' })!
    expect(visible.has('pipeline:plm-models')).toBe(true)
    expect(visible.has('system:plm')).toBe(true)
    expect(visible.has('type:hub:plm_model')).toBe(true)
    expect(visible.has('type:hub:plm_task')).toBe(false)
    expect(visible.has('pipeline:plm-tasks')).toBe(false)
    // 대상이 남으면 그 너머도 — 그 대상을 바꾸면 영향을 받는 곳이다.
    expect(visible.has('downstream:hub:report')).toBe(true)
  })

  it('표들은 같은 지도에서 — 파이프라인 · 원천 · 대상', () => {
    const models = pipelineRows(graph).find((one) => one.node.label === 'plm-models')!
    expect(models.systems.map((one) => one.label)).toEqual(['PLM'])
    expect(models.targets.map((one) => one.label)).toEqual(['자주검증 Datahub'])

    const plm = systemRows(graph).find((one) => one.node.label === 'PLM')!
    expect(plm.endpoints.map((one) => one.pipelines.map((p) => p.label))).toEqual([
      ['plm-tasks'],
      ['plm-models'],
    ])

    const hub = targetRows(graph).find((one) => one.node.label === '자주검증 Datahub')!
    expect(hub.types.map((one) => one.label)).toEqual(['plm_task', 'plm_project', 'plm_model'])
    expect(hub.downstream.map((one) => one.label)).toEqual(['리포트 아카이브'])
    expect(statusOptions(graph).map((one) => one.label)).toEqual(['정상', '적용 대기', '실패'])
  })

  it('건수 한 줄 — 관계가 움직였을 때만 뒤에 붙는다', () => {
    expect(countsLine(undefined)).toBe('')
    expect(
      countsLine({ objects: { a: { created: 2, changed: 1, unchanged: 9, gone: 0 }, b: { created: 1, changed: 0, gone: 1 } } }),
    ).toBe('새 3 · 바뀜 1 · 무덤 1')
    expect(
      countsLine({
        objects: { a: { created: 1, changed: 0, gone: 0 } },
        relations: { r: { created: 2, changed: 0, gone: 1 } },
      }),
    ).toBe('새 1 · 바뀜 0 · 무덤 0 (관계 새 2 · 바뀜 0 · 무덤 1)')
  })
})
