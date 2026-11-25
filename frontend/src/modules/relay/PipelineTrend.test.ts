import { describe, expect, it } from 'vitest'

import type { TrendRow } from '@/modules/relay/api'
import { memoryLine, modeLine, secondsLine } from '@/modules/relay/PipelineTrend'

function row(over: Partial<TrendRow> = {}): TrendRow {
  return { at: '2026-11-25T01:00:00Z', status: 'planned', seconds: 39, held: false, ...over }
}

describe('실행 추이의 마지막 실행', () => {
  it('변환과 비교를 증분으로 했는지, 전체로 간 까닭이 무엇인지 말한다', () => {
    expect(modeLine(row())).toBe('')
    expect(modeLine(row({ mode: 'incremental', compared: 'incremental' }))).toBe('증분 변환 · 증분 비교')
    expect(modeLine(row({ mode: 'full', full_reason: '색인이 없음', compared: 'full' }))).toBe(
      '전체 변환(색인이 없음) · 전체 비교',
    )
    // 미해결로 멈춰 비교까지 가지 않은 실행
    expect(modeLine(row({ mode: 'incremental' }))).toBe('증분 변환')
  })

  it('단계별 최고 메모리와 소요 시간을 한 줄로', () => {
    expect(memoryLine(row())).toBe('')
    expect(secondsLine(row())).toBe('')
    const measured = row({
      memory: { 수신: 52.3, 변환: 1167.6 },
      seconds_by_stage: { 수신: 3.6, 변환: 30.2 },
    })
    expect(memoryLine(measured)).toBe('수신 52 · 변환 1,168 MB')
    expect(secondsLine(measured)).toBe('수신 4 · 변환 30초')
  })
})
