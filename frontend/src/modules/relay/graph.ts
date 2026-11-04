/**
 * 지도를 읽는 순수 함수들 — 흐름도 · 파이프라인 목록 · 원천 · 대상 표가 **같은 판정**을 쓴다.
 *
 * 서버는 선을 파이프라인마다 따로 낸다(같은 원천 → API 선이 파이프라인 수만큼 온다). 여기서
 * 한 길(`source→target`)로 합치고, 그 선이 **어느 파이프라인의 것인가**를 기억해 둔다 — 거르기로
 * 파이프라인을 가리면 그 몫의 건수도 선 굵기에서 빠져야 한다.
 */

import type { MapEdge, MapNode, RelayMap } from '@/modules/relay/api'

/** 합친 선 하나. `parts` 는 그 길로 간 몫 — `owner` 가 없으면 특정 파이프라인의 것이 아니다. */
export interface FlowEdge {
  id: string
  source: string
  target: string
  status: string
  parts: { owner: string | null; weight: number }[]
}

export interface RelayGraph {
  nodes: MapNode[]
  byId: Map<string, MapNode>
  edges: FlowEdge[]
  /** 오른쪽(받는 쪽) 이웃 — 선이 온 차례대로, 겹치지 않게. */
  out: Map<string, string[]>
  /** 왼쪽(보내는 쪽) 이웃. */
  in: Map<string, string[]>
}

/** 선의 상태 중 무엇이 더 나쁜가 — 합친 선은 가장 나쁜 것을 든다. */
const STATUS_RANK: Record<string, number> = {
  failed: 5,
  broken: 5,
  blocked: 4,
  pending: 3,
  never: 1,
}

const rank = (status: string) => STATUS_RANK[status] ?? 0

function push(map: Map<string, string[]>, key: string, value: string) {
  const list = map.get(key)
  if (!list) map.set(key, [value])
  else if (!list.includes(value)) list.push(value)
}

/**
 * 타입으로 가는 선이 어느 파이프라인의 것인가.
 *
 * 서버(`services.build_map`)는 파이프라인 하나마다 「파이프라인 → 대상」 다음에 그 파이프라인의
 * 「대상 → 타입」 을 잇달아 낸다. 그 차례로 주인을 붙인다 — 차례가 바뀌어 앞의 것을 못 찾으면
 * 주인을 비운다(그 선은 대상을 보는 누구에게나 보인다).
 */
function ownerOf(edge: MapEdge, byId: Map<string, MapNode>, lastInto: Map<string, string>) {
  const from = byId.get(edge.source)
  const to = byId.get(edge.target)
  if (from?.kind === 'pipeline') {
    if (to?.kind === 'target') lastInto.set(edge.target, edge.source)
    return edge.source
  }
  if (to?.kind === 'pipeline') return edge.target
  if (from?.kind === 'target' && to?.kind === 'type') return lastInto.get(edge.source) ?? null
  return null
}

export function buildGraph(map: RelayMap): RelayGraph {
  const nodes = map.nodes
  const byId = new Map(nodes.map((one) => [one.id, one]))
  const merged = new Map<string, FlowEdge>()
  const out = new Map<string, string[]>()
  const into = new Map<string, string[]>()
  const lastInto = new Map<string, string>()
  for (const edge of map.edges) {
    // 마디가 없는 선은 그릴 자리가 없다 — 버린다(서버가 마디를 빼먹은 것이다).
    if (!byId.has(edge.source) || !byId.has(edge.target)) continue
    const id = `${edge.source}->${edge.target}`
    const part = { owner: ownerOf(edge, byId, lastInto), weight: edge.weight ?? 0 }
    const found = merged.get(id)
    if (found) {
      found.parts.push(part)
      if (rank(edge.status ?? '') > rank(found.status)) found.status = edge.status ?? ''
    } else {
      merged.set(id, {
        id,
        source: edge.source,
        target: edge.target,
        status: edge.status ?? '',
        parts: [part],
      })
    }
    push(out, edge.source, edge.target)
    push(into, edge.target, edge.source)
  }
  return { nodes, byId, edges: [...merged.values()], out, in: into }
}

/** 보이는 파이프라인 몫만 센 건수. `visible` 이 없으면 전부. */
export function edgeWeight(edge: FlowEdge, visible?: ReadonlySet<string>): number {
  return edge.parts
    .filter((one) => !visible || one.owner === null || visible.has(one.owner))
    .reduce((sum, one) => sum + one.weight, 0)
}

const ofKind = (graph: RelayGraph, ids: string[] | undefined, kind: string): MapNode[] =>
  (ids ?? []).map((id) => graph.byId.get(id)).filter((one): one is MapNode => one?.kind === kind)

export function nodesOfKind(graph: RelayGraph, kind: string): MapNode[] {
  return graph.nodes.filter((one) => one.kind === kind)
}

/** 파이프라인 한 줄 — 어디서 받아 어디로 보내나. */
export interface PipelineRow {
  node: MapNode
  systems: MapNode[]
  endpoints: MapNode[]
  targets: MapNode[]
}

export function pipelineRow(graph: RelayGraph, node: MapNode): PipelineRow {
  const endpoints = ofKind(graph, graph.in.get(node.id), 'endpoint')
  const systems: MapNode[] = []
  for (const endpoint of endpoints) {
    for (const system of ofKind(graph, graph.in.get(endpoint.id), 'system')) {
      if (!systems.includes(system)) systems.push(system)
    }
  }
  return { node, systems, endpoints, targets: ofKind(graph, graph.out.get(node.id), 'target') }
}

export function pipelineRows(graph: RelayGraph): PipelineRow[] {
  return nodesOfKind(graph, 'pipeline').map((one) => pipelineRow(graph, one))
}

/** 원천 시스템 한 줄 — 그 시스템의 API 와 API 마다의 파이프라인. */
export interface SystemRow {
  node: MapNode
  endpoints: { node: MapNode; pipelines: MapNode[] }[]
}

export function systemRows(graph: RelayGraph): SystemRow[] {
  return nodesOfKind(graph, 'system').map((node) => ({
    node,
    endpoints: ofKind(graph, graph.out.get(node.id), 'endpoint').map((endpoint) => ({
      node: endpoint,
      pipelines: ofKind(graph, graph.out.get(endpoint.id), 'pipeline'),
    })),
  }))
}

/** 대상 SP 한 줄 — 무엇이 보내고, 무슨 타입을 받고, 그 너머 누가 가져가나. */
export interface TargetRow {
  node: MapNode
  pipelines: MapNode[]
  types: MapNode[]
  downstream: MapNode[]
}

export function targetRows(graph: RelayGraph): TargetRow[] {
  return nodesOfKind(graph, 'target').map((node) => ({
    node,
    pipelines: ofKind(graph, graph.in.get(node.id), 'pipeline'),
    types: ofKind(graph, graph.out.get(node.id), 'type'),
    downstream: ofKind(graph, graph.out.get(node.id), 'downstream'),
  }))
}

/** 거르기 — 빈 글이면 그 축은 안 거른다. */
export interface MapFilter {
  system: string
  target: string
  status: string
}

export const NO_FILTER: MapFilter = { system: '', target: '', status: '' }

export function isFiltered(filter: MapFilter): boolean {
  return Boolean(filter.system || filter.target || filter.status)
}

/**
 * 거르기에 남는 마디 — 걸린 파이프라인과 **그 길에 있는 것만** 남긴다.
 *
 * 원천 · API 는 그 파이프라인의 왼쪽, 대상은 오른쪽, 타입은 그 파이프라인이 보내는 것만,
 * 그 너머는 남은 대상의 것. 거르기가 없으면 null(전부).
 */
export function visibleIds(graph: RelayGraph, filter: MapFilter): Set<string> | null {
  if (!isFiltered(filter)) return null
  const kept = pipelineRows(graph).filter(
    (row) =>
      (!filter.system || row.systems.some((one) => one.id === filter.system)) &&
      (!filter.target || row.targets.some((one) => one.id === filter.target)) &&
      (!filter.status || row.node.status === filter.status),
  )
  const visible = new Set<string>()
  for (const row of kept) {
    visible.add(row.node.id)
    for (const one of [...row.systems, ...row.endpoints, ...row.targets]) visible.add(one.id)
  }
  for (const edge of graph.edges) {
    const from = graph.byId.get(edge.source)
    const to = graph.byId.get(edge.target)
    if (from?.kind !== 'target' || !visible.has(from.id)) continue
    if (to?.kind === 'downstream') visible.add(to.id)
    if (to?.kind === 'type' && edge.parts.some((one) => one.owner === null || visible.has(one.owner)))
      visible.add(to.id)
  }
  return visible
}

/** 상태 거르기의 고를 것 — 지금 지도에 있는 상태만, 서버가 붙인 말로. */
export function statusOptions(graph: RelayGraph): { value: string; label: string }[] {
  const seen = new Map<string, string>()
  for (const one of nodesOfKind(graph, 'pipeline')) {
    if (one.status && !seen.has(one.status)) seen.set(one.status, one.status_label || one.status)
  }
  return [...seen].map(([value, label]) => ({ value, label }))
}
