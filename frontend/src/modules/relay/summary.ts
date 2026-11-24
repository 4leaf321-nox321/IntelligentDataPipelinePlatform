/**
 * 연결 지도의 요약 보기 — 원천 시스템 → 파이프라인 그룹 → 대상. 순수 함수다.
 *
 * 파이프라인이 많으면 전체 흐름도는 선이 엉켜 읽히지 않는다(2026-11-24). 요약 보기는 원천 시스템과 대상의
 * 짝마다 파이프라인을 노드 하나로 모으고, 그 노드에 파이프라인 수 · 확인 필요 수 · 가장 급한 상태를 싣는다.
 * 노드를 클릭하면 그 짝으로 필터한 전체 보기로 간다(초점 보기).
 *
 * 그룹 노드는 `kind: 'pipeline'` 이다 — 같은 열에 놓이고 같은 상태 배지 · 선 색을 쓴다. 대상으로 가는 선의
 * 굵기는 그룹에 든 파이프라인 몫의 합이다.
 */

import { ATTENTION_STATUSES, needsAttention } from '@/modules/relay/board'
import type { MapEdge, MapNode } from '@/modules/relay/api'
import { buildGraph, edgeWeight, nodesOfKind, pipelineRow } from '@/modules/relay/graph'
import type { PipelineRow, RelayGraph } from '@/modules/relay/graph'

/** 파이프라인이 이보다 많으면 지도의 기본 보기는 요약이다. */
export const SUMMARY_AT = 30

const URGENT = [...ATTENTION_STATUSES, 'never', 'draft', 'ok'] as readonly string[]

/** 가장 급한 상태의 노드 — 그 상태와 라벨을 그룹이 든다. */
function worst(rows: PipelineRow[]): MapNode {
  const urgency = (status: string) => {
    const index = URGENT.indexOf(status)
    return index < 0 ? URGENT.length : index
  }
  return rows
    .map((row) => row.node)
    .reduce((a, b) => (urgency(b.status ?? '') < urgency(a.status ?? '') ? b : a))
}

/** 그룹을 클릭했을 때 갈 곳 — 그 원천 시스템 · 대상으로 필터한 전체 보기. */
export function focusLink(systemId: string, targetId: string): string {
  const params = new URLSearchParams({ view: 'full' })
  if (systemId) params.set('system', systemId)
  if (targetId) params.set('target', targetId)
  return `/relay/map?${params.toString()}`
}

/**
 * 요약 그래프 — `visible`(필터에 남은 노드)이 있으면 그 안의 파이프라인만 모은다.
 * 대상이 없는 파이프라인(초안 등)은 원천 시스템 쪽 그룹으로만 남는다.
 */
export function summaryGraph(graph: RelayGraph, visible: ReadonlySet<string> | null): RelayGraph {
  const groups = new Map<string, { system: MapNode | null; target: MapNode | null; rows: PipelineRow[] }>()
  for (const node of nodesOfKind(graph, 'pipeline')) {
    if (visible && !visible.has(node.id)) continue
    const row = pipelineRow(graph, node)
    const systems: (MapNode | null)[] = row.systems.length ? row.systems : [null]
    const targets: (MapNode | null)[] = row.targets.length ? row.targets : [null]
    for (const system of systems) {
      for (const target of targets) {
        const key = `${system?.id ?? ''}|${target?.id ?? ''}`
        const found = groups.get(key)
        if (found) found.rows.push(row)
        else groups.set(key, { system, target, rows: [row] })
      }
    }
  }

  const nodes = new Map<string, MapNode>()
  const edges: MapEdge[] = []
  for (const [key, { system, target, rows }] of groups) {
    const urgent = worst(rows)
    const attention = rows.filter(needsAttention).length
    const id = `group:${key}`
    nodes.set(id, {
      id,
      kind: 'pipeline',
      label: `파이프라인 ${rows.length.toLocaleString()}개`,
      sub: attention ? `확인 필요 ${attention.toLocaleString()}건` : '모두 정상',
      status: urgent.status,
      status_label: urgent.status_label,
      link: focusLink(system?.id ?? '', target?.id ?? ''),
      detail: { members: rows.map((row) => row.node.label) },
    })
    if (system) {
      nodes.set(system.id, system)
      for (const row of rows)
        edges.push({ source: system.id, target: id, status: row.node.status ?? '', weight: 0 })
    }
    if (target) {
      nodes.set(target.id, target)
      for (const row of rows) {
        const sent = graph.edges.find((one) => one.source === row.node.id && one.target === target.id)
        edges.push({
          source: id,
          target: target.id,
          status: row.node.status ?? '',
          weight: sent ? edgeWeight(sent) : 0,
        })
      }
    }
  }
  return buildGraph({ generated_at: '', nodes: [...nodes.values()], edges, problems: [] })
}
