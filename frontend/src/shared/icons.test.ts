/**
 * 아이콘 — **모르는 이름이 와도 메뉴가 뜬다.**
 *
 * 이 값은 데이터에서 온다. 다른 설치에서 가져온 정의에 여기 없는 이름이 들어 있을 수
 * 있고, 그때 던지면 사이드바가 통째로 안 뜬다 — 고칠 화면조차 못 연다.
 */

import { describe, expect, it } from 'vitest'

import { DEFAULT_ICON, allIcons, iconOf, matches } from '@/shared/icons'

describe('아이콘 검색', () => {
  it('아는 이름은 그 그림으로', () => {
    expect(iconOf('Wrench')).not.toBe(DEFAULT_ICON)
    expect(iconOf('Wrench')).toBe(iconOf('Wrench'))
  })

  it('모르는 이름·빈 값은 기본으로 떨어진다', () => {
    expect(iconOf('없는이름')).toBe(DEFAULT_ICON)
    expect(iconOf('')).toBe(DEFAULT_ICON)
    expect(iconOf(null)).toBe(DEFAULT_ICON)
    expect(iconOf(undefined)).toBe(DEFAULT_ICON)
  })

  it('이름이 겹치지 않는다 — 겹치면 고르개에서 하나가 가려진다', () => {
    const names = allIcons().map((one) => one.name)
    expect(new Set(names).size).toBe(names.length)
  })
})

describe('고르개 검색', () => {
  const wrench = allIcons().find((one) => one.name === 'Wrench')!

  it('한글 이름으로 찾는다 — 영어 이름은 못 치는 사람이 많다', () => {
    expect(matches(wrench, '공구')).toBe(true)
  })

  it('뜻으로도 찾는다', () => {
    expect(matches(wrench, '정비')).toBe(true)
    expect(matches(wrench, 'wrench')).toBe(true)
  })

  it('빈 말이면 전부 걸린다 — 열자마자 다 보여야 훑을 수 있다', () => {
    expect(matches(wrench, '   ')).toBe(true)
  })

  it('안 맞으면 안 걸린다', () => {
    expect(matches(wrench, '달력')).toBe(false)
  })
})
