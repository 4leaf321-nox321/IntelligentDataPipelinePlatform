/**
 * 자동 저장 글 칸 — **그 자리에서 고치고 자동 저장한다.** 문서처럼 보이고(테두리 없음, 줄바꿈
 * 그대로), 누르면 고친다. 고칠 수 없으면 글만 선다. 저장 상태는 칸 아래에 작게(`SaveState`).
 *
 * ## 붙여넣기 · 끌어다 놓기 — 글이 아니라 파일이면 `onFiles` 로
 *
 * 화면 캡처를 칸에 바로 붙이는 일이 가장 잦다. 다만 **글이 함께 오면 글을 붙인다** — 엑셀 · 워드에서
 * 칸을 복사하면 클립보드에 글과 함께 그 칸을 그린 그림이 들어가는데, 그림을 먼저 보면 표를 붙이려던
 * 사람에게 사진이 올라간다. 캡처 · 「이미지 복사」 에는 글이 없다.
 *
 * ## 여럿이 함께 고칠 때
 *
 * `onSave(next, base)` 의 `base` 는 고치기 시작할 때 본 값이다(`useAutoSave`). 칸에 들어가 있는 동안은
 * 바깥 값이 바뀌어도 글을 덮지 않고 「다른 사람이 이 칸을 수정했습니다」 를 작게 보이며, 저장이
 * 충돌로 거절되면 칸 아래에 상자(`ConflictBox`)가 서서 고르게 한다. 칸에 들어가고 나가는 것은
 * `onFocusChange` 로 알린다(「수정 중」 표시).
 */

import { useEffect, useId, useRef, useState } from 'react'
import type { ClipboardEvent, ReactNode } from 'react'

import { ConflictBox } from '@/shared/components/ConflictBox'
import { SaveState } from '@/shared/components/SaveState'
import { useAutoSave } from '@/shared/hooks/useAutoSave'
import { cn } from '@/shared/lib/utils'

/** 클립보드에서 올릴 파일 — 글이 함께 있으면 없는 것으로 본다(위). */
export function pastedFiles(event: ClipboardEvent): File[] {
  const data = event.clipboardData
  if (!data) return []
  const text = typeof data.getData === 'function' ? data.getData('text/plain') : ''
  if (text && text.trim()) return []
  return Array.from(data.files ?? [])
}

export function AutoSaveText({
  value,
  onSave,
  readOnly = false,
  label,
  placeholder,
  singleLine = false,
  required = false,
  onFiles,
  onFocusChange,
  notice,
  className,
  autoFocus = false,
}: {
  value: string
  /** 저장 — 부르는 쪽의 API. `base` 는 고치기 시작할 때 본 값. 던지면 「저장 실패」 와 그 말을,
   * `SaveConflictError` 를 던지면 충돌 상자를 보인다. */
  onSave: (next: string, base: string) => Promise<void>
  readOnly?: boolean
  /** 칸의 이름 — 화면 읽기 · 시험이 이것으로 칸을 찾는다. */
  label: string
  placeholder?: string
  /** 줄바꿈을 받지 않는다(이름처럼 한 줄인 칸) — 친 줄바꿈은 띄어쓰기가 된다. */
  singleLine?: boolean
  /** 비울 수 없다 — 비우면 저장하지 않고 까닭을 보인다(앞 값은 서버에 그대로). */
  required?: boolean
  /** 붙여 넣거나 끌어다 놓은 파일 — 주면 그 칸의 사진으로 올린다. */
  onFiles?: (files: File[]) => void
  /** 칸에 들어갔다(true) · 나갔다(false). */
  onFocusChange?: (focused: boolean) => void
  /** 칸에 들어가 있는 동안 칸 아래에 보일 안내(「김OO 님이 이 칸을 수정하는 중입니다」). */
  notice?: ReactNode
  className?: string
  /** 처음 그릴 때 이 칸으로 들어가 글을 고른다 — 방금 더한 줄의 첫 칸. */
  autoFocus?: boolean
}) {
  const id = useId()
  const box = useRef<HTMLTextAreaElement>(null)
  const [focused, setFocused] = useState(false)
  const focusRef = useRef(onFocusChange)
  focusRef.current = onFocusChange
  useEffect(() => {
    if (!autoFocus || !box.current) return
    box.current.focus()
    box.current.select()
  }, [autoFocus])
  const clean = (next: string) => (singleLine ? next.replace(/\s*\n\s*/g, ' ').trim() : next)
  const auto = useAutoSave(
    value,
    async (next: string, base: string) => {
      const cleaned = clean(next)
      if (required && !cleaned.trim()) throw new Error(`${label}은(는) 비울 수 없습니다`)
      await onSave(cleaned, base)
    },
    { hold: focused },
  )

  if (readOnly) {
    return (
      <>
        <div
          className={cn('text-sm break-words whitespace-pre-wrap', className)}
          aria-label={label}
        >
          {value || <span className="text-muted-foreground print:hidden">—</span>}
        </div>
        {/* 치는 사이에 수정할 수 없게 됐다(회의 확정 …) — 저장 못 한 글을 말없이 버리지 않는다. */}
        {auto.unsaved && (
          <div className="rounded border border-amber-500/50 bg-amber-500/5 p-1.5 text-xs print:hidden">
            <p className="font-medium text-amber-800 dark:text-amber-300">
              저장하지 못한 내 글 — 이제 수정할 수 없는 칸입니다. 필요하면 복사해 둡니다.
            </p>
            <p className="mt-1 break-words whitespace-pre-wrap select-all">{auto.draft}</p>
          </div>
        )}
      </>
    )
  }

  const lines = Math.max(1, auto.draft.split('\n').length)
  return (
    <div className="space-y-0.5">
      {/* 인쇄에는 글만 — 입력 칸은 쪽이 넘어가면 잘린다. */}
      <div
        aria-hidden
        className={cn('hidden text-sm break-words whitespace-pre-wrap print:block', className)}
      >
        {auto.draft}
      </div>
      <textarea
        ref={box}
        id={id}
        aria-label={label}
        value={auto.draft}
        placeholder={placeholder}
        rows={singleLine ? 1 : Math.max(2, lines)}
        onChange={(event) => auto.change(event.target.value)}
        onFocus={() => {
          setFocused(true)
          focusRef.current?.(true)
        }}
        onBlur={() => {
          setFocused(false)
          focusRef.current?.(false)
          void auto.flush()
        }}
        onKeyDown={(event) => {
          if (singleLine && event.key === 'Enter' && !event.nativeEvent.isComposing) {
            event.preventDefault()
            event.currentTarget.blur()
          }
        }}
        onPaste={(event) => {
          if (!onFiles) return
          const files = pastedFiles(event)
          if (files.length === 0) return
          event.preventDefault()
          onFiles(files)
        }}
        className={cn(
          // 문서처럼 — 테두리 없이 글만, 누르면 옅은 바탕. 줄 수만큼 늘어난다.
          'placeholder:text-muted-foreground/60 focus:bg-muted/40 block w-full print:hidden resize-none rounded-sm bg-transparent px-1 py-0.5 text-sm [field-sizing:content] outline-none focus:ring-1 focus:ring-ring/40',
          className,
        )}
      />
      {focused && notice && (
        <p className="px-1 text-[11px] text-sky-700 print:hidden dark:text-sky-400">{notice}</p>
      )}
      {auto.stale && !auto.conflict && (
        <p className="px-1 text-[11px] text-amber-700 print:hidden dark:text-amber-400">
          다른 사람이 이 칸을 수정했습니다 — 저장할 때 확인합니다.
        </p>
      )}
      {auto.conflict && (
        <ConflictBox
          by={auto.conflict.by}
          at={auto.conflict.at}
          theirs={auto.conflict.current}
          mine={auto.draft}
          onTakeTheirs={auto.takeTheirs}
          onKeepMine={() => void auto.keepMine()}
        />
      )}
      <SaveState
        status={auto.status}
        error={auto.error}
        onRetry={() => void auto.flush()}
        className="px-1"
      />
    </div>
  )
}
