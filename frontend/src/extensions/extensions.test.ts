/**
 * 확장은 **켠 것만 붙는다** — 서버가 준 목록으로 메뉴 · 라우트가 갈린다.
 */
import { describe, expect, it } from 'vitest'

import { enabledExtensions, extensionNavGroups, missingExtensions } from '.'

describe('extensions', () => {
  it('켜지 않으면 메뉴가 없다', () => {
    expect(enabledExtensions([])).toEqual([])
    expect(extensionNavGroups([])).toEqual([])
  })

  it('켠 것의 메뉴만 붙고, 그룹은 제 이름으로 선다', () => {
    // **「확장」 이라는 바구니에 넣지 않는다.** 켜고 끄는 일이 「그 기능이 열렸다 / 닫혔다」
    // 로 읽혀야 한다 — 사용자는 「확장」 이라는 말로 자기 일을 찾지 않는다.
    const groups = extensionNavGroups(['sample'])
    expect(groups.map((g) => g.title)).toEqual(['본보기'])
    expect(groups[0].items.map((i) => i.to)).toEqual(['/ext/sample'])
  })

  it('화면 쪽 짝이 없는 이름은 따로 알린다', () => {
    expect(missingExtensions(['sample', 'nope'])).toEqual(['nope'])
    expect(enabledExtensions(['nope'])).toEqual([])
  })
})
