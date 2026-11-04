/**
 * 본보기 확장 — **새 확장을 만들 때 이 폴더를 복사한다.** 백엔드 `app/extensions/sample` 의 짝.
 *
 * **그룹을 제 이름으로 낸다.** 켜고 끄는 일이 「본보기 기능이 열렸다 / 닫혔다」 로 읽혀야 하고,
 * 「확장」 이라는 바구니는 개발자의 사정이다 — 사용자는 그 말로 자기 일을 찾지 않는다.
 */

import { Puzzle } from 'lucide-react'
import { lazy } from 'react'

import type { ExtensionDef } from '@/extensions'

const SamplePage = lazy(() => import('./SamplePage'))

export const sampleExtension: ExtensionDef = {
  name: 'sample',
  nav: [{ title: '본보기', items: [{ label: '본보기 화면', icon: Puzzle, to: '/ext/sample' }] }],
  routes: [{ path: 'ext/sample', element: <SamplePage /> }],
}
