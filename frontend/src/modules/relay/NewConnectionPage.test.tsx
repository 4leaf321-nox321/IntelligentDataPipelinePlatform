/**
 * 새 연결 — 원천을 탐색해 칸을 고르고, 정의 판 초안 → 레시피 판 초안 → 시험까지 화면에서. 사용은 상세에서
 * 한다(여기에는 「사용」 이 없다). 대상에 타입이 없으면 SP 타입 정의 초안을 짓는다. 시스템 관리자만.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { AdapterKinds, DefinitionTrial } from '@/modules/relay/api'

const relayApi = vi.hoisted(() => ({
  adapterKinds: vi.fn(),
  discover: vi.fn(),
  saveDefinition: vi.fn(),
  targetSchema: vi.fn(),
  typeDraft: vi.fn(),
  saveRecipe: vi.fn(),
  testDefinition: vi.fn(),
}))
const auth = vi.hoisted(() => ({ admin: true }))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', is_system_admin: auth.admin, memberships: [] } }),
}))
vi.mock('@/modules/relay/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/relay/api')>()),
  relayApi,
}))

const f = (name: string, type = 'str', required = false) => ({ name, type, required, default: null, doc: `${name} 설명` })
const KINDS: AdapterKinds = {
  kinds: [
    {
      kind: 'rest',
      doc: '',
      required: ['base_url', 'path', 'key', 'columns'],
      fields: [
        f('base_url', 'str', true),
        f('path', 'str', true),
        f('key', 'str | Sequence[str]', true),
        f('columns', 'Mapping[str, str]', true),
        f('rows_path'),
        f('paging'),
        f('explode'),
        f('detail_path'),
        f('min_interval', 'float'),
        f('auth', 'Auth'),
      ],
    },
  ],
  auth: [],
  secret_fields: ['token'],
}

const TRIAL: DefinitionTrial = {
  version: 1,
  problems: [],
  target: {
    server: 'http://hub',
    reachable: true,
    slug: 'hub',
    version: '1',
    token: '정상',
    types_present: ['mes_lot'],
    types_missing: [],
    managed_by: {},
  },
  source: {
    ok: true,
    probe: '응답 정상',
    rows: 3,
    deleted: 0,
    complete: true,
    watermark: null,
    columns: ['LOT_ID', 'MADE_ON'],
    always_empty: [],
    sample: [{ key: 'L1', LOT_ID: 'L1', MADE_ON: '2026-11-01' }],
  },
}

async function mount() {
  const { default: NewConnectionPage } = await import('@/modules/relay/NewConnectionPage')
  render(
    <MemoryRouter>
      <NewConnectionPage />
    </MemoryRouter>,
  )
}

describe('새 연결', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
    relayApi.adapterKinds.mockResolvedValue(KINDS)
    relayApi.discover.mockResolvedValue({
      kind: 'rest',
      ok: true,
      found: {
        paths: [
          { path: 'LotId', sample: 'L1' },
          { path: 'Name', sample: '로트 1' },
          { path: 'MadeOn', sample: '2026-11-01' },
        ],
        columns: { LOT_ID: 'LotId', NAME: 'Name', MADE_ON: 'MadeOn' },
      },
    })
    relayApi.saveDefinition.mockResolvedValue({ version: 1, problems: ['원천: 울타리 밖 — 시험 전에 해결'] })
    relayApi.targetSchema.mockResolvedValue({
      target: '허브',
      server: 'http://hub',
      note: '',
      types: { mes_lot: { made_on: { key: 'made_on', label: '생산일', data_type: 'date' } } },
    })
    relayApi.saveRecipe.mockResolvedValue({ version: 1, problems: [] })
    relayApi.testDefinition.mockResolvedValue(TRIAL)
  })

  it('탐색 → 칸 선택 → 정의 판 초안 → 레시피 판 초안 → 시험', async () => {
    await mount()
    await userEvent.type(await screen.findByLabelText('base_url'), 'http://mes')
    await userEvent.type(screen.getByLabelText('path'), '/lots')
    await userEvent.selectOptions(screen.getByLabelText('인증 방식'), 'query')
    await userEvent.type(screen.getByLabelText('비밀의 환경 변수 이름'), 'MES_KEY')
    await userEvent.type(screen.getByLabelText('질의 이름(예: api_key)'), 'api_key')
    await userEvent.click(screen.getByRole('button', { name: /탐색/ }))
    await waitFor(() =>
      expect(relayApi.discover).toHaveBeenCalledWith({
        kind: 'rest',
        base_url: 'http://mes',
        path: '/lots',
        auth: { kind: 'query', token_env: 'MES_KEY', param: 'api_key' },
      }),
    )

    await userEvent.click(await screen.findByLabelText('LotId 식별자'))
    await userEvent.click(screen.getByLabelText('Name 포함')) // 빼고
    await userEvent.type(screen.getByLabelText('파이프라인 slug'), 'mes-lots')
    await userEvent.type(screen.getByLabelText('원천 이름'), 'mes')
    await userEvent.type(screen.getByLabelText('대상 SP 주소'), 'http://hub')
    await userEvent.type(screen.getByLabelText('대상 토큰의 환경 변수'), 'HUB_TOKEN')
    await userEvent.click(screen.getByRole('button', { name: /정의 판 초안 저장/ }))
    await waitFor(() => expect(relayApi.saveDefinition).toHaveBeenCalled())
    const [slug, body] = relayApi.saveDefinition.mock.calls[0]
    expect(slug).toBe('mes-lots')
    expect(body.adapter).toMatchObject({ key: 'LotId', columns: { LOT_ID: 'LotId', MADE_ON: 'MadeOn' } })
    expect(body.target).toEqual({ name: '', server: 'http://hub', token_env: 'HUB_TOKEN' })
    expect(await screen.findByText('정의 판 1 초안을 저장했습니다')).toBeInTheDocument()
    expect(screen.getByText(/울타리 밖/)).toBeInTheDocument()

    await userEvent.selectOptions(await screen.findByLabelText('대상 타입'), 'mes_lot')
    await userEvent.selectOptions(screen.getByLabelText('made_on 의 열'), 'MADE_ON')
    await userEvent.click(screen.getByRole('button', { name: /레시피 판 초안 저장/ }))
    await waitFor(() => expect(relayApi.saveRecipe).toHaveBeenCalled())
    expect(relayApi.saveRecipe.mock.calls[0][1]).toMatchObject({
      types: [{ type_slug: 'mes_lot', key: { column: 'LOT_ID' }, fields: { made_on: { column: 'MADE_ON', date: true } } }],
    })

    await userEvent.click(screen.getByRole('button', { name: /정의 시험/ }))
    await waitFor(() =>
      expect(relayApi.testDefinition).toHaveBeenCalledWith('mes-lots', 1, { recipe_version: 1 }),
    )
    expect(screen.getByRole('link', { name: /파이프라인 상세로/ })).toHaveAttribute('href', '/relay/pipelines/mes-lots')
    expect(screen.queryByRole('button', { name: /^사용$/ })).not.toBeInTheDocument()
  })

  it('대상에 타입이 없으면 SP 타입 정의 초안을 짓고 그 레시피를 저장한다', async () => {
    relayApi.typeDraft.mockResolvedValue({
      ontology: { types: [{ slug: 'mes_lot', label: '로트', properties: [{ key: 'made_on', label: 'MADE_ON', data_type: 'date' }] }] },
      recipe: { format: 'sp-table/1', types: [{ type_slug: 'mes_lot' }] },
    })
    await mount()
    await userEvent.type(await screen.findByLabelText('base_url'), 'http://mes')
    await userEvent.type(screen.getByLabelText('path'), '/lots')
    await userEvent.click(screen.getByRole('button', { name: /탐색/ }))
    await userEvent.click(await screen.findByLabelText('LotId 식별자'))
    await userEvent.type(screen.getByLabelText('파이프라인 slug'), 'mes-lots')
    await userEvent.type(screen.getByLabelText('원천 이름'), 'mes')
    await userEvent.type(screen.getByLabelText('대상 SP 주소'), 'http://hub')
    await userEvent.type(screen.getByLabelText('대상 토큰의 환경 변수'), 'HUB_TOKEN')
    await userEvent.click(screen.getByRole('button', { name: /정의 판 초안 저장/ }))

    await userEvent.selectOptions(await screen.findByLabelText('대상 타입'), '__new__')
    await userEvent.type(screen.getByLabelText('새 타입 slug'), 'mes_lot')
    await userEvent.click(screen.getByRole('button', { name: /타입 정의 초안 작성/ }))
    await waitFor(() =>
      expect(relayApi.typeDraft).toHaveBeenCalledWith({
        type_slug: 'mes_lot',
        label: '',
        key_column: 'LOT_ID',
        label_column: '',
        columns: { LOT_ID: ['L1'], NAME: ['로트 1'], MADE_ON: ['2026-11-01'] },
        system: '',
      }),
    )
    expect(await screen.findByText(/made_on\(date\)/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /타입 정의 초안 다운로드/ })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /레시피 판 초안 저장/ }))
    await waitFor(() =>
      expect(relayApi.saveRecipe).toHaveBeenCalledWith('mes-lots', { format: 'sp-table/1', types: [{ type_slug: 'mes_lot' }] }, '새 연결 — 첫 판'),
    )
  })

  it('식별자를 고르지 않으면 정의를 저장하지 않고 까닭을 말한다 · 관리자가 아니면 만들 수 없다', async () => {
    await mount()
    await userEvent.type(await screen.findByLabelText('base_url'), 'http://mes')
    await userEvent.type(screen.getByLabelText('path'), '/lots')
    await userEvent.click(screen.getByRole('button', { name: /탐색/ }))
    await screen.findByLabelText('LotId 식별자')
    await userEvent.click(screen.getByRole('button', { name: /정의 판 초안 저장/ }))
    expect(await screen.findByText(/식별자로 쓸 칸을 하나 이상 선택하세요/)).toBeInTheDocument()
    expect(relayApi.saveDefinition).not.toHaveBeenCalled()
  })

  it('관리자가 아니면 만들 수 없다', async () => {
    auth.admin = false
    await mount()
    expect(screen.getByText('새 연결은 시스템 관리자가 만듭니다.')).toBeInTheDocument()
    expect(within(document.body).queryByRole('button', { name: /탐색/ })).not.toBeInTheDocument()
  })
})
