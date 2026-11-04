/**
 * 계정 관리.
 *
 * **승인 대기가 맨 위에 온다.** 관리자가 할 일이 목록 맨 위에 있어야 한다 —
 * 이름순으로 두면 대기 하나를 찾으려고 세 쪽을 넘겨야 하고, 그러면 며칠씩 방치된다.
 */

import { useState } from 'react'
import { Plus } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import { accountApi } from '@/modules/accounts/api'
import type { Account } from '@/modules/accounts/api'
import { AccountTokensDialog } from '@/modules/accounts/AccountTokensDialog'
import { AccountWorkspacesDialog } from '@/modules/accounts/AccountWorkspacesDialog'
import { workspaceApi } from '@/modules/workspaces/api'
import { ApiError } from '@/shared/api/client'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { PageHeader } from '@/shared/components/PageHeader'
import { Pagination } from '@/shared/components/Pagination'
import { StatusBadge } from '@/shared/components/StatusBadge'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { SearchablePicker } from '@/shared/components/SearchablePicker'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { shownDate } from '@/shared/lib/datetime'

/** 서버 상한(`accounts/routes.py` 의 le=100) 안에서 고른 한 쪽 크기. */
const PER_PAGE = 50

/** 상태 거르기 — 알림의 링크(`?status=pending`)가 이 값을 싣고 온다. */
const STATUS_FILTERS: { value: string | null; label: string }[] = [
  { value: null, label: '전체' },
  { value: 'pending', label: '승인 대기' },
  { value: 'active', label: '정상' },
  { value: 'suspended', label: '정지' },
]

export default function AccountsAdminPage() {
  // **상태는 주소가 든다.** 가입 신청 알림이 `/admin/accounts?status=pending` 으로 보내는데
  // 화면이 그것을 안 읽으면 전체 목록이 열리고, 대기는 그 안 어딘가에 묻힌다(2026-10-08).
  const [params, setParams] = useSearchParams()
  const raw = params.get('status')
  const status = STATUS_FILTERS.some((one) => one.value === raw) ? raw : null
  // 쪽 위치는 **그 거르기의 것**이다 — 알림 링크로 거르기가 바뀌면 3쪽에 남지 않고 첫 쪽으로.
  const [paging, setPaging] = useState<{ status: string | null; offset: number }>({
    status,
    offset: 0,
  })
  const offset = paging.status === status ? paging.offset : 0
  const list = useResource(
    () => accountApi.list({ status, limit: PER_PAGE, offset }),
    [status, offset],
  )
  const rows = list.data?.items ?? []

  function chooseStatus(value: string | null) {
    const next = new URLSearchParams(params)
    if (value) next.set('status', value)
    else next.delete('status')
    setParams(next, { replace: true })
  }
  const summary = useResource(() => accountApi.summary(), [])
  const workspaces = useResource(() => workspaceApi.options(), [])
  const [error, setError] = useState<ApiError | Error | null>(null)
  // 임시 비밀번호는 **한 번만** 나온다. 화면이 붙들고 있어야 관리자가 옮겨 적는다.
  const [issued, setIssued] = useState<{ email: string; password: string } | null>(null)
  const [rejecting, setRejecting] = useState<{ id: string; email: string } | null>(null)
  const [editing, setEditing] = useState<Account | null>(null)
  // 그 사람의 액세스 토큰 — 계정은 두고 연동 하나만 끊는 자리(정지는 토큰을 그대로 둔다).
  const [tokensOf, setTokensOf] = useState<Account | null>(null)
  // **시스템 관리자 지정은 한 번 누르면 끝나는 일이 아니다.** 그 계정은 전 부서의 자료와
  // 계정 · 서버 설정을 다루게 된다 — 잘못 누른 것을 알아채는 자리가 어디에도 없다.
  const [promoting, setPromoting] = useState<Account | null>(null)
  const [note, setNote] = useState('')
  // **희망 부서가 없는 신청**(HWAX 포털로 들어온 신청 · 신청한 부서가 없어진 것) — 그대로
  // 승인하면 서버가 「부서를 골라 주세요」 로 거절하는데 고를 자리가 없었다. 승인할 때 묻는다.
  const [approving, setApproving] = useState<Account | null>(null)
  const [approveTo, setApproveTo] = useState('')

  const [newEmail, setNewEmail] = useState('')
  const [newName, setNewName] = useState('')
  const [newWorkspace, setNewWorkspace] = useState('')

  async function act(run: () => Promise<unknown>) {
    setError(null)
    try {
      await run()
      list.reload()
      summary.reload()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    }
  }

  const onlyOneAdmin = (summary.data?.active_system_admins ?? 0) <= 1

  return (
    <div className="space-y-6">
      <PageHeader
        title="계정"
        description="가입 승인과 권한을 다룹니다. 부서 멤버 관리는 부서 화면에서 하고, 승인 대기 계정은 승인한 뒤에 관리자 지정 · 소속 변경을 할 수 있습니다."
      />

      {/* **관리자가 하나뿐이면 말한다.** 그 사람이 잠기는 순간 복구 경로가 서버
          콘솔뿐이고, 그때는 화면에서 할 수 있는 것이 하나도 없다. */}
      {summary.data && onlyOneAdmin && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          활성 시스템 관리자가 1명입니다. 그 계정이 잠기면 서버 콘솔로만 복구할 수 있습니다 — 한 명
          더 지정해 두세요.
        </div>
      )}

      {issued && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
          <p className="font-medium">
            {issued.email} 의 임시 비밀번호입니다. 지금 전달하세요 — 다시 볼 수 없습니다.
          </p>
          <p className="mt-1 font-mono text-xs">{issued.password}</p>
        </div>
      )}

      <form
        className="flex flex-wrap items-end gap-2 rounded-md border p-4"
        onSubmit={(event) => {
          event.preventDefault()
          act(async () => {
            const body = await accountApi.create({
              email: newEmail,
              display_name: newName,
              workspace_slug: newWorkspace,
            })
            setIssued({ email: newEmail, password: body.temporary_password })
            setNewEmail('')
            setNewName('')
          })
        }}
      >
        <div className="space-y-2">
          <Label htmlFor="new-email">아이디</Label>
          <Input
            id="new-email"
            value={newEmail}
            onChange={(event) => setNewEmail(event.target.value)}
            placeholder="hong"
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="new-name">이름</Label>
          <Input
            id="new-name"
            value={newName}
            onChange={(event) => setNewName(event.target.value)}
            placeholder="홍길동"
            required
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="new-workspace">부서</Label>
          {/* **Select 가 아니다.** 조직도는 금방 스물을 넘고, 통째로 펼쳐 놓고
              눈으로 찾으라고 하면 못 찾은 사람은 없다고 결론 내린다.
              경로를 함께 보여 준다 — 같은 이름의 팀이 본부마다 있을 수 있다. */}
          <SearchablePicker
            id="new-workspace"
            className="w-64"
            value={newWorkspace || null}
            onChange={setNewWorkspace}
            placeholder="부서를 선택하세요"
            searchPlaceholder="부서 이름이나 주소로 검색"
            emptyText="그런 부서가 없습니다"
            options={(workspaces.data ?? []).map((one) => ({
              value: one.slug,
              label: one.name,
              hint: one.path,
              keywords: one.slug,
            }))}
          />
        </div>
        <Button type="submit" disabled={!newWorkspace}>
          <Plus className="size-4" />
          계정 생성
        </Button>
      </form>

      <ErrorNotice error={error ?? list.error} />

      <div className="flex flex-wrap gap-1" role="group" aria-label="상태">
        {STATUS_FILTERS.map((one) => (
          <Button
            key={one.label}
            size="sm"
            variant={status === one.value ? 'default' : 'outline'}
            aria-pressed={status === one.value}
            onClick={() => chooseStatus(one.value)}
          >
            {one.label}
          </Button>
        ))}
      </div>

      {list.data && rows.length === 0 ? (
        status ? (
          <EmptyState
            title={`${STATUS_FILTERS.find((one) => one.value === status)?.label} 계정이 없습니다`}
            hint="상태 거르기를 「전체」 로 바꾸면 다른 계정이 보입니다."
          />
        ) : (
          <EmptyState
            title="계정이 없습니다"
            hint="설치 스크립트로 만든 관리자 계정만 있는 상태일 수 있습니다."
          />
        )
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>아이디</TableHead>
              <TableHead>이름</TableHead>
              <TableHead>상태</TableHead>
              <TableHead>소속</TableHead>
              <TableHead>신청/승인</TableHead>
              <TableHead className="text-right">처리</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((one) => (
              <TableRow key={one.id}>
                <TableCell className="font-mono text-xs">{one.email}</TableCell>
                <TableCell>
                  {one.display_name}
                  {one.is_system_admin && (
                    <span className="text-muted-foreground ml-2 text-xs">시스템 관리자</span>
                  )}
                </TableCell>
                <TableCell>
                  <StatusBadge kind="account" value={one.status} />
                </TableCell>
                {/* **이름으로 적는다.** slug(hq)는 주소지 부서 이름이 아니고, 사람은
                    자기 부서를 「본사」 로 안다. 대표 소속을 앞에 두고 별표를 붙인다. */}
                <TableCell className="text-sm">
                  {one.workspaces.length > 0
                    ? [...one.workspaces]
                        .sort((a, b) => Number(b.is_home) - Number(a.is_home))
                        .map((room) => (room.is_home ? `★ ${room.name}` : room.name))
                        .join(', ')
                    : (one.requested_workspace_name ??
                      (one.status === 'pending' ? '희망 부서 없음 — 승인할 때 선택' : '—'))}
                </TableCell>
                <TableCell className="text-sm">
                  {shownDate(one.decided_at ?? one.created_at)}
                </TableCell>
                <TableCell className="space-x-1 text-right">
                  {one.status === 'pending' ? (
                    <>
                      <Button
                        size="sm"
                        onClick={() => {
                          if (one.requested_workspace_slug) {
                            void act(() => accountApi.approve(one.id, {}))
                            return
                          }
                          setApproveTo('')
                          setApproving(one)
                        }}
                      >
                        승인
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          setNote('')
                          setRejecting({ id: one.id, email: one.email })
                        }}
                      >
                        거절
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          act(async () => {
                            const body = await accountApi.resetPassword(one.id)
                            setIssued({ email: one.email, password: body.temporary_password })
                          })
                        }
                      >
                        비밀번호 초기화
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => setEditing(one)}>
                        소속 변경
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => setTokensOf(one)}>
                        토큰
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setPromoting(one)}
                      >
                        {one.is_system_admin ? '관리자 해제' : '관리자 지정'}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          act(() =>
                            one.status === 'active'
                              ? accountApi.suspend(one.id)
                              : accountApi.activate(one.id),
                          )
                        }
                      >
                        {one.status === 'active' ? '정지' : '활성화'}
                      </Button>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      {/* **없으면 50명이 넘는 순간 나머지를 볼 방법이 없다** — 그리고 목록은 잘렸다는
          말을 하지 않으므로 관리자는 그것이 전부라고 읽는다. */}
      {list.data && (
        <Pagination
          total={list.data.total}
          limit={list.data.limit}
          offset={list.data.offset}
          unit="명"
          onChange={(next) => setPaging({ status, offset: next })}
        />
      )}

      {tokensOf && <AccountTokensDialog account={tokensOf} onClose={() => setTokensOf(null)} />}

      {editing && (
        <AccountWorkspacesDialog
          account={editing}
          options={workspaces.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={() => {
            list.reload()
            summary.reload()
          }}
        />
      )}

      {/* **사유를 반드시 받는다.** 메일이 없어 통보가 앱 안에서만 되므로, 안 적으면
          신청한 사람은 이유를 영영 모른다. window.prompt 를 쓰지 않는 이유는
          그것이 오류를 보여 줄 자리가 없기 때문이다 — 서버가 거절하면 그 말이
          어디에도 안 뜬다. */}
      <ConfirmDialog
        open={promoting !== null}
        title={
          promoting?.is_system_admin
            ? `${promoting?.email} 의 시스템 관리자를 해제합니다`
            : `${promoting?.email} 를 시스템 관리자로 지정합니다`
        }
        description={
          promoting?.is_system_admin ? (
            <>
              이 계정은 더 이상 계정 · 서버 설정 · 확장 모듈을 다루지 못하고, 부서 밖 자료를
              고치지 못합니다. <b>마지막 관리자는 해제되지 않습니다</b> — 서버가 막습니다.
            </>
          ) : (
            <>
              이 계정은 <b>모든 부서의 자료</b>를 보고 고칠 수 있게 됩니다. 계정을 만들고
              지우고, 서버 설정과 확장 모듈을 켜고 끄며, 감사 기록을 읽습니다. 되돌리려면 같은
              자리에서 해제합니다.
            </>
          )
        }
        confirmLabel={promoting?.is_system_admin ? '관리자 해제' : '관리자 지정'}
        destructive={Boolean(promoting?.is_system_admin)}
        onConfirm={async () => {
          if (!promoting) return
          await act(() => accountApi.setSystemAdmin(promoting.id, !promoting.is_system_admin))
          setPromoting(null)
        }}
        onClose={() => setPromoting(null)}
      />

      <ConfirmDialog
        open={approving !== null}
        title="가입 승인 — 부서 선택"
        description={
          <div className="space-y-2">
            <p>
              {approving?.display_name}({approving?.email}) 의 신청에는 희망 부서가 없습니다 — HWAX
              포털로 들어온 신청이거나, 신청한 부서가 없어졌습니다. 소속할 부서를 선택하세요. 이
              부서가 그 사람의 대표 소속이 됩니다.
            </p>
            <SearchablePicker
              id="approve-workspace"
              className="w-full"
              value={approveTo || null}
              onChange={setApproveTo}
              placeholder="부서를 선택하세요"
              searchPlaceholder="부서 이름이나 주소로 검색"
              emptyText="그런 부서가 없습니다"
              options={(workspaces.data ?? []).map((one) => ({
                value: one.slug,
                label: one.name,
                hint: one.path,
                keywords: one.slug,
              }))}
            />
          </div>
        }
        confirmLabel="승인"
        confirmDisabled={!approveTo}
        onConfirm={async () => {
          if (!approving) return
          await accountApi.approve(approving.id, { workspace_slug: approveTo })
          list.reload()
          summary.reload()
        }}
        onClose={() => setApproving(null)}
      />

      <ConfirmDialog
        open={rejecting !== null}
        title="가입 거절"
        description={
          <div className="space-y-2">
            <p>{rejecting?.email} 의 신청을 거절합니다.</p>
            <p className="text-muted-foreground">
              메일 통보가 없으므로 여기 적은 사유가 신청자에게 보이는 전부입니다.
            </p>
            <Input
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="거절 사유"
              autoFocus
            />
          </div>
        }
        confirmLabel="거절"
        destructive
        onConfirm={async () => {
          if (!rejecting) return
          await accountApi.reject(rejecting.id, note)
          list.reload()
          summary.reload()
        }}
        onClose={() => setRejecting(null)}
      />
    </div>
  )
}
