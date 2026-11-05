/**
 * 레시피 편집 — 출발 판을 불러 칸의 규칙을 고치고 초안으로 저장한다. 바뀐 칸을 저장 전에 보이고, 빈 열 이름 ·
 * 읽히지 않는 본문은 저장하지 않는다. 여러 행 모으기 같은 규칙은 화면에서 읽기만 한다.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { RecipeVersion } from '@/modules/relay/api'
import { RecipeEditor } from '@/modules/relay/RecipeEditor'

const relayApi = vi.hoisted(() => ({
  recipe: vi.fn(),
  recipeFile: vi.fn(),
  sample: vi.fn(),
  saveRecipe: vi.fn(),
}))
vi.mock('@/modules/relay/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/relay/api')>()),
  relayApi,
}))

const BODY = {
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
}

const version = (over: Partial<RecipeVersion>): RecipeVersion => ({
  version: 3,
  status: 'active',
  note: '',
  client: '화면',
  created_at: '2026-11-05T00:00:00Z',
  activated_at: '2026-11-05T01:00:00Z',
  ...over,
})

function mount(versions: RecipeVersion[] = [version({})]) {
  const onSaved = vi.fn()
  const onClose = vi.fn()
  render(<RecipeEditor slug="plm-tasks" versions={versions} onSaved={onSaved} onClose={onClose} />)
  return { onSaved, onClose }
}

async function modelFields() {
  await screen.findByTestId('recipe-field-key')
  await userEvent.selectOptions(screen.getByLabelText('편집할 타입'), 'plm_model')
  return screen.getByTestId('recipe-field-model_kind')
}

describe('레시피 편집', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    relayApi.recipe.mockResolvedValue({ ...version({}), body: BODY })
    relayApi.sample.mockResolvedValue({ columns: ['MODEL_ID', 'KIND', 'KIND_CD'], total: 3, rows: [], note: '' })
    relayApi.saveRecipe.mockResolvedValue({ version: 4, problems: [] })
  })

  it('사용 중인 판에서 출발해 열 · 대응을 고치고, 바뀐 칸을 보인 뒤 초안으로 저장한다', async () => {
    const { onSaved } = mount()
    await waitFor(() => expect(relayApi.recipe).toHaveBeenCalledWith('plm-tasks', 3))
    expect(screen.getByLabelText('출발 판')).toHaveDisplayValue('사용 중인 판 3')
    const row = within(await modelFields())
    expect(screen.getByRole('button', { name: '초안 저장' })).toBeDisabled() // 바뀐 것이 없다

    const column = row.getByLabelText('model_kind 열')
    await userEvent.clear(column)
    await userEvent.type(column, 'KIND_CD')
    await userEvent.click(row.getByRole('button', { name: 'model_kind 대응 펼치기' }))
    const raw = row.getByLabelText('대응 1 원천 값')
    await userEvent.clear(raw)
    await userEvent.type(raw, '01')
    await userEvent.click(row.getByRole('button', { name: '대응 2 삭제' }))
    expect(screen.getByTestId('recipe-changes')).toHaveTextContent('출발 판에서 바뀐 것: plm_model.model_kind')
    // 원천 열 목록을 고르게 준다(미러에서).
    expect(document.querySelector('#recipe-columns-plm-tasks option[value="KIND_CD"]')).not.toBeNull()

    await userEvent.type(screen.getByLabelText('메모'), '원천 코드가 01 로 바뀜')
    await userEvent.click(screen.getByRole('button', { name: '초안 저장' }))
    await waitFor(() => expect(relayApi.saveRecipe).toHaveBeenCalled())
    const [slug, body, note] = relayApi.saveRecipe.mock.calls[0]
    expect(slug).toBe('plm-tasks')
    expect(note).toBe('원천 코드가 01 로 바뀜')
    expect(body.types[1].fields.model_kind).toEqual({ column: 'KIND_CD', map: { '01': '양산' } })
    expect(body.types[1].fields.tags).toEqual({ collect: { column: 'TAG' } }) // 건드리지 않은 칸은 그대로
    expect(onSaved).toHaveBeenCalledWith({ version: 4, problems: [] })
  })

  it('칸을 추가 · 삭제하고, 빈 열 이름은 저장을 막는다 — 모으기 규칙은 읽기만', async () => {
    mount()
    await modelFields()
    expect(within(screen.getByTestId('recipe-field-tags')).getByText(/본문\(JSON\)」 에서 수정합니다/)).toBeInTheDocument()
    expect(within(screen.getByTestId('recipe-field-key')).queryByRole('button', { name: 'key 칸 삭제' })).toBeNull()

    await userEvent.type(screen.getByLabelText('새 칸 이름'), 'region')
    await userEvent.click(screen.getByRole('button', { name: '칸 추가' }))
    expect(screen.getByText('plm_model.region: 열 이름이 비었습니다.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '초안 저장' })).toBeDisabled()
    await userEvent.type(within(screen.getByTestId('recipe-field-region')).getByLabelText('region 열'), 'REGION')
    await userEvent.click(screen.getByRole('button', { name: 'task 칸 삭제' }))
    expect(screen.getByTestId('recipe-changes')).toHaveTextContent(
      'plm_model.task(칸 삭제) · plm_model.region(칸 추가)',
    )
    expect(screen.getByRole('button', { name: '초안 저장' })).toBeEnabled()
  })

  it('본문(JSON) — 읽히지 않으면 저장하지 않고 까닭을 말한다', async () => {
    mount()
    await screen.findByTestId('recipe-field-key')
    await userEvent.click(screen.getByRole('button', { name: '본문(JSON)' }))
    const text = screen.getByLabelText('레시피 본문')
    expect((text as HTMLTextAreaElement).value).toContain('"plm_model"')
    await userEvent.clear(text)
    await userEvent.type(text, '{{"format": ')
    await userEvent.click(screen.getByRole('button', { name: '초안 저장' }))
    expect(screen.getByText(/본문을 JSON 으로 읽지 못했습니다/)).toBeInTheDocument()
    expect(relayApi.saveRecipe).not.toHaveBeenCalled()
  })

  it('사용 중인 판이 없으면 정의 파일에서 출발하고, 파일에도 없으면 그렇다고 말한다', async () => {
    relayApi.recipeFile.mockResolvedValue({ path: '', body: null })
    mount([version({ version: 2, status: 'draft', activated_at: null, note: 'AI 초안' })])
    await waitFor(() => expect(relayApi.recipeFile).toHaveBeenCalledWith('plm-tasks'))
    expect(await screen.findByText(/정의 파일에 레시피가 없습니다/)).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('출발 판'), '2')
    await userEvent.click(screen.getByRole('button', { name: '불러오기' }))
    await waitFor(() => expect(relayApi.recipe).toHaveBeenCalledWith('plm-tasks', 2))
    expect(await screen.findByTestId('recipe-field-key')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '판 2(초안 · AI 초안)' })).toBeInTheDocument()
  })
})
