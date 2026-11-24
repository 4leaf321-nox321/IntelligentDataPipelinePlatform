/**
 * 비밀 — 이름 · 상태 · 쓰는 곳만 보이고 값은 없다. 사람이 넣고(관리자만), `?name=` 이면 그
 * 비밀의 입력 창이 바로 열린다(AI 가 건네는 링크).
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SecretOut } from '@/modules/relay/api'

const relayApi = vi.hoisted(() => ({
  secrets: vi.fn(),
  putSecret: vi.fn(),
  deleteSecret: vi.fn(),
}))
const auth = vi.hoisted(() => ({ admin: true }))
vi.mock('@/shared/auth/AuthContext', () => ({
  useAuth: () => ({ user: { id: 'u1', is_system_admin: auth.admin, memberships: [] } }),
}))
vi.mock('@/modules/relay/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/modules/relay/api')>()),
  relayApi,
}))

const SECRETS: SecretOut[] = [
  {
    name: 'MES_LOT_TOKEN',
    status: 'missing',
    note: '아직 넣지 않았습니다 — 이 비밀을 쓰는 원천 · 대상에 닿지 못합니다.',
    uses: [{ pipeline: 'mes-lots', role: '원천 인증', state: '요청됨(정의 전)' }],
    updated_at: null,
    updated_by: null,
    forbidden: false,
  },
  {
    name: 'MFG_HUB_TOKEN',
    status: 'screen',
    note: '',
    uses: [{ pipeline: 'plm-models', role: '대상 토큰', state: '사용 중' }],
    updated_at: '2026-11-17T01:02:03+00:00',
    updated_by: 'ops@corp.local',
    forbidden: false,
  },
  {
    name: 'PLM_PASSWORD',
    status: 'server',
    note: '서버 설정(.env)에 있습니다.',
    uses: [{ pipeline: 'plm-models', role: '원천 인증', state: '사용 중' }],
    updated_at: null,
    updated_by: null,
    forbidden: false,
  },
]

async function show(path = '/relay/secrets') {
  const { default: Page } = await import('@/modules/relay/SecretsPage')
  render(
    <MemoryRouter initialEntries={[path]}>
      <Page />
    </MemoryRouter>,
  )
}

const rowOf = (name: string) => within(screen.getByText(name).closest('tr')!)

describe('비밀', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    auth.admin = true
    relayApi.secrets.mockResolvedValue(SECRETS)
    relayApi.putSecret.mockResolvedValue({ name: 'MES_LOT_TOKEN', status: 'screen' })
    relayApi.deleteSecret.mockResolvedValue({ name: 'MFG_HUB_TOKEN', status: 'deleted' })
  })

  it('상태 · 쓰는 곳 · 마지막 입력 — 값은 없다', async () => {
    await show()
    expect(await screen.findByText('MES_LOT_TOKEN')).toBeInTheDocument()
    expect(screen.getByTestId('secrets-missing')).toHaveTextContent('미입력 인증 정보 1개')
    const missing = rowOf('MES_LOT_TOKEN')
    expect(missing.getByText('미입력')).toBeInTheDocument()
    // 정의 전 요청은 상세로 갈 곳이 없다 — 글자만.
    expect(missing.queryByRole('link')).not.toBeInTheDocument()
    expect(missing.getByRole('button', { name: '입력' })).toBeInTheDocument()

    const screenRow = rowOf('MFG_HUB_TOKEN')
    expect(screenRow.getByText('입력됨')).toBeInTheDocument()
    expect(screenRow.getByRole('link', { name: 'plm-models' })).toHaveAttribute(
      'href',
      '/relay/pipelines/plm-models',
    )
    expect(screenRow.getByText('ops@corp.local')).toBeInTheDocument()
    expect(screenRow.getByRole('button', { name: '변경' })).toBeInTheDocument()
    expect(screenRow.getByRole('button', { name: '삭제' })).toBeInTheDocument()

    // 서버 설정은 화면에서 지울 것이 없다.
    const server = rowOf('PLM_PASSWORD')
    expect(server.getByText('서버 설정')).toBeInTheDocument()
    expect(server.queryByRole('button', { name: '삭제' })).not.toBeInTheDocument()
  })

  it('입력 — 비밀번호 칸으로 받아 저장하고 다시 읽는다', async () => {
    await show()
    await screen.findByText('MES_LOT_TOKEN')
    fireEvent.click(rowOf('MES_LOT_TOKEN').getByRole('button', { name: '입력' }))
    const field = await screen.findByLabelText('값')
    expect(field).toHaveAttribute('type', 'password')
    fireEvent.change(field, { target: { value: 's3cret' } })
    fireEvent.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(relayApi.putSecret).toHaveBeenCalledWith('MES_LOT_TOKEN', 's3cret'))
    await waitFor(() => expect(relayApi.secrets).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('s3cret')).not.toBeInTheDocument()
  })

  it('?name= 이면 그 비밀의 입력 창이 바로 열린다', async () => {
    await show('/relay/secrets?name=MES_LOT_TOKEN')
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('MES_LOT_TOKEN')).toBeInTheDocument()
    expect(within(dialog).getByText(/mes-lots · 원천 인증/)).toBeInTheDocument()
  })

  it('지우기 — 무엇을 잃는지 적고 지운다', async () => {
    await show()
    fireEvent.click(await screen.findByRole('button', { name: '삭제' }))
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent('서버 설정(.env)에 같은 이름이 있으면')
    fireEvent.click(within(dialog).getByRole('button', { name: '삭제' }))
    await waitFor(() => expect(relayApi.deleteSecret).toHaveBeenCalledWith('MFG_HUB_TOKEN'))
  })

  it('관리자가 아니면 넣는 단추가 없다', async () => {
    auth.admin = false
    await show('/relay/secrets?name=MES_LOT_TOKEN')
    expect(await screen.findByText('MES_LOT_TOKEN')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '입력' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
