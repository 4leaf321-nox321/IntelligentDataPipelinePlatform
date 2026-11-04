/**
 * 확장 모듈의 화면 쪽 — **켠 것만 붙는다.**
 *
 * 백엔드 `app/extensions/<이름>` 의 짝이 `src/extensions/<이름>` 이다. **켜진 목록은 서버가
 * 답한다**(`/api/server/enabled-extensions`, `EnabledProvider`) — 메타(`app-extensions`)는 첫
 * 그림의 씨앗일 뿐이다. 메뉴는 그 목록으로 그리고, 경로는 전부 등록해 꺼진 것을 문이 막는다.
 *
 * 새 확장: `src/extensions/<이름>/index.tsx` 에서 `ExtensionDef` 를 내보내고 아래 REGISTRY 에
 * 한 줄 더한다. **코어 화면(`modules` · `shared`)은 확장을 import 하지 않는다.**
 */

import { createElement } from 'react'
import type { RouteObject } from 'react-router-dom'

import { ExtensionGate } from '@/extensions/ExtensionGate'
import { ENABLED_EXTENSIONS } from '@/shared/branding'
import type { NavGroup } from '@/shared/layout/navigation'

import { sampleExtension } from './sample'

export { ExtensionsProvider, useEnabledExtensions, useExtensionsReload } from './EnabledProvider'
export { ExtensionGate } from './ExtensionGate'

export interface ExtensionDef {
  /** 백엔드의 이름과 같다(EXTENSIONS 에 적는 값). */
  name: string
  /**
   * 사이드바에 더할 그룹 — **제 이름으로 낸다.** 홈 바로 아래에 선다.
   *
   * 「확장」 이라는 바구니에 넣지 않는다. 켜고 끄는 일이 **기능 한 덩어리가 열리고 닫히는
   * 일**이어야 하고, 사용자는 그것을 「설비 관리가 있다 / 없다」 로 읽는다 — 「확장 안에
   * 무언가가 있다」 는 개발자의 사정이다.
   *
   * 기존 그룹과 제목이 같으면 그 그룹에 항목이 합쳐진다(`mergeExtensions`).
   */
  nav: NavGroup[]
  /** AppShell 아래 라우트. 경로는 `ext/<이름>/…` 로 시작한다 — 코어 경로와 안 겹치게. */
  routes: RouteObject[]
}

const REGISTRY: Record<string, ExtensionDef> = {
  [sampleExtension.name]: sampleExtension,
}

/** 켜져 있는데 화면 쪽 짝이 없는 이름 — 서버 화면이 말한다. */
export function missingExtensions(enabled: readonly string[] = ENABLED_EXTENSIONS): string[] {
  return enabled.filter((name) => !(name in REGISTRY))
}

export function enabledExtensions(enabled: readonly string[] = ENABLED_EXTENSIONS): ExtensionDef[] {
  return enabled.flatMap((name) => (name in REGISTRY ? [REGISTRY[name]] : []))
}

export function extensionNavGroups(enabled?: readonly string[]): NavGroup[] {
  return enabledExtensions(enabled).flatMap((ext) => ext.nav)
}

/**
 * 확장의 경로 — **번들에 든 것 전부를 등록하고, 꺼진 것은 문이 답한다**(`ExtensionGate`).
 *
 * 켠 것만 등록하면 방금 켠 확장의 링크가 새로 고침 전까지 「없는 페이지」 가 된다. 사이드바는
 * 서버에게 받은 목록으로 이미 살아 있으니, 링크는 있고 페이지는 없는 상태가 된다.
 */
export function extensionRoutes(): RouteObject[] {
  return Object.values(REGISTRY).flatMap((ext) =>
    ext.routes.map((route) => ({
      ...route,
      element: createElement(ExtensionGate, { name: ext.name }, route.element),
    })),
  )
}
