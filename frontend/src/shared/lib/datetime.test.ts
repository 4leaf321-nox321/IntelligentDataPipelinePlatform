/**
 * 저장된 날짜·시각을 입력 칸의 모양으로 — **가져오기로 들어온 모양도 빈칸이 되지 않게.**
 *
 * 공백 · 오프셋이 든 값을 그대로 넣으면 `datetime-local` 이 빈칸으로 보였고, 빈 줄 알고 손대면
 * 그 값이 지워졌다(2026-10-08).
 */

import { describe, expect, it } from 'vitest'

import { toDatetimeLocal } from '@/shared/lib/datetime'

const two = (value: number) => String(value).padStart(2, '0')

describe('toDatetimeLocal', () => {
  it('이미 맞는 모양 · 공백 · 소수 초는 그 시각 그대로', () => {
    expect(toDatetimeLocal('2026-10-08T09:30')).toBe('2026-10-08T09:30')
    expect(toDatetimeLocal('2026-10-08 09:30')).toBe('2026-10-08T09:30')
    expect(toDatetimeLocal('2026-10-08 09:30:15.123456')).toBe('2026-10-08T09:30:15')
  })

  it('오프셋이 있으면 이 브라우저의 시각으로 옮긴다', () => {
    const when = new Date('2026-10-08T00:30:00Z')
    const local = `${when.getFullYear()}-${two(when.getMonth() + 1)}-${two(when.getDate())}T${two(
      when.getHours(),
    )}:${two(when.getMinutes())}`
    expect(toDatetimeLocal('2026-10-08T00:30Z')).toBe(local)
    expect(toDatetimeLocal('2026-10-08T09:30+09:00')).toBe(local)
    expect(toDatetimeLocal('2026-10-08 09:30+0900')).toBe(local)
  })

  it('못 읽으면 null — 부르는 쪽이 원값을 보존한다', () => {
    expect(toDatetimeLocal('2026년 10월 8일 아침')).toBeNull()
    expect(toDatetimeLocal('')).toBe('')
    expect(toDatetimeLocal(null)).toBe('')
  })
})
