/**
 * 자동 저장의 표시 — 저장 중 · 저장됨 · 실패(다시 시도). 아무 일 없으면 자리만 지킨다. 충돌은 여기서
 * 말하지 않는다 — 고를 것이 있어 상자(`ConflictBox`)가 따로 선다.
 *
 * 인쇄에는 안 나온다 — 한 장 보고서에 「저장됨」 이 찍히면 그것은 보고서의 글처럼 읽힌다.
 */

import { AlertCircle, Check, Loader2 } from 'lucide-react'

import type { SaveStatus } from '@/shared/hooks/useAutoSave'
import { cn } from '@/shared/lib/utils'

export function SaveState({
  status,
  error,
  onRetry,
  className,
}: {
  status: SaveStatus
  error?: Error | null
  onRetry?: () => void
  className?: string
}) {
  if (status === 'idle' || status === 'dirty' || status === 'conflict') return null
  return (
    <span
      role="status"
      className={cn('inline-flex items-center gap-1 text-[11px] print:hidden', className)}
    >
      {status === 'saving' && (
        <>
          <Loader2 className="text-muted-foreground size-3 animate-spin" />
          <span className="text-muted-foreground">저장 중…</span>
        </>
      )}
      {status === 'saved' && (
        <>
          <Check className="size-3 text-emerald-600" />
          <span className="text-emerald-700 dark:text-emerald-400">저장됨</span>
        </>
      )}
      {status === 'failed' && (
        <>
          <AlertCircle className="text-destructive size-3" />
          <span className="text-destructive" title={error?.message}>
            저장 실패{error?.message ? ` — ${error.message}` : ''}
          </span>
          {onRetry && (
            <button
              type="button"
              className="text-destructive underline underline-offset-2"
              onClick={onRetry}
            >
              다시 시도
            </button>
          )}
        </>
      )}
    </span>
  )
}
