/**
 * 흐름도의 자리 — **열을 고정하고 줄만 계산한다.** 자동 배치 라이브러리를 안 쓴다.
 *
 * 힘 · 계층 배치는 정의가 한 줄 늘 때마다 그림 전체를 뒤섞는다. 그러면 어제 본 자리에서 오늘
 * 같은 파이프라인을 못 찾는다. 여기서는 열(종류)이 x 를 정하고, 파이프라인의 차례가 y 의 뼈대다:
 *
 *   1. 파이프라인을 원천 시스템 → API 차례로 훑어 줄을 세운다(같은 원천의 것이 모인다).
 *   2. 왼쪽(API · 원천)은 제 파이프라인들의 가운데에, 오른쪽(대상)도 제 파이프라인들의 가운데에.
 *   3. 타입 · 그 너머는 제 대상 둘레에 부채꼴로 편다.
 *   4. 열마다 겹치지 않게 밀고, 민 만큼 다시 가운데로 당긴다.
 *
 * 그래서 한 파이프라인의 원천 · API · 대상은 대개 한 줄에 선다.
 */

import { NODE_KINDS } from '@/modules/relay/api'
import type { MapNode } from '@/modules/relay/api'

export const NODE_WIDTH = 216
export const NODE_HEIGHT = 76
/** 열 사이 — 선이 보일 만큼은 띄운다. */
export const COLUMN_GAP = 64
export const COLUMN_WIDTH = NODE_WIDTH + COLUMN_GAP
/** 한 열 안에서 마디 위끝과 다음 마디 위끝 사이. */
export const ROW_GAP = NODE_HEIGHT + 20
/** 열 제목이 첫 마디 위로 이만큼. */
export const HEADER_OFFSET = 40

export const COLUMN_TITLE: Record<string, string> = {
  system: '원천 시스템',
  endpoint: 'API',
  pipeline: '파이프라인',
  target: '대상 SP',
  type: '타입',
  downstream: '그 너머',
}

export interface Point {
  x: number
  y: number
}

interface Link {
  source: string
  target: string
}

/** 모르는 종류는 맨 오른쪽 열 하나에 모은다 — 버리면 서버가 새로 낸 것이 안 보인다. */
export function columnOf(kind: string): number {
  const at = (NODE_KINDS as readonly string[]).indexOf(kind)
  return at === -1 ? NODE_KINDS.length : at
}

const PIPELINE = columnOf('pipeline')

function neighbours(links: Link[], known: Set<string>) {
  const out = new Map<string, string[]>()
  const into = new Map<string, string[]>()
  const add = (map: Map<string, string[]>, key: string, value: string) => {
    const list = map.get(key) ?? []
    if (!list.includes(value)) list.push(value)
    map.set(key, list)
  }
  for (const one of links) {
    if (!known.has(one.source) || !known.has(one.target)) continue
    add(out, one.source, one.target)
    add(into, one.target, one.source)
  }
  return { out, into }
}

const mean = (values: number[]) => values.reduce((sum, one) => sum + one, 0) / values.length

/**
 * 한 열을 겹치지 않게 — `order` 차례로 세우고 아래로 밀어 띄운 뒤, 민 평균만큼 다시 올린다
 * (아래로만 밀면 긴 열이 통째로 처진다).
 */
function settle(order: { id: string; want: number }[], placed: Map<string, number>) {
  if (order.length === 0) return
  const ys: number[] = []
  for (const [at, one] of order.entries()) {
    ys.push(at === 0 ? one.want : Math.max(one.want, ys[at - 1] + ROW_GAP))
  }
  const drift = mean(ys.map((y, at) => y - order[at].want))
  for (const [at, one] of order.entries()) placed.set(one.id, ys[at] - drift)
}

/** 바라는 y 차례 — 같으면 서버가 준 차례. */
const byWant = (a: { want: number; index: number }, b: { want: number; index: number }) =>
  a.want - b.want || a.index - b.index

/** 파이프라인의 차례 — 원천 시스템 차례 › 그 API 차례 › 선이 온 차례. 길이 없는 것은 뒤에. */
function pipelineOrder(nodes: MapNode[], out: Map<string, string[]>, kindOf: Map<string, string>) {
  const order: string[] = []
  const seen = new Set<string>()
  const visit = (id: string) => {
    const column = columnOf(kindOf.get(id) ?? '')
    if (column > PIPELINE || seen.has(id)) return
    seen.add(id)
    if (column === PIPELINE) {
      order.push(id)
      return
    }
    for (const next of out.get(id) ?? []) visit(next)
  }
  for (const one of nodes) if (columnOf(one.kind) === 0) visit(one.id)
  for (const one of nodes) if (columnOf(one.kind) < PIPELINE) visit(one.id)
  for (const one of nodes) if (one.kind === 'pipeline' && !seen.has(one.id)) order.push(one.id)
  return order
}

/**
 * 마디마다 왼쪽 위 자리. `nodes` 의 차례는 서버가 준 그대로 둔다(같은 정의면 같은 그림).
 */
export function layout(nodes: MapNode[], links: Link[]): Map<string, Point> {
  const kindOf = new Map(nodes.map((one) => [one.id, one.kind]))
  const { out, into } = neighbours(links, new Set(kindOf.keys()))
  const columns = new Map<number, string[]>()
  for (const one of nodes) {
    const column = columnOf(one.kind)
    columns.set(column, [...(columns.get(column) ?? []), one.id])
  }
  const y = new Map<string, number>()

  // 1. 뼈대 — 파이프라인 줄.
  for (const [index, id] of pipelineOrder(nodes, out, kindOf).entries()) y.set(id, index * ROW_GAP)

  // 2. 왼쪽 — 오른쪽 이웃(이미 자리 잡은 것)의 가운데로. 이웃이 없으면 열 맨 아래.
  const bottom = () => Math.max(-ROW_GAP, ...y.values()) + ROW_GAP
  for (let column = PIPELINE - 1; column >= 0; column -= 1) {
    const order = (columns.get(column) ?? []).map((id, index) => {
      const right = (out.get(id) ?? []).filter((one) => y.has(one)).map((one) => y.get(one)!)
      return { id, index, want: right.length ? mean(right) : bottom() }
    })
    settle(order.sort(byWant), y)
  }

  // 3. 오른쪽 — 대상은 제 파이프라인들의 가운데, 그 뒤는 제 왼쪽 이웃 둘레에 부채꼴로. 부채는
  //    **형제끼리 붙여 세운다** — 바라는 y 로만 줄 세우면 이웃한 두 대상의 타입이 엇갈려 선이
  //    꼬인다.
  const maxColumn = Math.max(PIPELINE, ...columns.keys())
  for (let column = PIPELINE + 1; column <= maxColumn; column += 1) {
    const fan = column > PIPELINE + 1
    const order = (columns.get(column) ?? []).map((id, index) => {
      const left = (into.get(id) ?? []).filter((one) => y.has(one))
      if (left.length === 0) return { id, index, want: bottom(), group: Infinity, at: index }
      if (!fan) {
        const want = mean(left.map((one) => y.get(one)!))
        return { id, index, want, group: want, at: index }
      }
      const parent = left[0]
      const siblings = (out.get(parent) ?? []).filter((one) => kindOf.get(one) === kindOf.get(id))
      const at = siblings.indexOf(id)
      const want = y.get(parent)! + (at - (siblings.length - 1) / 2) * ROW_GAP
      return { id, index, want, group: y.get(parent)!, at }
    })
    settle(
      order.sort((a, b) => a.group - b.group || a.at - b.at || a.index - b.index),
      y,
    )
  }

  // 위끝을 0 으로 — 열 제목이 그 위에 선다.
  const top = Math.min(...y.values())
  const placed = new Map<string, Point>()
  for (const one of nodes) {
    placed.set(one.id, { x: columnOf(one.kind) * COLUMN_WIDTH, y: (y.get(one.id) ?? 0) - top })
  }
  return placed
}

/** 마디가 하나라도 있는 열의 제목 자리. */
export function columnHeaders(nodes: MapNode[]): { kind: string; title: string; x: number }[] {
  const kinds = [...new Set(nodes.map((one) => one.kind))]
  return kinds
    .sort((a, b) => columnOf(a) - columnOf(b))
    .map((kind) => ({
      kind,
      title: COLUMN_TITLE[kind] ?? kind,
      x: columnOf(kind) * COLUMN_WIDTH,
    }))
}

/** 선 굵기 — 지난 실행에서 그 길로 보낸 건수. 0 이면 1, 만 건이어도 그림을 덮지 않게 상한. */
export function edgeWidth(weight: number): number {
  return Math.min(8, 1 + Math.log2(1 + Math.max(0, weight)))
}
