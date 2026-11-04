/**
 * 칸 하나의 **자동 저장** — 치는 동안은 기다렸다가, 멈추거나 칸을 떠나면 저장한다.
 *
 * 「저장」 단추 없이 그 자리에서 고치는 화면(자주검증의 회의 표 …)이 쓴다. 무엇을 부를지(`save`)는
 * 부르는 쪽이 정한다 — 이 훅은 언제 부르고, 그 결과를 어떻게 말할지만 안다.
 *
 * 상태는 다섯이다 — 저장 중 · 저장됨 · 실패 · 충돌 · (아무 일 없음). 「저장」 단추가 없는 화면에서 이
 * 표시가 없으면 사람은 저장됐는지 모르고, 실패는 **창을 닫은 뒤에야** 「안 들어가 있다」 로 만난다.
 *
 * - 저장하는 동안 또 고치면 끝난 뒤 한 번 더 저장한다(앞 저장을 버리지 않고 차례로).
 * - 실패하면 고친 글을 그대로 들고 「다시 시도」 를 낸다 — 글을 되돌리지 않는다(사람이 쓴 것이
 *   사라지는 것이 저장 실패보다 나쁘다).
 *
 * ## 여럿이 함께 고칠 때 — 본 값(`base`)
 *
 * `save(next, base)` 의 `base` 는 **고치기 시작할 때 본 값**(서버에 있다고 아는 값)이다. 서버는 그
 * 칸의 지금 값이 `base` 와 다르면 「그사이 남이 고쳤다」 로 거절하고, 부르는 쪽은 그것을
 * `SaveConflictError`(지금 값 · 누가 · 언제)로 던진다. 그러면 훅은 `conflict` 를 들고 **자동 저장을
 * 멈춘다** — 사람이 「상대 글 사용」(`takeTheirs`) · 「내 글로 덮어쓰기」(`keepMine`) 중 하나를 고를
 * 때까지 내 글은 그대로 있다.
 *
 * 바깥 값(서버에서 다시 읽은 값)이 바뀌면:
 * - 고치는 중도 아니고 칸에 들어가 있지도 않으면(`hold`) 따라간다.
 * - **고치는 중이거나 칸에 들어가 있으면 덮지 않는다** — 치고 있는 글을 남의 저장이 덮지 않게.
 *   본 값도 그대로 둔다(그래야 저장할 때 충돌로 잡힌다). 대신 `stale` 로 「다른 사람이 이 칸을
 *   수정했습니다」 를 말하게 한다. 칸을 떠나며 고친 것이 없으면 그때 따라간다.
 * - 바깥 값이 지금 글과 같으면(내 저장이 돌아왔거나 남도 같은 글을 썼으면) 부딪칠 것이 없다.
 *
 * 화면을 떠나면(언마운트) 남은 것을 저장한다.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export type SaveStatus = 'idle' | 'dirty' | 'saving' | 'saved' | 'failed' | 'conflict'

/** 저장이 「그사이 다른 사람이 먼저 수정했다」 로 거절됐다 — 서버의 지금 값과 누가 · 언제. */
export class SaveConflictError<T = unknown> extends Error {
  readonly current: T
  readonly by: string | null
  readonly at: string | null

  constructor(message: string, current: T, by: string | null, at: string | null) {
    super(message)
    this.name = 'SaveConflictError'
    this.current = current
    this.by = by
    this.at = at
  }
}

export interface SaveConflict<T> {
  current: T
  by: string | null
  at: string | null
  message: string
}

export interface AutoSave<T> {
  /** 지금 칸에 보일 값 — 저장 전의 글까지. */
  draft: T
  /** 고쳤다 — 잠시 뒤 저장한다. */
  change: (next: T) => void
  /** 지금의 글 — 기다렸다 고칠 때(사진을 올린 뒤 그 자리에 표시를 넣을 때) 그때의 글을 읽는다. */
  read: () => T
  /** 기다리지 말고 지금 저장한다(칸을 떠날 때). 저장됐거나 저장할 것이 없으면 true — 실패 ·
   * 충돌이면 false(그 뒤에 할 일을 멈출 때 — 사진의 표시 줄을 빼고 사진을 뗄 때). */
  flush: () => Promise<boolean>
  status: SaveStatus
  error: Error | null
  /** 고치는 동안 다른 사람이 이 칸을 수정했다(바깥 값이 본 값과 다르다) — 저장할 때 묻는다. */
  stale: boolean
  /** 아직 서버에 없는 글이 있다(본 값과 다르다). */
  unsaved: boolean
  /** 저장이 충돌로 거절됐다 — 고를 때까지 자동 저장을 멈춘다. */
  conflict: SaveConflict<T> | null
  /** 「상대 글 사용」 — 내 글을 버리고 서버의 지금 값으로. */
  takeTheirs: () => void
  /** 「내 글로 덮어쓰기」 — 본 값을 서버의 지금 값으로 옮기고 내 글을 다시 저장한다. */
  keepMine: () => Promise<boolean>
}

/** 치기를 멈추고 이만큼 지나면 저장한다(ms). 칸을 떠나면 바로. */
export const AUTOSAVE_DELAY = 1000
/** 「저장됨」 을 이만큼 보이고 지운다(ms). */
const SAVED_SHOWN = 2000

export function useAutoSave<T>(
  source: T,
  save: (next: T, base: T) => Promise<void>,
  options: { delay?: number; equals?: (a: T, b: T) => boolean; hold?: boolean } = {},
): AutoSave<T> {
  const delay = options.delay ?? AUTOSAVE_DELAY
  const hold = options.hold ?? false
  const equalsRef = useRef(options.equals ?? Object.is)
  const [draft, setDraft] = useState<T>(source)
  const [status, setStatus] = useState<SaveStatus>('idle')
  const [error, setError] = useState<Error | null>(null)
  const [stale, setStale] = useState(false)
  const [conflict, setConflict] = useState<SaveConflict<T> | null>(null)
  /** 본 값 — 내 글이 거기서 시작했고, 서버에 있다고 아는 값. */
  const base = useRef<T>(source)
  /** 바깥에서 마지막으로 받은 값. */
  const latest = useRef<T>(source)
  const draftRef = useRef<T>(source)
  const conflictRef = useRef<SaveConflict<T> | null>(null)
  const saveRef = useRef(save)
  saveRef.current = save
  const timer = useRef<number | null>(null)
  const fade = useRef<number | null>(null)
  const running = useRef<Promise<void> | null>(null)

  const same = (a: T, b: T) => equalsRef.current(a, b)

  const follow = (value: T) => {
    base.current = value
    draftRef.current = value
    setDraft(value)
    setStale(false)
  }

  // 바깥 값이 바뀌었다 · 칸을 떠났다 — 고치는 중도 들어가 있지도 않으면 따라간다.
  useEffect(() => {
    latest.current = source
    if (same(base.current, source)) {
      setStale(false)
      return
    }
    if (same(draftRef.current, source)) {
      // 서버가 이미 내 글을 들고 있다 — 부딪칠 것이 없다(열려 있던 충돌도 풀린다).
      base.current = source
      setStale(false)
      if (conflictRef.current) {
        conflictRef.current = null
        setConflict(null)
        setStatus('idle')
      }
      return
    }
    const editing = !same(draftRef.current, base.current) || conflictRef.current !== null
    if (!editing && !hold) follow(source)
    else setStale(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, hold])

  const run = useCallback(async (): Promise<boolean> => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
    if (running.current) await running.current
    // 충돌을 고르기 전에는 저장하지 않는다 — 같은 거절을 되풀이할 뿐이다.
    if (conflictRef.current) {
      setStatus('conflict')
      return false
    }
    const next = draftRef.current
    const seen = base.current
    if (same(next, seen)) {
      setStatus((now) => (now === 'dirty' ? 'idle' : now))
      return true
    }
    setStatus('saving')
    setError(null)
    let ok = false
    const job = saveRef
      .current(next, seen)
      .then(() => {
        base.current = next
        if (same(latest.current, next)) setStale(false)
        ok = true
      })
      .catch((caught: unknown) => {
        if (caught instanceof SaveConflictError) {
          const found: SaveConflict<T> = {
            current: caught.current as T,
            by: caught.by,
            at: caught.at,
            message: caught.message,
          }
          conflictRef.current = found
          setConflict(found)
          setStatus('conflict')
          return
        }
        setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
        setStatus('failed')
      })
      .finally(() => {
        running.current = null
      })
    running.current = job
    await job
    if (!ok) return false
    if (!same(draftRef.current, base.current)) {
      // 저장하는 동안 또 고쳤다 — 이어서 한 번 더.
      setStatus('dirty')
      timer.current = window.setTimeout(() => void run(), delay)
      return true
    }
    setStatus('saved')
    if (fade.current !== null) window.clearTimeout(fade.current)
    fade.current = window.setTimeout(
      () => setStatus((now) => (now === 'saved' ? 'idle' : now)),
      SAVED_SHOWN,
    )
    return true
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [delay])

  const change = useCallback(
    (next: T) => {
      draftRef.current = next
      setDraft(next)
      if (conflictRef.current) return // 고를 때까지 기다린다 — 글은 그대로 받는다
      setStatus('dirty')
      if (timer.current !== null) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => void run(), delay)
    },
    [run, delay],
  )

  const takeTheirs = useCallback(() => {
    const found = conflictRef.current
    if (!found) return
    conflictRef.current = null
    setConflict(null)
    follow(found.current)
    setStale(!same(latest.current, found.current))
    setStatus('idle')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const keepMine = useCallback(async () => {
    const found = conflictRef.current
    if (!found) return true
    conflictRef.current = null
    setConflict(null)
    base.current = found.current
    setStale(false)
    return run()
  }, [run])

  // 떠날 때 남은 것을 저장한다 — 기다렸다 저장하는 사이에 화면을 옮기면 친 글이 사라진다.
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
      if (fade.current !== null) window.clearTimeout(fade.current)
      if (!same(draftRef.current, base.current)) {
        // 떠난 뒤라 실패를 보일 자리가 없다 — 삼키되 조용히 던지지는 않는다(처리 안 된 거절).
        saveRef.current(draftRef.current, base.current).catch(() => undefined)
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  return {
    draft,
    change,
    read: () => draftRef.current,
    flush: run,
    status,
    error,
    stale,
    unsaved: !same(draft, base.current),
    conflict,
    takeTheirs,
    keepMine,
  }
}
