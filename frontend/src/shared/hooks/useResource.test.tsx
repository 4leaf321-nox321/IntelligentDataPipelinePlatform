/**
 * `useResource` 의 `stale` — deps 가 바뀐 뒤 새 응답이 오기 전에는 앞 응답을 든 채라(깜빡이지 않게),
 * 그 사이 저장 · 고르기를 막으려면 「이 데이터가 지금 대상의 것인가」 를 알아야 한다. 새 응답이
 * 오류면 데이터는 여전히 앞 대상의 것이다.
 */

import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { useResource } from '@/shared/hooks/useResource'

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined
  let reject: (error: Error) => void = () => undefined
  const promise = new Promise<T>((ok, fail) => {
    resolve = ok
    reject = fail
  })
  return { promise, resolve, reject }
}

describe('useResource 의 stale', () => {
  it('대상이 바뀌면 새 응답이 올 때까지 앞 데이터를 들고 stale 이다', async () => {
    const answers: Record<string, ReturnType<typeof deferred<string>>> = {
      A: deferred<string>(),
      B: deferred<string>(),
    }
    const { result, rerender } = renderHook(
      ({ target }) => useResource(() => answers[target].promise, [target]),
      { initialProps: { target: 'A' } },
    )
    expect(result.current.stale).toBe(false) // 아직 아무것도 없다
    await act(async () => answers.A.resolve('A 의 값'))
    expect(result.current).toMatchObject({ data: 'A 의 값', stale: false })

    rerender({ target: 'B' })
    expect(result.current).toMatchObject({ data: 'A 의 값', stale: true })
    await act(async () => answers.B.resolve('B 의 값'))
    expect(result.current).toMatchObject({ data: 'B 의 값', stale: false })
  })

  it('새 대상의 응답이 오류면 데이터는 앞 대상의 것이라 계속 stale 이다', async () => {
    const answers: Record<string, ReturnType<typeof deferred<string>>> = {
      A: deferred<string>(),
      B: deferred<string>(),
    }
    const { result, rerender } = renderHook(
      ({ target }) => useResource(() => answers[target].promise, [target]),
      { initialProps: { target: 'A' } },
    )
    await act(async () => answers.A.resolve('A 의 값'))
    rerender({ target: 'B' })
    await act(async () => answers.B.reject(new Error('B 를 못 읽음')))
    await waitFor(() => expect(result.current.error?.message).toBe('B 를 못 읽음'))
    expect(result.current).toMatchObject({ data: 'A 의 값', stale: true })
  })

  it('같은 대상을 다시 읽는 동안은 stale 이 아니다', async () => {
    let round = 0
    const { result } = renderHook(() =>
      useResource(() => Promise.resolve(`값 ${++round}`), ['같은 대상']),
    )
    await waitFor(() => expect(result.current.data).toBe('값 1'))
    act(() => result.current.reload())
    expect(result.current.stale).toBe(false)
    await waitFor(() => expect(result.current.data).toBe('값 2'))
  })
})
