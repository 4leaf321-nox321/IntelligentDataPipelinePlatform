/**
 * 많은 것 중에서 하나 선택.
 *
 * **고를 것이 스물을 넘으면 `<Select>` 를 쓰지 않는다.** 통째로 펼쳐 놓고 눈으로
 * 찾으라고 하면 그 일은 실패하고, **못 찾은 사람은 없다고 결론 내리고 새로 만든다**
 * — 그러면 같은 값이 둘로 갈린다. 닫힌 축(부서·기준정보처럼 값이 정해져 있어야
 * 하는 것)에서는 특히 나쁘다.
 *
 * ## 두 길을 함께 낸다
 *
 *   치는 것    이름의 일부를 알 때
 *   훑는 것    무엇이 있는지 모를 때 — 열자마자 전부 보인다
 *
 * 하나만 있으면 반쪽이다. 검색만 있으면 뭘 쳐야 할지 모르는 사람이 막히고, 목록만
 * 있으면 아는 사람이 스크롤을 해야 한다.
 *
 * ## 고를 수 없는 줄은 이유를 적는다
 *
 * 비활성만 시키고 말 안 하면 버그로 읽힌다. `disabledReason` 이 배지로 붙는다.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronsUpDown, Search } from 'lucide-react'

import { Button } from '@/shared/components/ui/button'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/shared/components/ui/popover'
import { Input } from '@/shared/components/ui/input'
import { cn } from '@/shared/lib/utils'

export interface PickerOption {
  value: string
  label: string
  /** 같은 이름이 여럿일 때 구별해 주는 줄. 부서라면 경로, 기종이라면 계열. */
  hint?: string
  /** 검색에 걸리게 할 추가 문자열(별칭·코드). 화면에는 안 보인다. */
  keywords?: string
  /** 값이 있으면 못 고른다. **그 이유가 배지로 뜬다.** */
  disabledReason?: string
  /**
   * 같은 제목끼리 그 제목 아래로 모은다 — 이어진 것 너머의 칸처럼 「개발사 (기업)」 · 「모델 ›
   * 과제 (과제)」 걸음마다. 제목도 검색에 걸린다. 차례는 호스트가 준 대로다.
   */
  group?: string
}

interface SearchablePickerProps {
  options: PickerOption[]
  value: string | null
  onChange: (value: string) => void
  placeholder?: string
  /** 검색 칸의 안내. 무엇으로 찾을 수 있는지 적는다. */
  searchPlaceholder?: string
  emptyText?: string
  id?: string
  className?: string
  /**
   * **서버가 찾는 모드.** 친 글자를 이리로 넘기면 호스트가 후보를 다시 받아 온다 —
   * 그때 이 컴포넌트는 스스로 거르지 않는다(서버가 이미 걸렀고, 검색 속성처럼 화면에
   * 없는 것으로 맞은 줄을 여기서 떨어뜨리면 안 된다).
   */
  onQueryChange?: (query: string) => void
  /** 서버가 말한 전체 수. 후보가 잘렸음을 「N / 전체」 로 보여 준다. */
  total?: number
  loading?: boolean
  /** 골라 둔 값이 후보에 없을 때 대신 보여 줄 것(호스트가 상세에서 읽어 온 이름). */
  pinned?: PickerOption | null
  /** 단추의 이름(화면 읽기 · 시험) — 옆에 글자 라벨이 없을 때. */
  ariaLabel?: string
}

/** 눈에 같아 보이는 값을 같게 본다 — 전각·대소문자·군더더기 공백. */
function normalize(raw: string): string {
  return raw.normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim()
}

export function SearchablePicker({
  options,
  value,
  onChange,
  placeholder = '선택하세요',
  searchPlaceholder = '이름으로 검색',
  emptyText = '맞는 것이 없습니다',
  id,
  className,
  onQueryChange,
  total,
  loading = false,
  pinned = null,
  ariaLabel,
}: SearchablePickerProps) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)

  const selected =
    options.find((one) => one.value === value) ?? (pinned && pinned.value === value ? pinned : null)

  const shown = useMemo(() => {
    // 골라 둔 것이 후보 밖이면 맨 위에 꽂는다 — 안 그러면 고른 것이 목록에 없어 보인다.
    const base =
      pinned && pinned.value === value && !options.some((one) => one.value === pinned.value)
        ? [pinned, ...options]
        : options
    const needle = normalize(query)
    if (!needle || onQueryChange) return base
    return base.filter((one) =>
      normalize(`${one.label} ${one.hint ?? ''} ${one.keywords ?? ''} ${one.group ?? ''}`).includes(
        needle,
      ),
    )
  }, [options, pinned, value, query, onQueryChange])

  // 닫히면 검색어를 비운다. **바깥 클릭 · Esc 는 팝오버가 맡는다** — 손으로 달아 두면
  // 창 · 시트 안에서 어느 것이 먼저 닫히는지가 화면마다 달라진다.
  useEffect(() => {
    if (open) return
    setQuery('')
    onQueryChange?.('')
    // onQueryChange 는 호스트가 매 렌더 새로 만들 수 있다 — 열림/닫힘에만 반응한다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  function pick(option: PickerOption) {
    if (option.disabledReason) return
    onChange(option.value)
    setOpen(false)
  }

  return (
    // **목록은 포털로 띄운다.** 창(Dialog) 은 넘칠 때를 대비해 안쪽을 잘라 두는데, 목록을
    // 그 안에 그리면 긴 목록이 창 경계에서 잘린다 — 실측으로 그랬다(2026-09-24).
    // 팝오버는 화면 맨 위에 그리고 자리가 모자라면 스스로 접는다.
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          className={cn('w-full justify-between font-normal', className)}
        >
          <span className={cn('truncate', !selected && 'text-muted-foreground')}>
            {selected ? (selected.hint ?? selected.label) : placeholder}
          </span>
          <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>

      <PopoverContent
        align="start"
        // 단추와 같은 너비로 서고, **남은 높이만큼만** 차지한다.
        className="w-(--radix-popover-trigger-width) max-h-(--radix-popover-content-available-height) overflow-hidden p-0"
        collisionPadding={8}
        onOpenAutoFocus={(event) => {
          // 열면 검색 칸으로 바로 간다 — 열고 나서 한 번 더 클릭하게 하지 않는다.
          event.preventDefault()
          searchRef.current?.focus()
        }}
      >
        <div className="flex max-h-[inherit] flex-col">
          <div className="relative border-b p-2">
            <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-4 size-4 -translate-y-1/2" />
            <Input
              ref={searchRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value)
                onQueryChange?.(event.target.value)
              }}
              placeholder={searchPlaceholder}
              className="h-8 pl-8"
            />
          </div>

          {/* **열자마자 전부 보인다.** 무엇이 있는지 모르는 사람의 길이 이것이다. */}
          <ul className="min-h-0 flex-1 overflow-y-auto p-1">
            {shown.length === 0 && (
              <li className="text-muted-foreground px-2 py-6 text-center text-sm">
                {loading ? '찾는 중…' : emptyText}
              </li>
            )}
            {shown.map((one, index) => (
              <li key={one.value}>
                {one.group && one.group !== shown[index - 1]?.group && (
                  <p className="text-muted-foreground px-2 pt-2 pb-1 text-xs font-medium">
                    {one.group}
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => pick(one)}
                  disabled={Boolean(one.disabledReason)}
                  className={cn(
                    'flex w-full items-start gap-2 rounded-sm px-2 py-1.5 text-left text-sm',
                    one.disabledReason
                      ? 'cursor-not-allowed opacity-60'
                      : 'hover:bg-accent hover:text-accent-foreground',
                  )}
                >
                  <Check
                    className={cn(
                      'mt-0.5 size-4 shrink-0',
                      one.value === value ? 'opacity-100' : 'opacity-0',
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{one.label}</span>
                    {one.hint && (
                      <span className="text-muted-foreground block truncate text-xs">
                        {one.hint}
                      </span>
                    )}
                  </span>
                  {/* **왜 못 고르는지 적는다.** 비활성만 시키고 말 안 하면
                      버그로 읽힌다. */}
                  {one.disabledReason && (
                    <span className="text-muted-foreground shrink-0 rounded border px-1 text-[10px] leading-4">
                      {one.disabledReason}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>

          {/* 몇 개 중 몇 개를 보고 있는지. 거르고 나서 「이게 전부인가」 를 묻지
              않게 한다. 서버가 찾는 모드면 전체 수는 서버의 것이다 — 후보가 잘렸으면
              「더 좁혀 치라」 고 말한다. */}
          {(options.length > 0 || (total ?? 0) > 0) && (
            <p className="text-muted-foreground border-t px-3 py-1.5 text-xs tabular-nums">
              {shown.length} / {total ?? options.length}
              {total !== undefined &&
                total > options.length &&
                ' — 더 있습니다. 이름을 더 쳐서 좁히세요.'}
              {loading && ' · 찾는 중…'}
            </p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}
