import { describe, expect, it } from 'vitest'

import { descendants, movePlan, parentOptions, visibleRows } from '@/modules/workspaces/tree'
import type { Workspace } from '@/shared/api/types'

function node(
  slug: string,
  parent: string | null,
  depth: number,
  order: number,
  name = slug,
): Workspace {
  return {
    id: slug,
    slug,
    name,
    description: '',
    parent_slug: parent,
    depth,
    path: name,
    sort_order: order,
    is_active: true,
    created_at: '2026-01-01T00:00:00Z',
    member_count: 0,
    my_role: null,
  }
}

// 개발본부 > (설계팀, 해석팀 > 구조파트), 품질본부
const ROWS: Workspace[] = [
  node('dev', null, 0, 0, '개발본부'),
  node('design', 'dev', 1, 0, '설계팀'),
  node('cae', 'dev', 1, 1, '해석팀'),
  node('struct', 'cae', 2, 0, '구조파트'),
  node('qa', null, 0, 1, '품질본부'),
]

describe('descendants', () => {
  it('자신과 모든 하위를 모은다', () => {
    expect(descendants(ROWS, 'dev')).toEqual(new Set(['dev', 'design', 'cae', 'struct']))
    expect(descendants(ROWS, 'struct')).toEqual(new Set(['struct']))
  })
})

describe('movePlan', () => {
  it('줄 본체에 놓으면 그 부서의 막내가 된다', () => {
    expect(movePlan(ROWS, 'qa', 'dev', 'inside')).toEqual({
      slug: 'qa',
      parentSlug: 'dev',
      position: 2,
    })
  })

  it('줄 위 띠에 놓으면 그 형제 앞에 끼운다', () => {
    expect(movePlan(ROWS, 'qa', 'cae', 'before')).toEqual({
      slug: 'qa',
      parentSlug: 'dev',
      position: 1,
    })
  })

  it('뿌리 줄 앞에 놓으면 상위가 없어진다', () => {
    expect(movePlan(ROWS, 'struct', 'dev', 'before')).toEqual({
      slug: 'struct',
      parentSlug: null,
      position: 0,
    })
  })

  // **자기 하위로 옮기면 그 가지가 트리에서 통째로 사라진다** — 화면에 안 나오니
  // 되돌릴 수도 없다.
  it('자기 자신이나 자기 하위로는 못 옮긴다', () => {
    expect(movePlan(ROWS, 'dev', 'struct', 'inside')).toBeNull()
    expect(movePlan(ROWS, 'dev', 'dev', 'inside')).toBeNull()
  })

  it('이미 그 자리면 아무것도 안 보낸다', () => {
    expect(movePlan(ROWS, 'design', 'cae', 'before')).toBeNull()
    expect(movePlan(ROWS, 'struct', 'cae', 'inside')).toBeNull()
  })

  it('sort_order 가 같으면 서버처럼 이름으로 가른다 — slug 가 아니라', () => {
    // 서버(move)는 `(sort_order, name)` 으로 형제를 세운다. 화면이 slug 로 가르던 때는 같은
    // 「몇 번째」 가 서로 다른 형제를 가리켜, 놓은 자리와 다른 자리에 끼었다(2026-10-08).
    // slug 차례(a-team < z-team)와 이름 차례(가팀 < 나팀)가 거꾸로다.
    const tied = [
      node('root', null, 0, 0, '본부'),
      node('z-team', 'root', 1, 0, '가팀'),
      node('a-team', 'root', 1, 0, '나팀'),
      node('mover', null, 0, 1, '옮길 팀'),
    ]
    // 「나팀」 앞 = 이름 차례로 두 번째(가팀 다음) — slug 로 갈랐으면 0 이었다.
    expect(movePlan(tied, 'mover', 'a-team', 'before')).toEqual({
      slug: 'mover',
      parentSlug: 'root',
      position: 1,
    })
  })
})

describe('visibleRows', () => {
  it('접으면 그 아래가 통째로 빠지고 숨은 수를 센다', () => {
    const shown = visibleRows(ROWS, { collapsed: new Set(['dev']), query: '' })
    expect(shown.map((one) => one.node.slug)).toEqual(['dev', 'qa'])
    expect(shown[0].hidden).toBe(3)
    expect(shown[0].collapsed).toBe(true)
  })

  it('찾으면 맞은 줄의 상위까지 남긴다 — 어느 본부의 팀인지 알아야 한다', () => {
    const shown = visibleRows(ROWS, { collapsed: new Set(), query: '구조' })
    expect(shown.map((one) => one.node.slug)).toEqual(['dev', 'cae', 'struct'])
  })

  it('찾는 동안에는 접기를 무시한다 — 접힌 가지 안의 답이 안 보이면 없다고 읽힌다', () => {
    const shown = visibleRows(ROWS, { collapsed: new Set(['dev']), query: '해석' })
    expect(shown.map((one) => one.node.slug)).toEqual(['dev', 'cae'])
  })

  it('주소나 설명으로도 찾는다', () => {
    expect(visibleRows(ROWS, { collapsed: new Set(), query: 'struct' })).toHaveLength(3)
  })
})

describe('parentOptions', () => {
  it('자기와 하위는 이유를 달아 못 고르게 한다', () => {
    const options = parentOptions(ROWS, 'cae')
    expect(options.find((one) => one.value === 'cae')?.disabledReason).toBe('자기 자신')
    expect(options.find((one) => one.value === 'struct')?.disabledReason).toBe('자기 하위 부서')
    expect(options.find((one) => one.value === 'qa')?.disabledReason).toBeUndefined()
  })
})
