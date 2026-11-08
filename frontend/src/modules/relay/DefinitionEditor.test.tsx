/**
 * 정의 편집 — 출발 판을 불러 칸을 고치고, 바뀐 칸을 보인 뒤 초안으로 저장한다(사용하지 않는다). 「본문(JSON)」
 * 은 읽히지 않으면 칸으로 돌아가지 않고 까닭을 말한다. 비밀을 적으면 저장하지 않는다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AdapterKinds, DefinitionVersion } from '@/modules/relay/api'

const relayApi = vi.hoisted(() => ({
  adapterKinds: vi.fn(),
  definition: vi.fn(),
  definitionFile: vi.fn(),
  saveDefinition: vi.fn(),
}))
vi.mock('@/modules/relay/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/relay/api')>()),
  relayApi,
}))

const f = (name: string, type = 'str', required = false, fallback: unknown = null) => ({
  name,
  type,
  required,
  default: fallback,
  doc: '',
})
const KINDS: AdapterKinds = {
  kinds: [
    {
      kind: 'rest',
      doc: '',
      required: ['base_url', 'path', 'key', 'columns'],
      fields: [
        f('base_url', 'str', true),
        f('path', 'str', true),
        f('page_size', 'int', false, 100),
        f('params', 'Mapping[str, str]'),
        f('key', 'str | Sequence[str]', true),
        f('columns', 'Mapping[str, str]', true),
        f('auth', 'Auth'),
      ],
    },
  ],
  auth: [],
  secret_fields: ['dsn', 'password', 'secret', 'token'],
}

const BODY = {
  format: 'relay-pipeline/1',
  slug: 'plm-tasks',
  source: 'plm',
  adapter: {
    kind: 'rest',
    base_url: 'http://plm.local',
    path: '/tasks',
    key: 'code',
    columns: { CODE: 'code', DUE: 'due' },
    auth: { kind: 'bearer', token_env: 'PLM_TOKEN' },
  },
  target: { name: '자주검증 Datahub', server: 'http://hub', token_env: 'HUB_TOKEN' },
  schedule: { daily_at: '01:00' },
}

const VERSIONS: DefinitionVersion[] = [
  {
    version: 3,
    status: 'active',
    note: '처음 판',
    client: '화면',
    created_at: '2026-11-02T10:00:00Z',
    activated_at: '2026-11-02T11:00:00Z',
    summary: '',
  },
]

async function mount(onSaved = vi.fn()) {
  const { DefinitionEditor } = await import('@/modules/relay/DefinitionEditor')
  render(<DefinitionEditor slug="plm-tasks" versions={VERSIONS} onSaved={onSaved} onClose={vi.fn()} />)
  return onSaved
}

describe('정의 편집', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    relayApi.adapterKinds.mockResolvedValue(KINDS)
    relayApi.definition.mockResolvedValue({ ...VERSIONS[0], body: BODY })
    relayApi.definitionFile.mockResolvedValue({ path: '/defs/plm-tasks.json', body: null, problems: [] })
    relayApi.saveDefinition.mockResolvedValue({ version: 5, problems: [] })
  })

  it('사용 중인 판을 불러 칸을 고치고, 바뀐 칸을 보인 뒤 초안으로 저장한다', async () => {
    const onSaved = await mount()
    await waitFor(() => expect(relayApi.definition).toHaveBeenCalledWith('plm-tasks', 3))
    const path = await screen.findByLabelText('path')
    expect(path).toHaveValue('/tasks')
    expect(screen.getByTestId('definition-changes')).toHaveTextContent('출발 판에서 바뀐 것이 없습니다.')
    expect(screen.getByRole('button', { name: '초안 저장' })).toBeDisabled()

    await userEvent.clear(path)
    await userEvent.type(path, '/v2/tasks')
    await userEvent.type(screen.getByLabelText('page_size'), '200')
    await userEvent.selectOptions(screen.getByLabelText('인증 방식'), 'ntlm')
    await userEvent.type(screen.getByLabelText('계정(CORP\\svc_relay)'), 'CORP\\svc')
    await userEvent.type(screen.getByLabelText('열 2 원천의 자리'), '.at')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: '일정' }), 'every')
    await userEvent.click(screen.getByLabelText('받기 점검 사용'))
    expect(screen.getByTestId('definition-changes')).toHaveTextContent(
      '출발 판에서 바뀐 것: adapter.path · adapter.columns.DUE · adapter.auth.kind · adapter.auth.user(추가) · adapter.page_size(추가) · schedule.daily_at(삭제) · schedule.every_minutes(추가) · intake(추가)',
    )
    await userEvent.type(screen.getByLabelText('정의 메모'), '경로 v2')
    await userEvent.click(screen.getByRole('button', { name: '초안 저장' }))
    await waitFor(() => expect(relayApi.saveDefinition).toHaveBeenCalled())
    const [slug, body, note] = relayApi.saveDefinition.mock.calls[0]
    expect([slug, note]).toEqual(['plm-tasks', '경로 v2'])
    expect(body).toEqual({
      ...BODY,
      adapter: {
        ...BODY.adapter,
        path: '/v2/tasks',
        page_size: 200,
        columns: { CODE: 'code', DUE: 'due.at' },
        auth: { kind: 'ntlm', token_env: 'PLM_TOKEN', user: 'CORP\\svc' },
      },
      schedule: { every_minutes: 60 },
      intake: false,
    })
    expect(onSaved).toHaveBeenCalledWith({ version: 5, problems: [] })
  })

  it('본문(JSON) — 읽히지 않으면 까닭을 말하고, 비밀을 적으면 저장하지 않는다', async () => {
    await mount()
    await screen.findByLabelText('path')
    await userEvent.click(screen.getByRole('button', { name: '본문(JSON)' }))
    const text = screen.getByLabelText('정의 본문')
    await userEvent.clear(text)
    await userEvent.type(text, '{{not json')
    await userEvent.click(screen.getByRole('button', { name: '칸' }))
    expect(screen.getByText(/본문을 JSON 으로 읽지 못했습니다/)).toBeInTheDocument()
    expect(screen.getByLabelText('정의 본문')).toBeInTheDocument() // 칸으로 돌아가지 않았다

    const leaked = { ...BODY, adapter: { ...BODY.adapter, auth: { kind: 'bearer', token: 's3cret' } } }
    await userEvent.clear(text)
    await userEvent.click(text)
    await userEvent.paste(JSON.stringify(leaked))
    await userEvent.click(screen.getByRole('button', { name: '칸' }))
    expect(await screen.findByText(/adapter.auth.token: 비밀은 정의에 적지 않습니다/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '초안 저장' })).toBeDisabled()
    expect(relayApi.saveDefinition).not.toHaveBeenCalled()
  })

  it('정의 파일이 없으면 그렇다고 말한다', async () => {
    const { DefinitionEditor } = await import('@/modules/relay/DefinitionEditor')
    render(<DefinitionEditor slug="plm-tasks" versions={[]} onSaved={vi.fn()} onClose={vi.fn()} />)
    expect(await screen.findByText(/같은 slug 의 정의 파일이 없습니다/)).toBeInTheDocument()
  })
})
