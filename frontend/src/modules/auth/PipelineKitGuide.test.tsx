/**
 * 정제 도구 키트 안내 — **서버가 같은 판을 내려주고, 등록은 단추 하나 · 더블클릭 하나.**
 *
 * 받는 사람은 개발자가 아니다(Claude Desktop 만 쓰는 데이터 담당자). 「이 PC 에 등록」 은 이
 * 플랫폼용 토큰을 하나 발급해 `install.cmd` 가 찾는 한 줄로 복사한다 — 사람이 범위를 고르거나
 * 명령을 고쳐 칠 일이 없다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const client = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), downloadFile: vi.fn() }))
vi.mock('@/shared/api/client', async (original) => {
  const real = await original<typeof import('@/shared/api/client')>()
  return {
    ...real,
    api: { ...real.api, get: client.get, post: client.post },
    downloadFile: client.downloadFile,
  }
})
const clipboard = vi.hoisted(() => ({ copyText: vi.fn() }))
vi.mock('@/shared/lib/clipboard', () => clipboard)
const auth = vi.hoisted(() => ({ user: { is_system_admin: false } }))
vi.mock('@/shared/auth/AuthContext', () => ({ useAuth: () => ({ user: auth.user }) }))

import {
  appUrlFrom,
  PipelineKitGuide,
  REGISTRATION,
  registrationLine,
  setupCommand,
} from '@/modules/auth/PipelineKitGuide'

const KIT = {
  available: true,
  filename: 'sp-pipeline-v0.4.43.zip',
  version: 'v0.4.43',
  size_bytes: 58 * 1024 * 1024,
}

describe('정제 도구 키트 안내', () => {
  beforeEach(() => {
    client.get.mockReset().mockResolvedValue(KIT)
    client.post.mockReset().mockResolvedValue({ token: 'spt_new' })
    client.downloadFile.mockReset().mockResolvedValue(undefined)
    clipboard.copyText.mockReset().mockResolvedValue(undefined)
    auth.user = { is_system_admin: false }
  })

  it('서버가 들고 있는 같은 판을 내려받는다', async () => {
    render(<PipelineKitGuide token={null} />)
    const button = await screen.findByRole('button', { name: /sp-pipeline-v0\.4\.43\.zip/ })
    expect(button).toHaveTextContent('58MB')
    await userEvent.click(button)
    await waitFor(() =>
      expect(client.downloadFile).toHaveBeenCalledWith(
        '/server/pipeline-kit',
        'sp-pipeline-v0.4.43.zip',
      ),
    )
  })

  it('「이 PC 에 등록」 은 토큰을 하나 발급해 install.cmd 가 찾는 한 줄로 복사한다', async () => {
    const issued = vi.fn()
    render(<PipelineKitGuide token={null} onIssued={issued} />)
    await userEvent.click(await screen.findByRole('button', { name: /이 PC 에 등록 정보 복사/ }))
    await waitFor(() => expect(clipboard.copyText).toHaveBeenCalled())
    // 사람이 범위를 고르지 않는다 — 정제에 필요한 만큼만(정의는 관리자가 따로 켠다).
    expect(client.post).toHaveBeenCalledWith(
      '/auth/tokens',
      expect.objectContaining({ scopes: ['read', 'objects:write'] }),
    )
    const line = String(clipboard.copyText.mock.calls[0][0])
    expect(line.startsWith(`${REGISTRATION} `)).toBe(true)
    expect(JSON.parse(line.slice(REGISTRATION.length + 1))).toMatchObject({ token: 'spt_new' })
    expect(issued).toHaveBeenCalled() // 아래 토큰 목록이 다시 읽힌다
    expect(screen.getByText(/복사했습니다/)).toBeInTheDocument()
  })

  it('정의까지 넣는 범위는 시스템 관리자만 고를 수 있다', async () => {
    render(<PipelineKitGuide token={null} />)
    await screen.findByRole('button', { name: /이 PC 에 등록 정보 복사/ })
    expect(screen.queryByText(/정의\(타입 · 속성\)까지/)).toBeNull()

    auth.user = { is_system_admin: true }
    render(<PipelineKitGuide token={null} />)
    await userEvent.click(await screen.findByText(/정의\(타입 · 속성\)까지/))
    const buttons = screen.getAllByRole('button', { name: /이 PC 에 등록 정보 복사/ })
    await userEvent.click(buttons[buttons.length - 1])
    await waitFor(() =>
      expect(client.post).toHaveBeenCalledWith(
        '/auth/tokens',
        expect.objectContaining({ scopes: ['read', 'objects:write', 'ontology:write'] }),
      ),
    )
  })

  it('키트가 없는 설치(개발 · 옛 번들)는 그렇다고 말한다 — 단추를 안 세운다', async () => {
    client.get.mockResolvedValue({ ...KIT, available: false, size_bytes: 0 })
    render(<PipelineKitGuide token={null} />)
    expect(await screen.findByText(/키트가 들어 있지 않습니다/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /sp-pipeline/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /이 PC 에 등록/ })).toBeNull()
  })

  it('명령은 한 줄이고 접두어까지 든 주소와 이 설치의 이름을 쓴다', () => {
    // 줄 잇기 기호(cmd 의 ^ · PowerShell 의 `)를 쓰면 다른 셸에서 깨진 명령이 된다.
    const command = setupCommand(
      'spt_x',
      appUrlFrom('http://10.0.0.5:3030', '/rootdesign'),
      'rootdesign',
    )
    expect(command).not.toContain('\n')
    // **키트에 든 파이썬으로** — PC 에 파이썬이 없어도 된다(python.org 의 최신판은 휠이 없어
    // 거절됐고, PATH 체크를 빠뜨리면 「python 을 찾을 수 없다」 였다).
    expect(command.startsWith('python\\python.exe sp_setup.py ')).toBe(true)
    expect(command).toContain('--server http://10.0.0.5:3030/rootdesign ')
    // **이름으로 더한다** — 한 PC 가 플랫폼 여럿을 겨눈다. 이름이 없으면 덮어쓰게 된다.
    expect(command).toContain('--platform rootdesign ')
    expect(registrationLine('qings', 'http://h/qings', 't')).toBe(
      `${REGISTRATION} {"platform":"qings","server":"http://h/qings","token":"t"}`,
    )
  })
})
