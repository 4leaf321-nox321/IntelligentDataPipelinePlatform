/**
 * 서버 화면의 「HWAX 포털 로그인」 — 켜졌는데 공개 키가 안 닿으면 그렇다고 말한다.
 *
 * 설정만 보고 「켜짐」 이라고 하면 그 사실은 첫 사람이 포털로 로그인을 시도해야 드러난다.
 */

import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { PortalStatus } from '@/modules/auth/portal'

const status = vi.hoisted(() => vi.fn())
vi.mock('@/modules/auth/portal', () => ({ portalApi: { status } }))

const BASE: PortalStatus = {
  enabled: true,
  jwks_url: 'http://portal:8088/.well-known/jwks.json',
  audience: 'hwaxapp',
  issuer: 'https://portal.example',
  launch_url: 'https://portal.example/launch/hwaxapp',
  callback_path: '/hwaxapp/api/auth/portal-callback',
  reachable: true,
  kids: ['k1'],
  fetched_at: '2026-10-13T00:00:00Z',
  last_error: null,
  last_error_at: null,
  problems: [],
  delegation: {
    enabled: false,
    sso_path: '/api/auth/sso',
    allowed_ips: [],
    invalid_ips: [],
    scopes: ['read', 'ontology:write', 'objects:write'],
    token_seconds: 86400,
    live_tokens: 0,
    last_issued_at: null,
    last_refusal: null,
  },
}

async function show(body: PortalStatus) {
  status.mockResolvedValue(body)
  const { PortalLogin } = await import('@/modules/server/PortalLogin')
  render(<PortalLogin />)
}

describe('서버 화면 · HWAX 포털 로그인', () => {
  beforeEach(() => vi.clearAllMocks())

  it('켜져 있고 공개 키를 받았으면 kid 와 콜백 길을 보인다', async () => {
    await show(BASE)
    expect(await screen.findByText('켜짐')).toBeInTheDocument()
    expect(screen.getByText(/서명 키 1개 \(k1\)/)).toBeInTheDocument()
    expect(screen.getByText(/\/hwaxapp\/api\/auth\/portal-callback/)).toBeInTheDocument()
  })

  it('공개 키를 못 받으면 그렇다고 말하고 마지막 오류를 보인다', async () => {
    await show({
      ...BASE,
      reachable: false,
      kids: [],
      last_error: '포털 JWKS 를 못 받았다: ConnectError',
      last_error_at: '2026-10-13T00:01:00Z',
    })
    expect(await screen.findByText('공개 키 못 받음')).toBeInTheDocument()
    expect(screen.getByText(/포털로 오는 로그인을 확인할 수 없습니다/)).toBeInTheDocument()
    expect(screen.getByText(/ConnectError/)).toBeInTheDocument()
  })

  it('꺼져 있으면 켜는 길을 말하고, 설정이 안 맞는 것도 보인다', async () => {
    await show({
      ...BASE,
      enabled: false,
      reachable: null,
      kids: [],
      problems: ['PORTAL_LAUNCH_URL 은 있는데 PORTAL_JWKS_URL 이 없습니다'],
    })
    // 포털 로그인과 게이트웨이 위임 — 둘 다 꺼져 있다.
    expect(await screen.findAllByText('꺼짐')).toHaveLength(2)
    expect(screen.getByText(/HWAX 포털에 연결되어 있지 않습니다/)).toBeInTheDocument()
    expect(screen.getByText(/PORTAL_JWKS_URL 이 없습니다/)).toBeInTheDocument()
  })

  it('게이트웨이 위임이 꺼져 있으면 켜는 길을 말한다', async () => {
    await show(BASE)
    expect(await screen.findByText(/게이트웨이 위임/)).toBeInTheDocument()
    expect(screen.getByText(/HEAX_SSO_SECRET/)).toBeInTheDocument()
    expect(screen.getAllByText('꺼짐')).toHaveLength(1) // 포털 로그인은 켜짐, 위임만 꺼짐
  })

  it('게이트웨이 위임이 켜져 있으면 마지막 발급과 거절을 보인다', async () => {
    await show({
      ...BASE,
      delegation: {
        ...BASE.delegation,
        enabled: true,
        allowed_ips: ['10.1.2.3/32'],
        live_tokens: 3,
        last_issued_at: '2026-10-13T01:00:00Z',
        last_refusal: {
          at: '2026-10-13T02:00:00Z',
          status: 403,
          reason: '처음 보는 사람 x@corp — 계정을 만들지 않는다(JIT 없음)',
        },
      },
    })
    expect(await screen.findByText(/사용 중 3개/)).toBeInTheDocument()
    expect(screen.getByText('10.1.2.3/32')).toBeInTheDocument()
    expect(screen.getByText(/403 — 처음 보는 사람/)).toBeInTheDocument()
    expect(screen.getAllByText('켜짐')).toHaveLength(2)
  })

  it('허용 주소를 못 읽으면 모두 거절이라고 말한다', async () => {
    await show({
      ...BASE,
      delegation: { ...BASE.delegation, enabled: true, invalid_ips: ['portal-box'] },
      problems: ['HEAX_SSO_ALLOWED_IPS 에 읽지 못한 값이 있어 게이트웨이 위임 발급을 모두 거절합니다: portal-box'],
    })
    expect(await screen.findByText('모두 거절')).toBeInTheDocument()
  })
})
