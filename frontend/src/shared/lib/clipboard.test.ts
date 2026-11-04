/**
 * 복사는 **HTTP 에서도 되어야 한다.**
 *
 * `navigator.clipboard` 는 보안 컨텍스트(HTTPS · localhost)에서만 있다. 사내에서는 앱을
 * `http://<서버>:8041` 로 여는 일이 흔한데, 그 화면에서 복사 단추가 조용히 아무 일도 안
 * 하면 사람은 「이 기능은 없다」 로 결론 내린다. 그래서 옛 방식(`execCommand`)으로 떨어진다.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { copyText } from '@/shared/lib/clipboard'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('copyText', () => {
  it('보안 컨텍스트에서는 클립보드 API 를 쓴다', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    await copyText('가\t나')
    expect(writeText).toHaveBeenCalledWith('가\t나')
  })

  it('HTTP 처럼 클립보드 API 가 없으면 옛 방식으로 복사한다', async () => {
    vi.stubGlobal('navigator', {})
    const exec = vi.fn().mockReturnValue(true)
    // jsdom 에는 execCommand 가 없다 — 있는 자리에서 어떻게 불리는지만 본다.
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true })
    await copyText('한 줄')
    expect(exec).toHaveBeenCalledWith('copy')
    // 임시 칸은 남기지 않는다 — 남으면 화면에 빈 자리가 생긴다.
    expect(document.querySelectorAll('textarea')).toHaveLength(0)
  })

  it('클립보드 API 가 막히면(권한 거부) 옛 방식으로 떨어진다', async () => {
    vi.stubGlobal('navigator', {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error('막힘')) },
    })
    const exec = vi.fn().mockReturnValue(true)
    Object.defineProperty(document, 'execCommand', { value: exec, configurable: true })
    await copyText('두 줄')
    expect(exec).toHaveBeenCalledWith('copy')
  })
})
