/**
 * 그 사람의 **액세스 토큰** — 이름 · 범위 · 만료 · 최종 사용, 그리고 폐기.
 *
 * 토큰 목록이 본인의 「내 정보」 에만 있으면, 관리자는 퇴사자 · 정지된 계정의 연동이 지금
 * 살아 있는지조차 볼 수 없다. 폐기된 것도 보인다 — 「언제 끊겼나」 도 이 목록이 답할 자리다.
 * 평문은 없다(발급할 때 한 번만 나온다).
 */

import { useState } from 'react'

import { accountApi } from '@/modules/accounts/api'
import type { Account, Pat } from '@/modules/accounts/api'
import { TokenRevokeDialog } from '@/modules/accounts/TokenRevokeDialog'
import type { RevokeTarget } from '@/modules/accounts/TokenRevokeDialog'
import { EmptyState } from '@/shared/components/EmptyState'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { useResource } from '@/shared/hooks/useResource'
import { shownDate, shownDateTime } from '@/shared/lib/datetime'

interface Props {
  account: Account
  onClose: () => void
}

/** 토큰의 지금 상태 — 폐기가 만료보다 먼저다(폐기한 뒤 만료일이 지나도 「폐기」). */
export function tokenState(token: Pat, now = new Date()): 'active' | 'expired' | 'revoked' {
  if (token.revoked_at) return 'revoked'
  if (token.expires_at && new Date(token.expires_at) <= now) return 'expired'
  return 'active'
}

export function AccountTokensDialog({ account, onClose }: Props) {
  const tokens = useResource(() => accountApi.tokens(account.id), [account.id])
  const [revoking, setRevoking] = useState<RevokeTarget | null>(null)
  const rows = tokens.data ?? []
  const who = `${account.display_name}(${account.email})`

  return (
    <>
      <Dialog open onOpenChange={(open) => !open && onClose()}>
        <DialogContent className="sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{who} 의 액세스 토큰</DialogTitle>
            <DialogDescription>
              스크립트 · 연동 · AI 도구가 이 계정으로 접속할 때 사용하는 자격입니다. 폐기하면 해당
              토큰만 사용할 수 없게 되고 계정과 다른 토큰은 그대로입니다. 계정을 정지해도 토큰은
              유지되며, 다시 활성화하면 그대로 사용됩니다.
            </DialogDescription>
          </DialogHeader>
          <ErrorNotice error={tokens.error} />
          {tokens.data && rows.length === 0 ? (
            <EmptyState
              title="발급한 토큰이 없습니다"
              hint="토큰은 본인이 내 정보 › 액세스 토큰에서 발급합니다."
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>이름</TableHead>
                  <TableHead>범위</TableHead>
                  <TableHead>만료</TableHead>
                  <TableHead>최종 사용</TableHead>
                  <TableHead>상태</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((one) => {
                  const state = tokenState(one)
                  return (
                    <TableRow key={one.id} className={state === 'revoked' ? 'opacity-60' : ''}>
                      <TableCell>
                        {one.name}
                        <span className="text-muted-foreground ml-2 font-mono text-xs">
                          {one.prefix}
                        </span>
                      </TableCell>
                      <TableCell className="font-mono text-xs">{one.scopes.join(', ')}</TableCell>
                      <TableCell className="text-sm">
                        {one.expires_at ? (
                          shownDate(one.expires_at)
                        ) : (
                          <span className="text-muted-foreground">없음</span>
                        )}
                      </TableCell>
                      <TableCell className="text-sm">
                        {one.last_used_at ? (
                          shownDateTime(one.last_used_at)
                        ) : (
                          <span className="text-muted-foreground">미사용</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <StatusBadge kind="token" value={state} />
                        {one.revoked_at && (
                          <span className="text-muted-foreground ml-1 text-xs">
                            {shownDate(one.revoked_at)}
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        {/* 만료된 것도 폐기할 수 있다 — 만료일을 고쳐 되살리는 길은 없지만,
                            「끊었다」 는 사실과 사유를 기록에 남기는 것은 다른 일이다. */}
                        {!one.revoked_at && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() =>
                              setRevoking({
                                accountId: account.id,
                                tokenId: one.id,
                                name: one.name,
                                owner: who,
                                lastUsedAt: one.last_used_at,
                              })
                            }
                          >
                            폐기
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={onClose}>
              닫기
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <TokenRevokeDialog
        target={revoking}
        onClose={() => setRevoking(null)}
        onRevoked={() => tokens.reload()}
      />
    </>
  )
}
