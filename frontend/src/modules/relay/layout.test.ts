/**
 * 흐름도의 자리 — **열은 종류가, 줄은 파이프라인이 정한다.**
 *
 * 그림이 정의 한 줄에 뒤섞이면 어제 본 자리에서 오늘 같은 파이프라인을 못 찾는다. 같은 파이프라인의
 * 원천 · API · 대상은 한 줄에, 타입은 제 대상 둘레에 서야 한다.
 */

import { describe, expect, it } from 'vitest'

import type { MapNode } from '@/modules/relay/api'
import { sampleMap } from '@/modules/relay/fixtures'
import { COLUMN_WIDTH, ROW_GAP, columnHeaders, columnOf, edgeWidth, layout } from '@/modules/relay/layout'

const map = sampleMap()
const placed = layout(map.nodes, map.edges)
const y = (id: string) => placed.get(id)!.y

describe('흐름도 자리', () => {
  it('열은 종류가 정한다 — 원천 | API | 파이프라인 | 대상 | 타입 | 그 너머', () => {
    for (const one of map.nodes) {
      expect(placed.get(one.id)!.x).toBe(columnOf(one.kind) * COLUMN_WIDTH)
    }
    expect(columnOf('system')).toBe(0)
    expect(columnOf('downstream')).toBe(5)
    // 모르는 종류는 버리지 않고 맨 오른쪽에 모은다.
    expect(columnOf('무엇')).toBe(6)
  })

  it('파이프라인은 원천 차례로 서고, 그 API · 대상이 같은 줄에 선다', () => {
    expect(y('pipeline:plm-tasks')).toBeLessThan(y('pipeline:plm-models'))
    expect(y('pipeline:plm-models')).toBeLessThan(y('pipeline:mes-lots'))
    expect(y('endpoint:tasks')).toBe(y('pipeline:plm-tasks'))
    expect(y('endpoint:lots')).toBe(y('pipeline:mes-lots'))
    expect(y('target:mfg')).toBe(y('pipeline:mes-lots'))
    // 두 파이프라인을 거느린 원천 · 두 파이프라인이 보내는 대상은 그 가운데.
    const middle = (y('pipeline:plm-tasks') + y('pipeline:plm-models')) / 2
    expect(y('system:plm')).toBe(middle)
    expect(y('target:hub')).toBe(middle)
  })

  it('한 열 안에서 겹치지 않고, 맨 위가 0 이다', () => {
    const columns = new Map<number, number[]>()
    for (const one of map.nodes) {
      const column = columnOf(one.kind)
      columns.set(column, [...(columns.get(column) ?? []), y(one.id)])
    }
    for (const ys of columns.values()) {
      const sorted = [...ys].sort((a, b) => a - b)
      for (let at = 1; at < sorted.length; at += 1) {
        expect(sorted[at] - sorted[at - 1]).toBeGreaterThanOrEqual(ROW_GAP)
      }
    }
    expect(Math.min(...[...placed.values()].map((one) => one.y))).toBe(0)
  })

  it('타입은 제 대상 둘레에 형제끼리 붙어 선다 — 이웃 대상의 것과 엇갈리지 않게', () => {
    const hub = ['type:hub:plm_task', 'type:hub:plm_project', 'type:hub:plm_model'].map(y)
    expect(hub).toEqual([...hub].sort((a, b) => a - b))
    expect(Math.max(...hub)).toBeLessThan(y('type:mfg:lot'))
    // 가운데 형제가 대상과 거의 같은 높이 — 민 만큼은 어긋날 수 있다.
    expect(Math.abs(hub[1] - y('target:hub'))).toBeLessThan(ROW_GAP)
  })

  it('길이 없는 마디도 자리를 받는다', () => {
    const lonely: MapNode = {
      id: 'pipeline:alone',
      kind: 'pipeline',
      label: 'alone',
      sub: '',
      status: 'never',
      status_label: '',
      link: '',
      detail: {},
    }
    const out = layout([...map.nodes, lonely], map.edges)
    expect(out.get('pipeline:alone')!.y).toBeGreaterThan(out.get('pipeline:mes-lots')!.y)
  })

  it('열 제목은 마디가 있는 열만', () => {
    const titles = columnHeaders(map.nodes.filter((one) => one.kind !== 'downstream'))
    expect(titles.map((one) => one.title)).toEqual(['원천 시스템', 'API', '파이프라인', '대상 SP', '타입'])
  })

  it('선 굵기는 건수의 로그 — 0 이면 1, 그림을 덮지 않게 상한', () => {
    expect(edgeWidth(0)).toBe(1)
    expect(edgeWidth(1)).toBe(2)
    expect(edgeWidth(3)).toBe(3)
    expect(edgeWidth(1_000_000)).toBe(8)
  })
})
