/**
 * 레시피 편집의 규칙 — 규칙 종류를 읽고 바꾸고, 대응을 줄로 다루고, 출발 판에서 바뀐 것과 저장을 막을 것을
 * 센다. 화면 시험은 `RecipeEditor.test.tsx`.
 */

import { describe, expect, it } from 'vitest'

import {
  blockers,
  changes,
  clone,
  mapPairs,
  ruleKind,
  typesOf,
  withField,
  withKind,
  withPairs,
  withType,
} from '@/modules/relay/recipeEdit'
import type { RecipeDoc } from '@/modules/relay/recipeEdit'

const RECIPE: RecipeDoc = {
  format: 'sp-table/1',
  title: '시험',
  types: [
    { type_slug: 'plm_task', key: { column: 'TASK_CODE' }, label: { column: 'TASK_NAME' } },
    {
      type_slug: 'plm_model',
      key: { column: 'MODEL_ID' },
      label: { column: 'MODEL_NAME' },
      fields: {
        task: { key_of: 'plm_task' },
        model_kind: { column: 'KIND', map: { A: '양산', B: '선행' } },
        tags: { collect: { column: 'TAG' } },
      },
    },
  ],
  relations: [{ relation: 'plm_derived_from', type_slug: 'plm_model', src: { key_of: 'plm_model' }, dst: { column: 'BASE' } }],
}

describe('레시피 편집 규칙', () => {
  it('규칙 종류 — 열 · 다른 타입의 식별자 · 고정값, 모으기 · 해석기 · 대조는 화면에서 읽기만', () => {
    expect(ruleKind({ column: 'A' })).toBe('column')
    expect(ruleKind({ key_of: 'plm_task' })).toBe('key_of')
    expect(ruleKind({ value: '고정' })).toBe('value')
    expect(ruleKind({ collect: { column: 'TAG' } })).toBe('complex')
    expect(ruleKind({ column: 'P', match: { keys: 'plm_part' } })).toBe('complex')
    expect(ruleKind(undefined)).toBe('column')
  })

  it('종류를 바꾸면 그 종류에 없는 칸은 버리고, 열로 돌아오면 열의 칸은 남긴다', () => {
    expect(withKind({ column: 'KIND', map: { A: '양산' } }, 'value')).toEqual({ value: '' })
    expect(withKind({ key_of: 'x' }, 'column')).toEqual({ column: '' })
    expect(withKind({ column: 'D', date: true, extra: 1 }, 'column')).toEqual({ column: 'D', date: true })
  })

  it('대응은 줄로 — 빈 원천 값은 버리고, 줄이 없으면 map 을 지운다', () => {
    const spec = { column: 'KIND', map: { A: '양산', B: '선행' } }
    expect(mapPairs(spec)).toEqual([
      ['A', '양산'],
      ['B', '선행'],
    ])
    expect(withPairs(spec, [['A', '양산'], [' C ', '파생'], ['', '버려짐']])).toEqual({
      column: 'KIND',
      map: { A: '양산', C: '파생' },
    })
    expect(withPairs(spec, [])).toEqual({ column: 'KIND' })
  })

  it('바뀐 것 — 칸 수정 · 추가 · 삭제, 관계, 그 밖을 따로 센다', () => {
    const after = clone(RECIPE)
    const model = typesOf(after)[1]
    let next = withField(model, 'model_kind', { column: 'KIND_CD', map: { '01': '양산' } })
    next = withField(next, 'region', { column: 'REGION' })
    next = withField(next, 'task', null)
    let doc = withType(after, 'plm_model', next)
    expect(changes(RECIPE, doc)).toEqual([
      'plm_model.task(칸 삭제)',
      'plm_model.model_kind',
      'plm_model.region(칸 추가)',
    ])
    doc = { ...doc, title: '바뀜', relations: [] }
    expect(changes(RECIPE, doc)).toContain('관계')
    expect(changes(RECIPE, doc)).toContain('그 밖(title · dictionaries …)')
    expect(changes(RECIPE, clone(RECIPE))).toEqual([])
  })

  it('저장을 막을 것 — 빈 열 이름, 레시피에 없는 다른 타입, 빈 types', () => {
    expect(blockers(RECIPE)).toEqual([])
    const model = typesOf(RECIPE)[1]
    const broken = withType(
      RECIPE,
      'plm_model',
      withField(withField(model, 'region', { column: ' ' }), 'task', { key_of: 'plm_project' }),
    )
    expect(blockers(broken)).toEqual([
      'plm_model.task: 다른 타입 「plm_project」 이 레시피에 없습니다.',
      'plm_model.region: 열 이름이 비었습니다.',
    ])
    expect(blockers({ format: 'sp-table/1', types: [] })).toEqual([
      'types 가 비었습니다 — 무엇을 만들지 적어야 합니다.',
    ])
  })
})
