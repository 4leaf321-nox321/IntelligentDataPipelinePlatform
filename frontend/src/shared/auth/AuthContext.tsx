/**
 * 인증 상태.
 *
 * 새로고침하면 메모리의 access 토큰이 사라지므로, 앱이 뜰 때 refresh 쿠키로 한 번
 * 갱신을 시도한다. 성공하면 로그인 상태가 유지되고 실패하면 익명이다 — 사용자
 * 눈에는 "로그인이 유지되는" 것으로 보인다.
 *
 * **HWAX 포털 안이면 저절로 로그인한다**(ADR 0028, Report Archive 와 같은 길) — 쿠키로 못
 * 되살렸고 포털 안에서 열렸으면 포털의 launch 로 토큰을 받아 바꾼다(`portalHandoff`). 그 동안도
 * 「불러오는 중」 이라 로그인 화면이 한 번 번쩍이지 않는다. 브라우저 세션당 한 번이고, 사용자가
 * 로그아웃하면 그 세션에는 다시 들이지 않는다 — 안 그러면 「로그아웃이 안 된다」 가 된다.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import {
  clearPortalOptOut,
  insidePortal,
  markPortalOptOut,
  portalHandoff,
  shouldAutoHandoff,
} from '@/modules/auth/portal'
import type { HandoffResult } from '@/modules/auth/portal'
import { api, refreshSession, session } from '@/shared/api/client'
import type { CurrentUser, LoginResponse } from '@/shared/auth/types'

type Status = 'loading' | 'authenticated' | 'anonymous'

interface AuthContextValue {
  status: Status
  user: CurrentUser | null
  login: (email: string, password: string) => Promise<CurrentUser>
  logout: () => Promise<void>
  /** 비밀번호 변경 등으로 사용자 정보가 바뀐 뒤 다시 읽는다. */
  reload: () => Promise<void>
  /** HWAX 포털 계정으로 — 로그인 화면의 단추(포털 안에서). 막아 둔 자동 로그인도 푼다. */
  loginWithPortal: () => Promise<HandoffResult>
  /** 기동 때의 포털 자동 로그인이 거절된 사유(승인 대기 등) — 로그인 화면이 사람 말로 보인다. */
  portalRefusal: string | null
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading')
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [portalRefusal, setPortalRefusal] = useState<string | null>(null)

  const clear = useCallback(() => {
    session.setToken(null)
    setUser(null)
    setStatus('anonymous')
  }, [])

  // 앱 기동 시 1회 — 쿠키가 살아 있으면 세션을 되살린다.
  //
  // **api.post 가 아니라 refreshSession 이다.** StrictMode 가 이 effect 를 두 번
  // 돌리므로 api.post 면 refresh 가 같은 쿠키로 둘 나가고, 서버가 둘째를 탈취로
  // 봐 세션을 전부 끊는다. refreshSession 은 진행 중인 요청을 같이 기다린다.
  useEffect(() => {
    let cancelled = false
    session.onLost(clear)

    refreshSession<LoginResponse>()
      .then((body) => {
        if (cancelled) return
        session.setToken(body.access_token)
        setUser(body.user)
        setStatus('authenticated')
      })
      .catch(async () => {
        if (cancelled) return
        if (shouldAutoHandoff()) {
          const got = await portalHandoff({ auto: true })
          if (cancelled) return
          if (got.ok) {
            session.setToken(got.body.access_token)
            setUser(got.body.user)
            setStatus('authenticated')
            return
          }
          // 포털 세션이 없거나(포털 로그인 화면의 일) 타일이 없는 것은 조용히 — 계정 사유만 말한다.
          if (!['portal-login', 'no-tile', 'network'].includes(got.reason)) {
            setPortalRefusal(got.reason)
          }
        }
        clear()
      })

    return () => {
      cancelled = true
      session.onLost(null)
    }
  }, [clear])

  const login = useCallback(async (email: string, password: string) => {
    const body = await api.post<LoginResponse>('/auth/login', { email, password })
    session.setToken(body.access_token)
    setUser(body.user)
    setStatus('authenticated')
    return body.user
  }, [])

  const logout = useCallback(async () => {
    // 포털 안에서 **사용자가** 로그아웃하면 다음 화면에서 곧바로 다시 들어오지 않게 한다.
    if (insidePortal()) markPortalOptOut()
    try {
      await api.post('/auth/logout')
    } finally {
      clear()
    }
  }, [clear])

  const loginWithPortal = useCallback(async () => {
    clearPortalOptOut()
    const got = await portalHandoff()
    if (got.ok) {
      session.setToken(got.body.access_token)
      setUser(got.body.user)
      setPortalRefusal(null)
      setStatus('authenticated')
    }
    return got
  }, [])

  const reload = useCallback(async () => {
    const me = await api.get<CurrentUser>('/auth/me')
    setUser(me)
  }, [])

  const value = useMemo(
    () => ({ status, user, login, logout, reload, loginWithPortal, portalRefusal }),
    [status, user, login, logout, reload, loginWithPortal, portalRefusal],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth 는 AuthProvider 안에서만 쓸 수 있습니다')
  return value
}
