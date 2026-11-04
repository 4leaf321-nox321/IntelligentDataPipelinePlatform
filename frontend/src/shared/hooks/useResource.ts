/**
 * 목록·상세를 불러오는 공통 패턴.
 *
 * 화면마다 loading/error/reload 상태를 다시 쓰면 그때마다 조금씩 달라지고,
 * "왜 이 화면만 오류가 안 보이지" 같은 차이가 생긴다. 한 곳에 둔다.
 */

import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '@/shared/api/client'

export interface Resource<T> {
  data: T | null
  error: ApiError | Error | null
  loading: boolean
  reload: () => void
  /**
   * `data` 가 **지금의 deps 로 불러온 것이 아니다** — deps 가 바뀐 뒤 새 응답이 오기 전에는(새
   * 응답이 오류면 그 뒤로도) 앞 응답을 든 채다(목록이 깜빡이지 않게). 그 사이 화면은 앞 대상의
   * 것을 보이고 있으므로, 그것을 바탕으로 **저장 · 고르기 · 옮기기**를 하면 엉뚱한 대상에
   * 나간다(부서를 바꾼 직후 앞 부서의 값이 새 부서에 저장되는 식). 그런 동작은 이것이 참이면
   * 막는다. 다시 읽기(`reload`)는 같은 대상이라 해당하지 않는다.
   */
  stale: boolean
}

function sameDeps(a: unknown[], b: unknown[]): boolean {
  return a.length === b.length && a.every((value, index) => Object.is(value, b[index]))
}

export function useResource<T>(loader: () => Promise<T>, deps: unknown[] = []): Resource<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  /** 지금 든 `data` 를 불러온 deps — 성공한 응답만 적는다(오류는 `data` 를 안 바꾼다). */
  const [loadedFor, setLoadedFor] = useState<unknown[] | null>(null)

  // deps 는 호출부가 주는 값들이라 훅이 그 내용을 알 수 없다.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(loader, deps)

  useEffect(() => {
    let cancelled = false
    // 이 부름이 어느 deps 의 것인가 — 응답이 오면 그것을 함께 적는다.
    const asked = deps
    setLoading(true)
    run()
      .then((value) => {
        if (!cancelled) {
          setData(value)
          setError(null)
          setLoadedFor(asked)
        }
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // `deps` 는 `run` 이 이미 따라간다 — 이 효과는 `run` 이 바뀔 때만 돈다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, tick])

  const reload = useCallback(() => setTick((value) => value + 1), [])
  const stale = loadedFor !== null && !sameDeps(loadedFor, deps)

  return { data, error, loading, reload, stale }
}
