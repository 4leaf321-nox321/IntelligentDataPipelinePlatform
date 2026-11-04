/**
 * **새 판이 나왔을 때** — 열어 둔 탭이 옛 조각을 찾다 멈추지 않게(`shared/newBuild`).
 *
 * 업데이트는 서버 이미지를 통째로 바꾸므로 옛 이름의 조각은 사라진다. 업데이트 전부터 열어
 * 둔 탭이 아직 안 가 본 메뉴를 누르면 「Failed to fetch dynamically imported module」 이
 * 떴다 — 새로 고치면 나았다. 그래서 못 받으면 **한 번** 새로 고치고(되풀이는 막는다), 새 판이
 * 나온 것을 미리 띠로 말한다.
 */

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const systemApi = vi.hoisted(() => ({ health: vi.fn() }))
vi.mock('@/shared/api/system', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/shared/api/system')>()),
  systemApi,
}))

const reload = vi.fn()
const realLocation = window.location

beforeEach(() => {
  vi.resetModules()
  vi.clearAllMocks()
  window.sessionStorage.clear()
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...realLocation, reload },
  })
})

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: realLocation })
})

describe('조각을 못 받으면', () => {
  it('브라우저마다 다른 말을 같은 것으로 알아본다', async () => {
    const { isChunkLoadError } = await import('@/shared/newBuild')
    for (const message of [
      'Failed to fetch dynamically imported module: https://x/assets/JobsPage-B3dqRXw1.js', // Chrome
      'error loading dynamically imported module: https://x/assets/a.js', // Firefox
      'Importing a module script failed.', // Safari
      'Unable to preload CSS for https://x/assets/a.css',
    ]) {
      expect(isChunkLoadError(new TypeError(message))).toBe(true)
    }
    expect(isChunkLoadError(new Error("Cannot read properties of undefined (reading 'x')"))).toBe(
      false,
    )
  })

  it('한 번 새로 고치고, 1분 안에 또 못 받으면 고치지 않는다(깜박임만 되풀이된다)', async () => {
    const first = await import('@/shared/newBuild')
    expect(first.reloadForNewBuild(1_000_000)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)

    // 새로 고친 뒤의 새 화면 — 모듈이 새로 뜬다.
    vi.resetModules()
    const second = await import('@/shared/newBuild')
    expect(second.reloadForNewBuild(1_000_000 + 30_000)).toBe(false)
    expect(reload).toHaveBeenCalledTimes(1)
    expect(second.reloadForNewBuild(1_000_000 + second.QUIET_MS + 1)).toBe(true)
    expect(reload).toHaveBeenCalledTimes(2)
  })

  it('Vite 가 알리면 막고(오류를 안 던진다) 새로 고친다', async () => {
    const { listenForStaleChunks } = await import('@/shared/newBuild')
    listenForStaleChunks()
    const event = new Event('vite:preloadError', { cancelable: true })
    window.dispatchEvent(event)
    expect(event.defaultPrevented).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('오류 경계는 고칠 수 없을 때 「파일을 받지 못했다」 와 새로 고침을 보인다', async () => {
    window.sessionStorage.setItem('new-build-reloaded-at', String(Date.now()))
    const { ErrorBoundary } = await import('@/shared/components/ErrorBoundary')
    const Broken = () => {
      throw new TypeError('Failed to fetch dynamically imported module: https://x/a.js')
    }
    vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    )
    expect(screen.getByText('이 화면의 파일을 받지 못했습니다')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '새로 고침' })).toBeInTheDocument()
    expect(reload).not.toHaveBeenCalled()
  })
})

describe('새 판이 나왔다는 띠', () => {
  it('서버가 이 화면보다 새 판일 때만 — 옛 판(이중화를 올리는 중) · 모르는 판은 아니다', async () => {
    const { isNewer } = await import('@/shared/newBuild')
    expect(isNewer('v0.4.48', 'v0.4.47')).toBe(true)
    expect(isNewer('v0.5.0', 'v0.4.47')).toBe(true)
    expect(isNewer('v0.4.47', 'v0.4.47')).toBe(false)
    expect(isNewer('v0.4.46', 'v0.4.47')).toBe(false)
    expect(isNewer('unknown', 'v0.4.47')).toBe(false)
    expect(isNewer('v0.4.48', 'dev')).toBe(false)
  })

  it('새 판이면 띠와 새로 고침 단추가 뜨고, 같으면 안 뜬다', async () => {
    systemApi.health.mockResolvedValue({ status: 'ok', version: 'v999.0.0', app: 'x' })
    const { NewBuildBanner } = await import('@/shared/layout/NewBuildBanner')
    const { unmount } = render(<NewBuildBanner />)
    expect(await screen.findByText(/새 판/)).toHaveTextContent('v999.0.0')
    screen.getByRole('button', { name: /새로 고침/ }).click()
    expect(reload).toHaveBeenCalledTimes(1)
    unmount()

    systemApi.health.mockResolvedValue({ status: 'ok', version: __APP_VERSION__, app: 'x' })
    render(<NewBuildBanner />)
    await waitFor(() => expect(systemApi.health).toHaveBeenCalledTimes(2))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})
