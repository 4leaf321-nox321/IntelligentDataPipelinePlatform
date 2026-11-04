/**
 * 이 사람이 무엇을 할 수 있나 — **한 곳에서 판정한다.**
 *
 * 같은 식이 화면마다 복사되면 갈라지고, 그때 **어떤 화면은 단추를 보이고 어떤
 * 화면은 안 보이는** 상태가 된다. 그 차이는 아무도 설명할 수 없다.
 *
 * **이것은 표시일 뿐 권한이 아니다.** 권한은 서버가 판정한다 — 여기를 고쳐
 * 우회할 수 있으면 그건 애초에 보안이 아니다. 여기서 하는 일은 하나다:
 * **눌러 보고 403 을 알게 하지 않는 것.**
 */

import type { CurrentUser, WorkspaceMembership } from '@/shared/auth/types'

export function isSystemAdmin(user: CurrentUser | null | undefined): boolean {
  return Boolean(user?.is_system_admin)
}

/** 어느 부서에서든 관리자인가. 시스템 관리자는 언제나 참이다. */
export function isAnyManager(user: CurrentUser | null | undefined): boolean {
  return isSystemAdmin(user) || (user?.memberships ?? []).some((one) => one.role === 'manager')
}

/** 지금 서 있는 부서에서 관리자인가. 부서 스코프 화면이 단추를 보일 근거다. */
export function isManagerOf(
  user: CurrentUser | null | undefined,
  slug: string | null | undefined,
): boolean {
  if (isSystemAdmin(user)) return true
  if (!slug) return false
  return (user?.memberships ?? []).some((one) => one.slug === slug && one.role === 'manager')
}

/** 내가 관리자인 소속 — 부서 소유 자료(객체 · 부서 뷰)를 만들 수 있는 곳.
 *  서버의 `require_manager` 와 같은 문턱이다. */
export function managedMemberships(user: CurrentUser | null | undefined): WorkspaceMembership[] {
  return (user?.memberships ?? []).filter((one) => one.role === 'manager')
}

/** 이 용도의 객체를 넣을 수 있는 소속 — **기록(`log`)은 구성원이면, 축은 관리자일 때**
 *  (ADR 0036, 서버의 `objects/access.py`). 용도를 모르면 축으로 본다(좁은 쪽). */
export function ownableMemberships(
  user: CurrentUser | null | undefined,
  usage?: string | null,
): WorkspaceMembership[] {
  return usage === 'log' ? (user?.memberships ?? []) : managedMemberships(user)
}

/** 이 용도의 객체를 어딘가에 넣을 수 있나 — 목록의 「생성」 단추가 설 근거다. */
export function canCreateObject(
  user: CurrentUser | null | undefined,
  usage?: string | null,
): boolean {
  return isSystemAdmin(user) || ownableMemberships(user, usage).length > 0
}

function canOwn(
  user: CurrentUser | null | undefined,
  slug: string,
  usage?: string | null,
): boolean {
  if (isSystemAdmin(user)) return true
  return ownableMemberships(user, usage).some((one) => one.slug === slug)
}

/**
 * 부서 소유 자료를 **어느 부서 것으로** 만들지의 기본값.
 *
 * 차례: 지금 보고 있는 부서(`preferred`) → 대표 소속 → 관리하는 소속의 첫째. **대표 소속이라고
 * 무조건 고르지 않는다** — B 의 관리자인데 대표 소속 A 에서는 멤버인 사람이 A 로 보내면 403 을
 * 보고, 그 단추는 그 사람에게 아예 안 서야 맞았다(2026-10-08). 관리하는 곳이 없으면 null.
 * 기록(`usage` 가 `log`)은 구성원인 곳이면 된다(ADR 0036).
 */
export function defaultOwnerWorkspace(
  user: CurrentUser | null | undefined,
  preferred?: string | null,
  usage?: string | null,
): string | null {
  if (preferred && canOwn(user, preferred, usage)) return preferred
  const home = user?.home_workspace_slug ?? null
  if (home && canOwn(user, home, usage)) return home
  if (isSystemAdmin(user)) return user?.memberships[0]?.slug ?? null
  return ownableMemberships(user, usage)[0]?.slug ?? null
}
