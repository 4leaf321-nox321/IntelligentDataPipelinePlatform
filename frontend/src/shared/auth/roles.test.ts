/**
 * 부서 소유 자료의 기본 부서 — **관리하는 곳만 고른다.**
 *
 * 대표 소속을 무조건 고르던 때, B 의 관리자인데 대표 소속 A 에서는 멤버인 사람은 생성 · 일괄
 * 입력 · 홈 게시에서 늘 403 을 봤다(2026-10-08).
 */

import { describe, expect, it } from 'vitest'

import type { CurrentUser } from '@/shared/auth/types'
import {
  canCreateObject,
  defaultOwnerWorkspace,
  managedMemberships,
  ownableMemberships,
} from '@/shared/auth/roles'

function user(extra: Partial<CurrentUser>): CurrentUser {
  return {
    is_system_admin: false,
    home_workspace_slug: 'a',
    memberships: [],
    ...extra,
  } as CurrentUser
}

const room = (slug: string, role: string) => ({
  workspace_id: slug,
  slug,
  name: slug,
  path: slug,
  depth: 0,
  role,
})

describe('defaultOwnerWorkspace', () => {
  it('대표 소속에서 멤버뿐이면 관리하는 부서로 떨어진다', () => {
    const one = user({ memberships: [room('a', 'member'), room('b', 'manager')] })
    expect(defaultOwnerWorkspace(one)).toBe('b')
    expect(managedMemberships(one).map((each) => each.slug)).toEqual(['b'])
  })

  it('보고 있던 부서가 관리하는 곳이면 그것이 먼저다', () => {
    const one = user({ memberships: [room('a', 'manager'), room('b', 'manager')] })
    expect(defaultOwnerWorkspace(one, 'b')).toBe('b')
    // 관리하지 않는 부서를 보고 있었으면 대표 소속으로.
    expect(defaultOwnerWorkspace(one, 'zz')).toBe('a')
  })

  it('시스템 관리자는 보고 있던 부서가 소속이 아니어도 그곳이다', () => {
    const admin = user({ is_system_admin: true, home_workspace_slug: 'hq' })
    expect(defaultOwnerWorkspace(admin, 'sales')).toBe('sales')
    expect(defaultOwnerWorkspace(admin)).toBe('hq')
  })

  it('관리하는 곳이 없으면 없다 — 대표 소속을 내밀지 않는다', () => {
    expect(defaultOwnerWorkspace(user({ memberships: [room('a', 'member')] }))).toBeNull()
  })
})

describe('기록은 구성원도 넣는다(ADR 0036)', () => {
  const plain = user({ memberships: [room('a', 'member'), room('b', 'member')] })

  it('기록이면 소속 부서 전부가 고를 거리이고, 축이면 관리하는 곳만이다', () => {
    expect(ownableMemberships(plain, 'log').map((each) => each.slug)).toEqual(['a', 'b'])
    expect(ownableMemberships(plain, 'axis')).toEqual([])
    // 용도를 모르면 좁은 쪽(축)으로 본다.
    expect(ownableMemberships(plain)).toEqual([])
  })

  it('멤버뿐인 사람도 기록의 「생성」 단추는 선다', () => {
    expect(canCreateObject(plain, 'log')).toBe(true)
    expect(canCreateObject(plain, 'axis')).toBe(false)
    expect(canCreateObject(user({ memberships: [] }), 'log')).toBe(false)
  })

  it('기록의 기본 부서는 대표 소속이다 — 멤버여도', () => {
    expect(defaultOwnerWorkspace(plain, null, 'log')).toBe('a')
    expect(defaultOwnerWorkspace(plain, 'b', 'log')).toBe('b')
    expect(defaultOwnerWorkspace(plain, null, 'axis')).toBeNull()
  })
})
