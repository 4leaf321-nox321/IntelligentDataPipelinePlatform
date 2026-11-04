/**
 * 켜진 확장 — **서버에게 묻는다.**
 *
 * `<meta name="app-extensions">` 는 페이지를 받은 순간의 사진이다. 그것만 믿으면 관리자가
 * 서버 화면에서 끈 뒤에도 사이드바는 새로 고침 전까지 옛 메뉴를 들고 있고, Vite 개발 서버는
 * `backend/.env` 를 심으므로 아예 안 맞는다 — **실측으로 「껐는데 본보기가 그대로」 가 나왔다.**
 *
 * 그래서 목록은 `/api/server/enabled-extensions` 가 답하고, 메타는 **첫 그림의 씨앗**으로만
 * 쓴다(로그인 전이나 서버를 못 부를 때도 메뉴가 서야 한다).
 */

import type { ReactNode } from 'react'
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { api } from '@/shared/api/client'
import { ENABLED_EXTENSIONS } from '@/shared/branding'

interface Enabled {
  names: readonly string[]
  /** 켜고 끈 뒤 부른다 — 새로 고침 없이 메뉴와 경로가 따라온다. */
  reload: () => void
}

const Ctx = createContext<Enabled | null>(null)

export function ExtensionsProvider({ children }: { children: ReactNode }) {
  const [names, setNames] = useState<readonly string[]>(ENABLED_EXTENSIONS)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    api
      .get<string[]>('/server/enabled-extensions')
      .then((list) => {
        if (!cancelled) setNames(list)
      })
      .catch(() => {
        // **못 받아도 메타로 그린다.** 사이드바가 통째로 비면 나갈 길까지 사라진다.
      })
    return () => {
      cancelled = true
    }
  }, [tick])

  const reload = useCallback(() => setTick((n) => n + 1), [])
  const value = useMemo(() => ({ names, reload }), [names, reload])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

/** 지금 켜진 확장. 공급자 밖(로그인 화면 등)에서는 메타의 값이다. */
export function useEnabledExtensions(): readonly string[] {
  return useContext(Ctx)?.names ?? ENABLED_EXTENSIONS
}

/** 켜고 끈 뒤 목록을 다시 받는다. 공급자 밖에서는 아무 일도 안 한다. */
export function useExtensionsReload(): () => void {
  const value = useContext(Ctx)
  return value?.reload ?? (() => {})
}
