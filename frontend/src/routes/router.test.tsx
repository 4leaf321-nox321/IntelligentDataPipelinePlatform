/**
 * 사이드바와 라우터가 어긋나지 않는가.
 *
 * **사이드바가 화면 목록의 정본이다.** 메뉴에는 있는데 라우트가 없으면 눌렀을 때
 * 「없는 페이지」 로 떨어지고, 그것은 권한 문제와 구별되지 않는다 — 사람은 자기가
 * 못 보는 것인지 시스템이 고장난 것인지 알 수 없다.
 *
 * **정규식으로 파일을 훑지 않는다.** 라우터 객체를 직접 걸으면 `pending` 항목이
 * 만드는 stub 까지 실제로 그 자리에 있는지 확인된다 — 소스를 문자열로 뒤지는
 * 검사는 그것을 못 보고, 그래서 「stub 이 안 생기는」 회귀를 통과시킨다.
 */

import { describe, expect, it } from 'vitest'

import { router } from '@/routes/router'
import { NAV_GROUPS } from '@/shared/layout/navigation'

interface RouteLike {
  path?: string
  children?: RouteLike[]
}

/** 라우터가 실제로 아는 경로 전부. 부모 경로를 이어 붙인다. */
function collectPaths(routes: RouteLike[], prefix = ''): string[] {
  const found: string[] = []
  for (const route of routes) {
    // path 가 없는 레이아웃 라우트는 자식만 넘긴다 — 접두사가 늘지 않는다.
    const here =
      route.path === undefined
        ? prefix
        : route.path.startsWith('/')
          ? route.path
          : `${prefix.replace(/\/$/, '')}/${route.path}`
    if (route.path !== undefined) found.push(here)
    if (route.children) found.push(...collectPaths(route.children, here))
  }
  return found
}

describe('라우터', () => {
  const known = new Set(collectPaths(router.routes as RouteLike[]))

  it('사이드바의 고정 경로가 전부 라우터에 있다', () => {
    // resolve 로 만드는 부서 스코프 경로는 슬러그가 런타임 값이라 따로 본다.
    const fixed = NAV_GROUPS.flatMap((group) => group.items)
      .map((item) => item.to)
      .filter((to): to is string => Boolean(to))

    expect(fixed.length).toBeGreaterThan(0)
    for (const path of fixed) {
      expect(known, `사이드바의 ${path} 에 대응 라우트가 없습니다`).toContain(path)
    }
  })

  it('미구현 항목도 자리를 갖는다', () => {
    // **자리는 보이되 눌러 보고 알게 하지 않는다.** pending 인데 stub 이 없으면
    // 「없는 페이지」 가 뜨고, 그것은 「아직 안 만들었다」 와 다른 말이다.
    const pending = NAV_GROUPS.flatMap((group) => group.items).filter((item) => item.pending)
    for (const item of pending) {
      expect(known, `미구현 항목 ${item.to} 의 stub 이 없습니다`).toContain(item.to)
    }
  })

  it('로그인 전 화면 셋만 가드 밖에 있다', () => {
    // **새 화면을 추가할 때 가드를 깜빡할 자리가 없어야 한다.** 최상위에 경로가
    // 하나 더 늘면 그것은 인증 없이 열리는 화면이고, 대개 실수다.
    const top = (router.routes as RouteLike[]).flatMap((route) =>
      route.path ? [route.path] : (route.children ?? []).map((child) => child.path),
    )
    expect(new Set(top.filter(Boolean))).toEqual(
      new Set(['/login', '/signup', '/force-password-change', '/']),
    )
  })

  it('부서 스코프 화면이 슬러그를 받는다', () => {
    expect(known).toContain('/w/:slug')
    expect(known).toContain('/w/:slug/members')
  })

})
