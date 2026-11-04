/**
 * 부서 붙여 넣어 추가 — **다른 플랫폼(ReportArchive 등)의 부서 정보 내보내기를 그대로.**
 *
 * 내보낸 CSV 를 열어 복사해 붙이면(탭 구분) 계획이 선다: 행마다 새로/고침/그대로/건너뜀/오류.
 * 오류가 하나라도 있으면 아무것도 안 넣는다. 같은 slug 면 고치고, 상위는 같은 표 안의 것이어도
 * 된다. 개인 공간은 건너뛴다. 멤버·관리자 열은 안 읽는다 — 사람은 이쪽 계정으로 따로.
 */

import { useState } from 'react'
import { ClipboardPaste, Loader2 } from 'lucide-react'

import { workspaceApi } from '@/modules/workspaces/api'
import type { WorkspaceImportPlan } from '@/modules/workspaces/api'
import { ErrorNotice } from '@/shared/components/ErrorNotice'
import { Badge } from '@/shared/components/ui/badge'
import { Button } from '@/shared/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'
import { Textarea } from '@/shared/components/ui/textarea'

const ACTION: Record<string, string> = {
  create: '새로',
  update: '고침',
  unchanged: '그대로',
  skip: '건너뜀',
  error: '오류',
}

interface Props {
  onClose: () => void
  onApplied: () => void
}

export function WorkspaceImportDialog({ onClose, onApplied }: Props) {
  const [text, setText] = useState('')
  const [plan, setPlan] = useState<WorkspaceImportPlan | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  async function run(apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const got = await workspaceApi.importText(text, apply)
      setPlan(got)
      if (got.applied) onApplied()
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error('알 수 없는 오류'))
    } finally {
      setBusy(false)
    }
  }

  const canApply = Boolean(
    plan && !plan.applied && plan.errors.length === 0 && plan.counts.error === 0,
  )
  const shown = plan
    ? plan.rows.filter((one) => one.action !== 'unchanged' || plan.rows.length <= 30)
    : []

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>부서 붙여넣기 추가</DialogTitle>
          <DialogDescription>
            ReportArchive 등 다른 플랫폼의 「부서 정보 CSV 내보내기」 를 엑셀에서 열어 복사해
            붙입니다(탭 구분). 열 이름이 열쇠라 첫 줄은 그대로 두세요. 같은 slug 는 수정하고, 개인
            공간은 건너뜁니다. 멤버·관리자는 안 읽습니다.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <Textarea
            rows={8}
            value={text}
            placeholder={
              'slug\tname\tparent_slug\tstatus\tdescription\neng\t엔지니어링\t\tactive\t설계와 해석\ncae\t해석팀\teng\tactive\t'
            }
            className="font-mono text-xs"
            onChange={(event) => {
              setText(event.target.value)
              setPlan(null)
            }}
          />
          {error && <ErrorNotice error={error} />}
          {plan && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge>새로 {plan.counts.create}</Badge>
                <Badge variant="secondary">고침 {plan.counts.update}</Badge>
                <Badge variant="outline">그대로 {plan.counts.unchanged}</Badge>
                {plan.counts.skip > 0 && <Badge variant="outline">건너뜀 {plan.counts.skip}</Badge>}
                {plan.counts.error > 0 && (
                  <Badge variant="destructive">오류 {plan.counts.error}</Badge>
                )}
                {plan.applied && (
                  <span className="text-emerald-600 dark:text-emerald-400">적용했습니다.</span>
                )}
              </div>
              {plan.errors.map((one) => (
                <p key={one} className="text-destructive text-sm">
                  {one}
                </p>
              ))}
              {shown.length > 0 && (
                <div className="max-h-64 overflow-auto rounded-md border text-xs">
                  <table className="w-full">
                    <tbody>
                      {shown.map((one) => (
                        <tr key={one.row} className="border-b">
                          <td className="text-muted-foreground px-2 py-1 tabular-nums">
                            {one.row}
                          </td>
                          <td className="px-2 py-1">
                            <span
                              className={
                                one.action === 'error'
                                  ? 'text-destructive'
                                  : one.action === 'create'
                                    ? 'text-emerald-700 dark:text-emerald-400'
                                    : ''
                              }
                            >
                              {ACTION[one.action] ?? one.action}
                            </span>
                          </td>
                          <td className="px-2 py-1 font-mono">{one.slug}</td>
                          <td className="px-2 py-1">{one.label}</td>
                          <td className="text-muted-foreground px-2 py-1">
                            {one.message || one.changes.join(', ')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {plan?.applied ? '닫기' : '취소'}
          </Button>
          {!plan?.applied && (
            <>
              <Button variant="outline" onClick={() => run(false)} disabled={busy || !text.trim()}>
                {busy ? (
                  <Loader2 className="mr-1 size-4 animate-spin" />
                ) : (
                  <ClipboardPaste className="mr-1 size-4" />
                )}
                미리 보기
              </Button>
              <Button onClick={() => run(true)} disabled={busy || !canApply}>
                적용 — 새로 {plan?.counts.create ?? 0}, 고침 {plan?.counts.update ?? 0}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
