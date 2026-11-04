/**
 * 남의 액세스 토큰 **하나**를 폐기한다 — 시스템 관리자.
 *
 * 예전에는 본인만 폐기할 수 있어서, 퇴사자 · 정지된 계정의 연동을 끊으려면 계정을 지워야
 * 했다(지우면 그 사람의 토큰이 전부 끊긴다). 정지는 토큰을 그대로 두므로(다시 켜면 연동이
 * 이어진다) 「계정은 두고 이 연동만 끊는다」 는 이 길뿐이다(2026-10-08).
 *
 * **무엇이 끊기는지 적는다** — 최종 사용 시각이 그 판단의 근거다. 어제 쓰인 토큰과 한 번도 안
 * 쓰인 토큰은 다른 무게다. 사유는 감사 기록과 토큰 주인의 알림에 그대로 실린다 — 비우면
 * 주인은 「왜」 를 관리자에게 따로 물어야 한다.
 *
 * 계정 화면(그 사람의 토큰)과 코어 화면(액세스 토큰 탭)이 같은 창을 쓴다.
 */

import { useState } from 'react'

import { accountApi } from '@/modules/accounts/api'
import { ConfirmDialog } from '@/shared/components/ConfirmDialog'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { shownDateTime } from '@/shared/lib/datetime'

export interface RevokeTarget {
  accountId: string
  tokenId: string
  name: string
  /** 토큰 주인 — 이름이나 아이디. */
  owner: string
  lastUsedAt: string | null
}

interface Props {
  target: RevokeTarget | null
  onClose: () => void
  onRevoked: () => void
}

export function TokenRevokeDialog({ target, onClose, onRevoked }: Props) {
  const [reason, setReason] = useState('')

  function close() {
    setReason('')
    onClose()
  }

  return (
    <ConfirmDialog
      open={target !== null}
      title={`액세스 토큰 「${target?.name ?? ''}」 폐기`}
      description={
        <div className="space-y-2">
          <p>
            <b>{target?.owner}</b> 의 토큰입니다. 이 토큰을 사용하는 연동 · 스크립트 · AI 도구는{' '}
            <b>즉시 인증에 실패합니다</b> —{' '}
            {target?.lastUsedAt
              ? `최종 사용 ${shownDateTime(target.lastUsedAt)}.`
              : '아직 한 번도 사용되지 않은 토큰입니다.'}
          </p>
          <p className="text-muted-foreground">
            되돌릴 수 없습니다. 다시 연결하려면 토큰 주인이 새 토큰을 발급해 연동 설정에 입력해야
            합니다. 계정과 다른 토큰은 그대로입니다.
          </p>
          <div className="space-y-1">
            <Label htmlFor="token-revoke-reason">폐기 사유</Label>
            <Input
              id="token-revoke-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="예: 퇴사 · 연동 종료 · 토큰 유출"
              maxLength={500}
              autoFocus
            />
            <p className="text-muted-foreground text-xs">
              감사 기록과 토큰 주인에게 가는 알림에 표시됩니다.
            </p>
          </div>
        </div>
      }
      confirmLabel="폐기"
      destructive
      onConfirm={async () => {
        if (!target) return
        await accountApi.revokeToken(target.accountId, target.tokenId, reason)
        onRevoked()
      }}
      onClose={close}
    />
  )
}
