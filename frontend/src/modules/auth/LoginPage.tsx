/**
 * 로그인.
 *
 * **원래 가려던 곳으로 되돌려 보낸다.** 링크로 받은 주소가 로그인 후 홈으로
 * 흘러가 버리면, 사람은 그 링크를 다시 찾아야 한다.
 *
 * **HWAX 포털로 로그인**(ADR 0028) — 설치가 포털에 붙어 있으면 단추가 선다. 포털이 그 사람으로
 * 토큰을 만들어 서버에 보내고, 서버가 쿠키를 심어 첫 화면으로 보낸다. 안 되면 서버가 이 화면에
 * `?sso=<사유>` 를 달아 돌려보낸다 — 그 사유를 여기서 사람 말로 보인다.
 *
 * **포털 안에서 열렸으면**(포털 쿠키가 보이면) 단추가 페이지를 옮기지 않고 그 자리에서 포털
 * 로그인을 한다(Report Archive 와 같은 길 — `portalHandoff`). 화면이 뜰 때 한 번은 저절로 하고
 * (`AuthContext`), 이 단추는 로그아웃한 뒤 · 거절된 뒤에 사람이 다시 누르는 자리다.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'

import { insidePortal, portalLaunchUrl, ssoMessage } from '@/modules/auth/portal'
import { ApiError } from '@/shared/api/client'
import { useAuth } from '@/shared/auth/AuthContext'
import { APP_NAME, APP_TAGLINE } from '@/shared/branding'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'

export default function LoginPage() {
  const { status, login, loginWithPortal, portalRefusal } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const [portalReason, setPortalReason] = useState<string | null>(null)
  const sso = params.get('sso') ?? portalReason ?? portalRefusal
  const notice = sso ? ssoMessage(sso) : null
  const launch = portalLaunchUrl()
  const inside = insidePortal()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [busy, setBusy] = useState(false)

  const wanted = (location.state as { from?: { pathname: string } } | null)?.from?.pathname
  // 로그인 · 비밀번호 변경 화면은 「돌아갈 곳」 이 아니다 — 거기서 로그아웃돼 왔을 때 그리로
  // 되돌리면 방금 한 일을 또 시킨다.
  const from = wanted && !['/login', '/force-password-change'].includes(wanted) ? wanted : undefined

  if (status === 'authenticated') return <Navigate to={from ?? '/'} replace />

  async function portalLogin() {
    setBusy(true)
    setError(null)
    setPortalReason(null)
    try {
      const got = await loginWithPortal()
      if (got.ok) {
        navigate(from ?? '/', { replace: true })
        return
      }
      // 포털 로그인이 끝났으면 포털로 — 거기서 로그인하면 타일이 다시 데려온다.
      if (got.reason === 'portal-login' && launch) {
        window.location.assign(launch)
        return
      }
      setPortalReason(got.reason)
    } finally {
      setBusy(false)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const user = await login(email, password)
      // 임시 비밀번호로 들어왔으면 다른 화면을 못 쓴다. 가드도 막지만, 여기서
      // 바로 보내면 한 번 깜빡이는 것을 없앨 수 있다.
      navigate(user.must_change_password ? '/force-password-change' : (from ?? '/'), {
        replace: true,
      })
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center p-6">
      <form onSubmit={submit} className="w-full max-w-sm space-y-5">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{APP_NAME}</h1>
          {APP_TAGLINE && <p className="text-muted-foreground mt-1 text-sm">{APP_TAGLINE}</p>}
        </div>

        {/* **포털이 돌려보낸 까닭.** 승인 대기는 할 일이 「기다림」 이라 오류 색을 쓰지 않는다. */}
        {notice && (
          <div
            role={notice.tone === 'error' ? 'alert' : 'status'}
            className={
              notice.tone === 'error'
                ? 'border-destructive/40 bg-destructive/5 text-destructive rounded-md border p-3 text-sm'
                : 'rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm'
            }
          >
            {notice.text}
          </div>
        )}

        {/* **주소는 서버가 준다**(`PORTAL_LAUNCH_URL`) — 포털에 붙지 않은 설치에는 단추가 없다.
            포털 화면을 거쳐 돌아오므로 앱에 직접 들어온 사람도 같은 길로 로그인된다. 포털 안에서
            열렸으면 페이지를 옮기지 않고 그 자리에서 한다. */}
        {(launch || inside) && (
          <>
            {inside ? (
              <Button
                type="button"
                variant="outline"
                className="w-full"
                disabled={busy}
                onClick={() => void portalLogin()}
              >
                {busy ? 'HWAX 포털 계정 확인 중…' : 'HWAX 포털로 로그인'}
              </Button>
            ) : (
              <Button asChild variant="outline" className="w-full">
                <a href={launch}>HWAX 포털로 로그인</a>
              </Button>
            )}
            <p className="text-muted-foreground text-center text-xs">
              또는 이 플랫폼의 아이디 · 비밀번호로
            </p>
          </>
        )}

        <div className="space-y-2">
          <Label htmlFor="email">아이디</Label>
          <Input
            id="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="username"
            autoFocus
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="password">비밀번호</Label>
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete="current-password"
            required
          />
        </div>

        {/* **왜 안 되는지 그대로 보여 준다.** 승인 대기와 정지는 사람이 할 일이
            다르고, 그 차이는 서버가 이미 메시지로 말해 준다. */}
        <ErrorNotice error={error} />

        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? '확인 중…' : '로그인'}
        </Button>

        <p className="text-muted-foreground text-center text-sm">
          계정이 없나요?{' '}
          <Link to="/signup" className="hover:text-foreground underline">
            가입 신청
          </Link>
        </p>
      </form>
    </div>
  )
}
