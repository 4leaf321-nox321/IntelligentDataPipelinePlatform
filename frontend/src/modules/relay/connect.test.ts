/**
 * 새 연결의 입력 → 정의 · 레시피. 비면 안 적고(서버의 기본값), 숫자 · JSON 칸은 그 모양으로, 식별자가
 * 여럿이면 목록으로, 비밀은 환경 변수 이름만.
 */

import { describe, expect, it } from 'vitest'

import type { AdapterField } from '@/modules/relay/api'
import { adapterOf, definitionOf, picksOf, recipeOf, valueOf } from '@/modules/relay/connect'
import type { AdapterForm, DefinitionForm } from '@/modules/relay/connect'

const field = (name: string, type = 'str'): AdapterField => ({
  name,
  type,
  required: false,
  default: null,
  doc: '',
})

const FIELDS = [
  field('base_url'),
  field('path'),
  field('page_size', 'int'),
  field('min_interval', 'float'),
  field('params', 'Mapping[str, str]'),
  field('key', 'str | Sequence[str]'),
  field('columns', 'Mapping[str, str]'),
  field('auth', 'Auth'),
]

function form(over: Partial<AdapterForm> = {}): AdapterForm {
  return {
    kind: 'rest',
    fields: FIELDS,
    values: { base_url: 'http://mes', path: '/lots', page_size: '100', params: '{"plant": "P1"}' },
    auth: { kind: 'oauth2', token_env: 'MES_SECRET', user: 'relay', token_url: 'http://idp/token', scope: '' },
    picks: picksOf({
      paths: [
        { path: 'LotId', sample: 'L1' },
        { path: 'steps.Seq', sample: '1' },
        { path: 'steps.Grams', sample: '1250' },
      ],
      columns: { LOT_ID: 'LotId', STEPS_SEQ: 'steps.Seq', STEPS_GRAMS: 'steps.Grams' },
    }),
    ...over,
  }
}

describe('새 연결의 입력', () => {
  it('칸의 모양대로 — 수 · JSON, 빈 칸은 안 적는다', () => {
    expect(valueOf(field('page_size', 'int'), '200')).toBe(200)
    expect(valueOf(field('params', 'Mapping[str, str]'), '{"a":"b"}')).toEqual({ a: 'b' })
    expect(valueOf(field('rows_path'), '  ')).toBeUndefined()
    expect(() => valueOf(field('page_size', 'int'), 'many')).toThrow(/수입니다/)
    expect(() => valueOf(field('params', 'Mapping[str, str]'), '{a')).toThrow(/JSON/)
  })

  it('탐색용은 식별자 · 열 없이, 정의용은 선택한 칸으로 — 여러 칸 식별자는 목록', () => {
    const partial = adapterOf(form(), true)
    expect(partial).toEqual({
      kind: 'rest',
      base_url: 'http://mes',
      path: '/lots',
      page_size: 100,
      params: { plant: 'P1' },
      auth: { kind: 'oauth2', token_env: 'MES_SECRET', user: 'relay', token_url: 'http://idp/token' },
    })
    expect(() => adapterOf(form())).toThrow(/식별자/)

    const picked = form()
    picked.picks = picked.picks.map((one) => ({ ...one, key: one.path !== 'steps.Grams' }))
    picked.picks[2].column = 'GRAMS'
    const full = adapterOf(picked)
    expect(full.key).toEqual(['LotId', 'steps.Seq'])
    expect(full.columns).toEqual({ LOT_ID: 'LotId', STEPS_SEQ: 'steps.Seq', GRAMS: 'steps.Grams' })

    const twice = form()
    twice.picks = twice.picks.map((one) => ({ ...one, key: true, column: 'SAME' }))
    expect(() => adapterOf(twice)).toThrow(/둘입니다/)
  })

  it('정의 · 레시피의 첫 판', () => {
    const meta: DefinitionForm = {
      slug: ' mes-lots ',
      source: 'mes',
      system: '',
      target: { name: '제조 허브', server: 'http://hub/', token_env: 'HUB_TOKEN' },
      schedule: { kind: 'daily', at: '01:30', minutes: '60' },
    }
    const body = definitionOf(meta, { kind: 'rest' })
    expect(body).toMatchObject({
      format: 'relay-pipeline/1',
      slug: 'mes-lots',
      system: 'MES',
      target: { server: 'http://hub', token_env: 'HUB_TOKEN' },
      schedule: { daily_at: '01:30' },
    })
    expect(definitionOf({ ...meta, schedule: { ...meta.schedule, kind: 'manual' } }, {})).not.toHaveProperty(
      'schedule',
    )
    expect(recipeOf('mes_lot', 'LOT_ID', '', { made_on: 'MADE', qty: 'QTY', skip: '' }, new Set(['made_on']))).toEqual({
      format: 'sp-table/1',
      title: 'mes_lot(새 연결)',
      types: [
        {
          type_slug: 'mes_lot',
          key: { column: 'LOT_ID' },
          label: { column: 'LOT_ID' },
          fields: { made_on: { column: 'MADE', date: true }, qty: { column: 'QTY' } },
        },
      ],
    })
  })
})
