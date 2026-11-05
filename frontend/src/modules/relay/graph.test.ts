/**
 * 지도 읽기 — 겹친 선을 한 길로 합치고, 거르기는 **그 길에 있는 것만** 남긴다.
 */

import { describe, expect, it } from 'vitest'

import {
  countsLine,
  driftCount,
  driftOf,
  fixableCount,
  isPlanJob,
  reconcileStatus,
  reportTruncated,
} from '@/modules/relay/api'
import type { RelayMap } from '@/modules/relay/api'
import { fullReconcile, sampleMap, sampleReconcile, withDraft } from '@/modules/relay/fixtures'
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

  it('초안도 상태 거르기에 서버의 말로 서고, 거르면 그 길만 남는다', () => {
    const drafted = buildGraph(withDraft(sampleMap()))
    expect(statusOptions(drafted)).toContainEqual({ value: 'draft', label: '초안(미사용)' })
    const visible = visibleIds(drafted, { ...NO_FILTER, status: 'draft' })!
    expect(visible.has('pipeline:plm-parts')).toBe(true)
    expect(visible.has('endpoint:parts')).toBe(true)
    expect(visible.has('target:hub')).toBe(true)
    expect(visible.has('pipeline:plm-tasks')).toBe(false)
    // 초안은 아직 아무것도 안 보낸다 — 같은 대상의 남의 타입은 안 남는다.
    expect(visible.has('type:hub:plm_task')).toBe(false)
  })

  it('건수 한 줄 — 관계가 움직였을 때만 뒤에 붙는다', () => {
    expect(countsLine(undefined)).toBe('')
    expect(
      countsLine({ objects: { a: { created: 2, changed: 1, unchanged: 9, gone: 0 }, b: { created: 1, changed: 0, gone: 1 } } }),
    ).toBe('생성 3 · 변경 1 · 삭제 1')
    expect(
      countsLine({
        objects: { a: { created: 1, changed: 0, gone: 0 } },
        relations: { r: { created: 2, changed: 0, gone: 1 } },
      }),
    ).toBe('생성 1 · 변경 0 · 삭제 0 (관계 생성 2 · 변경 0 · 삭제 1)')
  })
})

describe('대조 읽기', () => {
  it('마디의 어긋남 — 파이프라인만, 0 · 없음은 0', () => {
    const tasks = graph.byId.get('pipeline:plm-tasks')!
    expect(driftOf(tasks)).toEqual({ drift: 3, at: '2026-11-05T03:00:00+00:00', problem: false })
    expect(driftOf(graph.byId.get('pipeline:plm-models')!).drift).toBe(0)
    expect(driftOf(graph.byId.get('pipeline:mes-lots')!)).toEqual({ drift: 0, at: null, problem: false })
    expect(driftOf(graph.byId.get('target:hub')!).drift).toBe(0)
    // 대조하지 못했으면 어긋남 수(0)가 아니라 「대조 못 함」 이다
    const broken = { ...tasks, detail: { ...tasks.detail, drift: 0, reconcile_status: 'problem' } }
    expect(driftOf(broken)).toMatchObject({ drift: 0, problem: true })
  })

  it('보고서 — 참 건수는 counts, 본보기가 잘렸으면 그렇다고, 결은 problems · drift 순', () => {
    const report = sampleReconcile()
    expect(driftCount(report.types.plm_task, 'missing')).toBe(25)
    expect(reportTruncated(report)).toBe(true)
    expect(reportTruncated(fullReconcile())).toBe(false)
    expect(reconcileStatus(report)).toBe('drift')
    expect(reconcileStatus(sampleReconcile({ drift: 0, types: {} }))).toBe('same')
    expect(reconcileStatus(sampleReconcile({ problems: ['대상에 타입이 없습니다: x'] }))).toBe('problem')
    // counts 가 없는 옛 보고서는 목록의 길이로 센다.
    const old = { ...report.types.plm_task, counts: undefined }
    expect(driftCount(old, 'missing')).toBe(20)
    expect(driftCount(old, 'changed')).toBe(1)
  })

  it('보정 계획에 실을 것 — 관리하지 않는 타입의 「대상에만」 은 빠진다', () => {
    // plm_task: 빠짐 25 + 안 씀 1 + 다름 1 + 대상에만 1(관리함) · plm_project: 대상에만 1(관리 안 함).
    expect(fixableCount(sampleReconcile())).toBe(28)
    const report = sampleReconcile()
    delete (report.types as Record<string, unknown>).plm_task
    expect(fixableCount(report)).toBe(0)
  })

  it('대조 작업 중 보정 계획까지 간 것 — 결과 한 줄의 머리로 가른다', () => {
    expect(isPlanJob({ kind: 'relay_reconcile', summary: '[planned] 보정 계획: 고침 2' })).toBe(true)
    expect(isPlanJob({ kind: 'relay_reconcile', summary: '[waiting] 적용을 기다리는 계획' })).toBe(true)
    expect(isPlanJob({ kind: 'relay_reconcile', summary: '[drift] 대상과 어긋남 2건' })).toBe(false)
    expect(isPlanJob({ kind: 'relay_run', summary: '[planned] 계획을 올렸습니다' })).toBe(false)
  })
})
