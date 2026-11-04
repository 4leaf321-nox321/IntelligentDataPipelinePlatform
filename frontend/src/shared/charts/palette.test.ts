/**
 * 색과 상한 — **실측에서 나온 규칙이 지켜지나.**
 *
 * 이 셋(점 마커 자동 끄기·범례 높이·이름 고정 색)은 ReportArchive 가 실제로 데인
 * 자리에서 나왔다. 시험이 없으면 「보기 좋게」 고치는 다음 사람이 조용히 되돌린다.
 */

import { describe, expect, it } from 'vitest'

import {
  DOT_MAX_POINTS,
  SERIES_COLORS,
  colorAt,
  colorFor,
  dotConfig,
  legendHeightFor,
  shownNumber,
} from '@/shared/charts/palette'

describe('색', () => {
  it('자리로 선택하면 팔레트를 돌아가며 쓴다', () => {
    expect(colorAt(0)).toBe(SERIES_COLORS[0])
    expect(colorAt(SERIES_COLORS.length)).toBe(SERIES_COLORS[0])
    // 음수가 와도 팔레트 안이다 — 자리 계산이 어긋나도 그림이 색 없이 그려지지 않는다.
    expect(SERIES_COLORS).toContain(colorAt(-3))
  })

  it('이름으로 선택하면 언제나 같은 색이다', () => {
    // **자리로만 주면** 「A 등급」 이 필터 전에는 첫째라 파랑, 거른 뒤에는 둘째라
    // 빨강이 된다. 사람은 그 변화를 데이터가 바뀐 것으로 읽는다.
    expect(colorFor('A 등급')).toBe(colorFor('A 등급'))
    expect(SERIES_COLORS).toContain(colorFor('아무 이름'))
  })
})

describe('실측에서 나온 상한', () => {
  it('점이 많아지면 마커가 알아서 꺼진다', () => {
    // 17계열 × 1,144점이 SVG 원 19,448개가 되어 그림을 얼룩으로 만들고 내보내기에
    // 60.8초가 걸렸다. **그리는 사람이 몰라도 걸려야 한다.**
    expect(dotConfig(DOT_MAX_POINTS)).toEqual({ r: 3 })
    expect(dotConfig(DOT_MAX_POINTS + 1)).toBe(false)
  })

  it('범례는 필요한 줄만, 그러나 세 줄까지만 가져간다', () => {
    expect(legendHeightFor(1)).toBe(legendHeightFor(6))
    expect(legendHeightFor(7)).toBeGreaterThan(legendHeightFor(6))
    // 그 위로는 도표가 남지 않는다.
    expect(legendHeightFor(100)).toBe(legendHeightFor(19))
  })
})

describe('눈금 숫자', () => {
  it('자릿점을 찍고 소수는 둘째 자리까지', () => {
    expect(shownNumber(12345)).toBe('12,345')
    expect(shownNumber(3.33333)).toBe('3.33')
    // 못 읽는 값은 빈 글자 — 눈금에 NaN 이 찍히면 그림 전체가 고장 난 것으로 읽힌다.
    expect(shownNumber(Number.NaN)).toBe('')
  })
})
