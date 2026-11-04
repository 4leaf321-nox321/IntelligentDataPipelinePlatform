/**
 * 덮어쓰기 충돌 상자 — 같은 칸을 다른 사람이 먼저 저장했을 때 칸 아래에 선다(`useAutoSave` 의
 * `conflict`).
 *
 * 「김OO 님이 10:32 에 먼저 수정했습니다」 · 상대 글과 내 글을 나란히 · 「상대 글 사용」 「내 글로
 * 덮어쓰기」. **고르기 전까지 내 글은 그대로 있다** — 칸의 글도, 이 상자의 「내 글」 도. 말없이 한쪽을
 * 고르면 남의 글이 사라지거나 내 글이 사라지고, 어느 쪽이든 그 사람은 모른다.
 *
 * 인쇄에는 안 나온다.
 */

import { AlertTriangle } from 'lucide-react'

import { Button } from '@/shared/components/ui/button'
import { cn } from '@/shared/lib/utils'

/** 10:32 — 오늘이면 시각만, 아니면 날짜와 함께(절대 시각 — `shared/lib/datetime` 과 같은 까닭). */
export function shownWhen(raw: string | null): string | null {
  if (!raw) return null
  const when = new Date(raw)
  if (Number.isNaN(when.getTime())) return null
  const clock = when.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })
  const today = new Date().toDateString() === when.toDateString()
  return today ? clock : `${when.toLocaleDateString('ko-KR')} ${clock}`
}

export function ConflictBox({
  by,
  at,
  theirs,
  mine,
  noun = '글',
  onTakeTheirs,
  onKeepMine,
  className,
}: {
  by: string | null
  at: string | null
  /** 서버의 지금 값 — 보이는 글로. */
  theirs: string
  /** 내가 쓴 것 — 보이는 글로. */
  mine: string
  /** 「글」 · 「값」(날짜 칸). */
  noun?: '글' | '값'
  onTakeTheirs: () => void
  onKeepMine: () => void
  className?: string
}) {
  const when = shownWhen(at)
  const who = by ? `${by} 님이` : '다른 사람이'
  return (
    <div
      role="alert"
      className={cn(
        'space-y-2 rounded-md border border-amber-500/50 bg-amber-500/5 p-2 text-xs print:hidden',
        className,
      )}
    >
      <p className="flex items-center gap-1.5 font-medium text-amber-800 dark:text-amber-300">
        <AlertTriangle className="size-3.5 shrink-0" />
        {who}
        {when ? ` ${when} 에` : ''} 먼저 수정했습니다
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="min-w-0">
          <p className="text-muted-foreground mb-0.5">상대 {noun}</p>
          <p className="bg-background max-h-32 overflow-auto rounded border px-1.5 py-1 break-words whitespace-pre-wrap">
            {theirs || <span className="text-muted-foreground">(비어 있음)</span>}
          </p>
        </div>
        <div className="min-w-0">
          <p className="text-muted-foreground mb-0.5">내 {noun}</p>
          <p className="bg-background max-h-32 overflow-auto rounded border px-1.5 py-1 break-words whitespace-pre-wrap">
            {mine || <span className="text-muted-foreground">(비어 있음)</span>}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        <Button type="button" size="sm" variant="outline" className="h-7" onClick={onTakeTheirs}>
          상대 {noun} 사용
        </Button>
        <Button type="button" size="sm" className="h-7" onClick={onKeepMine}>
          {`내 ${noun}${noun === '글' ? '로' : '으로'} 덮어쓰기`}
        </Button>
      </div>
    </div>
  )
}
