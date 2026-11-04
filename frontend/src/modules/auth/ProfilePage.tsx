/**
 * 내 정보 — 표시 이름과 개인 액세스 토큰.
 *
 * 팝업이 아니라 화면인 이유: 토큰 목록이 붙으면 팝업이 좁다.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'

import { ApiError, api } from '@/shared/api/client'
import type { Pat } from '@/shared/api/types'
import { useAuth } from '@/shared/auth/AuthContext'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { McpSetupGuide } from './McpSetupGuide'
import { PipelineKitGuide } from './PipelineKitGuide'
import { shownDate } from '@/shared/lib/datetime'

export default function ProfilePage() {
  const { user, reload } = useAuth()
  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [busy, setBusy] = useState(false)

  const tokens = useResource(() => api.get<Pat[]>('/auth/tokens'), [])
  // **아는 범위는 서버가 준다.** 화면이 손 목록을 들면 도메인이 범위를 더한 날
  // 사람은 있는 범위를 못 고른다.
  const scopes = useResource(() => api.get<{ scopes: string[] }>('/auth/token-scopes'), [])
  const [tokenName, setTokenName] = useState('')
  /** 며칠 뒤에 스스로 끊기나. **비우면 만료가 없다** — 바깥에 주는 토큰에는 적는 편이 낫다. */
  const [expiresDays, setExpiresDays] = useState('')
  const [granted, setGranted] = useState<string[]>(['read'])
  // 평문은 발급 응답에서 **한 번만** 나온다. 새로고침하면 다시 볼 수 없다.
  const [issued, setIssued] = useState<string | null>(null)

  async function saveName(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.patch('/auth/me', { display_name: displayName })
      await reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  async function createToken(event: FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      const days = Number(expiresDays)
      const body = await api.post<{ token: string }>('/auth/tokens', {
        name: tokenName,
        scopes: granted,
        // 빈 칸은 **만료 없음**이다. 0 을 보내면 서버가 거절하므로 아예 안 보낸다.
        expires_in_days: Number.isFinite(days) && days > 0 ? days : null,
      })
      setIssued(body.token)
      setTokenName('')
      setExpiresDays('')
      setGranted(['read'])
      tokens.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  /** 폐기 — **실패하면 말한다.** 삼키던 때는 눌러도 아무 일이 없어, 사람은 끊었다고 믿고 그
   *  토큰은 살아 있었다(2026-10-08). 자격을 끊는 일이라 그 차이가 곧 구멍이다. */
  async function revoke(id: string) {
    setError(null)
    try {
      await api.delete(`/auth/tokens/${id}`)
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      tokens.reload()
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <PageHeader title="내 정보" description={user?.email} />

      <form onSubmit={saveName} className="space-y-3">
        <div className="space-y-2">
          <Label htmlFor="display-name">표시 이름</Label>
          <Input
            id="display-name"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            className="max-w-sm"
          />
          {/* **아이디는 여기서 못 바꾼다.** 로그인 식별자라 본인이 바꾸면 감사
              기록이 가리키는 대상이 흔들린다 — 그것은 관리자의 일이다. */}
          <p className="text-muted-foreground text-xs">아이디는 관리자만 바꿀 수 있습니다.</p>
        </div>
        <Button type="submit" disabled={busy}>
          {busy ? '저장 중…' : '저장'}
        </Button>
      </form>

      <ErrorNotice error={error ?? scopes.error ?? tokens.error} />

      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold">액세스 토큰</h2>
          <p className="text-muted-foreground mt-1 text-sm">
            스크립트나 연계 프로그램이 이 시스템의 API 를 부를 때 사용하는 자격입니다. 사람 세션과
            달리 만료가 길고, 안 쓰면 삭제합니다.
          </p>
        </div>

        {issued && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
            {/* **여기서 한 번만 보인다.** 다시 볼 수 없다는 것을 말하지 않으면
                사람은 창을 닫고 나서 다시 찾는다. */}
            <p className="font-medium">지금 복사해 두세요. 다시 볼 수 없습니다.</p>
            <p className="mt-1 font-mono text-xs break-all">{issued}</p>
          </div>
        )}

        {/* 받아 든 토큰을 어디에 넣는지 — 발급한 자리에서 바로. */}
        <McpSetupGuide token={issued} />
        {/* 한꺼번에(수만 줄) 넣는 길 — 사용자 PC 에 푸는 키트. 서버가 같은 판을 내려준다. */}
        <PipelineKitGuide token={issued} onIssued={tokens.reload} />

        <form onSubmit={createToken} className="space-y-3 rounded-md border p-4">
          <div className="flex gap-2">
            <Input
              value={tokenName}
              onChange={(event) => setTokenName(event.target.value)}
              placeholder="토큰 용도 (예: 정제 스크립트)"
              className="max-w-sm"
              required
            />
            {/* **만료를 적을 자리가 없으면 아무도 안 적는다.** 바깥 시스템에 주는 토큰은
                특히 — 연동이 끝난 뒤에도 살아 있는 자격이 가장 오래 남는 구멍이다. */}
            <Input
              type="number"
              min={1}
              max={3650}
              value={expiresDays}
              onChange={(event) => setExpiresDays(event.target.value)}
              placeholder="만료 (일)"
              className="w-32"
              aria-label="만료까지 일수"
            />
            <Button type="submit">발급</Button>
          </div>
          {/* **못 받았으면 못 받았다고 말한다.** 예전에는 실패해도 `read` 하나만 그려서,
              화면은 「이 설치에는 읽기 범위뿐」 처럼 보였다 — 사람은 고칠 자리를 못 찾는다. */}
          {scopes.error && (
            <p className="text-destructive text-sm">
              범위 목록을 받지 못했습니다 — 지금 발급하면 읽기(read)만 들어갑니다. 새로 고쳐
              보고, 그래도 같으면 서버 상태를 확인하세요.
            </p>
          )}
          <div className="flex flex-wrap gap-3">
            {(scopes.data?.scopes ?? []).map((one) => (
              <label key={one} className="text-muted-foreground flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  checked={granted.includes(one)}
                  onChange={(event) =>
                    setGranted((current) =>
                      event.target.checked
                        ? [...current, one]
                        : current.filter((value) => value !== one),
                    )
                  }
                />
                <span className="font-mono text-xs">{one}</span>
              </label>
            ))}
          </div>
          {/* **기본이 읽기뿐인 이유를 적는다.** 안 적으면 사람은 전부 켜 놓고
              「나중에 좁히자」 고 하는데, 나중은 오지 않는다. */}
          <p className="text-muted-foreground text-xs">
            선택하지 않으면 읽기만 됩니다. 필요한 것만 켜세요 — 계정 관리와 서버 설정은 어느
            범위로도 열리지 않습니다. <b>만료를 비우면 만료가 없습니다</b> — 바깥 시스템에 주는
            토큰에는 일수를 적어 두세요.
          </p>
        </form>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead>앞자리</TableHead>
              <TableHead>범위</TableHead>
              <TableHead>발급</TableHead>
              <TableHead>만료</TableHead>
              <TableHead>마지막 사용</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {(tokens.data ?? []).map((one) => (
              <TableRow key={one.id} className={one.revoked_at ? 'opacity-50' : undefined}>
                <TableCell>{one.name}</TableCell>
                <TableCell className="font-mono text-xs">{one.prefix}</TableCell>
                {/* **목록에서 범위가 보인다** — 폐기할지 판단하는 근거가 이름과
                    이것뿐이다. */}
                <TableCell className="font-mono text-xs">{one.scopes.join(', ')}</TableCell>
                <TableCell>{shownDate(one.created_at)}</TableCell>
                {/* **만료가 목록에 없으면 아무도 안 본다.** 지난 토큰은 그 자리에서 보이게. */}
                <TableCell>
                  {one.expires_at ? (
                    new Date(one.expires_at) < new Date() ? (
                      <span className="text-destructive">{shownDate(one.expires_at)} 지남</span>
                    ) : (
                      shownDate(one.expires_at)
                    )
                  ) : (
                    <span className="text-muted-foreground">없음</span>
                  )}
                </TableCell>
                <TableCell>{shownDate(one.last_used_at)}</TableCell>
                <TableCell className="text-right">
                  {!one.revoked_at && (
                    <Button variant="ghost" size="sm" onClick={() => void revoke(one.id)}>
                      폐기
                    </Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
    </div>
  )
}
