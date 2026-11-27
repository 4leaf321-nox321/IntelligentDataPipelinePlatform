/**
 * 정의 편집의 규칙 — 경로로 읽고 쓰고(비게 된 객체는 지운다), 출발 판에서 바뀐 칸을 경로로 말하고, 저장
 * 전에 slug · 비밀 · 꼭 있을 칸을 본다.
 */

import { describe, expect, it } from 'vitest'

import {
  blockers,
  changes,
  columnRows,
  columnsOf,
  keyOf,
  keyText,
  reconcileKindOf,
  weekdaysOf,
  withAt,
  withAuthKind,
  withReconcileKind,
  withSchedule,
  withWeekday,
} from '@/modules/relay/definitionEdit'

const BASE = {
  format: 'relay-pipeline/1',
  slug: 'mes-lots',
  source: 'mes',
  adapter: {
    kind: 'rest',
    base_url: 'http://mes',
    path: '/lots',
    key: 'LotId',
    columns: { LOT_ID: 'LotId' },
    auth: { kind: 'bearer', token_env: 'MES_TOKEN' },
  },
  target: { name: '허브', server: 'http://hub', token_env: 'HUB_TOKEN' },
  schedule: { daily_at: '01:00' },
}

describe('정의 편집의 규칙', () => {
  it('경로로 쓰고 지운다 — 비게 된 객체는 남기지 않고, 본문은 그대로', () => {
    const next = withAt(BASE, ['adapter', 'path'], '/v2/lots')
    expect(next.adapter).toMatchObject({ path: '/v2/lots' })
    expect(BASE.adapter.path).toBe('/lots')
    const computed = withAt(BASE, ['computed', 'K'], { template: '{A}|{B}' })
    expect(computed.computed).toEqual({ K: { template: '{A}|{B}' } })
    expect(withAt(computed, ['computed', 'K'], undefined)).not.toHaveProperty('computed')
    expect(withAt(BASE, ['nothing', 'here'], undefined)).toEqual(BASE)
  })

  it('바뀐 칸을 경로로 — 추가 · 삭제를 가르고, 키 차례는 따지지 않는다', () => {
    let next = withAt(BASE, ['adapter', 'path'], '/v2/lots')
    next = withAt(next, ['adapter', 'auth', 'kind'], 'header')
    next = withAt(next, ['adapter', 'page_size'], 200)
    next = withAt(next, ['schedule'], undefined)
    expect(changes(BASE, next)).toEqual([
      'adapter.path',
      'adapter.auth.kind',
      'adapter.page_size(추가)',
      'schedule(삭제)',
    ])
    const reordered = { ...BASE, adapter: { ...BASE.adapter, columns: { LOT_ID: 'LotId' } } }
    expect(changes(BASE, JSON.parse(JSON.stringify(reordered)))).toEqual([])
  })

  it('식별자 · 열 · 인증 · 일정의 모양', () => {
    expect(keyOf('LotId')).toBe('LotId')
    expect(keyOf(' LotId , steps.Seq ')).toEqual(['LotId', 'steps.Seq'])
    expect(keyOf(' , ')).toBeUndefined()
    expect(keyText(['A', 'B'])).toBe('A, B')
    expect(columnRows('*')).toBeNull()
    expect(columnRows({ A: 'a' })).toEqual([['A', 'a']])
    expect(columnsOf([['A', 'a'], ['', '']])).toEqual({ A: 'a' })
    expect(columnsOf([['', '']])).toBeUndefined()

    // 방식을 바꾸면 앞 방식의 칸은 지우고 비밀의 환경 변수 · 인증서는 남긴다.
    expect(withAuthKind({ kind: 'header', header: 'X-Key', token_env: 'K', cert: 'c.pem' }, 'basic')).toEqual({
      kind: 'basic',
      token_env: 'K',
      cert: 'c.pem',
    })
    expect(withAuthKind({ kind: 'bearer', token_env: 'K' }, 'none')).toBeUndefined()
    expect(withAuthKind({ kind: 'bearer', cert: 'c.pem' }, 'none')).toEqual({ kind: 'none', cert: 'c.pem' })

    expect(withSchedule({ daily_at: '01:00', weekdays: [1] }, 'every', '30')).toEqual({
      weekdays: [1],
      every_minutes: 30,
    })
    expect(withSchedule({ daily_at: '01:00' }, 'manual', '')).toBeUndefined()
  })

  it('대조 주기 — 서버 기본 · 매주 요일 · N일마다 · 사용 안 함, 시각은 남긴다', () => {
    expect(reconcileKindOf(undefined)).toBe('server')
    expect(reconcileKindOf(false)).toBe('off')
    expect(reconcileKindOf({ weekdays: ['sun'], every_days: 14 })).toBe('weekly')
    expect(reconcileKindOf({ every_days: 3 })).toBe('every')
    expect(withReconcileKind({ every_days: 3, at: '04:00' }, 'weekly')).toEqual({ weekdays: ['sun'], at: '04:00' })
    expect(withReconcileKind({ weekdays: ['sun'] }, 'every')).toEqual({ every_days: 7 })
    expect(withReconcileKind({ weekdays: ['sun'] }, 'server')).toBeUndefined()
    expect(withReconcileKind({ weekdays: ['sun'] }, 'off')).toBe(false)
    // 한국어 요일도 읽고, 켜고 끄면 월요일부터의 차례로 둔다.
    expect(weekdaysOf({ weekdays: ['일', 'MON'] })).toEqual(['sun', 'mon'])
    expect(weekdaysOf({ weekdays: 'sat' })).toEqual(['sat'])
    expect(withWeekday({ weekdays: ['일'], at: '02:00' }, 'wed', true)).toEqual({ weekdays: ['wed', 'sun'], at: '02:00' })
    expect(withWeekday({ weekdays: ['sun', 'wed'] }, 'sun', false)).toEqual({ weekdays: ['wed'] })
  })

  it('저장 전에 — slug 는 못 바꾸고, 비밀은 어디에도 못 적는다', () => {
    const secrets = ['dsn', 'password', 'secret', 'token']
    expect(blockers(BASE, 'mes-lots', secrets)).toEqual([])
    expect(blockers({ ...BASE, slug: 'other' }, 'mes-lots', secrets)[0]).toMatch(/slug는 변경할 수 없습니다/)
    const leaked = withAt(BASE, ['adapter', 'auth', 'token'], 's3cret')
    expect(blockers(leaked, 'mes-lots', secrets)).toEqual([
      'adapter.auth.token: 인증 정보의 값은 정의에 입력할 수 없습니다. 환경 변수 이름(token_env)만 입력하세요.',
    ])
    const keyless = withAt(BASE, ['adapter', 'key'], undefined)
    expect(blockers(keyless, 'mes-lots', secrets)).toEqual(['adapter.key(식별자)가 필요합니다.'])
    expect(blockers(withAt(BASE, ['target', 'token_env'], undefined), 'mes-lots', secrets)[0]).toMatch(/token_env/)
    expect(blockers([], 'mes-lots', secrets)).toEqual(['정의는 JSON 객체여야 합니다.'])
  })
})
