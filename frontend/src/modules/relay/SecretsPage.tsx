/**
 * 비밀 — 원천 · 대상의 토큰 · 비밀번호를 **사람이 넣는 자리**.
 *
 * 파이프라인 정의(AI 가 MCP 로 짓는 것 포함)에는 비밀이 든 환경 변수의 **이름**만 있다. AI 는
 * 「무엇이 비었나」 를 보고(`secrets_status`) 사람에게 여기서 넣어 달라고 한다 — 값은 대화로
 * 받지 않는다. 넣은 값은 다시 보이지 않는다(서버가 값을 내주지 않는다).
 *
 * `?name=` 으로 오면 그 비밀의 입력 창을 바로 연다 — AI 가 건네는 링크가 그 자리를 가리킨다.
 */

import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { relayApi } from '@/modules/relay/api'
import type { SecretOut } from '@/modules/relay/api'
import { ApiError } from '@/shared/api/client'
import { useAuth } from '@/shared/auth/AuthContext'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
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
import { shownDateTime } from '@/shared/lib/datetime'

/** 「요청됨(정의 전)」 — 아직 정의가 없어 상세로 갈 곳이 없다. */
const BEFORE_DEFINITION = '요청됨(정의 전)'

function SecretDialog({
  secret,
  onClose,
  onSaved,
}: {
  secret: SecretOut | null
  onClose: () => void
  onSaved: () => void
}) {
  const [value, setValue] = useState('')
  const [error, setError] = useState<ApiError | Error | null>(null)
  const [busy, setBusy] = useState(false)

  function close() {
    setValue('')
    setError(null)
    onClose()
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!secret) return
    setBusy(true)
    setError(null)
    try {
      await relayApi.putSecret(secret.name, value)
      setValue('')
      onSaved()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const replacing = secret?.status === 'screen'
  return (
    <Dialog open={secret !== null} onOpenChange={(open) => !open && !busy && close()}>
      <DialogContent>
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>
              인증 정보 {replacing ? '변경' : '입력'} — <span className="font-mono">{secret?.name}</span>
            </DialogTitle>
            <DialogDescription>
              값은 한 줄로 입력합니다. 저장한 값은 화면과 감사 기록 어디에도 다시 표시되지 않습니다.
              {replacing ? ' 현재 값은 새 값으로 변경됩니다.' : ''} 다음 실행과 테스트부터 이 값을
              사용합니다.
            </DialogDescription>
          </DialogHeader>
          {secret && secret.uses.length > 0 && (
            <ul className="text-muted-foreground space-y-0.5 text-xs">
              {secret.uses.map((use) => (
                <li key={`${use.pipeline}:${use.role}:${use.state}`}>
                  {use.pipeline || '—'} · {use.role} · {use.state}
                </li>
              ))}
            </ul>
          )}
          <div className="space-y-2">
            <Label htmlFor="secret-value">값</Label>
            <Input
              id="secret-value"
              type="password"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              required
            />
          </div>
          <ErrorNotice error={error} />
          <DialogFooter>
            <Button type="button" variant="outline" onClick={close} disabled={busy}>
              취소
            </Button>
            <Button type="submit" disabled={busy || !value}>
              {busy ? '저장 중…' : '저장'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default function SecretsPage() {
  const { user } = useAuth()
  const admin = Boolean(user?.is_system_admin)
  const list = useResource(() => relayApi.secrets(), [])
  const [params, setParams] = useSearchParams()
  const asked = params.get('name')
  const [editing, setEditing] = useState<string | null>(null)
  const [removing, setRemoving] = useState<SecretOut | null>(null)

  // 링크(`?name=`)가 가리킨 비밀은 창을 바로 연다 — 닫으면 주소에서도 뺀다.
  const open = editing ?? (admin ? asked : null)
  const current = useMemo(
    () => list.data?.find((one) => one.name === open && !one.forbidden) ?? null,
    [list.data, open],
  )
  const missing = list.data?.filter((one) => one.status === 'missing') ?? []

  function closeDialog() {
    setEditing(null)
    if (asked) {
      params.delete('name')
      setParams(params, { replace: true })
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="인증 정보"
        description="원천과 대상의 토큰, 비밀번호를 입력하는 화면입니다. 파이프라인 정의에는 인증 정보의 이름만 있으며, 값은 이 화면에서 사람이 입력합니다. 입력한 값은 다시 표시되지 않습니다."
      />
      <ErrorNotice error={list.error} />
      {list.loading && !list.data && <p className="text-muted-foreground text-sm">불러오는 중…</p>}
      {missing.length > 0 && (
        <p className="text-destructive text-sm" data-testid="secrets-missing">
          미입력 인증 정보 {missing.length}개. 이 인증 정보를 사용하는 원천과 대상에 연결할 수 없습니다.
        </p>
      )}
      {list.data && list.data.length === 0 && (
        <EmptyState
          title="사용 중인 인증 정보 없음"
          hint="파이프라인 정의(초안 포함)의 token_env, password_env 등에 지정한 인증 정보 이름이 이 목록에 표시됩니다."
        />
      )}
      {list.data && list.data.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>이름</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>사용처</TableHead>
              <TableHead>마지막 입력</TableHead>
              {admin && <TableHead className="text-right">동작</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.data.map((secret) => (
              <TableRow
                key={secret.name}
                data-state={secret.name === asked ? 'selected' : undefined}
              >
                <TableCell className="align-top">
                  <p className="font-mono text-sm">{secret.name}</p>
                  {secret.note && (
                    <p className="text-muted-foreground mt-0.5 text-xs">{secret.note}</p>
                  )}
                  {secret.forbidden && (
                    <p className="text-destructive mt-0.5 text-xs">
                      이 플랫폼의 설정 이름이므로 화면에서 입력할 수 없습니다.
                    </p>
                  )}
                </TableCell>
                <TableCell className="align-top">
                  <StatusBadge kind="relay_secret" value={secret.status} />
                </TableCell>
                <TableCell className="align-top text-sm">
                  <ul className="space-y-0.5">
                    {secret.uses.map((use) => (
                      <li key={`${use.pipeline}:${use.role}:${use.state}`}>
                        {use.pipeline && use.state !== BEFORE_DEFINITION ? (
                          <Link
                            to={`/relay/pipelines/${encodeURIComponent(use.pipeline)}`}
                            className="underline-offset-2 hover:underline"
                          >
                            {use.pipeline}
                          </Link>
                        ) : (
                          <span>{use.pipeline || '—'}</span>
                        )}
                        <span className="text-muted-foreground">
                          {' '}
                          · {use.role} · {use.state}
                        </span>
                      </li>
                    ))}
                  </ul>
                </TableCell>
                <TableCell className="text-muted-foreground align-top text-xs">
                  {secret.updated_at ? (
                    <>
                      <p>{shownDateTime(secret.updated_at)}</p>
                      {secret.updated_by && <p>{secret.updated_by}</p>}
                    </>
                  ) : (
                    '—'
                  )}
                </TableCell>
                {admin && (
                  <TableCell className="space-x-1 text-right align-top whitespace-nowrap">
                    {!secret.forbidden && (
                      <Button
                        size="sm"
                        variant={secret.status === 'missing' ? 'default' : 'outline'}
                        onClick={() => setEditing(secret.name)}
                      >
                        {secret.status === 'screen' ? '변경' : '입력'}
                      </Button>
                    )}
                    {secret.status === 'screen' && (
                      <Button size="sm" variant="ghost" onClick={() => setRemoving(secret)}>
                        삭제
                      </Button>
                    )}
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      <SecretDialog
        secret={current}
        onClose={closeDialog}
        onSaved={() => {
          closeDialog()
          list.reload()
        }}
      />
      <ConfirmDialog
        open={removing !== null}
        title={`인증 정보 삭제 — ${removing?.name ?? ''}`}
        description={
          <>
            화면에서 입력한 값을 삭제합니다. 서버 설정(.env)에 같은 이름이 있으면 그 값을 사용하고,
            없으면 이 인증 정보를 사용하는 원천과 대상에 연결할 수 없습니다.
            {removing && removing.uses.length > 0
              ? ` 해당 파이프라인: ${removing.uses.map((use) => use.pipeline).filter(Boolean).join(', ')}`
              : ''}
          </>
        }
        confirmLabel="삭제"
        destructive
        onConfirm={async () => {
          if (removing) await relayApi.deleteSecret(removing.name)
          list.reload()
        }}
        onClose={() => setRemoving(null)}
      />
    </div>
  )
}
