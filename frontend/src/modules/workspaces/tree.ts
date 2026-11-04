/**
 * 부서 트리의 셈 — **그리는 것과 떼어 놓는다.**
 *
 * 끌어 놓기·접기·검색은 전부 「어느 줄을 어디에 둘 것인가」 라는 계산이고, 그것이
 * 화면 컴포넌트 안에 있으면 눈으로만 확인할 수 있다. 여기 있으면 시험이 잡는다 —
 * 조직도가 틀어지는 것은 화면이 깨지는 것보다 나쁘다. 자료가 엉뚱한 부서 밑에
 * 붙어도 **아무 오류도 안 나기 때문이다.**
 */

import type { Workspace } from '@/shared/api/types'

/** 트리 순서로 펼친 한 줄. `depth` 는 서버가 준 것을 그대로 쓴다. */
export interface TreeRow {
  node: Workspace
  /** 자식이 있나 — 접기 삼각형을 보일지 정한다. */
  hasChildren: boolean
  /** 접혀서 자식이 안 보이나. */
  collapsed: boolean
  /** 이 줄 아래 숨은 하위 부서 수. 접었을 때 「+3」 으로 보인다. */
  hidden: number
}

/** 놓는 자리. 줄 위 가는 띠면 `before`(그 형제 앞), 줄 본체면 `inside`(그 부서의 막내). */
export type DropKind = 'before' | 'inside'

export interface MovePlan {
  slug: string
  parentSlug: string | null
  /** 형제 사이 몇 번째. 서버가 이 자리로 끼우고 형제 순서를 다시 매긴다. */
  position: number
}

/**
 * 형제 차례 — **서버와 같은 열쇠로**(`workspaces/services.py` 의 move: `(sort_order, name)`).
 *
 * 같은 `sort_order` 에서 화면은 slug 로, 서버는 이름으로 갈라 「몇 번째」 가 서로 다른 형제를
 * 가리켰다 — 끌어 놓은 자리와 다른 자리에 끼었다(2026-10-08). 이름은 `localeCompare` 가 아니라
 * 글자 차례로 견준다 — 파이썬의 문자열 비교가 그렇다.
 */
export function siblingOrder(a: Workspace, b: Workspace): number {
  if (a.sort_order !== b.sort_order) return a.sort_order - b.sort_order
  return a.name < b.name ? -1 : a.name > b.name ? 1 : 0
}

/** 자신과 모든 하위. 자기 밑으로 옮기는 것을 막는 데 쓴다. */
export function descendants(rows: Workspace[], slug: string): Set<string> {
  const byParent = new Map<string | null, Workspace[]>()
  for (const one of rows) {
    const key = one.parent_slug ?? null
    byParent.set(key, [...(byParent.get(key) ?? []), one])
  }
  const found = new Set([slug])
  const stack = [slug]
  while (stack.length > 0) {
    const current = stack.pop() as string
    for (const child of byParent.get(current) ?? []) {
      if (!found.has(child.slug)) {
        found.add(child.slug)
        stack.push(child.slug)
      }
    }
  }
  return found
}

/**
 * 끌어 놓기 하나를 **서버 요청 하나로.**
 *
 * 화면이 형제들의 순서를 제 손으로 다시 매겨 여러 번 저장하면, 중간에 하나가
 * 실패했을 때 트리가 반쯤 뒤섞인 채로 남는다. 여기서는 「어느 부모의 몇 번째」 만
 * 정하고 나머지는 서버가 한 트랜잭션으로 한다.
 *
 * 못 옮기는 자리(자기 자신·자기 하위)면 `null` — 부르는 쪽은 아무것도 안 보낸다.
 */
export function movePlan(
  rows: Workspace[],
  dragged: string,
  target: string,
  kind: DropKind,
): MovePlan | null {
  if (dragged === target && kind === 'inside') return null
  const blocked = descendants(rows, dragged)
  if (blocked.has(target)) return null
  const targetRow = rows.find((one) => one.slug === target)
  const draggedRow = rows.find((one) => one.slug === dragged)
  if (!targetRow || !draggedRow) return null

  const parentSlug = kind === 'inside' ? targetRow.slug : targetRow.parent_slug
  const siblings = rows
    .filter((one) => (one.parent_slug ?? null) === (parentSlug ?? null) && one.slug !== dragged)
    .sort(siblingOrder)

  let position = siblings.length
  if (kind === 'before') {
    const found = siblings.findIndex((one) => one.slug === target)
    if (found >= 0) position = found
  }

  // 이미 그 자리면 아무것도 안 보낸다. 보내면 감사 기록에 「안 바뀐 이동」 이 쌓이고,
  // 나중에 개편 이력을 읽는 사람이 그것을 진짜 이동으로 읽는다.
  const currentParent = draggedRow.parent_slug ?? null
  if (currentParent === (parentSlug ?? null)) {
    const current = rows
      .filter((one) => (one.parent_slug ?? null) === currentParent)
      .sort(siblingOrder)
      .findIndex((one) => one.slug === dragged)
    if (current === position) return null
  }
  return { slug: dragged, parentSlug: parentSlug ?? null, position }
}

/**
 * 서버가 준 트리 순서 그대로 두되, **접힌 것과 검색으로 걸러진 것을 뺀다.**
 *
 * 찾을 때는 **맞은 줄의 조상도 함께 남긴다.** 「해석팀」 만 덩그러니 보이면 그것이
 * 어느 본부의 해석팀인지 알 수 없고, 조직도에서는 그 물음이 거의 항상 따라온다.
 * 찾는 동안에는 접기를 무시한다 — 접힌 가지 안의 답이 안 보이면 사람은 없다고
 * 결론 내린다.
 */
export function visibleRows(
  rows: Workspace[],
  options: { collapsed: Set<string>; query: string },
): TreeRow[] {
  const query = options.query.trim().toLowerCase()
  const childCount = new Map<string, number>()
  for (const one of rows) {
    if (one.parent_slug) childCount.set(one.parent_slug, (childCount.get(one.parent_slug) ?? 0) + 1)
  }

  let keep: Set<string> | null = null
  if (query) {
    const bySlug = new Map(rows.map((one) => [one.slug, one]))
    keep = new Set<string>()
    for (const one of rows) {
      const hay = `${one.name} ${one.slug} ${one.description}`.toLowerCase()
      if (!hay.includes(query)) continue
      let cursor: Workspace | undefined = one
      while (cursor && !keep.has(cursor.slug)) {
        keep.add(cursor.slug)
        cursor = cursor.parent_slug ? bySlug.get(cursor.parent_slug) : undefined
      }
    }
  }

  const out: TreeRow[] = []
  // 「이 부서의 자식은 안 보인다」 를 모아 가며 한 번만 훑는다. 서버가 준 순서가 곧
  // 깊이 우선 순회라 부모가 항상 먼저 나온다.
  //
  // 깊이 숫자로 세지 않는 이유: 깊이와 상위가 어긋난 행이 하나라도 있으면 그 뒤가
  // 통째로 사라지는데, **사라진 부서는 화면에서 고칠 수도 없다.**
  const concealed = new Set<string>()
  for (const node of rows) {
    const buried = node.parent_slug !== null && concealed.has(node.parent_slug)
    const total = childCount.get(node.slug) ?? 0
    const collapsed = !query && options.collapsed.has(node.slug) && total > 0
    if (buried || collapsed) concealed.add(node.slug)
    if (buried) continue
    if (keep && !keep.has(node.slug)) continue
    out.push({
      node,
      hasChildren: total > 0,
      collapsed,
      hidden: collapsed ? descendants(rows, node.slug).size - 1 : 0,
    })
  }
  return out
}

/** 상위 부서 선택에 쓸 목록 — 자기와 하위는 못 고른다(트리가 끊어진다). */
export function parentOptions(
  rows: Workspace[],
  slug: string | null,
): { value: string; label: string; hint?: string; disabledReason?: string }[] {
  const blocked = slug ? descendants(rows, slug) : new Set<string>()
  return rows.map((one) => ({
    value: one.slug,
    label: one.name,
    hint: one.path,
    disabledReason: blocked.has(one.slug)
      ? one.slug === slug
        ? '자기 자신'
        : '자기 하위 부서'
      : undefined,
  }))
}
